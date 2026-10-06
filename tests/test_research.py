import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from crewai import BaseLLM
from openai import OpenAI
from pydantic import PrivateAttr

from crew_stock import config, longbridge
from crew_stock.longbridge import Collector, command_for
from crew_stock.models import ResearchReport
from crew_stock.research import (
    EvidenceTool,
    SkillReferenceTool,
    create_llm,
    execute_run,
    validate_report,
)


def run_record(store, mode="research"):
    return store.insert(
        {
            "id": store.allocate("run"),
            "type": "run",
            "parent_id": "cmp-000001",
            "company_id": "cmp-000001",
            "symbol": "NVDA.US",
            "mode": mode,
            "question": "核查英伟达业务，保留日期和证据。",
            "status": "queued",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
        }
    )


def report_data(evidence_id="evd-000006"):
    return {
        "title": "历史资料研究",
        "summary": "基于历史公司概况的核查结果。",
        "claims": [
            {
                "classification": "fact",
                "text": "公司历史资料记载成立于 1993 年。",
                "evidence_ids": [evidence_id],
                "as_of": "2026-08-05",
                "confidence": "high",
            }
        ],
        "changes": [],
        "counter_evidence": [],
        "gaps": ["历史资料不能代表当前情况"],
        "thesis_proposal": "持续核查原始披露。",
        "next_questions": ["后续财报有何变化？"],
    }


def test_seed_import_idempotent_and_sequences(store):
    assert len(store.list("evidence")) == 17
    store.initialize()
    assert len(store.list("evidence")) == 17
    assert store.allocate("evd") == "evd-000018"
    assert store.get("evd-000006")["historical"] is True
    assert store.get("evd-000006")["captured_at"].startswith("2026-08-05")
    assert "NVIDIA" in store.read_blob(store.get("evd-000006")["blob"]).decode()


def test_ids_are_unique_under_concurrency(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: store.allocate("evd"), range(50)))
    assert len(set(ids)) == 50


@pytest.mark.parametrize(
    "topic,symbol",
    [
        ("order", "NVDA.US"),
        ("quote", "NVDA.US;rm -rf /"),
        ("quote", "--help.US"),
        ("quote", "../../NVDA.US"),
    ],
)
def test_cli_rejects_unapproved_commands(topic, symbol):
    with pytest.raises(ValueError):
        command_for(topic, symbol)


def test_detail_source_cannot_inject_command():
    with pytest.raises(ValueError):
        command_for("news_detail", "NVDA.US", "1 --output /etc/passwd")


def test_capture_dedup_diff_and_failure(store, monkeypatch):
    responses = iter([("100", 0), ("100", 0), ("101.5", 0), ("", 1)])

    def fake(argv):
        price, code = next(responses)
        return subprocess.CompletedProcess(
            argv,
            code,
            json.dumps({"price": price}),
            "not authenticated" if code else "",
        )

    monkeypatch.setattr(longbridge, "cli", fake)
    evidence = [
        Collector(store, run_record(store, "collect")).collect("quote")
        for _ in range(4)
    ]
    first, same, changed, failure = evidence
    assert first["blob"] == same["blob"]
    assert same["changed"] is False
    assert changed["changed"] is True
    assert changed["differences"][0]["numeric_delta"] == "1.5"
    assert failure["acquisition_status"] == "failed"
    assert failure["normalized_hash"] is None
    assert failure["changed"] is None
    assert failure["as_of"] is None


def test_blob_tamper_detected(store):
    digest = store.blob(b"original")
    (store.root / "blobs" / digest).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        store.read_blob(digest)


def test_plaintext_article_diff_preserves_full_raw(store, monkeypatch):
    responses = iter(["first article body", "second article body"])
    monkeypatch.setattr(
        longbridge,
        "cli",
        lambda argv: subprocess.CompletedProcess(argv, 0, next(responses), ""),
    )
    first = Collector(store, run_record(store, "collect")).collect("news_detail", "123")
    second = Collector(store, run_record(store, "collect")).collect(
        "news_detail", "123"
    )
    assert first["acquisition_status"] == "success"
    assert second["differences"][0]["before"] == "first article body"
    assert second["differences"][0]["after"] == "second article body"
    assert store.read_blob(first["blob"]) == b"first article body"


def test_large_difference_is_bounded_in_prompt(store):
    collector = Collector(store, run_record(store))
    e = store.get("evd-000006")
    e["differences"] = [{"before": "x" * 30000, "after": "y" * 30000}]
    preview = collector.context(e)
    assert preview["differences_truncated"]
    assert len(preview["differences_preview"]) <= 6000


def test_failed_or_invented_citations_rejected(store):
    report = ResearchReport.model_validate(report_data("evd-000008"))
    with pytest.raises(ValueError, match="失败"):
        validate_report(report, store.list("evidence"))
    report.claims[0].evidence_ids = ["evd-999999"]
    with pytest.raises(ValueError):
        validate_report(report, store.list("evidence"))


def test_no_arbitrary_evidence_or_skill_files(store):
    collector = Collector(store, run_record(store))
    tool = EvidenceTool(collector, [store.get("evd-000006")])
    assert "仅可读取" in tool._run("evd-000007")
    assert "NVIDIA" in tool._run("evd-000006")
    assert SkillReferenceTool()._run("../../.env") == "技能参考文件不存在"


