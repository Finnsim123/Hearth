from __future__ import annotations

from pathlib import Path

import pytest

from hearth.adapters.app_db import AppDb
from hearth.domain.schemas import Binding, Person, Role, User


@pytest.fixture
def db(tmp_path: Path) -> AppDb:
    db = AppDb(tmp_path / "test.db")
    db.migrate()
    return db


def test_binding_crud(db):
    b = db.save_binding(Binding(entity_id="sensor.x_w", role=Role.POWER, name="x"))
    assert b.id is not None
    assert db.bindings()[0].role is Role.POWER
    b.room = "kitchen"
    db.save_binding(b)
    assert db.bindings()[0].room == "kitchen"
    db.delete_binding(b.id)
    assert db.bindings() == []


def test_person_quiet_hours_roundtrip(db):
    db.save_person(Person(id="kid", name="Kid", has_device=False, quiet_hours=(21, 7),
                          notify_system=True, ask_budget_per_day=3))
    p = db.persons()[0]
    assert p.has_device is False and p.quiet_hours == (21, 7)
    assert p.notify_system is True and p.ask_budget_per_day == 3


def test_connection_token_encrypted_at_rest(db, tmp_path):
    db.set_connection("ha", "http://ha.local:8123", "super-secret-token")
    assert db.get_connection("ha")["token"] == "super-secret-token"
    blob = (tmp_path / "test.db").read_bytes()
    assert b"super-secret-token" not in blob       # never plaintext on disk


def test_settings_roundtrip(db):
    db.set_setting("composites", [{"name": "a"}])
    assert db.get_setting("composites") == [{"name": "a"}]
    assert db.get_setting("missing", 42) == 42


def test_user_login_flow(db):
    db.create_user(User(email="a@b.c", display_name="A", role="admin"), "longpassword1")
    assert db.user_count() == 1
    assert db.verify_login("a@b.c", "longpassword1").role == "admin"
    assert db.verify_login("a@b.c", "wrong") is None


def test_email_normalized_both_ways(db):
    db.create_user(User(email="  Alice@B.C ", display_name="A", role="admin"),
                   "longpassword1")
    assert db.verify_login("alice@b.c", "longpassword1") is not None
    assert db.verify_login(" ALICE@B.C  ", "longpassword1") is not None
