"""Opt-in DeepSeek commercial-order extraction check in a temporary store.
Reads existing configuration; never confirms a fictional contract or writes formal data.
"""

import json
import tempfile
from pathlib import Path

from crew_stock.config import require_test_database
from crew_stock.orders import (
    PartyInput,
    ScanInput,
    create_party,
    process_scan,
    start_scan,
)
from crew_stock.store import Store

require_test_database()

with tempfile.TemporaryDirectory(prefix="v05-live-") as path:
    s = Store(Path(path))
    s.initialize()
    create_party(
        s, PartyInput(name="Example Buyer", notes="Fictitious software test party")
    )
    raw = 'FICTIONAL CONTRACT FOR SOFTWARE VERIFICATION ONLY; NOT A REAL COMMERCIAL DISCLOSURE. In this fictional example, NVIDIA (NVDA.US) and Example Buyer have a firm chip supply order TEST-001. Contract amount: USD 100 million. Supplier: NVIDIA. Buyer and payer: Example Buyer. Planned delivery: 2026-12-01. Planned payment: 2027-01-15. The phrase "before 2027" is ambiguous and must remain an uncertain expiry term. There is no evidence that delivery or payment has occurred.'
    e = s.insert(
        {
            "id": s.allocate("evd"),
            "type": "evidence",
            "parent_id": "cmp-000001",
            "source": "user",
            "topic": "document_parse",
            "acquisition_status": "success",
            "blob": s.blob(raw.encode()),
        }
    )
    j = start_scan(s, ScanInput(evidence_ids=[e["id"]]))
    process_scan(s, j)
    result = s.get(j["id"])
    print(
        json.dumps(
            {k: result.get(k) for k in ["status", "proposal_ids", "gaps", "error"]},
            ensure_ascii=False,
        ),
        flush=True,
    )
    assert result["status"] == "completed"
    assert result["proposal_ids"], (
        "No extracted candidate; inspect gaps, do not claim success."
    )
    for pid in result["proposal_ids"]:
        p = s.get(pid)
        print(
            json.dumps(
                {
                    "review_status": p["review_status"],
                    "amount": p["data"]["amount"],
                    "scale": p["data"]["amount_scale"],
                    "milestones": p["data"]["milestones"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        assert p["review_status"] == "pending"
        for m in p["data"]["milestones"]:
            if "before 2027" in m["timing"]["raw"]:
                assert (
                    m["timing"]["precision"] == "unknown" and not m["timing"]["value"]
                )
    assert not s.list("business_order")
    print(
        "No confirmed orders or broker activity; temporary data discarded.", flush=True
    )
