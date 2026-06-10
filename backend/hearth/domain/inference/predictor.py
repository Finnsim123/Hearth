"""Inference service — newest feature windows -> predictions.

Falls back to BOOTSTRAP RULES when no model is promoted (model_version
'rules-v0') so brand-new homes get a day-one ribbon to correct — those
corrections become the first training set. With a model: probabilities,
top-SHAP explanation, hysteresis smoothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from ..features.registry import feature_set_version
from ..labeling.rules import bootstrap_labels
from ..schemas import Prediction

log = logging.getLogger(__name__)

RULES_VERSION = "rules-v0"
RULES_CONFIDENCE = 0.55  # below ask-threshold by design: rules want feedback


def _rules_predict(repo, feats: pd.DataFrame, person_id: str) -> pd.DataFrame:
    default_activity = repo.get_setting("default_activity", "home") or "home"
    labels = bootstrap_labels(repo.rules(), feats, person_id, default_activity)
    slugs = sorted({a.slug for a in repo.activities()} | set(labels.unique()))
    probs = pd.DataFrame(0.0, index=feats.index, columns=slugs)
    rest = (1 - RULES_CONFIDENCE) / max(len(slugs) - 1, 1)
    for ts, lab in labels.items():
        probs.loc[ts] = rest
        probs.loc[ts, lab] = RULES_CONFIDENCE
    return probs


def predict_person(person_id: str, tsdb, repo, store) -> list[Prediction]:
    composites = repo.get_setting("composites", []) or []
    fset = feature_set_version(composites)
    now = datetime.now(timezone.utc)
    feats = tsdb.read_features(person_id, fset, now - timedelta(hours=2), now)
    if feats.empty:
        return []
    history = _history(tsdb, person_id, now)
    done_ts = {pd.Timestamp(p.window_ts) for p in history}
    todo = feats.loc[[ts for ts in feats.index if ts not in done_ts]]
    if todo.empty:
        return []

    record = next((m for m in repo.models(person_id) if m.promoted), None)
    bindings = repo.bindings()
    if record is not None:
        est = store.load(record)
        probs = est.predict_proba(todo)
        explains = est.explain(todo)        # all windows: explanation + evidence
        version = record.version
    else:
        probs = _rules_predict(repo, todo, person_id)
        explains = pd.DataFrame(index=todo.index)
        version = RULES_VERSION

    out: list[Prediction] = []
    for ts in todo.index:
        row = probs.loc[ts]
        predicted = str(row.idxmax())
        confidence = float(row.max())
        explanation: list[tuple[str, float]] = []
        evidence = None
        if not explains.empty and ts in explains.index:
            top = explains.loc[ts].abs().nlargest(3)
            explanation = [(f, float(explains.loc[ts, f])) for f in top.index]
            from ..features.evidence import (
                WEAK_CONFIDENCE_CAP, WEAK_DIRECT_SHARE, window_evidence)
            evidence = round(window_evidence(explains.loc[ts], bindings), 4)
            if evidence < WEAK_DIRECT_SHARE and confidence > WEAK_CONFIDENCE_CAP:
                # the model is confident but not anchored on direct signal —
                # don't assert; the capped confidence triggers a question
                log.info("[%s] weak evidence (%.0f%% direct) — confidence "
                         "%.2f capped to %.2f", person_id, evidence * 100,
                         confidence, WEAK_CONFIDENCE_CAP)
                confidence = WEAK_CONFIDENCE_CAP
        smoothed = _apply_smoothing(history, predicted, confidence)
        pred = Prediction(person_id=person_id, window_ts=ts.to_pydatetime(),
                          model_version=version, predicted=predicted,
                          smoothed=smoothed, confidence=confidence,
                          probabilities={c: float(v) for c, v in row.items()},
                          explanation=explanation, evidence=evidence)
        tsdb.write_prediction(pred)
        history.insert(0, pred)
        out.append(pred)
    return out


def _apply_smoothing(history, predicted, confidence) -> str:
    from .smoothing import smooth
    return smooth(history, predicted, confidence)


def _history(tsdb, person_id: str, now: datetime) -> list:
    raw = tsdb.read_predictions(person_id, now - timedelta(hours=3), now)
    out = []
    for r in raw:
        out.append(Prediction(person_id=person_id, window_ts=datetime.fromisoformat(r["time"]),
                              model_version=r["model_version"], predicted=r["predicted"],
                              smoothed=r["smoothed"], confidence=r["confidence"],
                              probabilities=r.get("probs", {})))
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
            await maybe_ask(preds[-1], person, repo, notifier)
    tsdb.write_heartbeat("inference")
