"""LlmAdvisor adapter — OpenRouter / any OpenAI-compatible chat endpoint.

Design constraints (ADR-12):
- structured output: response_format json_schema (or tool-call fallback),
  parsed straight into domain schemas; a malformed response = empty proposal,
  never a crash, never partial application
- prompt contains entity METADATA + aggregate stats only — raw history and
  household member names are never sent
- per-call token budget from connections.options.max_cost_per_call; every
  call's actual cost is logged for the Settings cost log
- model configurable (default: a cheap structured-output-capable model)
"""
from __future__ import annotations


class OpenRouterAdvisor:
    """Implements domain.ports.LlmAdvisor."""

    def __init__(self, repo) -> None:  # AppRepo (key + options from UI)
        raise NotImplementedError

    async def propose_bindings(self, inventory):
        raise NotImplementedError

    async def propose_taxonomy(self, inventory):
        raise NotImplementedError

    async def propose_rules(self, bindings, activities):
        raise NotImplementedError

    async def suggest_cluster_name(self, card, activities):
        raise NotImplementedError
