"""Tests for OAuth 2.0 module — URL building, state tokens."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import pytest

from amiagi.interfaces.web.auth.oauth import (
    build_authorize_url,
    generate_state_token,
    verify_state_token,
)


# ---------------------------------------------------------------------------
# Fixture: minimal Settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FakeSettings:
    oauth_client_id: str = "test-client-id"
    oauth_client_secret: str = "test-secret"
    oauth_redirect_uri: str = "http://localhost:8080/auth/callback"
    oauth_scopes: str = "openid email profile"
    oauth_provider: str = "google"


@pytest.fixture()
def settings():
    return _FakeSettings()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildAuthorizeUrl:
    def test_url_starts_with_google(self, settings):
        url = build_authorize_url(settings, "state123")
        assert url.startswith("https://accounts.google.com")

    def test_url_contains_client_id(self, settings):
        url = build_authorize_url(settings, "state123")
        parsed = parse_qs(urlparse(url).query)
        assert parsed["client_id"] == ["test-client-id"]

    def test_url_contains_redirect_uri(self, settings):
        url = build_authorize_url(settings, "state123")
        parsed = parse_qs(urlparse(url).query)
        assert parsed["redirect_uri"] == ["http://localhost:8080/auth/callback"]

    def test_url_contains_state(self, settings):
        url = build_authorize_url(settings, "my-state")
        parsed = parse_qs(urlparse(url).query)
        assert parsed["state"] == ["my-state"]

    def test_url_contains_scope(self, settings):
        url = build_authorize_url(settings, "s")
        parsed = parse_qs(urlparse(url).query)
        assert "openid email profile" in parsed["scope"]

    def test_url_response_type_code(self, settings):
        url = build_authorize_url(settings, "s")
        parsed = parse_qs(urlparse(url).query)
        assert parsed["response_type"] == ["code"]


class TestStateToken:
    def test_generate_returns_string(self):
        token = generate_state_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_tokens_are_unique(self):
        t1 = generate_state_token()
        t2 = generate_state_token()
        assert t1 != t2

    def test_verify_matching(self):
        token = generate_state_token()
        assert verify_state_token(token, token) is True

    def test_verify_mismatch(self):
        assert verify_state_token("aaa", "bbb") is False
