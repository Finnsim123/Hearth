"""Integration-token scope allow-list (docs/SECURITY.md)."""
from __future__ import annotations

from hearth.api.scopes import integration_allowed


def test_static_paths_allowed():
    for p in ("/api/predictions", "/api/persons", "/api/journey",
              "/api/feedback/action", "/api/controls"):
        assert integration_allowed(p) is True


def test_person_controls_allowed():
    assert integration_allowed("/api/persons/alice/override") is True
    assert integration_allowed("/api/persons/bob/questions") is True


def test_other_paths_denied():
    for p in ("/api/models", "/api/bindings", "/api/persons/alice",
              "/api/persons/alice/avatar", "/api/feature-spec",
              "/api/persons/alice/override/extra"):
        assert integration_allowed(p) is False
