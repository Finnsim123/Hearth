"""API aggregation — thin routes calling exactly one domain/adapter function.
Auth middleware arrives in Phase 2; LAN-only until then (docs/SECURITY.md)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response

log = logging.getLogger(__name__)

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

    @api.post("/auth/reset")
    def reset_password(body: dict) -> dict:
        """Redeem a one-time recovery token (minted by `python -m hearth.recover`)
        and set a new password. Public — the high-entropy single-use token is the
        credential. On success every existing session is revoked."""
        import hashlib
        token = (body.get("token") or "").strip()
        new_pw = body.get("new") or ""
        if len(new_pw) < 10:
            raise HTTPException(400, "New password must be at least 10 characters")
        sha = hashlib.sha256(token.encode()).hexdigest()
        if not token or not repo.reset_password_with_token(sha, new_pw):
            raise HTTPException(400, "Invalid or expired recovery token")
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
        out = {"configured": True, "url": conn["url"], "options": conn["options"],
               "token_masked": security.mask(conn["token"]) if conn["token"] else None}
        if kind == "llm":
            out["status"] = repo.get_setting("llm.status")
            out["activity"] = repo.get_setting("llm.activity")
            out["usage"] = repo.get_setting("llm.usage")
        return out

    @api.post("/llm/usage/reset")
    def reset_llm_usage() -> dict:
        repo.set_setting("llm.usage", None)
        return {"ok": True}

    @api.post("/llm/retry")
    async def retry_llm() -> dict:
        """Re-run sensor mapping after the user tops up / fixes their AI key.
        Seed mapping silently degrades to the basic rules when the LLM call
        fails (401/402/429); this re-queues it so the now-working key remaps at
        full quality. Runs in-process when the HA event adapter is live (no
        restart); otherwise it applies on the next boot, same path as seeding."""
        if repo.get_connection("llm") is None:
            raise HTTPException(409, "No AI key configured")
        # preserve any members from an in-flight seed; a bare re-map needs none.
        repo.set_setting("seed.pending", repo.get_setting("seed.pending") or {"members": []})
        events = deps.get("events")
        if events is None:
            return {"ok": True, "restart": True,
                    "note": "restart the container to remap: docker compose restart hearth"}
        import asyncio

        from ..domain.onboarding.seed import run_seed
        asyncio.create_task(run_seed(repo, events))
        return {"ok": True, "restart": False}

    # ── feature power mode (conservative vs full whitelist) ────────────────
    @api.get("/feature-power")
    def get_feature_power() -> dict:
        from ..domain.features.transforms import WHITELIST_MODES, active_mode
        return {"mode": active_mode(repo), "modes": list(WHITELIST_MODES)}

    @api.post("/feature-power")
    def set_feature_power(body: dict) -> dict:
        from ..domain.features.transforms import set_feature_power_mode
        try:
            mode = set_feature_power_mode(repo, str(body.get("mode", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "mode": mode}

    # ── model family selector (random_forest | gradient_boosting | logistic) ─
    @api.get("/model-family")
    def get_model_family() -> dict:
        from ..domain.training.estimators import KNOWN_FAMILIES
        from ..domain.training.trainer import load_training_config
        return {"family": load_training_config(repo).model_family,
                "families": list(KNOWN_FAMILIES)}

    @api.post("/model-family")
    def set_model_family_ep(body: dict) -> dict:
        from ..domain.training.trainer import set_model_family
        try:
            fam = set_model_family(repo, str(body.get("family", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "family": fam,
                "note": "applies on the next training run"}

    # ── editable LLM system prompts (Settings → AI prompts) ────────────────
    @api.get("/prompts")
    def list_prompts_ep() -> dict:
        from ..domain.prompts import list_prompts
        return {"prompts": list_prompts(repo)}

    @api.post("/prompts")
    def set_prompt_ep(body: dict) -> dict:
        """Override a system prompt, or reset it to the default (body
        {key, reset:true}). Editing changes how the AI assistant behaves on the
        next analysis; the JSON output contract lives in the text, so a reckless
        edit can break a pass — every prompt has a one-click reset."""
        from ..domain.prompts import PROMPT_DEFS, reset_override, set_override
        key = body.get("key")
        if key not in PROMPT_DEFS:
            raise HTTPException(404, "unknown prompt")
        if body.get("reset"):
            reset_override(repo, key)
            return {"ok": True, "reset": True}
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "text required (non-empty)")
        if len(text) > 20_000:
            raise HTTPException(400, "prompt too long (max 20000 chars)")
        set_override(repo, key, text)
        return {"ok": True}

    # ── training look-back window (how far back each run learns) ───────────
    @api.get("/training-window")
    def get_training_window() -> dict:
        from ..domain.training.trainer import load_training_config
        return {"weeks": load_training_config(repo).train_weeks}

    @api.post("/training-window")
    def set_training_window(body: dict) -> dict:
        from ..domain.training.trainer import set_train_weeks
        try:
            weeks = set_train_weeks(repo, body.get("weeks"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "weeks": weeks,
                "note": "applies on the next training run; 0 = all retained history"}

    # ── entity triage (coarse funnel stage): clusters + relevant shortlist ──
    @api.get("/entity-triage")
    def get_entity_triage() -> dict:
        tr = repo.get_setting("entity_triage") or {
            "by": None, "total": 0, "kept_count": 0, "kept": [], "clusters": []}
        # strip per-cluster membership (kept server-side for re-scoping) — the UI
        # only needs labels + counts; it toggles by label.
        clusters = [{k: v for k, v in c.items() if k != "entities"}
                    for c in tr.get("clusters", [])]
        return {**tr, "clusters": clusters,
                "awaiting": bool(repo.get_setting("triage.awaiting")),
                "has_llm": repo.get_connection("llm") is not None}

    @api.post("/triage/preview")
    async def triage_preview(body: dict) -> dict:
        """Run the coarse triage DURING the wizard (pre-auth), with the AI key the
        user just entered but hasn't saved yet — so 'Scanning your home' can show
        the clusters + let them pick before setup completes. Stores entity_triage
        (survives the setup restart) and returns clusters (membership stripped)."""
        inventory = body.get("inventory") or []
        llm = body.get("llm") or {}
        key = llm.get("key")
        advisor = None
        if key:
            class _AdHocRepo:                       # lend the unsaved key to the advisor
                def __init__(self, real):
                    self._real = real
                    self._llm = {"url": llm.get("url") or "https://openrouter.ai/api/v1",
                                 "token": key,
                                 "options": {"model": llm.get("model") or "openai/gpt-4o-mini"}}
                def get_connection(self, kind):
                    return self._llm if kind == "llm" else self._real.get_connection(kind)
                def get_setting(self, k, d=None): return self._real.get_setting(k, d)
                def set_setting(self, k, v): return self._real.set_setting(k, v)
                def persons(self): return self._real.persons()
            from ..adapters.openrouter_llm import OpenRouterAdvisor
            advisor = OpenRouterAdvisor(_AdHocRepo(repo))
        from ..domain.onboarding.triage import triage_entities
        res = await triage_entities(repo, inventory, advisor)
        clusters = [{k: v for k, v in c.items() if k != "entities"}
                    for c in res.get("clusters", [])]
        return {**res, "clusters": clusters, "has_llm": bool(key)}

    @api.post("/entity-triage/approve")
    async def approve_entity_triage(body: dict) -> dict:
        """Approve the keep-set (optionally with whole clusters toggled off) and
        run the expensive AI mapping pass on it — the 'don't spend without a yes'
        gate. Re-seeds with the LLM enabled, then re-warm-starts. Needs a key."""
        if repo.get_connection("llm") is None:
            raise HTTPException(409, "No AI key configured")
        tr = repo.get_setting("entity_triage")
        if not tr:
            raise HTTPException(409, "No triage to approve yet")
        from ..domain.onboarding.triage import keepset_from
        excluded = set(body.get("excluded_labels") or [])
        included = set(body.get("included_labels") or [])
        kept = keepset_from(tr, excluded, included)
        kept_set = set(kept)
        tr["kept"] = kept
        tr["kept_count"] = len(kept)
        for c in tr.get("clusters", []):
            c["kept"] = sum(1 for e in c.get("entities", []) if e in kept_set)
        repo.set_setting("entity_triage", tr)
        repo.set_setting("triage.approved", True)
        repo.set_setting("triage.awaiting", False)
        # re-run mapping (LLM now enabled) then re-warm-start on the new bindings
        repo.set_setting("seed.pending", {"members": []})
        influx = repo.get_connection("influx") or {}
        src = (influx.get("options") or {}).get("source_bucket")
        repo.set_setting("fasttrack.pending",
                         {"source_bucket": src} if src else {"source": "recorder", "days": 10})
        events = deps.get("events")
        if events is None:
            return {"ok": True, "restart": True,
                    "note": "restart the container to apply: docker compose restart hearth"}
        import asyncio

        async def _refine() -> None:
            from ..domain.fasttrack import run_fast_track
            from ..domain.onboarding.seed import run_seed
            await run_seed(repo, events)
            if deps.get("tsdb"):
                await run_fast_track(repo, deps["tsdb"], deps["models"],
                                     deps.get("notifier"), events)
        asyncio.create_task(_refine())
        return {"ok": True, "restart": False, "kept_count": len(kept)}

    # ── recent logs (Logs page) — session-only, never in integration scope ──
    @api.get("/logs")
    def get_logs(level: str = "INFO", limit: int = 500,
                 since_seq: int | None = None) -> dict:
        buf = deps.get("log_buffer")
        if buf is None:
            return {"records": [], "levels": ["DEBUG", "INFO", "WARNING", "ERROR"]}
        min_level = logging.getLevelName(level.upper())
        if not isinstance(min_level, int):
            min_level = logging.INFO
        records = buf.records(min_level=min_level, limit=max(1, min(limit, 2000)),
                              since_seq=since_seq)
        return {"records": records,
                "levels": ["DEBUG", "INFO", "WARNING", "ERROR"]}

    # ── per-person two-way controls (override + questions opt-out) ──────────
    # Read/written by BOTH output channels: the MQTT switch/select and the HA
    # integration's switch/select, so behaviour is identical for every user.
    @api.get("/controls")
    def get_controls() -> dict:
        from ..domain.controls import active_override, questions_disabled
        return {"activities": [a.slug for a in repo.activities()],
                "persons": {p.id: {"override": active_override(repo, p.id) or "auto",
                                   "questions": not questions_disabled(repo, p.id)}
                            for p in repo.persons()}}

    @api.post("/persons/{person_id}/override")
    def set_person_override(person_id: str, body: dict) -> dict:
        from ..domain.controls import set_override
        slug = set_override(repo, person_id, str(body.get("activity", "")),
                            {a.slug for a in repo.activities()})
        return {"ok": True, "override": slug or "auto"}

    @api.post("/persons/{person_id}/questions")
    def set_person_questions(person_id: str, body: dict) -> dict:
        from ..domain.controls import set_questions_optout
        on = bool(body.get("enabled", True))
        set_questions_optout(repo, person_id, not on)
        return {"ok": True, "questions": on}

    # ── output policy: abstain / "unknown" state ──────────────────────────
    @api.get("/output-policy")
    def get_output_policy() -> dict:
        from ..domain.inference.output import load_output_policy
        pol = load_output_policy(repo)
        return {"abstain_enabled": pol.abstain_enabled,
                "abstain_threshold": pol.abstain_threshold}

    @api.post("/output-policy")
    def set_output_policy(body: dict) -> dict:
        cur = repo.get_setting("output.policy") or {}
        if not isinstance(cur, dict):
            cur = {}
        if "abstain_enabled" in body:
            cur["abstain_enabled"] = bool(body["abstain_enabled"])
        if "abstain_threshold" in body:
            try:
                t = float(body["abstain_threshold"])
            except (TypeError, ValueError):
                raise HTTPException(400, "abstain_threshold must be a number 0..1")
            if not 0.0 <= t <= 1.0:
                raise HTTPException(400, "abstain_threshold must be between 0 and 1")
            cur["abstain_threshold"] = t
        repo.set_setting("output.policy", cur)
        from ..domain.inference.output import load_output_policy
        pol = load_output_policy(repo)
        return {"ok": True, "abstain_enabled": pol.abstain_enabled,
                "abstain_threshold": pol.abstain_threshold}

    # ── aggregate-stats consent (privacy lever for the AI assistant) ───────
    @api.get("/stats-consent")
    def get_stats_consent() -> dict:
        from ..domain.onboarding.inventory import stats_consent, stats_consent_decided
        return {"share_stats": stats_consent(repo), "decided": stats_consent_decided(repo)}

    @api.post("/stats-consent")
    def set_stats_consent_ep(body: dict) -> dict:
        from ..domain.onboarding.inventory import set_stats_consent
        if "share" not in body:
            raise HTTPException(400, "missing 'share' (true/false)")
        try:
            share = set_stats_consent(repo, body["share"])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "share_stats": share}

    # ── newly-discovered sensors awaiting approval (detect-then-ask) ───────
    @api.get("/sensors/pending")
    def list_pending_sensors() -> dict:
        return {"pending": repo.get_setting("discovery.pending") or []}

    @api.post("/sensors/pending/approve")
    async def approve_pending(body: dict) -> dict:
        import asyncio
        from ..domain.onboarding.integrate import integrate
        from ..domain.onboarding.inventory_sync import approve_pending_sensors
        pending = repo.get_setting("discovery.pending") or []
        req_ids = body.get("entity_ids")
        approved_ids = (list(req_ids) if req_ids
                        else [p["entity_id"] for p in pending if isinstance(p, dict)])
        added = approve_pending_sensors(repo, req_ids)
        do_bg = bool(added and body.get("retrain", True))
        if do_bg:
            advisor = None
            try:
                if repo.get_connection("llm"):
                    from ..adapters.openrouter_llm import OpenRouterAdvisor
                    advisor = OpenRouterAdvisor(repo)
            except Exception:
                advisor = None
            # fire-and-forget: scoped re-analysis + retrain; the buddy narrates
            asyncio.create_task(integrate(
                repo, approved_ids=approved_ids, advisor=advisor,
                events=deps.get("events"), tsdb=deps.get("tsdb"),
                store=deps.get("models")))
        return {"ok": True, "added": added, "integrating": do_bg,
                "pending": len(repo.get_setting("discovery.pending") or [])}

    @api.post("/sensors/pending/dismiss")
    def dismiss_pending(body: dict) -> dict:
        from ..domain.onboarding.inventory_sync import dismiss_pending_sensors
        remaining = dismiss_pending_sensors(repo, body.get("entity_ids"))
        return {"ok": True, "pending": remaining}

    # ── the active feature spec (AI's design work, for transparency) ───────
    @api.get("/feature-spec")
    def get_feature_spec() -> dict:
        from ..domain.features.spec_builder import load_active_spec
        spec = load_active_spec(repo)
        if spec is None:
            raw = repo.get_setting("feature_spec")
            return {"active": False,
                    "created_by": raw.get("created_by") if isinstance(raw, dict) else None}
        d = spec.model_dump(mode="json")
        return {"active": True, "spec_version": d["spec_version"],
                "created_by": d["created_by"], "llm_model": d.get("llm_model"),
                "selections": d["selections"], "features": d["features"]}

    # ── pre-run cost estimate for an AI feature-spec analysis ──────────────
    @api.post("/feature-spec/estimate")
    def feature_spec_estimate(body: dict) -> dict:
        from ..domain.features.transforms import active_mode
        from ..domain.onboarding.feature_architect import estimate_spec_cost
        n = body.get("entity_count")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise HTTPException(400, "entity_count (non-negative int) required")
        mode = body.get("mode") or active_mode(repo)
        model = body.get("model")
        if not model:
            conn = repo.get_connection("llm") or {}
            model = (conn.get("options") or {}).get("model")
        return estimate_spec_cost(n, mode=mode, model=model, repo=repo)

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

        for required in ("account", "ha", "influx"):
            if not isinstance(body.get(required), dict):
                raise HTTPException(400, f"missing '{required}'")
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

        # AI data-sharing consent (metadata-only vs +aggregate stats) is asked on
        # the AI-assist wizard step now, because it shapes the feature-spec pass.
        if "shareStats" in body:
            from ..domain.onboarding.inventory import set_stats_consent
            try:
                set_stats_consent(repo, bool(body["shareStats"]))
            except ValueError:
                pass

        # The wizard already ran the coarse triage ('Scanning your home') and the
        # user picked which groups to keep. entity_triage is already stored (it
        # survives this restart); apply the selection and pre-approve so seeding
        # skips re-triaging and goes straight to mapping — no second ask.
        tr = repo.get_setting("entity_triage")
        if tr:
            from ..domain.onboarding.triage import keepset_from
            sel = body.get("triage") or {}
            kept = keepset_from(tr, set(sel.get("excluded_labels") or []),
                                set(sel.get("included_labels") or []))
            kset = set(kept)
            tr["kept"] = kept
            tr["kept_count"] = len(kept)
            for c in tr.get("clusters", []):
                c["kept"] = sum(1 for e in c.get("entities", []) if e in kset)
            repo.set_setting("entity_triage", tr)
            repo.set_setting("triage.approved", True)
            repo.set_setting("triage.awaiting", False)

        if body.get("modelFamily"):
            from ..domain.training.trainer import set_model_family
            try:
                set_model_family(repo, str(body["modelFamily"]))
            except ValueError:
                pass  # unknown family from a stale client → keep the default

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

        # treat the install build as already seen, so the buddy's "what's new"
        # only ever fires for a LATER update, never on first boot.
        from ..domain.whatsnew import mark_seen
        mark_seen(repo)

        # Warm start: an external bucket (HA→Influx) gives the longest history;
        # otherwise pull ~10 days from HA's own recorder via the history API, so
        # EVERY home gets a provisional model on day one — no integration needed.
        if influx.get("mode") == "external" and influx.get("sourceBucket"):
            repo.set_setting("fasttrack.pending",
                             {"source_bucket": influx["sourceBucket"]})
        else:
            repo.set_setting("fasttrack.pending", {"source": "recorder", "days": 10})

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
            asyncio.get_running_loop().call_later(1.0, os._exit, 0)
        # warm start always runs now (external bucket or HA recorder)
        return {"ok": True, "restarting": True, "fasttrack": True}

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

    @api.post("/persons/{person_id}/avatar")
    def upload_avatar(person_id: str, body: dict) -> dict:
        """Accept a data-URL image (base64), store it under the uploads dir, and
        point the person's avatar at it. JSON, so no multipart dependency."""
        import base64
        import re
        import time
        person = next((p for p in repo.persons() if p.id == person_id), None)
        if person is None:
            raise HTTPException(404, "unknown person")
        m = re.match(r"data:image/(png|jpeg|jpg|webp|gif);base64,(.+)$",
                     (body.get("image") or "").strip(), re.S)
        if not m:
            raise HTTPException(400, "expected a PNG/JPEG/WebP/GIF data URL")
        ext = "jpg" if m.group(1) == "jpeg" else m.group(1)
        try:
            raw = base64.b64decode(m.group(2), validate=True)
        except Exception:
            raise HTTPException(400, "invalid base64 image")
        if len(raw) > 4 * 1024 * 1024:
            raise HTTPException(413, "image too large (max 4 MB)")
        uploads = deps["uploads_dir"]
        safe = re.sub(r"[^a-z0-9_-]", "", person_id.lower()) or "person"
        for old in uploads.glob(f"{safe}.*"):
            old.unlink(missing_ok=True)
        (uploads / f"{safe}.{ext}").write_bytes(raw)
        person.avatar = f"upload:/uploads/{safe}.{ext}?v={int(time.time())}"
        repo.save_person(person)
        return {"avatar": person.avatar}

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

    @api.post("/bindings/prune-empty")
    def prune_empty() -> dict:
        """Disable bindings that have NO observations in the last 7 days — empty
        feature columns that only add noise. Person bindings are kept. Reversible
        on this page once the sensor produces data."""
        from ..domain.schemas import Role
        tsdb = deps.get("tsdb")
        if tsdb is None:
            raise HTTPException(409, "Connect InfluxDB first")
        names = [b.name for b in repo.bindings() if b.enabled]
        counts = tsdb.raw_event_counts(names, days=7)
        pruned = []
        for b in repo.bindings():
            if (b.enabled and b.role != Role.PERSON
                    and counts.get(b.name, 0) == 0):
                b.enabled = False
                repo.save_binding(b)
                pruned.append(b.name)
        return {"disabled": len(pruned), "names": pruned}

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

    @api.post("/ha/sync")
    async def ha_sync() -> dict:
        """Rescan Home Assistant: add new sensors, update rooms from areas.
        Uses the LLM for new entities when a key is configured."""
        events = deps.get("events")
        if events is None:
            raise HTTPException(409, "Connect Home Assistant first")
        from ..domain.onboarding.inventory_sync import sync_inventory
        return await sync_inventory(repo, events,
                                    use_llm=repo.get_connection("llm") is not None)

    @api.get("/ha/entities")
    async def ha_entities() -> dict:
        """The FULL Home Assistant entity list (for the 'Add sensor' search). Each
        item is flagged with whether it's already bound (and to what role/member),
        whether it's a genuine home/away tracker, and the heuristic suggested role.
        Returns counts so the UI can show 'discovered / bound / available'."""
        events = deps.get("events")
        if events is None:
            raise HTTPException(409, "Connect Home Assistant first")
        from ..domain.onboarding.advisor import is_person_tracker, suggest_role
        inv = await events.discover_entities()
        by_id = {b.entity_id: b for b in repo.bindings()}
        persons = {p.id: p.name for p in repo.persons()}
        out, n_disabled = [], 0
        for e in inv:
            if e.get("disabled"):
                n_disabled += 1
                continue
            eid = e["entity_id"]
            role = suggest_role(e)
            b = by_id.get(eid)
            out.append({"entity_id": eid, "domain": e.get("domain"),
                        "friendly_name": e.get("friendly_name"), "area": e.get("area"),
                        "state": e.get("state"),
                        "suggested_role": role.value if role else None,
                        "is_tracker": is_person_tracker(eid, e.get("friendly_name") or ""),
                        "bound": b is not None,
                        "bound_role": b.role.value if b else None,
                        "bound_person": persons.get(b.person_id) if b and b.person_id else None})
        return {"entities": out, "total": len(inv), "disabled": n_disabled,
                "bound": len(by_id), "available": sum(1 for e in out if not e["bound"])}

    @api.post("/household/relink")
    async def relink_persons() -> dict:
        """Re-link every member to their home/away entity — LLM match (messy
        names → the right member) with a name fallback. Fixes a member whose
        person.* was missed at setup, without re-running the wizard."""
        events = deps.get("events")
        if events is None:
            raise HTTPException(409, "Connect Home Assistant first")
        from ..domain.onboarding.advisor import is_person_tracker
        from ..domain.onboarding.person_link import (ensure_member_persons,
                                                      repair_person_bindings)
        # first heal any numeric distance/proximity entity wrongly given the
        # PERSON role (and its inverted away rule), then (re)link real trackers.
        repaired = repair_person_bindings(repo)
        inv = [e for e in await events.discover_entities() if not e.get("disabled")]
        candidates = sum(1 for e in inv
                         if is_person_tracker(e["entity_id"], e.get("friendly_name") or ""))
        matches = {}
        if repo.get_connection("llm"):
            try:
                from ..adapters.openrouter_llm import OpenRouterAdvisor
                matches = await OpenRouterAdvisor(repo).match_person_entities(repo.persons(), inv)
            except Exception:
                log.exception("relink: LLM match failed — name fallback only")
        linked = ensure_member_persons(repo, inv, matches)
        # who's still without a live link, so the UI can guide a manual pick
        unlinked = [p.name for p in repo.persons()
                    if not any(b.role.value == "person" and b.person_id == p.id
                               for b in repo.bindings())]
        return {"linked": linked, "candidates": candidates, "unlinked": unlinked, **repaired}

    @api.get("/buddy")
    def buddy() -> dict:
        """Current phase for the ember buddy (every page) + first-run timeline.
        Pure read; degrades to a neutral 'waiting' state on any error."""
        from ..domain.buddy import buddy_state
        try:
            return buddy_state(repo, deps.get("tsdb"))
        except Exception:
            log.exception("buddy_state failed")
            return {"phase": "live", "tone": "live", "title": "Watching & predicting",
                    "detail": "", "progress": None, "cta": None}

    @api.get("/flow")
    def flow() -> dict:
        """Live pipeline map (nodes + edges + this instance's numbers) for the
        animated data-flow diagram. Pure read; never throws."""
        from ..domain.flow import flow_state
        try:
            return flow_state(repo, deps.get("tsdb"))
        except Exception:
            log.exception("flow_state failed")
            return {"phase": "live", "tone": "live", "nodes": {}, "edges": {}}

    @api.get("/methodology")
    def methodology() -> dict:
        """Live numbers that personalise the Methodology page (docs/METHODOLOGY.md).
        Best-effort; every field degrades to null so the page always renders."""
        from ..domain.methodology import build_methodology
        return build_methodology(repo, deps.get("tsdb"))

    @api.post("/rooms/tidy")
    async def rooms_tidy() -> dict:
        """Merge duplicate rooms: fold case/separator variants deterministically,
        then (if an LLM key is set) fold semantic duplicates too."""
        from ..domain.onboarding.rooms import (
            apply_room_mapping, tidy_rooms_deterministic)
        det = tidy_rooms_deterministic(repo)
        merged_llm = 0
        if repo.get_connection("llm") and len(det["rooms"]) > 1:
            try:
                from ..adapters.openrouter_llm import OpenRouterAdvisor
                mapping = await OpenRouterAdvisor(repo).propose_room_canon(det["rooms"])
                merged_llm = apply_room_mapping(repo, mapping)
            except Exception:
                pass
        rooms_now = sorted({b.room for b in repo.bindings() if b.room})
        return {"changed": det["changed"] + merged_llm, "rooms": rooms_now}

    @api.get("/bindings/health")
    def bindings_health(hours: int = 168) -> dict:
        """Per-binding signal health over a selectable window (1h / 24h / 7d via
        ?hours=), plus the training class balance. Answers 'is this sensor
        actually a feature?' — a binding whose feature columns never vary is dead
        weight the model ignores, and a class with 0 windows can never be
        predicted (no examples to learn). The sparkline reflects the window;
        obs/per_day stay a stable 7-day rate."""
        tsdb = deps.get("tsdb")
        if tsdb is None:
            return {"bindings": [], "classes": {}, "note": "InfluxDB not connected"}
        from datetime import datetime, timedelta, timezone
        from ..domain.features.registry import recipe_for
        hours = max(1, min(int(hours), 168 * 4))   # clamp 1h .. 4w
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
        persons = [p for p in repo.persons() if p.enabled]
        bindings = repo.bindings()

        # Sparkline = the raw signal over the selected window. Always dense and
        # window-reactive (the feature store is sparse + churns on feature-set
        # version bumps); the raw trace IS the model's per-window input.
        traces = tsdb.raw_traces([b.name for b in bindings], start, end, buckets=60)
        BINARY_ROLES = {"presence", "door", "focus", "media"}

        def _norm(vals):
            if not vals:
                return []
            lo, hi = min(vals), max(vals)
            rng = hi - lo
            return [round((v - lo) / rng, 3) if rng > 1e-9 else 0.0 for v in vals]

        counts = tsdb.raw_event_counts([b.name for b in bindings], days=7)
        # live heartbeat for the coverage map — who fired in the last 15 min
        recent = tsdb.recent_active_names(15) if hasattr(tsdb, "recent_active_names") else set()
        from ..domain.features.evidence import binding_tiers
        tiers = binding_tiers(bindings)
        # per-binding model reliance: max over promoted models of the summed
        # importance of that binding's feature columns (0 if no model yet)
        imp_by_col: dict = {}
        for m in repo.models():
            if not m.promoted:
                continue
            for col, v in (m.metrics.get("importance_all") or {}).items():
                imp_by_col[col] = max(imp_by_col.get(col, 0.0), float(v))

        from ..domain.onboarding.inventory import heuristic_reliability
        out = []
        for b in bindings:
            trace = traces.get(b.name, [])
            if not trace:
                status = "no_data"
            elif max(trace) - min(trace) < 1e-9:
                status = "constant"
            else:
                status = "alive"
            # deterministic reliability verdict (no LLM needed). The clear
            # statuses map directly; an alive-but-rarely-changing sensor goes
            # through the shared heuristic on its 7-day change rate.
            if status == "no_data":
                reliability, rel_reason = "unusable", "no recent data"
            elif status == "constant":
                reliability, rel_reason = "unusable", "value never changes"
            else:
                reliability, rel_reason = heuristic_reliability(
                    {"distinct_values": 2, "flatline_frac": 0.0,
                     "changes_per_day": round(counts.get(b.name, 0) / 7, 2)})
            kind = "binary" if b.role.value in BINARY_ROLES else "numeric"
            suffixes = recipe_for(b.role).suffixes
            feature = f"{b.name}_{suffixes[0]}" if suffixes else b.name
            model_use = round(sum(v for c, v in imp_by_col.items()
                                  if c == b.name or c.startswith(b.name + "_")), 4)
            out.append({"id": b.id, "name": b.name, "role": b.role.value,
                        "entity_id": b.entity_id, "enabled": b.enabled,
                        "status": status,
                        "reliability": reliability, "reliability_reason": rel_reason,
                        "spark": _norm(trace) if status == "alive" else [],
                        "kind": kind,
                        "obs": int(counts.get(b.name, 0)),
                        "per_day": round(counts.get(b.name, 0) / 7, 1),
                        "feature": feature, "model_use": model_use,
                        "room": b.room, "tier": tiers.get(b.name, 2),
                        "recent": b.name in recent})
        # class balance from confirmed + bootstrap labels (stable 7-day window)
        classes: dict[str, int] = {}
        label_start = end - timedelta(days=7)
        for p in persons:
            for ev in tsdb.read_labels(p.id, label_start, end):
                classes[ev.label] = classes.get(ev.label, 0) + 1
        # presence health: does each member have a LIVE home/away (person) binding?
        # Without it the away rule can't fire for them — the biggest early-accuracy
        # lever. status comes from the per-binding 'out' we just built.
        status_by_name = {o["name"]: o["status"] for o in out}
        members = []
        for p in persons:
            mine = [b for b in bindings if b.role.value == "person" and b.person_id == p.id]
            # A person who simply hasn't left the house in the window reads as
            # "constant" (home_frac flat at 1.0) — that's valid presence data, not
            # a fault. Only true "no_data" means we've never seen their state.
            # Person state arrives live over the WebSocket, never via any integration.
            alive = any(status_by_name.get(b.name) in ("alive", "constant") for b in mine)
            members.append({"id": p.id, "name": p.name, "has_person": bool(mine),
                            "person_alive": alive})
        return {"bindings": out, "classes": classes, "hours": hours, "members": members,
                "rooms_known": repo.get_setting("ha.areas") or []}

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

        from ..adapters.influx_import import earliest_source_time, import_history
        bucket = body.get("source_bucket")
        if not bucket:
            raise HTTPException(400, "missing 'source_bucket'")
        days = int(body.get("days", 60))   # 0 = import the full recorded history
        end = datetime.now(timezone.utc)
        if days <= 0:
            start = earliest_source_time(tsdb, bucket) or end - timedelta(days=90)
        else:
            start = end - timedelta(days=days)
        results = import_history(tsdb, bucket, repo.bindings(), start, end)
        return {"imported": results, "span_days": max(1, (end - start).days)}

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
        person_id = body.get("person_id")
        if not person_id:
            raise HTTPException(400, "missing 'person_id'")
        # no 'weeks' in body → use the configured training window (Settings → Model)
        weeks = int(body["weeks"]) if body.get("weeks") is not None else None
        record = train_person(person_id, tsdb, repo, deps["models"],
                              weeks=weeks,
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
        if not body.get("person_id"):
            raise HTTPException(400, "missing 'person_id'")
        record = rollback(body["person_id"], repo)
        if record is None:
            raise HTTPException(409, "nothing to roll back to")
        return {"ok": True, "version": record.version}

    # ── labels: bulk range + question skip ────────────────────────────────
    @api.post("/labels/bulk")
    def labels_bulk(body: dict) -> dict:
        """body: {person_id, start, end (ISO), activity}

        A manual correction is the user's ground truth, so it does two things:
        (1) stores a CONFIRMED training label the next model learns from, and
        (2) overwrites the displayed prediction for each window so the dashboard
        reflects the correction at once (Influx last-write-wins on the window
        timestamp; model_version='correction' marks it user-set, not model
        output). This is why a corrected heatmap cell repaints immediately."""
        tsdb = deps.get("tsdb")
        if tsdb is None:
            raise HTTPException(409, "Connect InfluxDB first")
        from datetime import datetime

        from ..domain.labeling.bulk import bulk_label_events
        from ..domain.schemas import Prediction
        missing = [k for k in ("person_id", "start", "end", "activity") if not body.get(k)]
        if missing:
            raise HTTPException(400, f"missing {', '.join(missing)}")
        activity = body["activity"]
        try:
            start_dt = datetime.fromisoformat(body["start"])
            end_dt = datetime.fromisoformat(body["end"])
        except (ValueError, TypeError):
            raise HTTPException(400, "start/end must be ISO datetimes")
        events = bulk_label_events(
            body["person_id"],
            start_dt,
            end_dt,
            activity,
            source=body.get("source", "bulk"))
        for ev in events:
            tsdb.write_label(ev)
            tsdb.write_prediction(Prediction(
                person_id=ev.person_id, window_ts=ev.window_ts,
                model_version="correction", predicted=activity, smoothed=activity,
                confidence=1.0, probabilities={activity: 1.0}))
        return {"labeled_windows": len(events)}

    @api.post("/inbox/{question_id}/skip")
    def skip(question_id: int) -> dict:
        repo.skip_question(question_id)
        return {"ok": True}

    # ── pattern discovery: cluster → name → labels ─────────────────────────
    @api.get("/clusters")
    def clusters(status: str | None = None, person: str | None = None) -> list:
        return repo.clusters(status=status, person_id=person)

    @api.get("/clusters/{cluster_id}/evidence")
    def cluster_evidence(cluster_id: int) -> dict:
        """Deterministic 'what is this' card: plain-English signature, when/where
        it happens, weekday cadence, what sits before/after it, and which named
        activity it resembles. Pure code — works with no AI key."""
        card = repo.get_cluster(cluster_id)
        if card is None:
            raise HTTPException(404, "no such pattern")
        from ..domain.discovery.evidence import build_evidence
        return build_evidence(card, repo, deps.get("tsdb"))

    @api.post("/discovery/run")
    async def discovery_run(body: dict | None = None) -> dict:
        tsdb = deps.get("tsdb")
        if tsdb is None:
            raise HTTPException(409, "Connect InfluxDB first")
        import asyncio

        from ..domain.discovery.clustering import run_discovery
        days = int((body or {}).get("days", 30))
        cards = await asyncio.to_thread(run_discovery, tsdb, repo, days)
        # optional: ask the LLM for tap-to-accept name candidates per pattern,
        # fed the same deterministic evidence card the UI shows
        if repo.get_connection("llm"):
            from ..adapters.openrouter_llm import OpenRouterAdvisor
            from ..domain.discovery.evidence import build_evidence
            adv = OpenRouterAdvisor(repo)
            acts = repo.activities()
            for c in cards:
                try:
                    ev = build_evidence(c, repo, tsdb)
                    c.suggestions = await adv.suggest_cluster_names(c, ev, acts)
                    c.suggested_slug = next(
                        (s["slug"] for s in c.suggestions if s.get("slug")), None)
                    if c.suggestions:
                        repo.save_cluster(c)
                except Exception:
                    pass
        return {"found": len(cards),
                "persons": sorted({c.person_id for c in cards})}

    @api.post("/clusters/{cluster_id}/suggest")
    async def suggest_cluster(cluster_id: int) -> dict:
        """(Re)generate LLM name suggestions for one pattern, on demand — lets a
        user ask for help on a specific card (e.g. discovery ran before a key
        was added). No-op without an LLM connection."""
        card = repo.get_cluster(cluster_id)
        if card is None:
            raise HTTPException(404, "no such pattern")
        if not repo.get_connection("llm"):
            return {"suggestions": [], "has_llm": False}
        from ..adapters.openrouter_llm import OpenRouterAdvisor
        from ..domain.discovery.evidence import build_evidence
        ev = build_evidence(card, repo, deps.get("tsdb"))
        card.suggestions = await OpenRouterAdvisor(repo).suggest_cluster_names(
            card, ev, repo.activities())
        card.suggested_slug = next(
            (s["slug"] for s in card.suggestions if s.get("slug")), None)
        repo.save_cluster(card)
        return {"suggestions": card.suggestions, "has_llm": True}

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
    async def feedback_action(body: dict) -> dict:
        """body: {"action": "HEARTH_<qid>_<idx|more>", "device": "..."} — sent by
        the Hearth integration's event-bus listener. No automations involved.

        A numeric idx is a concrete answer (records the label). The "more" token
        is the No/Other escape: it supersedes this question and pushes a FOLLOW-UP
        notification with the next batch of options, repeating until one is picked."""
        action = str(body.get("action", ""))
        parts = action.split("_")
        if len(parts) != 3 or parts[0] != "HEARTH":
            raise HTTPException(400, "not a hearth action")
        try:
            qid = int(parts[1])
        except ValueError:
            raise HTTPException(400, "malformed action id")
        q = repo.get_question(qid)
        if q is None:
            raise HTTPException(404, "unknown question")
        if q.status != "open":
            return {"ok": True, "note": "already answered"}

        # ── escape: "No" / "Other" → send the next batch as a follow-up ──────
        if parts[2] == "more":
            from ..domain.labeling.phrasing import next_batch
            from ..domain.schemas import Question
            activities = repo.activities()
            probs = q.probabilities or {q.predicted: q.confidence}
            batch, _has_more = next_batch(probs, activities, q.asked)
            repo.supersede_question(qid)
            if not batch:
                return {"ok": True, "note": "no further options"}
            child = repo.save_question(Question(
                person_id=q.person_id, window_ts=q.window_ts, predicted=q.predicted,
                confidence=q.confidence, probabilities=probs, alternatives=batch,
                asked=list(q.asked) + batch, parent_id=qid, channel=q.channel,
                ask_reason=q.ask_reason))  # follow-up keeps the parent's gold status
            notifier = deps.get("notifier")
            person = next((p for p in repo.persons() if p.id == q.person_id), None)
            if notifier is not None and person is not None:
                try:
                    await notifier.ask(child, person)
                except Exception:
                    log.exception("follow-up notification failed (stays in inbox)")
            return {"ok": True, "followup": child.id, "options": batch}

        # ── concrete answer ─────────────────────────────────────────────────
        try:
            idx = int(parts[2])
        except ValueError:
            raise HTTPException(400, "malformed action id")
        if idx >= len(q.alternatives):
            raise HTTPException(400, "option index out of range")
        answer_slug = q.alternatives[idx]
        repo.answer_question(qid, answer_slug)
        tsdb = deps.get("tsdb")
        if tsdb is not None:
            from ..domain.schemas import LabelEvent, Provenance
            tsdb.write_label(LabelEvent(person_id=q.person_id, window_ts=q.window_ts,
                                        label=answer_slug, provenance=Provenance.CONFIRMED,
                                        source="notification",
                                        gold=q.ask_reason == "explore"))
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
        ans = body.get("answer")
        if not ans:
            raise HTTPException(400, "missing 'answer'")
        q = repo.answer_question(question_id, ans)
        if q is None:
            raise HTTPException(404, "unknown question")
        tsdb = deps.get("tsdb")
        if tsdb is not None:
            from ..domain.schemas import LabelEvent, Provenance
            tsdb.write_label(LabelEvent(person_id=q.person_id, window_ts=q.window_ts,
                                        label=ans, provenance=Provenance.CONFIRMED,
                                        source="inbox", gold=q.ask_reason == "explore"))
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

    @api.post("/system/whats-new/seen")
    def whats_new_seen() -> dict:
        """Acknowledge the 'what's new' the buddy showed after an update —
        marks this build seen so it stops announcing."""
        from ..domain.whatsnew import mark_seen
        mark_seen(repo)
        return {"ok": True}

    @api.post("/system/restart")
    def restart_app() -> dict:
        """Restart the container so newly-saved connections (HA / InfluxDB) take
        effect — those are wired at startup. Relies on the compose restart
        policy (unless-stopped): we exit, Docker brings us straight back."""
        import os
        import threading
        threading.Timer(0.6, lambda: os._exit(0)).start()
        return {"ok": True, "note": "restarting — back in a few seconds"}

    @api.post("/system/reset")
    def factory_reset(body: dict, response: Response) -> dict:
        """Wipe configuration so the app re-enters first-run setup. With
        wipe_data=true, also erase all recorded sensor history, features and
        models. Destructive and irreversible — the UI gates it behind a typed
        confirmation."""
        wipe_data = bool(body.get("wipe_data"))
        if wipe_data:
            tsdb = deps.get("tsdb")
            if tsdb is not None and hasattr(tsdb, "wipe_all"):
                try:
                    tsdb.wipe_all()
                except Exception:
                    log.exception("reset: time-series wipe failed")
            import shutil
            mdir = getattr(deps.get("models"), "models_dir", None)
            if mdir is not None:
                shutil.rmtree(mdir, ignore_errors=True)
        uploads = deps.get("uploads_dir")
        if uploads is not None:
            for f in uploads.glob("*"):
                try:
                    f.unlink()
                except Exception:
                    pass
        repo.factory_reset()                          # clears users → needs_setup
        response.delete_cookie("hearth_session")       # current session is gone
        return {"ok": True, "wiped_data": wipe_data}

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

    # ── data history retention (raw + features bucket lifetime) ───────────
    @api.get("/settings/retention")
    def get_retention() -> dict:
        from ..adapters.influx_store import DEFAULT_RETENTION_DAYS
        days = repo.get_setting("retention.days", DEFAULT_RETENTION_DAYS)
        if not isinstance(days, int):
            days = DEFAULT_RETENTION_DAYS
        return {"days": days, "default": DEFAULT_RETENTION_DAYS}

    @api.post("/settings/retention")
    def set_retention_ep(body: dict) -> dict:
        """Set how long raw events + features are kept (the training corpus).
        days=0 keeps data forever; otherwise 1..3650 (10y). Applied to the live
        InfluxDB buckets at once when connected, else on the next restart."""
        raw = body.get("days")
        try:
            days = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, "days must be an integer (0 = keep forever)")
        if days != 0 and not (1 <= days <= 3650):
            raise HTTPException(400, "days must be 0 (forever) or between 1 and 3650")
        repo.set_setting("retention.days", days)
        tsdb = deps.get("tsdb")
        applied = False
        if tsdb is not None and hasattr(tsdb, "set_retention"):
            try:
                tsdb.set_retention(days)
                applied = True
            except Exception:
                log.exception("retention update failed")
        return {"ok": True, "days": days, "applied": applied,
                "note": None if applied else
                        "saved — applies when InfluxDB is connected (restart to apply now)"}

    @api.get("/system/status")
    def status() -> dict:
        return {"bindings": len(repo.bindings()),
                "persons": len(repo.persons()),
                "tsdb": deps.get("tsdb") is not None,
                "ha": deps.get("events") is not None}

    return api
