"""The buddy — a single resolver for "what is Hearth doing right now?".

Drives the ember mascot (every page) and the dashboard's first-run timeline from
ONE source of truth, so they never disagree. Pure read, cheap: a few settings +
counts. Returns a friendly, first-person narration of the current phase.

Phase priority (first match wins):
  fast-track failed → live incident (domain/health: can't reach HA, history
  failing, …) → fast-track / seed (import→sort→map→features→train→patterns) →
  stalled sensors → retraining → questions waiting → live (watch & predict) →
  collecting (no data yet) → waiting (nothing connected).

Any component records a problem via domain.health.record_issue and the buddy
surfaces it prominently (and loudly) until it's cleared or expires — so runtime
failures reach the user instead of dying silently in the logs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Where each fast-track stage sits on the overall 0..1 progress bar.
_FT_SPAN = {
    "importing": (0.02, 0.35), "imported": (0.35, 0.37), "pruned_empty": (0.37, 0.4),
    "building_features": (0.40, 0.70), "features_built": (0.70, 0.72),
    "training": (0.72, 0.88), "trained": (0.88, 0.90),
    "discovering": (0.90, 0.99), "discovered": (0.99, 1.0),
}
_FT_COPY = {
    "importing": ("Scanning your history", "Reading everything your home has recorded"),
    "imported": ("History imported", "Got it all — tidying up"),
    "pruned_empty": ("Tidying up", "Setting aside sensors with no history"),
    "building_features": ("Making sense of it", "Turning raw sensors into features"),
    "features_built": ("Features ready", "Now I can start learning"),
    "training": ("Learning your routines", "Training a model for each of you"),
    "trained": ("Almost there", "Model trained — finishing up"),
    "discovering": ("Spotting your patterns", "Looking for routines worth naming"),
    "discovered": ("Patterns found", "Ready for you to name them"),
}


def _span_phrase(days: int) -> str:
    if days < 45:
        return f"{days} days"
    if days < 365:
        return f"~{round(days / 30)} months"
    years = days / 365
    return f"~{years:.0f} years" if years >= 1.95 else "~1 year"


def _state(phase, tone, title, detail, progress=None, cta=None, ack=None,
           ack_label=None) -> dict:
    return {"phase": phase, "tone": tone, "title": title, "detail": detail,
            "progress": progress, "cta": cta, "ack": ack, "ack_label": ack_label}


def _fasttrack(ft: dict) -> dict:
    stage = ft.get("stage", "importing")
    lo, hi = _FT_SPAN.get(stage, (0.0, 1.0))
    progress = lo
    if stage == "building_features" and ft.get("of"):
        progress = lo + (hi - lo) * (ft.get("chunk", 0) / max(ft["of"], 1))
    else:
        progress = (lo + hi) / 2
    title, detail = _FT_COPY.get(stage, ("Setting things up", "One moment"))
    if stage == "importing" and ft.get("span_days"):
        detail = f"Reading {_span_phrase(int(ft['span_days']))} of history"
    if stage == "imported" and ft.get("points"):
        detail = f"Imported {int(ft['points']):,} readings — tidying up"
    if stage == "discovered" and ft.get("found"):
        detail = f"Found {int(ft['found'])} routine{'s' if ft['found'] != 1 else ''} to name"
    tone = "live" if stage in ("discovered", "trained") else "work"
    return _state(f"setup:{stage}", tone, title, detail, round(progress, 3))


def buddy_state(repo, tsdb) -> dict:
    now = datetime.now(timezone.utc)

    ft = _get(repo, "fasttrack.status") or {}
    stage = ft.get("stage")
    if stage == "failed":
        return _state("error", "error", "I hit a snag importing",
                      ft.get("error") or "Check the logs, then re-run setup.",
                      cta={"label": "Settings", "href": "/settings"})

    # A live incident (can't reach HA, history failing, …) recorded by any
    # component takes priority — the buddy is where problems must surface.
    from .health import current_issue
    issue = current_issue(repo)
    if issue:
        return _state(f"issue:{issue.get('kind', 'problem')}", "alert",
                      issue.get("title", "Something's up"),
                      issue.get("detail", ""), cta=issue.get("cta"))

    # Seeding (scan → sort → map) runs BEFORE fast-track and must be surfaced in
    # order, otherwise the Welcome stepper sees "importing" the whole time and
    # lights up several steps at once. Waiting on the user to approve the triage
    # parks us at the sorting step until they say go (strictly sequential).
    if _get(repo, "triage.awaiting"):
        return _state("setup:triaging", "ask", "Your turn — pick the groups",
                      "Keep or skip each group, then let me analyse them")
    # Only narrate seed sub-phases as setup:* during ONBOARDING (fast-track
    # pending). A deliberate later re-map has no fast-track pending and is
    # handled by the remap:* block further down.
    seed = _get(repo, "seed.status") or {}
    sstage = seed.get("stage")
    _SEED = {"scanning": ("setup:scanning", "Scanning your home", "Reading your entities", 0.2),
             "triaging": ("setup:triaging", "Sorting into groups", "Clustering what I found", 0.45),
             "mapping": ("setup:mapping", "Reading your sensors", "Giving each one a role", 0.7),
             "writing_rules": ("setup:mapping", "Reading your sensors", "Writing your starter rules", 0.85),
             "designing_features": ("setup:mapping", "Reading your sensors", "Designing feature recipes for your sensors", 0.92)}
    if (_get(repo, "fasttrack.pending") and sstage != "done"
            and (_get(repo, "seed.pending") or sstage in _SEED)):
        ph, title, detail, prog = _SEED.get(sstage, _SEED["scanning"])
        return _state(ph, "work", title, detail, prog)

    if _get(repo, "fasttrack.pending") or (stage and stage != "done"):
        return _fasttrack(ft)

    # integrating user-approved new sensors (scoped re-analysis + retrain)
    intg = _get(repo, "discovery.integrate") or {}
    istage = intg.get("stage")
    if istage and istage != "done":
        copy = {"analyzing": ("Analysing your new sensors",
                              "Working out what they can tell me"),
                "retraining": ("Retraining on your new sensors",
                               "A fresh model is on the way")}
        title, detail = copy.get(istage, ("Adding your new sensors", "One moment"))
        return _state(f"integrate:{istage}", "work", title, detail)

    # re-mapping sensors after an AI-key retry (Settings "Try again"). run_seed
    # writes seed.status as it goes; narrate the live stages so the click gets a
    # visible reaction. Sits above llm_error so the active remap wins over the
    # stale "key rejected" message (which clears once the first call succeeds).
    # During initial onboarding this is masked by fast-track (its pending flag
    # is set), so it only surfaces for a deliberate re-map.
    seed = _get(repo, "seed.status") or {}
    if seed.get("stage") in ("scanning", "mapping", "writing_rules", "designing_features"):
        copy = {"scanning": ("Re-reading your sensors",
                             "Taking a fresh look at everything Home Assistant exposes"),
                "mapping": ("Re-mapping your sensors",
                            "Matching sensors to roles with your AI key"),
                "writing_rules": ("Refreshing your rules",
                                  "Writing smarter household rules from the new mapping"),
                "designing_features": ("Designing your features",
                                       "Tailoring feature recipes to your sensors with your AI key")}
        title, detail = copy[seed["stage"]]
        return _state(f"remap:{seed['stage']}", "work", title, detail)

    persons = [p for p in _safe(repo.persons, []) if getattr(p, "enabled", True)]
    promoted = [m for m in _safe(repo.models, []) if m.promoted]
    bound = len([b for b in _safe(repo.bindings, []) if b.enabled])

    # stalled: bound sensors but nothing arriving recently
    if tsdb is not None and bound:
        recent = _safe(lambda: tsdb.count_raw_events(3), None)
        first = _safe(lambda: tsdb.first_raw_time(), None)
        if recent == 0 and first is not None and (now - first) > timedelta(hours=6):
            return _state("stalled", "alert", "I've stopped hearing from your sensors",
                          "No new readings in a few hours — is Home Assistant still connected?",
                          cta={"label": "Check sensors", "href": "/sensors"})

    # AI assistant in trouble — silent failures degrade sensor mapping, so
    # surface it with a link to top up / fix the key, or flag a connectivity fault.
    llm_st = _get(repo, "llm.status") or {}
    if llm_st and not llm_st.get("ok", True):
        code = llm_st.get("code")
        if code == 429:
            title, detail = "AI assistant rate-limited", "Your AI key hit its rate limit — mapping used the basic fallback. It'll recover, or top up for headroom."
        elif code == 402:
            title, detail = "AI credits used up", "Top up your AI provider to restore smart sensor mapping (basic fallback is active)."
        elif code in (401, 403):
            title, detail = "AI key was rejected", "Check the AI assistant key in Settings — sensor mapping is on the basic fallback."
        elif code == 0 or (isinstance(code, int) and code >= 500):
            title, detail = "I can't reach the AI service", "The AI endpoint isn't responding — mapping is on the basic fallback. Check the URL/network, or try again later."
        else:
            title, detail = None, None
        if title:
            cta = ({"label": "Open AI provider", "href": _llm_link(repo)} if code in (402, 429)
                   else {"label": "Settings", "href": "/settings"})
            return _state("llm_error", "alert", title, detail, cta=cta)

    # retraining in progress (weekly / first train)
    ts = _get(repo, "training.status") or {}
    if ts.get("running"):
        return _state("retraining", "work", "Refreshing what I know",
                      "Re-learning from your latest confirmations", None)

    # an in-app update just landed — celebrate it once and say what changed
    # (from the deployed commit message). Below real problems, above routine
    # nudges. Dismissing (ack) marks this build seen.
    from .whatsnew import pending_news
    news = _safe(lambda: pending_news(repo))
    if news:
        detail = news["subject"]
        if news.get("body"):
            first = news["body"].splitlines()[0].strip()
            if first and first.lower() != news["subject"].lower():
                detail = f"{detail} — {first}"
        return _state("whatsnew", "news", "Updated — here's what's new", detail,
                      ack="/api/system/whats-new/seen")

    # standing advisory (sensor demoted, model health) — surfaced above routine
    # nudges because it affects what the user can trust; dismissible (snoozes it).
    # info-level advisories stay passive (Activity page), so only warn/critical here.
    from .advisories import worst_advisory
    adv = _safe(lambda: worst_advisory(repo))
    if adv:
        tone = "alert" if adv.get("severity") == "critical" else "ask"
        return _state(f"advisory:{adv['kind']}", tone, adv["title"], adv["detail"],
                      cta=adv.get("cta"),
                      ack=f"/api/advisories/dismiss?kind={adv['kind']}",
                      ack_label="Dismiss")

    # questions waiting — a gentle nudge
    open_q = _safe(lambda: repo.open_questions(), [])
    if open_q:
        n = len(open_q)
        return _state("questions", "ask", f"I've got {n} question{'s' if n != 1 else ''} for you",
                      "A minute of your time sharpens the model",
                      cta={"label": "Open inbox", "href": "/inbox"})

    # new sensors found by the daily scan, waiting for the user to approve them
    # into the model (detect-then-ask: never auto-added). A gentle, actionable nudge.
    pending = _get(repo, "discovery.pending") or []
    if isinstance(pending, list) and pending:
        n = len(pending)
        return _state("new_sensors", "ask",
                      f"I found {n} new sensor{'s' if n != 1 else ''}",
                      "Want them in your model? Review and approve when you're ready.",
                      cta={"label": "Review sensors", "href": "/sensors"})

    # live — a model is promoted and predicting
    if promoted and tsdb is not None:
        states = []
        for p in persons:
            rows = _safe(lambda p=p: tsdb.read_predictions(p.id, now - timedelta(hours=6), now), [])
            if rows:
                last = rows[-1]
                st = last.get("smoothed") or last.get("predicted")
                # make the glass-box ethos visible: known fact vs model guess
                known = str(last.get("model_version", "")).lower().startswith("fact")
                conf = last.get("confidence")
                tag = " (known)" if known else (f" ({round(conf * 100)}%)"
                                                if isinstance(conf, (int, float)) else "")
                states.append(f"{p.name}: {st}{tag}")
        events = _safe(lambda: tsdb.count_raw_events(24), None)
        detail = " · ".join(states) if states else (
            f"{int(events):,} readings today" if events else "Keeping an eye on things")
        return _state("live", "live", "Watching & predicting", detail)

    # collecting — recording, but not enough to train yet
    first = _safe(lambda: tsdb.first_raw_time(), None) if tsdb is not None else None
    if first is not None:
        days = (now - first).total_seconds() / 86400
        return _state("collecting", "work", "Getting to know your home",
                      f"Day {max(1, round(days))} — learning your rhythms before I predict",
                      progress=round(min(days / 10.0, 0.95), 2))

    # No data yet — diagnose precisely so the message is actionable. Connections
    # are wired at startup; saving them in Settings needs a restart to apply.
    settings_cta = {"label": "Settings", "href": "/settings"}
    influx_conf = bool(_safe(lambda: repo.get_connection("influx")))
    ha_conf = bool(_safe(lambda: repo.get_connection("ha")))
    if tsdb is None:
        return _state("waiting", "work", "InfluxDB isn't connected",
                      ("Saved it just now? Restart Hearth to apply. Otherwise check the URL and token."
                       if influx_conf else "Point Hearth at your InfluxDB so it can store sensor data."),
                      cta=settings_cta)
    if not ha_conf:
        return _state("waiting", "work", "Connect Home Assistant",
                      "Add your Home Assistant URL and token so I can read your sensors.",
                      cta=settings_cta)
    return _state("waiting", "work", "Waiting for the first readings",
                  "Connected — I've started watching. If nothing arrives, restart Hearth to apply the new connection.",
                  cta=settings_cta)


def _llm_link(repo) -> str:
    """A useful 'fix it' link for the configured AI provider."""
    conn = _safe(lambda: repo.get_connection("llm")) or {}
    url = conn.get("url") or ""
    if "openrouter" in url:
        return "https://openrouter.ai/credits"
    import re
    m = re.match(r"(https?://[^/]+)", url)
    return m.group(1) if m else "/settings"


def _get(repo, key):
    try:
        return repo.get_setting(key)
    except Exception:
        return None


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default
