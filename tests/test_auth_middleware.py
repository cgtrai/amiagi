"""Tests for AuthMiddleware — public paths, cookie/header auth, redirects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.interfaces.web.auth.middleware import AuthMiddleware
from amiagi.interfaces.web.auth.session import UserSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER = UserSession(
    user_id=uuid4(),
    session_id=uuid4(),
    email="u@example.com",
    display_name="User",
    roles=["operator"],
    permissions=["agents.view"],
)


def _protected_handler(request: Request):
    user = getattr(request.state, "user", None)
    name = user.display_name if user else "anon"
    return PlainTextResponse(f"hello {name}")


def _make_app(session_manager) -> Starlette:
    app = Starlette(
        routes=[
            Route("/dashboard", _protected_handler),
            Route("/api/data", _protected_handler),
            Route("/auth/login", lambda r: PlainTextResponse("login")),
            Route("/health", lambda r: PlainTextResponse("ok")),
            Route("/static/style.css", lambda r: PlainTextResponse("css")),
        ],
    )
    app.add_middleware(AuthMiddleware, session_manager=session_manager)
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sm_ok():
    """SessionManager that always validates."""
    sm = AsyncMock()
    sm.validate_session.return_value = _USER
    return sm


@pytest.fixture()
def sm_fail():
    """SessionManager that always rejects."""
    sm = AsyncMock()
    sm.validate_session.return_value = None
    return sm


# ---------------------------------------------------------------------------
# Public paths bypass auth
# ---------------------------------------------------------------------------

class TestPublicPaths:
    def test_auth_login_accessible(self, sm_fail):
        client = TestClient(_make_app(sm_fail))
        r = client.get("/auth/login", follow_redirects=False)
        assert r.status_code == 200
        assert r.text == "login"

    def test_health_accessible(self, sm_fail):
        client = TestClient(_make_app(sm_fail))
        r = client.get("/health")
        assert r.status_code == 200

    def test_static_accessible(self, sm_fail):
        client = TestClient(_make_app(sm_fail))
        r = client.get("/static/style.css")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Unauthenticated behaviour
# ---------------------------------------------------------------------------

class TestUnauthenticated:
    def test_redirect_html(self, sm_fail):
        """Browser requests get 302 → /auth/login."""
        client = TestClient(_make_app(sm_fail))
        r = client.get("/dashboard", headers={"accept": "text/html"}, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/auth/login"

    def test_401_json_api(self, sm_fail):
        """API requests get 401 JSON."""
        client = TestClient(_make_app(sm_fail))
        r = client.get("/api/data", headers={"accept": "application/json"})
        assert r.status_code == 401
        assert r.json()["error"] == "unauthenticated"

    def test_401_json_api_prefix(self, sm_fail):
        """Paths starting with /api/ get 401 without accept header."""
        client = TestClient(_make_app(sm_fail))
        r = client.get("/api/data")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Authenticated via cookie
# ---------------------------------------------------------------------------

class TestCookieAuth:
    def test_valid_cookie(self, sm_ok):
        client = TestClient(_make_app(sm_ok))
        client.cookies.set("amiagi_session", "valid-jwt")
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert "User" in r.text

    def test_invalid_cookie_redirect(self, sm_fail):
        client = TestClient(_make_app(sm_fail))
        client.cookies.set("amiagi_session", "bad")
        r = client.get(
            "/dashboard",
            headers={"accept": "text/html"},
            follow_redirects=False,
        )
        assert r.status_code == 302


# ---------------------------------------------------------------------------
# Authenticated via Authorization header
# ---------------------------------------------------------------------------

class TestBearerAuth:
    def test_valid_bearer(self, sm_ok):
        client = TestClient(_make_app(sm_ok))
        r = client.get("/dashboard", headers={"authorization": "Bearer valid-jwt"})
        assert r.status_code == 200
        assert "User" in r.text

    def test_invalid_bearer_401(self, sm_fail):
        client = TestClient(_make_app(sm_fail))
        r = client.get(
            "/api/data",
            headers={"authorization": "Bearer bad", "accept": "application/json"},
        )
        assert r.status_code == 401
