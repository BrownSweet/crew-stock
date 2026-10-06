from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from crew_stock import app as module
from crew_stock import config
from crew_stock.assets import process_asset, save_upload
from crew_stock.decisions import (
    BudgetInput,
    PaperAccountInput,
    PaperBuy,
    PlanProposal,
    PlanRequest,
    account_numbers,
    calculate_budget,
    cancel_order,
    create_account,
    match_snapshot,
    paper_buy,
    paper_close,
    process_plan,
    start_plan,
    tick_decisions,
)
from crew_stock.filing import Classification, assign_asset, classify_asset
from crew_stock.store import now
from crew_stock.workspace import archive

CID = "cmp-000001"
H = {"X-Crew-Stock": "1"}


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(config, "DATA", store.root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-test-key")
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


def proposal():
    return {
        "thesis": "测试用假设，不是真实建议",
        "business_to_earnings": "假设毛利改善推动每股盈利",
        "earnings_to_valuation": "EPS × PE 的情景假设",
        "priced_in": "部分预期反映",
        "decision": "conditional_buy",
        "horizon_months": 12,
        "currency": "USD",
        "scenarios": [
            {
                "name": n,
                "forward_eps": eps,
                "pe": 20,
                "assumptions": "测试假设",
                "evidence_ids": ["evd-test"],
            }
            for n, eps in [("bear", 4), ("base", 7), ("bull", 9)]
        ],
        "entry_low": "95",
        "entry_high": "100",
        "stop_loss": "90",
        "take_profit": "130",
        "catalysts": ["测试催化"],
        "invalidation": ["盈利不达标"],
        "counter_evidence": [],
        "gaps": [],
        "evidence_ids": ["evd-test"],
    }


@pytest.fixture
def plan(store):
    return store.insert(
        {
            "id": store.allocate("pln"),
            "type": "trade_plan",
            "parent_id": CID,
            "symbol": "NVDA.US",
            "proposal": proposal(),
            "market": {
                "currency": "USD",
                "lot_size": 1,
                "price": "101",
                "timestamp": now(),
            },
            "scenario_prices": {"bear": "80", "base": "140", "bull": "180"},
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "mode": "paper_only",
        }
    )


@pytest.fixture
def account(store):
    return create_account(
        store,
        PaperAccountInput(
            name="虚拟测试", initial_cash="10000", fee_bps=5, slippage_bps=5
        ),
    )


def buy(store, plan, account, quantity=5, **kwargs):
    p = PaperBuy(
        account_id=account["id"],
        limit_price="100",
        quantity=quantity,
        request_id="test-request-001",
        confirm_plan=True,
        **kwargs,
    )
    return paper_buy(store, plan["id"], p), p


def tick(price="99", volume=2, **kwargs):
    return {
        "symbol": "NVDA.US",
        "price": price,
        "volume": volume,
        "timestamp": now(),
        "trade_status": "Normal",
        "currency": "USD",
        "quote_evidence_id": "evd-market",
        **kwargs,
    }


def test_budget_lot_cash_currency_and_expiry(store, plan, account):
    p = BudgetInput(account_id=account["id"], limit_price=100)
    budget = calculate_budget(plan, account, [], p)
    assert budget["quantity"] == 9
    assert Decimal(budget["planned_loss"]) <= 100
    plan["market"]["lot_size"] = 10
    assert calculate_budget(plan, account, [], p)["quantity"] == 0
    account["currency"] = "HKD"
    with pytest.raises(ValueError, match="币种"):
        calculate_budget(plan, account, [], p)
    account["currency"] = "USD"
    plan["expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="过期"):
        calculate_budget(plan, account, [], p)


def test_reject_malformed_and_unconfirmed(store, plan, account):
    bad = proposal()
    bad["stop_loss"] = "101"
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(bad)
    bad = proposal()
    bad["scenarios"][0]["forward_eps"] = 1000
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(bad)
    with pytest.raises(ValueError, match="超过"):
        buy(store, plan, account, 100)
    p = PaperBuy(
        account_id=account["id"],
        limit_price=100,
        quantity=1,
        request_id="request-test",
        confirm_plan=False,
    )
    with pytest.raises(ValueError, match="确认"):
        paper_buy(store, plan["id"], p)
    assert not store.list("paper_order")


def test_pending_cash_reservation_idempotency_cancel(store, plan, account):
    order, p = buy(store, plan, account)
    assert order["status"] == "pending"
    assert order["budget_snapshot"]["quantity"] == 5
    assert order["budget_snapshot"]["max_quantity"] == 9
    assert order["budget_snapshot"]["capital"] == "500"
    assert store.get(account["id"])["cash"] == "10000"
    assert paper_buy(store, plan["id"], p)["id"] == order["id"]
    assert len(store.list("paper_order")) == 1
    assert account_numbers(account, [order])[1] == Decimal("9499.75")
    with pytest.raises(ValueError, match="不同"):
        paper_buy(store, plan["id"], p.model_copy(update={"quantity": 4}))
    cancelled = cancel_order(store, order["id"])
    assert account_numbers(account, [cancelled])[1] == Decimal("10000")
    assert store.list("paper_decision")[0]["plan_snapshot"] == plan


def test_partial_volume_never_reused_and_slippage_limit(store, plan, account):
    order, _ = buy(store, plan, account)
    assert (
        match_snapshot(store, account["id"], tick("100"))["filled"] == 0
    )  # adverse slip crosses limit
    market = tick()
    assert match_snapshot(store, account["id"], market)["filled"] == 2
    assert match_snapshot(store, account["id"], market)["filled"] == 0
    current = store.get(account["id"])
    assert current["positions"]["NVDA.US"]["quantity"] == 2
    assert store.get(order["id"])["status"] == "partially_filled"
    assert (
        Decimal(current["cash"])
        == Decimal("10000") - Decimal("99") * Decimal("1.0005") * Decimal("1.0005") * 2
    )
    assert match_snapshot(store, account["id"], tick(volume=100))["filled"] == 3
    assert store.get(order["id"])["status"] == "filled"
    assert len(store.list("paper_fill")) == 2


def test_no_stale_preorder_halted_or_wrong_currency_fill(store, plan, account):
    before = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    buy(store, plan, account)
    for market in [
        tick(timestamp=before),
        tick(timestamp=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()),
        tick(trade_status="Halted"),
    ]:
        assert match_snapshot(store, account["id"], market)["filled"] == 0
    with pytest.raises(ValueError, match="币种"):
        match_snapshot(store, account["id"], tick(currency="HKD"))
    assert not store.list("paper_fill")


def test_expiry_releases_cash_even_without_fresh_market(store, plan, account):
    order, _ = buy(store, plan, account)
    store.update(
        order["id"],
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    match_snapshot(
        store,
        account["id"],
        tick(timestamp=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat()),
    )
    assert store.get(order["id"])["status"] == "expired"
    assert account_numbers(account, store.list("paper_order"))[1] == 10000


def test_stop_creates_future_exit_then_realizes_pnl(store, plan, account):
    order, _ = buy(store, plan, account)
    match_snapshot(store, account["id"], tick(volume=2))
    stop = tick(price="89", volume=100)
    result = match_snapshot(store, account["id"], stop)
    assert result["filled"] == 0 and "触发" in result["reason"]
    assert store.get(order["id"])["status"] == "cancelled"
    exit_order = store.list("paper_order")[0]
    assert exit_order["side"] == "sell" and exit_order["trigger"] == "stop_loss"
    assert match_snapshot(store, account["id"], stop)["filled"] == 0
    sold = match_snapshot(store, account["id"], tick(price="88", volume=100))
    assert sold["filled"] == 2
    a = store.get(account["id"])
    assert a["positions"]["NVDA.US"]["quantity"] == 0
    assert Decimal(a["cash"]) == 10000 + Decimal(a["realized_pnl"])
    assert Decimal(a["realized_pnl"]) < 0
    assert store.get(exit_order["id"])["status"] == "filled"


def test_manual_close_cancels_remaining_buy_and_prevents_duplicate(
    store, plan, account
):
    order, _ = buy(store, plan, account)
    match_snapshot(store, account["id"], tick())
    paper_close(store, account["id"], "NVDA.US")
    assert store.get(order["id"])["status"] == "cancelled"
    with pytest.raises(ValueError, match="已有平仓"):
        paper_close(store, account["id"], "NVDA.US")


def test_pending_other_symbols_cannot_overspend(store, plan, account):
    buy(store, plan, account)
    a = store.get(account["id"])
    a["cash"] = "520"
    alt = {**plan, "symbol": "OTHER.US"}
    p = BudgetInput(
        account_id=a["id"], limit_price=100, risk_percent=5, position_percent=50
    )
    assert calculate_budget(alt, a, store.list("paper_order"), p)["quantity"] == 0


def test_paper_api_and_journal(client, store, plan):
    a = client.post(
        "/api/decisions/accounts",
        headers=H,
        json={"name": "API模拟", "initial_cash": "10000"},
    ).json()
    payload = {"account_id": a["id"], "limit_price": "100"}
    assert (
        client.post(
            f"/api/decisions/plans/{plan['id']}/budget", headers=H, json=payload
        ).json()["quantity"]
        == 9
    )
    r = client.post(
        f"/api/decisions/plans/{plan['id']}/orders",
        headers=H,
        json={
            **payload,
            "quantity": 3,
            "request_id": "api-test-request",
            "confirm_plan": True,
        },
    )
    assert r.status_code == 201
    order = r.json()
    assert (
        client.get("/api/decisions/accounts").json()[0]["available_cash"] == "9699.8500"
    )
    assert (
        client.post(f"/api/decisions/accounts/{a['id']}/refresh", headers=H).status_code
        == 202
    )
    assert (
        client.put(
            f"/api/decisions/accounts/{a['id']}/tracking",
            headers=H,
            json={"enabled": False},
        ).status_code
        == 200
    )
    journal = client.get("/api/decisions?company_id=" + CID).json()["paper_decision"][0]
    r = client.post(
        f"/api/decisions/journal/{journal['id']}/review",
        headers=H,
        json={
            "thesis_change": "未变",
            "process_review": "已检查委托仍未成交",
            "next_action": "继续观察",
        },
    )
    assert r.status_code == 201 and r.json()["order_snapshot"]["status"] == "pending"
    assert (
        client.post(
            f"/api/decisions/orders/{order['id']}/cancel", headers=H
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/decisions/accounts", json={"name": "无令牌", "initial_cash": 100}
        ).status_code
        == 403
    )


def test_plan_worker_validates_refs_and_computes_prices(client, store, monkeypatch):
    from crew_stock import decisions

    evidence = store.insert(
        {
            "id": "evd-test",
            "type": "evidence",
            "parent_id": CID,
            "acquisition_status": "success",
            "blob": store.blob(b"Example EPS: 7. Assumption only."),
        }
    )
    monkeypatch.setattr(
        decisions,
        "market_snapshot",
        lambda *_: {
            "currency": "USD",
            "evidence_ids": ["evd-test"],
            "price": "100",
            "timestamp": now(),
            "lot_size": 1,
        },
    )
    monkeypatch.setattr(decisions.Collector, "collect", lambda *_: evidence)
    p = PlanRequest(company_id=CID, symbol="NVDA.US")
    job = start_plan(store, p)
    with pytest.raises(ValueError, match="正在生成"):
        start_plan(store, p)
    process_plan(
        store,
        job,
        SimpleNamespace(call=lambda *a, **kw: PlanProposal.model_validate(proposal())),
    )
    assert store.get(job["id"])["status"] == "completed"
    plan = store.list("trade_plan")[0]
    assert plan["scenario_prices"]["base"] == "140"
    assert plan["proposal"]["decision"] == "conditional_buy"
    assert plan["evidence_snapshot"][0]["blob"] == evidence["blob"]
    bad = proposal()
    bad["evidence_ids"] = ["evd-fake"]
    job = start_plan(store, p)
    process_plan(
        store,
        job,
        SimpleNamespace(call=lambda *a, **kw: PlanProposal.model_validate(bad)),
    )
    assert store.get(job["id"])["status"] == "failed"
    assert len(store.list("trade_plan")) == 1


def parsed_file(store, monkeypatch):
    from crew_stock import assets

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-test-key")
    monkeypatch.setattr(
        assets,
        "describe_page",
        lambda text, picture, model: (
            {"transcription": text, "summary": "测试资料", "uncertainties": []},
            {},
        ),
    )
    raw = b"NVIDIA NVDA.US and Example Memory TEST.US discuss storage chips. NewCo has no dossier."
    job = save_upload(store, None, "unrelated-name.txt", raw)
    process_asset(store, job, parse=assets.describe_page)
    return store.get(job["id"])


def classifier(job, other):
    eid = job["evidence_ids"][0]
    return Classification(
        document_type="industry_data",
        title="存储公司观察",
        tags=["存储"],
        matches=[
            {
                "target_id": CID,
                "target_type": "company",
                "name": "NVIDIA",
                "confidence": 0.99,
                "reason": "正文含公司与代码",
                "evidence_id": eid,
                "quote": "NVIDIA NVDA.US",
            },
            {
                "target_id": other["id"],
                "target_type": "company",
                "name": "Example Memory",
                "confidence": 0.95,
                "reason": "正文包含",
                "evidence_id": eid,
                "quote": "Example Memory TEST.US",
            },
            {
                "target_id": None,
                "target_type": "company",
                "name": "NewCo",
                "confidence": 0.99,
                "reason": "未建档",
                "evidence_id": eid,
                "quote": "NewCo has no dossier.",
            },
            {
                "target_id": CID,
                "target_type": "company",
                "name": "伪引用",
                "confidence": 1,
                "reason": "虚构",
                "evidence_id": eid,
                "quote": "正文没有这句话",
            },
        ],
    )


def test_standalone_classification_multi_dossier_no_copy(client, store, monkeypatch):
    other = store.create_company(
        {"name": "Example Memory", "symbols": ["TEST.US"], "purpose": "test"}
    )
    job = parsed_file(store, monkeypatch)
    originals = {e["id"]: e.get("blob") for e in store.list("evidence")}
    classify_asset(
        store, job, SimpleNamespace(call=lambda *a, **kw: classifier(job, other))
    )
    updated = store.get(job["id"])
    assert updated["classification_status"] == "completed"
    assert set(updated["target_ids"]) == {CID, other["id"]}
    assert len(updated["classification"]["matches"]) == 3
    assert updated["classification"]["matches"][-1]["status"] == "new_candidate"
    assert len(store.list("company")) == 2
    assert originals == {e["id"]: e.get("blob") for e in store.list("evidence")}
    for cid in [CID, other["id"]]:
        assert job["source_document_id"] in {
            x["id"] for x in store.list("source_document", cid)
        }
        assert job["id"] in {x["id"] for x in archive(store, cid)["records"]}
        assert (
            client.post(
                f"/api/workspace/documents/{job['source_document_id']}/analyze?company_id={cid}",
                headers=H,
            ).status_code
            == 202
        )
    unrelated = store.create_company(
        {"name": "Other", "symbols": ["OTHER.US"], "purpose": "test"}
    )
    assert (
        client.post(
            f"/api/workspace/documents/{job['source_document_id']}/analyze?company_id={unrelated['id']}",
            headers=H,
        ).status_code
        == 400
    )


def test_manual_links_survive_reclassification_and_can_be_cleared(
    client, store, monkeypatch
):
    other = store.create_company(
        {"name": "Example Memory", "symbols": ["TEST.US"], "purpose": "test"}
    )
    job = parsed_file(store, monkeypatch)
    assign_asset(store, job["id"], [other["id"]], "人工核对")
    classify_asset(
        store, job, SimpleNamespace(call=lambda *a, **kw: classifier(job, other))
    )
    assert store.get(job["id"])["target_ids"] == [other["id"]]
    assert (
        client.put(
            f"/api/assets/{job['id']}/links",
            headers=H,
            json={"target_ids": [], "note": "取消关联"},
        ).status_code
        == 200
    )
    assert not store.list("source_document", other["id"])
    assert len(store.list("asset_classification_review", job["id"])) == 2
    assert client.get("/api/assets").json()[0]["id"] == job["id"]


def test_upload_without_target_api(client):
    r = client.post(
        "/api/assets",
        headers=H,
        files={"file": ("standalone.txt", b"NVIDIA NVDA.US test")},
    )
    assert r.status_code == 202 and r.json()["parent_id"] is None


def test_refresh_worker_is_opt_in(store, account, monkeypatch):
    from crew_stock import decisions

    calls = []
    monkeypatch.setattr(decisions, "refresh_paper", lambda *args: calls.append(args[1]))
    assert not tick_decisions(store) and not calls
    store.update(account["id"], refresh_requested=True)
    assert tick_decisions(store) and calls == [account["id"]]
    assert not store.get(account["id"])["refresh_requested"]
    assert not tick_decisions(store)
