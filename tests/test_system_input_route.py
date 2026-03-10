from __future__ import annotations

from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.system_routes import system_input


class _FakeWebAdapter:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit_user_turn(self, text: str) -> None:
        self.submitted.append(text)


def _make_client(adapter: _FakeWebAdapter) -> TestClient:
    app = Starlette(routes=[Route("/api/system/input", system_input, methods=["POST"])])
    app.state.web_adapter = adapter
    app.state.event_hub = type("Hub", (), {"broadcast": AsyncMock()})()
    return TestClient(app)


def test_system_input_submits_plain_message() -> None:
    adapter = _FakeWebAdapter()
    client = _make_client(adapter)

    response = client.post("/api/system/input", json={"message": "hello"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert adapter.submitted == ["hello"]
    assert response.json()["dispatch"]["target_scope"] == "broadcast"
    assert response.json()["dispatch"]["summary"] == "Operator → all: hello"
    assert response.json()["dispatch"]["from"] == "Operator"
    assert response.json()["dispatch"]["to"] == "all"
    assert response.json()["dispatch"]["message_type"] == "operator.input.accepted"
    assert response.json()["dispatch"]["status"] == "accepted"
    assert response.json()["dispatch"]["thread_owners"] == ["supervisor"]


def test_system_input_wraps_targeted_message() -> None:
    adapter = _FakeWebAdapter()
    client = _make_client(adapter)

    response = client.post(
        "/api/system/input",
        json={"message": "sprawdź status", "target_agent": "Kastor"},
    )

    assert response.status_code == 200
    assert adapter.submitted == ["[Sponsor -> Kastor] sprawdź status"]
    assert response.json()["submitted_message"] == "[Sponsor -> Kastor] sprawdź status"
    assert response.json()["dispatch"]["target_agent"] == "Kastor"
    assert response.json()["dispatch"]["summary"] == "Operator → Kastor: sprawdź status"
    assert response.json()["dispatch"]["from"] == "Operator"
    assert response.json()["dispatch"]["to"] == "Kastor"
    assert response.json()["dispatch"]["thread_owners"] == ["agent:Kastor"]