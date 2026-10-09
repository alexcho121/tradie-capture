"""SQLite access. One short-lived connection per operation (safe with FastAPI's threadpool).

Tables
  projects  - a job/site and its list of site contacts (used as an STT / extraction hint)
  captures  - one row per button-press capture; derived columns are *materialised* from events
  events    - append-only, per-capture, hash-chained log (see ledger.py)

File paths are stored relative to MEDIA_DIR and served under /media/<path>.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  site_contacts TEXT NOT NULL DEFAULT '[]',
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captures (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES projects(id),
  created_at      TEXT NOT NULL,
  audio_path      TEXT,
  photo_path      TEXT,
  audio_sha256    TEXT,
  photo_sha256    TEXT,
  transcript      TEXT,
  fields          TEXT NOT NULL DEFAULT '{}',
  status          TEXT NOT NULL DEFAULT 'captured',
  notice_deadline TEXT,
  deadline_note   TEXT,
  user_note       TEXT,
  extract_meta    TEXT NOT NULL DEFAULT '{}',
  client_meta     TEXT NOT NULL DEFAULT '{}',
  location        TEXT,
  client_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_captures_project ON captures(project_id, created_at);

CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  capture_id TEXT NOT NULL REFERENCES captures(id),
  seq        INTEGER NOT NULL,
  at         TEXT NOT NULL,
  type       TEXT NOT NULL,
  payload    TEXT NOT NULL,
  prev_hash  TEXT NOT NULL,
  hash       TEXT NOT NULL,
  UNIQUE (capture_id, seq)
);
"""


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text: Optional[str], default: Any = None) -> Any:
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def init(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            # tiny migration for databases created by v1 (before location / client_id existed)
            have = {r["name"] for r in conn.execute("PRAGMA table_info(captures)")}
            for column in ("location", "client_id", "user_note"):
                if column not in have:
                    conn.execute(f"ALTER TABLE captures ADD COLUMN {column} TEXT")
            # one capture per client-generated id, so an offline retry can't create a duplicate
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_captures_client_id "
                         "ON captures(client_id) WHERE client_id IS NOT NULL")
        finally:
            conn.close()

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """Read-only style access (no explicit transaction)."""
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Write transaction. BEGIN IMMEDIATE takes the write lock up front so that
        event sequence numbers cannot race."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()
