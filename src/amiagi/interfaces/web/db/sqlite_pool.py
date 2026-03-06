"""Async SQLite pool emulating the asyncpg.Pool interface.

Provides a drop-in replacement for *asyncpg.Pool* when PostgreSQL is not
configured, enabling the Web GUI to start locally using a single SQLite
file for persistence.

Limitations compared to the PostgreSQL backend
----------------------------------------------
* Full-text search falls back to ``LIKE`` matching (no tsvector / GIN).
* ``percentile_cont()`` metrics return ``NULL`` (p50 / p95 unavailable).
* Array columns are stored as JSON ``TEXT``.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiosqlite

logger = logging.getLogger(__name__)

# ── Query translator (PostgreSQL → SQLite) ───────────────────

_RE_SCHEMA_PFX = re.compile(r"\bdbo\.")

# Full-text search
_RE_TS_HEADLINE = re.compile(
    r"ts_headline\(\s*'english'\s*,\s*([\w.]+)\s*,\s*to_tsquery\([^)]*\)\s*,"
    r"\s*'[^']*'\s*\)\s+AS\s+(\w+)",
    re.IGNORECASE,
)
_RE_TS_RANK = re.compile(
    r"ts_rank\([^,]+,\s*to_tsquery\([^)]*\)\)\s+AS\s+(\w+)",
    re.IGNORECASE,
)
_RE_TSQUERY_WHERE = re.compile(
    r"(\w+)\s*@@\s*to_tsquery\(\s*'english'\s*,\s*\$(\d+)\s*\)",
    re.IGNORECASE,
)

# Array operators
_RE_ANY_OP = re.compile(r"\$(\d+)\s*=\s*ANY\(([^)]+)\)", re.IGNORECASE)
_RE_CARDINALITY = re.compile(r"\bcardinality\(([^)]+)\)", re.IGNORECASE)

# Aggregates
_RE_FILTER = re.compile(
    r"\bcount\(\*\)\s+FILTER\s*\(\s*WHERE\s+(.+?)\)", re.IGNORECASE,
)
_RE_PERCENTILE = re.compile(
    r"percentile_cont\([^)]+\)\s+WITHIN\s+GROUP\s*\("
    r"\s*ORDER\s+BY\s+[^)]+\)\s+AS\s+(\w+)",
    re.IGNORECASE,
)

# Type casts
_RE_FLOAT_CAST = re.compile(r"::float\b", re.IGNORECASE)
_RE_TYPE_CAST = re.compile(r"::\w+")

# Parameters & misc
_RE_PARAM = re.compile(r"\$(\d+)")
_RE_ILIKE = re.compile(r"\bILIKE\b", re.IGNORECASE)
_RE_QUESTION_N = re.compile(r"\?(\d+)")


def _translate(sql: str) -> str:
    """Translate PostgreSQL DML to SQLite dialect.

    Handles: ``$N`` → ``?N``, type-cast removal, ``dbo.`` prefix,
    ``ANY()``, ``cardinality()``, ``FILTER``, ``percentile_cont()``,
    full-text-search fallback, ``ILIKE``, ``now()``.
    """
    stripped = sql.strip()
    up = stripped.upper()
    if up.startswith("SET SEARCH_PATH") or up.startswith("CREATE SCHEMA"):
        return ""

    q = sql

    # 1. Schema prefix
    q = _RE_SCHEMA_PFX.sub("", q)

    # 2. Full-text search → LIKE fallback (before general $N rewrite)
    q = _RE_TS_HEADLINE.sub(r"substr(\1, 1, 200) AS \2", q)
    q = _RE_TS_RANK.sub(r"1.0 AS \1", q)
    q = _RE_TSQUERY_WHERE.sub(r"content LIKE '%' || ?\2 || '%'", q)

    # 3. Array operators (consumes $N, emits ?N)
    q = _RE_ANY_OP.sub(
        r"EXISTS(SELECT 1 FROM json_each(\2) WHERE value = ?\1)", q,
    )
    q = _RE_CARDINALITY.sub(r"json_array_length(\1)", q)

    # 4. Aggregates
    q = _RE_FILTER.sub(r"SUM(CASE WHEN \1 THEN 1 ELSE 0 END)", q)
    q = _RE_PERCENTILE.sub(r"NULL AS \1", q)

    # 5. Type casts  (::float → *1.0 before generic removal)
    q = _RE_FLOAT_CAST.sub(" * 1.0 ", q)
    q = _RE_TYPE_CAST.sub("", q)

    # 6. Remaining $N → ?N
    q = _RE_PARAM.sub(r"?\1", q)

    # 7. Functions
    q = q.replace("now()", "datetime('now')").replace("NOW()", "datetime('now')")
    q = q.replace("gen_random_uuid()", "lower(hex(randomblob(16)))")

    # 8. ILIKE → LIKE (SQLite LIKE is already case-insensitive for ASCII)
    q = _RE_ILIKE.sub("LIKE", q)

    return q


def _rewrite_params(
    sql: str, args: tuple[Any, ...],
) -> tuple[str, tuple[Any, ...]]:
    """Convert ``?N`` numbered placeholders to positional ``?`` and reorder *args*."""
    refs: list[int] = []

    def _replacer(m: re.Match[str]) -> str:
        refs.append(int(m.group(1)))
        return "?"

    new_sql = _RE_QUESTION_N.sub(_replacer, sql)
    if not refs:
        return new_sql, args
    try:
        new_args = tuple(args[i - 1] for i in refs)
    except IndexError:
        logger.warning(
            "Parameter index out of range: refs=%s, len(args)=%d\nSQL: %s",
            refs, len(args), sql[:300],
        )
        return new_sql, args
    return new_sql, new_args


# ── Value conversion helpers ─────────────────────────────────

def _prepare_params(args: tuple[Any, ...]) -> tuple[Any, ...]:
    """Convert Python types to SQLite-compatible values."""
    out: list[Any] = []
    for a in args:
        if a is None:
            out.append(None)
        elif isinstance(a, (list, tuple)) and not isinstance(a, (bytes, str)):
            out.append(json.dumps(a, ensure_ascii=False))
        elif isinstance(a, dict):
            out.append(json.dumps(a, ensure_ascii=False))
        elif hasattr(a, "hex") and hasattr(a, "int"):
            # UUID-like object
            out.append(str(a))
        elif isinstance(a, bool):
            out.append(int(a))
        elif isinstance(a, (_dt.datetime, _dt.date)):
            # datetime / date → ISO string for TEXT columns
            out.append(a.strftime("%Y-%m-%d %H:%M:%S"))
        else:
            out.append(a)
    return tuple(out)


def _maybe_parse_json(val: Any) -> Any:
    """Attempt to deserialise JSON arrays / objects stored as TEXT."""
    if isinstance(val, str) and len(val) >= 2 and val[0] in ("[", "{"):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            pass
    return val


# ── Record ───────────────────────────────────────────────────

class SqliteRecord:
    """Dict-like record emulating ``asyncpg.Record``."""

    __slots__ = ("_data",)

    def __init__(self, keys: tuple[str, ...], values: tuple[Any, ...]) -> None:
        self._data: dict[str, Any] = {
            k: _maybe_parse_json(v) for k, v in zip(keys, values)
        }

    # -- dict-like access --------------------------------------------------

    def __getitem__(self, key: str | int) -> Any:  # type: ignore[override]
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self) -> Any:
        return self._data.keys()

    def values(self) -> Any:
        return self._data.values()

    def items(self) -> Any:
        return self._data.items()

    def __repr__(self) -> str:
        return f"<SqliteRecord {self._data}>"


# ── Helpers ──────────────────────────────────────────────────

def _is_write(sql: str) -> bool:
    cmd = sql.strip().split(maxsplit=1)[0].upper()
    return cmd in {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE"}


def _status_tag(sql: str, rowcount: int) -> str:
    """Return an asyncpg-style status tag, e.g. ``INSERT 0 1``."""
    cmd = sql.strip().split(maxsplit=1)[0].upper()
    if cmd == "INSERT":
        return f"INSERT 0 {rowcount}"
    return f"{cmd} {rowcount}"


# ── Connection wrapper ───────────────────────────────────────

class SqliteConnection:
    """Wraps an ``aiosqlite.Connection`` with asyncpg-compatible methods."""

    def __init__(self, conn: aiosqlite.Connection, lock: asyncio.Lock) -> None:
        self._conn = conn
        self._lock = lock

    async def fetch(self, query: str, *args: Any) -> list[SqliteRecord]:
        q = _translate(query)
        if not q.strip():
            return []
        p = _prepare_params(args)
        q, p = _rewrite_params(q, p)
        async with self._lock:
            cur = await self._conn.execute(q, p)
            rows = await cur.fetchall()
            if _is_write(q):
                await self._conn.commit()
            if cur.description is None:
                return []
            keys = tuple(c[0] for c in cur.description)
            return [SqliteRecord(keys, tuple(r)) for r in rows]

    async def fetchrow(self, query: str, *args: Any) -> SqliteRecord | None:
        q = _translate(query)
        if not q.strip():
            return None
        p = _prepare_params(args)
        q, p = _rewrite_params(q, p)
        async with self._lock:
            cur = await self._conn.execute(q, p)
            row = await cur.fetchone()
            if _is_write(q):
                await self._conn.commit()
            if row is None or cur.description is None:
                return None
            keys = tuple(c[0] for c in cur.description)
            return SqliteRecord(keys, tuple(row))

    async def fetchval(self, query: str, *args: Any, column: int = 0) -> Any:
        row = await self.fetchrow(query, *args)
        if row is None:
            return None
        vals = list(row.values())
        return vals[column] if column < len(vals) else None

    async def execute(self, query: str, *args: Any) -> str:
        q = _translate(query)
        if not q.strip():
            return "SKIP 0"
        p = _prepare_params(args)
        q, p = _rewrite_params(q, p)
        async with self._lock:
            cur = await self._conn.execute(q, p)
            await self._conn.commit()
            return _status_tag(q, cur.rowcount)

    async def executemany(self, query: str, args_list: list[Any]) -> None:
        q = _translate(query)
        if not q.strip():
            return
        rows = [_rewrite_params(q, _prepare_params(tuple(a)))[1] for a in args_list]
        q_final = _rewrite_params(q, _prepare_params(tuple(args_list[0])))[0] if args_list else q
        async with self._lock:
            await self._conn.executemany(q_final, rows)
            await self._conn.commit()


# ── Pool ─────────────────────────────────────────────────────

class SqlitePool:
    """Async SQLite pool emulating ``asyncpg.Pool``."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> aiosqlite.Connection:
        if self._conn is not None:
            # Verify the connection is still usable.
            try:
                await self._conn.execute("SELECT 1")
            except Exception:
                logger.warning("SQLite connection stale — reconnecting: %s", self._db_path)
                try:
                    await self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None

        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA foreign_keys=ON")
            await self._conn.commit()
            logger.info("SQLite connection opened: %s (WAL mode)", self._db_path)
        return self._conn

    # -- Pool-level query helpers (acquire → run → release) ---------------

    async def fetch(self, query: str, *args: Any) -> list[SqliteRecord]:
        conn = await self._ensure()
        return await SqliteConnection(conn, self._lock).fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> SqliteRecord | None:
        conn = await self._ensure()
        return await SqliteConnection(conn, self._lock).fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any, column: int = 0) -> Any:
        conn = await self._ensure()
        return await SqliteConnection(conn, self._lock).fetchval(
            query, *args, column=column,
        )

    async def execute(self, query: str, *args: Any) -> str:
        conn = await self._ensure()
        return await SqliteConnection(conn, self._lock).execute(query, *args)

    async def executemany(self, query: str, args_list: list[Any]) -> None:
        conn = await self._ensure()
        await SqliteConnection(conn, self._lock).executemany(query, args_list)

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[SqliteConnection]:
        """Yield a connection wrapper (mimics ``asyncpg`` pool acquire)."""
        conn = await self._ensure()
        yield SqliteConnection(conn, self._lock)

    async def close(self) -> None:
        """Close the underlying connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("SQLite connection closed: %s", self._db_path)

    # -- Introspection used by the dashboard status endpoint ---------------

    def get_size(self) -> int:
        """Total pool size (always 1 for SQLite)."""
        return 1

    def get_idle_size(self) -> int:
        """Idle connections in the pool."""
        return 1 if self._conn else 0
