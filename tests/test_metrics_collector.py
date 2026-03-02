"""Tests for MetricsCollector — ring buffer, flush, query, summary."""

from __future__ import annotations

import time
from pathlib import Path

from amiagi.infrastructure.metrics_collector import MetricPoint, MetricsCollector


class TestMetricsCollector:
    def test_record_and_query(self, tmp_path: Path) -> None:
        mc = MetricsCollector(db_path=tmp_path / "m.db", auto_flush_every=9999)
        mc.record("test.metric", 42.0)
        mc.record("test.metric", 43.0)
        results = mc.query("test.metric", last_n=10)
        assert len(results) >= 2
        values = [p.value for p in results]
        assert 42.0 in values
        assert 43.0 in values

    def test_record_with_tags(self, tmp_path: Path) -> None:
        mc = MetricsCollector(db_path=tmp_path / "m.db", auto_flush_every=9999)
        mc.record("tagged", 1.0, tags={"agent": "polluks"})
        results = mc.query("tagged", last_n=1)
        assert len(results) == 1
        assert results[0].tags.get("agent") == "polluks"

    def test_flush(self, tmp_path: Path) -> None:
        mc = MetricsCollector(db_path=tmp_path / "m.db", auto_flush_every=9999)
        for i in range(10):
            mc.record("flush.test", float(i))
        mc.flush()
        # After flush, the DB should have the data
        results = mc.query("flush.test", last_n=20)
        assert len(results) >= 10

    def test_auto_flush(self, tmp_path: Path) -> None:
        """Auto-flush triggers after auto_flush_every records."""
        mc = MetricsCollector(db_path=tmp_path / "m.db", auto_flush_every=5)
        for i in range(10):
            mc.record("auto", float(i))
        # Should have flushed at least once automatically
        results = mc.query("auto", last_n=20)
        assert len(results) >= 5

    def test_summary(self, tmp_path: Path) -> None:
        mc = MetricsCollector(db_path=tmp_path / "m.db", auto_flush_every=9999)
        mc.record("sum.test", 10.0)
        mc.record("sum.test", 20.0)
        mc.record("sum.test", 30.0)
        summary = mc.summary()
        assert "sum.test" in summary
        stats = summary["sum.test"]
        assert stats["count"] == 3
        assert stats["sum"] == 60.0
        assert stats["min"] == 10.0
        assert stats["max"] == 30.0
        assert abs(stats["avg"] - 20.0) < 0.01

    def test_query_since(self, tmp_path: Path) -> None:
        mc = MetricsCollector(db_path=tmp_path / "m.db", auto_flush_every=9999)
        mc.record("since.test", 1.0)
        cutoff = time.time()
        time.sleep(0.05)
        mc.record("since.test", 2.0)
        results = mc.query("since.test", since=cutoff)
        # Should get at least the second record
        assert any(p.value == 2.0 for p in results)

    def test_ring_buffer_limits(self, tmp_path: Path) -> None:
        mc = MetricsCollector(
            db_path=tmp_path / "m.db",
            buffer_size=5,
            auto_flush_every=9999,
        )
        for i in range(10):
            mc.record("ring", float(i))
        # Buffer holds max 5
        results = mc.query("ring", last_n=100)
        assert len(results) <= 5

    def test_empty_query(self, tmp_path: Path) -> None:
        mc = MetricsCollector(db_path=tmp_path / "m.db")
        results = mc.query("nonexistent")
        assert results == []

    def test_empty_summary(self, tmp_path: Path) -> None:
        mc = MetricsCollector(db_path=tmp_path / "m.db")
        assert mc.summary() == {}
