"""Onboarding suggestion service — heuristics always, LLM when configured.

The wizard's Sensors/Activities/Rules screens call ONE function each; whether
the proposals came from device-class heuristics or an LlmAdvisor is just a
badge on the row. The LLM path (ADR-12):

  inventory = entity metadata + aggregate stats (NEVER raw history)
  -> LlmAdvisor.propose_*  (structured output, pydantic-validated)
  -> merged with heuristic proposals (LLM wins ties, both shown)
  -> wizard screens, human approves/edits every row

Cost control: a per-call cost estimate is computed from the inventory size
and shown before any LLM call; calls are logged to the connections table.
"""
from __future__ import annotations

from ..ports import LlmAdvisor
from ..schemas import Activity, Binding, Rule


def heuristic_bindings(inventory: list[dict]) -> list[Binding]:
    """device_class/domain/unit/name-pattern rules -> proposed Bindings.
    (Port of prototype knowledge: 'vermogen'/W -> power, presence patterns,
    bed/load names -> bed, media_player domain -> media, etc.)"""
    raise NotImplementedError


async def suggest_setup(
    inventory: list[dict],
    advisor: LlmAdvisor | None,
) -> tuple[list[Binding], list[Activity], list[Rule]]:
    """The wizard's single entrypoint. advisor=None -> heuristics only."""
    raise NotImplementedError
