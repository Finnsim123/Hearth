"""MQTT output channel (ADR-5): discovery + state payloads, and publish wiring
against a fake broker client (no real broker needed)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from hearth.adapters.mqtt_publisher import (
    AVAILABILITY_TOPIC, MqttPublisher, activity_state_topic, confidence_state_topic,
    discovery_configs, override_state_topic, questions_state_topic, state_messages,
)
from hearth.domain.schemas import Person, Prediction


def _persons():
    return [Person(id="alice", name="Alice"), Person(id="bob", name="Bob")]


def test_discovery_configs():
    activities = [type("A", (), {"slug": "movie"})(), type("A", (), {"slug": "home"})()]
    cfgs = dict(discovery_configs(_persons(), activities))
    # one availability binary_sensor + activity & confidence per person
    assert "homeassistant/binary_sensor/hearth/alive/config" in cfgs
    assert "homeassistant/sensor/hearth_alice/activity/config" in cfgs
    assert "homeassistant/sensor/hearth_bob/confidence/config" in cfgs
    act = cfgs["homeassistant/sensor/hearth_alice/activity/config"]
    assert act["state_topic"] == activity_state_topic("alice")
    assert act["value_template"] == "{{ value_json.state }}"
    assert act["availability_topic"] == AVAILABILITY_TOPIC
    assert act["unique_id"] == "hearth_alice_activity"
    conf = cfgs["homeassistant/sensor/hearth_alice/confidence/config"]
    assert conf["unit_of_measurement"] == "%"
    # two-way controls: a questions switch and an override select with activity options
    sw = cfgs["homeassistant/switch/hearth_alice/questions/config"]
    assert sw["command_topic"] == "hearth/alice/questions/set" and sw["payload_off"] == "OFF"
    sel = cfgs["homeassistant/select/hearth_alice/override/config"]
    assert sel["command_topic"] == "hearth/alice/override/set"
    assert sel["options"] == ["auto", "movie", "home"]


def test_state_messages():
    pred = Prediction(person_id="alice", window_ts=datetime(2026, 6, 1, 21, 0, tzinfo=timezone.utc),
                      model_version="alice-v3", predicted="movie", smoothed="movie",
                      confidence=0.83, probabilities={"movie": 0.83, "home": 0.17},
                      parent="home")
    msgs = dict(state_messages(pred))
    act = json.loads(msgs[activity_state_topic("alice")])
    assert act["state"] == "movie" and act["raw"] == "movie"
    assert act["confidence"] == 0.83 and act["state_level"] == "home"
    assert act["model_version"] == "alice-v3"
    assert msgs[confidence_state_topic("alice")] == "83"


def test_abstain_unknown_publishes_state():
    pred = Prediction(person_id="bob", window_ts=datetime.now(timezone.utc),
                      model_version="bob-v1", predicted="cooking", smoothed="unknown",
                      confidence=0.3, probabilities={"cooking": 0.3})
    act = json.loads(dict(state_messages(pred))[activity_state_topic("bob")])
    assert act["state"] == "unknown" and act["raw"] == "cooking"   # honest: raw kept


class FakeClient:
    def __init__(self):
        self.published = []
    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))


class FakeRepo:
    def __init__(self):
        self.s: dict = {}
    def get_connection(self, kind):
        return {"url": "mqtt://broker.local:1883", "options": {}} if kind == "mqtt" else None
    def get_setting(self, k, d=None):
        return self.s.get(k, d)
    def set_setting(self, k, v):
        self.s[k] = v
    def activities(self):
        return [type("A", (), {"slug": "movie"})()]


def test_publisher_uses_client_retained(monkeypatch):
    pub = MqttPublisher(FakeRepo())
    fake = FakeClient()
    pub._client = fake          # inject — skip the real paho connect
    pub.announce(_persons(), [])
    topics = [t for t, _, _ in fake.published]
    assert "homeassistant/sensor/hearth_alice/activity/config" in topics
    assert (AVAILABILITY_TOPIC, "online", True) in fake.published      # retained availability
    # current control states echoed (defaults: questions on, no override)
    assert (questions_state_topic("alice"), "ON", True) in fake.published
    assert (override_state_topic("alice"), "auto", True) in fake.published
    assert all(retain for _, _, retain in fake.published)              # all retained


def test_on_message_applies_control_command():
    repo = FakeRepo()
    pub = MqttPublisher(repo)
    fake = FakeClient()
    pub._client = fake

    class Msg:
        topic = "hearth/alice/override/set"
        payload = b"movie"

    pub._on_message(fake, None, Msg())
    from hearth.domain.controls import active_override
    assert active_override(repo, "alice") == "movie"                   # command applied
    assert (override_state_topic("alice"), "movie", True) in fake.published   # state echoed

    fake.published.clear()
    pred = Prediction(person_id="alice", window_ts=datetime.now(timezone.utc),
                      model_version="v", predicted="home", smoothed="home",
                      confidence=0.9, probabilities={"home": 0.9})
    pub.publish(pred)
    assert (activity_state_topic("alice")) in [t for t, _, _ in fake.published]


def test_publisher_noop_without_broker():
    class NoMqtt:
        def get_connection(self, kind):
            return None
    pub = MqttPublisher(NoMqtt())
    # no client, no broker -> announce/publish are safe no-ops
    pub.announce(_persons(), [])
    pub.publish(Prediction(person_id="alice", window_ts=datetime.now(timezone.utc),
                           model_version="v", predicted="home", confidence=0.9,
                           probabilities={}))
