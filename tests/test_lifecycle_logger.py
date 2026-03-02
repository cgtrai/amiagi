"""Tests for LifecycleLogger — JSONL event logging."""

from __future__ import annotations

import json
from pathlib import Path

from amiagi.infrastructure.lifecycle_logger import LifecycleLogger


class TestLifecycleLogger:
    def test_log_creates_file(self, tmp_path: Path) -> None:
        log_path = tmp_path / "lifecycle.jsonl"
        logger = LifecycleLogger(log_path)
        logger.log(agent_id="a1", event="test.event")
        assert log_path.exists()

    def test_log_appends_jsonl(self, tmp_path: Path) -> None:
        log_path = tmp_path / "lifecycle.jsonl"
        logger = LifecycleLogger(log_path)
        logger.log(agent_id="a1", event="first")
        logger.log(agent_id="a2", event="second")
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_log_record_structure(self, tmp_path: Path) -> None:
        log_path = tmp_path / "lifecycle.jsonl"
        logger = LifecycleLogger(log_path)
        logger.log(agent_id="a1", event="test.event", details={"key": "value"})
        record = json.loads(log_path.read_text().strip())
        assert record["agent_id"] == "a1"
        assert record["event"] == "test.event"
        assert record["key"] == "value"
        assert "timestamp" in record

    def test_log_without_details(self, tmp_path: Path) -> None:
        log_path = tmp_path / "lifecycle.jsonl"
        logger = LifecycleLogger(log_path)
        logger.log(agent_id="a1", event="simple")
        record = json.loads(log_path.read_text().strip())
        assert record["agent_id"] == "a1"
        assert record["event"] == "simple"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        log_path = tmp_path / "deep" / "nested" / "lifecycle.jsonl"
        logger = LifecycleLogger(log_path)
        logger.log(agent_id="a1", event="deep")
        assert log_path.exists()
