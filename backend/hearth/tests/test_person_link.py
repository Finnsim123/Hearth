"""Linking members to their home/away entity — fallback + ensure logic.

The LLM does the fuzzy 'Alex' ↔ person.alexander_jansen match (not tested here, it's
network); this covers the deterministic name fallback + that ensure links every
member, LLM match winning over fallback.
"""
from __future__ import annotations

from hearth.domain.onboarding.person_link import ensure_member_persons, fallback_match
from hearth.domain.schemas import Person, Role

INV = [
    {"entity_id": "person.alexander_jansen", "friendly_name": "Alex", "disabled": False},
    {"entity_id": "person.nora", "friendly_name": "Nora", "disabled": False},
    {"entity_id": "device_tracker.alex_phone", "friendly_name": "Alex phone", "disabled": False},
]


class _Repo:
    def __init__(self, persons, bindings=None):
        self._p, self._b = persons, bindings or []
    def persons(self): return self._p
    def bindings(self): return self._b
    def save_binding(self, b):
        for i, x in enumerate(self._b):
            if x.entity_id == b.entity_id:
                self._b[i] = b
                return b
        self._b.append(b)
        return b


def test_fallback_matches_via_friendly_name_prefers_person():
    members = [Person(id="alex", name="Alex"), Person(id="nora", name="Nora")]
    out = fallback_match(members, INV)
    assert out["nora"] == "person.nora"
    # 'Alex' matches the person.* via its friendly_name, and person.* is preferred
    # over the device_tracker even though that also matches on the 'alex' token
    assert out["alex"] == "person.alexander_jansen"


def test_ensure_links_all_llm_wins():
    repo = _Repo([Person(id="alex", name="Alex"), Person(id="nora", name="Nora")])
    # LLM points Alex at the tracker, overriding what the fallback would pick
    n = ensure_member_persons(repo, INV, {"alex": "device_tracker.alex_phone"})
    assert n == 2
    links = {b.person_id: b.entity_id for b in repo.bindings() if b.role == Role.PERSON}
    assert links["alex"] == "device_tracker.alex_phone"   # LLM match wins
    assert links["nora"] == "person.nora"             # fallback used


def test_ensure_skips_already_linked():
    from hearth.domain.schemas import Binding
    pre = [Binding(entity_id="person.nora", role=Role.PERSON, name="nora_loc", person_id="nora")]
    repo = _Repo([Person(id="nora", name="Nora")], bindings=pre)
    assert ensure_member_persons(repo, INV, {}) == 0
