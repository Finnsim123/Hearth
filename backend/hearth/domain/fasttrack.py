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
import math
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

FALLBACK_DAYS = 90          # used only if the earliest-time probe fails
FEATURE_CHUNK_DAYS = 90     # build features one slice at a time (bounds memory on
                            # multi-year backfills — a 5-y 1-min resample at once
                            # would never fit in RAM)


def _status(repo, stage: str, **extra) -> None:
    repo.set_setting("fasttrack.status",
                     {"stage": stage, "at": datetime.now(timezone.utc).isoformat(), **extra})


async def run_fast_track(repo, tsdb, store, notifier=None, events=None) -> None:
    pending = repo.get_setting("fasttrack.pending")
    if not pending or tsdb is None:
        return
    end = datetime.now(timezone.utc)
    # Two warm-start sources: the HA recorder (history API, ~10 days, works for
    # every home) or a pre-existing external HA→Influx bucket (longer history).
    recorder = pending.get("source") == "recorder"
    source_bucket = pending.get("source_bucket")

    from ..adapters.influx_import import (
        earliest_source_time, import_history, import_recorder_history)
    if recorder:
        days = int(pending.get("days", 10))
        start = end - timedelta(days=days)
        log.info("fast track: warm start from HA recorder (%d days)", days)
    else:
        # Import the FULL history this home recorded — not a fixed window. Probe
        # the source bucket's earliest timestamp; optionally cap with
        # import.max_days (0/unset = no cap → take everything).
        earliest = await asyncio.to_thread(earliest_source_time, tsdb, source_bucket)
        start = earliest or (end - timedelta(days=FALLBACK_DAYS))
        cap_days = int(repo.get_setting("import.max_days", 0) or 0)
        if cap_days:
            start = max(start, end - timedelta(days=cap_days))
        log.info("fast track: importing from %s", source_bucket)
    span_days = max(1, (end - start).days)

    try:
        _status(repo, "importing", span_days=span_days,
                **({} if recorder else {"source_bucket": source_bucket}))
        bindings = [b for b in repo.bindings() if b.enabled]
        if recorder:
            if events is None:
                raise RuntimeError("recorder warm-start needs a Home Assistant connection")
            results = await import_recorder_history(events, tsdb, bindings, start, end, repo)
        else:
            results = await asyncio.to_thread(
                import_history, tsdb, source_bucket, bindings, start, end)
        imported = sum(results.values())
        _status(repo, "imported", points=imported)
        log.info("fast track: %d points imported", imported)

        # prune empties: a sensor with NO imported history is an empty column —
        # it can only add noise to the feature matrix. Disable (not delete) so
        # it's excluded from features but reviewable/re-enablable on the Sensors
        # page once it starts producing data. person bindings are always kept.
        pruned = []
        from ..schemas import Role
        for b in bindings:
            if results.get(b.name, 0) == 0 and b.role != Role.PERSON:
                b.enabled = False
                repo.save_binding(b)
                pruned.append(b.name)
        if pruned:
            _status(repo, "pruned_empty", count=len(pruned))
            log.info("fast track: disabled %d empty sensors: %s",
                     len(pruned), ", ".join(pruned[:12]))
            repo.set_setting("fasttrack.pruned", pruned)
        # rebuild the live binding list (pruned ones excluded downstream)
        bindings = [b for b in repo.bindings() if b.enabled]

        # Build features across the whole imported span, one time-slice at a
        # time so peak memory stays flat no matter how far back history goes.
        _status(repo, "building_features", span_days=span_days)
        from .features.pipeline import build_windows
        people = [p for p in repo.persons() if p.enabled]
        n_chunks = math.ceil(span_days / FEATURE_CHUNK_DAYS)
        n_windows = 0
        for ci in range(n_chunks):
            cstart = start + timedelta(days=ci * FEATURE_CHUNK_DAYS)
            cstop = min(cstart + timedelta(days=FEATURE_CHUNK_DAYS), end)
            for person in people:
                feats = await asyncio.to_thread(
                    build_windows, tsdb, repo, person.id, cstart, cstop, 30)
                n_windows += len(feats)
            _status(repo, "building_features", span_days=span_days,
                    chunk=ci + 1, of=n_chunks, windows=n_windows)
        log.info("fast track: built %d windows over %d days", n_windows, span_days)
        _status(repo, "features_built", windows=n_windows)

        # Train over the full imported span (recency weighting in the trainer
        # down-weights old windows, so this is safe even with years of history).
        _status(repo, "training")
        from .training.trainer import train_person
        weeks = math.ceil(span_days / 7) + 1
        trained = []
        for person in people:
            record = await asyncio.to_thread(
                train_person, person.id, tsdb, repo, store, weeks, True)
            if record is not None:
                trained.append(record.version)
        _status(repo, "trained", models=trained)

        from .inference.predictor import predict_latest
        await predict_latest(tsdb, repo, store, None, None)

        # Discovery: cluster the windows the rules couldn't explain into routines
        # the user can name — turns "your model is ready" into "here are 4
        # patterns I found in your history, name them in a tap".
        _status(repo, "discovering")
        try:
            from .discovery.clustering import run_discovery
            found = await asyncio.to_thread(run_discovery, tsdb, repo)
            _status(repo, "discovered", found=len(found))
        except Exception:
            log.exception("fast track: discovery failed")

        if notifier is not None:
            from .milestones import check_milestones
            await check_milestones(repo, tsdb, notifier)

        _status(repo, "done", points=imported, windows=n_windows, models=trained)
        repo.set_setting("fasttrack.pending", None)
        log.info("fast track complete: %s windows, models %s", n_windows, trained)
    except Exception as exc:
        log.exception("fast track failed")
        _status(repo, "failed", error=str(exc))
        # surface a database-unreachable failure on the buddy, like the live jobs do
        s = f"{type(exc).__name__} {exc}".lower()
        if any(k in s for k in ("timed out", "timeout", "connection", "max retries",
                                "newconnectionerror", "protocolerror", "8086")):
            from .health import record_issue
            record_issue(repo, "influx_unreachable", "I can't reach your database",
                         "InfluxDB timed out while I was learning from your history — "
                         "I'll pick up where I left off once it's reachable.",
                         cta={"label": "Logs", "href": "/settings#logs"})
