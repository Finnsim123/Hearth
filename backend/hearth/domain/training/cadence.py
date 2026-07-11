"""Adaptive training cadence — train hard while learning, ease off when stable.

Training takes seconds, so the old fixed weekly cron wasted the most valuable
period: a young model improves with almost every day of new data and labels, and
should retrain DAILY. But retraining on essentially-unchanged data yields an
essentially-unchanged model — so once the promotion gate keeps saying "no better",
daily runs are pointless heat. This module decides, per person, whether today's
run is worth it, using only the model registry (no extra state to maintain):

  streak = consecutive root-model training runs with NO material improvement
           (not promoted, or promoted without beating the best headline by
           EPS — with a reset when a run gained a meaningful batch of new
           confirmed labels, because new human signal deserves eager training)

  interval = 1 day   while streak < 3        (young / still improving)
             3 days  while streak < 5        (plateauing)
             7 days  after                   (stable — the old weekly cadence)

Manual "Train now" always works regardless; a promoted improvement or a label
burst snaps the cadence back to daily automatically.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

EPS = 0.005            # headline must improve by >0.5pp to count as "better"
NEW_LABELS_RESET = 10  # a run that gained ≥ this many confirmed labels = fresh signal
INTERVALS = ((3, 1), (5, 3))   # (streak below, interval days); else MAX_INTERVAL
MAX_INTERVAL_DAYS = 7


def _headline(metrics: dict) -> float | None:
    """The most honest accuracy available (mirrors the promotion gate's order)."""
    m = metrics or {}
    if (m.get("n_gold") or 0) >= 30 and m.get("accuracy_gold") is not None:
        return float(m["accuracy_gold"])
    if m.get("accuracy_confirmed") is not None:
        return float(m["accuracy_confirmed"])
    if m.get("accuracy_bootstrap") is not None:
        return float(m["accuracy_bootstrap"])
    return None


def improvement_streak(records: list) -> int:
    """Consecutive most-recent root-model runs with no material improvement.
    `records` = ModelRecords (any node/order); pure."""
    roots = sorted((r for r in records if getattr(r, "node", "root") == "root"),
                   key=lambda r: (r.trained_at or datetime.min.replace(tzinfo=timezone.utc)))
    streak = 0
    best: float | None = None
    prev_confirmed: int | None = None
    for r in roots:
        h = _headline(r.metrics)
        confirmed = (r.label_counts or {}).get("confirmed", 0)
        label_burst = (prev_confirmed is not None
                       and confirmed - prev_confirmed >= NEW_LABELS_RESET)
        improved = bool(r.promoted and h is not None
                        and (best is None or h > best + EPS))
        if r.promoted and h is not None:
            best = h if best is None else max(best, h)
        streak = 0 if (improved or label_burst) else streak + 1
        prev_confirmed = confirmed
    return streak


def interval_days(streak: int) -> int:
    for below, days in INTERVALS:
        if streak < below:
            return days
    return MAX_INTERVAL_DAYS


def cadence_for(repo, person_id: str, now: datetime | None = None) -> dict:
    """{due, streak, interval_days, last_trained, phase} for one person."""
    now = now or datetime.now(timezone.utc)
    records = [m for m in repo.models(person_id)]
    roots = [r for r in records if getattr(r, "node", "root") == "root" and r.trained_at]
    if not roots:
        return {"due": True, "streak": 0, "interval_days": 1,
                "last_trained": None, "phase": "first"}
    streak = improvement_streak(records)
    days = interval_days(streak)
    last = max(r.trained_at for r in roots)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    due = now - last >= timedelta(days=days) - timedelta(hours=2)  # cron-jitter slack
    phase = "learning" if days == 1 else ("plateauing" if days < MAX_INTERVAL_DAYS
                                          else "stable")
    return {"due": due, "streak": streak, "interval_days": days,
            "last_trained": last.isoformat(), "phase": phase}


def should_train(repo, person_id: str, now: datetime | None = None) -> bool:
    try:
        return bool(cadence_for(repo, person_id, now)["due"])
    except Exception:
        log.exception("cadence check failed for %s — training anyway", person_id)
        return True
