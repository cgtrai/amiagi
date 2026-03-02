"""Tests for AuditChain (Phase 7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amiagi.application.audit_chain import AuditChain, AuditEntry


@pytest.fixture()
def chain(tmp_path: Path) -> AuditChain:
    return AuditChain(log_path=tmp_path / "audit.jsonl")


class TestAuditChain:
    def test_record_and_count(self, chain: AuditChain) -> None:
        assert chain.count() == 0
        chain.record(AuditEntry(agent_id="a1", action="read", target="file.txt"))
        assert chain.count() == 1

    def test_record_action_convenience(self, chain: AuditChain) -> None:
        entry = chain.record_action(
            agent_id="a1",
            action="write",
            target="output.txt",
            approved_by="supervisor",
            details={"size": 42},
        )
        assert entry.agent_id == "a1"
        assert entry.approved_by == "supervisor"
        assert chain.count() == 1

    def test_query_by_agent(self, chain: AuditChain) -> None:
        chain.record_action(agent_id="a1", action="r", target="t1")
        chain.record_action(agent_id="a2", action="r", target="t2")
        results = chain.query(agent_id="a1")
        assert len(results) == 1
        assert results[0].agent_id == "a1"

    def test_query_by_action(self, chain: AuditChain) -> None:
        chain.record_action(agent_id="a1", action="read", target="t1")
        chain.record_action(agent_id="a1", action="write", target="t2")
        results = chain.query(action="write")
        assert len(results) == 1

    def test_query_by_outcome(self, chain: AuditChain) -> None:
        chain.record_action(agent_id="a1", action="r", target="t", outcome="ok")
        chain.record_action(agent_id="a1", action="r", target="t", outcome="denied")
        results = chain.query(outcome="denied")
        assert len(results) == 1

    def test_query_limit(self, chain: AuditChain) -> None:
        for i in range(10):
            chain.record_action(agent_id="a1", action="r", target=f"t{i}")
        results = chain.query(limit=3)
        assert len(results) == 3

    def test_query_newest_first(self, chain: AuditChain) -> None:
        chain.record(AuditEntry(agent_id="a1", action="r", target="old", timestamp=1.0))
        chain.record(AuditEntry(agent_id="a1", action="r", target="new", timestamp=2.0))
        results = chain.query()
        assert results[0].target == "new"

    def test_persist_to_disk(self, chain: AuditChain, tmp_path: Path) -> None:
        chain.record_action(agent_id="a1", action="read", target="f.txt")
        log_file = tmp_path / "audit.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["agent_id"] == "a1"

    def test_append_only(self, chain: AuditChain, tmp_path: Path) -> None:
        chain.record_action(agent_id="a1", action="r", target="t1")
        chain.record_action(agent_id="a2", action="w", target="t2")
        log_file = tmp_path / "audit.jsonl"
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_default_outcome_is_ok(self, chain: AuditChain) -> None:
        entry = chain.record_action(agent_id="a1", action="r", target="t")
        assert entry.outcome == "ok"
