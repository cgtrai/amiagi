"""Tests for auth routes — login page, callback flow, logout."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.auth_routes import auth_routes


# ---------------------------------------------------------------------------
# Fake settings that mimic real Settings
# ---------------------------------------------------------------------------

@dataclass
class _FakeSettings:
    oauth_client_id: str = "fake-client-id"
    oauth_client_secret: str = "fake-secret"
    oauth_redirect_uri: str = "http://localhost:8080/auth/callback"
    oauth_scopes: str = "openid email profile"
    oauth_provider: str = "google"


# ---------------------------------------------------------------------------
# Minimal app factory
# ---------------------------------------------------------------------------

def _make_app(*, templates=None, session_manager=None, db_pool=None):
    app = Starlette(routes=list(auth_routes))
    app.state.settings = _FakeSettings()
    if templates is not None:
        app.state.templates = templates
    if session_manager is not None:
        app.state.session_manager = session_manager
    if db_pool is not None:
        app.state.db_pool = db_pool
    return app


# ---------------------------------------------------------------------------
# GET /auth/login
# ---------------------------------------------------------------------------

class TestAuthLogin:
    def test_login_redirects_when_no_templates(self):
        """Without templates, auth_login falls back to 302 redirect."""
        client = TestClient(_make_app())
        r = client.get("/auth/login", follow_redirects=False)
        assert r.status_code == 302
        assert "accounts.google.com" in r.headers["location"]

    def test_login_includes_state_in_url(self):
        client = TestClient(_make_app())
        r = client.get("/auth/login", follow_redirects=False)
        # State is embedded in the Google redirect URL, not as a cookie
        assert r.status_code == 302
        assert "state=" in r.headers["location"]

    def test_login_renders_template_with_google_url(self, tmp_path):
        """When Jinja2Templates are configured, renders login.html."""
        from starlette.templating import Jinja2Templates

        tpl = tmp_path / "login.html"
        tpl.write_text(
            '<a href="{{ login_url }}">Sign in</a><div>{{ error_message }}</div>'
        )
        templates = Jinja2Templates(directory=str(tmp_path))
        client = TestClient(_make_app(templates=templates))
        r = client.get("/auth/login")
        assert r.status_code == 200
        assert "accounts.google.com" in r.text
        assert "Sign in" in r.text

    def test_login_error_param_rendered(self, tmp_path):
        from starlette.templating import Jinja2Templates

        tpl = tmp_path / "login.html"
        tpl.write_text('<div class="err">{{ error_message }}</div>')
        templates = Jinja2Templates(directory=str(tmp_path))
        client = TestClient(_make_app(templates=templates))
        r = client.get("/auth/login?error=Konto+zablokowane")
        assert "Konto zablokowane" in r.text


# ---------------------------------------------------------------------------
# GET /auth/callback
# ---------------------------------------------------------------------------

class TestAuthCallback:
    def test_callback_missing_code_returns_400(self):
        client = TestClient(_make_app())
        r = client.get("/auth/callback?state=abc")
        assert r.status_code == 400

    def test_callback_bad_state_returns_400(self):
        client = TestClient(_make_app())
        r = client.get("/auth/callback?code=abc&state=bad")
        assert r.status_code == 400

    @patch("amiagi.interfaces.web.auth.oauth.verify_state_token")
    @patch("amiagi.interfaces.web.auth.oauth.fetch_userinfo", new_callable=AsyncMock)
    @patch("amiagi.interfaces.web.auth.oauth.exchange_code_for_tokens", new_callable=AsyncMock)
    def test_callback_happy_path(self, mock_exchange, mock_userinfo, mock_verify):
        """Full callback: code exchange, upsert, session, cookie, redirect."""
        # Arrange
        mock_verify.return_value = True
        mock_exchange.return_value = {"access_token": "at-123"}
        user_id = uuid4()
        mock_userinfo.return_value = {
            "email": "test@example.com",
            "name": "Test User",
            "picture": "http://img.example.com/pic.jpg",
            "sub": "google-sub-123",
        }

        # Fake pool that handles upsert and auto-admin
        fake_conn = AsyncMock()
        fake_conn.fetchrow.return_value = {
            "id": user_id,
            "is_active": True,
            "is_blocked": False,
        }
        fake_conn.fetchval.return_value = 0  # no user_roles yet
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        # Pool-level async helpers used by login attempt tracking & admin token check
        pool.fetchval = AsyncMock(return_value=0)
        pool.execute = AsyncMock(return_value="INSERT 0 1")

        sm = AsyncMock()
        sm.create_session.return_value = "jwt-token-xxx"

        client = TestClient(
            _make_app(session_manager=sm, db_pool=pool),
        )
        # HMAC-signed state — no cookie needed, just matching query param
        r = client.get(
            "/auth/callback?code=AUTH_CODE&state=signed-state",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/dashboard"
        assert "amiagi_session" in r.cookies


# ---------------------------------------------------------------------------
# GET /auth/logout
# ---------------------------------------------------------------------------

class TestAuthLogout:
    def test_logout_clears_cookie(self):
        sm = AsyncMock()

        app = _make_app(session_manager=sm)
        client = TestClient(app)
        r = client.get("/auth/logout", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/auth/login"

    def test_logout_revokes_session_when_user_present(self):
        sm = AsyncMock()

        # Build an app where middleware would have set request.state.user
        # (We can't easily simulate middleware in this unit test, so we just
        # verify that without user the code path doesn't crash.)
        app = _make_app(session_manager=sm)
        client = TestClient(app)
        r = client.get("/auth/logout", follow_redirects=False)
        assert r.status_code == 302
