"""Inference service — newest feature windows -> predictions.

Falls back to BOOTSTRAP RULES when no model is promoted (model_version
'rules-v0') so brand-new homes get a day-one ribbon to correct — those
corrections become the first training set. With a model: probabilities,
top-SHAP explanation, hysteresis smoothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from ..features.registry import active_feature_set_version
from ..labeling.rules import bootstrap_labels
from ..schemas import Prediction
from .output import apply_abstain, load_output_policy

log = logging.getLogger(__name__)

RULES_VERSION = "rules-v0"
RULES_CONFIDENCE = 0.55  # below ask-threshold by design: rules want feedback
FACT_VERSION = "fact-v0"  # foundational ground-truth gate (away/asleep): basis is
                          # carried in model_version, mirroring RULES_VERSION — no
                          # schema change. The model is SKIPPED for these windows
                          # (foundational_facts_design §5 cascade; presence.py).


def _rules_predict(repo, feats: pd.DataFrame, person_id: str):
    default_activity = repo.get_setting("default_activity", "home") or "home"
    labels, basis = bootstrap_labels(repo.rules(), feats, person_id,
                                     default_activity, return_basis=True)
    slugs = sorted({a.slug for a in repo.activities()} | set(labels.unique()))
    probs = pd.DataFrame(0.0, index=feats.index, columns=slugs)
    rest = (1 - RULES_CONFIDENCE) / max(len(slugs) - 1, 1)
    for ts, lab in labels.items():
        probs.loc[ts] = rest
        probs.loc[ts, lab] = RULES_CONFIDENCE
    return probs, basis


def predict_person(person_id: str, tsdb, repo, store) -> list[Prediction]:
    from ..controls import active_override, override_is_labeling, override_prediction
    fset = active_feature_set_version(repo)
    out_pol = load_output_policy(repo)
    override = active_override(repo, person_id)
    now = datetime.now(timezone.utc)
    label_override = bool(override) and override_is_labeling(repo, person_id, now)
    feats = tsdb.read_features(person_id, fset, now - timedelta(hours=2), now)
    if feats.empty:
        return []
    history = _history(tsdb, person_id, now)
    done_ts = {pd.Timestamp(p.window_ts) for p in history}
    todo = feats.loc[[ts for ts in feats.index if ts not in done_ts]]
    if todo.empty:
        return []

    bindings = repo.bindings()
    # ── foundational gating fact: AWAY (ground truth from the person tracker) ──
    # The cascade (foundational_facts_design §5): a gating fact settles the window
    # and BYPASSES the model — correct (a fact beats a prediction) and cheap (no
    # inference on those windows). Presence is fact-eligible by default (§7a);
    # sleep/other gates extend this once their reliability verdict == 'fact'.
    from ..features.presence import AWAY, gate_row, presence_state
    from ..foundational.facts import extra_gate_slugs, fact_series
    presence_by_ts = {ts: presence_state(todo.loc[ts], bindings, person_id)
                      for ts in todo.index}
    gate_by_ts: dict = {ts: (AWAY, "you're out (tracker)")
                        for ts, p in presence_by_ts.items() if p == AWAY}
    # earned non-away facts (e.g. asleep from a reliable sleep sensor) gate the
    # remaining windows — only facts whose reliability verdict == 'fact' (§7a)
    for f in extra_gate_slugs(repo, person_id):
        s = fact_series(todo, f)
        for ts in todo.index:
            if ts not in gate_by_ts and bool(s.get(ts, False)):
                gate_by_ts[ts] = (f.gate, "known from a reliable sensor")
    gate_ts = set(gate_by_ts)
    model_todo = todo.loc[[ts for ts in todo.index if ts not in gate_ts]]

    promoted = [m for m in repo.models(person_id) if m.promoted]
    record = next((m for m in promoted if m.node == "root"), None)
    # per-node conformal thresholds ride inside each record's metrics, so the
    # threshold always matches the model that produced the probabilities
    conformals = {m.node: (m.metrics or {}).get("conformal") for m in promoted}
    # hierarchy (LCPN): one fine classifier per coarse state that has one
    child_probs: dict[str, pd.DataFrame] = {}
    child_explains: dict[str, pd.DataFrame] = {}
    probs = pd.DataFrame()
    explains = pd.DataFrame()
    rule_basis: dict = {}
    version = RULES_VERSION
    if not model_todo.empty and record is not None:
        est = store.load(record)
        probs = est.predict_proba(model_todo)
        explains = est.explain(model_todo)  # all windows: explanation + evidence
        version = record.version
        for child in (m for m in promoted if m.node != "root"):
            try:
                child_est = store.load(child)
                child_probs[child.node] = child_est.predict_proba(model_todo)
                # explain from the CHILD too: a fine prediction's "based on"
                # and evidence must describe the child model, not the root
                child_explains[child.node] = child_est.explain(model_todo)
            except Exception:
                log.exception("child model %s failed to load", child.version)
    elif not model_todo.empty:
        probs, rule_basis = _rules_predict(repo, model_todo, person_id)
        explains = pd.DataFrame(index=model_todo.index)
        version = RULES_VERSION

    trans = repo.get_setting(f"transitions.{person_id}") or None
    durations = repo.get_setting(f"durations.{person_id}") or None
    from ..markers import apply_marker_prior, marker_fired, markers_for
    markers = markers_for(repo, person_id)
    tz_name = repo.get_setting("timezone", "UTC") or "UTC"
    out: list[Prediction] = []
    for ts in todo.index:
        if ts in gate_ts:                       # foundational fact → bypass model
            slug, detail = gate_by_ts[ts]
            pred = Prediction(person_id=person_id, window_ts=ts.to_pydatetime(),
                              model_version=FACT_VERSION, predicted=slug, smoothed=slug,
                              confidence=1.0, probabilities={slug: 1.0},
                              explanation=[(f"known: {detail}", 1.0)],
                              evidence=None, parent=None, coarse_confidence=None)
            if override:                        # override still wins over a fact
                pred = override_prediction(pred, override)
                if label_override:
                    from ..schemas import LabelEvent, Provenance
                    tsdb.write_label(LabelEvent(
                        person_id=person_id, window_ts=ts.to_pydatetime(), label=override,
                        provenance=Provenance.CONFIRMED, source="override"))
            tsdb.write_prediction(pred)
            history.insert(0, pred)
            out.append(pred)
            continue
        row = probs.loc[ts]
        if presence_by_ts.get(ts) == "home" and AWAY in row.index:
            row = gate_row(row, "home")         # a present person can't be 'away'
        # learned-transition forward filter: the previous window's state sets
        # a prior (sleeping is sticky; sleeping→cooking at 3am is rare).
        # ONLY for model predictions — the transition matrix is keyed on coarse
        # STATES, so it must run on the root row before the hierarchy picks a
        # fine label; applying it to the rules row could flip the argmax away
        # from the rule we then cite as the reason.
        if (trans or markers) and history and version != RULES_VERSION:
            prev = history[0]
            prev_ts = prev.window_ts if prev.window_ts.tzinfo else \
                prev.window_ts.replace(tzinfo=timezone.utc)
            if abs((ts.to_pydatetime() - prev_ts).total_seconds() - 1800) < 1:
                prev_state = prev.parent or prev.predicted
                # learned transition prior (stationary, daypart-keyed). With
                # duration stats the self-transition also DECAYS as the run
                # outlives the household's typical duration for that activity
                # (HSMM-lite, smoothing.py) — blips die, marathons end.
                if trans:
                    from ..features.pipeline import _bucket
                    from .smoothing import transition_filter
                    try:
                        local_hour = ts.tz_convert(ZoneInfo(tz_name)).hour
                    except Exception:
                        local_hour = ts.hour
                    run_len = None
                    if durations:
                        # walk newest-first history while the state holds and
                        # the 30-min grid stays contiguous
                        run_len, cursor = 1, prev_ts
                        for p in history[1:]:
                            p_ts = p.window_ts if p.window_ts.tzinfo else \
                                p.window_ts.replace(tzinfo=timezone.utc)
                            if abs((cursor - p_ts).total_seconds() - 1800) > 1 \
                                    or (p.parent or p.predicted) != prev_state:
                                break
                            run_len, cursor = run_len + 1, p_ts
                    row = transition_filter(row, prev_state, trans,
                                            daypart=int(_bucket(int(local_hour))),
                                            durations=durations, run_len=run_len)
                # transition markers: an OBSERVED, time-localised trigger (alarm,
                # coffee) sharply boosts P(from→to) so the published state switches
                # cleanly at the right window. Markers are never classifier labels.
                if markers:
                    # a marker's signal may LEAD the transition (coffee ~30 min
                    # before waking): look up the window at ts − lead_min and, if it
                    # fired there, boost THIS (the real transition) window.
                    fired = []
                    for m in markers:
                        fire_ts = ts - pd.Timedelta(minutes=m.lead_min) if m.lead_min else ts
                        frow = feats.loc[fire_ts] if fire_ts in feats.index else None
                        if frow is not None and marker_fired(frow, m):
                            fired.append(m)
                    if fired:
                        row = apply_marker_prior(row, prev_state, fired)
        predicted = str(row.idxmax())
        confidence = float(row.max())
        parent = None
        coarse_confidence = None
        node_used = "root"                    # which node's probs get published
        active_explains = explains            # which model explains THIS window
        if predicted in child_probs:
            # top-down: the root said e.g. "home"; the home-node classifier
            # now picks among home's children (or "just home" = parent slug).
            # "home" and "eating" are simultaneously true — that's the point.
            node_key = predicted             # child_explains is keyed by the NODE
            node_used = node_key
            fine_row = child_probs[node_key].loc[ts]
            fine = str(fine_row.idxmax())
            coarse_confidence = confidence
            if fine != predicted:
                parent = predicted
                predicted = fine
            confidence = float(fine_row.max())
            row = fine_row                  # alternatives/asking use siblings
            # NB: look up by the node key, NOT the (possibly reassigned) fine
            # label — otherwise the child's explanation is lost exactly when the
            # child model changed the answer, the one case that matters.
            active_explains = child_explains.get(node_key, explains)
        explanation: list[tuple[str, float]] = []
        evidence = None
        if version == RULES_VERSION:
            why = rule_basis.get(ts)
            why = why if isinstance(why, str) else None   # pandas None→NaN trap
            explanation = [(f"rule: {why}" if why else "default (no rule matched)", 1.0)]
        if not active_explains.empty and ts in active_explains.index:
            top = active_explains.loc[ts].abs().nlargest(3)
            explanation = [(f, float(active_explains.loc[ts, f])) for f in top.index]
            from ..features.evidence import (
                WEAK_CONFIDENCE_CAP, WEAK_DIRECT_SHARE, window_evidence)
            evidence = round(window_evidence(active_explains.loc[ts], bindings), 4)
            if evidence < WEAK_DIRECT_SHARE and confidence > WEAK_CONFIDENCE_CAP:
                # the model is confident but not anchored on direct signal —
                # don't assert; the capped confidence triggers a question
                log.info("[%s] weak evidence (%.0f%% direct) — confidence "
                         "%.2f capped to %.2f", person_id, evidence * 100,
                         confidence, WEAK_CONFIDENCE_CAP)
                confidence = WEAK_CONFIDENCE_CAP
        probabilities = {c: float(v) for c, v in row.items()}
        # conformal set from the published node's calibrated threshold:
        # 1 = commit, 2+ = honest ambiguity, [] = novelty (drives abstain)
        from .conformal import prediction_set
        pset = None
        if version != RULES_VERSION:
            pset = prediction_set(probabilities, conformals.get(node_used))
        smoothed = _apply_smoothing(history, predicted, confidence)
        smoothed = apply_abstain(smoothed, confidence, out_pol, pred_set=pset)
        pred = Prediction(person_id=person_id, window_ts=ts.to_pydatetime(),
                          model_version=version, predicted=predicted,
                          smoothed=smoothed, confidence=confidence,
                          probabilities=probabilities,
                          explanation=explanation, evidence=evidence,
                          parent=parent, coarse_confidence=coarse_confidence,
                          pred_set=pset if pset is not None else [predicted])
        if override:                       # manual override pins the published state
            pred = override_prediction(pred, override)
            if label_override:             # …and teaches the model while it's fresh
                from ..schemas import LabelEvent, Provenance
                tsdb.write_label(LabelEvent(
                    person_id=person_id, window_ts=ts.to_pydatetime(), label=override,
                    provenance=Provenance.CONFIRMED, source="override"))
        tsdb.write_prediction(pred)
        history.insert(0, pred)
        out.append(pred)
    return out


def _apply_smoothing(history, predicted, confidence) -> str:
    from .smoothing import smooth
    return smooth(history, predicted, confidence)


def _history(tsdb, person_id: str, now: datetime) -> list:
    # 12 h look-back: the duration-aware filter measures how long the current
    # activity run has lasted, and 3 h capped every run at 6 windows (a censored
    # run length understates the hazard). 12 h ≈ 24 rows — still cheap.
    raw = tsdb.read_predictions(person_id, now - timedelta(hours=12), now)
    out = []
    for r in raw:
        out.append(Prediction(person_id=person_id, window_ts=datetime.fromisoformat(r["time"]),
                              model_version=r["model_version"], predicted=r["predicted"],
                              smoothed=r["smoothed"], confidence=r["confidence"],
                              probabilities=r.get("probs", {}),
                              # parent is what the transition filter keys on next
                              # window — without it the filter silently no-ops
                              parent=r.get("parent"),
                              evidence=r.get("evidence")))
    return out


async def predict_latest(tsdb, repo, store, publisher=None, notifier=None) -> None:
    """Scheduler entrypoint: predict, publish, maybe ask. Heartbeats."""
    from ..labeling.active import maybe_ask
    for person in repo.persons():
        if not person.enabled:
            continue
        try:
            preds = predict_person(person.id, tsdb, repo, store)
        except Exception:
            log.exception("inference failed for %s", person.id)
            continue
        for pred in preds:
            if publisher is not None:
                try:
                    publisher.publish(pred)
                except Exception:
                    log.exception("publish failed")
        if preds and notifier is not None:
            try:
                await maybe_ask(preds[-1], person, repo, notifier)
            except Exception:
                # a bad timezone setting or sqlite hiccup must not abort the
                # loop for other members or skip the heartbeat
                log.exception("maybe_ask failed for %s", person.id)
    tsdb.write_heartbeat("inference")
