"""API authorization scopes for bearer tokens (docs/SECURITY.md).

Kept in its own module (no heavy imports) so the allow-list is unit-testable
without standing up the whole app.
"""
from __future__ import annotations

# Static paths an `integration`-scope token may reach: read predictions/persons,
# forward notification taps, read the two-way controls' current state.
BEARER_INTEGRATION = {
    "/api/feedback/action", "/api/predictions", "/api/persons", "/api/journey",
    "/api/controls",
}


def integration_allowed(path: str) -> bool:
    """True if an integration-scope token may reach `path`. Covers the static
    list plus the per-person two-way controls (`/api/persons/<id>/override` and
    `/api/persons/<id>/questions`) the HA integration writes to."""
    if path in BEARER_INTEGRATION:
        return True
    parts = path.split("/")
    return (len(parts) == 5 and parts[1] == "api" and parts[2] == "persons"
            and parts[4] in ("override", "questions"))
