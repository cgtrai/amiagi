from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.api_routes import get_status_bar


_WEB_ROOT = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web"


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


def _make_client(**state_attrs) -> TestClient:
    app = Starlette(
        routes=[Route("/api/status-bar", get_status_bar, methods=["GET"])],
        middleware=[Middleware(_InjectUserMiddleware, user=_FakeUser())],
    )
    for key, value in state_attrs.items():
        setattr(app.state, key, value)
    return TestClient(app, raise_server_exceptions=False)


def test_status_bar_uses_inbox_pending_count() -> None:
    inbox_service = AsyncMock()
    inbox_service.count_by_status = AsyncMock(return_value={"pending": 7, "approved": 1})
    task_queue = MagicMock()
    task_queue.pending_count.return_value = 2
    client = _make_client(inbox_service=inbox_service, task_queue=task_queue)

    response = client.get("/api/status-bar")

    assert response.status_code == 200
    assert response.json()["inbox_pending"] == 7
    inbox_service.count_by_status.assert_awaited_once()


def test_status_bar_falls_back_to_notification_count() -> None:
    notification_service = AsyncMock()
    notification_service.unread_count = AsyncMock(return_value=5)
    task_queue = MagicMock()
    task_queue.pending_count.return_value = 0
    client = _make_client(notification_service=notification_service, task_queue=task_queue)

    response = client.get("/api/status-bar")

    assert response.status_code == 200
    assert response.json()["inbox_pending"] == 5
    notification_service.unread_count.assert_awaited_once_with("u1")


def test_status_bar_reports_running_and_pending_task_split() -> None:
    task_queue = MagicMock()
    task_queue.stats.return_value = {
        "in_progress": 3,
        "running": 1,
        "pending": 2,
        "assigned": 4,
    }
    client = _make_client(task_queue=task_queue)

    response = client.get("/api/status-bar")

    assert response.status_code == 200
    assert response.json()["running_tasks"] == 4
    assert response.json()["pending_tasks"] == 6
    assert response.json()["active_tasks"] == 6


def test_status_bar_template_renders_running_pending_breakdown() -> None:
    html = (_WEB_ROOT / "templates" / "partials" / "status_bar.html").read_text(encoding="utf-8")

    assert 'id="active-tasks-running"' in html
    assert 'id="active-tasks-pending"' in html
    assert "tasks.in_progress" in html
    assert "tasks.pending" in html
    assert "status-clickable" in html


def test_status_bar_js_uses_status_bar_sections_for_click_handlers() -> None:
    js = (_WEB_ROOT / "static" / "js" / "status_bar.js").read_text(encoding="utf-8")

    assert "closest('.status-bar-section')" in js
    assert "bindStatusAction('active-tasks-count'" in js
    assert "fetch('/api/tasks')" in js
    assert "active-tasks-running" in js
    assert "active-tasks-pending" in js


def test_command_rail_active_state_uses_glass_pill_style() -> None:
    css = (_WEB_ROOT / "static" / "css" / "components.css").read_text(encoding="utf-8")
    rail = (_WEB_ROOT / "templates" / "partials" / "command_rail.html").read_text(encoding="utf-8")

    assert ".command-rail-item.active" in css
    assert "border-radius: var(--glass-radius-full);" in css
    assert "linear-gradient(135deg" in css
    assert "0 8px 24px rgba(37, 99, 235, 0.22)" in css
    assert 'class="command-rail-item{% if request.url.path == \'/dashboard\' %} active{% endif %}"' in rail
    assert "rail-group-label" in rail