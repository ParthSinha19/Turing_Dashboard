import sqlite3
from pathlib import Path

from . import config


def connect(path=None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and seed the school records if the database is empty."""
    schema = (config.APP_DIR / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    from .seed import seed_schools
    seed_schools(conn)
    conn.commit()


def get_conn():
    """FastAPI dependency: one connection per request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
