"""Watch for newly-added HA devices and offer to integrate them.

Daily (and on demand) we diff the live device registry against a stored snapshot. A
genuinely new device that looks useful (not infra, has at least one keepable entity)
raises an advisory + a Home Assistant push — "New device: Oral-B. Use it for
predictions?". Answering yes binds its useful entities and retrains; no is remembered.

The first-ever scan only seeds the snapshot (no notifications), so existing homes aren't
spammed with everything they already own.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SEEN_DEV = "ha.devices.seen"
SEEN_INT = "ha.integrations.seen"
PENDING = "ha.pending_nodes"


async def scan_new_nodes(repo, events, notifier=None) -> dict:
    """Detect new useful devices → pending + advisory + push. Returns {"new": n}."""
    from .. import advisories
    from .. import events as ev_log
    from ..hierarchy import device_relevance, load_decisions, relevance_of
    try:
        tree = await events.discover_all()
        integrations, devices, entities = tree["integrations"], tree["devices"], tree["entities"]
    except Exception:
        log.exception("hierarchy scan failed")
        return {"new": 0}
    # cache the device catalog so other surfaces (facts picker, coverage, drill-down)
    # can show device context cheaply without hitting HA again.
    repo.set_setting("ha.devices", {d["id"]: {k: d.get(k) for k in
                     ("name", "area", "manufacturer", "model")}
                     for d in devices if d.get("id")})
    repo.set_setting("ha.entity_device", {e["entity_id"]: e["device_id"]
                     for e in entities if e.get("device_id")})

    integ_by = {i["entry_id"]: i for i in integrations if i.get("entry_id")}
    dev_by = {d["id"]: d for d in devices if d.get("id")}
    ents_by_dev: dict[str, list] = {}
    for e in entities:
        if e.get("device_id"):
            ents_by_dev.setdefault(e["device_id"], []).append(e)
    decisions = load_decisions(repo)

    seen_dev = set(repo.get_setting(SEEN_DEV) or [])
    first_run = not seen_dev
    pending = repo.get_setting(PENDING) or []
    pending_ids = {p.get("id") for p in pending}

    fresh = []
    for did, d in dev_by.items():
        if did in seen_dev:
            continue
        if device_relevance(d) == "skip":
            continue
        useful = [e["entity_id"] for e in ents_by_dev.get(did, [])
                  if relevance_of(e, dev_by, integ_by, decisions)[0] in ("keep", "unsure")]
        if not useful:
            continue
        fresh.append({"kind": "device", "id": did,
                      "name": d.get("name") or d.get("model") or did,
                      "detail": f"{d.get('manufacturer') or ''} {d.get('model') or ''}".strip(),
                      "entities": useful})

    # advance the snapshot regardless, so a skipped node isn't re-evaluated forever
    repo.set_setting(SEEN_DEV, sorted(seen_dev | set(dev_by)))
    repo.set_setting(SEEN_INT, sorted(set(repo.get_setting(SEEN_INT) or []) | set(integ_by)))

    if first_run or not fresh:
        return {"new": 0}

    added = 0
    for c in fresh:
        if c["id"] in pending_ids:
            continue
        pending.append(c)
        added += 1
        advisories.record_advisory(
            repo, f"newnode:{c['id']}", f"New device: {c['name']}",
            f"Use {c['name']} for predictions? Review to add or dismiss.",
            severity="info", cta={"label": "Review", "href": "/sensors"})
        ev_log.record_event(repo, "new_device", f"New device seen: {c['name']}", c.get("detail", ""))
    repo.set_setting(PENDING, pending)
    if added and notifier is not None:
        await _push(repo, notifier, [c for c in fresh if c["id"] in {p["id"] for p in pending}])
    return {"new": added}


async def _push(repo, notifier, candidates) -> None:
    """One ACTIONABLE notification per new device (capped at 3 per scan): tap
    "Use it" to bind its useful entities, "Not now" to remember the skip — no
    need to open Hearth. Action ids HEARTH_DEV_<id>_yes|no ride the existing
    integration tap-forward to /api/feedback/action; first tap wins (the decide
    core is idempotent). The deep link still lands on /sensors for the full view."""
    if not candidates:
        return
    base = (repo.get_setting("hearth_base_url", "") or "").rstrip("/")
    try:
        recipients = [p for p in repo.persons() if getattr(p, "notify_system", False)]
        for c in candidates[:3]:
            n_ent = len(c.get("entities") or [])
            detail = c.get("detail") or ""
            msg = (f"{detail + ' — ' if detail else ''}{n_ent} useful "
                   f"sensor{'s' if n_ent != 1 else ''}. Use it for predictions?")
            data = {
                "actions": [
                    {"action": f"HEARTH_DEV_{c['id']}_yes", "title": "✓ Use it"},
                    {"action": f"HEARTH_DEV_{c['id']}_no", "title": "Not now"},
                ],
                "tag": f"hearth_dev_{c['id']}",
                "persistent": False,
            }
            if base:
                data["url"] = f"{base}/sensors"
            for p in recipients:
                await notifier.notify(p, f"New device: {c['name']}", msg, data)
    except Exception:
        log.exception("new-device push failed")
