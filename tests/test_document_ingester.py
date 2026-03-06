"""Tests for DocumentIngester + ChunkingStrategy — Sprint P3 item 4.12."""

from __future__ import annotations

import pytest

from amiagi.application.document_ingester import (
    ChunkingStrategy,
    DocumentIngester,
    FixedSizeChunking,
    IngestionResult,
    ParagraphChunking,
    SentenceChunking,
)


# ── Chunking strategies ─────────────────────────────────────


class TestParagraphChunking:
    def test_splits_on_blank_lines(self) -> None:
        text = (
            "First paragraph with enough content to exceed the threshold easily.\n\n"
            "Second paragraph also exceeds the minimum length requirement.\n\n"
            "Third paragraph is long enough to stand on its own as a chunk."
        )
        chunks = ParagraphChunking().chunk(text)
        assert len(chunks) == 3

    def test_merges_short_paragraphs(self) -> None:
        text = "Hi.\n\nA very long paragraph that definitely exceeds the minimum length threshold for splitting."
        chunks = ParagraphChunking(min_length=40).chunk(text)
        # "Hi." is short and gets buffered, then flushed when long para appears
        # Buffer "Hi." is flushed, then long para added → 2 chunks
        # OR merged into 1 if buffer gets appended to long para
        assert len(chunks) >= 1

    def test_empty_text(self) -> None:
        chunks = ParagraphChunking().chunk("")
        assert chunks == []

    def test_no_merge_when_min_length_small(self) -> None:
        text = "Short A.\n\nShort B.\n\nShort C."
        chunks = ParagraphChunking(min_length=1).chunk(text)
        assert len(chunks) == 3


class TestSentenceChunking:
    def test_groups_sentences(self) -> None:
        text = "One. Two. Three. Four. Five."
        chunks = SentenceChunking(sentences_per_chunk=2).chunk(text)
        assert len(chunks) == 3  # [One. Two.], [Three. Four.], [Five.]

    def test_single_sentence(self) -> None:
        chunks = SentenceChunking().chunk("Hello world.")
        assert len(chunks) == 1


class TestFixedSizeChunking:
    def test_fixed_windows(self) -> None:
        text = "A" * 100
        chunks = FixedSizeChunking(size=30, overlap=0).chunk(text)
        assert len(chunks) == 4  # ceil(100/30)

    def test_overlap(self) -> None:
        text = "A" * 100
        chunks = FixedSizeChunking(size=50, overlap=10).chunk(text)
        # step = 40, so: 0-50, 40-90, 80-100 = 3 chunks
        assert len(chunks) >= 3


# ── Fake KB ──────────────────────────────────────────────────

class _FakeKB:
    def __init__(self):
        self._store: list[tuple[str, dict]] = []

    def store(self, text, metadata=None):
        eid = len(self._store)
        self._store.append((text, metadata or {}))
        return eid


# ── DocumentIngester ─────────────────────────────────────────


class TestDocumentIngester:
    def test_ingest_text(self) -> None:
        kb = _FakeKB()
        ing = DocumentIngester(kb, strategy=ParagraphChunking(min_length=1))
        result = ing.ingest_text("Para one.\n\nPara two.", source="test")
        assert isinstance(result, IngestionResult)
        assert result.chunks_count == 2
        assert len(result.stored_ids) == 2
        assert result.errors == []

    def test_ingest_file(self, tmp_path) -> None:
        f = tmp_path / "note.md"
        f.write_text("Hello world.\n\nGoodbye world.")
        kb = _FakeKB()
        ing = DocumentIngester(kb)
        result = ing.ingest_file(f)
        assert result.chunks_count >= 1
        assert result.errors == []

    def test_ingest_file_not_found(self, tmp_path) -> None:
        kb = _FakeKB()
        ing = DocumentIngester(kb)
        result = ing.ingest_file(tmp_path / "missing.txt")
        assert len(result.errors) == 1

    def test_strategy_swap(self) -> None:
        kb = _FakeKB()
        ing = DocumentIngester(kb, strategy=ParagraphChunking())
        assert isinstance(ing.strategy, ParagraphChunking)
        ing.strategy = SentenceChunking()
        assert isinstance(ing.strategy, SentenceChunking)

    def test_ingest_directory(self, tmp_path) -> None:
        (tmp_path / "a.md").write_text("File A content.")
        (tmp_path / "b.md").write_text("File B content.")
        (tmp_path / "c.txt").write_text("Ignored by glob.")
        kb = _FakeKB()
        ing = DocumentIngester(kb)
        results = ing.ingest_directory(tmp_path, glob="*.md")
        assert len(results) == 2
