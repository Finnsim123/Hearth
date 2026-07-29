"""Binding auto-audit — the loop that ACTS on what Hearth already knows.

The failure it exists for (observed in the wild): the model predicts "cooking"
from `coffee_machine_temperature ↓` and `coffee_machine_humidity ↓` — tier-3
ambient drift — while the same physical coffee machine exposes a power/state
entity that is UNBOUND. The evidence system flags "weak evidence", the device
hierarchy knows the sibling exists, feature importance knows what the model
leans on — but nothing connects them. This module is that connection:

  detect   a model leaning on a device's AMBIENT entity
  locate   the unused DIRECT sibling (power/on/state) on the same device
  propose  bind the sibling + stop training on the ambient one
           (always ASK — findings become advisories, never silent rebinds)
  verify   after the user applies + retrains, the promotion gate decides

Pure detection lives here (data in, findings out); the apply/action side is in
the API layer. Consumes only cached state: bindings, promoted-model importances,
and the ha.devices / ha.entity_device scan caches — no HA round-trip.
"""
from __future__ import annotations

import logging
import re

from .features.evidence import binding_tiers
from .schemas import Binding

log = logging.getLogger(__name__)

# A binding must carry at least this share of total importance mass before we
# call the model "leaning on it" — below this it's background.
MIN_RELIANCE = 0.02
# Appliance-ish names: an ambient sensor ON one of these is telemetry about the
# machine, not the room — safe to propose excluding even with no sibling found.
_APPLIANCE = re.compile(
    r"coffee|koffie|oven|stove|kook|wash|wasmachine|dryer|droger|dish|vaatwas|"
    r"printer|fridge|koelkast|freezer|vriezer|kettle|waterkoker|airfryer|"
    r"machine|boiler|tv\b|television")
# Sibling domains that can carry a DIRECT "the machine is doing something" signal.
_DIRECT_DOMAINS = {"switch", "binary_sensor", "sensor", "media_player", "light"}
# Domains that mean the device can DO something (an appliance), not just
# measure. A device with none of these is a pure monitor — every entity on it
# is telemetry, so it can never offer a "direct human signal" and proposing
# one is a category error (seen live: an air monitor's fan-status binary and
# its offline_since diagnostic suggested as fixes, after brightness was).
_ACTUATOR_DOMAINS = {"switch", "light", "media_player", "climate", "fan",
                     "vacuum", "lock", "cover", "humidifier", "water_heater"}
# For sensor/binary_sensor siblings, require POSITIVE action evidence in the
# name (power draw, running state, door/motion/presence) — absence of bad
# words is not enough, as the brightness→fan→offline_since sequence proved.
_ACTIONY = re.compile(
    r"power|watt|current|amper|verbruik|running|active|bezig|in_use|"
    r"door|deur|motion|beweg|occup|presence|aanwezig|open|button|knop")
_DIAGNOSTIC = re.compile(
    r"offline|online|since|uptime|rssi|signal|firmware|update|connect|link")


def reliance_by_binding(importance: dict[str, float],
                        bindings: list[Binding]) -> dict[str, float]:
    """{binding.name: share of total |importance| mass} — longest-prefix match of
    feature columns onto binding names (names may contain underscores)."""
    names = sorted((b.name for b in bindings), key=len, reverse=True)
    mass: dict[str, float] = {}
    total = 0.0
    for col, w in (importance or {}).items():
        w = abs(float(w))
        total += w
        for n in names:                          # longest name first → best match
            if col == n or col.startswith(n + "_"):
                mass[n] = mass.get(n, 0.0) + w
                break
    if total <= 0:
        return {}
    return {n: v / total for n, v in mass.items()}


def _device_entities(repo) -> dict[str, list[str]]:
    """Reverse of the ha.entity_device cache: {device_id: [entity_id, …]}."""
    out: dict[str, list[str]] = {}
    for eid, did in (repo.get_setting("ha.entity_device") or {}).items():
        if did:
            out.setdefault(did, []).append(eid)
    return out


def _looks_direct(entity_id: str) -> bool:
    """Could this UNBOUND entity carry the device's direct on/off/power signal?
    Judged from the entity id alone (that's all the cache holds). Actuator
    domains qualify on domain alone; sensor/binary_sensor need POSITIVE
    action evidence in the name — a blocklist alone kept losing whack-a-mole
    (brightness, then fan status, then offline_since, each proposed in turn
    as an air monitor's "direct signal")."""
    from .onboarding.advisor import is_noise
    domain = entity_id.split(".")[0]
    if domain not in _DIRECT_DOMAINS:
        return False
    if is_noise({"entity_id": entity_id, "domain": domain}):
        return False
    obj = entity_id.split(".", 1)[-1].lower()
    if _DIAGNOSTIC.search(obj):
        return False
    if domain in ("switch", "media_player", "light"):
        return True
    # ambient-metric names are exactly what we're steering AWAY from — incl.
    # light-LEVEL sensors (brightness/illuminance): a room being bright is
    # ambient drift, not a human doing something
    if re.search(r"temp|humid|vocht|lucht|co2|pressure|druk|lux|illum|"
                 r"bright|helder|luminance|fan", obj):
        return False
    return bool(_ACTIONY.search(obj))


