"""User settings persistence routes."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def _get_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        return "anonymous"
    if hasattr(user, "user_id"):
        return str(user.user_id)
    if isinstance(user, dict):
        return str(user.get("user_id") or user.get("sub") or "anonymous")
    return str(user)


def _get_repo(request: Request):
    return getattr(request.app.state, "user_settings_repo", None)


async def get_user_settings(request: Request) -> JSONResponse:
    repo = _get_repo(request)
    if repo is None:
        return JSONResponse({"error": "user_settings_repo unavailable"}, status_code=503)

    prefs = await repo.get_for_user(_get_user_id(request))
    return JSONResponse({"preferences": prefs})


async def update_user_settings(request: Request) -> JSONResponse:
    repo = _get_repo(request)
    if repo is None:
        return JSONResponse({"error": "user_settings_repo unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    prefs = await repo.save_for_user(_get_user_id(request), body if isinstance(body, dict) else {})

    from amiagi.interfaces.web.audit.log_helpers import log_action

    await log_action(request, "settings.preferences.updated", {
        "keys": sorted(prefs.keys()),
    })

    response = JSONResponse({"ok": True, "preferences": prefs})
    response.set_cookie("lang", prefs.get("language", "pl"), max_age=365 * 24 * 3600, path="/")
    return response


async def update_notification_settings(request: Request) -> JSONResponse:
    """Persist per-user notification channel preferences."""
    repo = _get_repo(request)
    if repo is None:
        return JSONResponse({"error": "user_settings_repo unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    channels = body.get("channels")
    if not isinstance(channels, dict):
        return JSONResponse({"error": "channels must be an object"}, status_code=400)

    prefs = await repo.get_for_user(_get_user_id(request))
    prefs["notification_channels"] = channels
    saved = await repo.save_for_user(_get_user_id(request), prefs)

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "settings.notifications.updated", {
        "events": sorted(channels.keys()),
    })

    return JSONResponse({"ok": True, "preferences": saved})


settings_routes = [
    Route("/api/settings/preferences", get_user_settings, methods=["GET"]),
    Route("/api/settings/preferences", update_user_settings, methods=["PUT"]),
    Route("/settings/notifications", update_notification_settings, methods=["PUT"]),
]