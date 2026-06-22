"""System vitals + governor + coverage API (system_observability_and_governor_design
§3; llm_vs_statistics_and_discovery_audit §5).

Self-contained router with a bind() seam — no assumption about a DI container. In
main.py:  system_routes.bind(monitor, repo); app.include_router(system_routes.router).
Governor state lives in domain.system.runtime, shared with the scheduler.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domain.system import runtime
from ..domain.system.governor import plan_for
from ..domain.system.vitals import GovernorConfig, heaviness_index

router = APIRouter(prefix="/api/system", tags=["system"])

_repo = None


def _config_from_repo(repo):
    def fn() -> GovernorConfig:
        try:
            raw = repo.get_setting("system.governor")
            if isinstance(raw, dict) and raw:
                return GovernorConfig(**{**GovernorConfig().model_dump(), **raw})
        except Exception:
            pass
        return GovernorConfig()
    return fn


def bind(monitor, repo=None) -> None:
    """Wire the resource monitor + repo. Registers the governor's config source."""
    global _repo
    _repo = repo
    runtime.bind(monitor, _config_from_repo(repo) if repo is not None else None)


@router.get("/vitals")
def get_vitals() -> dict:
    v, state = runtime.refresh()
    return {"vitals": v.model_dump(mode="json"),
            "heaviness": round(heaviness_index(v, runtime.config()), 3),
            "state": state.name.lower(),
            "plan": plan_for(state).model_dump(mode="json")}


@router.get("/state")
def get_state() -> dict:
    s = runtime.state()
    return {"state": s.name.lower(), "plan": plan_for(s).model_dump(mode="json")}


@router.post("/mode")
def set_mode(body: dict) -> dict:
    mode = str(body.get("mode", "")).lower()
    s = runtime.set_mode(mode)
    if _repo is not None:
        try:
            _repo.set_setting("system.mode", mode)
        except Exception:
            pass
    return {"state": s.name.lower()}


@router.get("/history")
def get_history() -> dict:
    """Rolling vitals history the governor tick records (last ~180 samples) — for
    the System page's sparkline. Each point: {t, cpu, temp, mem, watts, h, state}."""
    hist = []
    if _repo is not None:
        try:
            h = _repo.get_setting("system.vitals.history")
            hist = h if isinstance(h, list) else []
        except Exception:
            hist = []
    return {"history": hist}


@router.get("/config")
def get_config() -> dict:
    """Current governor thresholds (effective) + the defaults, for the editor."""
    return {"config": runtime.config().model_dump(mode="json"),
            "defaults": GovernorConfig().model_dump(mode="json")}


@router.post("/config")
def set_config(body: dict) -> dict:
    """Validate + persist governor thresholds to the `system.governor` setting.
    Takes effect on the next governor tick (config() re-reads it; no restart)."""
    if _repo is None:
        raise HTTPException(503, "no store")
    base = GovernorConfig().model_dump()
    merged = {**base, **{k: v for k, v in (body or {}).items() if k in base}}
    try:
        cfg = GovernorConfig(**merged)
    except Exception as exc:                       # bad types / out of range
        raise HTTPException(400, f"invalid config: {exc}")
    if not (0 < cfg.enter_elevated < cfg.enter_high < cfg.enter_critical <= 1.0):
        raise HTTPException(400, "thresholds must satisfy 0 < elevated < high < critical ≤ 1")
    if not (0 < cfg.temp_warn < cfg.temp_max):
        raise HTTPException(400, "temp_warn must be above 0 and below temp_max")
    _repo.set_setting("system.governor", cfg.model_dump(mode="json"))
    return {"config": cfg.model_dump(mode="json")}


@router.get("/coverage")
def get_coverage() -> dict:
    """Blind-spot advisor — ranked 'add a sensor' recommendations from the promoted
    models' confusion + the home's room coverage. LLM-free; deterministic phrasing."""
    if _repo is None:
        return {"gaps": []}
    from ..domain.coverage.advisor import gaps_from_home
    gaps = gaps_from_home(_repo)
    return {"gaps": [g.model_dump(mode="json") for g in gaps]}
