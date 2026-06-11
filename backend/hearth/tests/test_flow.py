"""Flow map aggregator — nodes + edges with live values, degrades gracefully."""
from __future__ import annotations

from hearth.domain.flow import flow_state
from hearth.domain.schemas import Binding, Role


class _Repo:
    def __init__(self, bindings=None, settings=None):
        self._b = bindings or []
        self._s = settings or {}
    def bindings(self): return self._b
    def persons(self): return []
    def models(self, person=None): return []
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
