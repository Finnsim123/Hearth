"""Feature selection — measuring what actually helps, on data the model never saw.

The problem this module exists for: impurity (Gini) importance — the default the
trainer stored — is structurally biased toward continuous / high-cardinality
features (Strobl et al. 2007). A temperature sensor offers thousands of split
points, so it LOOKS important even when it carries no activity signal; the model
"keeps staring at the coffee machine's thermometer" and retraining never fixes
it, because the measurement itself is broken.

The fix (this file, stage 1): **held-out permutation importance** — shuffle one
column of the VALIDATION slice and measure how much held-out accuracy drops. A
feature the model doesn't truly need drops ~nothing; a harmful one can even go
negative. Unbiased w.r.t. cardinality because it scores predictions, not splits.

Later stages build on this measure: a noise gate (features that never beat
shuffling collect strikes across retrains — stability selection) and a
champion/challenger prune trial verified by the promotion gate.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

MIN_VAL_ROWS = 20      # below this, permutation estimates are noise — fall back
N_REPEATS = 2          # shuffles per column (mean taken); 2 keeps a 600-col
                       # matrix in the tens of seconds on a small val slice

# ── the noise gate: strikes across retrains (stability selection) ────────────
# One bad week can't condemn a good sensor: a feature only becomes an
# "uninformative" candidate after repeatedly failing to beat shuffling on
# held-out data. Rounds are recorded at most every STRIKE_MIN_GAP_DAYS so daily
# retrains (whose val windows overlap) don't count the same evidence twice.
STRIKE_EPS = 1e-6            # importance ≤ this = "shuffling didn't hurt" = noise
STRIKE_ROUNDS_KEPT = 4       # rolling window of independent-ish rounds
STRIKES_TO_CANDIDATE = 3     # flagged in ≥3 of the last 4 rounds → candidate
STRIKE_MIN_GAP_DAYS = 3      # min days between rounds (val window must move)


def _strike_key(person_id: str, node: str) -> str:
    return f"selection.strikes.{person_id}.{node}"


def record_strike_round(repo, person_id: str, node: str,
                        imp: dict[str, float], now: datetime | None = None) -> bool:
    """Record one strike round from a train's held-out permutation importances.
    Only call with PERMUTATION importances (the impurity fallback is the biased
    ruler — it must never issue strikes). Returns True if a round was recorded,
    False when skipped (too soon after the previous round)."""
    from ..features.pipeline import TEMPORAL_COLS
    now = now or datetime.now(timezone.utc)
    key = _strike_key(person_id, node)
    data = repo.get_setting(key) or {}
    rounds = data.get("rounds") or []
    if rounds:
        try:
            last = datetime.fromisoformat(rounds[-1]["at"])
            if now - last < timedelta(days=STRIKE_MIN_GAP_DAYS):
                return False               # overlapping val window — same evidence
        except (KeyError, ValueError):
            pass
    scored = {c: v for c, v in imp.items() if c not in TEMPORAL_COLS}
    if not scored:
        return False
    rounds.append({
        "at": now.isoformat(),
        "flagged": sorted(c for c, v in scored.items() if v <= STRIKE_EPS),
        "seen": sorted(scored),
    })
    repo.set_setting(key, {"rounds": rounds[-STRIKE_ROUNDS_KEPT:]})
    return True


def dropped_features(repo, person_id: str) -> list[str]:
    """Features currently soft-dropped for this person (consumed by train_person).
    Soft: they stay in the feature store and visible to discovery — only the
    supervised matrix excludes them. Reversible via the comeback audition."""
    return (repo.get_setting(f"selection.dropped.{person_id}") or {}).get("features") or []


def strike_candidates(repo, person_id: str, node: str = "root") -> list[str]:
    """Features flagged uninformative in ≥ STRIKES_TO_CANDIDATE of the last
    STRIKE_ROUNDS_KEPT rounds — and SEEN (scored) in at least that many rounds,
    so a newly-added feature can't be condemned on thin evidence."""
    data = repo.get_setting(_strike_key(person_id, node)) or {}
    rounds = data.get("rounds") or []
    if len(rounds) < STRIKES_TO_CANDIDATE:
        return []
    flags: dict[str, int] = {}
    seen: dict[str, int] = {}
    for r in rounds:
        for c in r.get("seen", []):
            seen[c] = seen.get(c, 0) + 1
        for c in r.get("flagged", []):
            flags[c] = flags.get(c, 0) + 1
    return sorted(c for c, n in flags.items()
                  if n >= STRIKES_TO_CANDIDATE and seen.get(c, 0) >= STRIKES_TO_CANDIDATE)


