"""Google OAuth 2.0 flow — redirect, callback, token exchange."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

if TYPE_CHECKING:
    from amiagi.config import Settings

# Google endpoints
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# State token validity (seconds)
_STATE_MAX_AGE = 600  # 10 minutes


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


# ------------------------------------------------------------------
# HMAC-signed state tokens (no cookie required)
# ------------------------------------------------------------------

def generate_state_token(secret: str = "") -> str:
    """Generate an HMAC-signed CSRF state token.

    The token embeds a timestamp and nonce, signed with *secret*.
    It can be verified solely from the URL query parameter — no
    server-side storage or cookie round-trip needed.

    Format: ``{timestamp}.{nonce}.{signature}``

    For backward compatibility, if *secret* is empty a plain random
    token is returned (legacy mode).
    """
    if not secret:
        return secrets.token_urlsafe(32)
    ts = str(int(time.time()))
    nonce = secrets.token_urlsafe(16)
    sig = _sign(f"{ts}.{nonce}", secret)
    return f"{ts}.{nonce}.{sig}"


def verify_state_token(
    received: str,
    expected_or_secret: str,
    *,
    max_age: int = _STATE_MAX_AGE,
) -> bool:
    """Verify an OAuth state token.

    Supports two modes:

    1. **HMAC mode** (recommended): *received* is a ``ts.nonce.sig`` token
       and *expected_or_secret* is the signing secret.  Verifies the
       HMAC signature and checks the timestamp is within *max_age*.

    2. **Legacy mode**: plain constant-time string comparison (both
       arguments are opaque tokens).
    """
    if not received:
        return False

    parts = received.split(".")
    # HMAC-signed tokens always have exactly 3 dot-separated parts
    # where the first part is a numeric timestamp.
    if len(parts) == 3 and parts[0].isdigit():
        ts_str, nonce, sig = parts
        # Check expiry
        try:
            ts = int(ts_str)
        except ValueError:
            return False
        if abs(time.time() - ts) > max_age:
            return False
        # Verify signature
        expected_sig = _sign(f"{ts_str}.{nonce}", expected_or_secret)
        return hmac.compare_digest(sig, expected_sig)

    # Legacy: plain comparison
    return secrets.compare_digest(received, expected_or_secret)


def _sign(payload: str, secret: str) -> str:
    """Return an HMAC-SHA256 signature (URL-safe base64, no padding)."""
    import base64
    raw = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
