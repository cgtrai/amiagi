"""Google OAuth 2.0 flow — redirect, callback, token exchange."""

from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

if TYPE_CHECKING:
    from amiagi.config import Settings

# Google endpoints
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def build_authorize_url(settings: "Settings", state: str) -> str:
    """Build the Google OAuth 2.0 authorization URL with CSRF state."""
    params = {
        "client_id": settings.oauth_client_id,
        "redirect_uri": settings.oauth_redirect_uri,
        "response_type": "code",
        "scope": settings.oauth_scopes,
        "access_type": "offline",
        "state": state,
        "prompt": "consent",
    }
    return f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(
    settings: "Settings",
    code: str,
) -> dict[str, Any]:
    """Exchange the authorization code for access + id tokens via httpx."""
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.oauth_client_id,
                "client_secret": settings.oauth_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.oauth_redirect_uri,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_userinfo(access_token: str) -> dict[str, Any]:
    """Fetch user profile from Google userinfo endpoint."""
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def generate_state_token() -> str:
    """Generate a cryptographically secure CSRF state token."""
    return secrets.token_urlsafe(32)


def verify_state_token(received: str, expected: str) -> bool:
    """Constant-time comparison of the state token."""
    return secrets.compare_digest(received, expected)
