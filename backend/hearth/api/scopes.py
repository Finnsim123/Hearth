"""API authorization scopes for bearer tokens (docs/SECURITY.md).

Kept in its own module (no heavy imports) so the allow-list is unit-testable
without standing up the whole app.
"""
from __future__ import annotations

# Read-only paths an `integration`-scope token may GET (predictions, household
# roster, journey, current control state). NB: method matters — `/api/persons`
# is readable but `POST /api/persons` (save_person) must NOT be reachable.
BEARER_READ = {
    "/api/predictions", "/api/persons", "/api/journey", "/api/controls",
}
# Paths the token may POST to: the notification tap-forward only.
BEARER_WRITE = {"/api/feedback/action"}


def integration_allowed(path: str, method: str = "GET") -> bool:
    """True if an integration-scope token may reach (path, method). The scope is
    read-only + tap-forward + the per-person two-way controls
    (`/api/persons/<id>/override|questions`) the HA integration reads and writes;
    everything else (incl. writes to shared paths like POST /api/persons) is denied."""
    method = method.upper()
    if method == "GET" and path in BEARER_READ:
        return True
    if method == "POST" and path in BEARER_WRITE:
        return True
    parts = path.split("/")
    is_control = (len(parts) == 5 and parts[1] == "api" and parts[2] == "persons"
                  and parts[4] in ("override", "questions"))
    return is_control and method in ("GET", "POST")
