"""Tests for SessionReplay — JSONL loading, merging, filtering."""

from __future__ import annotations

import json
from pathlib import Path

from amiagi.infrastructure.session_replay import ReplayEvent, SessionReplay


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class TestSessionReplay:
    def test_load_empty_dir(self, tmp_path: Path) -> None:
        replay = SessionReplay(log_dir=tmp_path)
        events = replay.load_session()
        assert events == []

    def test_load_single_log(self, tmp_path: Path) -> None:
        _write_jsonl(tmp_path / "activity.jsonl", [
            {"timestamp": "2025-01-01T10:00:00Z", "action": "test.action", "intent": "Testing"},
            {"timestamp": "2025-01-01T10:01:00Z", "action": "test.action2", "intent": "Testing 2"},
        ])
        replay = SessionReplay(log_dir=tmp_path)
        events = replay.load_session()
        assert len(events) == 2

    def test_events_sorted_by_timestamp(self, tmp_path: Path) -> None:
        _write_jsonl(tmp_path / "activity.jsonl", [
            {"timestamp": "2025-01-01T10:05:00Z", "action": "later"},
            {"timestamp": "2025-01-01T10:00:00Z", "action": "earlier"},
        ])
        replay = SessionReplay(log_dir=tmp_path)
        events = replay.load_session()
        assert events[0].timestamp <= events[1].timestamp

    def test_merge_multiple_logs(self, tmp_path: Path) -> None:
        _write_jsonl(tmp_path / "activity.jsonl", [
            {"timestamp": "2025-01-01T10:00:00Z", "action": "a1"},
        ])
        _write_jsonl(tmp_path / "model_io_executor.jsonl", [
            {"timestamp": "2025-01-01T10:00:30Z", "model": "test"},
        ])
        replay = SessionReplay(log_dir=tmp_path)
        events = replay.load_session()
        assert len(events) == 2
        sources = {e.source for e in events}
        assert "activity.jsonl" in sources
        assert "model_io_executor.jsonl" in sources

    def test_filter_by_sources(self, tmp_path: Path) -> None:
        _write_jsonl(tmp_path / "activity.jsonl", [
            {"timestamp": "2025-01-01T10:00:00Z", "action": "keep"},
        ])
        _write_jsonl(tmp_path / "model_io_executor.jsonl", [
            {"timestamp": "2025-01-01T10:00:30Z", "model": "skip"},
        ])
        replay = SessionReplay(log_dir=tmp_path)
        events = replay.load_session(sources=["activity.jsonl"])
        assert len(events) == 1
        assert events[0].source == "activity.jsonl"

    def test_filter_since_until(self, tmp_path: Path) -> None:
        _write_jsonl(tmp_path / "activity.jsonl", [
            {"timestamp": "2025-01-01T09:00:00Z", "action": "too_early"},
            {"timestamp": "2025-01-01T10:00:00Z", "action": "in_range"},
            {"timestamp": "2025-01-01T11:00:00Z", "action": "too_late"},
        ])
        replay = SessionReplay(log_dir=tmp_path)
        events = replay.load_session(
            since="2025-01-01T09:30:00Z",
            until="2025-01-01T10:30:00Z",
        )
        assert len(events) == 1

    def test_limit(self, tmp_path: Path) -> None:
        records = [
            {"timestamp": f"2025-01-01T10:{i:02d}:00Z", "action": f"a{i}"}
            for i in range(20)
        ]
        _write_jsonl(tmp_path / "activity.jsonl", records)
        replay = SessionReplay(log_dir=tmp_path)
        events = replay.load_session(limit=5)
        assert len(events) == 5

    def test_event_count(self, tmp_path: Path) -> None:
        _write_jsonl(tmp_path / "activity.jsonl", [
            {"timestamp": "2025-01-01T10:00:00Z", "action": "a1"},
            {"timestamp": "2025-01-01T10:01:00Z", "action": "a2"},
        ])
        replay = SessionReplay(log_dir=tmp_path)
        counts = replay.event_count()
        assert counts.get("activity.jsonl") == 2

    def test_malformed_json_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "activity.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"timestamp": "2025-01-01T10:00:00Z", "action": "ok"}\n'
            'NOT JSON\n'
            '{"timestamp": "2025-01-01T10:01:00Z", "action": "ok2"}\n',
            encoding="utf-8",
        )
        replay = SessionReplay(log_dir=tmp_path)
        events = replay.load_session()
        assert len(events) == 2
