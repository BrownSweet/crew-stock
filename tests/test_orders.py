from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from crew_stock import app as module
from crew_stock import config, orders
from crew_stock.orders import (
    Extraction,
    OrderDraft,
    OrderTime,
    PartyInput,
    ProposalInput,
    ScanInput,
    WatchInput,
    alerts,
    approve,
    at_orders,
    create_party,
    graph,
    process_scan,
    propose,
    start_scan,
    tick_orders,
    watch,
)
from crew_stock.workspace import archive

H = {"X-Crew-Stock": "1"}
CID = "cmp-000001"


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(config, "DATA", store.root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-test-key")
    with TestClient(module.app) as c:
        assert (
            c.post(
                "/api/auth/setup",
                headers={"X-Crew-Stock": "1"},
                json={"username": "test-admin", "password": "test-admin-password"},
            ).status_code
            == 201
        )
        yield c


@pytest.fixture
def source(store):
    raw = "FICTITIOUS TEST: NVIDIA supplies Example Buyer with chips. Contract USD 100 million. Delivery on 2026-12-01. Payment on 2027-01-15. Expires before 2027; exact meaning unknown."
    return store.insert(
        {
            "id": store.allocate("evd"),
            "type": "evidence",
            "parent_id": CID,
            "source": "user",
            "topic": "document_parse",
            "acquisition_status": "success",
            "blob": store.blob(raw.encode()),
        }
    ), raw


@pytest.fixture
def buyer(store):
    return create_party(
        store, PartyInput(name="Example Buyer", notes="Unlisted fictitious party")
    )


def draft(source, buyer):
    e, quote = source
    return OrderDraft(
        title="虚构测试订单",
        reference="TEST-001",
        agreement_type="firm",
        supplier_id=CID,
        supplier_name="NVIDIA",
        buyer_id=buyer["id"],
        buyer_name="Example Buyer",
        product="chips",
        amount=100,
        currency="USD",
        amount_scale="million",
        status="active",
        milestones=[
            {
                "key": "delivery-1",
                "name": "交付批次一",
                "kind": "delivery",
                "timing": {
                    "raw": "2026-12-01",
                    "precision": "day",
                    "value": "2026-12-01",
                },
            },
            {
                "key": "payment-1",
                "name": "合同货款",
                "kind": "payment",
                "timing": {
                    "raw": "2027-01-15",
                    "precision": "day",
                    "value": "2027-01-15",
                },
            },
            {
                "key": "expires",
                "name": "模糊有效期",
                "kind": "expiry",
                "timing": {"raw": "before 2027", "precision": "unknown", "value": ""},
            },
        ],
        citations=[{"evidence_id": e["id"], "quote": quote}],
    )


def confirm(store, data):
    p = propose(store, ProposalInput(data=data, change_summary="测试登记"))
    r = approve(store, p["id"], "approve", "已核对虚构样例")
    return store.get(r["order_id"]), p


def test_dates_do_not_invent_day_or_confuse_before_year():
    assert OrderTime(raw="2027年前").bounds() == (None, None)
    assert OrderTime(raw="2027", precision="year", value="2027").bounds() == (
        date(2027, 1, 1),
        date(2027, 12, 31),
    )
    assert OrderTime(raw="2028-02", precision="month", value="2028-02").bounds()[
        1
    ] == date(2028, 2, 29)
    for payload in [
        {"raw": "2027年前", "precision": "year", "value": "2027"},
        {"raw": "before 2027", "precision": "year", "value": "2027"},
        {"raw": "未知", "precision": "unknown", "value": "2027-12-31"},
        {"raw": "bad", "precision": "day", "value": "2026-02-30"},
        {"raw": "bad", "precision": "range", "value": "2027-02-01/2026-01-01"},
    ]:
        with pytest.raises(ValidationError):
            OrderTime(**payload)


def test_proposal_confirmation_versions_and_company_links(store, source, buyer):
    d = draft(source, buyer)
    p = propose(store, ProposalInput(data=d, change_summary="new"))
    assert not store.list("business_order") and not graph(store)["edges"]
    r = approve(store, p["id"], "approve", "人工核对")
    o = store.get(r["order_id"])
    assert len(graph(store)["edges"]) == 2
    assert graph(store)["edges"][0]["source"] == CID
    assert graph(store)["edges"][1]["source"] == buyer["id"]
    assert o["id"] in {r["id"] for r in archive(store, CID)["records"]}
    assert store.belongs_to(o, {buyer["id"]})
    with pytest.raises(ValueError, match="处理"):
        approve(store, p["id"], "approve", "再次")
    assert len(store.list("order_version", o["id"])) == 1
    assert not store.list("relation")


def test_distinct_payer_recipient_flows(store, source, buyer):
    payer = create_party(store, PartyInput(name="Payment subsidiary"))
    recipient = create_party(store, PartyInput(name="Cloud operator"))
    d = draft(source, buyer).model_copy(
        update={"payer_id": payer["id"], "recipient_id": recipient["id"]}
    )
    confirm(store, d)
    g = graph(store)
    assert {(e["flow"], e["source"], e["target"]) for e in g["edges"]} == {
        ("goods", CID, recipient["id"]),
        ("cash", payer["id"], CID),
    }


def test_review_requires_resolved_parties_and_literal_evidence(store, source, buyer):
    d = draft(source, buyer)
    d.buyer_id = None
    p = propose(store, ProposalInput(data=d, change_summary="unresolved"))
    with pytest.raises(ValueError, match="匹配"):
        approve(store, p["id"], "approve", "test")
    d.citations[0].quote = "not actually present"
    with pytest.raises(ValueError, match="逐字"):
        propose(store, ProposalInput(data=d, change_summary="bad quote"))
    assert not store.list("business_order")


def test_history_only_then_known_and_stale_proposal_blocked(
    store, source, buyer, monkeypatch
):
    clock = ["2026-08-01T01:00:00+00:00"]
    monkeypatch.setattr(orders, "now", lambda: clock[0])
    o, _ = confirm(store, draft(source, buyer))
    changed = draft(source, buyer)
    changed.terms = "延期候选"
    p = propose(
        store,
        ProposalInput(
            order_id=o["id"],
            base_version=o["version_id"],
            data=changed,
            change_summary="变更",
        ),
    )
    alternative = draft(source, buyer)
    alternative.terms = "另一条变更"
    stale = propose(
        store,
        ProposalInput(
            order_id=o["id"],
            base_version=o["version_id"],
            data=alternative,
            change_summary="旧版本建议",
        ),
    )
    clock[0] = "2026-09-01T01:00:00+00:00"
    approve(store, p["id"], "approve", "新条款")
    assert not at_orders(store, datetime(2026, 7, 1, tzinfo=timezone.utc))
    past = at_orders(store, datetime(2026, 8, 15, tzinfo=timezone.utc))[0]
    assert past["data"]["terms"] == "" and past["version_id"] == o["version_id"]
    assert at_orders(store)[0]["data"]["terms"] == "延期候选"
    with pytest.raises(ValueError, match="旧版本"):
        approve(store, stale["id"], "approve", "不能覆盖")
    with pytest.raises(ValueError, match="时区"):
        at_orders(store, datetime(2026, 8, 1))


def test_deadline_thresholds_no_defaulting_to_breach(store, source, buyer):
    o, _ = confirm(store, draft(source, buyer))
    for days, kind in [
        (91, None),
        (90, "within_90"),
        (30, "within_30"),
        (7, "within_7"),
        (0, "in_window"),
        (-1, "due_check"),
    ]:
        values = [
            a
            for a in alerts(o, date(2026, 12, 1) - timedelta(days=days))
            if a["milestone_key"] == "delivery-1"
        ]
        assert (values[0]["kind"] if values else None) == kind
    a = alerts(o, date(2027, 2, 1))
    assert all("违约" not in x["kind"] for x in a)
    assert any(x["kind"] == "date_uncertain" for x in a)
    o["data"]["status"] = "cancelled"
    assert alerts(o) == []


def test_month_window_uses_uncertainty_not_first_day_overdue(store, source, buyer):
    d = draft(source, buyer)
    d.milestones[0].timing = OrderTime(
        raw="2027-01", precision="month", value="2027-01"
    )
    o, _ = confirm(store, d)
    assert (
        next(
            a
            for a in alerts(o, date(2027, 1, 20))
            if a["milestone_key"] == "delivery-1"
        )["kind"]
        == "in_window"
    )


def test_multiple_orders_and_proposal_dedup_reject(store, source, buyer):
    d = draft(source, buyer)
    o, p = confirm(store, d)
    assert (
        propose(store, ProposalInput(data=d, change_summary="repeat"))["id"] == p["id"]
    )
    d.reference = "TEST-002"
    d.title = "第二笔"
    confirm(store, d)
    assert len(graph(store)["orders"]) == 2 and len(graph(store)["edges"]) == 4
    d.reference = "TEST-003"
    p = propose(store, ProposalInput(data=d, change_summary="reject"))
    approve(store, p["id"], "reject", "没有足够证据")
    assert (
        len(at_orders(store)) == 2 and store.get(p["id"])["review_status"] == "rejected"
    )


def test_ai_extract_queued_and_never_confirmed(client, store, source, buyer):
    d = draft(source, buyer)
    j = start_scan(store, ScanInput(evidence_ids=[source[0]["id"]]))
    llm = SimpleNamespace(
        call=lambda *args, **kwargs: Extraction(
            proposals=[ProposalInput(data=d, change_summary="AI识别")]
        )
    )
    process_scan(store, j, llm)
    result = store.get(j["id"])
    assert result["status"] == "completed" and len(result["proposal_ids"]) == 1
    assert not at_orders(store)
    assert store.get(result["proposal_ids"][0])["origin"] == "ai"
    j2 = start_scan(store, ScanInput(evidence_ids=[source[0]["id"]]))
    j2 = store.update(j2["id"], automatic=True)
    process_scan(
        store,
        j2,
        SimpleNamespace(call=lambda *a, **kw: pytest.fail("unchanged should skip LLM")),
    )
    assert store.get(j2["id"])["status"] == "completed"
    assert "没有变化" in store.get(j2["id"])["gaps"][-1]


def test_one_click_discovery_collects_without_manual_order_or_file(
    client, store, monkeypatch
):
    from crew_stock import longbridge

    article = "NVIDIA supplies Example Buyer with chips under contract USD 100 million."

    class FakeCollector:
        def __init__(self, db, run):
            self.db, self.run = db, run

        def collect(self, topic, source_id=""):
            raw = (
                '{"list":[{"id":123,"title":"chip supply contract"}]}'
                if topic == "news"
                else '{"list":[]}'
                if topic == "filings"
                else article
            )
            return self.db.insert(
                {
                    "id": self.db.allocate("evd"),
                    "type": "evidence",
                    "parent_id": self.run["company_id"],
                    "topic": topic,
                    "source": "longbridge",
                    "acquisition_status": "success",
                    "blob": self.db.blob(raw.encode()),
                }
            )

    monkeypatch.setattr(longbridge, "Collector", FakeCollector)
    response = client.post(
        "/api/orders/scans",
        headers=H,
        json={"scope_id": CID, "pull_latest": True, "evidence_ids": []},
    )
    assert response.status_code == 202
    job = response.json()
    assert job["company_ids"] == [CID] and job["scope_name"]
    with pytest.raises(ValueError, match="相同范围"):
        start_scan(store, ScanInput(scope_id=CID, pull_latest=True))

    def model_call(*args, **kwargs):
        details = store.list("evidence", CID)
        detail = next(e for e in details if e["topic"] == "news_detail")
        data = OrderDraft(
            title="测试芯片供应合同",
            agreement_type="firm",
            supplier_id=CID,
            supplier_name="NVIDIA",
            buyer_name="Example Buyer",
            product="chips",
            amount=100,
            amount_scale="million",
            currency="USD",
            citations=[{"evidence_id": detail["id"], "quote": article}],
        )
        return Extraction(proposals=[ProposalInput(data=data, change_summary="新线索")])

    process_scan(store, job, SimpleNamespace(call=model_call))
    result = store.get(job["id"])
    assert result["status"] == "completed"
    assert len(result["proposal_ids"]) == 1
    assert result["evidence_ids"]
    assert not store.list("business_order")
    assert store.get(result["proposal_ids"][0])["review_status"] == "pending"

    listing = next(e for e in store.list("evidence", CID) if e["topic"] == "news")
    list_only = OrderDraft(
        title="仅标题的线索",
        agreement_type="intent",
        supplier_name="NVIDIA",
        buyer_name="Example Buyer",
        product="chips",
        citations=[{"evidence_id": listing["id"], "quote": "chip supply contract"}],
    )
    second = start_scan(store, ScanInput(scope_id=CID, pull_latest=True))
    process_scan(
        store,
        second,
        SimpleNamespace(
            call=lambda *a, **kw: Extraction(
                proposals=[ProposalInput(data=list_only, change_summary="仅标题")]
            )
        ),
    )
    assert not store.get(second["id"])["proposal_ids"]
    assert "标题仅为线索" in store.get(second["id"])["gaps"][1]


def test_discovery_scope_and_missing_company_validation(client, store):
    industry = store.list("industry")[0]
    job = start_scan(store, ScanInput(scope_id=industry["id"], pull_latest=True))
    assert job["company_ids"] == [CID]
    with pytest.raises(ValueError, match="同时"):
        start_scan(
            store, ScanInput(order_id="bso-000001", scope_id=CID, pull_latest=True)
        )
    with pytest.raises(ValueError, match="没有公司"):
        start_scan(
            store,
            ScanInput(
                scope_id=store.insert(
                    {
                        "id": store.allocate("ind"),
                        "type": "industry",
                        "display_name": "空行业",
                        "members": [],
                    }
                )["id"],
                pull_latest=True,
            ),
        )


def test_ai_fabricated_amount_or_date_rejected(client, store, source, buyer):
    d = draft(source, buyer)
    d.amount = 999
    j = start_scan(store, ScanInput(evidence_ids=[source[0]["id"]]))
    process_scan(
        store,
        j,
        SimpleNamespace(
            call=lambda *a, **kw: Extraction(
                proposals=[ProposalInput(data=d, change_summary="fake amount")]
            )
        ),
    )
    assert not store.list("order_proposal")
    assert "金额" in store.get(j["id"])["gaps"][0]


def test_ai_amount_scale_must_match_chinese_source(source, buyer):
    d = draft(source, buyer)
    d.milestones = []
    d.citations[0].quote = "合同总价为人民币 1000 万元。"
    d.amount = 1000
    d.currency = "人民币"
    d.amount_scale = "million"
    with pytest.raises(ValueError, match="数量级"):
        orders.validate_ai_numbers(d)
    d.amount_scale = "ten_thousand"
    orders.validate_ai_numbers(d)
    d.citations[0].quote = "合同总价为人民币 2 亿元。"
    d.amount = 2
    d.amount_scale = "hundred_million"
    orders.validate_ai_numbers(d)


def test_order_watch_due_and_local_sources(client, store, source, buyer, monkeypatch):
    o, _ = confirm(store, draft(source, buyer))
    calls = []
    monkeypatch.setattr(orders, "process_scan", lambda s, j: calls.append(j))
    watch(store, o["id"], WatchInput(enabled=False))
    assert not tick_orders(store)
    watch(store, o["id"], WatchInput(enabled=True, pull_latest=False))
    assert tick_orders(store) and len(calls) == 1
    assert calls[0]["order_id"] == o["id"] and calls[0]["pull_latest"] is False
    evidence, gaps = orders.scan_sources(store, calls[0])
    assert source[0]["id"] in {e["id"] for e in evidence}
    assert not gaps
    assert datetime.fromisoformat(
        store.list("order_watch")[0]["next_at"]
    ) > datetime.now(timezone.utc)


def test_order_api_contracts_and_historical_view(client, store, source, buyer):
    r = client.post(
        "/api/orders/proposals",
        headers=H,
        json=ProposalInput(data=draft(source, buyer), change_summary="API").model_dump(
            mode="json"
        ),
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    r = client.post(
        f"/api/orders/proposals/{pid}/review",
        headers=H,
        json={"decision": "approve", "note": "测试证据已核对"},
    )
    assert r.status_code == 200
    oid = r.json()["order_id"]
    detail = client.get("/api/orders/" + oid).json()
    assert len(detail["versions"]) == 1
    assert len(client.get("/api/orders?scope_id=" + CID).json()["orders"]) == 1
    assert client.get("/api/orders?at=2000-01-01T00:00:00Z").json()["orders"] == []
    assert client.get("/api/orders?at=2026-01-01T00:00:00").status_code == 400
    assert (
        client.put(
            "/api/orders/" + oid + "/watch",
            headers=H,
            json={"enabled": True, "pull_latest": False},
        ).status_code
        == 200
    )
    assert (
        client.post("/api/orders/parties", json={"name": "without header"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/orders/parties", headers=H, json={"name": "Example Buyer"}
        ).status_code
        == 400
    )


def test_ai_failure_and_restart_recovery(client, store, source):
    j = start_scan(store, ScanInput(evidence_ids=[source[0]["id"]]))

    def fail(*args, **kwargs):
        raise RuntimeError("network error")

    process_scan(store, j, SimpleNamespace(call=fail))
    assert store.get(j["id"])["status"] == "failed"
    store.update(j["id"], status="analyzing")
    from crew_stock.extended_api import recover_extensions

    recover_extensions(store)
    assert store.get(j["id"])["status"] == "interrupted"
    assert store.get(source[0]["id"])["blob"]


def test_duplicate_reference_cannot_create_two_confirmed_orders(store, source, buyer):
    d = draft(source, buyer)
    confirm(store, d)
    d.terms = "Different proposal but same contract reference"
    p = propose(store, ProposalInput(data=d, change_summary="same contract"))
    with pytest.raises(ValueError, match="已经建档"):
        approve(store, p["id"], "approve", "duplicate")
    assert len(at_orders(store)) == 1
