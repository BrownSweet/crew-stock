"""Bounded discovery of original disclosures and earnings-call coverage."""

import json
import re

from .longbridge import unwrap
from .workspace import archive, check_evidence, ingest_consensus


def entries(value):
    if isinstance(value, list):
        for item in value:
            yield from entries(item)
    elif isinstance(value, dict):
        if (value.get("title") or value.get("headline")) and (
            value.get("id") or value.get("news_id") or value.get("filing_id")
        ):
            yield value
        else:
            for item in value.values():
                yield from entries(item)


def enrich(collector, evidence):
    for e in evidence:
        ingest_consensus(collector.store, e)
    search = collector.collect("call_search")
    evidence.extend(
        [
            search,
            collector.collect("industry"),
            collector.collect("balance_sheet"),
            collector.collect("cash_flow"),
            collector.collect("earnings"),
        ]
    )
    seen, fetched = set(), 0
    for e in [search, *evidence]:
        if (
            e.get("topic") not in ("call_search", "news", "filings")
            or e.get("acquisition_status") != "success"
        ):
            continue
        try:
            data = unwrap(json.loads(collector.store.read_blob(e["blob"])))
        except ValueError:
            continue
        for item in entries(data):
            title = str(item.get("title") or item.get("headline"))
            if not re.search(
                r"电话会|电话会议|业绩会|财报|业绩|earnings|results|10-Q|10-K",
                title,
                re.I,
            ):
                continue
            if e["topic"] == "call_search":
                ticker = collector.run["symbol"].split(".")[0].lower()
                company = collector.store.get(collector.run["company_id"])
                names = [
                    str(company.get(key) or "").lower()
                    for key in ("display_name", "name_zh_cn", "name_en")
                ]
                if ticker not in title.lower() and not any(
                    name and name in title.lower() for name in names
                ):
                    continue
            source_id = str(
                item.get("id") or item.get("news_id") or item.get("filing_id")
            )
            topic = "filing_detail" if e["topic"] == "filings" else "news_detail"
            if not re.fullmatch(r"\d{1,40}", source_id) or (topic, source_id) in seen:
                continue
            seen.add((topic, source_id))
            evidence.append(collector.collect(topic, source_id))
            fetched += 1
            if fetched >= 4:
                return


def validate_extraction(store, cid, bundle, allowed_ids):
    for values in (bundle.disclosures, bundle.promises, bundle.metrics):
        for item in values:
            if not set(item.evidence_ids).issubset(allowed_ids):
                raise ValueError("结构化记录引用了本次研究之外的证据")
            check_evidence(store, cid, item.evidence_ids, item.source_quote)


def archive_context(store, cid):
    records = archive(store, cid)["records"]
    kinds = {
        "business_order",
        "disclosure",
        "promise",
        "promise_evaluation",
        "thesis_version",
        "investment_decision",
        "decision_review",
        "metric",
    }
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "created_at": r.get("created_at"),
            "review_status": r.get("review_status"),
            "preview": str(r.get("data", r))[:900],
        }
        for r in records
        if r["type"] in kinds
    ][:35]


def source_field_issues(store, kind, data):
    """Literal provenance checks for model-extracted structured fields, not semantic proof."""
    raw = "\n".join(
        store.read_blob(store.get(eid)["blob"]).decode(errors="replace")
        for eid in data["evidence_ids"]
    )
    text = raw.replace(".", "-").replace("/", "-")
    issues = []
    for key in (
        "published_at",
        "period_start",
        "period_end",
        "promised_at",
        "due_date",
    ):
        if data.get(key) and str(data[key])[:10] not in text:
            issues.append(f"{key} 的日期未出现在所引原文")
    if kind in ("metric", "promise"):
        if data.get("basis"):
            basis = data["basis"].lower()
            if basis not in raw.lower():
                issues.append("会计/统计口径未在所引原文明确提供")
        if (
            data.get("currency")
            and data["currency"] != "N/A"
            and data["currency"] not in raw
        ):
            issues.append("币种未在所引原文明确提供")
        number = data.get("value", data.get("target"))
        if number is not None:
            from decimal import Decimal, InvalidOperation

            found = False
            for token in re.findall(
                r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", raw.replace(",", "")
            ):
                try:
                    if Decimal(token) == Decimal(str(number)):
                        found = True
                        break
                except InvalidOperation:
                    pass
            if not found:
                issues.append("数值未在所引原文中找到（不自动推算或换算单位）")
    return issues


def sanitize_extraction(store, report, cid=None, allowed_ids=None):
    report.gaps.extend(report.extraction.validation_issues)
    for field, kind in (
        ("disclosures", "disclosure"),
        ("promises", "promise"),
        ("metrics", "metric"),
    ):
        retained = []
        for item in getattr(report.extraction, field):
            issues = []
            try:
                if allowed_ids is not None and not set(item.evidence_ids).issubset(
                    allowed_ids
                ):
                    raise ValueError("不属于本次研究")
                issues = source_field_issues(store, kind, item.model_dump(mode="json"))
                check_evidence(
                    store,
                    cid or store.get(item.evidence_ids[0])["parent_id"],
                    item.evidence_ids,
                    item.source_quote,
                )
            except (ValueError, KeyError):
                issues.append("逐字原文摘录或公司来源校验未通过")
            if issues:
                report.gaps.append(f"已拦截 {kind} 候选：" + "；".join(issues))
            else:
                retained.append(item)
        setattr(report.extraction, field, retained)
    report.gaps = list(dict.fromkeys(report.gaps))[:20]
    return report


def revalidate_pending_records(store):
    """One-way validation of older pending model records; keep originals and audit trail."""
    from .workspace import review_record

    for kind in ("disclosure", "promise", "metric"):
        for record in store.list(kind, limit=10000):
            if (
                record.get("origin") != "model"
                or record.get("review_status") != "pending_confirmation"
            ):
                continue
            issues = source_field_issues(store, kind, record["data"])
            if issues:
                review_record(
                    store,
                    record["id"],
                    "rejected",
                    "系统来源字段校验未通过（非用户决定）：" + "；".join(issues),
                )
                store.update(
                    record["id"], validation_errors=issues, validation_origin="system"
                )
