"""Teams API routes — /api/teams, /api/teams/{team_id}/org."""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)


async def api_teams(request: Request) -> Response:
    """GET /api/teams — team summary from TeamDashboard."""
    team_dashboard = getattr(request.app.state, "team_dashboard", None)
    if team_dashboard is None:
        return JSONResponse({"teams": [], "error": "team_dashboard_not_available"})

    summary = team_dashboard.summary()
    return JSONResponse(summary)


async def api_team_org(request: Request) -> Response:
    """GET /api/teams/{team_id}/org — org chart for a single team."""
    team_dashboard = getattr(request.app.state, "team_dashboard", None)
    if team_dashboard is None:
        return JSONResponse({"error": "team_dashboard_not_available"}, status_code=503)

    team_id = request.path_params["team_id"]
    chart = team_dashboard.org_chart(team_id)
    if not chart:
        return JSONResponse({"error": "team_not_found"}, status_code=404)

    return JSONResponse(chart)


team_routes = [
    Route("/api/teams", api_teams, methods=["GET"]),
    Route("/api/teams/{team_id}/org", api_team_org, methods=["GET"]),
]
