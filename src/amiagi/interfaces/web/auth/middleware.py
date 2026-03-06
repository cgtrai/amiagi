"""Authentication middleware — redirect unauthenticated requests.

Reads the ``amiagi_session`` cookie (or ``Authorization: Bearer <jwt>`` header),
validates it via ``SessionManager``, and populates ``request.state.user``.
Also supports ``X-API-Key`` header for programmatic access via API keys.

Public paths (login, callback, health, static) are whitelisted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

if TYPE_CHECKING:
    from amiagi.interfaces.web.auth.session import SessionManager
    from amiagi.interfaces.web.monitoring.api_key_manager import ApiKeyManager

logger = logging.getLogger(__name__)

# Paths that do not require authentication
_PUBLIC_PREFIXES = (
    "/auth/",
    "/health",
    "/static/",
    "/favicon.ico",
)

_SESSION_COOKIE = "amiagi_session"
_API_KEY_HEADER = "X-API-Key"


@dataclass
class ApiKeyUser:
    """Lightweight user representation for API key-authenticated requests."""

    user_id: str
    display_name: str = "api-key-user"
    permissions: list[str] | None = None

    def has_permission(self, codename: str) -> bool:
        return codename in (self.permissions or [])


class AuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware enforcing JWT session authentication.

    Authentication order:
    1. ``X-API-Key`` header (API key validation via :class:`ApiKeyManager`)
    2. ``amiagi_session`` cookie or ``Authorization: Bearer <jwt>``
    """

    def __init__(
        self,
        app,
        session_manager: "SessionManager | None" = None,
        api_key_manager: "ApiKeyManager | None" = None,
    ) -> None:
        super().__init__(app)
        self._session_manager = session_manager
        self._api_key_manager = api_key_manager

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Skip auth for public endpoints
        if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            return await call_next(request)

        # Lazy-resolve from app.state (set during on_startup) when not
        # injected via the constructor — allows registering the middleware
        # before the application has started.
        session_mgr = self._session_manager or getattr(
            request.app.state, "session_manager", None,
        )
        api_key_mgr = self._api_key_manager or getattr(
            request.app.state, "api_key_manager", None,
        )

        if session_mgr is None:
            # Auth subsystem not yet initialised — let request through.
            return await call_next(request)

        # --- 1. Try X-API-Key header ---
        api_key = request.headers.get(_API_KEY_HEADER)
        if api_key and api_key_mgr is not None:
            key_record = await api_key_mgr.validate_key(api_key)
            if key_record is not None:
                request.state.user = ApiKeyUser(
                    user_id=key_record.user_id,
                    display_name=key_record.name,
                    permissions=key_record.scopes,
                )
                return await call_next(request)

        # --- 2. Try cookie / Bearer token ---
        token = request.cookies.get(_SESSION_COOKIE)
        if token is None:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

        if token is None:
            return self._unauthenticated_response(request)

        # Validate session
        user_session = await session_mgr.validate_session(token)
        if user_session is None:
            return self._unauthenticated_response(request)

        # Populate request state
        request.state.user = user_session
        return await call_next(request)

    # ------------------------------------------------------------------

    @staticmethod
    def _unauthenticated_response(request: Request) -> Response:
        """Return 401 JSON for API requests, redirect for HTML requests."""
        accept = request.headers.get("accept", "")
        if "application/json" in accept or request.url.path.startswith("/api/"):
            return JSONResponse(
                {"error": "unauthenticated", "detail": "Valid session required."},
                status_code=401,
            )
        return RedirectResponse(url="/auth/login", status_code=302)
