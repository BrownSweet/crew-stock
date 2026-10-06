import pytest
from fastapi.testclient import TestClient

from crew_stock import app as module
from crew_stock import config

HEADERS = {"X-Crew-Stock": "1"}


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(config, "DATA", store.root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    with TestClient(module.app) as client:
        assert (
            client.post(
                "/api/auth/setup",
                headers={"X-Crew-Stock": "1"},
                json={"username": "test-admin", "password": "test-admin-password"},
            ).status_code
            == 201
        )
        yield client


def test_health_and_history(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert (
        client.get("/api/companies").json()[0]["securities"][0]["symbol"] == "NVDA.US"
    )
    assert len(client.get("/api/objects?kind=evidence").json()) == 17
    assert "NVIDIA" in client.get("/api/evidence/evd-000006/raw").text
    assert client.get("/").status_code == 200
    assert "default-src 'self'" in client.get("/").headers["content-security-policy"]


def test_mutation_origin_guard(client):
    payload = {"name": "Apple", "symbols": ["AAPL.US"]}
    assert client.post("/api/companies", json=payload).status_code == 403
    assert (
        client.post(
            "/api/companies",
            headers={**HEADERS, "Origin": "https://evil.example"},
            json=payload,
        ).status_code
        == 403
    )
    assert (
        client.post("/api/companies", headers=HEADERS, json=payload).status_code == 201
    )


def test_missing_model_key_no_queued_research(client):
    result = client.post(
        "/api/runs",
        headers=HEADERS,
        json={"company_id": "cmp-000001", "symbol": "NVDA.US"},
    )
    assert result.status_code == 400
    assert len(client.get("/api/objects?kind=run").json()) == 1


def test_no_secret_return_and_private_file(client, store):
    result = client.put(
        "/api/settings", headers=HEADERS, json={"api_key": "test-private-key"}
    )
    assert result.status_code == 200
    assert "test-private-key" not in result.text
    assert (store.root / "settings.json").stat().st_mode & 0o777 == 0o600
    assert "test-private-key" not in client.get("/api/objects?kind=run").text


def test_company_mismatch_is_rejected(client):
    result = client.post(
        "/api/runs",
        headers=HEADERS,
        json={"company_id": "cmp-000001", "symbol": "AAPL.US", "mode": "collect"},
    )
    assert result.status_code == 400


def test_submission_keeps_annotation_separate(client):
    result = client.post(
        "/api/submissions",
        headers=HEADERS,
        json={"company_id": "cmp-000001", "text": "外部文章", "annotation": "我的猜测"},
    )
    assert result.status_code == 201
    item = result.json()
    assert item["text"] == "外部文章"
    assert item["annotation"] == "我的猜测"
    assert item["classification"] == "user_provided_unverified"
    raw = client.get("/api/evidence/" + item["evidence_ids"][0] + "/raw")
    assert raw.text == "外部文章"


def test_schedule_starts_disabled_and_invalid_symbol_rejected(client):
    payload = {"company_id": "cmp-000001", "symbol": "NVDA.US"}
    result = client.post("/api/schedules", headers=HEADERS, json=payload)
    assert result.status_code == 201
    assert result.json()["enabled"] is False
    assert (
        client.post("/api/schedules", headers=HEADERS, json=payload).status_code == 400
    )
    assert (
        client.post(
            "/api/schedules", headers=HEADERS, json={**payload, "symbol": "AAPL.US"}
        ).status_code
        == 400
    )


def test_pending_run_can_be_inspected(client):
    result = client.post(
        "/api/runs",
        headers=HEADERS,
        json={"company_id": "cmp-000001", "symbol": "NVDA.US", "mode": "collect"},
    )
    assert result.status_code == 202
    assert (
        client.get("/api/objects/" + result.json()["id"]).json()["status"] == "queued"
    )
