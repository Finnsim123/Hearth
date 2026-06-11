"""Methodology injection — assembles live numbers, degrades gracefully."""
from __future__ import annotations

from hearth.domain.methodology import build_methodology
from hearth.domain.schemas import Binding, Role


class _Repo:
    def __init__(self, bindings=None, settings=None):
        self._b = bindings or []
        self._s = settings or {}
    def bindings(self): return self._b
    def persons(self): return []
    def activities(self): return []
    def rules(self): return []
    def models(self): return []
    def clusters(self, status=None): return []
    def get_setting(self, key, default=None): return self._s.get(key, default)
    def get_connection(self, name): return None


def test_empty_instance_renders_without_crashing():
    out = build_methodology(_Repo(), None)
    # core fields always present, missing data is None not an exception
    assert "generated_at" in out
    assert out["sensor_count"] == 0
    assert out["model_version"] is None
    assert out["recency_half_life"] > 0          # constant always available
    assert isinstance(out["role_windows"], dict) and out["role_windows"]


def test_role_and_tier_breakdown_from_bindings():
    bindings = [
        Binding(entity_id="binary_sensor.couch", role=Role.PRESENCE, name="couch", room="Living Room"),
        Binding(entity_id="sensor.co2", role=Role.ENV, name="co2", room="Living Room"),
        Binding(entity_id="binary_sensor.bed", role=Role.BED, name="bed", room="Bedroom"),
    ]
    out = build_methodology(_Repo(bindings), None)
    assert out["sensor_count"] == 3
    assert out["role_breakdown"]["presence"] == 1
    assert out["room_count"] == 2
    assert sum(out["tier_breakdown"].values()) == 3
