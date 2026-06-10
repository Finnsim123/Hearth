"""Phase-0 smoke tests: the skeleton imports and the API boots."""
from __future__ import annotations

import os

os.environ.setdefault("HEARTH_SECRET", "test-secret")
os.environ.setdefault("HEARTH_DATA_DIR", "/tmp/hearth-test")


def test_schemas_import():
    from hearth.domain import schemas

    p = schemas.Person(id="alice", name="Alice")
    assert p.has_device is True


def test_ports_are_protocols():
    from hearth.domain import ports

    assert hasattr(ports, "TimeSeriesStore")
    assert hasattr(ports, "Estimator")
    assert hasattr(ports, "Embedder")


def test_health_endpoint():
    from fastapi.testclient import TestClient

    from hearth.main import create_app

    client = TestClient(create_app())
    assert client.get("/api/health").json()["status"] == "ok"
