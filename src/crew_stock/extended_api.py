from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import config
from .assets import MAX_BYTES, save_upload
from .filing import assign_asset, classify_asset
from .industries import (
    IndustryInput,
    IndustryRunInput,
    IndustrySchedule,
    industry,
    save_industry,
    start_batch,
)
from .influence import (
    RelationInput,
    SimulationInput,
    graph,
    review_relation,
    save_relation,
    simulate,
)
from .store import now


class Review(BaseModel):
    decision: str
    note: str = Field(min_length=1, max_length=3000)


class AssetLinks(BaseModel):
    target_ids: list[str] = Field(max_length=30)
    note: str = Field(min_length=1, max_length=3000)


class Suggest(BaseModel):
    evidence_ids: list[str] = Field(min_length=1, max_length=15)


def router(get_store):
    api = APIRouter(prefix="/api")

    @api.get("/industries")
    def industries():
        return get_store().list("industry")

    @api.post("/industries", status_code=201)
    def create(payload: IndustryInput):
        return save_industry(get_store(), payload)

    @api.put("/industries/{iid}")
    def update(iid: str, payload: IndustryInput):
        return save_industry(get_store(), payload, iid)

    @api.get("/industries/{iid}")
    def detail(iid: str):
        s = get_store()
        ind = industry(s, iid)
        return {
            **ind,
            "companies": [
                {
                    **s.get(m["company_id"]),
                    "role": m.get("role", ""),
                    "securities": s.list("security", m["company_id"]),
                    "latest_reports": s.list("report", m["company_id"], limit=2),
                }
                for m in ind.get("members", [])
            ],
            "runs": s.list("industry_run", iid),
            "reports": s.list("industry_report", iid),
            "schedules": s.list("industry_schedule", iid),
            "versions": s.list("industry_version", iid),
            "evidence": s.list("evidence", iid),
        }

    @api.post("/industries/{iid}/runs", status_code=202)
    def run(iid: str, payload: IndustryRunInput):
        return start_batch(get_store(), iid, payload)

    @api.put("/industries/{iid}/schedule")
    def schedule(iid: str, payload: IndustrySchedule):
        s = get_store()
        ind = industry(s, iid)
        if payload.enabled and not ind.get("members"):
            raise ValueError("行业没有成员，请先添加公司")
        if (
            payload.enabled
            and payload.mode == "research"
            and not config.settings()["api_key"]
        ):
            raise ValueError("请先配置 DeepSeek")
        existing = s.list("industry_schedule", iid)
        fields = {
            **payload.model_dump(),
            "next_at": (
                datetime.now(timezone.utc) + timedelta(hours=payload.interval_hours)
            ).isoformat(),
            "last_error": None,
        }
        if existing:
            return s.update(existing[0]["id"], **fields)
        return s.insert(
            {
                "id": s.allocate("isc"),
                "type": "industry_schedule",
                "parent_id": iid,
                **fields,
            }
        )

    @api.post("/assets", status_code=202)
    def upload(
        target_id: str = Form(""),
        start_page: int = Form(1),
        end_page: int = Form(10),
        file: UploadFile = File(...),
    ):
        try:
            content = file.file.read(MAX_BYTES + 1)
            return save_upload(
                get_store(),
                target_id,
                file.filename or "",
                content,
                start_page,
                end_page,
            )
        finally:
            file.file.close()

    @api.get("/assets")
    def assets(target_id: str | None = None):
        return get_store().list("asset_job", target_id or None)

    @api.put("/assets/{aid}/links")
    def asset_links(aid: str, payload: AssetLinks):
        return assign_asset(get_store(), aid, payload.target_ids, payload.note)

    @api.post("/assets/{aid}/classify", status_code=202)
    def reclassify(aid: str):
        s = get_store()
        a = s.get(aid)
        if a["type"] != "asset_job" or not a.get("source_document_id"):
            raise ValueError("请先完成文件解析")
        if a.get("classification_status") in {"queued", "analyzing"}:
            raise ValueError("归类正在进行")
        return s.update(aid, classification_status="queued", classification_error=None)

    @api.get("/assets/{aid}/original")
    def original(aid: str):
        s = get_store()
        a = s.get(aid)
        if a["type"] != "asset_job":
            raise ValueError("请选择原始文件")
        return Response(
            s.read_blob(a["original_blob"]),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(a["filename"])
            },
        )

    @api.post("/assets/{aid}/retry", status_code=202)
    def retry(aid: str):
        s = get_store()
        a = s.get(aid)
        if a["type"] != "asset_job" or a["status"] not in {"failed", "interrupted"}:
            raise ValueError("只能重试失败或中断的解析")
        return save_upload(
            s,
            a["parent_id"],
            a["filename"],
            s.read_blob(a["original_blob"]),
            a["start_page"],
            a["end_page"],
        )

    @api.get("/graph/{scope_id}")
    def get_graph(
        scope_id: str, depth: int = Query(2, ge=1, le=6), proposals: bool = False
    ):
        return graph(get_store(), scope_id, depth, proposals)

    @api.post("/relations", status_code=201)
    def create_relation(payload: RelationInput):
        return save_relation(get_store(), payload)

    @api.put("/relations/{rid}")
    def edit_relation(rid: str, payload: RelationInput):
        return save_relation(get_store(), payload, rid)

    @api.get("/relations/{rid}/versions")
    def versions(rid: str):
        return get_store().list("relation_version", rid)

    @api.post("/relations/{rid}/review")
    def review(rid: str, payload: Review):
        return review_relation(get_store(), rid, payload.decision, payload.note)

    @api.post("/graph/{scope_id}/suggest", status_code=202)
    def suggest(scope_id: str, payload: Suggest):
        s = get_store()
        graph(s, scope_id, 1)
        if not config.settings()["api_key"]:
            raise ValueError("请先配置 DeepSeek")
        for eid in payload.evidence_ids:
            e = s.get(eid)
            if (
                e["type"] != "evidence"
                or not e.get("blob")
                or e.get("acquisition_status") != "success"
            ):
                raise ValueError("证据不可用")
        return s.insert(
            {
                "id": s.allocate("grj"),
                "type": "graph_job",
                "parent_id": scope_id,
                "status": "queued",
                **payload.model_dump(),
            }
        )

    @api.get("/graph/{scope_id}/jobs")
    def graph_jobs(scope_id: str):
        return get_store().list("graph_job", scope_id, limit=20)

    @api.post("/simulations", status_code=201)
    def scenario(payload: SimulationInput):
        return simulate(get_store(), payload)

    return api


