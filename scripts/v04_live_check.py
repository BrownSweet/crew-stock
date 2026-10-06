"""Opt-in live verification. Read configured credentials; write only to a temp store.

Run with CREWAI_STORAGE_DIR=/tmp/crewai. Makes read-only Longbridge calls and three
DeepSeek requests. Never creates a paper account or broker order.
"""

import json
import tempfile
from pathlib import Path

from crew_stock.assets import process_asset, save_upload
from crew_stock.config import require_test_database
from crew_stock.decisions import PlanRequest, process_plan, start_plan
from crew_stock.filing import classify_asset
from crew_stock.store import Store

require_test_database()

with tempfile.TemporaryDirectory(prefix="v04-live-") as path:
    store = Store(Path(path))
    store.initialize()
    job = save_upload(
        store,
        None,
        "classification-sample.txt",
        b"TEST DOCUMENT - fictional scenario, not company disclosure. NVIDIA NVDA.US is the subject of this research note. We study the relationship between GPU demand and storage-chip supply; there are no financial predictions here.",
    )
    process_asset(store, job)
    job = store.get(job["id"])
    assert job["status"] == "completed", job.get("error")
    classify_asset(store, job)
    job = store.get(job["id"])
    print(
        json.dumps(
            {
                "stage": "live_classification",
                "status": job.get("classification_status"),
                "targets": job.get("target_ids"),
                "match_count": len(job.get("classification", {}).get("matches", [])),
            }
        ),
        flush=True,
    )
    assert job["classification_status"] == "completed"
    assert "cmp-000001" in job["target_ids"]
    planjob = start_plan(
        store,
        PlanRequest(
            company_id="cmp-000001",
            symbol="NVDA.US",
            question="Live integration test: use only actual retrieved data and explicitly labelled assumptions. Explain market data limitations; return watch if valuation evidence is insufficient. No orders.",
        ),
    )
    process_plan(store, planjob)
    result = store.get(planjob["id"])
    print(
        json.dumps(
            {
                "stage": "live_plan",
                "status": result["status"],
                "error": result.get("error"),
            }
        ),
        flush=True,
    )
    assert result["status"] == "completed"
    plan = store.get(result["plan_id"])
    print(
        json.dumps(
            {
                "decision": plan["proposal"]["decision"],
                "scenarios": len(plan["proposal"]["scenarios"]),
                "evidence_count": len(plan["evidence_ids"]),
                "currency": plan["market"]["currency"],
                "market_timestamp": plan["market"]["timestamp"],
                "gaps": plan["proposal"]["gaps"],
                "paper_orders": len(store.list("paper_order")),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
