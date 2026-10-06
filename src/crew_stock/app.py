import os
import subprocess
import threading
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field, SecretStr
from pymysql import MySQLError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config
from .auth import COOKIE, authenticate
from .auth import router as auth_router
from .decision_api import router as decision_router
from .deployment import mount_public_path
from .extended_api import recover_extensions, tick_extensions
from .extended_api import router as extended_router
from .longbridge import auth_status, cli
from .models import CompanyInput, RunInput
from .order_api import router as order_router
from .research import execute_run
from .store import Store, now
from .workspace_api import router as workspace_router

store = Store()
stop = threading.Event()
worker_thread = None
ACTIVE = {"queued", "collecting", "analyzing"}


def enqueue(payload: RunInput) -> dict:
    company = store.get(payload.company_id)
    securities = store.list("security", payload.company_id)
    if company["type"] != "company" or payload.symbol not in {
        s["symbol"] for s in securities
    }:
        raise ValueError("证券不属于所选公司")
    if payload.source_document_id:
        doc = store.get(payload.source_document_id)
        if doc["type"] != "source_document" or not store.belongs_to(
            doc, {payload.company_id}
        ):
            raise ValueError("原始资料不属于所选公司")
    if payload.mode != "collect" and not config.settings()["api_key"]:
        raise ValueError("请在连接设置中保存并测试 DeepSeek API Key")
    active = [
        r
        for r in store.list("run")
        if r.get("status") in ACTIVE and not r.get("historical")
    ]
    if len(active) >= 10:
        raise ValueError("运行队列已满，请等待已有任务完成")
    rid = store.allocate("run")
    s = config.public_settings()
    return store.insert(
        {
            "id": rid,
            "type": "run",
            "parent_id": payload.company_id,
            **payload.model_dump(),
            "status": "queued",
            "evidence_ids": [],
            "model": s["model"],
            "base_url": s["base_url"],
            "historical": False,
        }
    )


def worker():
    while not stop.is_set():
        store.check_worker_lease()
        tick_extensions(store, enqueue)
        current = datetime.now(timezone.utc)
        for schedule in store.list("schedule"):
            if (
                not schedule.get("enabled")
                or datetime.fromisoformat(schedule["next_at"]) > current
            ):
                continue
            try:
                active = any(
                    r.get("status") in ACTIVE
                    for r in store.list("run", schedule["parent_id"])
                )
                if not active:
                    run = enqueue(
                        RunInput(
                            company_id=schedule["parent_id"],
                            symbol=schedule["symbol"],
                            mode=schedule["mode"],
                            question=schedule["question"],
                        )
                    )
                    store.update(schedule["id"], last_run_id=run["id"], last_error=None)
                store.update(
                    schedule["id"],
                    next_at=(
                        current + timedelta(hours=schedule["interval_hours"])
                    ).isoformat(),
                )
            except (ValueError, KeyError) as exc:
                store.update(schedule["id"], enabled=False, last_error=str(exc))
        queued = [
            r
            for r in store.list("run")
            if r.get("status") == "queued" and not r.get("historical")
        ]
        if queued:
            execute_run(store, queued[-1])
        else:
            stop.wait(1)


@asynccontextmanager
async def lifespan(app):
    global worker_thread
    stack = ExitStack()
    if os.getenv("CREW_STOCK_DISABLE_WORKER") != "1":
        stack.enter_context(store.worker_lease())
    try:
        store.initialize()
        from .discovery import revalidate_pending_records

        revalidate_pending_records(store)
        for run in store.list("run"):
            if run.get("status") in {"collecting", "analyzing"} and not run.get(
                "historical"
            ):
                store.update(
                    run["id"],
                    status="interrupted",
                    error="容器上次退出中断了此运行。证据保留，请创建新运行。",
                    completed_at=now(),
                )
        recover_extensions(store)
        stop.clear()
        thread = threading.Thread(target=worker, name="research-worker", daemon=True)
        if os.getenv("CREW_STOCK_DISABLE_WORKER") != "1":
            thread.start()
        worker_thread = thread
        yield
        stop.set()
        if thread.is_alive():
            thread.join(timeout=2)
    finally:
        stop.set()
        stack.close()


