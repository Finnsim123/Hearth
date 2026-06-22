"""Feature-drift monitor (audit F5).

`population_stability_index` already measures drift correctly; nothing called it
live. This computes per-feature PSI between the window the promoted model TRAINED
on and recent production windows, flags features over the >0.2 investigate bar,
stores a report (for the Sensors/Model UI + a trend), and — when severe — surfaces
a health issue and optionally triggers a retrain. Recency weighting softens drift
but does not DETECT a regime change (your summer-vs-winter example); this does.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..features.pipeline import TEMPORAL_COLS
from .evaluate import population_stability_index
from .trainer import load_training_config

log = logging.getLogger(__name__)

PSI_INVESTIGATE = 0.2   # standard "look into it" bar
PSI_SEVERE = 0.5        # "the home has clearly changed" bar
RECENT_DAYS = 7         # production window compared against the training span


def compute_drift(person_id: str, tsdb, repo, recent_days: int = RECENT_DAYS) -> dict:
    """Per-feature PSI (training span vs last `recent_days`) for one person.
    Returns {} when there's no promoted model or not enough data to compare."""
    from ..features.registry import active_feature_set_version

    record = next((m for m in repo.models(person_id)
                   if m.promoted and m.node == "root"), None)
    if record is None or record.trained_at is None:
        return {}
    cfg = load_training_config(repo)
    fset = active_feature_set_version(repo)
    now = datetime.now(timezone.utc)
    trained_at = record.trained_at
    if trained_at.tzinfo is None:
        trained_at = trained_at.replace(tzinfo=timezone.utc)
    weeks = cfg.train_weeks if cfg.train_weeks and cfg.train_weeks > 0 else 8
    train_start = trained_at - timedelta(weeks=weeks)

    expected = tsdb.read_features(person_id, fset, train_start, trained_at)
    actual = tsdb.read_features(person_id, fset, now - timedelta(days=recent_days), now)
    if len(expected) < 30 or len(actual) < 30:
        return {}

    cols = [c for c in expected.columns
            if c in actual.columns and c not in TEMPORAL_COLS]
    psi = {}
    for c in cols:
        e, a = expected[c].dropna(), actual[c].dropna()
        if e.empty or a.empty or e.nunique() < 2:
            continue
        psi[c] = round(population_stability_index(e, a), 4)
    if not psi:
        return {}
    drifted = sorted((c for c, v in psi.items() if v > PSI_INVESTIGATE),
                     key=lambda c: -psi[c])
    return {
        "person_id": person_id,
        "model_version": record.version,
        "computed_at": now.isoformat(),
        "recent_days": recent_days,
        "n_expected": int(len(expected)),
        "n_actual": int(len(actual)),
        "psi": psi,
        "drifted": drifted,
        "max_psi": max(psi.values()),
        "severe": any(v > PSI_SEVERE for v in psi.values()),
    }


def run_drift_check(tsdb, repo, store=None) -> list[dict]:
    """Scheduler entrypoint: per-person drift report, persisted to the
    `drift.<person_id>` setting (with a short trend), a health issue when
    severe, and an opt-in retrain (`drift.auto_retrain` setting)."""
    from ..health import clear_issue, record_issue

    reports: list[dict] = []
    auto_retrain = bool(repo.get_setting("drift.auto_retrain", False))
    for person in repo.persons():
        if not person.enabled:
            continue
        try:
            report = compute_drift(person.id, tsdb, repo)
        except Exception:
            log.exception("drift check failed for %s", person.id)
            continue
        if not report:
            continue
        prev = repo.get_setting(f"drift.{person.id}") or {}
        trend = (prev.get("trend") or [])[-9:] + [report["max_psi"]]
        report["trend"] = trend
        repo.set_setting(f"drift.{person.id}", report)
        reports.append(report)

        if report["drifted"]:
            top = ", ".join(report["drifted"][:3])
            record_issue(
                repo, f"drift_{person.id}", "Your home's signals have shifted",
                f"{len(report['drifted'])} feature(s) drifted since {person.name or person.id}'s "
                f"model was trained (e.g. {top}). Predictions may degrade — a retrain "
                f"will recalibrate to the new normal.",
                cta={"label": "Train now", "href": "/settings#model"})
            log.info("[drift:%s] %d drifted (max PSI %.2f)%s", person.id,
                     len(report["drifted"]), report["max_psi"],
                     " — severe" if report["severe"] else "")
            if auto_retrain and report["severe"] and store is not None:
                from .trainer import train_person
                try:
                    train_person(person.id, tsdb, repo, store)
                    log.info("[drift:%s] auto-retrained on severe drift", person.id)
                except Exception:
                    log.exception("drift auto-retrain failed for %s", person.id)
        else:
            clear_issue(repo, f"drift_{person.id}")
    return reports
