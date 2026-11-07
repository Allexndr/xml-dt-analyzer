"""One small database layer for SQLite (local, tests) and Postgres (``DATABASE_URL``, e.g. Neon on Vercel).

Modules write SQLite-flavoured SQL with ``?`` placeholders and explicit ``BEGIN``/``COMMIT``; for Postgres
the adapter translates placeholders and schema types. Rows support both ``row["col"]`` and ``row[0]``.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path


def pg_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or None


class Row(dict):
    """dict row that also supports positional access like sqlite3.Row."""

    def __init__(self, cols, values):
        super().__init__(zip(cols, values))
        self._values = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    def _cols(self):
        return [d.name for d in self._cur.description] if self._cur.description else []

    def fetchone(self):
        r = self._cur.fetchone()
        return Row(self._cols(), r) if r is not None else None

    def fetchall(self):
        cols = self._cols()
        return [Row(cols, r) for r in self._cur.fetchall()]


def _translate(sql: str) -> str:
    sql = re.sub(r"\bBEGIN IMMEDIATE\b", "BEGIN", sql)
    sql = sql.replace("?", "%s")
    return sql


def _translate_schema(script: str) -> list[str]:
    script = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", script)
    script = re.sub(r"\bREAL\b", "DOUBLE PRECISION", script)
    script = re.sub(r"\bBLOB\b", "BYTEA", script)
    return [s.strip() for s in script.split(";") if s.strip() and not s.strip().upper().startswith("PRAGMA")]


class PgConn:
    """Autocommit connection; explicit BEGIN/COMMIT/ROLLBACK statements delimit transactions.

    One connection is reused per thread (opening a TLS connection to a managed Postgres costs
    ~100–300 ms); ``close()`` keeps it open, a broken connection is replaced on next use."""

    def __init__(self, url: str):
        import psycopg

        self._c = psycopg.connect(url, autocommit=True, prepare_threshold=None, connect_timeout=10)

    def execute(self, sql: str, params=()):
        cur = self._c.cursor()
        cur.execute(_translate(sql), tuple(params))
        return _PgCursor(cur)

    def executescript(self, script: str) -> None:
        cur = self._c.cursor()
        for stmt in _translate_schema(script):
            cur.execute(stmt)

    def commit(self) -> None:  # autocommit mode: nothing pending outside explicit transactions
        pass

    def close(self) -> None:
        # leave an open transaction never dangling on a reused connection
        if not self._c.closed and self._c.info.transaction_status != 0:  # 0 = IDLE
            try:
                self._c.execute("ROLLBACK")
            except Exception:
                self._c.close()

    @property
    def healthy(self) -> bool:
        return not self._c.closed and not self._c.broken


_ready: set[str] = set()
_local = threading.local()


def connect(sqlite_path: Path, schema: str):
    """Open a connection (Postgres when configured, else SQLite at ``sqlite_path``) and ensure the schema."""
    url = pg_url()
    if url:
        c = getattr(_local, "pg", None)
        if c is None or not c.healthy:
            c = PgConn(url)
            _local.pg = c
        key = "pg:" + str(hash(schema))
        if key not in _ready:
            c.executescript(schema)
            _ready.add(key)
        return c
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(sqlite_path, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    key = str(sqlite_path) + str(hash(schema))
    if key not in _ready:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(schema)
        _ready.add(key)
    return c
