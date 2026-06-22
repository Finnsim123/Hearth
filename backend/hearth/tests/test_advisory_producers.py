from __future__ import annotations

import pandas as pd

from hearth.domain import advisories as A
from hearth.domain import events as E
from hearth.domain.advisory_scan import refresh_system_advisories
from hearth.domain.foundational.facts import FoundationalFact, run_verdicts, save_facts
from hearth.domain.schemas import Role


class FakeRepo:
    def __init__(self, persons=None, bindings=None, models=None):
        self._s = {}; self._p = persons or []; self._b = bindings or []
        self._m = models or {}
    def get_setting(self, k, d=None): return self._s.get(k, d)
    def set_setting(self, k, v): self._s[k] = v
    def persons(self): return self._p
    def bindings(self): return self._b
    def models(self, person_id=None): return self._m.get(person_id, [])


class FakeTSDB:
    def __init__(self, feats): self.feats = feats
    def read_features(self, *a, **k): return self.feats


def _flaky_bed_feats(days=7):
    idx = pd.date_range("2026-06-01 00:00", periods=days * 48, freq="30min", tz="UTC")
    occ = pd.Series(([1.0, 0.0] * (len(idx) // 2 + 1))[: len(idx)], index=idx).to_numpy()
    df = pd.DataFrame({"bed_occupied": occ}, index=idx)
    df["lamp_on_frac"] = 1.0          # "asleep" while a light is on → contradiction
    return df


def test_foundational_demotion_records_advisory_and_event():
    repo = FakeRepo()
    fact = FoundationalFact(id="alice:asleep", gate="asleep", binding_name="bed",
                            role=Role.BED, person_id="alice")
    save_facts(repo, [fact])
    # seed a prior verdict that said 'fact', so the new (poor) score is a DEMOTION
    repo.set_setting("foundational.verdicts", {"alice:asleep": {"role_decision": "fact"}})
    run_verdicts(FakeTSDB(_flaky_bed_feats()), repo)
    adv = A.worst_advisory(repo)
    assert adv is not None and adv["kind"] == "foundational:alice:asleep"
    assert any(e["kind"] == "sensor_demoted" for e in E.list_events(repo))


def test_coverage_blindspot_advisory_from_confusion():
    confusion = {"labels": ["cooking", "eating"],
                 "matrix": [[10, 8], [7, 9]]}        # heavy cooking/eating confusion

    class M:
        promoted = True; node = "root"; metrics = {"confusion": confusion}

    class P:
        id = "alice"; name = "Alice"
    repo = FakeRepo(persons=[P()], bindings=[], models={"alice": [M()]})
    refresh_system_advisories(repo)
    kinds = [a["kind"] for a in A.active_advisories(repo)]
    assert "coverage:blindspot" in kinds
    assert any(e["kind"] == "blindspot" for e in E.list_events(repo))


def test_model_health_low_accuracy_advisory():
    class M:
        promoted = True; node = "root"; version = "v1"
        metrics = {"n_gold": 50, "accuracy_gold": 0.41}

    class P:
        id = "bob"; name = "Bob"
    repo = FakeRepo(persons=[P()], models={"bob": [M()]})
    refresh_system_advisories(repo)
    adv = {a["kind"]: a for a in A.active_advisories(repo)}
    assert "model:bob" in adv and adv["model:bob"]["severity"] == "warn"
