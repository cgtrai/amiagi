"""Tiny helper to emit activity-log entries from route handlers.

Usage::

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "file.upload", {"filename": name})
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request

logger = logging.getLogger(__name__)


async def log_action(
    request: Request,
    action: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Fire-and-forget activity log entry.

    Silently no-ops if *activity_logger* is not wired into ``app.state``.
    """
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is None:
        return

    user = getattr(request.state, "user", None)
    if user is None:
        user_id = "anonymous"
        session_id = None
    elif hasattr(user, "user_id"):
        user_id = str(user.user_id)
        session_id = getattr(user, "session_id", None)
    elif isinstance(user, dict):
        user_id = str(user.get("sub", user.get("user_id", "anonymous")))
        session_id = user.get("session_id")
    else:
        user_id = str(user)
        session_id = None

    ip_address = request.client.host if request.client else None

    try:
        await activity_logger.log(
            user_id=user_id,
            session_id=str(session_id) if session_id else None,
            action=action,
            detail=detail,
            ip_address=ip_address,
        )
    except Exception:
        logger.debug("Failed to log activity %s", action, exc_info=True)
