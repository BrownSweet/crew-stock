import importlib.util
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crew_stock import app as module
from crew_stock import config
from crew_stock.auth import digest
from crew_stock.deployment import mount_public_path

HEADERS = {"X-Crew-Stock": "1", "Origin": "https://localhost"}
CREDS = {"username": "crew-admin", "password": "synthetic-admin-password"}
ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(config, "DATA", store.root)
    monkeypatch.setattr(config, "BASE_PATH", "/crew/")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    with TestClient(
        mount_public_path(module.app, "/crew/"),
        base_url="https://localhost",
        headers=HEADERS,
    ) as c:
        yield c


def test_prefix_login_protects_all_business_data(client, store):
    assert (
        client.get("/crew?x=1", follow_redirects=False).headers["location"]
        == "/crew/?x=1"
    )
    assert client.get("/crew/api/health").json()["database"] == "mysql"
    assert client.get("/api/health").status_code == 404
    assert client.get("/crew/static/style.css").status_code == 200
    for path in ("companies", "status", "objects", "evidence/evd-000006/raw", "assets"):
        assert client.get("/crew/api/" + path).status_code == 401
    response = client.post("/crew/api/auth/setup", json=CREDS)
    assert response.status_code == 201
    assert "Path=/crew/" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert client.get("/crew/api/companies").status_code == 200
    assert client.get("/crew/api/companies").headers["cache-control"] == "no-store"
    assert client.post("/crew/api/auth/setup", json=CREDS).status_code == 409
    with store.connection() as con:
        encoded = con.execute("SELECT password_hash FROM accounts").fetchone()[0]
        token = con.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert CREDS["password"] not in encoded
    assert token != client.cookies.get("crew_session")
    assert client.post("/crew/api/auth/logout").status_code == 200
    assert not list(client.cookies)
    assert client.get("/crew/api/companies").status_code == 401
    assert (
        client.post(
            "/crew/api/auth/login", json={**CREDS, "password": "incorrect"}
        ).status_code
        == 401
    )
    assert client.post("/crew/api/auth/login", json=CREDS).status_code == 200


def test_concurrent_setup_only_one_admin(client, store):
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(
            pool.map(
                lambda _: client.post("/crew/api/auth/setup", json=CREDS).status_code,
                range(2),
            )
        )
    assert sorted(statuses) == [201, 409]
    with store.connection() as con:
        assert con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1


def test_expiry_rejected_and_validation_never_echoes_password(client, store):
    response = client.post("/crew/api/auth/setup", json={**CREDS, "password": "secret"})
    assert response.status_code == 422
    assert "secret" not in response.text
    assert client.post("/crew/api/auth/setup", json=CREDS).status_code == 201
    with store.connection() as con:
        con.execute(
            "UPDATE sessions SET expires_at=%s WHERE token_hash=%s",
            (int(time.time()) - 1, digest(client.cookies.get("crew_session"))),
        )
    assert client.get("/crew/api/auth/session").status_code == 401
    assert (
        client.post(
            "/crew/api/auth/login",
            json=CREDS,
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )


def test_build_bakes_all_static_urls_and_rejects_invalid_prefix(tmp_path):
    build = script("build_web").build
    for base in ("/", "/crew", "/projects/crew/"):
        target = tmp_path / (base.strip("/").replace("/", "-") or "root")
        normalized = build(ROOT / "web", target, base)
        html = (target / "index.html").read_text()
        assert f'content="{normalized}"' in html
        assert f'src="{normalized}static/app.js"' in html
        assert f'href="{normalized}"' in html
        assert (target / ".base-path").read_text() == normalized
    for base in ("https://evil.example/", "/../", "/x?y", "/x\n"):
        with pytest.raises(ValueError):
            build(ROOT / "web", tmp_path / "bad", base)


def test_worker_lease_and_transaction_rollback(store):
    with store.worker_lease():
        with pytest.raises(RuntimeError, match="已有"):
            with store.worker_lease():
                pass
    with pytest.raises(ValueError):
        with store.transaction() as con:
            con.execute("INSERT INTO meta VALUES ('rollback-check','value')")
            raise ValueError("abort")
    with store.connection() as con:
        assert not con.execute(
            "SELECT value FROM meta WHERE `key`='rollback-check'"
        ).fetchone()


def test_full_legacy_migration_and_nonempty_target_rejected(store, tmp_path):
    migration = script("migrate_sqlite_to_mysql")
    old = tmp_path / "legacy"
    old.mkdir()
    legacy = sqlite3.connect(old / "research.sqlite3")
    source_rows = {}
    with store.connection() as con:
        for table, size in migration.TABLES.items():
            source_rows[table] = list(con.execute(f"SELECT * FROM {table}").fetchall())
            legacy.execute(
                f"CREATE TABLE {table} ({','.join(f'c{i}' for i in range(size))})"
            )
            legacy.executemany(
                f"INSERT INTO {table} VALUES ({','.join('?' for _ in range(size))})",
                source_rows[table],
            )
            con.execute(f"DELETE FROM {table}")
    legacy.commit()
    legacy.close()
    import shutil

    shutil.copytree(store.root / "blobs", old / "blobs")
    (old / "settings.json").write_text('{"api_key":"synthetic-migration-key"}')
    result = migration.migrate(old, tmp_path / "new")
    assert result["verified"]
    assert result["tables"]["objects"] == len(source_rows["objects"])
    assert (tmp_path / "new" / "settings.json").read_text() == (
        old / "settings.json"
    ).read_text()
    assert not (tmp_path / "new" / "research.sqlite3").exists()
    with store.connection() as con:
        for table in migration.TABLES:
            assert migration.fingerprint(
                con.execute(f"SELECT * FROM {table}").fetchall()
            ) == migration.fingerprint(source_rows[table])
    with pytest.raises(ValueError, match="必须为空"):
        migration.migrate(old, tmp_path / "new")
    assert store.get("cmp-000001")["type"] == "company"