app = FastAPI(
    title="Crew Stock",
    version="0.5.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    + [
        host.strip()
        for host in os.getenv("ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ],
)


@app.middleware("http")
async def local_request_guard(request: Request, call_next):
    path = request.url.path.removeprefix(request.scope.get("root_path", ""))
    if path == "/api/assets" and request.method == "POST":
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > 13 * 1024 * 1024):
            return JSONResponse({"detail": "上传请求超过 13 MiB"}, status_code=413)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if request.headers.get("x-crew-stock") != "1" or (
            origin and origin != f"{request.url.scheme}://{request.url.netloc}"
        ):
            return JSONResponse({"detail": "只接受本地应用的操作请求"}, status_code=403)
    public = {"/api/health", "/api/auth/status", "/api/auth/setup", "/api/auth/login"}
    if path.startswith("/api/") and path not in public:
        try:
            username = await run_in_threadpool(
                authenticate, store, request.cookies.get(COOKIE, "")
            )
        except MySQLError:
            return JSONResponse(
                {"detail": "数据库暂不可用，请检查 MySQL 连接"}, status_code=503
            )
        if not username:
            return JSONResponse(
                {"detail": "请先登录工作台"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        request.state.account_name = username
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    if path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(KeyError)
async def missing(request, exc):
    return JSONResponse({"detail": "记录不存在"}, status_code=404)


@app.exception_handler(MySQLError)
async def database_unavailable(request, exc):
    return JSONResponse(
        {"detail": "数据库暂不可用，请检查 MySQL 连接"}, status_code=503
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        {
            "detail": [
                {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ]
        },
        status_code=422,
    )


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/health")
def health():
    if os.getenv("CREW_STOCK_DISABLE_WORKER") != "1" and not (
        worker_thread and worker_thread.is_alive()
    ):
        raise HTTPException(503, "后台工作器未运行，请重启容器并检查日志")
    with store.connection() as con:
        con.execute("SELECT 1")
    return {
        "status": "ok",
        "service": "crew-stock",
        "version": "0.5.0",
        "crewai": "1.15.22",
        "read_only_data_tools": True,
        "database": "mysql",
    }


@app.get("/api/status")
def status():
    return {
        "deepseek": config.public_settings(),
        "longbridge": auth_status(),
        "skills": [p.parent.name for p in config.SKILLS.glob("*/SKILL.md")],
        "storage": "MySQL 业务记录与任务日志 + /data 原始文件",
        "trade_tools": False,
    }


@app.get("/api/companies")
def companies():
    return [
        {**c, "securities": store.list("security", c["id"])}
        for c in store.list("company")
    ]


@app.post("/api/companies", status_code=201)
def create_company(payload: CompanyInput):
    return store.create_company(payload.model_dump())


@app.get("/api/objects")
def objects(
    kind: Literal[
        "evidence",
        "report",
        "document",
        "run",
        "submission",
        "opinion",
        "industry",
        "decision",
        "schedule",
    ],
    company_id: str | None = None,
):
    rows = store.list(kind, limit=1000)
    if company_id:
        company = store.get(company_id)
        allowed = {company_id, *company.get("security_ids", [])}
        rows = [r for r in rows if store.belongs_to(r, allowed)]
    return rows


@app.get("/api/objects/{object_id}")
def get_object(object_id: str):
    body = store.get(object_id)
    return {**body, "markdown": store.seed_text(body)}


@app.get("/api/evidence/{evidence_id}/raw")
def raw_evidence(evidence_id: str):
    body = store.get(evidence_id)
    if body["type"] != "evidence" or not body.get("blob"):
        raise HTTPException(404, "无原始载荷")
    return Response(
        store.read_blob(body["blob"]), media_type="text/plain; charset=utf-8"
    )


@app.get("/api/attachments/{attachment_id}")
def attachment(attachment_id: str):
    body = store.get(attachment_id)
    if body["type"] != "attachment" or not body.get("seed_path"):
        raise HTTPException(404)
    path = (config.SEED / body["seed_path"]).with_suffix(".jpg").resolve()
    if not path.is_relative_to(config.SEED.resolve()) or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.post("/api/runs", status_code=202)
def run(payload: RunInput):
    return enqueue(payload)


class ReviewInput(BaseModel):
    decision: Literal["confirmed", "rejected"]
    note: str = Field(min_length=1, max_length=4000)


@app.post("/api/reports/{report_id}/review")
def review(report_id: str, payload: ReviewInput):
    return store.review(report_id, payload.decision, payload.note)


class SubmissionInput(BaseModel):
    company_id: str = Field(pattern=r"^cmp-\d{6}$")
    text: str = Field(min_length=1, max_length=30000)
    source_url: str = Field(default="", max_length=2000)
    annotation: str = Field(default="", max_length=8000)


@app.post("/api/submissions", status_code=201)
def submit(payload: SubmissionInput):
    if store.get(payload.company_id)["type"] != "company":
        raise ValueError("请选择公司档案")
    sid, eid = store.allocate("sub"), store.allocate("evd")
    blob = store.blob(payload.text.encode())
    body = store.insert(
        {
            "id": sid,
            "type": "submission",
            "parent_id": payload.company_id,
            **payload.model_dump(),
            "display_name": payload.text[:60],
            "classification": "user_provided_unverified",
            "review_status": "pending_confirmation",
            "evidence_ids": [eid],
        }
    )
    store.insert(
        {
            "id": eid,
            "type": "evidence",
            "parent_id": payload.company_id,
            "submission_id": sid,
            "display_name": "用户提交原文",
            "source": "user",
            "transport": "form",
            "blob": blob,
            "content_hash": blob,
            "as_of": None,
            "captured_at": now(),
            "acquisition_status": "success",
            "content_classification": "opinion",
            "original_url": payload.source_url,
        }
    )
    return body


class SettingsInput(BaseModel):
    api_key: SecretStr | None = None
    model: str = Field(
        default="deepseek-flash", pattern=r"^deepseek-[a-zA-Z0-9._-]{1,60}$"
    )
    base_url: Literal["https://api.deepseek.com", "https://api.deepseek.com/v1"] = (
        "https://api.deepseek.com"
    )


@app.put("/api/settings")
def settings(payload: SettingsInput):
    values = payload.model_dump(exclude={"api_key"})
    if payload.api_key:
        values["api_key"] = payload.api_key.get_secret_value().strip()
    config.save_settings(values)
    return config.public_settings()


@app.post("/api/settings/test-deepseek")
def test_deepseek():
    s = config.settings()
    if not s["api_key"]:
        raise ValueError("请先保存 API Key")
    try:
        with OpenAI(
            api_key=s["api_key"], base_url=s["base_url"], timeout=25, max_retries=0
        ) as client:
            result = client.chat.completions.create(
                model=s["model"],
                messages=[{"role": "user", "content": "只回复 OK"}],
                max_tokens=16,
                extra_body={"thinking": {"type": "disabled"}},
            )
        ok = bool(result.choices and result.choices[0].message.content)
        if not ok:
            raise ValueError("模型没有返回正文")
        return {"ok": True, "model": result.model, "checked_at": now()}
    except Exception as exc:
        raise HTTPException(
            502,
            f"DeepSeek 连接测试失败（{type(exc).__name__}），请检查 Key、额度、模型名称和网络。",
        ) from None


class AuthInput(BaseModel):
    code: SecretStr = Field(min_length=1, max_length=4096)


@app.post("/api/longbridge/authorize")
def authorize(payload: AuthInput):
    code = payload.code.get_secret_value().strip()
    try:
        result = cli(
            [
                os.getenv("LONGBRIDGE_BIN", "longbridge"),
                "auth",
                "login",
                "--auth-code",
                code,
                "--client-name",
                "Crew Stock Docker",
            ],
            45,
        )
        if result.returncode:
            # Some CLI error paths may echo the authorization code. Never return raw output.
            raise ValueError("长桥授权失败，请检查授权码是否过期、网络是否可达")
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("长桥授权连接失败或超时") from None
    return auth_status()


@app.post("/api/longbridge/test")
def test_longbridge():
    from .longbridge import command_for

    try:
        result = cli(command_for("quote", "NVDA.US"), 25)
        if result.returncode:
            raise ValueError("行情测试失败，请检查长桥授权、网络及美股行情权限")
        import json

        data = json.loads(result.stdout)
        if not data or (
            isinstance(data, dict) and (data.get("error") or data.get("isError"))
        ):
            raise ValueError("长桥未返回有效行情")
        return {"ok": True, "symbol": "NVDA.US", "checked_at": now()}
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("长桥行情测试超时或 CLI 无法运行") from None


class ScheduleInput(BaseModel):
    company_id: str = Field(pattern=r"^cmp-\d{6}$")
    symbol: str
    interval_hours: int = Field(default=24, ge=1, le=720)
    mode: Literal["collect", "research"] = "collect"
    question: str = Field(
        default="检查本次与上次资料的变化、反方证据和数据缺口。",
        min_length=1,
        max_length=4000,
    )


@app.post("/api/schedules", status_code=201)
def create_schedule(payload: ScheduleInput):
    request = RunInput(
        company_id=payload.company_id,
        symbol=payload.symbol,
        mode=payload.mode,
        question=payload.question,
    )
    if request.symbol not in {
        s["symbol"] for s in store.list("security", request.company_id)
    }:
        raise ValueError("证券不属于公司")
    if any(
        s["parent_id"] == payload.company_id and s["symbol"] == request.symbol
        for s in store.list("schedule")
    ):
        raise ValueError("此证券已有计划，请启用已有计划")
    return store.insert(
        {
            "id": store.allocate("sta"),
            "type": "schedule",
            "parent_id": payload.company_id,
            **payload.model_dump(),
            "symbol": request.symbol,
            "enabled": False,
            "next_at": (
                datetime.now(timezone.utc) + timedelta(hours=payload.interval_hours)
            ).isoformat(),
        }
    )


class ToggleInput(BaseModel):
    enabled: bool


@app.put("/api/schedules/{schedule_id}")
def toggle_schedule(schedule_id: str, payload: ToggleInput):
    body = store.get(schedule_id)
    if body["type"] != "schedule":
        raise KeyError(schedule_id)
    if (
        payload.enabled
        and body["mode"] == "research"
        and not config.settings()["api_key"]
    ):
        raise ValueError("请先配置 DeepSeek")
    return store.update(
        schedule_id,
        enabled=payload.enabled,
        last_error=None,
        next_at=(
            datetime.now(timezone.utc) + timedelta(hours=body["interval_hours"])
        ).isoformat(),
    )


@app.get("/")
def index():
    return FileResponse(config.ROOT / "web" / "index.html")


app.include_router(auth_router(lambda: store))
app.include_router(workspace_router(lambda: store, enqueue))
app.include_router(extended_router(lambda: store))
app.include_router(decision_router(lambda: store))
app.include_router(order_router(lambda: store))

app.mount("/static", StaticFiles(directory=config.ROOT / "web"), name="static")

# Mounted apps do not run their lifespan automatically; the wrapper owns it.
application = mount_public_path(app, config.BASE_PATH)
