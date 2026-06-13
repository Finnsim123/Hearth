"""Approved-sensor integration: scoped re-analysis merge + background retrain
coordination (gap analysis E5). Pure coordinator, fakes for the heavy parts."""
from __future__ import annotations

import pytest

from hearth.domain.onboarding.integrate import integrate, merge_feature_spec
from hearth.domain.schemas import (
    Activity, EntitySelection, FeatureDef, FeatureSpec, InfoTier, Role,
)


def _spec(entity, name, tier=InfoTier.DISCRETE_EVENT_GATE, role=Role.PRESENCE):
    return FeatureSpec(
        selections=[EntitySelection(entity_id=entity, keep=True, role=role, info_tier=tier)],
        features=[FeatureDef(name=name, transform="occupancy_fraction",
                             inputs=[entity], info_tier=tier)])


def test_merge_feature_spec_unions_new_wins():
    existing = _spec("binary_sensor.sofa", "sofa_occ").model_dump(mode="json")
    new = _spec("sensor.co2", "co2_mean", tier=InfoTier.CONTINUOUS_MEASUREMENT, role=Role.ENV)
    merged = merge_feature_spec(existing, new)
    ent = {s["entity_id"] for s in merged["selections"]}
    names = {f["name"] for f in merged["features"]}
    assert ent == {"binary_sensor.sofa", "sensor.co2"}
    assert names == {"sofa_occ", "co2_mean"}
    assert merged["created_by"] == "llm+human"


def test_merge_into_empty():
    merged = merge_feature_spec(None, _spec("sensor.co2", "co2_mean"))
    assert [f["name"] for f in merged["features"]] == ["co2_mean"]


class FakeRepo:
    def __init__(self):
        self.settings = {}
        self._persons = [type("P", (), {"id": "alice", "enabled": True})()]
    def get_setting(self, k, d=None):
        return self.settings.get(k, d)
    def set_setting(self, k, v):
        self.settings[k] = v
    def get_connection(self, k):
        return None
    def activities(self):
        return [Activity(slug="movie", name="Movie")]
    def persons(self):
        return self._persons


class FakeEvents:
    async def discover_entities(self):
        return [{"entity_id": "sensor.co2", "domain": "sensor", "device_class": "carbon_dioxide"},
                {"entity_id": "sensor.other", "domain": "sensor"}]


class FakeAdvisor:
    def __init__(self):
        self.seen_ids = None
    async def propose_feature_spec(self, catalog, activities, mode="conservative"):
        self.seen_ids = [c["entity_id"] for c in catalog]
        return _spec("sensor.co2", "co2_mean",
                     tier=InfoTier.CONTINUOUS_MEASUREMENT, role=Role.ENV)


@pytest.mark.asyncio
async def test_integrate_reanalyzes_only_new_and_retrains():
    repo = FakeRepo()
    advisor = FakeAdvisor()
    trained = []

    def fake_train(pid, tsdb, repo_, store):
        trained.append(pid)
        return type("R", (), {"version": f"{pid}-v9"})()

    summary = await integrate(
        repo, approved_ids=["sensor.co2"], advisor=advisor, events=FakeEvents(),
        tsdb=object(), store=object(), train_fn=fake_train)

    # re-analysis saw ONLY the approved entity, merged it into the spec
    assert advisor.seen_ids == ["sensor.co2"]
    assert summary["analyzed"] is True
    assert "sensor.co2" in {s["entity_id"] for s in repo.settings["feature_spec"]["selections"]}
    # retrained the enabled person, status ends "done"
    assert trained == ["alice"] and summary["trained"] == ["alice-v9"]
    assert repo.settings["discovery.integrate"]["stage"] == "done"


@pytest.mark.asyncio
async def test_integrate_without_advisor_just_retrains():
    repo = FakeRepo()
    trained = []
    summary = await integrate(
        repo, approved_ids=["sensor.co2"], advisor=None, events=None,
        tsdb=object(), store=object(),
        train_fn=lambda pid, *a: trained.append(pid) or type("R", (), {"version": "v"})())
    assert summary["analyzed"] is False and trained == ["alice"]
    assert "feature_spec" not in repo.settings        # no spec change without an advisor


@pytest.mark.asyncio
async def test_integrate_survives_train_failure():
    repo = FakeRepo()

    def boom(pid, *a):
        raise RuntimeError("train down")

    summary = await integrate(repo, approved_ids=[], advisor=None, events=None,
                              tsdb=object(), store=object(), train_fn=boom)
    assert summary["trained"] == []                    # failure swallowed
    assert repo.settings["discovery.integrate"]["stage"] == "done"
