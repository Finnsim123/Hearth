"""Composition root — the ONLY module that knows concrete adapter classes."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

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
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    from .adapters.email_sender import EmailSender
    deps: dict = {"repo": repo, "models": FileModelStore(settings.models_dir),
                  "uploads_dir": settings.uploads_dir, "tsdb": None, "events": None,
                  "email": EmailSender(repo)}

    influx = repo.get_connection("influx") or (
        {"url": settings.influx_url, "token": settings.influx_token,
         "options": {"org": settings.influx_org}}
        if settings.influx_token else None)
    if influx:
        from .adapters.influx_store import InfluxStore
        tsdb = InfluxStore(influx["url"], influx["options"].get("org", "hearth"),
                           influx["token"])
        if tsdb.ping():
            # retention.days bounds the RAW bucket only (Settings → Model);
            # features + ml are kept forever. Create missing buckets at that raw
            # window AND realign existing ones, so an upgrade from the old shared
            # retention drops features/ml back to 'forever' automatically.
            from .adapters.influx_store import DEFAULT_RAW_RETENTION_DAYS
            days = repo.get_setting("retention.days", DEFAULT_RAW_RETENTION_DAYS)
            days = days if isinstance(days, int) else DEFAULT_RAW_RETENTION_DAYS
            tsdb.ensure_buckets(days)
            try:
                tsdb.set_retention(days)
            except Exception:
                log.warning("could not apply retention=%sd to existing buckets", days)
            deps["tsdb"] = tsdb
        else:
            log.warning("InfluxDB configured but unreachable — pipeline paused")

    if repo.get_connection("ha"):
        from .adapters.ha_rest import HaRestNotifier
        from .adapters.ha_websocket import HaWebSocketSource
        deps["events"] = HaWebSocketSource(repo)
        deps["notifier"] = HaRestNotifier(repo)
    # Optional MQTT output (ADR-5): publishes predictions as HA-discovery entities
    # for broker-centric or non-HA hubs. The HA integration is the primary path;
    # this only activates when an MQTT broker is configured.
    if repo.get_connection("mqtt"):
        from .adapters.mqtt_publisher import MqttPublisher
        deps["publisher"] = MqttPublisher(repo)
    return deps


def create_app() -> FastAPI:
    logging.basicConfig(level=settings.log_level)
    # In-memory ring buffer so the Logs page can show recent activity (GET
    # /api/logs) without docker access. Attached to the root logger once.
    from .adapters.logbuffer import RingBufferHandler
    root = logging.getLogger()
    if not any(isinstance(h, RingBufferHandler) for h in root.handlers):
        log_buffer = RingBufferHandler()
        log_buffer.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(log_buffer)
    else:
        log_buffer = next(h for h in root.handlers if isinstance(h, RingBufferHandler))
    deps = build_deps()
    deps["log_buffer"] = log_buffer

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # idempotent: existing installs get activity parents without re-seeding
        try:
            from .domain.labeling.taxonomy import ensure_hierarchy
            ensure_hierarchy(deps["repo"])
        except Exception:
            pass
        scheduler = build_scheduler(deps)
        scheduler.start()
        # publish MQTT discovery once on boot (idempotent, retained) so entities
        # exist before the first prediction; no-op without a broker.
        if deps.get("publisher"):
            try:
                deps["publisher"].announce(deps["repo"].persons(), deps["repo"].activities())
            except Exception:
                log.exception("MQTT announce on startup failed")
        if deps.get("ingest_coro"):
            app.state.ingest_task = asyncio.create_task(deps["ingest_coro"]())
        if deps.get("realtime_coro"):
            app.state.realtime_task = asyncio.create_task(deps["realtime_coro"]())
        repo = deps["repo"]
        if repo.get_setting("seed.pending") or repo.get_setting("fasttrack.pending"):
            async def _seed_then_fasttrack() -> None:
                if repo.get_setting("seed.pending") and deps.get("events"):
                    from .domain.onboarding.seed import run_seed
                    await run_seed(repo, deps["events"])
                # don't warm-start while the triage is awaiting approval — the
                # pipeline must wait at the AI step until the user says go.
                if (repo.get_setting("fasttrack.pending") and deps.get("tsdb")
                        and not repo.get_setting("triage.awaiting")):
                    from .domain.fasttrack import run_fast_track
                    await run_fast_track(repo, deps["tsdb"], deps["models"],
                                         deps.get("notifier"), deps.get("events"))
            app.state.setup_task = asyncio.create_task(_seed_then_fasttrack())
        log.info("Hearth up on :%s (tsdb=%s, ha=%s)", settings.port,
                 bool(deps["tsdb"]), bool(deps["events"]))
        yield
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

    app = FastAPI(title="Hearth", version="0.1.0", lifespan=lifespan)
    app.state.deps = deps
    app.include_router(build_api_router(deps), prefix="/api")

    # system self-awareness: vitals + governor + blind-spot coverage. The monitor
    # is best-effort (psutil/RAPL/Pi); absent psutil it just yields empty vitals.
    import os as _os

    from .adapters.psutil_monitor import PsutilResourceMonitor
    from .api import system_routes
    system_routes.bind(
        PsutilResourceMonitor(data_path=_os.getenv("HEARTH_DATA_DIR", "/data"),
                              influx_health=deps.get("tsdb")),
        deps["repo"])
    app.include_router(system_routes.router)

    # foundational facts: bind a sensor to a gate (away/asleep), reliability-gated
    from .api import foundational_routes
    foundational_routes.bind(deps["repo"], deps.get("tsdb"))
    app.include_router(foundational_routes.router)

    # behaviour: habits & routines analytics over the prediction store
    from .api import behaviour_routes
    behaviour_routes.bind(deps["repo"], deps.get("tsdb"))
    app.include_router(behaviour_routes.router)

    # transition markers: events (alarm/coffee) that mark a state change
    from .api import markers_routes
    markers_routes.bind(deps["repo"])
    app.include_router(markers_routes.router)

    # ── auth middleware (docs/SECURITY.md) ──────────────────────────────────
    # /api/* requires a session, except: health, login, the integration
    # webhook (TODO: bearer scope check), and — ONLY while no users exist —
    # the wizard's probe + setup endpoints. The SPA itself is public; it
    # gates itself on /api/auth/me.
    from .api.scopes import integration_allowed
    PUBLIC = {"/api/health", "/api/auth/login", "/api/auth/reset", "/api/auth/forgot"}
    SETUP_ONLY = {"/api/setup/complete", "/api/ha/test", "/api/ha/inventory",
                  "/api/influx/inspect", "/api/tokens", "/api/feature-spec/estimate",
                  "/api/triage/preview"}

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
        authz = request.headers.get("authorization", "")
        if authz.startswith("Bearer hrt_"):
            scope = repo.api_token_scope(authz[7:])
            if scope == "integration" and integration_allowed(path, request.method):
                return await call_next(request)
            return JSONResponse({"detail": "Token invalid, revoked, or out of scope"},
                                status_code=403)
        cookie = request.cookies.get("hearth_session")
        user = repo.session_user(
            hashlib.sha256(cookie.encode()).hexdigest()) if cookie else None
        if user is None:
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        request.state.user = user
        return await call_next(request)

    import os
    from pathlib import Path
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")
    static_dir = Path(os.getenv("HEARTH_STATIC_DIR", "/app/static"))
    if static_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        from fastapi.responses import FileResponse

        static_root = static_dir.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            # SPA fallback: real files (favicon etc.) served as-is, every
            # client route (/onboarding, /inbox?q=..) gets index.html.
            # SECURITY: this route is unauthenticated, so resolve the path and
            # confirm it stays inside the static root — otherwise "../../etc/passwd"
            # (or %2e%2e) would escape and read arbitrary files incl. the DB.
            if path:
                try:
                    candidate = (static_root / path).resolve()
                    if candidate.is_file() and candidate.is_relative_to(static_root):
                        return FileResponse(candidate)
                except (ValueError, OSError):
                    pass
            return FileResponse(static_root / "index.html")

    return app


def main() -> None:
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
