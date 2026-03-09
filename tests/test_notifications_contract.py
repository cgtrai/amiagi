from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient

from amiagi.interfaces.web.monitoring.notification_service import Notification
from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes


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


class _FakeSettingsRepo:
    def __init__(self, initial: dict | None = None) -> None:
        self._settings = initial or {}

    async def get_for_user(self, user_id: str):
        return dict(self._settings.get(user_id, {}))

    async def save_for_user(self, user_id: str, settings: dict):
        self._settings[user_id] = dict(settings)
        return dict(settings)


def _make_client(service: AsyncMock, repo: _FakeSettingsRepo | None = None) -> TestClient:
    app = Starlette(
        routes=list(monitoring_routes),
        middleware=[Middleware(_InjectUserMiddleware, user=_FakeUser())],
    )
    app.state.notification_service = service
    if repo is not None:
        app.state.user_settings_repo = repo
    return TestClient(app, raise_server_exceptions=False)


def test_notifications_payload_matches_nav_contract() -> None:
    service = AsyncMock()
    service.unread_count = AsyncMock(return_value=1)
    service.list_for_user = AsyncMock(return_value=[
        Notification(
            id="n1",
            user_id="u1",
            type="info",
            title="Tytuł",
            body="Treść",
            is_read=False,
            created_at=None,
        )
    ])
    client = _make_client(service)

    response = client.get("/api/notifications")

    assert response.status_code == 200
    payload = response.json()
    assert payload["unread_count"] == 1
    assert payload["notifications"][0]["read"] is False
    assert payload["notifications"][0]["is_read"] is False
    assert payload["notifications"][0]["message"] == "Treść"


def test_notifications_unread_count_shortcut() -> None:
    service = AsyncMock()
    service.unread_count = AsyncMock(return_value=4)
    service.list_for_user = AsyncMock(return_value=[])
    client = _make_client(service)

    response = client.get("/api/notifications?unread_count=1")

    assert response.status_code == 200
    assert response.json() == {"unread_count": 4, "notifications": []}
    service.list_for_user.assert_not_called()


def test_notifications_accept_post_mark_read() -> None:
    service = AsyncMock()
    service.mark_read = AsyncMock(return_value=True)
    service.unread_count = AsyncMock(return_value=0)
    service.list_for_user = AsyncMock(return_value=[])
    client = _make_client(service)

    response = client.post("/api/notifications/n1/read")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    service.mark_read.assert_awaited_once_with("n1")


def test_notifications_accept_post_mark_all_read() -> None:
    service = AsyncMock()
    service.mark_all_read = AsyncMock(return_value=3)
    service.unread_count = AsyncMock(return_value=0)
    service.list_for_user = AsyncMock(return_value=[])
    client = _make_client(service)

    response = client.post("/api/notifications/read-all")

    assert response.status_code == 200
    assert response.json() == {"marked": 3}
    service.mark_all_read.assert_awaited_once_with("u1")


def test_notifications_preferences_can_be_loaded_and_saved() -> None:
    service = AsyncMock()
    service.unread_count = AsyncMock(return_value=0)
    service.list_for_user = AsyncMock(return_value=[])
    repo = _FakeSettingsRepo({"u1": {"notification_channels": {"task.done": ["ui"]}}})
    client = _make_client(service, repo)

    get_response = client.get("/api/notifications/preferences")
    put_response = client.put(
        "/api/notifications/preferences",
        json={
            "channels": {"task.done": ["ui", "email"]},
            "muted_agents": ["polluks"],
        },
    )

    assert get_response.status_code == 200
    assert get_response.json()["preferences"]["channels"]["task.done"] == ["ui"]
    assert put_response.status_code == 200
    saved = put_response.json()["preferences"]
    assert saved["channels"]["task.done"] == ["ui", "email"]
    assert saved["muted_agents"] == ["polluks"]


def test_notifications_filter_muted_agents_from_payload() -> None:
    service = AsyncMock()
    service.unread_count = AsyncMock(return_value=2)
    service.list_for_user = AsyncMock(return_value=[
        {
            "id": "n1",
            "type": "agent.error",
            "title": "Kastor failed",
            "body": "Need approval",
            "is_read": False,
            "agent_id": "kastor",
        },
        {
            "id": "n2",
            "type": "task.done",
            "title": "Task finished",
            "body": "Done",
            "is_read": False,
            "agent_id": "polluks",
        },
    ])
    repo = _FakeSettingsRepo({"u1": {"notification_muted_agents": ["kastor"]}})
    client = _make_client(service, repo)

    response = client.get("/api/notifications")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["notifications"]) == 1
    assert payload["notifications"][0]["agent_id"] == "polluks"
    assert payload["groups"][0]["key"] == "polluks"


def test_notifications_mute_and_unmute_update_preferences() -> None:
    service = AsyncMock()
    service.unread_count = AsyncMock(return_value=0)
    service.list_for_user = AsyncMock(return_value=[])
    repo = _FakeSettingsRepo({"u1": {"notification_muted_agents": []}})
    client = _make_client(service, repo)

    mute_response = client.post("/api/notifications/mute/kastor")
    unmute_response = client.delete("/api/notifications/mute/kastor")

    assert mute_response.status_code == 200
    assert "kastor" in mute_response.json()["preferences"]["muted_agents"]
    assert unmute_response.status_code == 200
    assert "kastor" not in unmute_response.json()["preferences"]["muted_agents"]


def test_alert_rules_create_supports_channels_and_event_type() -> None:
    service = AsyncMock()
    service.unread_count = AsyncMock(return_value=0)
    service.list_for_user = AsyncMock(return_value=[])
    client = _make_client(service)

    response = client.post(
        "/api/alerts/rules",
        json={
            "name": "Budget email",
            "event_type": "budget.exceeded",
            "severity": "warning",
            "metric": "budget_pct",
            "operator": ">",
            "threshold": 90,
            "channels": ["ui", "email"],
        },
    )

    assert response.status_code == 201
    rule = response.json()["rule"]
    assert rule["event_type"] == "budget.exceeded"
    assert rule["severity"] == "warning"
    assert rule["channels"] == ["ui", "email"]