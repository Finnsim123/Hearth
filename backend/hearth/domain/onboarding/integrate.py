"""Integrate approved sensors: scoped LLM re-analysis + background retrain.

The second half of the detect-then-ask flow (gap analysis E4/E5). After the user
approves staged sensors (they are already bound by inventory_sync), this:

  1. (optional) re-runs the feature architect over ONLY the new entities and
     merges the result into the active feature spec — so new sensors get proper
     features, not just a default recipe.
  2. retrains each enabled person in the background. The existing promotion gate
     then decides, automatically, whether the fresh model replaces the live one.

Progress is written to `discovery.integrate` so the buddy can narrate it. The
coordinator takes its heavy collaborators (advisor, events, tsdb, store) as
arguments, so it stays in the domain (no adapter imports) and is fully testable.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..schemas import FeatureSpec

log = logging.getLogger(__name__)


def _set_status(repo, stage: str, **extra) -> None:
    try:
        repo.set_setting("discovery.integrate",
                         {"stage": stage, "at": datetime.now(timezone.utc).isoformat(),
                          **extra})
    except Exception:
        log.debug("integrate status write failed", exc_info=True)


def merge_feature_spec(existing, new_spec: FeatureSpec) -> dict:
    """Merge a freshly-proposed spec into the stored one: selections unioned by
    entity_id and features by name, with the NEW entries winning. Returns a
    JSON-safe dict ready to store as the 'feature_spec' setting."""
    if isinstance(existing, dict) and existing.get("features") is not None:
        try:
            base = FeatureSpec.model_validate(existing)
        except Exception:
            base = FeatureSpec()
    else:
        base = FeatureSpec()
    sel = {s.entity_id: s for s in base.selections}
    for s in new_spec.selections:
        sel[s.entity_id] = s
    feat = {f.name: f for f in base.features}
    for f in new_spec.features:
        feat[f.name] = f
    merged = FeatureSpec(created_at=datetime.now(timezone.utc), created_by="llm+human",
                         llm_model=new_spec.llm_model or base.llm_model,
                         selections=list(sel.values()), features=list(feat.values()))
    return merged.model_dump(mode="json")


def _default_train(person_id, tsdb, repo, store):
    from ..training.trainer import train_person
    return train_person(person_id, tsdb, repo, store)


async def integrate(repo, *, approved_ids, advisor=None, events=None,
                    tsdb=None, store=None, train_fn=None) -> dict:
    """Coordinate scoped re-analysis (if an advisor + events are available) and a
    background retrain (if a store + tsdb are available). Each step degrades on
    failure; the promotion gate inside training decides replacement."""
    summary: dict = {"analyzed": False, "trained": []}

    if advisor is not None and events is not None and approved_ids:
        _set_status(repo, "analyzing", added=len(approved_ids))
        try:
            from ..features.transforms import active_mode
            from .inventory import build_catalog, stats_consent
            ids = set(approved_ids)
            inv = [e for e in await events.discover_entities()
                   if e.get("entity_id") in ids]
            catalog = build_catalog(inv, share_stats=stats_consent(repo))
            new_spec = await advisor.propose_feature_spec(
                catalog, repo.activities(), mode=active_mode(repo))
            if new_spec is not None and (new_spec.features or new_spec.selections):
                repo.set_setting("feature_spec",
                                 merge_feature_spec(repo.get_setting("feature_spec"), new_spec))
                summary["analyzed"] = True
        except Exception:
            log.exception("scoped re-analysis failed; keeping heuristic bindings")

    if tsdb is not None and store is not None:
        _set_status(repo, "retraining")
        trainer = train_fn or _default_train
        for person in repo.persons():
            if not getattr(person, "enabled", True):
                continue
            try:
                rec = await asyncio.to_thread(trainer, person.id, tsdb, repo, store)
                if rec is not None:
                    summary["trained"].append(getattr(rec, "version", None))
            except Exception:
                log.exception("integration retrain failed for %s", person.id)

    _set_status(repo, "done", **summary)
    return summary
