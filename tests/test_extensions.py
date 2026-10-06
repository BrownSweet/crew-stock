import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from crew_stock import app as module
from crew_stock import config
from crew_stock.assets import pages, process_asset, save_upload
from crew_stock.extended_api import recover_extensions
from crew_stock.industries import (
    IndustryInput,
    IndustryRunInput,
    save_industry,
    start_batch,
    tick_industries,
)
from crew_stock.influence import (
    RelationInput,
    SimulationInput,
    graph,
    review_relation,
    save_relation,
    simulate,
)

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
def second(store):
    return store.create_company(
        {"name": "Test peer", "symbols": ["TEST.US"], "purpose": "test"}
    )


@pytest.fixture
def source(store):
    blob = store.blob(b"Test peer supplies chips to NVIDIA. Risk source 2026-09-20.")
    return store.insert(
        {
            "id": store.allocate("evd"),
            "type": "evidence",
            "parent_id": CID,
            "source": "user",
            "blob": blob,
            "acquisition_status": "success",
        }
    )


def rel(a, b, ev):
    factors = [
        "criticality",
        "substitutability",
        "concentration",
        "buffer_risk",
        "exposure",
        "geopolitical_risk",
    ]
    return RelationInput(
        upstream_id=a,
        downstream_id=b,
        input_category="chips",
        rationale="test supply dependency",
        as_of="2026-09-20",
        confidence=0.8,
        scores={k: {"value": 0.5, "reason": "scenario assumption"} for k in factors},
        citations=[
            {"evidence_id": ev["id"], "quote": "Test peer supplies chips to NVIDIA."}
        ],
    )


def test_industry_membership_versions_and_api(client, store, second):
    payload = {
        "display_name": "存储行业",
        "watch_points": "供需、库存",
        "members": [
            {"company_id": CID, "role": "观察对象"},
            {"company_id": second["id"], "role": "生产"},
        ],
    }
    r = client.post("/api/industries", headers=H, json=payload)
    assert r.status_code == 201
    iid = r.json()["id"]
    r = client.put(
        "/api/industries/" + iid,
        headers=H,
        json={**payload, "members": [{"company_id": second["id"]}]},
    )
    assert r.status_code == 200
    assert (
        store.list("industry_version", iid)[0]["before"]["members"]
        == payload["members"]
    )
    d = client.get("/api/industries/" + iid).json()
    assert d["companies"][0]["id"] == second["id"]
    assert len(store.list("company")) == 2
    assert (
        client.post(
            "/api/industries",
            headers=H,
            json={**payload, "members": [{"company_id": CID}, {"company_id": CID}]},
        ).status_code
        == 422
    )
    assert client.get("/api/industries/" + CID).status_code == 400


