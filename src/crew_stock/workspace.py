"""Company research ledger: immutable source records, decisions and evaluations."""

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from .store import dumps, now
from .workspace_models import Disclosure, Metric, Promise

SCHEMAS = {"disclosure": Disclosure, "metric": Metric, "promise": Promise}
PREFIXES = {"disclosure": "dsc", "metric": "met", "promise": "prm"}
KINDS = (
    "disclosure",
    "promise",
    "metric",
    "record_review",
    "promise_evaluation",
    "expectation_snapshot",
    "thesis_version",
    "investment_decision",
    "decision_review",
    "source_document",
)


def company(store, cid):
    item = store.get(cid)
    if item["type"] != "company":
        raise ValueError("请选择公司档案")
    return item


def check_evidence(store, cid, ids, quote=None):
    c = company(store, cid)
    allowed = {cid, *c.get("security_ids", [])}
    evidence = []
    for eid in dict.fromkeys(ids):
        e = store.get(eid)
        if (
            e["type"] != "evidence"
            or not store.belongs_to(e, allowed)
            or e.get("acquisition_status") != "success"
            or not e.get("blob")
        ):
            raise ValueError("引用必须是本公司可读取的成功证据")
        evidence.append(e)
    if not evidence:
        raise ValueError("至少需要一份证据")
    if quote:

        def normalize(s):
            return re.sub(r"\s+", " ", s).strip()

        found = False
        for e in evidence:
            raw = store.read_blob(e["blob"]).decode(errors="replace")
            # Decode JSON strings so escaped newlines/quotes do not break exact quoting.
            try:
                raw += "\n" + dumps(json.loads(raw))

                def strings(x):
                    if isinstance(x, str):
                        return [x]
                    if isinstance(x, dict):
                        return [v for item in x.values() for v in strings(item)]
                    if isinstance(x, list):
                        return [v for item in x for v in strings(item)]
                    return []

                raw += "\n" + "\n".join(strings(json.loads(store.read_blob(e["blob"]))))
            except (ValueError, TypeError):
                pass
            if normalize(quote) in normalize(raw):
                found = True
        if not found:
            raise ValueError("原文摘录未在引用证据中找到，请逐字复制原文")
    return evidence


def prepare_record(store, cid, kind, values, origin="manual", report_id=None):
    model = SCHEMAS[kind].model_validate(values)
    data = model.model_dump(mode="json")
    check_evidence(store, cid, data["evidence_ids"], data["source_quote"])
    fingerprint = hashlib.sha256(dumps([cid, kind, data]).encode()).hexdigest()
    return {
        "id": store.allocate(PREFIXES[kind]),
        "type": kind,
        "parent_id": cid,
        "data": data,
        "evidence_ids": data["evidence_ids"],
        "review_status": "pending_confirmation",
        "origin": origin,
        "report_id": report_id,
        "fingerprint": fingerprint,
        "created_at": now(),
    }


def insert_unique(store, rows):
    result = []
    with store.transaction() as con:
        for body in rows:
            existing = con.execute(
                "SELECT body FROM objects WHERE kind=%s AND JSON_UNQUOTE(JSON_EXTRACT(body,'$.fingerprint'))=%s",
                (body["type"], body["fingerprint"]),
            ).fetchone()
            if existing:
                result.append(json.loads(existing[0]))
            else:
                store._insert(con, body)
                result.append(body)
    return result


def persist_extraction(store, report):
    bundle = report["report"].get("extraction", {})
    rows = []
    for plural, kind in (
        ("disclosures", "disclosure"),
        ("promises", "promise"),
        ("metrics", "metric"),
    ):
        for data in bundle.get(plural, []):
            rows.append(
                prepare_record(
                    store, report["parent_id"], kind, data, "model", report["id"]
                )
            )
    return insert_unique(store, rows)


def review_record(store, rid, decision, note):
    review_id = store.allocate("rev")
    with store.transaction() as con:
        row = con.execute("SELECT body FROM objects WHERE id=%s", (rid,)).fetchone()
        if not row:
            raise KeyError(rid)
        body = json.loads(row[0])
        if (
            body["type"] not in SCHEMAS
            or body["review_status"] != "pending_confirmation"
        ):
            raise ValueError("只能审阅待确认的研究记录；修订请建立新版本")
        body.update(review_status=decision, reviewed_at=now(), review_note=note)
        con.execute("UPDATE objects SET body=%s WHERE id=%s", (dumps(body), rid))
        store._insert(
            con,
            {
                "id": review_id,
                "type": "record_review",
                "parent_id": body["parent_id"],
                "record_id": rid,
                "decision": decision,
                "note": note,
                "created_at": now(),
            },
        )
    return body


def compatible(a, b):
    keys = ("metric", "period_start", "period_end", "basis", "unit", "currency")
    return all(a.get(k) and a.get(k) == b.get(k) for k in keys)


