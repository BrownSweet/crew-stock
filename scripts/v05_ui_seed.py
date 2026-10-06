"""Create one unconfirmed fictitious commercial-order candidate for UI verification."""

import os

from crew_stock.config import require_test_database
from crew_stock.orders import (
    OrderDraft,
    PartyInput,
    ProposalInput,
    create_party,
    propose,
)
from crew_stock.store import Store

require_test_database()

if os.getenv("CREW_STOCK_DISABLE_WORKER") != "1":
    raise RuntimeError("Only run in a disposable worker-disabled test container")
s = Store()
s.initialize()
buyer = create_party(s, PartyInput(name="Example Buyer（虚构验收）"))
raw = "FICTITIOUS SOFTWARE TEST: NVIDIA supplies chips to Example Buyer. USD 100 million. Delivery 2026-12-01; payment 2027-01-15. Valid before 2027: exact meaning unconfirmed."
e = s.insert(
    {
        "id": s.allocate("evd"),
        "type": "evidence",
        "parent_id": "cmp-000001",
        "source": "user",
        "topic": "document_parse",
        "display_name": "虚构订单验收文本",
        "acquisition_status": "success",
        "blob": s.blob(raw.encode()),
    }
)
d = OrderDraft(
    title="虚构 UI 测试：芯片供货与货款",
    reference="TEST-UI-001",
    agreement_type="firm",
    supplier_id="cmp-000001",
    supplier_name="NVIDIA",
    buyer_id=buyer["id"],
    buyer_name=buyer["name"],
    product="测试芯片",
    amount=100,
    currency="USD",
    amount_scale="million",
    status="active",
    milestones=[
        {
            "key": "delivery",
            "name": "测试交付",
            "kind": "delivery",
            "timing": {"raw": "2026-12-01", "precision": "day", "value": "2026-12-01"},
        },
        {
            "key": "payment",
            "name": "测试货款",
            "kind": "payment",
            "timing": {"raw": "2027-01-15", "precision": "day", "value": "2027-01-15"},
        },
        {
            "key": "expiry",
            "name": "模糊有效期",
            "kind": "expiry",
            "timing": {"raw": "before 2027", "precision": "unknown", "value": ""},
        },
    ],
    citations=[{"evidence_id": e["id"], "quote": raw}],
    gaps=["虚构软件验收，非真实商业披露"],
)
p = propose(
    s,
    ProposalInput(
        data=d, change_summary="由隔离脚本登记的虚构候选，仅测试人工审核、图和时间轴"
    ),
)
print(
    {
        "candidate": p["id"],
        "evidence": e["id"],
        "confirmed_orders": len(s.list("business_order")),
    }
)
