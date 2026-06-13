"""EntityPublisher adapter — MQTT discovery (ADR-5).

The OPEN, broker-based output channel. Publishes retained Home Assistant MQTT
discovery configs under `homeassistant/…` so any MQTT consumer auto-creates a
"Hearth" device with per-person entities, then streams predictions to retained
state topics. Home Assistant picks them up with zero custom integration; so do
non-HA hubs that speak HA-style MQTT discovery (Homey, Node-RED, openHAB, …).

For Home Assistant specifically the bundled `custom_components/hearth` integration
is the recommended path (it also carries the low-latency event and the feedback
loop); MQTT is the alternative for broker-centric or non-HA setups.

Published per person:
    sensor.hearth_<person>_activity     state = smoothed activity (or "unknown");
                                        attributes: raw, confidence, probabilities,
                                        window_ts, model_version, state_level
    sensor.hearth_<person>_confidence   0–100 %
plus one global availability `binary_sensor.hearth_alive` (online/offline via LWT).

Payload building is pure and unit-tested; the paho client is best-effort — a
missing broker or paho is a no-op (the integration still delivers), never a crash.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from ..domain.controls import AUTO, active_override, apply_command, questions_disabled

log = logging.getLogger(__name__)

DISCOVERY_PREFIX = "homeassistant"     # HA's default MQTT discovery prefix
STATE_PREFIX = "hearth"
AVAILABILITY_TOPIC = f"{STATE_PREFIX}/status"   # "online" / "offline" (retained + LWT)


# ── pure payload builders (no broker, no paho) ───────────────────────────────
def _device(person_id: str, person_name: str) -> dict:
    return {"identifiers": [f"hearth_{person_id}"],
            "name": f"Hearth · {person_name}",
            "manufacturer": "Hearth", "model": "Activity recognition"}


def activity_state_topic(person_id: str) -> str:
    return f"{STATE_PREFIX}/{person_id}/activity"


def confidence_state_topic(person_id: str) -> str:
    return f"{STATE_PREFIX}/{person_id}/confidence"


def questions_state_topic(person_id: str) -> str:
    return f"{STATE_PREFIX}/{person_id}/questions"


def questions_command_topic(person_id: str) -> str:
    return f"{STATE_PREFIX}/{person_id}/questions/set"


def override_state_topic(person_id: str) -> str:
    return f"{STATE_PREFIX}/{person_id}/override"


def override_command_topic(person_id: str) -> str:
    return f"{STATE_PREFIX}/{person_id}/override/set"


def discovery_configs(persons, activities=None) -> list[tuple[str, dict]]:
    """(topic, config) pairs to publish RETAINED so HA/Homey/etc. create the
    entities. One availability binary_sensor + an activity and confidence sensor
    per person."""
    out: list[tuple[str, dict]] = [(
        f"{DISCOVERY_PREFIX}/binary_sensor/hearth/alive/config",
        {"name": "Hearth alive", "unique_id": "hearth_alive",
         "device_class": "connectivity", "state_topic": AVAILABILITY_TOPIC,
         "payload_on": "online", "payload_off": "offline",
         "device": {"identifiers": ["hearth"], "name": "Hearth",
                    "manufacturer": "Hearth"}},
    )]
    for p in persons:
        pid, name = p.id, getattr(p, "name", p.id)
        dev = _device(pid, name)
        out.append((
            f"{DISCOVERY_PREFIX}/sensor/hearth_{pid}/activity/config",
            {"name": "Activity", "unique_id": f"hearth_{pid}_activity",
             "state_topic": activity_state_topic(pid),
             "value_template": "{{ value_json.state }}",
             "json_attributes_topic": activity_state_topic(pid),
             "icon": "mdi:home-account",
             "availability_topic": AVAILABILITY_TOPIC, "device": dev}))
        out.append((
            f"{DISCOVERY_PREFIX}/sensor/hearth_{pid}/confidence/config",
            {"name": "Activity confidence", "unique_id": f"hearth_{pid}_confidence",
             "state_topic": confidence_state_topic(pid),
             "unit_of_measurement": "%", "state_class": "measurement",
             "availability_topic": AVAILABILITY_TOPIC, "device": dev}))
        # two-way: a switch to pause training questions for this person
        out.append((
            f"{DISCOVERY_PREFIX}/switch/hearth_{pid}/questions/config",
            {"name": "Activity questions", "unique_id": f"hearth_{pid}_questions",
             "state_topic": questions_state_topic(pid),
             "command_topic": questions_command_topic(pid),
             "payload_on": "ON", "payload_off": "OFF", "icon": "mdi:comment-question",
             "availability_topic": AVAILABILITY_TOPIC, "device": dev}))
        # two-way: a select to manually pin the published activity ("auto" = model)
        options = [AUTO] + [getattr(a, "slug", str(a)) for a in (activities or [])]
        out.append((
            f"{DISCOVERY_PREFIX}/select/hearth_{pid}/override/config",
            {"name": "Override activity", "unique_id": f"hearth_{pid}_override",
             "state_topic": override_state_topic(pid),
             "command_topic": override_command_topic(pid),
             "options": options, "icon": "mdi:gesture-tap-button",
             "availability_topic": AVAILABILITY_TOPIC, "device": dev}))
    return out


def state_messages(pred) -> list[tuple[str, str]]:
    """(topic, payload) pairs for one prediction — the activity JSON and the
    confidence percent. Published retained so a consumer that subscribes later
    sees the current state."""
    state = pred.smoothed or pred.predicted
    payload = {
        "state": state,
        "raw": pred.predicted,
        "confidence": round(pred.confidence, 3),
        "probabilities": {k: round(float(v), 3) for k, v in (pred.probabilities or {}).items()},
        "window_ts": pred.window_ts.isoformat() if pred.window_ts else None,
        "model_version": pred.model_version,
    }
    if getattr(pred, "parent", None):
        payload["state_level"] = pred.parent          # coarse state for stable triggers
    return [(activity_state_topic(pred.person_id), json.dumps(payload)),
            (confidence_state_topic(pred.person_id), str(round(pred.confidence * 100)))]


def _broker(conn: dict) -> tuple[str, int]:
    """Host + port from a connection: accepts a bare host or mqtt://host:port."""
    url = (conn or {}).get("url") or ""
    if "://" not in url:
        url = "mqtt://" + url
    u = urlparse(url)
    return (u.hostname or "localhost", u.port or 1883)