# ── the champion/challenger trial: selection that has to EARN adoption ───────
# Selection on small data overfits itself (Ambroise & McLachlan 2002), so no
# feature is ever dropped on importance alone: a challenger model trained
# WITHOUT the candidates must be not-credibly-worse than a champion trained WITH
# them, on the same temporal holdout, judged by Wilson lower bounds — the same
# discipline as the promotion gate. Drops are SOFT (selection.dropped, matrix-
# only, discovery unaffected) and auditioned for a comeback every AUDITION_DAYS,
# because a sensor's usefulness changes as labels accumulate.
TRIAL_MARGIN = 0.02        # prune adopted when chall_lo >= champ_lo - margin
AUDITION_MARGIN = 0.02     # re-add only when STRICTLY better: > champ_lo + margin
AUDITION_DAYS = 30
MIN_KEEP = 20              # never prune below this many columns


def _trial_matrices(person_id: str, tsdb, repo):
    """The same data prep as train_person's root node, minus side effects.
    Returns (feats, labels, provenance) or None when there's too little data."""
    from datetime import datetime as _dt
    from ..features.person_scope import drop_foreign_personal
    from ..features.registry import active_feature_set_version
    from ..labeling.merge import merge_labels
    from ..labeling.rules import bootstrap_labels
    from ..labeling.taxonomy import parent_map, to_coarse
    from .trainer import load_training_config
    cfg = load_training_config(repo)
    fset = active_feature_set_version(repo)
    end = _dt.now(timezone.utc)
    start = end - timedelta(weeks=cfg.train_weeks or 8)
    feats = tsdb.read_features(person_id, fset, start, end)
    if feats is None or len(feats) < cfg.min_train_windows:
        return None
    feats, _ = drop_foreign_personal(feats, repo.bindings(), repo.persons(), person_id)
    excl = [b.name for b in repo.bindings() if getattr(b, "model_excluded", False)]
    if excl:
        feats = feats.drop(columns=[c for c in feats.columns
                                    if any(c == n or c.startswith(f"{n}_") for n in excl)])
    default_activity = repo.get_setting("default_activity", "home") or "home"
    bootstrap = bootstrap_labels(repo.rules(), feats, person_id, default_activity)
    labels, provenance, _gold = merge_labels(bootstrap, tsdb.read_labels(person_id, start, end))
    aliases = repo.get_setting("activity.aliases") or {}
    if aliases:
        labels = labels.map(lambda lab: aliases.get(lab, lab))
    pmap = parent_map(repo.activities())
    labels = labels.map(lambda lab: to_coarse(lab, pmap))
    if labels.nunique() < 2:
        return None
    return feats, labels, provenance, cfg, end


def _holdout_acc(feats, labels, provenance, cfg, end, family: str):
    """Fit on the temporal train side, score on val. Prefers CONFIRMED val rows
    (human truth) when there are ≥10; else all val rows. → (k, n) successes."""
    from .estimators import make_estimator
    cutoff = end - timedelta(days=cfg.val_days)
    train_mask = feats.index < cutoff
    if train_mask.sum() < 10 or (~train_mask).sum() < MIN_VAL_ROWS:
        train_mask = feats.index < feats.index[int(len(feats) * 0.75)]
    if train_mask.sum() < 10 or (~train_mask).sum() < 5:
        return None
    X_tr, y_tr = feats[train_mask], labels[train_mask]
    X_v, y_v, prov_v = feats[~train_mask], labels[~train_mask], provenance[~train_mask]
    keep = y_v.isin(set(y_tr.unique()))
    X_v, y_v, prov_v = X_v[keep], y_v[keep], prov_v[keep]
    if len(X_v) < 5:
        return None
    est = make_estimator(family)
    est.fit(X_tr, y_tr)
    pred = est.predict_proba(X_v).idxmax(axis=1)
    conf = prov_v.astype(str) == "confirmed"
    if conf.sum() >= 10:                      # judge on human truth when we can
        pred, y_v = pred[conf.to_numpy()], y_v[conf]
    hits = int((pred.to_numpy() == y_v.to_numpy()).sum())
    return hits, int(len(y_v))