def compare_metrics(a, b):
    """Compare vintages or actual vs forecast. No implicit currency/unit conversion."""
    result = {"before_id": a["id"], "after_id": b["id"], "status": "incomparable"}
    x, y = a["data"], b["data"]
    if a.get("review_status") != "confirmed" or b.get("review_status") != "confirmed":
        return {**result, "reason": "指标须先人工核对确认"}
    if (
        a["parent_id"] != b["parent_id"]
        or not compatible(x, y)
        or x["direction"] != y["direction"]
    ):
        return {**result, "reason": "公司、期间、币种、单位、方向或会计口径不一致"}
    old, new = Decimal(x["value"]), Decimal(y["value"])
    if x["category"] in ("consensus", "guidance") and y["category"] == "actual":
        # Both published vintage and local availability must predate the release.
        release = datetime.fromisoformat(y["published_at"])
        captured = datetime.fromisoformat(a["created_at"])
        if datetime.fromisoformat(x["published_at"]) >= release or captured >= release:
            return {
                **result,
                "reason": "缺少结果公布前已保存的预期快照，不能证明当时预期",
            }
        kind = "expectation_gap"
    elif x["category"] == y["category"] and x["category"] in ("consensus", "guidance"):
        if datetime.fromisoformat(x["published_at"]) >= datetime.fromisoformat(
            y["published_at"]
        ):
            return {**result, "reason": "请选择发布时间更晚的新版本"}
        kind = "revision"
    else:
        return {**result, "reason": "仅比较同类指引/预期修订，或预期与实际"}
    delta = new - old
    better = delta if y["direction"] == "higher" else -delta
    return {
        **result,
        "status": "comparable",
        "kind": kind,
        "metric": y["metric"],
        "period": y["period"],
        "before": str(old),
        "after": str(new),
        "delta": str(delta),
        "percent": str(delta / abs(old) * 100) if old else None,
        "assessment": "above" if better > 0 else "below" if better < 0 else "inline",
        "evidence_ids": list(dict.fromkeys(a["evidence_ids"] + b["evidence_ids"])),
    }


def evaluate_promise(store, rid, payload):
    p = store.get(rid)
    if p["type"] != "promise" or p["review_status"] != "confirmed":
        raise ValueError("请先核对并确认管理层承诺")
    data = payload.model_dump(mode="json")
    if payload.actual_metric_id:
        actual = store.get(payload.actual_metric_id)
        target = p["data"]
        if (
            actual["type"] != "metric"
            or actual.get("review_status") != "confirmed"
            or actual["parent_id"] != p["parent_id"]
            or actual["data"]["category"] != "actual"
            or not compatible(target, actual["data"])
            or target["operator"] == "qualitative"
        ):
            raise ValueError("实际指标未确认或与承诺的期间、单位、币种、口径不一致")
        value, threshold = Decimal(actual["data"]["value"]), Decimal(target["target"])
        met = {
            "at_least": value >= threshold,
            "at_most": value <= threshold,
            "equal": value == threshold,
        }[target["operator"]]
        # Conditional promises need an explicit human explanation of condition fulfilment.
        data.update(
            status="fulfilled" if met else "missed",
            evidence_ids=actual["evidence_ids"],
            actual_value=str(value),
            target_value=str(threshold),
            method="numeric_comparison",
            conditions=target["conditions"],
        )
    else:
        check_evidence(store, p["parent_id"], payload.evidence_ids)
        data["method"] = "human_assessment"
    return store.insert(
        {
            "id": store.allocate("pev"),
            "type": "promise_evaluation",
            "parent_id": p["parent_id"],
            "promise_id": rid,
            **data,
        }
    )


def create_decision(store, payload):
    cid = payload.company_id
    evidence = check_evidence(store, cid, payload.evidence_ids)
    if payload.decided_at > datetime.now(timezone.utc):
        raise ValueError("不能把未来决策记为已经发生")
    if payload.review_date < payload.decided_at.date() or any(
        a.check_date < payload.decided_at.date() for a in payload.assumptions
    ):
        raise ValueError("假设检查日期和复盘日期不能早于决策")
    unavailable = [
        e["id"]
        for e in evidence
        if not e.get("captured_at")
        or datetime.fromisoformat(e["captured_at"]) > payload.decided_at
    ]
    if unavailable and not payload.retrospective:
        raise ValueError(
            "所选证据有决策后才入库的资料。回填历史决策必须明确勾选事后补记"
        )
    c = company(store, cid)
    return store.insert(
        {
            "id": store.allocate("ivd"),
            "type": "investment_decision",
            "parent_id": cid,
            **payload.model_dump(mode="json"),
            "thesis_snapshot": c.get("confirmed_thesis"),
            "evidence_snapshot": [
                {
                    "id": e["id"],
                    "hash": e["blob"],
                    "captured_at": e.get("captured_at"),
                    "as_of": e.get("as_of"),
                }
                for e in evidence
            ],
            "not_available_at_decision": unavailable,
            "execution": "journal_only_no_order",
        }
    )


