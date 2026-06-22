from __future__ import annotations

import pandas as pd

from hearth.domain.foundational.facts import (
    FoundationalFact,
    _awake_evidence,
    candidate_bindings,
    compute_verdict,
    contradiction_series,
    extra_gate_slugs,
    fact_series,
    load_facts,
    run_verdicts,
    save_facts,
)
from hearth.domain.schemas import Binding, Role


def _bucket(h):
    return 0 if (h < 5 or h >= 22) else (1 if h < 11 else (2 if h < 17 else 3))


def _sleep_feats(days=7, good=True):
    idx = pd.date_range("2026-06-01 00:00", periods=days * 48, freq="30min", tz="UTC")
    if good:
        occ = (((idx.hour >= 23) | (idx.hour < 5))).astype(float)   # all night-bucket
    else:
        occ = pd.Series(([1.0, 0.0] * (len(idx) // 2 + 1))[: len(idx)], index=idx).to_numpy()
    df = pd.DataFrame({"bed_occupied": occ}, index=idx)
    df["time_bucket"] = [float(_bucket(h)) for h in idx.hour]
    if not good:                                  # flaky one is 'asleep' during the day too
        df["lamp_on_frac"] = (df["time_bucket"] != 0).astype(float)
    return df


class FakeRepo:
    def __init__(self, bindings=None):
        self._s = {}
        self._b = bindings or []
    def get_setting(self, k, d=None): return self._s.get(k, d)
    def set_setting(self, k, v): self._s[k] = v
    def bindings(self): return self._b


class FakeTSDB:
    def __init__(self, feats): self.feats = feats
    def read_features(self, *a, **k): return self.feats


AWAY_FACT = FoundationalFact(id="alice:away", gate="away", binding_name="alice_loc",
                             role=Role.PERSON, person_id="alice")
SLEEP_FACT = FoundationalFact(id="alice:asleep", gate="asleep", binding_name="bed",
                              role=Role.BED, person_id="alice")


def test_fact_series_away_and_asleep():
    idx = pd.date_range("2026-06-01", periods=3, freq="30min", tz="UTC")
    feats = pd.DataFrame({"alice_loc_home_last": [1.0, 0.0, -1.0],
                          "bed_occupied": [0.0, 1.0, -1.0]}, index=idx)
    assert list(fact_series(feats, AWAY_FACT)) == [False, True, False]   # 0=away, -1=unknown
    assert list(fact_series(feats, SLEEP_FACT)) == [False, True, False]


def test_contradiction_only_for_sleep():
    feats = _sleep_feats()
    assert contradiction_series(feats, AWAY_FACT) is None
    assert contradiction_series(feats, SLEEP_FACT) is not None


def test_good_sleep_sensor_earns_fact():
    v = compute_verdict(_sleep_feats(good=True), SLEEP_FACT)
    assert v.role_decision == "fact"


def test_flaky_sleep_sensor_is_not_fact():
    v = compute_verdict(_sleep_feats(good=False), SLEEP_FACT)
    assert v.role_decision != "fact"


def test_save_load_roundtrip_and_run_verdicts():
    repo = FakeRepo()
    save_facts(repo, [SLEEP_FACT])
    assert [f.id for f in load_facts(repo)] == ["alice:asleep"]
    verdicts = run_verdicts(FakeTSDB(_sleep_feats(good=True)), repo)
    assert verdicts["alice:asleep"]["role_decision"] == "fact"
    # earned → it becomes an extra gate in the predictor
    extras = extra_gate_slugs(repo, "alice")
    assert [f.gate for f in extras] == ["asleep"]


def test_step_movement_contradicts_sleep():
    # all night-bucket windows; a burst of steps in one window = clearly awake
    idx = pd.date_range("2026-06-01 00:00", periods=6, freq="30min", tz="UTC")
    feats = pd.DataFrame({"time_bucket": [0.0] * 6,
                          "watch_steps_delta": [0, 0, 400, 0, 0, 0]}, index=idx)
    assert list(_awake_evidence(feats)) == [False, False, True, False, False, False]


def test_charging_and_still_cancels_daytime_awake():
    idx = pd.date_range("2026-06-01 13:00", periods=3, freq="30min", tz="UTC")  # daytime
    base = pd.DataFrame({"time_bucket": [2.0, 2.0, 2.0]}, index=idx)
    assert list(_awake_evidence(base)) == [True, True, True]      # daytime ⇒ awake
    charged = base.copy()
    charged["phone_charging_max"] = [1.0, 1.0, 0.0]               # docked, then unplugged
    charged["watch_steps_delta"] = [0.0, 0.0, 0.0]               # not moving
    # charging+still suppresses the soft daytime prior; last window is unplugged
    assert list(_awake_evidence(charged)) == [False, False, True]


def test_charging_does_not_excuse_real_activity():
    # phone charging but lights on + walking → still awake (hard evidence wins)
    idx = pd.date_range("2026-06-01 14:00", periods=2, freq="30min", tz="UTC")
    feats = pd.DataFrame({"time_bucket": [2.0, 2.0],
                          "phone_charging_max": [1.0, 1.0],
                          "lamp_on_frac": [0.9, 0.0],
                          "watch_steps_delta": [0.0, 300.0]}, index=idx)
    assert list(_awake_evidence(feats)) == [True, True]


def test_candidate_bindings_by_role():
    repo = FakeRepo(bindings=[
        Binding(entity_id="sensor.bed", role=Role.BED, name="bed", person_id="alice"),
        Binding(entity_id="person.alice", role=Role.PERSON, name="alice_loc", person_id="alice"),
    ])
    assert [c["binding_name"] for c in candidate_bindings(repo, "asleep")] == ["bed"]
    assert [c["binding_name"] for c in candidate_bindings(repo, "away")] == ["alice_loc"]
