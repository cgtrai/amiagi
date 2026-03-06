"""Tests for KnowledgeManager — Sprint P3 item 4.12."""

from __future__ import annotations

import pytest
from pathlib import Path

from amiagi.application.knowledge_manager import KnowledgeManager
from amiagi.application.document_ingester import ParagraphChunking, SentenceChunking
from amiagi.infrastructure.knowledge_base import KnowledgeBase, KnowledgeEntry


# ── Fake KnowledgeBase (same API as real one) ────────────────

class _FakeKB(KnowledgeBase):
    """Test double — same public API, no SQLite dependency."""

    def __init__(self) -> None:  # type: ignore[override]
        self._entries: list[dict] = []

    def store(self, text, metadata=None):
        eid = len(self._entries)
        self._entries.append({"id": eid, "text": text, "metadata": metadata or {}})
        return eid

    def query(self, question, top_k=5):
        results = [
            KnowledgeEntry(
                entry_id=e["id"],
                text=e["text"],
                metadata=e["metadata"],
                created_at=0.0,
                score=0.99,
            )
            for e in self._entries
            if question.lower() in e["text"].lower()
        ]
        return results[:top_k]

    def count(self):
        return len(self._entries)

    def delete(self, entry_id):
        for i, e in enumerate(self._entries):
            if e["id"] == entry_id:
                self._entries.pop(i)
                return True
        return False


# ── Tests ────────────────────────────────────────────────────

class TestKnowledgeManager:
    def test_ingest_text(self) -> None:
        kb = _FakeKB()
        mgr = KnowledgeManager(kb)
        result = mgr.ingest_text("first para.\n\nsecond para.")
        assert result.chunks_count >= 1
        assert mgr.count() >= 1

    def test_search(self) -> None:
        kb = _FakeKB()
        mgr = KnowledgeManager(kb)
        mgr.store("artificial intelligence is great")
        entries = mgr.search("artificial")
        assert len(entries) == 1

    def test_delete(self) -> None:
        kb = _FakeKB()
        mgr = KnowledgeManager(kb)
        eid = mgr.store("deletable")
        assert mgr.count() == 1
        mgr.delete(eid)
        assert mgr.count() == 0

    def test_stats(self) -> None:
        kb = _FakeKB()
        mgr = KnowledgeManager(kb)
        mgr.store("entry one")
        mgr.store("entry two")
        stats = mgr.stats()
        assert stats["chunks_count"] == 2
        assert stats["strategy"] == "ParagraphChunking"

    def test_change_strategy(self) -> None:
        kb = _FakeKB()
        mgr = KnowledgeManager(kb)
        assert isinstance(mgr.chunking_strategy, ParagraphChunking)
        mgr.chunking_strategy = SentenceChunking()
        assert isinstance(mgr.chunking_strategy, SentenceChunking)
        assert isinstance(mgr.ingester.strategy, SentenceChunking)

    def test_ingest_file(self, tmp_path) -> None:
        f = tmp_path / "test.md"
        f.write_text("File content here.\n\nSecond paragraph.")
        kb = _FakeKB()
        mgr = KnowledgeManager(kb)
        result = mgr.ingest_file(f)
        assert result.chunks_count >= 1
        assert mgr.count() >= 1

    def test_ingest_directory(self, tmp_path) -> None:
        (tmp_path / "a.md").write_text("Alpha content.")
        (tmp_path / "b.md").write_text("Beta content.")
        kb = _FakeKB()
        mgr = KnowledgeManager(kb)
        results = mgr.ingest_directory(tmp_path, glob="*.md")
        assert len(results) == 2
        assert mgr.count() >= 2

    def test_properties(self) -> None:
        kb = _FakeKB()
        mgr = KnowledgeManager(kb)
        assert mgr.knowledge_base is kb
        assert mgr.ingester is not None
