"""Composition root — the ONLY module that knows concrete adapter classes."""
from __future__ import annotations

import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .adapters.app_db import AppDb, FileModelStore
from .api.router import build_api_router
from .config import settings
from .scheduler import build_scheduler

log = logging.getLogger("hearth")


def build_deps() -> dict:
    repo = AppDb(settings.db_path)
    repo.migrate()
    deps: dict = {"repo": repo, "models": FileModelStore(settings.models_dir),
                  "tsdb": None, "events": None}

    influx = repo.get_connection("influx") or (
        {"url": settings.influx_url, "token": settings.influx_token,
         "options": {"org": settings.influx_org}}
        if settings.influx_token else None)
    if influx:
        from .adapters.influx_store import InfluxStore
        tsdb = InfluxStore(influx["url"], influx["options"].get("org", "hearth"),
                           influx["token"])
        if tsdb.ping():
            tsdb.ensure_buckets()
            deps["tsdb"] = tsdb
        else:
            log.warning("InfluxDB configured but unreachable — pipeline paused")

    if repo.get_connection("ha"):
        from .adapters.ha_rest import HaRestNotifier
        from .adapters.ha_websocket import HaWebSocketSource
        deps["events"] = HaWebSocketSource(repo)
        deps["notifier"] = HaRestNotifier(repo)
    return deps


def create_app() -> FastAPI:
    logging.basicConfig(level=settings.log_level)
    deps = build_deps()

    app = FastAPI(title="Hearth", version="0.1.0")
    app.state.deps = deps
    app.include_router(build_api_router(deps), prefix="/api")

    static_dir = settings.data_dir.parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    @app.on_event("startup")
    async def _start() -> None:
        scheduler = build_scheduler(deps)
        scheduler.start()
        if deps.get("ingest_coro"):
            app.state.ingest_task = asyncio.create_task(deps["ingest_coro"]())
        log.info("Hearth up on :%s (tsdb=%s, ha=%s)", settings.port,
                 bool(deps["tsdb"]), bool(deps["events"]))

    return app


def main() -> None:
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
