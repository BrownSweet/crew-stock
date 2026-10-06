from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .orders import (
    PartyInput,
    ProposalInput,
    ScanInput,
    WatchInput,
    alerts,
    approve,
    create_party,
    graph,
    parties,
    propose,
    start_scan,
    typed,
    watch,
)


class Review(BaseModel):
    decision: str
    note: str = Field(min_length=1, max_length=3000)


def router(get_store):
    api = APIRouter(prefix="/api/orders")

    @api.get("")
    def overview(at: datetime | None = None, scope_id: str | None = None):
        s = get_store()
        return {
            **graph(s, at, scope_id),
            "parties": parties(s),
            "proposals": [] if at else s.list("order_proposal"),
            "scans": [
                {k: v for k, v in j.items() if k != "order_snapshots"}
                for j in ([] if at else s.list("order_scan", limit=30))
            ],
            "watches": [] if at else s.list("order_watch"),
        }

    @api.post("/parties", status_code=201)
    def party(p: PartyInput):
        return create_party(get_store(), p)

    @api.post("/proposals", status_code=201)
    def proposal(p: ProposalInput):
        return propose(get_store(), p)

    @api.post("/proposals/{pid}/review")
    def review(pid: str, p: Review):
        return approve(get_store(), pid, p.decision, p.note)

    @api.post("/scans", status_code=202)
    def scan(p: ScanInput):
        return start_scan(get_store(), p)

    @api.get("/{oid}")
    def detail(oid: str):
        s = get_store()
        order = typed(s, oid, "business_order")
        return {
            **order,
            "alerts": alerts(order),
            "versions": s.list("order_version", oid),
            "reviews": s.list("order_review", oid),
            "watch": s.list("order_watch", oid),
            "scans": [
                {k: v for k, v in j.items() if k != "order_snapshots"}
                for j in s.list("order_scan", oid, limit=20)
            ],
        }

    @api.put("/{oid}/watch")
    def monitoring(oid: str, p: WatchInput):
        return watch(get_store(), oid, p)

    return api
