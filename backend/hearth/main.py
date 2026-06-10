"""Composition root.

Builds adapters, injects them into domain services, mounts the API and the
static SPA, starts the scheduler. This is the ONLY module that knows about
concrete adapter classes — everything else sees Protocols.
"""
from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.router import build_api_router
from .config import settings
from .scheduler import build_scheduler

log = logging.getLogger("hearth")


def create_app() -> FastAPI:
    logging.basicConfig(level=settings.log_level)

    # --- adapters (stubs until Phase 1+) -----------------------------------
    # repo = AppDb(settings.db_path)
    # tsdb = InfluxStore(settings.influx_url, settings.influx_org, settings.influx_token)
    # events = HaWebSocketSource(repo)          # reads HA connection from repo
    # publisher = MqttPublisher(repo)
    # notifier = HaRestNotifier(repo)

    app = FastAPI(title="Hearth", version="0.0.1")
    app.include_router(build_api_router(), prefix="/api")

    static_dir = settings.data_dir.parent / "static"  # baked in by Dockerfile
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    @app.on_event("startup")
    async def _start() -> None:
        scheduler = build_scheduler()
        scheduler.start()
        log.info("Hearth up on :%s", settings.port)

    return app


def main() -> None:
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
