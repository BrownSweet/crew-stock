"""Seed explicitly fictitious plan in a disposable UI test container only."""

import os
from datetime import datetime, timedelta, timezone

from crew_stock.config import require_test_database
from crew_stock.store import Store, now

require_test_database()

if os.getenv("CREW_STOCK_DISABLE_WORKER") != "1":
    raise RuntimeError(
        "UI fixtures require an explicitly worker-disabled disposable container"
    )

s = Store()
s.initialize()
e = s.insert(
    {
        "id": s.allocate("evd"),
        "type": "evidence",
        "parent_id": "cmp-000001",
        "source": "user",
        "topic": "ui_test",
        "blob": s.blob(b"FICTITIOUS UI TEST EPS 7 PE 20; NOT RESEARCH."),
        "acquisition_status": "success",
        "captured_at": now(),
    }
)
p = {
    "thesis": "虚构 UI 验收方案，不是真实研究或建议",
    "business_to_earnings": "测试经营假设",
    "earnings_to_valuation": "测试 EPS × PE",
    "priced_in": "未知，仅测试",
    "decision": "conditional_buy",
    "horizon_months": 12,
    "currency": "USD",
    "scenarios": [
        {
            "name": n,
            "forward_eps": eps,
            "pe": "20",
            "assumptions": "虚构测试假设",
            "evidence_ids": [e["id"]],
        }
        for n, eps in [("bear", "4"), ("base", "7"), ("bull", "9")]
    ],
    "entry_low": "95",
    "entry_high": "100",
    "stop_loss": "90",
    "take_profit": "130",
    "catalysts": ["测试条件"],
    "invalidation": ["测试失效条件"],
    "counter_evidence": [],
    "gaps": ["UI 测试样例不能用于投资"],
    "evidence_ids": [e["id"]],
}
s.insert(
    {
        "id": s.allocate("pln"),
        "type": "trade_plan",
        "parent_id": "cmp-000001",
        "symbol": "NVDA.US",
        "proposal": p,
        "market": {
            "symbol": "NVDA.US",
            "currency": "USD",
            "lot_size": 1,
            "price": "101",
            "timestamp": now(),
            "price_basis": "虚构测试价格",
        },
        "scenario_prices": {"bear": "80", "base": "140", "bull": "180"},
        "reward_risk": "3",
        "model": "UI test fixture",
        "evidence_ids": [e["id"]],
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "mode": "paper_only",
    }
)
print("Fictitious test plan seeded; no account/order created.")
