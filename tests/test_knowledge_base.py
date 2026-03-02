"""Tests for KnowledgeBase (Phase 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from amiagi.infrastructure.knowledge_base import KnowledgeBase, KnowledgeEntry


@pytest.fixture()
def kb(tmp_path: Path) -> KnowledgeBase:
    return KnowledgeBase(db_path=tmp_path / "kb.db")


class TestKnowledgeBase:
    def test_store_and_count(self, kb: KnowledgeBase) -> None:
        assert kb.count() == 0
        kb.store("Python is a language")
        assert kb.count() == 1

    def test_store_returns_id(self, kb: KnowledgeBase) -> None:
        eid = kb.store("first entry")
        assert isinstance(eid, int)
        assert eid >= 1

    def test_query_returns_relevant(self, kb: KnowledgeBase) -> None:
        kb.store("Python is great for data science")
        kb.store("JavaScript is used in web browsers")
        kb.store("Python machine learning frameworks include TensorFlow")
        results = kb.query("python data", top_k=2)
        assert len(results) <= 2
        # The Python-related entries should score higher
        assert results[0].score > 0

    def test_query_empty_db(self, kb: KnowledgeBase) -> None:
        assert kb.query("anything") == []

    def test_query_empty_question(self, kb: KnowledgeBase) -> None:
        kb.store("some data here")
        assert kb.query("") == []

    def test_delete_entry(self, kb: KnowledgeBase) -> None:
        eid = kb.store("to be deleted")
        assert kb.count() == 1
        assert kb.delete(eid)
        assert kb.count() == 0

    def test_delete_nonexistent(self, kb: KnowledgeBase) -> None:
        assert not kb.delete(9999)

    def test_metadata_round_trip(self, kb: KnowledgeBase) -> None:
        meta = {"source": "test", "priority": 1}
        eid = kb.store("entry with metadata", metadata=meta)
        results = kb.query("entry metadata", top_k=1)
        assert len(results) == 1
        assert results[0].metadata == meta

    def test_top_k_limit(self, kb: KnowledgeBase) -> None:
        for i in range(10):
            kb.store(f"document number {i} with common words")
        results = kb.query("document common words", top_k=3)
        assert len(results) == 3

    def test_score_ordering(self, kb: KnowledgeBase) -> None:
        kb.store("alpha bravo charlie")
        kb.store("alpha alpha alpha")
        kb.store("delta echo foxtrot")
        results = kb.query("alpha", top_k=3)
        # Results should be sorted by score descending
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score
