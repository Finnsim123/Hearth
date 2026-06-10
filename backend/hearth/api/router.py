"""API aggregation — thin routes calling exactly one domain/adapter function.
Auth middleware arrives in Phase 2; LAN-only until then (docs/SECURITY.md)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ..domain.onboarding.advisor import heuristic_bindings
from ..domain.schemas import Activity, Binding, Person, Rule


def build_api_router(deps: dict) -> APIRouter:
    api = APIRouter()
    repo = deps["repo"]

    @api.get("/health")
    def health() -> dict:
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(),
                "tsdb": deps.get("tsdb") is not None,
                "ha": deps.get("events") is not None}

    # ── connections ────────────────────────────────────────────────────────
    @api.post("/connections/{kind}")
    def set_connection(kind: str, body: dict) -> dict:
        if kind not in ("ha", "influx", "mqtt", "llm"):
            raise HTTPException(400, "unknown connection kind")
        repo.set_connection(kind, body.get("url", ""), body.get("token", ""),
                            body.get("options"))
        return {"ok": True, "note": "restart-free reconnect lands in Phase 2; "
                                    "restart the container to apply for now"}

    @api.get("/connections/{kind}")
    def get_connection(kind: str) -> dict:
        conn = repo.get_connection(kind)
        if conn is None:
            return {"configured": False}
        return {"configured": True, "url": conn["url"], "options": conn["options"]}

    # ── persons ────────────────────────────────────────────────────────────
    @api.get("/persons")
    def persons() -> list[Person]:
        return repo.persons()

    @api.post("/persons")
    def save_person(p: Person) -> Person:
        return repo.save_person(p)

    # ── bindings ───────────────────────────────────────────────────────────
    @api.get("/bindings")
    def bindings() -> list[Binding]:
        return repo.bindings()

    @api.post("/bindings")
    def save_binding(b: Binding) -> Binding:
        return repo.save_binding(b)

    @api.delete("/bindings/{binding_id}")
    def delete_binding(binding_id: int) -> dict:
        repo.delete_binding(binding_id)
        return {"ok": True}

    @api.get("/bindings/suggest")
    async def suggest() -> list[Binding]:
        events = deps.get("events")
        if events is None:
            raise HTTPException(409, "Connect Home Assistant first")
        inventory = await events.discover_entities()
        return heuristic_bindings([e for e in inventory if not e["disabled"]])

    # ── predictions (dashboard history) ────────────────────────────────────
    @api.get("/predictions")
    def predictions(person: str | None = None, hours: int = 24) -> dict:
        tsdb = deps.get("tsdb")
        if tsdb is None:
            return {"persons": {}, "note": "InfluxDB not connected"}
        from datetime import datetime, timedelta, timezone
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=min(hours, 24 * 30))
        targets = [person] if person else [p.id for p in repo.persons() if p.enabled]
        return {"persons": {pid: tsdb.read_predictions(pid, start, end)
                            for pid in targets}}

    # ── history import (wizard "import history" action) ───────────────────
    @api.post("/import/history")
    def import_history_ep(body: dict) -> dict:
        tsdb = deps.get("tsdb")
        if tsdb is None:
            raise HTTPException(409, "Connect InfluxDB first")
        from datetime import datetime, timedelta, timezone

        from ..adapters.influx_import import import_history
        days = int(body.get("days", 60))
        end = datetime.now(timezone.utc)
        results = import_history(tsdb, body["source_bucket"], repo.bindings(),
                                 end - timedelta(days=days), end)
        return {"imported": results}

    # ── activities & rules (taxonomy) ──────────────────────────────────────
    @api.get("/activities")
    def activities() -> list[Activity]:
        return repo.activities()

    @api.post("/activities")
    def save_activity(a: Activity) -> Activity:
        return repo.save_activity(a)

    @api.get("/rules")
    def rules() -> list[Rule]:
        return repo.rules()

    @api.post("/rules")
    def save_rule(r: Rule) -> Rule:
        return repo.save_rule(r)

    # ── models (registry + actions) ────────────────────────────────────────
    @api.get("/models")
    def models(person: str | None = None) -> list:
        return repo.models(person)

    @api.post("/models/train")
    def train_now(body: dict) -> dict:
        tsdb = deps.get("tsdb")
        if tsdb is None:
            raise HTTPException(409, "Connect InfluxDB first")
        from ..domain.training.trainer import train_person
        record = train_person(body["person_id"], tsdb, repo, deps["models"],
                              weeks=int(body.get("weeks", 8)),
                              force=bool(body.get("force", False)))
        if record is None:
            return {"trained": False, "reason": "not enough data or one class only"}
        return {"trained": True, "version": record.version,
                "promoted": record.promoted, "metrics": record.metrics}

    @api.post("/models/{model_id}/promote")
    def promote(model_id: int) -> dict:
        repo.promote_model(model_id)
        return {"ok": True}

    @api.post("/models/rollback")
    def rollback_ep(body: dict) -> dict:
        from ..domain.training.trainer import rollback
        record = rollback(body["person_id"], repo)
        if record is None:
            raise HTTPException(409, "nothing to roll back to")
        return {"ok": True, "version": record.version}

    # ── labels: bulk range + question skip ────────────────────────────────
    @api.post("/labels/bulk")
    def labels_bulk(body: dict) -> dict:
        """body: {person_id, start, end (ISO), activity}"""
        tsdb = deps.get("tsdb")
        if tsdb is None:
            raise HTTPException(409, "Connect InfluxDB first")
        from datetime import datetime
        from ..domain.labeling.bulk import bulk_label_events
        events = bulk_label_events(
            body["person_id"],
            datetime.fromisoformat(body["start"]),
            datetime.fromisoformat(body["end"]),
            body["activity"],
            source=body.get("source", "bulk"))
        for ev in events:
            tsdb.write_label(ev)
        return {"labeled_windows": len(events)}

    @api.post("/inbox/{question_id}/skip")
    def skip(question_id: int) -> dict:
        repo.skip_question(question_id)
        return {"ok": True}

    # ── feedback: notification action taps, forwarded by the HA integration ─
    @api.post("/feedback/action")
    def feedback_action(body: dict) -> dict:
        """body: {"action": "HEARTH_<qid>_<idx>", "device": "..."} — sent by
        the Hearth integration's event-bus listener. No automations involved."""
        action = str(body.get("action", ""))
        parts = action.split("_")
        if len(parts) != 3 or parts[0] != "HEARTH":
            raise HTTPException(400, "not a hearth action")
        try:
            qid, idx = int(parts[1]), int(parts[2])
        except ValueError:
            raise HTTPException(400, "malformed action id")
        q = repo.get_question(qid)
        if q is None:
            raise HTTPException(404, "unknown question")
        if q.status != "open":
            return {"ok": True, "note": "already answered"}
        if idx >= len(q.alternatives):
            raise HTTPException(400, "option index out of range")
        answer_slug = q.alternatives[idx]
        repo.answer_question(qid, answer_slug)
        tsdb = deps.get("tsdb")
        if tsdb is not None:
            from ..domain.schemas import LabelEvent, Provenance
            tsdb.write_label(LabelEvent(person_id=q.person_id, window_ts=q.window_ts,
                                        label=answer_slug, provenance=Provenance.CONFIRMED,
                                        source="notification"))
        return {"ok": True, "answer": answer_slug}

    # ── journey (cold-start dashboard) ─────────────────────────────────────
    @api.get("/journey")
    def journey() -> dict:
        from datetime import datetime, timezone
        tsdb = deps.get("tsdb")
        first = tsdb.first_raw_time() if tsdb else None
        days = ((datetime.now(timezone.utc) - first).total_seconds() / 86400) if first else 0.0
        return {
            "recording_since": first.isoformat() if first else None,
            "days": round(days, 1),
            "events_24h": tsdb.count_raw_events(24) if tsdb else 0,
            "sensors_bound": len([b for b in repo.bindings() if b.enabled]),
            "milestones": {
                "recording": bool(repo.get_setting("milestone.recording_started")),
                "patterns": bool(repo.get_setting("milestone.patterns_found")),
                "model": bool(repo.get_setting("milestone.model_live")),
            },
        }

    # ── inbox (dashboard "needs you" preview + Inbox page) ────────────────
    @api.get("/inbox")
    def inbox(person: str | None = None) -> list:
        return repo.open_questions(person)

    @api.post("/inbox/{question_id}/answer")
    def answer(question_id: int, body: dict) -> dict:
        q = repo.answer_question(question_id, body["answer"])
        tsdb = deps.get("tsdb")
        if tsdb is not None:
            from ..domain.schemas import LabelEvent, Provenance
            tsdb.write_label(LabelEvent(person_id=q.person_id, window_ts=q.window_ts,
                                        label=body["answer"], provenance=Provenance.CONFIRMED,
                                        source="inbox"))
        return {"ok": True}

    # ── system ─────────────────────────────────────────────────────────────
    @api.get("/system/status")
    def status() -> dict:
        return {"bindings": len(repo.bindings()),
                "persons": len(repo.persons()),
                "tsdb": deps.get("tsdb") is not None,
                "ha": deps.get("events") is not None}

    return api
