from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contacthop.config import Settings
from contacthop.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        sms_adapter="console",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
