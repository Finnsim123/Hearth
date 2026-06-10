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

        def _train_all() -> None:
            for person in repo.persons():
                if person.enabled:
                    train_person(person.id, tsdb, repo, deps.get("models"))

        scheduler.add_job(_train_all, "cron", day_of_week="sun", hour=3,
                          id="weekly_training", max_instances=1)
        scheduler.add_job(expire_stale_questions, "interval", hours=6,
                          args=[repo], id="question_expiry")

    if tsdb is not None and deps.get("notifier") is not None:
        scheduler.add_job(check_milestones, "interval", minutes=30,
                          args=[repo, tsdb, deps["notifier"]], id="milestones",
                          max_instances=1, coalesce=True)

    if tsdb is not None and events is not None:
        async def _ingest_forever() -> None:
            while True:
                try:
                    await run_ingest(events, tsdb, repo)
                    await asyncio.sleep(30)   # no bindings yet -> poll for some
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("ingest crashed — restarting in 10 s")
                    await asyncio.sleep(10)

        scheduler.add_job(_ingest_forever, id="ingest", next_run_time=None)
        # started as a one-shot task from main (long-running, not interval)
        deps["ingest_coro"] = _ingest_forever
    return scheduler
