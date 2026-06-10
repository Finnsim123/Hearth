from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("HEARTH_SECRET", "test-secret-please-be-long-enough")
os.environ.setdefault("HEARTH_DATA_DIR", "/tmp/hearth-test")

from hearth.domain.schemas import Binding, Role  # noqa: E402


@pytest.fixture
def bindings() -> list[Binding]:
    """A generic two-person home — NO real entity names, by design."""
    return [
        Binding(entity_id="binary_sensor.couch_zone", role=Role.PRESENCE, name="couch", room="living"),
        Binding(entity_id="sensor.bed_a_voltage", role=Role.BED, name="bed_a", person_id="alice",
                options={"threshold": 1.5}),
        Binding(entity_id="sensor.espresso_w", role=Role.POWER, name="espresso", room="kitchen",
                options={"on_threshold": 100}),
        Binding(entity_id="light.main_group", role=Role.LIGHT, name="lights"),
        Binding(entity_id="media_player.tv", role=Role.MEDIA, name="tv", room="living"),
        Binding(entity_id="sensor.air_co2", role=Role.ENV, name="co2"),
        Binding(entity_id="person.alice", role=Role.PERSON, name="alice_loc", person_id="alice"),
        Binding(entity_id="input_datetime.alice_alarm", role=Role.ALARM_TIME, name="alarm",
                person_id="alice"),
    ]


@pytest.fixture
def raw(bindings) -> pd.DataFrame:
    """3 h of 1-min synthetic data: night until 07:00, then morning routine."""
    idx = pd.date_range("2026-06-01 05:00", periods=180, freq="1min", tz="UTC")
    rng = np.random.default_rng(7)
    morning = idx >= pd.Timestamp("2026-06-01 07:00", tz="UTC")
    df = pd.DataFrame(index=idx)
    df["couch"] = np.where(morning & (idx.minute % 7 == 0), 1.0, 0.0)
    df["bed_a"] = np.where(~morning, 2.4 + rng.normal(0, 0.1, 180), 0.1)
    df["espresso"] = np.where(morning & (idx < idx[0] + pd.Timedelta("135min")), 1300.0, 0.0)
    df["lights"] = np.where(morning, 1.0, 0.0)
    df["tv"] = np.where(morning, "playing", "idle")
    df["co2"] = 600 + np.linspace(0, 80, 180) + rng.normal(0, 5, 180)
    df["alice_loc"] = "home"
    df["alarm"] = "07:00:00"
    return df
