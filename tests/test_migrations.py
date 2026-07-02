"""Alembic migrations: fresh upgrade builds the full schema, and the schema
matches the ORM models (drift guard — fails if models change without a new
migration)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from contacthop.db.migrate import downgrade, upgrade
from contacthop.domain.models import Base

EXPECTED_TABLES = {
    "contacts",
    "channel_identities",
    "conversations",
    "channel_sessions",
    "conversation_events",
    "messages",
    "follow_ups",
}


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "migrated.db"


def test_upgrade_head_creates_full_schema(db_path: Path) -> None:
    upgrade(f"sqlite+aiosqlite:///{db_path}")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables
    engine.dispose()


def test_migrated_schema_matches_models(db_path: Path) -> None:
    """Autogenerate against the migrated DB must find nothing to do."""
    upgrade(f"sqlite+aiosqlite:///{db_path}")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "render_as_batch": True}
        )
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diff == [], (
        "models and migrations have drifted — run "
        f"'alembic revision --autogenerate' and review: {diff}"
    )


def test_downgrade_base_removes_schema(db_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{db_path}"
    upgrade(url)
    downgrade(url, "base")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert EXPECTED_TABLES.isdisjoint(tables)


def test_app_runs_on_migrated_db_without_create_all(db_path: Path) -> None:
    from fastapi.testclient import TestClient

    from contacthop.config import Settings
    from contacthop.main import create_app

    upgrade(f"sqlite+aiosqlite:///{db_path}")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        auto_create_tables=False,
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        contact = client.post(
            "/v1/contacts",
            json={"identities": [{"channel": "sms", "address": "+15551112233"}]},
        )
        assert contact.status_code == 201, contact.text
