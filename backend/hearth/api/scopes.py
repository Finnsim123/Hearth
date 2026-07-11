"""API authorization scopes for bearer tokens (docs/SECURITY.md).

Kept in its own module (no heavy imports) so the allow-list is unit-testable
without standing up the whole app.

Two token scopes:
  readonly     may GET the read surface below — nothing else. For dashboards,
               wall panels, and the MCP server's read tools.
  integration  everything readonly can, plus the narrow write set: the HA
               notification tap-forward, the per-person two-way controls, and
               the assistant actions (answer a question, name a pattern,
               trigger a train). Writes to shared config (POST /api/persons,
               bindings, settings, …) are NEVER bearer-reachable.
"""
from __future__ import annotations

import re

# Read-only paths a bearer token may GET. NB: method matters — `/api/persons`
# is readable but `POST /api/persons` (save_person) must NOT be reachable.
BEARER_READ = {
    "/api/predictions", "/api/persons", "/api/journey", "/api/controls",
    # the assistant/MCP read surface: model health, honest capability,
    # behaviour insights, sensors, discovery, inbox, advisories
    "/api/models", "/api/models/cadence", "/api/models/gate",
    "/api/capability", "/api/behaviour",
    "/api/bindings/health", "/api/bindings/leadlag",
    "/api/clusters", "/api/inbox", "/api/advisories", "/api/audit/bindings",
}
# Exact paths an integration token may POST to.
BEARER_WRITE = {"/api/feedback/action", "/api/models/train"}
# POST patterns for the assistant actions — the same things a household member
# does from their phone (answer, name a pattern), never destructive ops.
_BEARER_WRITE_RX = [
    re.compile(r"^/api/inbox/\d+/answer$"),
    re.compile(r"^/api/clusters/\d+/name$"),
]


def _is_control(path: str) -> bool:
    parts = path.split("/")
    return (len(parts) == 5 and parts[1] == "api" and parts[2] == "persons"
            and parts[4] in ("override", "questions"))


def readonly_allowed(path: str, method: str = "GET") -> bool:
    """A `readonly` token: the GET surface only."""
    return method.upper() == "GET" and (path in BEARER_READ or _is_control(path))


def integration_allowed(path: str, method: str = "GET") -> bool:
    """An `integration` token: the read surface + the narrow write set — the
    tap-forward, per-person two-way controls, and the assistant actions.
    Everything else (incl. writes to shared paths like POST /api/persons)
    is denied."""
    method = method.upper()
    if readonly_allowed(path, method):
        return True
    if method == "POST":
        if path in BEARER_WRITE or _is_control(path):
            return True
        return any(rx.match(path) for rx in _BEARER_WRITE_RX)
    return False
