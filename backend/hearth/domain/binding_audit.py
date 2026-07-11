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
    Judged from the entity id alone (that's all the cache holds): domain +
    a non-diagnostic name. suggest_role does the real call at bind time."""
    from .onboarding.advisor import is_noise
    domain = entity_id.split(".")[0]
    if domain not in _DIRECT_DOMAINS:
        return False
    if is_noise({"entity_id": entity_id, "domain": domain}):
        return False
    obj = entity_id.split(".", 1)[-1].lower()
    # ambient-metric names are exactly what we're steering AWAY from
    if re.search(r"temp|humid|vocht|lucht|co2|pressure|druk|lux|illum", obj):
        return False
    return True


def audit_bindings(repo, importance: dict[str, float]) -> list[dict]:
    """The detection pass. Returns findings, strongest reliance first:

    {kind: "bind_sibling", ambient binding info, reliance, device,
     candidates: [unbound direct sibling entity_ids]}   — the coffee-machine case
    {kind: "exclude_ambient", …}                        — appliance telemetry with
                                                          no usable sibling
    Never mutates anything."""
    bindings = [b for b in repo.bindings() if b.enabled]
    if not bindings or not importance:
        return []
    tiers = binding_tiers(bindings)
    reliance = reliance_by_binding(importance, bindings)
    by_name = {b.name: b for b in bindings}
    bound_eids = {b.entity_id for b in bindings}
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
        siblings = [e for e in dev_ents.get(did, []) if did
                    and e != b.entity_id and e not in bound_eids and _looks_direct(e)]
        appliancey = bool(_APPLIANCE.search(
            f"{b.entity_id} {b.name} {dev_label or ''}".lower()))
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
    return findings


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
