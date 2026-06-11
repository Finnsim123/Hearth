"""API aggregation — thin routes calling exactly one domain/adapter function.
Auth middleware arrives in Phase 2; LAN-only until then (docs/SECURITY.md)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response

from ..domain.onboarding.advisor import heuristic_bindings
from ..domain.schemas import Activity, Binding, Person, Rule
from ..domain.labeling.active import _is_sleep_like


def build_api_router(deps: dict) -> APIRouter:
    api = APIRouter()
    repo = deps["repo"]

    # ── auth ────────────────────────────────────────────────────────────────
    @api.post("/auth/login")
    def login(body: dict, response: Response) -> dict:
        user = repo.verify_login(body.get("email", ""), body.get("password", ""))
        if user is None:
            raise HTTPException(401, "Wrong email or password")
        from .. import security
        cookie, sha = security.mint_session()
        repo.create_session(user.id, sha)
        response.set_cookie("hearth_session", cookie, httponly=True,
                            samesite="lax", max_age=30 * 86400)
        return {"ok": True, "user": {"email": user.email, "name": user.display_name,
                                     "role": user.role}}

    @api.post("/auth/logout")
    def logout(request: Request, response: Response) -> dict:
        from .. import security
        cookie = request.cookies.get("hearth_session")
        if cookie:
            import hashlib
            repo.delete_session(hashlib.sha256(cookie.encode()).hexdigest())
        response.delete_cookie("hearth_session")
        return {"ok": True}

    @api.get("/auth/me")
    def me(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(401, "Not signed in")
        return {"email": user.email, "name": user.display_name, "role": user.role}

    @api.post("/auth/password")
    def change_password(body: dict, request: Request, response: Response) -> dict:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(401, "Not signed in")
        new_pw = body.get("new") or ""
        if len(new_pw) < 10:
            raise HTTPException(400, "New password must be at least 10 characters")
        if not repo.change_password(user.id, body.get("current") or "", new_pw):
            raise HTTPException(403, "Current password is wrong")
        # every session was revoked — keep THIS browser signed in
        from .. import security
        cookie, sha = security.mint_session()
        repo.create_session(user.id, sha)
        response.set_cookie("hearth_session", cookie, httponly=True,
                            samesite="lax", max_age=30 * 86400)
        return {"ok": True}

    @api.get("/health")
    def health() -> dict:
        import os
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(),
                "tsdb": deps.get("tsdb") is not None,
                "ha": deps.get("events") is not None,
                "build": os.getenv("HEARTH_BUILD_SHA", "dev"),
                "needs_setup": repo.user_count() == 0}

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
        from .. import security
        return {"configured": True, "url": conn["url"], "options": conn["options"],
                "token_masked": security.mask(conn["token"]) if conn["token"] else None}

    # ── setup completion: persist EVERYTHING the wizard collected ──────────
    TAXONOMY_PRESETS = {
        "minimal": [("sleeping", "Sleeping", "sleeping"), ("away", "Away", "out of the house"),
                     ("home", "Home", "just at home")],
        "standard": [("sleeping", "Sleeping", "sleeping"), ("away", "Away", "out of the house"),
                      ("home", "Home", "just at home"), ("cooking", "Cooking", "cooking"),
                      ("eating", "Eating", "eating"), ("movie", "Movie", "watching something"),
                      ("working", "Working", "working")],
        "custom": [],
    }

    @api.post("/setup/complete")
    async def setup_complete(body: dict, response: Response) -> dict:
        """One-shot: create admin, save connections/household/taxonomy/bindings,
        mark fast-track, then restart to apply (docker restarts the container)."""
        import asyncio
        import os
        import re as _re

        from ..domain.onboarding.advisor import heuristic_bindings
        from ..domain.schemas import Activity, Binding, Person, Role, User

        if repo.user_count() > 0:
            raise HTTPException(409, "Setup already completed")

        acct = body["account"]
        if len(acct.get("password") or "") < 10:
            raise HTTPException(400, "Password missing or too short — go back "
                                      "to step 1 and re-enter it (resuming the "
                                      "wizard never restores passwords).")
        repo.create_user(User(email=acct["email"], display_name=acct["name"],
                              role="admin"), acct["password"])

        ha = body["ha"]
        repo.set_connection("ha", ha["url"].rstrip("/"), ha["token"])

        # home timezone from HA config (probe), fallback UTC
        from ..adapters.ha_probe import probe
        info = await probe(ha["url"], ha["token"])
        repo.set_setting("timezone", info.get("timezone") or "UTC")
        if body.get("appBaseUrl"):
            repo.set_setting("hearth_base_url", str(body["appBaseUrl"]).rstrip("/"))

        influx = body["influx"]
        if influx.get("mode") == "external":
            repo.set_connection("influx", influx["url"].rstrip("/"), influx["token"],
                                {"org": influx["org"] or "homelab", "mode": "external",
                                 "source_bucket": influx.get("sourceBucket") or None})
        else:
            from ..config import settings as cfg
            repo.set_connection("influx", cfg.influx_url, cfg.influx_token,
                                {"org": cfg.influx_org, "mode": "bundled"})

        if body.get("llmKey"):
            repo.set_connection("llm", "https://openrouter.ai/api/v1", body["llmKey"],
                                {"model": body.get("llmModel") or "openai/gpt-4o-mini"})

        def _slug(name: str) -> str:
            return _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "member"

        for m in body.get("members", []):
            repo.save_person(Person(id=_slug(m["name"]), name=m["name"],
                                    avatar=m.get("avatar"),
                                    ha_person_entity=m.get("personEntity") or None,
                                    notify_service=m.get("notifyService") or None,
                                    has_device=bool(m.get("hasDevice", True)),
                                    notify_system=bool(m.get("notifySystem", False)),
                                    ask_budget_per_day=int(m.get("askBudget", 8))))

        for slug, name, phrase in TAXONOMY_PRESETS.get(body.get("taxonomyPreset", "standard"), []):
            repo.save_activity(Activity(slug=slug, name=name, phrase=phrase,
                                        silent=_is_sleep_like(slug)))
        from ..domain.labeling.taxonomy import ensure_hierarchy
        ensure_hierarchy(repo)   # cooking/eating/movie/working → children of home
        repo.set_setting("default_activity", "home")

        # SLOW work (inventory, LLM mapping, rules) is deferred to the next
        # boot (domain/onboarding/seed.py) so this request answers in
        # milliseconds and the auto-login cookie reliably reaches the browser.
        repo.set_setting("seed.pending", {"members": body.get("members", [])})

        if influx.get("mode") == "external" and influx.get("sourceBucket"):
            repo.set_setting("fasttrack.pending",
                             {"source_bucket": influx["sourceBucket"]})

        # sign the new admin in right away (session survives the restart)
        from .. import security
        user = repo.verify_login(acct["email"], acct["password"])
        if user is not None:
            cookie, sha = security.mint_session()
            repo.create_session(user.id, sha)
            response.set_cookie("hearth_session", cookie, httponly=True,
                                samesite="lax", max_age=30 * 86400)

        # restart to (re)build adapters with the saved connections
        if os.getenv("HEARTH_NO_RESTART") != "1":
            asyncio.get_event_loop().call_later(1.0, os._exit, 0)
        return {"ok": True, "restarting": True,
                "fasttrack": bool(influx.get("mode") == "external" and influx.get("sourceBucket"))}

    @api.post("/ha/test")
    async def ha_test(body: dict) -> dict:
        """Staged wizard check; body {url, token}. Read-only, saves nothing."""
        from ..adapters.ha_probe import probe
        return await probe(body.get("url", ""), body.get("token", ""))

    @api.post("/ha/inventory")
    async def ha_inventory(body: dict) -> dict:
        """Pre-save inventory scan: full metadata + heuristic suggestions count."""
        from ..adapters.ha_probe import rest_inventory
        inventory = await rest_inventory(body.get("url", ""), body.get("token", ""))
        suggested = heuristic_bindings(inventory)
        return {"count": len(inventory),
                "bindable": len(suggested),
                "domains": len({e["domain"] for e in inventory}),
                "inventory": inventory}

    @api.post("/influx/inspect")
    def influx_inspect(body: dict) -> dict:
        """Staged wizard check; body {url, org, token} OR {mode: "bundled"}
        (server fills in its own env credentials). Read-only, saves nothing."""
        from ..adapters.influx_store import inspect_influx
        if body.get("mode") == "bundled":
            from ..config import settings as cfg
            return inspect_influx(cfg.influx_url, cfg.influx_org, cfg.influx_token)
        return inspect_influx(body.get("url", ""), body.get("org", ""),
                              body.get("token", ""))

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

    @api.post("/bindings/cleanup")
    def bindings_cleanup() -> dict:
        """Prune seeded bindings the improved heuristics would no longer
        suggest: device_tracker noise + diagnostics blocklist. Person bindings
        for household members (person.*) are always kept."""
        from ..domain.features.person_scope import binding_owner
        from ..domain.onboarding.advisor import is_bindable
        removed, owned = [], 0
        persons_now = repo.persons()
        for b in repo.bindings():
            if b.entity_id.split(".")[0] == "person":
                continue
            if not is_bindable(b.entity_id, b.role):
                repo.delete_binding(b.id)
                removed.append(b.entity_id)
                continue
            if not b.person_id:
                owner = binding_owner(b, persons_now)
                if owner:
                    b.person_id = owner
                    repo.save_binding(b)
                    owned += 1
        return {"removed": len(removed), "entities": removed,
                "owners_assigned": owned}

    @api.get("/bindings/health")
    def bindings_health() -> dict:
        """Per-binding signal health over the last 7 days, plus the training
        class balance. Answers 'is this sensor actually a feature?' — a binding
        whose feature columns never vary is dead weight the model ignores, and
        a class with 0 windows can never be predicted (no examples to learn)."""
        tsdb = deps.get("tsdb")
        if tsdb is None:
            return {"bindings": [], "classes": {}, "note": "InfluxDB not connected"}
        from datetime import datetime, timedelta, timezone
        from ..domain.features.registry import feature_set_version
        fset = feature_set_version(repo.get_setting("composites", []) or [],
                                   repo.get_setting("time_granularity", "coarse") or "coarse")
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        persons = [p for p in repo.persons() if p.enabled]
        # union of each person's recent feature matrix (binding cols are shared
        # + that person's personal sensors)
        import pandas as pd
        frames = [tsdb.read_features(p.id, fset, start, end) for p in persons]
        feats = pd.concat([f for f in frames if not f.empty], axis=0) \
            if any(not f.empty for f in frames) else pd.DataFrame()
        SPARK_N = 60   # downsample to ~60 points across the window
        BINARY_SUFFIXES = ("frac", "occupied", "on", "any", "playing", "active",
                           "home_frac", "home_last", "on_last", "on_frac",
                           "imminent", "opened_any")

        def _spark(col):
            ser = feats[col].dropna()
            if ser.empty:
                return []
            # bucket the window into SPARK_N slots, mean per slot, normalized 0..1
            ser = ser.sort_index()
            n = len(ser)
            step = max(1, n // SPARK_N)
            vals = [float(ser.iloc[i:i + step].mean()) for i in range(0, n, step)]
            lo, hi = min(vals), max(vals)
            rng = hi - lo
            return [round((v - lo) / rng, 3) if rng > 1e-9 else 0.0 for v in vals]

        def _pick_col(cols, name):
            # prefer a 'binary-ish' suffix so presence/bed read as a barcode
            for suf in BINARY_SUFFIXES:
                c = f"{name}_{suf}"
                if c in cols:
                    return c, "binary"
            return (cols[0] if cols else None), "numeric"

        out = []
        for b in repo.bindings():
            cols = [c for c in feats.columns
                    if c == b.name or c.startswith(b.name + "_")] if not feats.empty else []
            varies = any(feats[c].nunique(dropna=True) > 1 for c in cols)
            present = bool(cols) and any(feats[c].notna().any() for c in cols)
            col, kind = _pick_col(cols, b.name)
            spark = _spark(col) if col and varies else []
            out.append({"id": b.id, "name": b.name, "role": b.role.value,
                        "entity_id": b.entity_id, "enabled": b.enabled,
                        "status": ("alive" if varies else
                                   "constant" if present else "no_data"),
                        "spark": spark, "kind": kind})
        # class balance from confirmed + bootstrap labels (recent window)
        classes: dict[str, int] = {}
        for p in persons:
            for ev in tsdb.read_labels(p.id, start, end):
                classes[ev.label] = classes.get(ev.label, 0) + 1
        return {"bindings": out, "classes": classes,
                "windows": int(len(feats)) if not feats.empty else 0}

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
        # `person` is interpolated into a Flux query downstream and is reachable
        # by an integration-scope bearer token — only ever query KNOWN person
        # ids (defence in depth alongside read_predictions' own escaping).
        known = {p.id for p in repo.persons() if p.enabled}
        if person is not None and person not in known:
            raise HTTPException(404, "no such person")
        targets = [person] if person else sorted(known)
        return {"persons": {pid: tsdb.read_predictions(pid, start, end)
                            for pid in targets}}

    @api.post("/fasttrack/rerun")
    def fasttrack_rerun() -> dict:
        """Re-run the import->features->train pipeline (e.g. after recipe or
        binding changes). Takes effect on next container start."""
        influx = repo.get_connection("influx") or {}
        source = (influx.get("options") or {}).get("source_bucket")
        if not source:
            raise HTTPException(409, "No source bucket configured")
        repo.set_setting("fasttrack.pending", {"source_bucket": source})
        return {"ok": True, "note": "restart the container to start the rerun: "
                                    "docker compose restart hearth"}

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

    @api.post("/rules/regenerate")
    def regenerate_rules(body: dict | None = None) -> dict:
        """Regenerate starter rules from current bindings. Replaces previous
        auto-generated rules; user-edited ones (changed predicate/priority)
        are simply replaced too in v1 — the Activities page is the editor."""
        from ..domain.labeling.starter_rules import starter_rules
        existing = repo.rules()
        for r in existing:
            if r.origin == "user" and r.id is not None:
                # v1: regenerate replaces all; refine when Activities UI lands
                pass
        rules = starter_rules(repo.bindings(), repo.activities())
        saved = [repo.save_rule(r) for r in rules]
        return {"generated": len(saved),
                "rules": [{"activity": r.activity_slug, "person": r.person_id,
                           "predicate": r.predicate} for r in saved]}

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

    # ── pattern discovery: cluster → name → labels ─────────────────────────
    @api.get("/clusters")
    def clusters(status: str | None = None, person: str | None = None) -> list:
        return repo.clusters(status=status, person_id=person)

    @api.post("/discovery/run")
    async def discovery_run(body: dict | None = None) -> dict:
        tsdb = deps.get("tsdb")
        if tsdb is None:
            raise HTTPException(409, "Connect InfluxDB first")
        import asyncio

        from ..domain.discovery.clustering import run_discovery
        days = int((body or {}).get("days", 30))
        cards = await asyncio.to_thread(run_discovery, tsdb, repo, days)
        # optional: ask the LLM which existing activity each pattern looks like
        adv = None
        if repo.get_connection("llm"):
            from ..adapters.openrouter_llm import OpenRouterAdvisor
            adv = OpenRouterAdvisor(repo)
        if adv is not None:
            acts = repo.activities()
            for c in cards:
                try:
                    c.suggested_slug = await adv.suggest_cluster_name(c, acts)
                    if c.suggested_slug:
                        repo.save_cluster(c)
                except Exception:
                    pass
        return {"found": len(cards),
                "persons": sorted({c.person_id for c in cards})}

    @api.post("/clusters/{cluster_id}/name")
    def name_cluster(cluster_id: int, body: dict) -> dict:
        """Name a pattern: pick an existing activity slug OR a new name.
        Emits provenance=discovered labels for every member window — the
        next training run learns from them (confirmed labels still outrank)."""
        tsdb = deps.get("tsdb")
        card = repo.get_cluster(cluster_id)
        if card is None:
            raise HTTPException(404, "no such pattern")
        if tsdb is None:
            raise HTTPException(409, "Connect InfluxDB first")
        slug = (body.get("activity_slug") or "").strip()
        if not slug and body.get("name"):
            import re as _re
            name = str(body["name"]).strip()
            slug = _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            if not slug:
                raise HTTPException(400, "name needed")
            if slug not in {a.slug for a in repo.activities()}:
                repo.save_activity(Activity(slug=slug, name=name))
        if not slug:
            raise HTTPException(400, "activity_slug or name required")

        from ..domain.schemas import LabelEvent, Provenance
        for ts in card.example_windows:
            tsdb.write_label(LabelEvent(
                person_id=card.person_id, window_ts=ts, label=slug,
                provenance=Provenance.DISCOVERED, source=f"cluster:{card.id}"))
        card.status, card.named_activity_slug = "named", slug
        repo.save_cluster(card)
        # draft a rule from the signature (disabled until you switch it on in
        # Activities — auto-enabling a crude threshold rule could poison labels)
        from ..domain.labeling.rules import draft_rule_from_signature
        rule = draft_rule_from_signature(card.signature, slug)
        rule.person_id = card.person_id or None
        rule = repo.save_rule(rule)
        return {"ok": True, "activity": slug,
                "labeled_windows": len(card.example_windows),
                "drafted_rule_id": rule.id}

    @api.post("/clusters/{cluster_id}/merge")
    def merge_cluster(cluster_id: int, body: dict) -> dict:
        """This pattern is the same as that one — fold it in."""
        from ..domain.discovery.clustering import merge_clusters
        source = repo.get_cluster(cluster_id)
        target = repo.get_cluster(int(body.get("into", 0)))
        if source is None or target is None:
            raise HTTPException(404, "no such pattern")
        if source.id == target.id:
            raise HTTPException(400, "cannot merge a pattern into itself")
        source, target = merge_clusters(source, target)
        repo.save_cluster(source)
        repo.save_cluster(target)
        return {"ok": True, "into": target.id, "n_windows": target.n_windows}

    @api.post("/clusters/{cluster_id}/dismiss")
    def dismiss_cluster(cluster_id: int) -> dict:
        card = repo.get_cluster(cluster_id)
        if card is None:
            raise HTTPException(404, "no such pattern")
        card.status = "dismissed"
        repo.save_cluster(card)
        return {"ok": True}

    # ── api tokens (for the HA integration) ────────────────────────────────
    @api.post("/tokens")
    def create_token(body: dict | None = None) -> dict:
        name = (body or {}).get("name") or "Home Assistant"
        return {"token": repo.create_api_token(name), "note": "shown once"}

    @api.get("/tokens")
    def list_tokens() -> list[dict]:
        return repo.api_tokens()

    @api.delete("/tokens/{token_id}")
    def revoke_token(token_id: int) -> dict:
        repo.revoke_api_token(token_id)
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
            "seed": repo.get_setting("seed.status"),
            "fasttrack": repo.get_setting("fasttrack.status"),
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

    # ── in-app updates (host updater writes status; we write the trigger) ──
    @api.get("/system/update")
    def update_status() -> dict:
        import json as _json
        import os
        from pathlib import Path
        shared = Path(os.getenv("HEARTH_SHARED_DIR", "/shared"))
        out = {"build": os.getenv("HEARTH_BUILD_SHA", "dev"),
               "updater": False, "behind": 0}
        status_file = shared / "update_status.json"
        if status_file.is_file():
            try:
                out.update(_json.loads(status_file.read_text()))
                out["updater"] = True
            except Exception:
                pass
        out["pending"] = (shared / "update_requested").is_file()
        return out

    @api.post("/system/update")
    def request_update() -> dict:
        import os
        from pathlib import Path
        shared = Path(os.getenv("HEARTH_SHARED_DIR", "/shared"))
        if not (shared / "update_status.json").is_file():
            raise HTTPException(409, "Host updater not installed — run "
                                     "bash install.sh once on the host")
        (shared / "update_requested").touch()
        return {"ok": True, "note": "the host updater picks this up within a "
                                    "minute; Hearth rebuilds and restarts"}

    # ── system ─────────────────────────────────────────────────────────────
    # ── model settings (small allowlist; values validated) ────────────────
    _SETTING_CHOICES = {"time_granularity": {"coarse", "full", "none"}}

    @api.get("/settings/model")
    def get_model_settings() -> dict:
        return {"time_granularity": repo.get_setting("time_granularity", "coarse")}

    @api.post("/settings/model")
    def set_model_settings(body: dict) -> dict:
        for key, choices in _SETTING_CHOICES.items():
            if key in body:
                if body[key] not in choices:
                    raise HTTPException(400, f"{key} must be one of {sorted(choices)}")
                repo.set_setting(key, body[key])
        return {"ok": True, "note": "takes effect on the next training run "
                                    "(feature schema changed — retrain to apply)"}

    @api.get("/system/status")
    def status() -> dict:
        return {"bindings": len(repo.bindings()),
                "persons": len(repo.persons()),
                "tsdb": deps.get("tsdb") is not None,
                "ha": deps.get("events") is not None}

    return api