def test_batch_dispatch_snapshot_failure_and_schedule(client, store, second):
    ind = save_industry(
        store,
        IndustryInput(
            display_name="行业",
            members=[{"company_id": CID}, {"company_id": second["id"]}],
        ),
    )
    batch = start_batch(store, ind["id"], IndustryRunInput(mode="collect"))
    save_industry(
        store,
        IndustryInput(display_name="行业", members=[{"company_id": second["id"]}]),
        ind["id"],
    )
    tick_industries(store, module.enqueue)
    batch = store.get(batch["id"])
    assert batch["next_member"] == 1
    assert store.get(batch["run_ids"][0])["parent_id"] == CID
    tick_industries(store, module.enqueue)
    assert store.get(batch["id"])["next_member"] == 1
    store.update(batch["run_ids"][0], status="failed")
    tick_industries(store, module.enqueue)
    batch = store.get(batch["id"])
    assert batch["next_member"] == 2
    store.update(batch["run_ids"][1], status="completed")
    tick_industries(store, module.enqueue)
    assert store.get(batch["id"])["status"] == "completed_with_warnings"
    r = client.put(
        f"/api/industries/{ind['id']}/schedule",
        headers=H,
        json={"interval_hours": 24, "mode": "collect", "enabled": True},
    )
    assert r.status_code == 200
    store.update(
        r.json()["id"],
        next_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    tick_industries(store, module.enqueue)
    assert len(store.list("industry_run", ind["id"])) == 2
    tick_industries(store, module.enqueue)
    assert len(store.list("industry_run", ind["id"])) == 2


def test_batch_summary_report_and_member_refs(client, store, second):
    ind = save_industry(
        store, IndustryInput(display_name="行业", members=[{"company_id": CID}])
    )
    batch = start_batch(store, ind["id"], IndustryRunInput())
    tick_industries(store, module.enqueue)
    batch = store.get(batch["id"])
    store.update(batch["run_ids"][0], status="completed")

    def summary(s, b):
        assert b["run_ids"] == batch["run_ids"]
        return s.insert(
            {
                "id": s.allocate("irp"),
                "type": "industry_report",
                "parent_id": ind["id"],
                "report": {"summary": "test"},
            }
        )

    tick_industries(store, module.enqueue, summary)
    assert store.get(batch["id"])["status"] == "completed"
    assert store.get(batch["id"])["report_id"].startswith("irp-")


def test_asset_original_separate_from_ai_and_scope(client, store):
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(output, format="PNG")
    raw = output.getvalue()
    r = client.post(
        "/api/assets",
        headers=H,
        data={"target_id": CID},
        files={"file": ("chart.png", raw, "image/png")},
    )
    assert r.status_code == 202
    job = r.json()
    assert client.get(f"/api/assets/{job['id']}/original").content == raw

    def parser(text, picture, model):
        assert picture == raw and model == "deepseek-flash"
        return {
            "transcription": "red square",
            "summary": "红色方块",
            "uncertainties": ["无日期"],
        }, {"total_tokens": 10}

    process_asset(store, job, parser)
    a = store.get(job["id"])
    assert a["status"] == "completed"
    evidence = store.get(a["evidence_ids"][0])
    assert evidence["source"] == "user"
    assert evidence["blob"] != evidence["original_blob"]
    doc = store.get(a["source_document_id"])
    assert doc["evidence_ids"] == a["evidence_ids"]
    assert (
        client.post(
            f"/api/workspace/documents/{doc['id']}/analyze", headers=H
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/api/assets",
            headers=H,
            data={"target_id": CID, "end_page": 21},
            files={"file": ("chart.png", raw)},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/assets",
            headers=H,
            data={"target_id": CID},
            files={"file": ("bad.exe", raw)},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/assets", data={"target_id": CID}, files={"file": ("chart.png", raw)}
        ).status_code
        == 403
    )


def test_pdf_coverage_text_and_failure_survive(client, store):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(3):
        doc.new_page(100, 100)
    out = io.BytesIO()
    doc.save(out)
    doc.close()
    job = save_upload(store, CID, "sample.pdf", out.getvalue(), 2, 2)
    process_asset(
        store,
        job,
        lambda *_: (
            {"transcription": "", "summary": "空白页", "uncertainties": ["空白"]},
            None,
        ),
    )
    a = store.get(job["id"])
    assert a["status"] == "completed_with_warnings"
    assert a["total_pages"] == 3 and a["pages"][0]["page_or_sheet"] == "2"
    bad = save_upload(store, CID, "broken.pdf", b"not a pdf", 1, 1)
    process_asset(store, bad)
    assert store.get(bad["id"])["status"] == "failed"
    assert store.read_blob(bad["original_blob"]) == b"not a pdf"
    assert client.post(f"/api/assets/{bad['id']}/retry", headers=H).status_code == 202


def test_xlsx_formula_is_not_executed():
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["revenue", "=SUM(1,2)"])
    out = io.BytesIO()
    wb.save(out)
    result = list(pages(out.getvalue(), ".xlsx", 1, 10))
    assert "=SUM(1,2)" in result[0][1]
    assert result[0][2] is None