def review_decision(store, rid, payload):
    decision = store.get(rid)
    if decision["type"] != "investment_decision":
        raise ValueError("请选择投资决策记录")
    if (
        payload.observed_at < datetime.fromisoformat(decision["decided_at"]).date()
        or payload.observed_at > datetime.now(timezone.utc).date()
    ):
        raise ValueError("复盘观察日期应在决策之后且不晚于今天")
    indexes = [x.index for x in payload.assumption_results]
    if sorted(indexes) != list(range(len(decision["assumptions"]))):
        raise ValueError("请逐一复盘全部原始假设，不能遗漏或重复")
    check_evidence(store, decision["parent_id"], payload.evidence_ids)
    return store.insert(
        {
            "id": store.allocate("drv"),
            "type": "decision_review",
            "parent_id": decision["parent_id"],
            "decision_id": rid,
            **payload.model_dump(mode="json"),
        }
    )


def archive(store, cid):
    c = company(store, cid)
    parents = {cid, *c.get("security_ids", [])}
    with store.connection() as con:
        rows = con.execute(
            "SELECT body FROM objects WHERE parent_id IN (%s) OR id IN (SELECT object_id FROM object_links WHERE target_id IN (%s)) ORDER BY created_at DESC,id DESC"
            % (",".join("%s" for _ in parents), ",".join("%s" for _ in parents)),
            tuple(parents) * 2,
        ).fetchall()
    records = [json.loads(r[0]) for r in rows]
    return {"company": c, "records": records, "count": len(records)}


def ingest_consensus(store, evidence):
    """Preserve provider's own beat/miss separately from point-in-time local research."""
    if (
        evidence.get("topic") != "consensus"
        or evidence.get("acquisition_status") != "success"
    ):
        return
    raw = json.loads(store.read_blob(evidence["blob"]))
    raw = raw.get("structuredContent", raw)
    rows = []
    for period in raw.get("list", []):
        for item in period.get("details", []):
            if not item.get("estimate"):
                continue
            estimate = Decimal(item["estimate"])
            actual = Decimal(item["actual"]) if item.get("actual") else None
            if not estimate.is_finite() or (
                actual is not None and not actual.is_finite()
            ):
                continue
            data = {
                "metric": item["key"],
                "name": item["name"],
                "period": period["period_text"],
                "estimate": str(estimate),
                "actual": str(actual) if actual is not None else None,
                "delta": str(actual - estimate) if actual is not None else None,
                "percent": str((actual - estimate) / abs(estimate) * 100)
                if actual is not None and estimate
                else None,
                "provider_assessment": item.get("comp_desc"),
                "is_released": item.get("is_released"),
                "currency": raw.get("currency") or None,
                "vintage_at": None,
                "limitation": "长桥同一响应中的实际/预期；供应商未提供预期形成时间。不是本地事前预期证明。",
            }
            fingerprint = hashlib.sha256(
                dumps([evidence["blob"], data]).encode()
            ).hexdigest()
            rows.append(
                {
                    "id": store.allocate("exp"),
                    "type": "expectation_snapshot",
                    "parent_id": evidence["parent_id"],
                    "symbol": evidence["symbol"],
                    "data": data,
                    "evidence_ids": [evidence["id"]],
                    "captured_at": evidence["captured_at"],
                    "created_at": now(),
                    "fingerprint": fingerprint,
                }
            )
    insert_unique(store, rows)


def promise_checks(records):
    """Numeric checks are suggestions; conditions and fulfilment stay human-reviewed."""
    metrics = [
        r
        for r in records
        if r["type"] == "metric"
        and r.get("review_status") == "confirmed"
        and r["data"]["category"] == "actual"
    ]
    checks = []
    for p in records:
        if (
            p["type"] != "promise"
            or p.get("review_status") != "confirmed"
            or p["data"]["operator"] == "qualitative"
        ):
            continue
        target = p["data"]
        actual = next((m for m in metrics if compatible(target, m["data"])), None)
        if not actual:
            checks.append(
                {
                    "promise_id": p["id"],
                    "status": "awaiting_comparable_actual",
                    "reason": "尚缺同期间、单位、币种及口径的已确认实际指标",
                }
            )
            continue
        value, threshold = Decimal(actual["data"]["value"]), Decimal(target["target"])
        met = {
            "at_least": value >= threshold,
            "at_most": value <= threshold,
            "equal": value == threshold,
        }[target["operator"]]
        checks.append(
            {
                "promise_id": p["id"],
                "actual_metric_id": actual["id"],
                "status": "numeric_target_met" if met else "numeric_target_missed",
                "conditions": target["conditions"],
                "review_required": True,
                "reason": "仅完成数值对照；适用条件和最终兑现结论待人工评价",
            }
        )
    return checks
