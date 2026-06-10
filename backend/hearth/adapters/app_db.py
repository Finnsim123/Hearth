"""AppRepo + ModelStore adapters — SQLite via SQLAlchemy, Alembic migrations.

Tables in docs/DATA_MODEL.md §2. Tokens encrypted at rest (Fernet, key derived
from HEARTH_SECRET). Returns/accepts domain schemas, never leaks ORM objects.
Model artifacts: joblib files under settings.models_dir, path stored on the
ModelRecord row.
"""
from __future__ import annotations

from pathlib import Path


class AppDb:
    """Implements domain.ports.AppRepo."""

    def __init__(self, db_path: Path, secret: str) -> None:
        raise NotImplementedError

    def migrate(self) -> None:
        """Run Alembic migrations on boot (idempotent)."""
        raise NotImplementedError

    # ... AppRepo methods — Phase 1/2.


class FileModelStore:
    """Implements domain.ports.ModelStore (joblib on the data volume)."""

    def __init__(self, models_dir: Path) -> None:
        raise NotImplementedError
