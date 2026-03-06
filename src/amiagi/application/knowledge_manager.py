"""KnowledgeManager — orchestrates ingestion, search and lifecycle for knowledge bases.

A thin application-layer facade that delegates storage to
``KnowledgeBase`` and ingestion to ``DocumentIngester`` while keeping
routing / web concerns outside.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from amiagi.application.document_ingester import (
    ChunkingStrategy,
    DocumentIngester,
    IngestionResult,
    ParagraphChunking,
)
from amiagi.infrastructure.knowledge_base import KnowledgeBase, KnowledgeEntry

logger = logging.getLogger(__name__)


class KnowledgeManager:
    """High-level orchestrator for knowledge management.

    Wraps a ``KnowledgeBase`` instance and provides a unified API for
    ingestion (via ``DocumentIngester``), search, and statistics.

    Usage::

        kb = KnowledgeBase(db_path=Path("knowledge.db"))
        manager = KnowledgeManager(kb)
        result = manager.ingest_file(Path("notes/report.md"))
        entries = manager.search("project timeline", top_k=5)
    """

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        *,
        chunking_strategy: ChunkingStrategy | None = None,
    ) -> None:
        self._kb = knowledge_base
        self._strategy = chunking_strategy or ParagraphChunking()
        self._ingester = DocumentIngester(self._kb, strategy=self._strategy)

    # ── Properties ───────────────────────────────────────────

    @property
    def knowledge_base(self) -> KnowledgeBase:
        return self._kb

    @property
    def ingester(self) -> DocumentIngester:
        return self._ingester

    @property
    def chunking_strategy(self) -> ChunkingStrategy:
        return self._strategy

    @chunking_strategy.setter
    def chunking_strategy(self, value: ChunkingStrategy) -> None:
        self._strategy = value
        self._ingester.strategy = value

    # ── Ingestion ────────────────────────────────────────────

    def ingest_text(
        self,
        text: str,
        *,
        source: str = "raw",
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        """Chunk and store raw text."""
        return self._ingester.ingest_text(text, source=source, metadata=metadata)

    def ingest_file(
        self,
        path: Path,
        *,
        encoding: str = "utf-8",
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        """Ingest a single file."""
        return self._ingester.ingest_file(path, encoding=encoding, metadata=metadata)

    def ingest_directory(
        self,
        directory: Path,
        *,
        glob: str = "**/*.md",
        encoding: str = "utf-8",
        metadata: dict[str, Any] | None = None,
    ) -> list[IngestionResult]:
        """Ingest all matching files in a directory."""
        return self._ingester.ingest_directory(
            directory, glob=glob, encoding=encoding, metadata=metadata,
        )

    # ── Search ───────────────────────────────────────────────

    def search(self, query: str, *, top_k: int = 5) -> list[KnowledgeEntry]:
        """Run a TF-IDF-based search and return ranked results."""
        return self._kb.query(query, top_k=top_k)

    # ── Direct storage ───────────────────────────────────────

    def store(self, text: str, metadata: dict[str, Any] | None = None) -> int:
        """Store a single chunk directly (bypasses ingester)."""
        return self._kb.store(text, metadata=metadata)

    def delete(self, entry_id: int) -> bool:
        """Delete a single entry by id."""
        return self._kb.delete(entry_id)

    # ── Stats / lifecycle ────────────────────────────────────

    def count(self) -> int:
        """Return total number of stored entries."""
        return self._kb.count()

    def stats(self) -> dict[str, Any]:
        """Return summary statistics."""
        count = self.count()
        return {
            "chunks_count": count,
            "strategy": type(self._strategy).__name__,
        }
