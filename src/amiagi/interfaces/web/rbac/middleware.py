"""RBAC permission-checking decorators and helpers."""

from __future__ import annotations

import functools
import logging
from typing import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


def require_permission(*codenames: str) -> Callable:
    """Route decorator — verify that ``request.state.user`` has **all** listed permissions.

    Usage::

        @require_permission("admin.users")
        async def admin_users(request: Request) -> Response:
            ...

    Returns 403 JSON if any permission is missing, 401 if no user at all.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(request: Request, *args, **kwargs) -> Response:
            user = getattr(request.state, "user", None)
            if user is None:
                return JSONResponse(
                    {"error": "unauthenticated", "detail": "Login required."},
                    status_code=401,
                )

            # user may be UserSession (from middleware) with .permissions list
            user_perms: set[str] = set(getattr(user, "permissions", []) or [])

            missing = [c for c in codenames if c not in user_perms]
            if missing:
                logger.warning(
                    "Permission denied for user %s — missing: %s",
                    getattr(user, "email", "?"),
                    missing,
                )
                return JSONResponse(
                    {
                        "error": "forbidden",
                        "detail": f"Missing permissions: {', '.join(missing)}",
                    },
                    status_code=403,
                )

            return await func(request, *args, **kwargs)

        return wrapper

    return decorator
