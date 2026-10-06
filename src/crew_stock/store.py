"""MySQL is the transactional index; immutable raw files are content-addressed."""

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pymysql
import yaml

from . import config
from .database import Connection


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Store:
    def __init__(self, root: Path | None = None, seed: Path | None = None):
        self.root = root or config.DATA
        self.seed = seed if seed is not None else config.SEED
        self.root.mkdir(parents=True, exist_ok=True)
        self.lease_connection = None

    @contextmanager
    def connection(self):
        raw = pymysql.connect(**config.mysql_settings(), autocommit=False)
        con = Connection(raw)
        try:
            yield con
            raw.commit()
        except BaseException:
            raw.rollback()
            raise
        finally:
            con.close()

    @contextmanager
    def transaction(self):
        # Preserve serialized business writes across API threads and processes.
        # Sequence allocation uses its own short row lock, before business writes.
        with self.connection() as con:
            con.execute("SELECT id FROM store_mutex WHERE id=1 FOR UPDATE")
            yield con

    @contextmanager
    def worker_lease(self):
        options = config.mysql_settings()
        name = (
            "crew-worker-"
            + hashlib.sha256(options["database"].encode()).hexdigest()[:40]
        )
        with self.connection() as con:
            if con.execute("SELECT GET_LOCK(%s,0)", (name,)).fetchone()[0] != 1:
                raise RuntimeError("此 MySQL 数据库已有 Crew 后台进程运行")
            self.lease_connection = con.raw
            try:
                yield
            finally:
                self.lease_connection = None
                if con.raw.open:
                    con.execute("SELECT RELEASE_LOCK(%s)", (name,))

    def check_worker_lease(self):
        if self.lease_connection is not None:
            # Never reconnect silently: a new connection no longer owns the lease.
            self.lease_connection.ping(reconnect=False)

    def initialize(self, import_seed=True):
        if import_seed and (self.root / "research.sqlite3").exists():
            raise ValueError(
                "发现旧 SQLite 数据，请先迁入 MySQL 和新的数据卷，再启动应用"
            )
        tables = [
            """CREATE TABLE IF NOT EXISTS objects (
                id VARCHAR(64) PRIMARY KEY, kind VARCHAR(64) NOT NULL,
                parent_id VARCHAR(64), created_at VARCHAR(40) NOT NULL,
                body LONGTEXT NOT NULL, INDEX objects_kind(kind, parent_id)
            )""",
            "CREATE TABLE IF NOT EXISTS sequences (prefix VARCHAR(3) PRIMARY KEY, value INT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS symbols (symbol VARCHAR(64) PRIMARY KEY, company_id VARCHAR(64) NOT NULL)",
            "CREATE TABLE IF NOT EXISTS meta (`key` VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL)",
            """CREATE TABLE IF NOT EXISTS object_links (
                object_id VARCHAR(64) NOT NULL, target_id VARCHAR(64) NOT NULL,
                PRIMARY KEY(object_id, target_id), INDEX links_target(target_id)
            )""",
            "CREATE TABLE IF NOT EXISTS store_mutex (id INT PRIMARY KEY)",
            """CREATE TABLE IF NOT EXISTS accounts (
                id INT PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE,
                password_hash VARCHAR(256) NOT NULL, created_at VARCHAR(40) NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS sessions (
                token_hash CHAR(64) PRIMARY KEY, account_id INT NOT NULL,
                expires_at BIGINT NOT NULL, INDEX sessions_expiry(expires_at)
            )""",
            """CREATE TABLE IF NOT EXISTS crew_task_outputs (
                run_id VARCHAR(64) NOT NULL, task_index INT NOT NULL,
                body LONGTEXT NOT NULL, PRIMARY KEY(run_id, task_index)
            )""",
        ]
        with self.connection() as con:
            for ddl in tables:
                con.execute(
                    ddl + " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
                )
            con.execute("INSERT IGNORE INTO store_mutex VALUES (1)")
        if not import_seed:
            return
        with self.transaction() as con:
            if not con.execute(
                "SELECT 1 FROM meta WHERE `key`='seed_imported'"
            ).fetchone():
                for path in sorted(self.seed.rglob("*.yaml")):
                    if not re.fullmatch(r"[a-z]{3}-\d{6}", path.stem):
                        continue
                    body = yaml.safe_load(path.read_text())
                    if not isinstance(body, dict) or body.get("id") != path.stem:
                        continue
                    body["historical"] = True
                    body["seed_path"] = str(path.relative_to(self.seed))
                    body["imported_at"] = now()
                    prefix, number = path.stem.split("-")
                    con.execute(
                        "INSERT INTO sequences VALUES (%s,%s) ON DUPLICATE KEY UPDATE value=GREATEST(value,VALUES(value))",
                        (prefix, int(number)),
                    )
                    if body["type"] == "evidence":
                        raw = path.with_suffix(".json")
                        if not raw.exists():
                            raw = path.with_suffix(".md")
                        if raw.exists():
                            payload = raw.read_bytes()
                            actual = hashlib.sha256(payload).hexdigest()
                            body["import_file_sha256"] = actual
                            # Original historical hash semantics may differ. Never overwrite them.
                            body["original_hash_matches_file"] = (
                                body.get("content_hash") == actual
                            )
                            body["blob"] = self.blob(payload)
                    self._insert(con, body)
                    if body["type"] == "security":
                        con.execute(
                            "INSERT INTO symbols VALUES (%s,%s)",
                            (body["symbol"], body["company_id"]),
                        )
                con.execute("INSERT INTO meta VALUES ('seed_imported', %s)", (now(),))

    def allocate(self, prefix: str) -> str:
        if not re.fullmatch(r"[a-z]{3}", prefix):
            raise ValueError("Invalid ID prefix")
        with self.connection() as con:
            con.execute(
                "INSERT INTO sequences VALUES (%s,1) ON DUPLICATE KEY UPDATE value=value+1",
                (prefix,),
            )
            row = con.execute(
                "SELECT value FROM sequences WHERE prefix=%s", (prefix,)
            ).fetchone()
            if row[0] > 999999:
                raise ValueError("ID sequence exhausted")
            return f"{prefix}-{row[0]:06d}"

    @staticmethod
    def _insert(con, body):
        con.execute(
            "INSERT INTO objects VALUES (%s,%s,%s,%s,%s)",
            (
                body["id"],
                body["type"],
                body.get("parent_id"),
                str(body.get("created_at") or now()),
                dumps(body),
            ),
        )

    def insert(self, body: dict) -> dict:
        body = {"created_at": now(), **body}
        with self.transaction() as con:
            self._insert(con, body)
        return body

    def get(self, object_id: str) -> dict:
        with self.connection() as con:
            row = con.execute(
                "SELECT body FROM objects WHERE id=%s", (object_id,)
            ).fetchone()
        if row is None:
            raise KeyError(object_id)
        return json.loads(row[0])

    def list(
        self, kind: str, parent_id: str | None = None, limit: int = 500
    ) -> list[dict]:
        with self.connection() as con:
            rows = con.execute(
                "SELECT body FROM objects WHERE kind=%s AND (%s IS NULL OR parent_id=%s OR id IN (SELECT object_id FROM object_links WHERE target_id=%s)) ORDER BY created_at DESC, id DESC LIMIT %s",
                (kind, parent_id, parent_id, parent_id, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def belongs_to(self, body, targets):
        if body.get("parent_id") in targets:
            return True
        with self.connection() as con:
            links = con.execute(
                "SELECT target_id FROM object_links WHERE object_id=%s", (body["id"],)
            ).fetchall()
        return any(row[0] in targets for row in links)

    def update(self, object_id: str, **changes) -> dict:
        with self.transaction() as con:
            row = con.execute(
                "SELECT body FROM objects WHERE id=%s", (object_id,)
            ).fetchone()
            if not row:
                raise KeyError(object_id)
            body = json.loads(row[0])
            body.update(changes, updated_at=now())
            con.execute(
                "UPDATE objects SET body=%s WHERE id=%s", (dumps(body), object_id)
            )
        return body

    def create_company(self, values: dict) -> dict:
        cid = self.allocate("cmp")
        securities = [
            {
                "id": self.allocate("sec"),
                "type": "security",
                "parent_id": cid,
                "company_id": cid,
                "symbol": symbol,
                "display_name": symbol,
                "created_at": now(),
            }
            for symbol in values["symbols"]
        ]
        company = {
            "id": cid,
            "type": "company",
            "display_name": values["name"],
            "research_purpose": values["purpose"],
            "security_ids": [s["id"] for s in securities],
            "thesis_status": "not_established",
            "created_at": now(),
        }
        try:
            with self.transaction() as con:
                self._insert(con, company)
                for security in securities:
                    self._insert(con, security)
                    con.execute(
                        "INSERT INTO symbols VALUES (%s,%s)", (security["symbol"], cid)
                    )
        except pymysql.IntegrityError as exc:
            raise ValueError("证券已归属其他公司，请在现有档案中研究") from exc
        return company

    def blob(self, payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        folder = self.root / "blobs"
        folder.mkdir(exist_ok=True)
        target = folder / digest
        try:
            with target.open("xb") as f:
                f.write(payload)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise ValueError("Evidence integrity failure")
        return digest

    def read_blob(self, digest: str) -> bytes:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid blob")
        payload = (self.root / "blobs" / digest).read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("Evidence integrity failure")
        return payload

    def review(self, report_id: str, decision: str, note: str) -> dict:
        review_id = self.allocate("dec")
        thesis_id = self.allocate("thv")
        with self.transaction() as con:
            row = con.execute(
                "SELECT body FROM objects WHERE id=%s AND kind='report'", (report_id,)
            ).fetchone()
            if not row:
                raise KeyError(report_id)
            report = json.loads(row[0])
            if report["review_status"] != "pending_confirmation":
                raise ValueError("此报告已审阅，不能覆盖历史决定")
            report["review_status"] = decision
            report["reviewed_at"] = now()
            report["review_note"] = note
            con.execute(
                "UPDATE objects SET body=%s WHERE id=%s", (dumps(report), report_id)
            )
            self._insert(
                con,
                {
                    "id": review_id,
                    "type": "decision",
                    "parent_id": report["parent_id"],
                    "report_id": report_id,
                    "decision": decision,
                    "note": note,
                    "created_at": now(),
                },
            )
            if decision == "confirmed":
                row = con.execute(
                    "SELECT body FROM objects WHERE id=%s", (report["parent_id"],)
                ).fetchone()
                company = json.loads(row[0])
                previous = company.get("confirmed_thesis")
                self._insert(
                    con,
                    {
                        "id": thesis_id,
                        "type": "thesis_version",
                        "parent_id": company["id"],
                        "report_id": report_id,
                        "previous_report_id": previous.get("report_id")
                        if previous
                        else None,
                        "before": previous.get("text") if previous else None,
                        "after": report["report"]["thesis_proposal"],
                        "change_type": "established"
                        if not previous
                        else "unchanged"
                        if previous.get("text") == report["report"]["thesis_proposal"]
                        else "changed",
                        "reason": note,
                        "evidence_ids": report.get("evidence_ids", []),
                        "created_at": now(),
                    },
                )
                company["confirmed_thesis"] = {
                    "report_id": report_id,
                    "text": report["report"]["thesis_proposal"],
                    "confirmed_at": now(),
                }
                company["thesis_status"] = "human_confirmed"
                con.execute(
                    "UPDATE objects SET body=%s WHERE id=%s",
                    (dumps(company), company["id"]),
                )
        return report

    def seed_text(self, body: dict) -> str | None:
        if not body.get("seed_path"):
            return None
        path = (self.seed / body["seed_path"]).resolve()
        if not path.is_relative_to(self.seed.resolve()):
            raise ValueError("Invalid seed path")
        text_path = path.with_suffix(".md")
        return text_path.read_text() if text_path.exists() else None

    def export_record(self, body: dict):
        folder = self.root / "records" / body["id"]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{body['id']}.json"
        # Evidence records are append-only; atomic rename avoids partially written files.
        if not path.exists():
            tmp = folder / str(uuid4())
            tmp.write_text(dumps(body))
            tmp.replace(path)
