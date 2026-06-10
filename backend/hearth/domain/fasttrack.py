"""Fast track — homes that arrive with history skip the waiting week.

Triggered once after setup when an existing-Influx source bucket was chosen:
  import history -> backfill features -> train (force) -> predict -> milestones.
Progress lands in settings("fasttrack.status") so the dashboard journey card
can narrate it live. Failures stop the stage but never brick the app —
steady-state ingest continues regardless.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

IMPORT_DAYS = 90


def _status(repo, stage: str, **extra) -> None:
    repo.set_setting("fasttrack.status",
                     {"stage": stage, "at": datetime.now(timezone.utc).isoformat(), **extra})


async def run_fast_track(repo, tsdb, store, notifier=None) -> None:
    pending = repo.get_setting("fasttrack.pending")
    if not pending or tsdb is None:
        return
    source_bucket = pending.get("source_bucket")
    log.info("fast track: importing from %s", source_bucket)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=IMPORT_DAYS)

    try:
        _status(repo, "importing", source_bucket=source_bucket)
        from ..adapters.influx_import import import_history
        bindings = [b for b in repo.bindings() if b.enabled]
        results = await asyncio.to_thread(
            import_history, tsdb, source_bucket, bindings, start, end)
        imported = sum(results.values())
        _status(repo, "imported", points=imported)
        log.info("fast track: %d points imported", imported)

        _status(repo, "building_features")
        from .features.pipeline import build_windows
        n_windows = 0
        for person in repo.persons():
            if person.enabled:
                feats = await asyncio.to_thread(
                    build_windows, tsdb, repo, person.id, start, end, 30)
                n_windows += len(feats)
                log.info("fast track: %s -> %d windows", person.id, len(feats))
        _status(repo, "features_built", windows=n_windows)

        _status(repo, "training")
        from .training.trainer import train_person
        trained = []
        for person in repo.persons():
            if person.enabled:
                record = await asyncio.to_thread(
                    train_person, person.id, tsdb, repo, store, 12, True)
                if record is not None:
                    trained.append(record.version)
        _status(repo, "trained", models=trained)

        from .inference.predictor import predict_latest
        await predict_latest(tsdb, repo, store, None, None)

        if notifier is not None:
            from .milestones import check_milestones
            await check_milestones(repo, tsdb, notifier)

        _status(repo, "done", points=imported, windows=n_windows, models=trained)
        repo.set_setting("fasttrack.pending", None)
        log.info("fast track complete: %s windows, models %s", n_windows, trained)
    except Exception as exc:
        log.exception("fast track failed")
        _status(repo, "failed", error=str(exc))
