import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from test_research import report_data, run_record

from crew_stock import app as module
from crew_stock import config
from crew_stock.discovery import validate_extraction
from crew_stock.research import execute_run
from crew_stock.store import now
from crew_stock.workspace import compare_metrics, ingest_consensus, prepare_record
from crew_stock.workspace_models import Extraction

H = {"X-Crew-Stock": "1"}
CID = "cmp-000001"


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


@pytest.fixture
def ev(store):
    text = "CFO: We expect revenue of at least 100 USD for Q1 2026. Analyst: Why? CFO: Demand increased. Actual revenue 120 USD. GAAP. Period 2026-01-01 to 2026-03-31. Released 2026-04-20. Due 2026-04-30."
    blob = store.blob(text.encode())
    return store.insert(
        {
            "id": store.allocate("evd"),
            "type": "evidence",
            "parent_id": CID,
            "source": "longbridge",
            "acquisition_status": "success",
            "blob": blob,
            "captured_at": "2026-01-01T00:00:00+00:00",
            "as_of": "2026-01-01",
        }
    )


def metric(eid, **changes):
    return {
        "metric": "revenue",
        "period": "2026 Q1",
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "basis": "GAAP",
        "unit": "dollar",
        "currency": "USD",
        "value": "100",
        "category": "guidance",
        "published_at": "2026-01-01T00:00:00+00:00",
        "direction": "higher",
        "evidence_ids": [eid],
        "source_quote": "revenue of at least 100 USD",
        **changes,
    }


def promise(eid):
    return {
        "title": "Revenue target",
        "speaker": "CFO",
        "promised_at": "2026-01-01",
        "due_date": "2026-04-30",
        "conditions": "",
        "metric": "revenue",
        "target": "100",
        "operator": "at_least",
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "basis": "GAAP",
        "unit": "dollar",
        "currency": "USD",
        "evidence_ids": [eid],
        "source_quote": "We expect revenue of at least 100 USD for Q1 2026.",
    }


def save(client, kind, data):
    response = client.post(
        "/api/workspace/records",
        headers=H,
        json={"company_id": CID, "kind": kind, "data": data},
    )
    assert response.status_code == 201, response.text
    return response.json()


