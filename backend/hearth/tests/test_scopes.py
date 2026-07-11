"""Bearer-token scope allow-lists (docs/SECURITY.md).

Two scopes: `readonly` = the GET surface only; `integration` = reads + the
narrow write set (tap-forward, per-person controls, assistant actions). The
invariant that matters: NO bearer token can ever write shared config or reach
destructive/admin endpoints.
"""
from __future__ import annotations

from hearth.api.scopes import integration_allowed, readonly_allowed


def test_reads_allowed_via_get_for_both_scopes():
    for p in ("/api/predictions", "/api/persons", "/api/journey", "/api/controls",
              "/api/models", "/api/models/cadence", "/api/capability",
              "/api/behaviour", "/api/bindings/health", "/api/bindings/leadlag",
              "/api/clusters", "/api/inbox", "/api/advisories",
              "/api/audit/bindings"):
        assert integration_allowed(p, "GET") is True
        assert readonly_allowed(p, "GET") is True


def test_tap_forward_allowed_via_post():
    assert integration_allowed("/api/feedback/action", "POST") is True


def test_assistant_actions_integration_only():
    for p in ("/api/inbox/48/answer", "/api/clusters/12/name", "/api/models/train"):
        assert integration_allowed(p, "POST") is True
        assert readonly_allowed(p, "POST") is False


def test_person_controls_allowed_read_and_write():
    for m in ("GET", "POST"):
        assert integration_allowed("/api/persons/alice/override", m) is True
        assert integration_allowed("/api/persons/bob/questions", m) is True
    # readonly may look at the controls but never flip them
    assert readonly_allowed("/api/persons/alice/override", "GET") is True
    assert readonly_allowed("/api/persons/alice/override", "POST") is False


def test_writes_to_shared_paths_denied():
    # the vuln: a readable path must NOT be writable
    assert integration_allowed("/api/persons", "POST") is False
    assert integration_allowed("/api/models", "POST") is False
    assert integration_allowed("/api/feedback/action", "GET") is False


def test_dangerous_paths_denied_for_both_scopes():
    for p, m in (("/api/persons/alice/forget", "POST"),
                 ("/api/persons/alice/relink", "POST"),
                 ("/api/bindings", "POST"),
                 ("/api/settings", "POST"),
                 ("/api/models/1/promote", "POST"),
                 ("/api/models/rollback", "POST"),
                 ("/api/audit/bindings/apply", "POST"),
                 ("/api/clusters/12/dismiss", "POST"),
                 ("/api/inbox/48/skip", "POST"),
                 ("/api/tokens", "GET"),
                 ("/api/auth/me", "GET"),
                 ("/api/system/update", "POST"),
                 ("/api/persons/alice", "GET"),
                 ("/api/persons/alice/avatar", "POST"),
                 ("/api/feature-spec", "GET"),
                 ("/api/persons/alice/override/extra", "POST"),
                 ("/api/inbox/48x/answer", "POST"),
                 ("/api/clusters//name", "POST")):
        assert integration_allowed(p, m) is False, (p, m)
        assert readonly_allowed(p, m) is False, (p, m)
