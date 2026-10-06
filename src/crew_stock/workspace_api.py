from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import config
from .models import RunInput
from .store import dumps, now
from .workspace import (
    archive,
    company,
    compare_metrics,
    create_decision,
    evaluate_promise,
    insert_unique,
    prepare_record,
    promise_checks,
    review_decision,
    review_record,
)
from .workspace_models import (
    DecisionReview,
    GroupInput,
    ImportInput,
    InvestmentDecision,
    PromiseEvaluation,
    RecordInput,
    ReviewInput,
)


class CompareInput(BaseModel):
    before_id: str
    after_id: str


class GroupRunInput(BaseModel):
    question: str = Field(
        default="对比行业需求、竞争地位、最新经营指标及管理层指引。明确资料缺口。",
        min_length=1,
        max_length=3000,
    )


def router(get_store, enqueue):
    api = APIRouter(prefix="/api/workspace")

    @api.get("/companies/{cid}")
    def dossier(cid: str):
        result = archive(get_store(), cid)
        metrics = [r for r in result["records"] if r["type"] == "metric"]
        comparisons = []
        for i, b in enumerate(metrics[:80]):
            for a in metrics[i + 1 : 80]:
                compared = compare_metrics(a, b)
                if compared["status"] == "comparable":
                    comparisons.append(compared)
        result["comparisons"] = comparisons[:100]
        result["promise_checks"] = promise_checks(result["records"])
        return result

    @api.post("/records", status_code=201)
    def create_record(payload: RecordInput):
        store = get_store()
        return insert_unique(
            store,
            [prepare_record(store, payload.company_id, payload.kind, payload.data)],
        )[0]

    @api.post("/records/{rid}/review")
    def record_review(rid: str, payload: ReviewInput):
        return review_record(get_store(), rid, payload.decision, payload.note)

    @api.post("/compare")
    def compare(payload: CompareInput):
        store = get_store()
        a, b = store.get(payload.before_id), store.get(payload.after_id)
        if a["type"] != "metric" or b["type"] != "metric":
            raise ValueError("请选择两条结构化指标")
        return compare_metrics(a, b)

    @api.post("/promises/{rid}/evaluate", status_code=201)
    def promise_evaluation(rid: str, payload: PromiseEvaluation):
        return evaluate_promise(get_store(), rid, payload)

    @api.post("/documents", status_code=201)
    def import_document(payload: ImportInput):
        store = get_store()
        company(store, payload.company_id)
        securities = store.list("security", payload.company_id)
        if payload.analyze and (not config.settings()["api_key"] or not securities):
            raise ValueError("自动整理需要先配置 DeepSeek 并添加证券")
        sid, eid = store.allocate("src"), store.allocate("evd")
        blob = store.blob(payload.text.encode())
        timestamp = now()
        document = {
            "id": sid,
            "type": "source_document",
            "parent_id": payload.company_id,
            **payload.model_dump(mode="json", exclude={"text", "analyze"}),
            "blob": blob,
            "evidence_ids": [eid],
            "source_trust": "user_provided_unverified",
            "created_at": timestamp,
        }
        evidence = {
            "id": eid,
            "type": "evidence",
            "parent_id": payload.company_id,
            "source": "user",
            "source_document_id": sid,
            "display_name": payload.title,
            "topic": payload.kind,
            "blob": blob,
            "content_hash": blob,
            "acquisition_status": "success",
            "captured_at": timestamp,
            "as_of": str(payload.published_at) if payload.published_at else None,
            "original_url": payload.source_url,
            "created_at": timestamp,
        }
        with store.connection() as con:
            store._insert(con, document)
            store._insert(con, evidence)
        # Queue failure leaves the imported original intact, with a visible retry route.
        if payload.analyze:
            try:
                run = enqueue(
                    RunInput(
                        company_id=payload.company_id,
                        symbol=securities[0]["symbol"],
                        mode="research",
                        source_document_id=sid,
                        question="整理这份用户提供资料。提取财报/电话会摘要、管理层问答、承诺与指引。原文未经外部核验，不要把用户材料直接升级为公司事实。",
                    )
                )
                document = store.update(sid, run_id=run["id"])
            except ValueError as exc:
                document = store.update(sid, analysis_error=str(exc))
        return document

    @api.post("/documents/{rid}/analyze", status_code=202)
    def analyze_document(rid: str, company_id: str | None = None):
        store = get_store()
        doc = store.get(rid)
        if doc["type"] != "source_document":
            raise ValueError("请选择原始财报或电话会资料")
        cid = company_id or doc.get("parent_id")
        if not cid or not store.belongs_to(doc, {cid}):
            raise ValueError("请选择资料已关联的公司")
        company(store, cid)
        securities = store.list("security", cid)
        if not securities:
            raise ValueError("该公司尚未配置证券")
        return enqueue(
            RunInput(
                company_id=cid,
                symbol=securities[0]["symbol"],
                source_document_id=rid,
                question="整理原文，提取财报/电话会摘要、管理层问答、承诺与指引，并标注缺口。",
            )
        )

    @api.post("/decisions", status_code=201)
    def decision(payload: InvestmentDecision):
        return create_decision(get_store(), payload)

    @api.post("/decisions/{rid}/reviews", status_code=201)
    def decision_review(rid: str, payload: DecisionReview):
        return review_decision(get_store(), rid, payload)

    @api.post("/decisions/{rid}/analyze", status_code=202)
    def decision_analysis(rid: str):
        store = get_store()
        item = store.get(rid)
        if item["type"] != "investment_decision":
            raise ValueError("请选择投资决策")
        securities = store.list("security", item["parent_id"])
        run = enqueue(
            RunInput(
                company_id=item["parent_id"],
                symbol=securities[0]["symbol"],
                question=f"复盘投资决策 {rid}。先用 search_company_archive 读取原始决策和假设及历史复盘，再收集最新证据。逐项检验假设和失效条件，区分当时信息与事后信息、过程质量与结果好坏。输出下一步核查建议。不得改写原始决定。",
            )
        )
        return store.update(run["id"], decision_id=rid)

    @api.get("/groups")
    def groups():
        return get_store().list("peer_group")

    @api.post("/groups", status_code=201)
    def create_group(payload: GroupInput):
        store = get_store()
        for cid in payload.company_ids:
            company(store, cid)
        return store.insert(
            {"id": store.allocate("grp"), "type": "peer_group", **payload.model_dump()}
        )

    @api.put("/groups/{gid}")
    def edit_group(gid: str, payload: GroupInput):
        store = get_store()
        old = store.get(gid)
        if old["type"] != "peer_group":
            raise ValueError("请选择行业/竞对组")
        for cid in payload.company_ids:
            company(store, cid)
        version = {
            "id": store.allocate("grv"),
            "type": "group_version",
            "parent_id": gid,
            "before": old,
            "after": payload.model_dump(),
            "created_at": now(),
        }
        with store.transaction() as con:
            store._insert(con, version)
            updated = {**old, **payload.model_dump(), "updated_at": now()}
            con.execute("UPDATE objects SET body=%s WHERE id=%s", (dumps(updated), gid))
        return updated

    @api.post("/groups/{gid}/runs", status_code=202)
    def group_run(gid: str, payload: GroupRunInput):
        store = get_store()
        group = store.get(gid)
        if group["type"] != "peer_group":
            raise ValueError("请选择行业/竞对组")
        if not config.settings()["api_key"]:
            raise ValueError("请先配置 DeepSeek")
        runs, errors = [], []
        for cid in group["company_ids"]:
            securities = store.list("security", cid)
            try:
                if not securities:
                    raise ValueError("缺少证券")
                run = enqueue(
                    RunInput(
                        company_id=cid,
                        symbol=securities[0]["symbol"],
                        question=payload.question
                        + f" 本次行业组：{group['name']}。同行比较须同期间、单位和会计口径，不能强行排序。",
                    )
                )
                runs.append(
                    store.update(
                        run["id"],
                        peer_group_id=gid,
                        peer_company_ids=group["company_ids"],
                    )
                )
            except ValueError as exc:
                errors.append({"company_id": cid, "error": str(exc)})
        return {"runs": runs, "errors": errors}

    @api.get("/groups/{gid}")
    def group_detail(gid: str):
        store = get_store()
        group = store.get(gid)
        if group["type"] != "peer_group":
            raise ValueError("请选择行业/竞对组")
        members, aligned = [], {}
        for cid in group["company_ids"]:
            c = company(store, cid)
            metrics = [
                r
                for r in store.list("metric", cid, limit=1000)
                if r["review_status"] == "confirmed"
            ]
            members.append(
                {
                    "company": c,
                    "reports": store.list("report", cid, limit=3),
                    "metrics": metrics,
                    "industry_evidence": [
                        e
                        for e in store.list("evidence", cid)
                        if e.get("topic") == "industry"
                    ][:3],
                    "runs": store.list("run", cid, limit=3),
                }
            )
            for record in metrics:
                m = record["data"]
                key = tuple(
                    m[k]
                    for k in (
                        "metric",
                        "period_start",
                        "period_end",
                        "unit",
                        "currency",
                        "basis",
                        "category",
                    )
                )
                aligned.setdefault(key, {})
                aligned[key].setdefault(cid, record)
        table = [
            {
                "metric": key[0],
                "period_start": key[1],
                "period_end": key[2],
                "unit": key[3],
                "currency": key[4],
                "basis": key[5],
                "category": key[6],
                "members": values,
            }
            for key, values in aligned.items()
        ]
        return {
            "group": group,
            "members": members,
            "aligned_metrics": table,
            "gaps": [m["company"]["id"] for m in members if not m["metrics"]],
        }

    @api.get("/export/{cid}")
    def export(cid: str):
        result = archive(get_store(), cid)
        return {"format_version": 1, "exported_at": now(), **result}

    return api
