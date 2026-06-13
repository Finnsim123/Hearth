"""Job registry — all periodic work in one place (APScheduler).

| job              | cadence            | entrypoint                          |
|------------------|--------------------|--------------------------------------|
| window_builder   | every 5 min (cfg)  | features.pipeline.build_latest_windows |
| ingest           | long-running task  | domain.ingest.run_ingest             |
(training / discovery jobs land in Phase 2/4)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from .domain.features.pipeline import build_latest_windows
from .domain.ingest import run_ingest
from .domain.inference.predictor import predict_latest
from .domain.labeling.active import expire_stale_questions
from .domain.milestones import check_milestones
from .domain.training.trainer import train_person

log = logging.getLogger(__name__)


def build_scheduler(deps: dict) -> AsyncIOScheduler:
    """deps: {'repo': AppRepo, 'tsdb': TimeSeriesStore|None, 'events': EventSource|None}"""
    scheduler = AsyncIOScheduler(timezone="UTC")
    repo, tsdb, events = deps.get("repo"), deps.get("tsdb"), deps.get("events")

    if tsdb is not None:
        scheduler.add_job(build_latest_windows, "interval",
                          seconds=settings.window_builder_interval,
                          args=[tsdb, repo], id="window_builder",
                          max_instances=1, coalesce=True)

    if tsdb is not None:
        scheduler.add_job(predict_latest, "interval", minutes=5,
                          args=[tsdb, repo, deps.get("models"),
                                deps.get("publisher"), deps.get("notifier")],
                          id="inference", max_instances=1, coalesce=True)

        def _set_training(running: bool) -> None:
            repo.set_setting("training.status",
                             {"running": running, "at": datetime.now(timezone.utc).isoformat()})

        def _train_all() -> None:
            _set_training(True)
            try:
                for person in repo.persons():
                    if not person.enabled:
                        continue
                    try:
                        train_person(person.id, tsdb, repo, deps.get("models"))
                    except Exception:
                        log.exception("weekly training failed for %s", person.id)
            finally:
                _set_training(False)

        scheduler.add_job(_train_all, "cron", day_of_week="sun", hour=3,
                          id="weekly_training", max_instances=1)

        def _first_train_if_ready() -> None:
            """Cold-start accelerator: a fresh no-history install shouldn't wait
            until Sunday for its first model. As soon as a person has enough
            feature windows, train + promote — then this becomes a no-op."""
            from .domain.features.registry import active_feature_set_version
            from .domain.training.trainer import MIN_TRAIN_WINDOWS
            fset = active_feature_set_version(repo)
            now = datetime.now(timezone.utc)
            for person in repo.persons():
                if not person.enabled:
                    continue
                if any(m.promoted for m in repo.models(person.id)):
                    continue                                  # already live
                try:
                    feats = tsdb.read_features(person.id, fset, now - timedelta(weeks=8), now)
                    if len(feats) < MIN_TRAIN_WINDOWS:
                        continue
                    _set_training(True)
                    train_person(person.id, tsdb, repo, deps.get("models"))
                except Exception:
                    log.exception("first-train check failed for %s", person.id)
                finally:
                    _set_training(False)

        scheduler.add_job(_first_train_if_ready, "interval", minutes=30,
                          id="first_train", max_instances=1, coalesce=True)

        def _discover_all() -> None:
            from .domain.discovery.clustering import run_discovery
            run_discovery(tsdb, repo)

        # Saturday: fresh pattern candidates waiting in the UI before Sunday's
        # retrain — name one and the very next training run learns from it.
        scheduler.add_job(_discover_all, "cron", day_of_week="sat", hour=4,
                          id="weekly_discovery", max_instances=1)
        scheduler.add_job(expire_stale_questions, "interval", hours=6,
                          args=[repo], id="question_expiry")

        if events is not None:
            async def _sync_inventory() -> None:
                from .domain.onboarding.inventory_sync import sync_inventory
                await sync_inventory(repo, events, use_llm=False)

            # daily: pick up new sensors / renamed entities / new HA areas
            scheduler.add_job(_sync_inventory, "interval", hours=24,
                              id="inventory_sync", max_instances=1, coalesce=True)

    if tsdb is not None and deps.get("notifier") is not None:
        scheduler.add_job(check_milestones, "interval", minutes=30,
                          args=[repo, tsdb, deps["notifier"]], id="milestones",
                          max_instances=1, coalesce=True)

    if tsdb is not None and events is not None:
        from .domain.inference.realtime import RealtimeSignal, realtime_loop
        signal = RealtimeSignal()
        deps["realtime_signal"] = signal

        async def _ingest_forever() -> None:
            while True:
                try:
                    await run_ingest(events, tsdb, repo, signal)
                    await asyncio.sleep(30)   # no bindings yet -> poll for some
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("ingest crashed — restarting in 10 s")
                    await asyncio.sleep(10)

        scheduler.add_job(_ingest_forever, id="ingest", next_run_time=None)
        # started as a one-shot task from main (long-running, not interval)
        deps["ingest_coro"] = _ingest_forever

        async def _realtime_forever() -> None:
            while True:
                try:
                    await realtime_loop(tsdb, repo, deps.get("models"), signal,
                                        deps.get("notifier"))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("realtime lane crashed — restarting in 10 s")
                    await asyncio.sleep(10)

        deps["realtime_coro"] = _realtime_forever
    return scheduler
