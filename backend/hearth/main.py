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

    # ── auth middleware (docs/SECURITY.md) ──────────────────────────────────
    # /api/* requires a session, except: health, login, the integration
    # webhook (TODO: bearer scope check), and — ONLY while no users exist —
    # the wizard's probe + setup endpoints. The SPA itself is public; it
    # gates itself on /api/auth/me.
    PUBLIC = {"/api/health", "/api/auth/login", "/api/feedback/action"}
    SETUP_ONLY = {"/api/setup/complete", "/api/ha/test", "/api/ha/inventory",
                  "/api/influx/inspect"}

    @app.middleware("http")
    async def _auth(request, call_next):
        import hashlib

        from fastapi.responses import JSONResponse
        path = request.url.path
        if not path.startswith("/api/") or path in PUBLIC:
            return await call_next(request)
        repo = deps["repo"]
        if path in SETUP_ONLY and repo.user_count() == 0:
            return await call_next(request)
        cookie = request.cookies.get("hearth_session")
        user = repo.session_user(
            hashlib.sha256(cookie.encode()).hexdigest()) if cookie else None
        if user is None:
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        request.state.user = user
        return await call_next(request)

    import os
    from pathlib import Path
    static_dir = Path(os.getenv("HEARTH_STATIC_DIR", "/app/static"))
    if static_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        from fastapi.responses import FileResponse

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            # SPA fallback: real files (favicon etc.) served as-is, every
            # client route (/onboarding, /inbox?q=..) gets index.html.
            candidate = static_dir / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    @app.on_event("startup")
    async def _start() -> None:
        scheduler = build_scheduler(deps)
        scheduler.start()
        if deps.get("ingest_coro"):
            app.state.ingest_task = asyncio.create_task(deps["ingest_coro"]())
        if deps["repo"].get_setting("fasttrack.pending") and deps.get("tsdb"):
            from .domain.fasttrack import run_fast_track
            app.state.fasttrack_task = asyncio.create_task(
                run_fast_track(deps["repo"], deps["tsdb"], deps["models"],
                               deps.get("notifier")))
        log.info("Hearth up on :%s (tsdb=%s, ha=%s)", settings.port,
                 bool(deps["tsdb"]), bool(deps["events"]))

    return app


def main() -> None:
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
