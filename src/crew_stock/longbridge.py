import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from decimal import Decimal, InvalidOperation

from .models import normalize_symbol
from .store import Store, dumps, now

# The model selects a named capability, never a shell command or arbitrary flag.
COMMANDS = {
    "company": ["company", "{symbol}"],
    "quote": ["quote", "{symbol}"],
    "static": ["static", "{symbol}"],
    "trades": ["trades", "{symbol}", "--count", "1"],
    "financials": [
        "financial-statement",
        "{symbol}",
        "--kind",
        "IS",
        "--report",
        "qf",
    ],
    "earnings": ["financial-report", "snapshot", "{symbol}"],
    "balance_sheet": [
        "financial-statement",
        "{symbol}",
        "--kind",
        "BS",
        "--report",
        "qf",
    ],
    "cash_flow": ["financial-statement", "{symbol}", "--kind", "CF", "--report", "qf"],
    "segments": ["business-segments", "{symbol}"],
    "consensus": ["consensus", "{symbol}"],
    "valuation": ["valuation", "{symbol}"],
    "news": ["news", "{symbol}", "--count", "10"],
    "filings": ["filing", "{symbol}", "--count", "80"],
    "call_search": ["news", "search", "{ticker} 电话会", "--count", "40"],
    "industry": [
        "industry-rank",
        "--market",
        "{market}",
        "--indicator",
        "revenue",
        "--count",
        "20",
    ],
    "kline": ["kline", "{symbol}", "--period", "day", "--count", "60"],
    "news_detail": ["news", "detail", "{source_id}"],
    "filing_detail": ["filing", "detail", "{symbol}", "{source_id}"],
}
BASELINE = (
    "company",
    "quote",
    "financials",
    "segments",
    "consensus",
    "valuation",
    "news",
    "filings",
)
CLI_LOCK = threading.Lock()


def command_for(topic: str, symbol: str, source_id: str = "") -> list[str]:
    symbol = normalize_symbol(symbol)
    if topic not in COMMANDS:
        raise ValueError("未允许的长桥能力")
    if topic.endswith("_detail") and not re.fullmatch(r"[0-9]{1,40}", source_id):
        raise ValueError("新闻或公告 source_id 必须来自长桥列表")
    return [
        os.getenv("LONGBRIDGE_BIN", "longbridge"),
        *[
            s.format(
                symbol=symbol,
                source_id=source_id,
                ticker=symbol.split(".")[0],
                market="CN"
                if symbol.endswith((".SH", ".SZ"))
                else symbol.split(".")[-1],
            )
            for s in COMMANDS[topic]
        ],
        "--format",
        "json",
        "--lang",
        "zh-CN",
    ]


def cli(args: list[str], timeout: int = 35) -> subprocess.CompletedProcess:
    # Serialisation also prevents concurrent OAuth refreshes corrupting the token cache.
    with CLI_LOCK:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )


def auth_status() -> dict:
    if not shutil.which(os.getenv("LONGBRIDGE_BIN", "longbridge")):
        return {"installed": False, "authenticated": False, "status": "not_installed"}
    try:
        result = cli(
            [
                os.getenv("LONGBRIDGE_BIN", "longbridge"),
                "auth",
                "status",
                "--format",
                "json",
            ],
            10,
        )
        data = json.loads(result.stdout)
        state = data.get("token", {}).get("status", "unknown")
        return {
            "installed": True,
            "authenticated": state in ("valid", "authenticated", "active"),
            "status": state,
        }
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"installed": True, "authenticated": False, "status": "check_failed"}


def unwrap(data):
    if isinstance(data, dict) and "structuredContent" in data:
        return data["structuredContent"]
    return data


def differences(old, new, path="", output=None):
    """Structural differences only. Never claim semantic or fiscal comparability."""
    output = [] if output is None else output
    if len(output) >= 200:
        return output
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(old.keys() | new.keys()):
            differences(old.get(key), new.get(key), f"{path}/{key}", output)
    elif old != new:
        change = {"path": path or "/", "before": old, "after": new}
        # Scalars only; no calculations on dates, IDs, or list offsets.
        if (
            not isinstance(old, (bool, dict, list))
            and not isinstance(new, (bool, dict, list))
            and not re.search(r"id|time|date|year|period", path, re.I)
        ):
            try:
                a, b = Decimal(str(old)), Decimal(str(new))
                if a.is_finite() and b.is_finite():
                    change["numeric_delta"] = str(b - a)
            except InvalidOperation:
                pass
        output.append(change)
    return output


