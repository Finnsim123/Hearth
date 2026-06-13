"""Entity triage — the coarse first pass of the two-stage funnel.

A home can have 1700+ entities; sending all of them WITH metadata + stats to the
LLM is wasteful and noisy. So first we cluster the full list from ids + friendly
names alone (cheap) and let the model decide which clusters matter for activity
prediction. The relevant clusters become the SHORTLIST that the expensive
metadata pass (propose_bindings / feature spec) then analyses.

LLM-driven, not rule-driven — but with a thin safety floor: a genuine home/away
tracker (the single biggest early-accuracy lever, and few in number) is never
dropped even if the model missed it. Without an LLM key we fall back to
clustering by heuristic role, which reproduces the old candidate set.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .advisor import is_person_tracker, suggest_role

log = logging.getLogger(__name__)


def _heuristic_clusters(usable: list[dict]) -> list[dict]:
    """No-LLM fallback: group by heuristic role; entities with a role are a
    relevant cluster, the rest land in a single 'ignored' cluster."""
    groups: dict[str, dict] = {}
    for e in usable:
        role = suggest_role(e)
        label = role.value if role else "ignored"
        g = groups.setdefault(label, {"label": label, "relevant": role is not None,
                                       "why": "", "entities": []})
        g["entities"].append(e["entity_id"])
    return list(groups.values())


def keepset_from(triage: dict, excluded_labels: set[str]) -> list[str]:
    """Recompute the kept entity set from a stored triage when the user toggles
    whole clusters off (the Sensors / Welcome review). Relevant, non-excluded
    clusters are kept; the presence safety floor (real home/away trackers) is
    always re-added regardless of the toggles."""
    keep: set[str] = set()
    everything: set[str] = set()
    for c in triage.get("clusters", []):
        ents = c.get("entities", [])
        everything.update(ents)
        if c.get("relevant") and c["label"] not in excluded_labels:
            keep.update(ents)
    keep |= {e for e in everything if is_person_tracker(e)}
    return sorted(keep)


async def triage_entities(repo, inventory: list[dict], advisor=None) -> dict:
    """Cluster the inventory and derive the relevant shortlist. Stores the result
    in setting `entity_triage` (clusters for the bubble cloud + the kept ids) and
    returns it. Pure-ish: one LLM call (or none) + one setting write."""
    usable = [e for e in inventory if not e.get("disabled")]
    valid = {e["entity_id"] for e in usable}

    clusters: list[dict] = []
    by = "heuristic"
    if advisor is not None:
        try:
            clusters = await advisor.cluster_entities(usable)
            by = "llm" if clusters else "heuristic"
        except Exception:
            log.exception("entity clustering failed — heuristic triage")
    if not clusters:
        clusters = _heuristic_clusters(usable)
        by = "heuristic"

    keep: set[str] = set()
    for c in clusters:
        if c.get("relevant"):
            keep.update(e for e in c.get("entities", []) if e in valid)
    # safety floor: never drop a real home/away tracker — losing presence is
    # catastrophic and these are few. Everything else is the model's call.
    floor = {e["entity_id"] for e in usable
             if is_person_tracker(e["entity_id"], e.get("friendly_name") or "")}
    keep |= floor

    result = {
        "by": by,
        "total": len(usable),
        "kept_count": len(keep),
        "kept": sorted(keep),
        "clusters": [
            {"label": c["label"], "relevant": bool(c.get("relevant")),
             "why": c.get("why", ""), "count": len(c.get("entities", [])),
             "kept": sum(1 for e in c.get("entities", []) if e in keep),
             # membership kept so the Sensors page can toggle a whole cluster
             # on/off and recompute the keep-set (not sent to the bubble cloud).
             "entities": sorted(c.get("entities", []))}
            for c in sorted(clusters, key=lambda c: -len(c.get("entities", [])))
        ],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    repo.set_setting("entity_triage", result)
    log.info("entity triage (%s): kept %d of %d entities across %d clusters",
             by, len(keep), len(usable), len(clusters))
    return result