def audit_bindings(repo, importance: dict[str, float]) -> list[dict]:
    """The detection pass. Returns findings, strongest reliance first:

    {kind: "bind_sibling", ambient binding info, reliance, device,
     candidates: [unbound direct sibling entity_ids]}   — the coffee-machine case
    {kind: "exclude_ambient", …}                        — appliance telemetry with
                                                          no usable sibling
    Never mutates anything."""
    all_bindings = repo.bindings()
    bindings = [b for b in all_bindings if b.enabled]
    if not bindings or not importance:
        return []
    tiers = binding_tiers(bindings)
    reliance = reliance_by_binding(importance, bindings)
    by_name = {b.name: b for b in bindings}
    # candidates must exclude EVERY known binding, disabled ones included — a
    # disabled binding means we already tried that entity (or pruning benched
    # it); re-proposing it every audit round is thrash (seen live: a pruned
    # brightness binary re-suggested daily as the "fix")
    bound_eids = {b.entity_id for b in all_bindings}
    ent_dev = repo.get_setting("ha.entity_device") or {}
    dev_ents = _device_entities(repo)
    from .hierarchy import load_device_catalog
    catalog = load_device_catalog(repo)

    findings: list[dict] = []
    for name, share in sorted(reliance.items(), key=lambda kv: -kv[1]):
        b = by_name.get(name)
        if b is None or share < MIN_RELIANCE:
            continue
        if tiers.get(name, 2) != 3 or b.model_excluded:
            continue                                # only ambient reliance is a smell
        did = ent_dev.get(b.entity_id)
        dev = catalog.get(did) if did else None
        dev_label = (dev or {}).get("name") or (dev or {}).get("model")
        appliancey = bool(_APPLIANCE.search(
            f"{b.entity_id} {b.name} {dev_label or ''}".lower()))
        # pure monitors (air quality stations, weather sensors, phones-as-
        # telemetry): no actuator domain anywhere on the device and no
        # appliance-ish name — EVERYTHING on it is telemetry, so there is no
        # direct signal to propose and modest reliance on it is legitimate
        # occupancy seasoning, not a smell. No finding at all.
        has_actuator = any(e.split(".")[0] in _ACTUATOR_DOMAINS
                           for e in dev_ents.get(did, []))
        if not has_actuator and not appliancey:
            continue
        siblings = [e for e in dev_ents.get(did, []) if did
                    and e != b.entity_id and e not in bound_eids and _looks_direct(e)]
        if siblings:
            findings.append({
                "kind": "bind_sibling", "binding_id": b.id, "binding_name": b.name,
                "entity_id": b.entity_id, "reliance": round(share, 4),
                "device_id": did, "device": dev_label, "candidates": sorted(siblings),
                "why": (f"the model leans on {b.name} ({share:.0%} of its attention) — "
                        f"ambient telemetry of “{dev_label or 'this device'}”. The same "
                        f"device has a direct signal that isn't being used."),
            })
        elif appliancey:
            findings.append({
                "kind": "exclude_ambient", "binding_id": b.id, "binding_name": b.name,
                "entity_id": b.entity_id, "reliance": round(share, 4),
                "device_id": did, "device": dev_label, "candidates": [],
                "why": (f"{b.name} is appliance telemetry (temperature/…) yet carries "
                        f"{share:.0%} of the model's attention — noise it should stop "
                        f"training on."),
            })
    return _merge_by_device(findings)


def _merge_by_device(findings: list[dict]) -> list[dict]:
    """One card per (device, kind), not one per feature family — a device with
    co2 + temperature + pm25 bindings produced three near-identical findings
    with the same fix (seen live). The primary keeps the strongest reliance;
    the merged card lists every sibling family and sums the attention share,
    and apply_finding retires them all in one tap (also_binding_ids)."""
    merged: dict[tuple, dict] = {}
    out: list[dict] = []
    for f in findings:
        key = (f.get("device_id"), f.get("kind"))
        if not f.get("device_id") or key not in merged:
            if f.get("device_id"):
                merged[key] = f
            f["also_binding_ids"] = []
            f["also_names"] = []
            out.append(f)
            continue
        prime = merged[key]
        prime["also_binding_ids"].append(f["binding_id"])
        prime["also_names"].append(f["binding_name"])
        prime["reliance"] = round(prime["reliance"] + f["reliance"], 4)
        names = ", ".join([prime["binding_name"]] + prime["also_names"])
        if prime["kind"] == "bind_sibling":
            prime["why"] = (f"the model leans on {names} "
                            f"({prime['reliance']:.0%} of its attention combined) — "
                            f"ambient telemetry of “{prime.get('device') or 'this device'}”. "
                            f"The same device has a direct signal that isn't being used.")
        else:
            prime["why"] = (f"{names} are appliance telemetry yet carry "
                            f"{prime['reliance']:.0%} of the model's attention combined — "
                            f"noise it should stop training on.")
    return sorted(out, key=lambda f: -f.get("reliance", 0.0))