class MqttPublisher:
    """Implements domain.ports.EntityPublisher over an MQTT broker (paho)."""

    def __init__(self, repo) -> None:
        self.repo = repo
        self._client = None
        self._last: tuple = ([], [])   # last (persons, activities) for birth re-announce

    def _connect(self):
        if self._client is not None:
            return self._client
        conn = self.repo.get_connection("mqtt")
        if not conn or not conn.get("url"):
            return None
        try:
            import paho.mqtt.client as mqtt
            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)   # paho 2.x
            except (AttributeError, TypeError):
                client = mqtt.Client()                                   # paho 1.x
            opts = conn.get("options") or {}
            user = opts.get("username")
            if user:
                client.username_pw_set(user, conn.get("token") or opts.get("password"))
            client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
            client.on_connect = self._on_connect
            client.on_message = self._on_message
            host, port = _broker(conn)
            client.connect(host, port, keepalive=60)
            client.loop_start()
            self._client = client
        except Exception:
            log.exception("MQTT connect failed — predictions will not publish to MQTT")
            return None
        return self._client

    def _on_connect(self, client, *args) -> None:
        # re-publish discovery + availability on (re)connect, watch HA's birth,
        # and listen for the two-way control commands (questions switch, override)
        try:
            client.subscribe(f"{DISCOVERY_PREFIX}/status")
            client.subscribe(f"{STATE_PREFIX}/+/questions/set")
            client.subscribe(f"{STATE_PREFIX}/+/override/set")
            self.announce(*self._last)
        except Exception:
            log.debug("MQTT on_connect (re)subscribe failed", exc_info=True)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            topic = msg.topic
            payload = msg.payload.decode() if isinstance(msg.payload, bytes) else str(msg.payload)
            # HA came back online -> resend retained discovery so entities reappear
            if topic == f"{DISCOVERY_PREFIX}/status" and payload == "online":
                self.announce(*self._last)
                return
            # a control command -> apply in the domain, then echo the new state
            valid = {a.slug for a in self.repo.activities()}
            result = apply_command(self.repo, topic, payload, valid)
            if result is not None:
                self._publish_control_state(client, *result)
        except Exception:
            log.debug("MQTT message handling failed", exc_info=True)

    def _publish_control_state(self, client, control: str, pid: str, state: str) -> None:
        topic = (questions_state_topic(pid) if control == "questions"
                 else override_state_topic(pid))
        client.publish(topic, state, retain=True)

    def announce(self, persons, activities) -> None:
        self._last = (persons, activities)
        client = self._connect()
        if client is None:
            return
        try:
            for topic, payload in discovery_configs(persons, activities):
                client.publish(topic, json.dumps(payload), retain=True)
            client.publish(AVAILABILITY_TOPIC, "online", retain=True)
            # echo current control states so the switch/select reflect reality
            for p in persons:
                client.publish(questions_state_topic(p.id),
                               "OFF" if questions_disabled(self.repo, p.id) else "ON",
                               retain=True)
                client.publish(override_state_topic(p.id),
                               active_override(self.repo, p.id) or AUTO, retain=True)
        except Exception:
            log.exception("MQTT announce failed")

    def publish(self, pred) -> None:
        client = self._connect()
        if client is None:
            return
        try:
            for topic, payload in state_messages(pred):
                client.publish(topic, payload, retain=True)
        except Exception:
            log.exception("MQTT publish failed for %s", getattr(pred, "person_id", "?"))
