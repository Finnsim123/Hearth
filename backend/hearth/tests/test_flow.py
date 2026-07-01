"""Flow map aggregator — nodes + edges with live values, degrades gracefully."""
from __future__ import annotations

from hearth.domain.flow import flow_state
from hearth.domain.schemas import Binding, ModelRecord, Person, Role


class _Repo:
    def __init__(self, bindings=None, settings=None, persons=None, models=None):
        self._b = bindings or []
        self._s = settings or {}
        self._p = persons or []
        self._m = models or []
    def bindings(self): return self._b
    def persons(self): return self._p
    def models(self, person=None): return self._m
    def clusters(self, status=None): return []
    def open_questions(self, person=None): return []
    def get_setting(self, key, default=None): return self._s.get(key, default)
    def get_connection(self, name): return None


def test_flow_shape_and_nodes():
    out = flow_state(_Repo(), None)
    assert set(out) >= {"phase", "nodes", "edges"}
    assert set(out["nodes"]) == {"ha", "raw", "features", "model", "predictions", "you", "discovery"}
    assert set(out["edges"]) >= {"ha_raw", "raw_features", "model_predictions", "you_model"}
    # every node has the fields the map needs
    for nd in out["nodes"].values():
        assert {"label", "value", "status", "href"} <= set(nd)


def test_sensor_count_flows_to_ha_node():
    b = [Binding(entity_id="binary_sensor.couch", role=Role.PRESENCE, name="couch")]
    out = flow_state(_Repo(bindings=b), None)
    assert "1 sensor" in out["nodes"]["ha"]["value"]
    # no tsdb → ingest idle, no dots on the hot edge
    assert out["edges"]["ha_raw"]["rate"] == 0


def test_models_array_is_per_person():
    persons = [Person(id="alice", name="Alice"), Person(id="bob", name="Bob"),
               Person(id="kid", name="Kid")]
    models = [
        ModelRecord(person_id="alice", version="alice-v7", node="root", feature_set="f",
                    promoted=True, metrics={"accuracy_confirmed": 0.9}),
        ModelRecord(person_id="alice", version="alice-home-v2", node="home", feature_set="f",
                    promoted=True, metrics={}),
        ModelRecord(person_id="bob", version="bob-v3", node="root", feature_set="f",
                    promoted=True, metrics={"accuracy_confirmed": 0.8}),
    ]
    out = flow_state(_Repo(persons=persons, models=models), None)
    ms = out["models"]
    assert [m["person_id"] for m in ms] == ["alice", "bob", "kid"]
    alice = ms[0]
    assert alice["version"] == "alice-v7" and alice["nodes"] == 2 and alice["accuracy"] == 0.9
    assert ms[2]["version"] is None and ms[2]["status"] == "work"   # kid untrained
    # aggregate node label + value reflect the multiple trained models
    assert out["nodes"]["model"]["label"] == "Models"
    assert "2 models" in out["nodes"]["model"]["value"]


def test_single_person_keeps_familiar_model_value():
    persons = [Person(id="alice", name="Alice")]
    models = [ModelRecord(person_id="alice", version="alice-v7", node="root",
                          feature_set="f", promoted=True, metrics={"accuracy_confirmed": 0.9})]
    out = flow_state(_Repo(persons=persons, models=models), None)
    assert out["nodes"]["model"]["label"] == "Model"
    assert out["nodes"]["model"]["value"] == "alice-v7 · 90%"