def apply_finding(repo, finding: dict, candidate: str | None = None) -> dict:
    """Apply ONE user-approved finding (the 'always ask' hand):
      bind_sibling     bind the chosen direct sibling (role via suggest_role,
                       room inherited, owner via binding_owner) AND stop training
                       on the ambient binding (model_excluded — it stays ingested
                       and visible to discovery).
      exclude_ambient  just stop training on the ambient binding.
    Returns {ok, excluded, bound?}; the caller retrains + the promotion gate
    verifies the change actually helped."""
    from .features.person_scope import binding_owner
    from .onboarding.advisor import is_bindable, suggest_role
    from .schemas import Role

    kind = finding.get("kind")
    ambient = next((b for b in repo.bindings()
                    if b.id == finding.get("binding_id")
                    or b.name == finding.get("binding_name")), None)
    if ambient is None:
        return {"ok": False, "reason": "binding_gone"}

    bound = None
    if kind == "bind_sibling":
        eid = candidate or (finding.get("candidates") or [None])[0]
        if not eid:
            return {"ok": False, "reason": "no_candidate"}
        if any(b.entity_id == eid for b in repo.bindings()):
            return {"ok": False, "reason": "already_bound"}
        domain = eid.split(".")[0]
        role = suggest_role({"entity_id": eid, "domain": domain})
        if role is None and domain == "switch":
            role = Role.POWER                      # a bare appliance switch
        if role is None or not is_bindable(eid, role, override=True):
            return {"ok": False, "reason": "not_bindable"}
        taken = {b.name for b in repo.bindings()}
        base = re.sub(r"[^a-z0-9]+", "_", eid.split(".", 1)[-1].lower()).strip("_") or "sensor"
        name = base
        n = 2
        while name in taken:
            name, n = f"{base}_{n}", n + 1
        nb = Binding(entity_id=eid, role=role, name=name, room=ambient.room)
        nb.person_id = binding_owner(nb, repo.persons())
        bound = repo.save_binding(nb)

    repo.save_binding(ambient.model_copy(update={"model_excluded": True}))
    # a merged per-device finding retires EVERY listed sibling family at once
    excluded_names = [ambient.name]
    for bid in finding.get("also_binding_ids") or []:
        sib = next((b for b in repo.bindings() if b.id == bid), None)
        if sib is not None and not sib.model_excluded:
            repo.save_binding(sib.model_copy(update={"model_excluded": True}))
            excluded_names.append(sib.name)

    # retire the finding + tidy the advisory
    left = [f for f in (repo.get_setting("audit.findings") or [])
            if not (f.get("binding_id") == finding.get("binding_id")
                    and f.get("kind") == kind)]
    repo.set_setting("audit.findings", left)
    try:
        from . import advisories, events
        if not left:
            advisories.clear_advisory(repo, "bindaudit")
        detail = (f"bound {bound.entity_id} as {bound.role.value}, " if bound else "") \
            + f"stopped training on {', '.join(excluded_names)}"
        events.record_event(repo, "binding_audit",
                            f"Fixed a wrong-sensor reliance ({ambient.name})", detail)
    except Exception:
        log.debug("apply_finding: event/advisory failed", exc_info=True)
    return {"ok": True, "excluded": excluded_names,
            "bound": {"entity_id": bound.entity_id, "role": bound.role.value,
                      "name": bound.name} if bound else None}


def run_binding_audit(repo) -> list[dict]:
    """Audit against the promoted models' pooled importances, store the findings
    (settings key `audit.findings`), and raise ONE dismissible advisory when
    anything actionable was found. Always-ask: nothing is rebound here."""
    imp: dict[str, float] = {}
    try:
        for m in repo.models():
            if not getattr(m, "promoted", False):
                continue
            for col, v in ((m.metrics or {}).get("importance_all") or {}).items():
                imp[col] = max(imp.get(col, 0.0), float(v))
    except Exception:
        log.exception("binding audit: importance pool failed")
        return []
    findings = audit_bindings(repo, imp)
    repo.set_setting("audit.findings", findings)
    try:
        from . import advisories
        if findings:
            top = findings[0]
            more = f" (+{len(findings) - 1} more)" if len(findings) > 1 else ""
            advisories.record_advisory(
                repo, "bindaudit", "The model is leaning on the wrong sensors",
                f"e.g. {top['why']}{more} Review the suggested fixes — one tap each.",
                severity="warn", cta={"label": "Review", "href": "/sensors#audit"})
        else:
            advisories.clear_advisory(repo, "bindaudit")
    except Exception:
        log.debug("binding audit: advisory failed", exc_info=True)
    return findings
