"""Tests for the web app health endpoint and application factory."""

from __future__ import annotations

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
