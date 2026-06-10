"""API aggregation — one router per resource, all thin.

Full surface in docs/UI_SPEC.md. Routes validate/serialize with domain schemas
and call exactly one domain service; no business logic in this layer.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter


def build_api_router() -> APIRouter:
    api = APIRouter()

    @api.get("/health")
    def health() -> dict:
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

    # Phase 1+: include resource routers, e.g.
    # api.include_router(connections.router, prefix="/connections", tags=["connections"])
    # api.include_router(persons.router,     prefix="/persons",     tags=["household"])
    # api.include_router(bindings.router,    prefix="/bindings",    tags=["sensors"])
    # api.include_router(activities.router,  prefix="/activities",  tags=["taxonomy"])
    # api.include_router(inbox.router,       prefix="/inbox",       tags=["feedback"])
    # api.include_router(models_.router,     prefix="/models",      tags=["models"])
    # api.include_router(clusters.router,    prefix="/clusters",    tags=["discovery"])
    # api.include_router(feedback.router,    prefix="/feedback",    tags=["feedback"])
    return api