class Collector:
    def __init__(self, store: Store, run: dict):
        self.store, self.run = store, run
        self.cache = {}
        self.calls = 0

    def collect(self, topic: str, source_id: str = "") -> dict:
        key = (topic, source_id)
        if key in self.cache:
            return self.cache[key]
        if self.calls >= 24:
            raise ValueError("本次采集已达到 24 次调用上限")
        argv = command_for(topic, self.run["symbol"], source_id)
        self.calls += 1
        eid = self.store.allocate("evd")
        started = now()
        status, error, data = "success", None, None
        try:
            result = cli(argv)
            raw = result.stdout.encode()
            if result.returncode:
                status, error = (
                    "failed",
                    result.stderr.strip()[:2500]
                    or f"Longbridge exit {result.returncode}",
                )
                raw = dumps(
                    {
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": result.returncode,
                    }
                ).encode()
            else:
                try:
                    data = json.loads(result.stdout)
                except ValueError:
                    if topic.endswith("_detail") and result.stdout.strip():
                        data = {"text": result.stdout}
                    else:
                        status, error = "failed", "Longbridge 返回非 JSON 内容"
                if isinstance(data, dict) and (
                    data.get("isError")
                    or data.get("error")
                    or ("code" in data and str(data["code"]) != "0")
                ):
                    status, error = "failed", "Longbridge 返回业务错误，详见原始证据"
                if (
                    data in (None, {}, [])
                    or (isinstance(data, dict) and data.get("list") == [])
                ) and status == "success":
                    status, error = "empty", "Longbridge 未返回可用数据"
        except (OSError, subprocess.TimeoutExpired) as exc:
            status, error = (
                "failed",
                "Longbridge 调用超时"
                if isinstance(exc, subprocess.TimeoutExpired)
                else "Longbridge CLI 无法运行",
            )
            raw = dumps({"error": error}).encode()
        blob = self.store.blob(raw)
        normalized = unwrap(data)
        digest = (
            hashlib.sha256(
                json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if status == "success"
            else None
        )
        previous = next(
            (
                e
                for e in self.store.list("evidence", limit=10000)
                if e.get("symbol") == self.run["symbol"]
                and e.get("topic") == topic
                and e.get("source_id", "") == source_id
                and e.get("acquisition_status") == "success"
                and not e.get("historical")
            ),
            None,
        )
        changed, delta = None, []
        if previous and status == "success":
            changed = digest != previous.get("normalized_hash")
            if changed:
                previous_text = self.store.read_blob(previous["blob"]).decode()
                try:
                    previous_data = json.loads(previous_text)
                except ValueError:
                    previous_data = {"text": previous_text}
                delta = differences(unwrap(previous_data), normalized)
        body = {
            "id": eid,
            "type": "evidence",
            "parent_id": self.run["company_id"],
            "run_id": self.run["id"],
            "display_name": f"{self.run['symbol']} · {topic}",
            "symbol": self.run["symbol"],
            "source": "longbridge",
            "transport": "cli",
            "topic": topic,
            "tool": topic,
            "command": argv,
            "parameters": {"symbol": self.run["symbol"], "source_id": source_id},
            "source_id": source_id,
            "captured_at": started,
            "completed_at": now(),
            "as_of": None,
            "as_of_note": "业务时间保留在原始响应中，未将采集时间视为数据截止时间",
            "acquisition_status": status,
            "error": error,
            "content_hash": blob,
            "blob": blob,
            "normalized_hash": digest,
            "previous_evidence_id": previous["id"] if previous else None,
            "changed": changed,
            "differences": delta,
            "diff_limit": 200,
            "content_classification": "opinion"
            if topic in ("news", "consensus", "news_detail")
            else "fact",
            "historical": False,
        }
        body = self.store.insert(body)
        self.store.export_record(body)
        self.cache[key] = body
        return body

    def context(self, evidence: dict, max_chars: int = 14000) -> dict:
        raw = (
            self.store.read_blob(evidence["blob"]).decode(errors="replace")
            if evidence.get("blob")
            else "无原始载荷"
        )
        try:
            raw = dumps(unwrap(json.loads(raw)))
        except ValueError:
            pass
        return {
            "evidence_id": evidence["id"],
            "status": evidence.get("acquisition_status"),
            "topic": evidence.get("topic") or evidence.get("tool"),
            "historical": evidence.get("historical", False),
            "captured_at": evidence.get("captured_at"),
            "as_of": evidence.get("as_of"),
            "changed": evidence.get("changed"),
            "differences_preview": dumps(evidence.get("differences", [])[:12])[:6000],
            "differences_truncated": len(dumps(evidence.get("differences", []))) > 6000
            or len(evidence.get("differences", [])) > 12,
            "truncated": len(raw) > max_chars,
            "data": raw[:max_chars],
            "error": evidence.get("error"),
        }
