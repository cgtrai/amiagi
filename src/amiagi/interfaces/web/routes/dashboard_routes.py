"""Dashboard routes — serves the main dashboard page."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route


async def dashboard_page(request: Request) -> RedirectResponse:
    """Render the main dashboard.

    Requires authentication (ensured by AuthMiddleware).
    """
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "dashboard.html")


# A bare ``/`` redirects to ``/dashboard``.
async def root_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


dashboard_routes: list[Route] = [
    Route("/", root_redirect, methods=["GET"]),
    Route("/dashboard", dashboard_page, methods=["GET"]),
]