def test_graph_confirmation_provenance_and_versions(client, store, second, source):
    a = save_relation(store, rel(second["id"], CID, source))
    assert graph(store, CID)["edges"] == []
    assert len(graph(store, CID, proposals=True)["edges"]) == 1
    bad = rel(CID, second["id"], source).model_copy(update={"citations": []})
    # Normal validated API rejects missing evidence.
    assert (
        client.post(
            "/api/relations", headers=H, json=bad.model_dump(mode="json")
        ).status_code
        == 422
    )
    p = rel(CID, second["id"], source)
    p.citations[0].quote = "fabricated sentence"
    with pytest.raises(ValueError, match="逐字"):
        save_relation(store, p)
    a = review_relation(store, a["id"], "human_confirmed", "核对来源，接受此假设")
    with pytest.raises(ValueError, match="已审核"):
        review_relation(store, a["id"], "rejected", "overwrite")
    assert graph(store, CID)["edges"][0]["dependency_weight"] == 0.5
    old_version = a["version_id"]
    p = rel(second["id"], CID, source)
    p.confidence = 0.2
    save_relation(store, p, a["id"])
    assert graph(store, CID)["edges"] == []
    assert store.get(old_version)["after"]["confidence"] == 0.8


def test_simulation_paths_cycles_and_frozen_weights(client, store, second, source):
    forward = save_relation(store, rel(second["id"], CID, source))
    reverse = save_relation(store, rel(CID, second["id"], source))
    for r in [forward, reverse]:
        review_relation(store, r["id"], "human_confirmed", "核对来源")
    payload = SimulationInput(
        scope_id=CID,
        source_id=second["id"],
        shock=-0.2,
        assumptions="hypothetical",
        damping=0.8,
    )
    result = simulate(store, payload)
    assert result["impacts"][CID] == pytest.approx(-0.2 * 0.5 * 0.8 * 0.8)
    assert result["cycle_edges_skipped"] == [reverse["id"]]
    assert len(result["paths"]) == 1
    before = store.get(result["id"])
    save_relation(store, rel(second["id"], CID, source), forward["id"])
    assert store.get(result["id"]) == before
    assert (
        client.post(
            "/api/simulations",
            headers=H,
            json={**payload.model_dump(), "max_depth": 20},
        ).status_code
        == 422
    )


def test_restart_preserves_partial_jobs(client, store):
    job = store.insert(
        {
            "id": store.allocate("ast"),
            "type": "asset_job",
            "status": "parsing",
            "evidence_ids": ["evd-000001"],
        }
    )
    recover_extensions(store)
    assert store.get(job["id"])["status"] == "interrupted"
    assert store.get(job["id"])["evidence_ids"] == ["evd-000001"]


def test_model_relations_remain_pending_and_bad_quotes_are_rejected(
    client, store, second, source, monkeypatch
):
    from crew_stock import research
    from crew_stock.influence import RelationSuggestions, suggest_relations

    good = rel(second["id"], CID, source)
    bad = rel(CID, second["id"], source)
    bad.citations[0].quote = "not in evidence"

    class LLM:
        def call(self, *args, **kwargs):
            return RelationSuggestions(relations=[good, bad], gaps=["仅使用测试来源"])

    monkeypatch.setattr(research, "create_llm", lambda **kwargs: LLM())
    job = store.insert(
        {
            "id": store.allocate("grj"),
            "type": "graph_job",
            "parent_id": CID,
            "status": "queued",
            "evidence_ids": [source["id"]],
        }
    )
    suggest_relations(store, job)
    result = store.get(job["id"])
    assert result["status"] == "completed_with_warnings"
    assert len(result["relation_ids"]) == 1
    assert store.get(result["relation_ids"][0])["weight_status"] == "model_proposed"
    assert graph(store, CID)["edges"] == []
    assert len(result["gaps"]) == 2
    with pytest.raises(ValueError, match="已存在"):
        save_relation(store, good)


def test_docx_zip_limits_and_plain_text():
    import zipfile

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Company revenue 120</w:t></w:r></w:p></w:body></w:document>',
        )
    parsed = list(pages(output.getvalue(), ".docx", 1, 10))
    assert parsed[0][1] == "Company revenue 120"
    assert parsed[0][2] is None
    with pytest.raises(ValueError, match="UTF-8"):
        list(pages(b"\xff\xff", ".txt", 1, 10))