def tick_extensions(store, enqueue):
    from .assets import process_asset
    from .decisions import tick_decisions

    if tick_decisions(store):
        return
    from .industries import tick_industries
    from .influence import suggest_relations

    assets = [j for j in store.list("asset_job") if j["status"] == "queued"]
    if assets:
        process_asset(store, assets[-1])
        return
    pending = [
        j for j in store.list("asset_job") if j.get("classification_status") == "queued"
    ]
    if pending:
        classify_asset(store, pending[-1])
        return
    from .orders import tick_orders

    if tick_orders(store):
        return
    jobs = [j for j in store.list("graph_job") if j["status"] == "queued"]
    if jobs:
        suggest_relations(store, jobs[-1])
        return
    tick_industries(store, enqueue)


def recover_extensions(store):
    for a in store.list("paper_account"):
        if a.get("refresh_status") == "running":
            store.update(a["id"], refresh_status="idle", refresh_requested=True)
    for j in store.list("asset_job"):
        if j.get("classification_status") == "analyzing":
            store.update(j["id"], classification_status="queued")
    for kind, statuses in [
        ("asset_job", {"parsing"}),
        ("graph_job", {"analyzing"}),
        ("plan_job", {"analyzing"}),
        ("order_scan", {"analyzing"}),
        ("industry_run", {"summarizing"}),
    ]:
        for job in store.list(kind):
            if job["status"] in statuses:
                store.update(
                    job["id"],
                    status="interrupted",
                    error="容器重启中断了此任务，原件和已完成结果保留，请重新发起。",
                    completed_at=now(),
                )