def test_company_and_securities_atomic_uniqueness(store):
    c = store.create_company(
        {
            "name": "Alphabet",
            "symbols": ["GOOG.US", "GOOGL.US"],
            "purpose": "两类证券，一个主体",
        }
    )
    assert len(store.list("security", c["id"])) == 2
    with pytest.raises(ValueError):
        store.create_company(
            {"name": "duplicate", "symbols": ["GOOG.US"], "purpose": "test"}
        )
    assert len(store.list("company")) == 2


class ScriptedLLM(BaseLLM):
    model: str = "test-scripted-no-network"
    _calls: int = PrivateAttr(default=0)

    def supports_function_calling(self):
        return False

    def get_context_window_size(self):
        return 128000

    def call(self, messages, **kwargs):
        self._calls += 1
        if self._calls == 1:
            return 'Thought: 查阅原始证据。\nAction: read_evidence\nAction Input: {"evidence_id":"evd-000006","offset":0}'
        return "Final Answer: " + json.dumps(report_data(), ensure_ascii=False)


def test_real_crewai_flow_tools_report_and_review(store, monkeypatch):
    import sqlite3

    def reject_sqlite(*args, **kwargs):
        raise AssertionError("研究流程不应访问 SQLite")

    monkeypatch.setattr(sqlite3, "connect", reject_sqlite)
    monkeypatch.setattr(config, "DATA", store.root)
    run = run_record(store, "archive")
    llm = ScriptedLLM(model="test-scripted-no-network")
    execute_run(store, run, llm=llm)
    result = store.get(run["id"])
    assert result["status"] == "completed_with_warnings", result
    assert llm._calls >= 3  # Actual ReAct tool loop and second Task ran.
    with store.connection() as con:
        assert (
            con.execute(
                "SELECT COUNT(*) FROM crew_task_outputs WHERE run_id=%s", (run["id"],)
            ).fetchone()[0]
            == 2
        )
    report = store.get(result["report_id"])
    assert report["review_status"] == "pending_confirmation"
    assert report["historical"]
    assert len(report["skill_manifest"]) == 4
    assert "confirmed_thesis" not in store.get("cmp-000001")
    store.review(report["id"], "confirmed", "人工核对了历史来源")
    assert store.get("cmp-000001")["confirmed_thesis"]["report_id"] == report["id"]
    with pytest.raises(ValueError, match="已审阅"):
        store.review(report["id"], "rejected", "不能重写历史")
    assert len(store.list("decision")) == 1


def test_collect_failure_remains_failure(store, monkeypatch):
    monkeypatch.setattr(
        longbridge,
        "cli",
        lambda argv: subprocess.CompletedProcess(argv, 1, "", "Not authenticated"),
    )
    run = run_record(store, "collect")
    execute_run(store, run)
    result = store.get(run["id"])
    assert result["status"] == "failed"
    assert len(result["evidence_ids"]) == 8
    assert not store.list("report")


def test_deepseek_wire_contract_no_real_network():
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 1,
                    "total_tokens": 5,
                },
            },
        )

    llm = create_llm(
        {
            "api_key": "test-not-real",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
        }
    )
    llm._client = OpenAI(
        api_key="test-not-real",
        base_url="https://api.deepseek.com",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    assert llm.call([{"role": "user", "content": "reply OK"}]) == "OK"
    assert seen[0]["thinking"] == {"type": "disabled"}
    assert seen[0]["model"] == "deepseek-flash"
    assert seen[0]["max_tokens"] == 8000
    assert "response_format" not in seen[0]
    assert (
        not llm.supports_function_calling()
    )  # CrewAI ReAct, not OpenAI strict schema.


def test_deepseek_structured_response_uses_local_schema_validation():
    from pydantic import BaseModel

    class Output(BaseModel):
        title: str

    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "local-schema",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"title":"ok"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 2,
                    "total_tokens": 4,
                },
            },
        )

    llm = create_llm(
        {
            "api_key": "test",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
        }
    )
    llm._client = OpenAI(
        api_key="test",
        base_url="https://api.deepseek.com",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    result = llm.call("Return JSON", response_model=Output)
    assert result.title == "ok"
    assert "response_format" not in seen[0]
    assert "title" in seen[0]["messages"][-1]["content"]


def test_invalid_citation_is_removed_without_relabeling_it_as_a_valid_fact(store):
    from crew_stock.research import sanitize_claims

    data = report_data()
    data["claims"].append(
        {
            **data["claims"][0],
            "text": "Unverifiable conclusion",
            "evidence_ids": ["evd-999999"],
        }
    )
    report = sanitize_claims(
        ResearchReport.model_validate(data), store.list("evidence")
    )
    assert len(report.claims) == 1
    assert "Unverifiable conclusion" not in report.summary
    assert "暂不形成" in report.thesis_proposal
    assert any("已拦截" in g for g in report.gaps)
    with pytest.raises(ValueError, match="没有通过"):
        sanitize_claims(
            ResearchReport.model_validate(report_data("evd-999999")),
            store.list("evidence"),
        )
