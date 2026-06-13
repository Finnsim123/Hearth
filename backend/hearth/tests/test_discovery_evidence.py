"""Evidence card: feature columns reverse to plain English; when/where/cadence
/adjacency/contrast assemble from a synthetic cluster."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hearth.domain.discovery.evidence import build_evidence
from hearth.domain.discovery.lexicon import (
    humanize_feature, prettify, _match_binding)
from hearth.domain.schemas import Activity, Binding, ClusterCard, Person, Role


def _bindings():
    return [
        # a binding name that itself contains underscores (the hard case)
        Binding(entity_id="binary_sensor.bed_left", role=Role.BED,
                name="bed_sensor_bed_left_sensor", room="Bedroom", person_id="alex"),
        Binding(entity_id="sensor.bedroom_temp", role=Role.ENV,
                name="bedroom_temperature_temperatuur", room="Bedroom"),
        Binding(entity_id="sensor.nora_wekker", role=Role.ALARM_TIME,
                name="nora_wekker", person_id="nora"),
        Binding(entity_id="binary_sensor.sofa", role=Role.PRESENCE,
                name="sofa", room="Living room"),
    ]


PERSONS = {"alex": "Alex", "nora": "Nora"}


def test_longest_prefix_match_handles_underscore_names():
    b, suffix = _match_binding("bed_sensor_bed_left_sensor_occupied", _bindings())
    assert b is not None and b.role == Role.BED and suffix == "occupied"


def test_bed_direction_reads_naturally():
    down = humanize_feature("bed_sensor_bed_left_sensor_occupied", -2.1, _bindings(), PERSONS)
    up = humanize_feature("bed_sensor_bed_left_sensor_occupied", 2.1, _bindings(), PERSONS)
    assert down["label"] == "Bed empty" and down["dir"] == "down"
    assert up["label"] == "In bed"
    assert down["room"] == "Bedroom"


def test_env_metric_and_alarm_and_person():
    warm = humanize_feature("bedroom_temperature_temperatuur_mean", 1.8, _bindings(), PERSONS)
    assert "temperature higher" in warm["label"].lower()
    alarm = humanize_feature("nora_wekker_minutes_until", -1.5, _bindings(), PERSONS)
    assert "soon" in alarm["label"].lower()


def test_lag_and_unmapped_fallback():
    lag = humanize_feature("sofa_frac_lag1", 2.0, _bindings(), PERSONS)
    assert "just before" in lag["label"]
    # spec/composite feature with no binding -> prettified, no room
    spec = humanize_feature("alex_sleep_7d_avg_occupied", -1.0, _bindings(), PERSONS)
    assert spec["room"] is None
    assert "7-day" in prettify("alex_sleep_7d_avg_occupied")


class FakeTsdb:
    def __init__(self, preds): self._p = preds
    def read_predictions(self, person, start, end): return self._p


class FakeRepo:
    def __init__(self):
        self.settings = {"timezone": "Europe/Amsterdam"}
        self._clusters: list[ClusterCard] = []
    def bindings(self): return _bindings()
    def persons(self): return [Person(id="alex", name="Alex"), Person(id="nora", name="Nora")]
    def activities(self):
        return [Activity(slug="lunch", name="Lunch"), Activity(slug="cooking", name="Cooking")]
    def clusters(self, status=None, person_id=None): return self._clusters
    def get_setting(self, k, d=None): return self.settings.get(k, d)


def _card():
    base = datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc)  # a Tuesday afternoon
    hist = [0] * 24
    for h in (13, 14, 15, 16):
        hist[h] = 10
    return ClusterCard(
        id=1, person_id="alex", n_windows=40,
        signature=[("bed_sensor_bed_left_sensor_occupied", -2.4),
                   ("bedroom_temperature_temperatuur_mean", 1.6)],
        hour_histogram=hist,
        example_windows=[base + timedelta(days=d, minutes=0) for d in range(0, 8)])


def test_build_evidence_when_where_cadence_adjacency():
    repo = FakeRepo()
    base = datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc)
    preds = []
    for d in range(8):                       # lunch before / cooking after each window
        w = base + timedelta(days=d)
        preds.append({"time": (w - timedelta(minutes=10)).isoformat(), "smoothed": "lunch"})
        preds.append({"time": (w + timedelta(minutes=40)).isoformat(), "smoothed": "cooking"})
    ev = build_evidence(_card(), repo, FakeTsdb(preds))
    assert ev["when"]["daypart"] == "afternoon"
    assert ev["where"][0] == "Bedroom"
    assert ev["plain"][0]["label"] == "Bed empty"
    assert ev["cadence"]["weekday_frac"] >= 0.5
    assert ev["adjacency"]["before"] == "Lunch"
    assert ev["adjacency"]["after"] == "Cooking"
    assert "afternoon" in ev["summary"].lower()


def test_contrast_points_at_named_sibling():
    repo = FakeRepo()
    repo._clusters = [ClusterCard(
        id=2, person_id="alex", status="named", named_activity_slug="cooking",
        signature=[("bed_sensor_bed_left_sensor_occupied", -2.0),
                   ("bedroom_temperature_temperatuur_mean", 1.2)])]
    ev = build_evidence(_card(), repo, FakeTsdb([]))
    assert ev["contrast"]["name"] == "Cooking" and ev["contrast"]["shared"] >= 2