def run_selection_trial(person_id: str, tsdb, repo, now: datetime | None = None) -> dict:
    """The acting half of the loop. Prune mode: strike candidates exist → train
    champion (with) vs challenger (without) on the same split; adopt the pruned
    set when not credibly worse. Audition mode: dropped features ≥ AUDITION_DAYS
    old → re-add only if strictly better. Everything logged as an event."""
    from .evaluate import wilson_interval
    from .trainer import load_training_config
    now = now or datetime.now(timezone.utc)
    key = f"selection.dropped.{person_id}"
    state = repo.get_setting(key) or {}
    dropped = state.get("features") or []
    candidates = [c for c in strike_candidates(repo, person_id, "root")
                  if c not in dropped]

    audition_due = bool(dropped) and (
        not state.get("at")
        or now - datetime.fromisoformat(state["at"]) >= timedelta(days=AUDITION_DAYS))
    if not candidates and not audition_due:
        return {"mode": "none"}

    prep = _trial_matrices(person_id, tsdb, repo)
    if prep is None:
        return {"mode": "none", "reason": "not enough data"}
    feats, labels, provenance, cfg, end = prep
    base = feats.drop(columns=[c for c in dropped if c in feats.columns])

    if candidates:      # ── prune trial ──
        mode = "prune"
        champ_X = base
        chall_X = base.drop(columns=[c for c in candidates if c in base.columns])
        if chall_X.shape[1] < MIN_KEEP:
            return {"mode": "none", "reason": "would prune below floor"}
        margin, strictly_better = TRIAL_MARGIN, False
    else:               # ── comeback audition: challenger re-adds the dropped ──
        mode = "audition"
        champ_X, chall_X = base, feats
        margin, strictly_better = AUDITION_MARGIN, True

    champ = _holdout_acc(champ_X, labels, provenance, cfg, end, cfg.model_family)
    chall = _holdout_acc(chall_X, labels, provenance, cfg, end, cfg.model_family)
    if champ is None or chall is None:
        return {"mode": "none", "reason": "split too small"}
    champ_lo = wilson_interval(*champ)[0]
    chall_lo = wilson_interval(*chall)[0]
    adopt = (chall_lo > champ_lo + margin) if strictly_better \
        else (chall_lo >= champ_lo - margin)

    from .. import events as ev
    if mode == "prune":
        if not adopt:
            return {"mode": mode, "adopted": False,
                    "champ_lo": round(champ_lo, 4), "chall_lo": round(chall_lo, 4)}
        new_dropped = sorted(set(dropped) | set(candidates))
        repo.set_setting(key, {"features": new_dropped, "at": now.isoformat()})
        repo.set_setting(_strike_key(person_id, "root"), {"rounds": []})   # fresh ledger
        try:
            ev.record_event(repo, "features_pruned",
                            f"Stopped training on {len(candidates)} uninformative "
                            f"feature(s) for {person_id}",
                            f"held-out accuracy held ({chall[0]}/{chall[1]} vs "
                            f"{champ[0]}/{champ[1]}); reversible — they get a "
                            f"comeback audition in {AUDITION_DAYS} days")
        except Exception:
            log.debug("prune event failed", exc_info=True)
        return {"mode": mode, "adopted": True, "dropped": candidates,
                "total_dropped": len(new_dropped),
                "champ_lo": round(champ_lo, 4), "chall_lo": round(chall_lo, 4)}

    # audition
    if adopt:
        repo.set_setting(key, {"features": [], "at": now.isoformat()})
        try:
            ev.record_event(repo, "features_readmitted",
                            f"Re-admitted {len(dropped)} previously-dropped "
                            f"feature(s) for {person_id} — they help now")
        except Exception:
            log.debug("audition event failed", exc_info=True)
        return {"mode": mode, "adopted": True, "readmitted": dropped}
    repo.set_setting(key, {"features": dropped, "at": now.isoformat()})  # next in 30d
    return {"mode": mode, "adopted": False,
            "champ_lo": round(champ_lo, 4), "chall_lo": round(chall_lo, 4)}


def holdout_permutation_importance(est, X_val: pd.DataFrame, y_val: pd.Series,
                                   *, n_repeats: int = N_REPEATS,
                                   seed: int = 42) -> dict[str, float]:
    """{column: mean drop in held-out accuracy when that column is shuffled}.

    Skips columns that are constant in the val slice (shuffling is a no-op —
    their importance is exactly 0, free). Returns {} when the val slice is too
    small to trust (< MIN_VAL_ROWS), so the caller can fall back to impurity."""
    if len(X_val) < MIN_VAL_ROWS or X_val.shape[1] == 0:
        return {}
    rng = np.random.default_rng(seed)

    def _acc(X) -> float:
        pred = est.predict_proba(X).idxmax(axis=1)
        return float((pred.to_numpy() == y_val.to_numpy()).mean())

    base = _acc(X_val)
    out: dict[str, float] = {}
    Xw = X_val.copy()
    for col in X_val.columns:
        vals = Xw[col].to_numpy()
        if len(np.unique(vals)) <= 1:          # constant in val → cannot matter
            out[col] = 0.0
            continue
        orig = vals.copy()
        drops = []
        for _ in range(n_repeats):
            Xw[col] = rng.permutation(orig)
            drops.append(base - _acc(Xw))
        Xw[col] = orig
        out[col] = float(np.mean(drops))
    return out
