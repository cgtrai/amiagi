from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.settings_routes import settings_routes


@dataclass
class _FakeUser:
    user_id: str = "u1"


class _InjectUserMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, user: _FakeUser):
        super().__init__(app)
        self._user = user

    async def dispatch(self, request: Request, call_next) -> Response:
        request.state.user = self._user
        return await call_next(request)


def _make_client(repo: AsyncMock) -> TestClient:
    app = Starlette(
        routes=list(settings_routes),
        middleware=[Middleware(_InjectUserMiddleware, user=_FakeUser())],
    )
    app.state.user_settings_repo = repo
    return TestClient(app, raise_server_exceptions=False)


def test_get_settings_preferences() -> None:
    repo = AsyncMock()
    repo.get_for_user = AsyncMock(return_value={
        "language": "en",
        "theme": "dark",
        "default_workspace": "demo",
        "auto_refresh_seconds": 10,
        "panel_preferences": {"settings_active_tab": "cron"},
    })
    client = _make_client(repo)

    response = client.get('/api/settings/preferences')

    assert response.status_code == 200
    assert response.json()["preferences"]["default_workspace"] == "demo"
    repo.get_for_user.assert_awaited_once_with("u1")


def test_put_settings_preferences_persists_and_sets_cookie() -> None:
    repo = AsyncMock()
    repo.save_for_user = AsyncMock(return_value={
        "language": "en",
        "theme": "dark",
        "default_workspace": "team-a",
        "auto_refresh_seconds": 60,
        "panel_preferences": {"settings_active_tab": "system"},
    })
    client = _make_client(repo)

    response = client.put('/api/settings/preferences', json={
        "language": "en",
        "theme": "dark",
        "default_workspace": "team-a",
        "auto_refresh_seconds": 60,
        "panel_preferences": {"settings_active_tab": "system"},
    })

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "lang=en" in response.headers.get("set-cookie", "")
    repo.save_for_user.assert_awaited_once()


class TestSettingsFrontendContract:
    def test_settings_template_uses_preferences_api(self) -> None:
        html = (Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "templates" / "settings.html").read_text(encoding="utf-8")
        assert "/api/settings/preferences" in html
        assert "loadGeneralPrefs" in html
        assert "/models" in html
        assert "🧪 Test" not in html
        assert ">Delete</button>" in html
        assert '✓ {{ _("common.save") }}' not in html
        assert '✗ {{ _("common.error") }}' not in html
        assert 'notifySettings' in html
        assert 'settingsErrorMessage' in html
        assert 'API key revoked' in html
        assert 'Cron job deleted' in html


class TestSettingsRoutesExist:
    def test_settings_routes_exist(self) -> None:
        paths = [r.path for r in settings_routes]
        assert "/api/settings/preferences" in paths