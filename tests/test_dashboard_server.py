"""Tests for DashboardServer — HTTP endpoints, start/stop."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.alert_manager import AlertManager
from amiagi.application.task_queue import TaskQueue
from amiagi.domain.agent import AgentDescriptor, AgentRole
from amiagi.domain.task import Task, TaskPriority
from amiagi.infrastructure.dashboard_server import DashboardServer
from amiagi.infrastructure.metrics_collector import MetricsCollector


def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


class TestDashboardServer:
    def test_start_and_stop(self, tmp_path: Path) -> None:
        server = DashboardServer()
        port = _find_free_port()
        server.start(port=port)
        assert server.running
        assert server.port == port
        server.stop()
        assert not server.running

    def test_double_start_is_noop(self, tmp_path: Path) -> None:
        server = DashboardServer()
        port = _find_free_port()
        server.start(port=port)
        server.start(port=port + 1)  # should be ignored
        assert server.port == port
        server.stop()

    def test_api_agents(self, tmp_path: Path) -> None:
        registry = AgentRegistry()
        registry.register(AgentDescriptor(
            agent_id="p1", name="Polluks", role=AgentRole.EXECUTOR,
        ))
        server = DashboardServer(registry=registry)
        port = _find_free_port()
        server.start(port=port)
        time.sleep(0.2)
        try:
            data = _get_json(f"http://localhost:{port}/api/agents")
            assert len(data) == 1
            assert data[0]["name"] == "Polluks"
        finally:
            server.stop()

    def test_api_tasks(self, tmp_path: Path) -> None:
        queue = TaskQueue()
        queue.enqueue(Task(task_id="t1", title="Test Task"))
        server = DashboardServer(task_queue=queue)
        port = _find_free_port()
        server.start(port=port)
        time.sleep(0.2)
        try:
            data = _get_json(f"http://localhost:{port}/api/tasks")
            assert len(data) == 1
            assert data[0]["title"] == "Test Task"
        finally:
            server.stop()

    def test_api_metrics(self, tmp_path: Path) -> None:
        mc = MetricsCollector(db_path=tmp_path / "m.db")
        mc.record("test.m", 5.0)
        server = DashboardServer(metrics_collector=mc)
        port = _find_free_port()
        server.start(port=port)
        time.sleep(0.2)
        try:
            data = _get_json(f"http://localhost:{port}/api/metrics")
            assert isinstance(data, dict)
        finally:
            server.stop()

    def test_api_alerts(self, tmp_path: Path) -> None:
        am = AlertManager()
        server = DashboardServer(alert_manager=am)
        port = _find_free_port()
        server.start(port=port)
        time.sleep(0.2)
        try:
            data = _get_json(f"http://localhost:{port}/api/alerts")
            assert isinstance(data, list)
        finally:
            server.stop()

    def test_api_status(self, tmp_path: Path) -> None:
        server = DashboardServer()
        port = _find_free_port()
        server.start(port=port)
        time.sleep(0.2)
        try:
            data = _get_json(f"http://localhost:{port}/api/status")
            assert data["status"] == "running"
        finally:
            server.stop()

    def test_404_for_unknown_path(self, tmp_path: Path) -> None:
        server = DashboardServer()
        port = _find_free_port()
        server.start(port=port)
        time.sleep(0.2)
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                _get_json(f"http://localhost:{port}/api/nonexistent")
            assert exc_info.value.code == 404
        finally:
            server.stop()

    def test_stop_when_not_running(self) -> None:
        server = DashboardServer()
        server.stop()  # should not raise