def confirm(client, record):
    response = client.post(
        f"/api/workspace/records/{record['id']}/review",
        headers=H,
        json={"decision": "confirmed", "note": "核对原文与统计口径"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_source_quote_company_scope_and_dedup(client, store, ev):
    first = save(client, "metric", metric(ev["id"]))
    assert save(client, "metric", metric(ev["id"]))["id"] == first["id"]
    bad = metric(ev["id"], source_quote="invented facts")
    response = client.post(
        "/api/workspace/records",
        headers=H,
        json={"company_id": CID, "kind": "metric", "data": bad},
    )
    assert response.status_code == 400
    other = store.create_company(
        {"name": "Apple", "symbols": ["AAPL.US"], "purpose": "Research"}
    )
    with pytest.raises(ValueError, match="本公司"):
        prepare_record(store, other["id"], "metric", metric(ev["id"]))
    assert len(store.list("metric")) == 1


def test_promises_results_and_immutable_reviews(client, store, ev):
    p = confirm(client, save(client, "promise", promise(ev["id"])))
    actual = save(
        client,
        "metric",
        metric(
            ev["id"],
            value="120",
            category="actual",
            published_at="2026-04-20T00:00:00Z",
        ),
    )
    payload = {"actual_metric_id": actual["id"], "note": "核查承诺适用条件成立"}
    assert (
        client.post(
            f"/api/workspace/promises/{p['id']}/evaluate", headers=H, json=payload
        ).status_code
        == 400
    )
    confirm(client, actual)
    r = client.post(
        f"/api/workspace/promises/{p['id']}/evaluate", headers=H, json=payload
    )
    assert r.status_code == 201 and r.json()["status"] == "fulfilled"
    mismatch = confirm(
        client,
        save(client, "metric", metric(ev["id"], category="actual", currency="CNY")),
    )
    assert (
        client.post(
            f"/api/workspace/promises/{p['id']}/evaluate",
            headers=H,
            json={**payload, "actual_metric_id": mismatch["id"]},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/workspace/records/{p['id']}/review",
            headers=H,
            json={"decision": "rejected", "note": "overwrite"},
        ).status_code
        == 400
    )
    assert store.get(p["id"])["data"] == p["data"]


def test_period_currency_vintage_and_zero_comparison(client, store, ev):
    a = confirm(client, save(client, "metric", metric(ev["id"])))
    b = confirm(
        client,
        save(
            client,
            "metric",
            metric(
                ev["id"],
                value="120",
                category="actual",
                published_at="2026-04-20T00:00:00Z",
            ),
        ),
    )
    assert (
        "事前" in compare_metrics(a, b).get("reason", "")
        or "公布前" in compare_metrics(a, b)["reason"]
    )
    a["created_at"] = "2026-01-02T00:00:00Z"
    result = compare_metrics(a, b)
    assert result["delta"] == "20" and float(result["percent"]) == 20
    b["data"]["currency"] = "CNY"
    assert compare_metrics(a, b)["status"] == "incomparable"
    b["data"].update(currency="USD", category="guidance")
    a["data"]["value"] = "0"
    assert compare_metrics(a, b)["percent"] is None


def decision(eid):
    return {
        "company_id": CID,
        "action": "watch",
        "decided_at": now(),
        "rationale": "Wait for durable demand",
        "alternatives": "Buy now",
        "risks": "Cyclical demand",
        "assumptions": [
            {
                "text": "Revenue grows",
                "invalidate_if": "Revenue falls",
                "check_date": "2027-01-01",
            }
        ],
        "evidence_ids": [eid],
        "review_date": "2027-01-01",
    }


def test_decision_frozen_snapshot_hindsight_and_review(client, store, ev):
    data = decision(ev["id"])
    r = client.post("/api/workspace/decisions", headers=H, json=data)
    assert r.status_code == 201, r.text
    saved = r.json()
    assert saved["evidence_snapshot"][0]["hash"] == ev["blob"]
    data["decided_at"] = "2025-12-01T00:00:00Z"
    assert (
        client.post("/api/workspace/decisions", headers=H, json=data).status_code == 400
    )
    data["retrospective"] = True
    assert client.post("/api/workspace/decisions", headers=H, json=data).json()[
        "not_available_at_decision"
    ] == [ev["id"]]
    review = {
        "observed_at": datetime.now(timezone.utc).date().isoformat(),
        "outcome": "待验证",
        "process_quality": "在证据不足时等待",
        "lessons": "持续跟踪",
        "assumption_results": [
            {"index": 1, "status": "unresolved", "explanation": "尚待财报"}
        ],
        "evidence_ids": [ev["id"]],
    }
    url = f"/api/workspace/decisions/{saved['id']}/reviews"
    assert client.post(url, headers=H, json=review).status_code == 400
    review["assumption_results"][0]["index"] = 0
    assert client.post(url, headers=H, json=review).status_code == 201
    assert client.post(url, headers=H, json=review).status_code == 201
    assert store.get(saved["id"]) == saved
    assert len(store.list("decision_review")) == 2


def test_import_and_queue_contract(client, store, ev, monkeypatch):
    data = {
        "company_id": CID,
        "title": "Earnings call",
        "kind": "earnings_call",
        "text": store.read_blob(ev["blob"]).decode(),
        "analyze": False,
    }
    response = client.post("/api/workspace/documents", headers=H, json=data)
    assert response.status_code == 201
    doc = response.json()
    assert store.get(doc["evidence_ids"][0])["source"] == "user"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    r = client.post(f"/api/workspace/documents/{doc['id']}/analyze", headers=H)
    assert r.status_code == 202 and r.json()["source_document_id"] == doc["id"]
    assert client.get(f"/api/workspace/export/{CID}").json()["format_version"] == 1


def test_groups_alignment_membership_versions_and_scope(client, store, ev):
    other = store.create_company(
        {"name": "Apple", "symbols": ["AAPL.US"], "purpose": "research"}
    )
    payload = {"name": "Tech", "company_ids": [CID, other["id"]]}
    g = client.post("/api/workspace/groups", headers=H, json=payload).json()
    confirm(client, save(client, "metric", metric(ev["id"])))
    detail = client.get(f"/api/workspace/groups/{g['id']}").json()
    assert len(detail["aligned_metrics"]) == 1
    assert detail["gaps"] == [other["id"]]
    assert (
        client.put(
            f"/api/workspace/groups/{g['id']}",
            headers=H,
            json={**payload, "name": "Technology"},
        ).status_code
        == 200
    )
    assert len(store.list("group_version")) == 1
    assert (
        client.post(
            "/api/workspace/groups",
            headers=H,
            json={"name": "bad", "company_ids": [CID, CID]},
        ).status_code
        == 422
    )


def test_consensus_keeps_supplier_attribution_without_faking_vintage(store, ev):
    payload = {
        "list": [
            {
                "period_text": "Q1 2026",
                "details": [
                    {
                        "key": "revenue",
                        "name": "Revenue",
                        "estimate": "100",
                        "actual": "120",
                        "is_released": True,
                        "comp_desc": "Beat",
                    }
                ],
            }
        ]
    }
    e = {
        **ev,
        "topic": "consensus",
        "symbol": "NVDA.US",
        "blob": store.blob(json.dumps(payload).encode()),
    }
    ingest_consensus(store, e)
    ingest_consensus(store, e)
    snapshots = store.list("expectation_snapshot")
    assert len(snapshots) == 1
    assert snapshots[0]["data"]["vintage_at"] is None
    assert snapshots[0]["data"]["delta"] == "20"


def test_thesis_versions_preserve_before_after_and_reason(store):
    first = store.insert(
        {
            "id": store.allocate("rpt"),
            "type": "report",
            "parent_id": CID,
            "report": report_data(),
            "review_status": "pending_confirmation",
            "evidence_ids": ["evd-000006"],
        }
    )
    store.review(first["id"], "confirmed", "First thesis")
    second = store.insert(
        {
            **first,
            "id": store.allocate("rpt"),
            "report": {**report_data(), "thesis_proposal": "Changed thesis"},
        }
    )
    store.review(second["id"], "confirmed", "New evidence")
    versions = store.list("thesis_version")
    assert len(versions) == 2
    assert versions[0]["before"] == report_data()["thesis_proposal"]
    assert versions[0]["after"] == "Changed thesis"
    assert versions[0]["reason"] == "New evidence"


def test_full_crewai_extraction_to_ledger(store, monkeypatch, ev):
    from crewai import BaseLLM

    data = report_data(ev["id"])
    data["extraction"] = {
        "disclosures": [
            {
                "kind": "earnings_call",
                "title": "Call excerpt",
                "period": "2026 Q1",
                "coverage": "excerpt",
                "summary": "Management expects revenue growth",
                "evidence_ids": [ev["id"]],
                "source_quote": "Analyst: Why? CFO: Demand increased.",
                "questions_answers": [
                    {
                        "question": "Why?",
                        "answer": "Demand increased.",
                        "speaker": "CFO",
                    }
                ],
            }
        ],
        "promises": [promise(ev["id"])],
        "metrics": [metric(ev["id"])],
    }

    class LLM(BaseLLM):
        def call(self, *args, **kwargs):
            return "Final Answer: " + json.dumps(data)

        def supports_function_calling(self):
            return False

        def get_context_window_size(self):
            return 64000

    sid = store.allocate("src")
    store.insert(
        {
            "id": sid,
            "type": "source_document",
            "parent_id": CID,
            "evidence_ids": [ev["id"]],
        }
    )
    monkeypatch.setattr(config, "DATA", store.root)
    run = store.update(run_record(store)["id"], source_document_id=sid)
    execute_run(store, run, llm=LLM(model="test-scripted-no-network"))
    result = store.get(run["id"])
    assert result["status"] == "completed_with_warnings", result
    assert (
        len(store.list("disclosure"))
        == len(store.list("promise"))
        == len(store.list("metric"))
        == 1
    )
    assert store.list("promise")[0]["review_status"] == "pending_confirmation"
    assert len(store.get(result["report_id"])["extracted_record_ids"]) == 3


def test_extraction_rejects_invented_quote_and_out_of_run_evidence(store, ev):
    bundle = Extraction(metrics=[metric(ev["id"], source_quote="fake quote")])
    with pytest.raises(ValueError, match="摘录"):
        validate_extraction(store, CID, bundle, {ev["id"]})
    with pytest.raises(ValueError, match="之外"):
        validate_extraction(store, CID, Extraction(metrics=[metric(ev["id"])]), set())


def test_model_invented_metadata_is_removed_and_old_pending_preserved(store, ev):
    from crew_stock.discovery import revalidate_pending_records, sanitize_extraction
    from crew_stock.models import ResearchReport

    data = report_data(ev["id"])
    data["extraction"] = {
        "metrics": [metric(ev["id"], period_start="2026-01-02", basis="Invented-GAAP")]
    }
    report = sanitize_extraction(store, ResearchReport.model_validate(data))
    assert report.extraction.metrics == []
    assert any("已拦截" in g for g in report.gaps)
    old = store.insert(
        prepare_record(
            store, CID, "metric", data["extraction"]["metrics"][0], origin="model"
        )
    )
    revalidate_pending_records(store)
    assert store.get(old["id"])["review_status"] == "rejected"
    assert store.get(old["id"])["data"] == old["data"]
    assert store.get(old["id"])["validation_origin"] == "system"
    revalidate_pending_records(store)
    assert len(store.list("record_review")) == 1


def test_confirmed_promise_gets_automatic_numeric_check(client, ev):
    p = confirm(client, save(client, "promise", promise(ev["id"])))
    confirm(
        client, save(client, "metric", metric(ev["id"], category="actual", value="120"))
    )
    check = client.get(f"/api/workspace/companies/{CID}").json()["promise_checks"][0]
    assert check["promise_id"] == p["id"]
    assert check["status"] == "numeric_target_met"
    assert check["review_required"] is True


def test_all_four_skills_are_actually_discovered():
    from crewai.skills import discover_skills

    assert len(list(discover_skills(config.SKILLS))) == 4


def test_bad_optional_extraction_does_not_discard_valid_core_report(store, ev):
    from crew_stock.discovery import sanitize_extraction
    from crew_stock.models import ResearchReport

    data = report_data(ev["id"])
    incomplete = promise(ev["id"])
    incomplete["currency"] = ""
    data["extraction"] = {"promises": [incomplete], "metrics": [metric(ev["id"])]}
    report = ResearchReport.model_validate(data)
    assert report.extraction.promises == []
    assert report.extraction.validation_issues
    cleaned = sanitize_extraction(store, report, CID, {ev["id"]})
    assert len(cleaned.extraction.metrics) == 1
    assert cleaned.summary == data["summary"]
    assert any("未进入台账" in text for text in cleaned.gaps)
    report.extraction.metrics[0].evidence_ids = ["evd-999999"]
    assert sanitize_extraction(store, report, CID, {ev["id"]}).extraction.metrics == []


def test_call_discovery_accepts_company_chinese_name_not_unrelated_calls(store):
    from crew_stock.discovery import enrich

    class Collector:
        def __init__(self):
            self.store = store
            self.run = {"company_id": CID, "symbol": "NVDA.US"}
            self.calls = []

        def collect(self, topic, source_id=""):
            self.calls.append((topic, source_id))
            values = [
                {"id": "123", "title": "英伟达财报电话会纪要"},
                {"id": "456", "title": "其他公司财报电话会"},
            ]
            return {
                "id": "fixture",
                "topic": topic,
                "acquisition_status": "success",
                "blob": store.blob(
                    json.dumps(values if topic == "call_search" else {}).encode()
                ),
            }

    collector = Collector()
    enrich(collector, [])
    assert ("news_detail", "123") in collector.calls
    assert ("news_detail", "456") not in collector.calls
