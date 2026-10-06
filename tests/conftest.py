import os

os.environ["CREWAI_TELEMETRY_DISABLED"] = "true"
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["CREWAI_TRACING_ENABLED"] = "false"
os.environ["CREW_STOCK_DISABLE_WORKER"] = "1"

import pytest

from crew_stock import config
from crew_stock.store import Store

config.require_test_database()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "false")
    from crew_stock.auth import ATTEMPTS

    ATTEMPTS.clear()
    db = Store(tmp_path / "data")
    db.initialize()
    with db.connection() as con:
        for table in (
            "crew_task_outputs",
            "sessions",
            "accounts",
            "object_links",
            "objects",
            "symbols",
            "sequences",
            "meta",
        ):
            con.execute(f"DELETE FROM {table}")
    db.initialize()
    return db
