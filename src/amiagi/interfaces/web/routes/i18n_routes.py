"""Routes for internationalization — language switch.

Faza 14.2 — Multi-language UI.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

_SUPPORTED = ("pl", "en")


async def switch_language(request: Request) -> RedirectResponse:
    """Set language cookie and redirect back."""
    lang = request.path_params.get("lang", "pl")
    if lang not in _SUPPORTED:
        lang = "pl"
    redirect_to = request.query_params.get("next", "/dashboard")
    response = RedirectResponse(url=redirect_to, status_code=303)
    response.set_cookie("lang", lang, max_age=365 * 24 * 3600, path="/")
    return response


async def get_current_language(request: Request) -> JSONResponse:
    """Return the currently detected language."""
    from amiagi.interfaces.web.i18n_web import detect_language
    lang = detect_language(request)
    return JSONResponse({"lang": lang, "supported": list(_SUPPORTED)})


i18n_routes = [
    Route("/lang/{lang}", switch_language, methods=["GET"]),
    Route("/api/lang", get_current_language, methods=["GET"]),
]
