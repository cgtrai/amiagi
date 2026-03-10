"""Tests for the web app health endpoint and application factory."""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from amiagi import __version__


# ---------------------------------------------------------------------------
# Minimal mock fixtures
# ---------------------------------------------------------------------------

class _FakeAdapter:
    """Minimal WebAdapter stand-in for tests (no EventBus dependency)."""

    def set_event_hub(self, hub):
        pass

    def set_loop(self, loop):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class _FakeSettings:
    """Minimal Settings stand-in with required attributes."""
    db_host = "localhost"
    db_port = 5432
    db_name = "test_db"
    db_schema = "public"
    db_user = "test"
    db_password = "test"
    db_min_pool = 1
    db_max_pool = 2
    dashboard_port = 8080


def _create_test_app():
    """Create a Starlette app with *startup/shutdown disabled* for sync testing."""
    from starlette.applications import Starlette
    from starlette.routing import Route

    from amiagi.interfaces.web.routes.health_routes import health_routes

    return Starlette(routes=[*health_routes])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """GET /health should return status ok and the project version."""

    def test_health_returns_200(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_json_status(self):
        app = _create_test_app()
        client = TestClient(app)
        data = client.get("/health").json()
        assert data["status"] == "ok"

    def test_health_json_version(self):
        app = _create_test_app()
        client = TestClient(app)
        data = client.get("/health").json()
        assert data["version"] == __version__

    def test_health_version_is_1_2_0(self):
        app = _create_test_app()
        client = TestClient(app)
        data = client.get("/health").json()
        assert data["version"] == "1.2.0"

    def test_health_content_type(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert "application/json" in resp.headers.get("content-type", "")


class TestHealthRouteModule:
    """Verify route definitions in health_routes module."""

    def test_health_routes_list_not_empty(self):
        from amiagi.interfaces.web.routes.health_routes import health_routes
        assert len(health_routes) >= 1

    def test_health_route_path(self):
        from amiagi.interfaces.web.routes.health_routes import health_routes
        paths = [r.path for r in health_routes]
        assert "/health" in paths

    def test_health_route_method_get(self):
        from amiagi.interfaces.web.routes.health_routes import health_routes
        route = [r for r in health_routes if r.path == "/health"][0]
        assert route.methods is not None and "GET" in route.methods


@pytest.mark.asyncio
async def test_router_continuity_scheduler_drives_watchdog_and_idle_reactivation():
    from amiagi.interfaces.web.app import _RouterContinuityScheduler

    class _FakeRouterEngine:
        def __init__(self) -> None:
            self.watchdog_ticks = 0
            self.idle_reactivation_cycles = 0

        def watchdog_tick(self) -> None:
            self.watchdog_ticks += 1

        def run_idle_reactivation_cycle(self) -> None:
            self.idle_reactivation_cycles += 1

    router_engine = _FakeRouterEngine()
    scheduler = _RouterContinuityScheduler(router_engine, interval_seconds=0.1)
    scheduler.start(asyncio.get_running_loop())

    await asyncio.sleep(0.25)
    await scheduler.stop()

    assert router_engine.watchdog_ticks >= 2
    assert router_engine.idle_reactivation_cycles >= 2


@pytest.mark.asyncio
async def test_router_continuity_scheduler_stop_is_idempotent():
    from amiagi.interfaces.web.app import _RouterContinuityScheduler

    class _FakeRouterEngine:
        def watchdog_tick(self) -> None:
            pass

        def run_idle_reactivation_cycle(self) -> None:
            pass

    scheduler = _RouterContinuityScheduler(_FakeRouterEngine(), interval_seconds=0.1)
    scheduler.start(asyncio.get_running_loop())

    await scheduler.stop()
    await scheduler.stop()


def test_cleanup_stale_web_server_terminates_pid_from_pid_file(monkeypatch, tmp_path):
    from amiagi.interfaces.web import run as web_run

    settings = type(
        "Settings",
        (),
        {
            "work_dir": tmp_path / "amiagi-my-work",
            "activity_log_path": tmp_path / "logs" / "activity.jsonl",
        },
    )()

    terminated: list[int] = []
    written: list[tuple[Path, int]] = []
    pid_file = tmp_path / "logs" / "web_gui.pid"

    monkeypatch.setattr(web_run, "_web_runtime_pid_path", lambda _settings: pid_file)
    monkeypatch.setattr(web_run, "_read_pid_file", lambda _path: 4321)
    monkeypatch.setattr(web_run, "_pid_exists", lambda pid: pid == 4321)
    monkeypatch.setattr(web_run, "_looks_like_amiagi_web_process", lambda pid, repo_root: pid == 4321)
    monkeypatch.setattr(web_run, "_find_listening_pid_on_port_linux", lambda _port: None)
    monkeypatch.setattr(web_run, "_terminate_process", lambda pid: terminated.append(pid) or True)
    monkeypatch.setattr(web_run, "_write_pid_file", lambda path, pid: written.append((path, pid)))
    monkeypatch.setattr(web_run.os, "getpid", lambda: 9999)

    result = web_run._cleanup_stale_web_server(settings, 8080)

    assert result == pid_file
    assert terminated == [4321]
    assert written == [(pid_file, 9999)]


def test_cleanup_stale_web_server_terminates_port_holder_when_pid_file_is_stale(monkeypatch, tmp_path):
    from amiagi.interfaces.web import run as web_run

    settings = type(
        "Settings",
        (),
        {
            "work_dir": tmp_path / "amiagi-my-work",
            "activity_log_path": tmp_path / "logs" / "activity.jsonl",
        },
    )()

    removed: list[Path] = []
    terminated: list[int] = []
    written: list[tuple[Path, int]] = []
    pid_file = tmp_path / "logs" / "web_gui.pid"

    monkeypatch.setattr(web_run, "_web_runtime_pid_path", lambda _settings: pid_file)
    monkeypatch.setattr(web_run, "_read_pid_file", lambda _path: 1234)
    monkeypatch.setattr(web_run, "_pid_exists", lambda pid: pid == 5678)
    monkeypatch.setattr(web_run, "_remove_pid_file", lambda path: removed.append(path))
    monkeypatch.setattr(web_run, "_looks_like_amiagi_web_process", lambda pid, repo_root: pid == 5678)
    monkeypatch.setattr(web_run, "_find_listening_pid_on_port_linux", lambda _port: 5678)
    monkeypatch.setattr(web_run, "_terminate_process", lambda pid: terminated.append(pid) or True)
    monkeypatch.setattr(web_run, "_write_pid_file", lambda path, pid: written.append((path, pid)))
    monkeypatch.setattr(web_run.os, "getpid", lambda: 9999)

    result = web_run._cleanup_stale_web_server(settings, 8080)

    assert result == pid_file
    assert removed == [pid_file]
    assert terminated == [5678]
    assert written == [(pid_file, 9999)]


def test_cleanup_stale_web_server_does_not_kill_unrelated_port_holder(monkeypatch, tmp_path):
    from amiagi.interfaces.web import run as web_run

    settings = type(
        "Settings",
        (),
        {
            "work_dir": tmp_path / "amiagi-my-work",
            "activity_log_path": tmp_path / "logs" / "activity.jsonl",
        },
    )()

    terminated: list[int] = []
    written: list[tuple[Path, int]] = []
    pid_file = tmp_path / "logs" / "web_gui.pid"

    monkeypatch.setattr(web_run, "_web_runtime_pid_path", lambda _settings: pid_file)
    monkeypatch.setattr(web_run, "_read_pid_file", lambda _path: None)
    monkeypatch.setattr(web_run, "_find_listening_pid_on_port_linux", lambda _port: 7777)
    monkeypatch.setattr(web_run, "_looks_like_amiagi_web_process", lambda pid, repo_root: False)
    monkeypatch.setattr(web_run, "_terminate_process", lambda pid: terminated.append(pid) or True)
    monkeypatch.setattr(web_run, "_write_pid_file", lambda path, pid: written.append((path, pid)))
    monkeypatch.setattr(web_run.os, "getpid", lambda: 9999)

    result = web_run._cleanup_stale_web_server(settings, 8080)

    assert result == pid_file
    assert terminated == []
    assert written == [(pid_file, 9999)]
