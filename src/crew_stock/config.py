import json
import os
from pathlib import Path
from threading import RLock

ROOT = Path(os.getenv("APP_ROOT", Path(__file__).resolve().parents[2]))
DATA = Path(os.getenv("DATA_DIR", ROOT / "data"))
SEED = ROOT / "seed"
SKILLS = ROOT / "skills"
BASE_PATH = (
    (ROOT / "web" / ".base-path").read_text().strip()
    if (ROOT / "web" / ".base-path").exists()
    else "/"
)
SETTINGS_LOCK = RLock()


def mysql_settings() -> dict:
    def value(name, default=""):
        return os.getenv(name, os.getenv(name.upper(), default))

    required = ("mysql_ip", "mysql_root", "mysql_password", "mysql_database")
    missing = [name for name in required if not value(name)]
    if missing:
        raise ValueError("缺少 MySQL 配置：" + ", ".join(missing))
    try:
        port = int(value("mysql_port", "3306"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("mysql_port 必须为 1–65535") from None
    return {
        "host": value("mysql_ip"),
        "port": port,
        "user": value("mysql_root"),
        "password": value("mysql_password"),
        "database": value("mysql_database"),
        "charset": "utf8mb4",
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
    }


def require_test_database():
    if not mysql_settings()["database"].endswith("_test"):
        raise RuntimeError("验证脚本必须使用独立的 *_test MySQL 数据库")


def settings() -> dict:
    with SETTINGS_LOCK:
        path = DATA / "settings.json"
        saved = json.loads(path.read_text()) if path.exists() else {}
    return {
        "api_key": saved.get("api_key") or os.getenv("DEEPSEEK_API_KEY", ""),
        "model": saved.get("model") or os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "base_url": saved.get("base_url")
        or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    }


def save_settings(values: dict) -> None:
    with SETTINGS_LOCK:
        current = settings()
        current.update(
            {k: v for k, v in values.items() if v is not None and (k != "api_key" or v)}
        )
        DATA.mkdir(parents=True, exist_ok=True)
        temp = DATA / "settings.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(current, f)
        temp.replace(DATA / "settings.json")


def public_settings() -> dict:
    s = settings()
    return {
        "model": s["model"],
        "base_url": s["base_url"],
        "configured": bool(s["api_key"]),
    }
