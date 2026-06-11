"""Room canonicalisation — folding the case/separator variants a rescan spawns."""
from __future__ import annotations

from hearth.domain.onboarding.rooms import (
    apply_room_mapping, canonical_room, room_key, tidy_rooms_deterministic)
from hearth.domain.schemas import Binding, Role


def test_room_key_and_canonical():
    assert room_key("Living_room") == room_key("livingroom") == room_key("Living Room")
    assert canonical_room("living_room") == "Living Room"
    assert canonical_room("kitchen") == "Kitchen"
    assert canonical_room(None) is None


class _Repo:
    def __init__(self, bindings):
        self._b = bindings
    def bindings(self):
        return self._b
    def save_binding(self, b):
        pass  # bindings are mutated in place


def _b(name, room):
    return Binding(entity_id=f"sensor.{name}", role=Role.ENV, name=name, room=room)


def test_tidy_merges_case_and_separator_variants():
    bindings = [_b("a", "Living_room"), _b("b", "livingroom"),
                _b("c", "Kitchen"), _b("d", "kitchen")]
    repo = _Repo(bindings)
    out = tidy_rooms_deterministic(repo)
    rooms = {b.room for b in bindings}
    assert rooms == {"Living Room", "Kitchen"}      # four variants → two rooms
    assert out["changed"] >= 2
    assert set(out["rooms"]) == {"Living Room", "Kitchen"}


def test_apply_room_mapping_semantic():
    bindings = [_b("a", "Sleepingroom"), _b("b", "Bedroom")]
    repo = _Repo(bindings)
    changed = apply_room_mapping(repo, {"Sleepingroom": "Bedroom"})
    assert changed == 1
    assert {b.room for b in bindings} == {"Bedroom"}
