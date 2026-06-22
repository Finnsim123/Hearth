"""Foundational facts API — bind a sensor to a gate, see its reliability verdict,
toggle it, and run the verdict scorer on demand (the wizard 'test it' button).

bind(repo, tsdb) in main.py, then app.include_router(foundational_routes.router).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domain.foundational.facts import (
    ASLEEP,
    AWAY,
    FoundationalFact,
    candidate_bindings,
    load_facts,
    load_verdicts,
    run_verdicts,
    save_facts,
)
from ..domain.foundational.facts import _GATE_ROLE  # gate → required Role

router = APIRouter(prefix="/api/foundational", tags=["foundational"])

_repo = None
_tsdb = None


def bind(repo, tsdb=None) -> None:
    global _repo, _tsdb
    _repo, _tsdb = repo, tsdb


@router.get("")
def list_facts() -> dict:
    if _repo is None:
        return {"facts": [], "candidates": {}}
    verdicts = load_verdicts(_repo)
    facts = [{**f.model_dump(mode="json"), "verdict": verdicts.get(f.id)}
             for f in load_facts(_repo)]
    return {"facts": facts,
            "candidates": {AWAY: candidate_bindings(_repo, AWAY),
                           ASLEEP: candidate_bindings(_repo, ASLEEP)}}


@router.post("")
def upsert_fact(body: dict) -> dict:
    gate = str(body.get("gate", "")).lower()
    binding_name = body.get("binding_name")
    person_id = body.get("person_id")
    role = _GATE_ROLE.get(gate)
    if gate not in (AWAY, ASLEEP) or not binding_name or role is None:
        raise HTTPException(400, "gate must be 'away' or 'asleep' with a binding_name")
    fid = f"{person_id or 'home'}:{gate}"
    facts = [f for f in load_facts(_repo) if f.id != fid]
    facts.append(FoundationalFact(id=fid, gate=gate, binding_name=binding_name,
                                  role=role, person_id=person_id))
    save_facts(_repo, facts)
    return {"ok": True, "id": fid}


@router.post("/{fid}/toggle")
def toggle_fact(fid: str) -> dict:
    facts = load_facts(_repo)
    found = False
    for f in facts:
        if f.id == fid:
            f.enabled = not f.enabled
            found = True
    if not found:
        raise HTTPException(404, "no such fact")
    save_facts(_repo, facts)
    return {"ok": True, "id": fid}


@router.delete("/{fid}")
def delete_fact(fid: str) -> dict:
    facts = [f for f in load_facts(_repo) if f.id != fid]
    save_facts(_repo, facts)
    return {"ok": True}


@router.post("/run")
def run_now() -> dict:
    if _tsdb is None:
        raise HTTPException(409, "no time-series database connected")
    return {"verdicts": run_verdicts(_tsdb, _repo)}
