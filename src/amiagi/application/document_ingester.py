"""Document ingestion pipeline with pluggable chunking strategies.

Classes:
    ChunkingStrategy — abstract chunking interface with built-in variants.
    DocumentIngester — loads files / text, chunks, and feeds to KnowledgeBase.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)


# ── Chunking Strategies ─────────────────────────────────────


class ChunkingStrategy(ABC):
    """Base class for text-chunking strategies."""

    @abstractmethod
    def chunk(self, text: str) -> list[str]:
        """Split *text* into a list of chunk strings."""


class ParagraphChunking(ChunkingStrategy):
    """Split on double newlines (paragraphs).

    Empty chunks are dropped.  Very short paragraphs (< *min_length*)
    are merged with the next paragraph.
    """

    def __init__(self, *, min_length: int = 40) -> None:
        self._min_length = min_length

    def chunk(self, text: str) -> list[str]:
        raw = re.split(r"\n\s*\n", text)
        chunks: list[str] = []
        buffer = ""
        for part in raw:
            part = part.strip()
            if not part:
                continue
            if len(part) < self._min_length and buffer:
                buffer += "\n\n" + part
            elif len(part) < self._min_length:
                buffer = part
            else:
                if buffer:
                    chunks.append(buffer)
                    buffer = ""
                chunks.append(part)
        if buffer:
            chunks.append(buffer)
        return chunks


class SentenceChunking(ChunkingStrategy):
    """Split on sentence boundaries (. ! ?) and group into *n* sentences."""

    def __init__(self, *, sentences_per_chunk: int = 3) -> None:
        self._n = max(sentences_per_chunk, 1)

    def chunk(self, text: str) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        chunks: list[str] = []
        for i in range(0, len(sentences), self._n):
            group = " ".join(sentences[i : i + self._n]).strip()
            if group:
                chunks.append(group)
        return chunks


class FixedSizeChunking(ChunkingStrategy):
    """Split into fixed-size character windows with overlap."""

    def __init__(self, *, size: int = 500, overlap: int = 50) -> None:
        self._size = max(size, 1)
        self._overlap = max(overlap, 0)

    def chunk(self, text: str) -> list[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = start + self._size
            c = text[start:end].strip()
            if c:
                chunks.append(c)
            start += self._size - self._overlap
        return chunks


# ── Ingestion Result ─────────────────────────────────────────


@dataclass
class IngestionResult:
    """Summary returned by the ingester."""

    source: str
    chunks_count: int = 0
    stored_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "chunks_count": self.chunks_count,
            "stored_ids": self.stored_ids,
            "errors": self.errors,
        }


# ── Document Ingester ────────────────────────────────────────


class DocumentIngester:
    """Loads text from files or raw strings, chunks it, and stores chunks.

    Works with any object that exposes ``store(text, metadata=)``
    (i.e. ``KnowledgeBase``).

    Usage::

        ingester = DocumentIngester(kb, strategy=ParagraphChunking())
        result = ingester.ingest_file(Path("report.md"))
        result = ingester.ingest_text("long raw text …", source="paste")
    """

    def __init__(
        self,
        knowledge_base: Any,
        *,
        strategy: ChunkingStrategy | None = None,
    ) -> None:
        self._kb = knowledge_base
        self._strategy = strategy or ParagraphChunking()

    @property
    def strategy(self) -> ChunkingStrategy:
        return self._strategy

    @strategy.setter
    def strategy(self, value: ChunkingStrategy) -> None:
        self._strategy = value

    def ingest_text(
        self,
        text: str,
        *,
        source: str = "raw",
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        """Chunk *text* and store each chunk."""
        result = IngestionResult(source=source)
        chunks = self._strategy.chunk(text)
        result.chunks_count = len(chunks)
        meta = dict(metadata or {})
        meta["source"] = source

        for i, chunk in enumerate(chunks):
            try:
                eid = self._kb.store(chunk, metadata={**meta, "chunk_index": i})
                result.stored_ids.append(eid)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"chunk {i}: {exc}")
                logger.debug("Ingestion error for chunk %d: %s", i, exc)

        return result

    def ingest_file(
        self,
        path: Path,
        *,
        encoding: str = "utf-8",
        metadata: dict[str, Any] | None = None,
    ) -> IngestionResult:
        """Read a file and ingest its content."""
        try:
            text = path.read_text(encoding=encoding)
        except Exception as exc:
            return IngestionResult(source=str(path), errors=[str(exc)])
        meta = dict(metadata or {})
        meta["filename"] = path.name
        return self.ingest_text(text, source=str(path), metadata=meta)

    def ingest_directory(
        self,
        directory: Path,
        *,
        glob: str = "**/*.md",
        encoding: str = "utf-8",
        metadata: dict[str, Any] | None = None,
    ) -> list[IngestionResult]:
        """Ingest all files matching *glob* in *directory*."""
        results: list[IngestionResult] = []
        for fpath in sorted(directory.glob(glob)):
            if fpath.is_file():
                results.append(self.ingest_file(fpath, encoding=encoding, metadata=metadata))
        return results
