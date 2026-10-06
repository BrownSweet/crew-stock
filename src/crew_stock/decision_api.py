"""Research plans and explicitly local paper accounts."""

import json

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .decisions import (
    BudgetInput,
    PaperAccountInput,
    PaperBuy,
    PlanRequest,
    account_numbers,
    calculate_budget,
    cancel_order,
    create_account,
    expire_orders,
    object_type,
    paper_buy,
    paper_close,
    start_plan,
)
from .store import now


class Tracking(BaseModel):
    enabled: bool


class ReviewInput(BaseModel):
    thesis_change: str = Field(min_length=1, max_length=4000)
    process_review: str = Field(min_length=1, max_length=4000)
    next_action: str = Field(min_length=1, max_length=2000)


def router(get_store):
    api = APIRouter(prefix="/api/decisions")

    @api.get("")
    def overview(company_id: str | None = None):
        s = get_store()
        return {
            k: s.list(k, company_id)
            for k in ("trade_plan", "plan_job", "paper_decision", "paper_review")
        }

    @api.post("/plans", status_code=202)
    def plan(p: PlanRequest):
        return start_plan(get_store(), p)

    @api.get("/accounts")
    def accounts():
        s = get_store()
        expire_orders(s)
        result = []
        for a in s.list("paper_account"):
            with s.connection() as con:
                rows = con.execute(
                    "SELECT body FROM objects WHERE kind IN ('paper_order','paper_fill') ORDER BY created_at DESC,id DESC"
                ).fetchall()
            records = [json.loads(r[0]) for r in rows]
            orders = [
                r
                for r in records
                if r["type"] == "paper_order" and r["account_id"] == a["id"]
            ]
            fills = [
                r
                for r in records
                if r["type"] == "paper_fill" and r["account_id"] == a["id"]
            ]
            equity, free = account_numbers(a, orders)
            result.append(
                {
                    **a,
                    "equity": str(equity),
                    "available_cash": str(free),
                    "orders": orders,
                    "fills": fills[:100],
                }
            )
        return result

    @api.post("/accounts", status_code=201)
    def account(p: PaperAccountInput):
        return create_account(get_store(), p)

    @api.post("/plans/{pid}/budget")
    def budget(pid: str, p: BudgetInput):
        s = get_store()
        expire_orders(s)
        a = object_type(s, p.account_id, "paper_account")
        with s.connection() as con:
            orders = [
                json.loads(r[0])
                for r in con.execute(
                    "SELECT body FROM objects WHERE kind='paper_order'"
                )
            ]
        return calculate_budget(
            object_type(s, pid, "trade_plan"),
            a,
            [o for o in orders if o["account_id"] == a["id"]],
            p,
        )

    @api.post("/plans/{pid}/orders", status_code=201)
    def buy(pid: str, p: PaperBuy):
        expire_orders(get_store())
        return paper_buy(get_store(), pid, p)

    @api.post("/orders/{oid}/cancel")
    def cancel(oid: str):
        return cancel_order(get_store(), oid)

    @api.post("/accounts/{aid}/close/{symbol}", status_code=201)
    def close(aid: str, symbol: str):
        return paper_close(get_store(), aid, symbol)

    @api.post("/accounts/{aid}/refresh", status_code=202)
    def refresh(aid: str):
        s = get_store()
        object_type(s, aid, "paper_account")
        return s.update(aid, refresh_requested=True)

    @api.put("/accounts/{aid}/tracking")
    def tracking(aid: str, p: Tracking):
        s = get_store()
        object_type(s, aid, "paper_account")
        return s.update(aid, auto_refresh=p.enabled, refresh_requested=p.enabled)

    @api.post("/journal/{did}/review", status_code=201)
    def review(did: str, p: ReviewInput):
        s = get_store()
        d = object_type(s, did, "paper_decision")
        order = s.get(d["order_id"])
        a = s.get(order["account_id"])
        return s.insert(
            {
                "id": s.allocate("prv"),
                "type": "paper_review",
                "parent_id": d["parent_id"],
                "decision_id": did,
                "order_snapshot": order,
                "account_snapshot": {
                    k: a[k] for k in ("id", "cash", "positions", "realized_pnl")
                },
                **p.model_dump(),
                "reviewed_at": now(),
            }
        )

    return api
