"""Tests for HumanFeedbackCollector."""

from __future__ import annotations

from pathlib import Path

from amiagi.interfaces.human_feedback import FeedbackEntry, HumanFeedbackCollector


class TestHumanFeedbackCollector:
    def test_record_and_count(self, tmp_path: Path) -> None:
        c = HumanFeedbackCollector(tmp_path / "feedback.jsonl")
        c.record(FeedbackEntry(agent_id="a", rating=1))
        assert c.count() == 1

    def test_thumbs_up(self, tmp_path: Path) -> None:
        c = HumanFeedbackCollector(tmp_path / "feedback.jsonl")
        entry = c.thumbs_up("polluks", comment="Great")
        assert entry.rating == 1
        assert c.count() == 1

    def test_thumbs_down(self, tmp_path: Path) -> None:
        c = HumanFeedbackCollector(tmp_path / "feedback.jsonl")
        entry = c.thumbs_down("polluks", comment="Bad")
        assert entry.rating == -1

    def test_query_by_agent(self, tmp_path: Path) -> None:
        c = HumanFeedbackCollector(tmp_path / "feedback.jsonl")
        c.thumbs_up("a")
        c.thumbs_down("b")
        c.thumbs_up("a")
        assert len(c.query(agent_id="a")) == 2
        assert len(c.query(agent_id="b")) == 1

    def test_query_by_rating(self, tmp_path: Path) -> None:
        c = HumanFeedbackCollector(tmp_path / "feedback.jsonl")
        c.thumbs_up("a")
        c.thumbs_down("a")
        assert len(c.query(rating=1)) == 1
        assert len(c.query(rating=-1)) == 1

    def test_query_limit(self, tmp_path: Path) -> None:
        c = HumanFeedbackCollector(tmp_path / "feedback.jsonl")
        for i in range(10):
            c.thumbs_up("a")
        assert len(c.query(limit=3)) == 3

    def test_count_by_agent(self, tmp_path: Path) -> None:
        c = HumanFeedbackCollector(tmp_path / "feedback.jsonl")
        c.thumbs_up("a")
        c.thumbs_up("a")
        c.thumbs_down("b")
        assert c.count("a") == 2
        assert c.count("b") == 1

    def test_summary(self, tmp_path: Path) -> None:
        c = HumanFeedbackCollector(tmp_path / "feedback.jsonl")
        c.thumbs_up("a")
        c.thumbs_up("a")
        c.thumbs_down("a")
        c.thumbs_up("b")
        s = c.summary()
        assert s["a"]["positive"] == 2
        assert s["a"]["negative"] == 1
        assert s["a"]["total"] == 3
        assert s["b"]["positive"] == 1

    def test_persistence_writes_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "feedback.jsonl"
        c = HumanFeedbackCollector(path)
        c.thumbs_up("a", comment="test")
        content = path.read_text(encoding="utf-8")
        assert '"agent_id": "a"' in content
        assert '"rating": 1' in content

    def test_query_newest_first(self, tmp_path: Path) -> None:
        import time
        c = HumanFeedbackCollector(tmp_path / "feedback.jsonl")
        c.record(FeedbackEntry(agent_id="a", rating=1, timestamp=100.0))
        c.record(FeedbackEntry(agent_id="a", rating=-1, timestamp=200.0))
        entries = c.query()
        assert entries[0].timestamp > entries[1].timestamp
