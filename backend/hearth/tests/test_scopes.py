"""Integration-token scope allow-list (docs/SECURITY.md)."""
from __future__ import annotations

from hearth.api.scopes import integration_allowed


def test_reads_allowed_via_get():
    for p in ("/api/predictions", "/api/persons", "/api/journey", "/api/controls"):
        assert integration_allowed(p, "GET") is True


def test_tap_forward_allowed_via_post():
    assert integration_allowed("/api/feedback/action", "POST") is True


def test_person_controls_allowed_read_and_write():
    for m in ("GET", "POST"):
        assert integration_allowed("/api/persons/alice/override", m) is True
        assert integration_allowed("/api/persons/bob/questions", m) is True


def test_writes_to_shared_paths_denied():
    # the vuln: a readable path must NOT be writable
    assert integration_allowed("/api/persons", "POST") is False
    assert integration_allowed("/api/feedback/action", "GET") is False


def test_other_paths_denied():
    for p in ("/api/models", "/api/bindings", "/api/persons/alice",
              "/api/persons/alice/avatar", "/api/feature-spec",
              "/api/persons/alice/override/extra"):
        assert integration_allowed(p, "GET") is False
        assert integration_allowed(p, "POST") is False
