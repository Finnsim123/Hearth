"""Job registry — all periodic work in one place (APScheduler).

Jobs are thin wrappers that fetch dependencies from the composition root and
call ONE domain service function. Every job is idempotent and writes a
heartbeat; the UI's system strip and Grafana alerts watch those heartbeats.

| job              | default cadence | domain entrypoint                       |
|------------------|-----------------|------------------------------------------|
| window_builder   | 5 min           | features.pipeline.build_latest_windows   |
| inference        | on new window   | inference.predictor.predict_latest       |
| ask_policy       | after inference | labeling.active.maybe_ask                |
| training         | weekly + manual | training.trainer.train_person            |
| discovery        | nightly         | discovery.clustering.run_discovery       |
| ha_gap_fill      | on reconnect    | adapters.ha_websocket.backfill           |
"""
from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Phase 1+: register real jobs here, e.g.
    # scheduler.add_job(build_latest_windows, "interval",
    #                   seconds=settings.window_builder_interval, id="window_builder",
    #                   max_instances=1, coalesce=True)
    _ = settings  # placeholder until jobs land
    return scheduler
