"""KnowledgeBase — simple TF-IDF-based knowledge store backed by SQLite."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class KnowledgeEntry:
    """A single stored knowledge fragment."""

    entry_id: int
    text: str
    metadata: dict[str, Any]
    created_at: float
    score: float = 0.0


def _tokenise(text: str) -> list[str]:
    """Lowercase word tokenisation."""
    return re.findall(r"[a-zA-Z0-9_]+", text.lower())


class KnowledgeBase:
    """Per-project knowledge store using TF-IDF for retrieval.

    All data is persisted in a SQLite database.  The TF-IDF index is
    rebuilt lazily when a query is issued after new documents have been
    stored (``_dirty`` flag).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._dirty = True

        # IDF cache
        self._idf: dict[str, float] = {}
        self._doc_count = 0

        self._init_db()

    # ---- public API ----

    def store(self, text: str, metadata: dict[str, Any] | None = None) -> int:
        """Store a text fragment. Returns the entry id."""
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        now = time.time()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "INSERT INTO knowledge (text, metadata, created_at) VALUES (?, ?, ?)",
                (text, meta_json, now),
            )
            conn.commit()
            self._dirty = True
            entry_id: int = cur.lastrowid  # type: ignore[assignment]
            conn.close()
        return entry_id

    def query(self, question: str, top_k: int = 5) -> list[KnowledgeEntry]:
        """Return the *top_k* most relevant entries for *question*."""
        q_tokens = _tokenise(question)
        if not q_tokens:
            return []

        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT rowid, text, metadata, created_at FROM knowledge"
            ).fetchall()
            conn.close()

            if not rows:
                return []

            if self._dirty:
                self._rebuild_idf(rows)

            q_tf = Counter(q_tokens)
            q_vec = {t: q_tf[t] * self._idf.get(t, 0.0) for t in q_tf}

            scored: list[KnowledgeEntry] = []
            for row in rows:
                rid, text, meta_json, created = row
                d_tokens = _tokenise(text)
                d_tf = Counter(d_tokens)
                d_vec = {t: d_tf[t] * self._idf.get(t, 0.0) for t in d_tf}

                # cosine similarity
                dot = sum(q_vec.get(t, 0.0) * d_vec.get(t, 0.0) for t in d_vec)
                q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0
                d_norm = math.sqrt(sum(v * v for v in d_vec.values())) or 1.0
                score = dot / (q_norm * d_norm)

                scored.append(
                    KnowledgeEntry(
                        entry_id=rid,
                        text=text,
                        metadata=json.loads(meta_json),
                        created_at=created,
                        score=score,
                    )
                )

            scored.sort(key=lambda e: e.score, reverse=True)
            return scored[:top_k]

    def count(self) -> int:
        """Return total number of stored entries."""
        with self._lock:
            conn = self._connect()
            (n,) = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()
            conn.close()
            return int(n)

    def delete(self, entry_id: int) -> bool:
        """Delete an entry by id. Returns ``True`` if deleted."""
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "DELETE FROM knowledge WHERE rowid = ?", (entry_id,)
            )
            conn.commit()
            deleted = cur.rowcount > 0
            conn.close()
            if deleted:
                self._dirty = True
            return deleted

    # ---- internals ----

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS knowledge ("
            "  text TEXT NOT NULL,"
            "  metadata TEXT NOT NULL DEFAULT '{}',"
            "  created_at REAL NOT NULL"
            ")"
        )
        conn.commit()
        conn.close()

    def _rebuild_idf(self, rows: list[Any]) -> None:
        """Rebuild IDF from all documents."""
        self._doc_count = len(rows)
        df: Counter[str] = Counter()
        for _rid, text, _meta, _ts in rows:
            unique_terms = set(_tokenise(text))
            for t in unique_terms:
                df[t] += 1
        self._idf = {
            t: math.log((self._doc_count + 1) / (freq + 1)) + 1.0
            for t, freq in df.items()
        }
        self._dirty = False
