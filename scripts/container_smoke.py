"""Exercise the real Linux CrewAI path with a scripted LLM and disposable state."""

import json
import tempfile
from pathlib import Path

from crewai import BaseLLM
from pydantic import PrivateAttr

from crew_stock.config import require_test_database
from crew_stock.research import execute_run
from crew_stock.store import Store


class SmokeLLM(BaseLLM):
    _calls: int = PrivateAttr(default=0)

    def supports_function_calling(self):
        return False

    def get_context_window_size(self):
        return 128000

    def call(self, messages, **kwargs):
        self._calls += 1
        if self._calls == 1:
            return 'Thought: Read the historical evidence.\nAction: read_evidence\nAction Input: {"evidence_id":"evd-000006","offset":0}'
        return "Final Answer: " + json.dumps(
            {
                "title": "容器测试：历史资料",
                "summary": "仅用于验证代码路径的测试模型输出，不是真实 DeepSeek 研究。",
                "claims": [
                    {
                        "classification": "fact",
                        "text": "资料包含 NVIDIA 公司概况。",
                        "evidence_ids": ["evd-000006"],
                        "as_of": "2026-08-05",
                        "confidence": "low",
                    }
                ],
                "changes": [],
                "counter_evidence": [],
                "gaps": ["测试替身，无真实模型请求"],
                "thesis_proposal": "测试记录",
                "next_questions": [],
            },
            ensure_ascii=False,
        )


require_test_database()

with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory))
    store.initialize()
    run = store.insert(
        {
            "id": store.allocate("run"),
            "type": "run",
            "parent_id": "cmp-000001",
            "company_id": "cmp-000001",
            "symbol": "NVDA.US",
            "mode": "archive",
            "question": "验证历史资料路径",
            "status": "queued",
        }
    )
    llm = SmokeLLM(model="test-scripted-container")
    execute_run(store, run, llm=llm)
    completed = store.get(run["id"])
    assert completed["status"] == "completed_with_warnings", completed
    report = store.get(completed["report_id"])
    assert report["review_status"] == "pending_confirmation"
    assert len(report["skill_manifest"]) == 4
    assert llm._calls >= 3
    with store.connection() as con:
        assert (
            con.execute(
                "SELECT COUNT(*) FROM crew_task_outputs WHERE run_id=%s", (run["id"],)
            ).fetchone()[0]
            == 2
        )
    print(
        "CONTAINER_SMOKE_OK: real CrewAI Flow, tools, 2 Tasks, MySQL task outputs, schema and citations; scripted model; dedicated test database"
    )
