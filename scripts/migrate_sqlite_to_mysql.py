"""One-time, read-only legacy import. SQLite is never used by the running app."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from crew_stock.store import Store

TABLES = {"objects": 5, "sequences": 2, "symbols": 2, "meta": 2, "object_links": 2}


def fingerprint(rows):
    encoded = [
        json.dumps(list(row), ensure_ascii=False, separators=(",", ":")) for row in rows
    ]
    return hashlib.sha256("\n".join(sorted(encoded)).encode()).hexdigest()


def migrate(source: Path, target: Path):
    if source.resolve().is_relative_to(
        target.resolve()
    ) or target.resolve().is_relative_to(source.resolve()):
        raise ValueError("迁移须使用新的数据目录/数据卷，保留旧目录用于回滚")
    database = source / "research.sqlite3"
    if not database.is_file():
        raise ValueError("未找到旧 research.sqlite3")
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="crew-migration-") as folder:
        snapshot = sqlite3.connect(str(Path(folder) / "snapshot.sqlite3"))
        legacy = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            legacy.backup(snapshot)
        finally:
            legacy.close()
        try:
            if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("原数据库完整性检查失败")
            rows = {
                table: snapshot.execute(f"SELECT * FROM {table}").fetchall()
                for table in TABLES
            }
        finally:
            snapshot.close()
    blobs = list((source / "blobs").glob("*"))
    for blob in blobs:
        if (
            not blob.is_file()
            or hashlib.sha256(blob.read_bytes()).hexdigest() != blob.name
        ):
            raise ValueError("原始文件哈希校验失败")
    names = {p.name for p in blobs}
    for row in rows["objects"]:
        body = json.loads(row[4])
        for field in ("blob", "original_blob"):
            if body.get(field) and body[field] not in names:
                raise ValueError(f"记录 {row[0]} 缺少原始文件")
    store = Store(target)
    store.initialize(import_seed=False)
    task_outputs = []
    cache = source / "crewai" / "latest_kickoff_task_outputs.db"
    if cache.exists():
        cached = sqlite3.connect(cache.resolve().as_uri() + "?mode=ro", uri=True)
        cached.row_factory = sqlite3.Row
        try:
            for item in cached.execute("SELECT * FROM latest_kickoff_task_outputs"):
                entry = dict(item)
                for field in ("output", "inputs"):
                    if isinstance(entry.get(field), str):
                        entry[field] = json.loads(entry[field])
                task_outputs.append(
                    (
                        "legacy-cache",
                        entry["task_index"],
                        json.dumps(entry, ensure_ascii=False),
                    )
                )
        finally:
            cached.close()
    with store.worker_lease(), store.transaction() as con:
        for table in (*TABLES, "accounts", "sessions", "crew_task_outputs"):
            if con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                raise ValueError("目标 MySQL 必须为空；不会覆盖已有研究或账号")
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if relative.parts[0] in {"backups", "crewai"} or path.name in {
                "research.sqlite3",
                "research.sqlite3-wal",
                "research.sqlite3-shm",
                "worker.lock",
            }:
                continue
            if path.is_symlink():
                raise ValueError("源目录包含符号链接，请先确认并转换为普通文件")
            if not path.is_file():
                continue
            destination = target / relative
            if destination.exists() and destination.read_bytes() != path.read_bytes():
                raise ValueError(f"目标文件已存在且不一致：{relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            if (
                hashlib.sha256(destination.read_bytes()).digest()
                != hashlib.sha256(path.read_bytes()).digest()
            ):
                raise ValueError(f"复制后文件校验不一致：{relative}")
        for table, columns in TABLES.items():
            con.executemany(
                f"INSERT INTO {table} VALUES ({','.join(['%s'] * columns)})",
                rows[table],
            )
            copied = con.execute(f"SELECT * FROM {table}").fetchall()
            if fingerprint(copied) != fingerprint(rows[table]):
                raise ValueError(f"表 {table} 迁移后校验不一致，已回滚事务")
        # An empty legacy database should also stay empty after starting the app.
        con.execute(
            "INSERT IGNORE INTO meta VALUES ('seed_imported', 'legacy-migration')"
        )
        con.executemany("INSERT INTO crew_task_outputs VALUES (%s,%s,%s)", task_outputs)
        if fingerprint(
            con.execute("SELECT * FROM crew_task_outputs").fetchall()
        ) != fingerprint(task_outputs):
            raise ValueError("旧 CrewAI 任务缓存校验失败，已回滚事务")
    return {
        "tables": {table: len(values) for table, values in rows.items()},
        "blobs": len(blobs),
        "legacy_task_outputs": len(task_outputs),
        "verified": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="停用旧容器并备份后，将 SQLite 一次性导入空 MySQL"
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source_dir, args.target_dir), ensure_ascii=False))
