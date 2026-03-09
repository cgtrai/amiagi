from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.skill_admin_routes import skill_admin_routes


@dataclass
class _FakeUser:
    user_id: str = "u1"
    permissions: Optional[list[str]] = None

    def __post_init__(self) -> None:
        if self.permissions is None:
            self.permissions = ["admin.settings"]


class _InjectUserMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, user: _FakeUser):
        super().__init__(app)
        self._user = user

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.user = self._user
        return await call_next(request)


class _Repo:
    def __init__(self) -> None:
        self.skill_usage_map = AsyncMock(return_value=[
            {
                "skill_id": "s1",
                "name": "python-expert",
                "display_name": "Python Expert",
                "total_uses": 7,
                "useful_count": 5,
                "total_tokens": 420,
                "agents": [
                    {"agent_role": "polluks", "total_uses": 4, "useful_count": 3, "total_tokens": 300},
                    {"agent_role": "kastor", "total_uses": 3, "useful_count": 2, "total_tokens": 120},
                ],
            }
        ])


def _make_client() -> tuple[TestClient, _Repo]:
    repo = _Repo()
    app = Starlette(
        routes=list(skill_admin_routes),
        middleware=[Middleware(_InjectUserMiddleware, user=_FakeUser())],
    )
    app.state.skill_repository = repo
    return TestClient(app, raise_server_exceptions=False), repo


def test_skill_usage_map_route_is_registered() -> None:
    paths = {route.path for route in skill_admin_routes}
    assert "/admin/skills/usage-map" in paths


def test_skill_usage_map_route_returns_aggregated_skills() -> None:
    client, repo = _make_client()

    response = client.get("/admin/skills/usage-map")

    assert response.status_code == 200
    payload = response.json()
    assert payload["skills"][0]["display_name"] == "Python Expert"
    repo.skill_usage_map.assert_awaited_once()


def test_admin_skills_template_contains_usage_graph_hooks() -> None:
    template = Path("src/amiagi/interfaces/web/templates/admin/skills.html").read_text(encoding="utf-8")

    assert "skill-usage-map" in template
    assert "/admin/skills/usage-map" in template
    assert "loadUsageMap" in template
