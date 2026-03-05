"""Tests for Sprint C+D: skill delegation, catalog bridge, import route,
session batch insert, replay endpoint, workspace upload alias, skills CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

@dataclass
class _FakeUser:
    user_id: str = "test-user"
    permissions: list[str] | None = None

    def __post_init__(self):
        if self.permissions is None:
            self.permissions = ["admin.settings", "files.manage"]

    def has_permission(self, codename: str) -> bool:
        return codename in (self.permissions or [])

    def get(self, key: str, default=None):
        if key == "sub":
            return self.user_id
        return default


class _InjectUser(BaseHTTPMiddleware):
    def __init__(self, app, user=None):
        super().__init__(app)
        self._user = user or _FakeUser()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.user = self._user
        return await call_next(request)


# ==================================================================
# C1+C2: ChatService._build_skills_section delegation + fallback
# ==================================================================

class FakeClient:
    _is_api_client = True

    def chat(self, **kwargs):
        return "ok"


class TestBuildSkillsSection:
    def _make_service(self, tmp_path=None, **kwargs):
        from amiagi.application.chat_service import ChatService
        from amiagi.infrastructure.memory_repository import MemoryRepository
        db = (tmp_path or Path("/tmp")) / "test_chat.db"
        repo = MemoryRepository(db)
        return ChatService(memory_repository=repo, model_client=FakeClient(), **kwargs)

    def test_skill_provider_called_when_set(self, tmp_path: Path) -> None:
        provider = MagicMock(return_value=[{"name": "Test", "content": "Do X"}])
        svc = self._make_service(tmp_path, skill_provider=provider)
        result = svc._build_skills_section("polluks", "hello")
        provider.assert_called_once_with("polluks", "hello", None)
        assert "Test" in result
        assert "Do X" in result

    def test_fallback_to_skills_loader_when_provider_returns_empty(self, tmp_path: Path) -> None:
        from amiagi.application.skills_loader import SkillsLoader, Skill

        provider = MagicMock(return_value=[])
        loader = MagicMock(spec=SkillsLoader)
        loader.load_for_role.return_value = [
            Skill(name="legacy", role="polluks", content="legacy content", path=Path("/dev/null")),
        ]

        svc = self._make_service(tmp_path, skill_provider=provider, skills_loader=loader)
        result = svc._build_skills_section("polluks", "hello")
        # Provider was called but returned empty
        provider.assert_called_once()
        # Fallback to legacy loader
        loader.load_for_role.assert_called_once_with("polluks")
        assert "legacy" in result

    def test_fallback_to_skills_loader_when_provider_raises(self, tmp_path: Path) -> None:
        from amiagi.application.skills_loader import SkillsLoader, Skill

        provider = MagicMock(side_effect=RuntimeError("fail"))
        loader = MagicMock(spec=SkillsLoader)
        loader.load_for_role.return_value = [
            Skill(name="safe", role="polluks", content="safe content", path=Path("/dev/null")),
        ]

        svc = self._make_service(tmp_path, skill_provider=provider, skills_loader=loader)
        result = svc._build_skills_section("polluks", "test")
        assert "safe" in result

    def test_empty_when_not_api_model(self, tmp_path: Path) -> None:
        class NotAPI:
            _is_api_client = False
            def chat(self, **kwargs): return "ok"

        from amiagi.application.chat_service import ChatService
        from amiagi.infrastructure.memory_repository import MemoryRepository
        repo = MemoryRepository(tmp_path / "not_api.db")
        svc = ChatService(memory_repository=repo, model_client=NotAPI())
        assert svc._build_skills_section("polluks", "test") == ""


# ==================================================================
# C3: SkillCatalog PG bridge methods
# ==================================================================

class TestSkillCatalogBridge:
    def test_load_from_records(self) -> None:
        from amiagi.application.skill_catalog import SkillCatalog
        cat = SkillCatalog()
        records = [
            {"name": "code_review", "description": "Review code", "tags": ["quality"]},
            {"name": "planning", "description": "Task planning"},
        ]
        count = cat.load_from_records(records)
        assert count == 2
        assert cat.count == 2
        assert cat.get("code_review") is not None
        planning = cat.get("planning")
        assert planning is not None
        assert planning.description == "Task planning"

    def test_export_all(self) -> None:
        from amiagi.application.skill_catalog import SkillCatalog, SkillEntry
        cat = SkillCatalog()
        cat.register(SkillEntry(name="a", description="aaa"))
        cat.register(SkillEntry(name="b", description="bbb"))
        exported = cat.export_all()
        assert len(exported) == 2
        names = {d["name"] for d in exported}
        assert names == {"a", "b"}

    def test_load_from_records_empty(self) -> None:
        from amiagi.application.skill_catalog import SkillCatalog
        cat = SkillCatalog()
        assert cat.load_from_records([]) == 0
        assert cat.count == 0


# ==================================================================
# C4: CLI skills commands
# ==================================================================

class TestSkillsCLI:
    def test_list_empty(self, capsys, tmp_path: Path) -> None:
        from amiagi.interfaces.skills_cli import run_skills_cli
        path = tmp_path / "skills.json"
        run_skills_cli(["--catalog", str(path), "list"])
        out = capsys.readouterr().out
        assert "No skills" in out

    def test_add_and_list(self, capsys, tmp_path: Path) -> None:
        from amiagi.interfaces.skills_cli import run_skills_cli
        path = tmp_path / "skills.json"
        run_skills_cli(["--catalog", str(path), "add", "--name", "review", "--desc", "Code review"])
        run_skills_cli(["--catalog", str(path), "list"])
        out = capsys.readouterr().out
        assert "review" in out
        assert "Total: 1" in out

    def test_remove(self, capsys, tmp_path: Path) -> None:
        from amiagi.interfaces.skills_cli import run_skills_cli
        path = tmp_path / "skills.json"
        run_skills_cli(["--catalog", str(path), "add", "--name", "temp"])
        run_skills_cli(["--catalog", str(path), "remove", "temp"])
        out = capsys.readouterr().out
        assert "Removed" in out

    def test_remove_not_found(self, tmp_path: Path) -> None:
        from amiagi.interfaces.skills_cli import run_skills_cli
        path = tmp_path / "skills.json"
        with pytest.raises(SystemExit):
            run_skills_cli(["--catalog", str(path), "remove", "nonexistent"])

    def test_search(self, capsys, tmp_path: Path) -> None:
        from amiagi.interfaces.skills_cli import run_skills_cli
        path = tmp_path / "skills.json"
        run_skills_cli(["--catalog", str(path), "add", "--name", "planning", "--tags", "project,management"])
        run_skills_cli(["--catalog", str(path), "search", "project"])
        out = capsys.readouterr().out
        assert "planning" in out


# ==================================================================
# C5: /admin/skills/import route
# ==================================================================

class TestSkillImportRoute:
    def _make_client(self, repo_mock):
        from amiagi.interfaces.web.routes.skill_admin_routes import admin_import_skills
        app = Starlette(
            routes=[Route("/admin/skills/import", admin_import_skills, methods=["POST"])],
            middleware=[Middleware(_InjectUser)],
        )
        app.state.skill_repository = repo_mock
        return TestClient(app, raise_server_exceptions=False)

    def test_import_json(self) -> None:
        mock_skill = MagicMock()
        mock_skill.to_dict.return_value = {"id": "1", "name": "imported"}
        repo = AsyncMock()
        repo.create_skill = AsyncMock(return_value=mock_skill)

        client = self._make_client(repo)
        payload = [{"name": "imported", "content": "do things"}]
        resp = client.post(
            "/admin/skills/import",
            content=json.dumps(payload),
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["imported"] == 1

    def test_import_missing_content(self) -> None:
        repo = AsyncMock()
        client = self._make_client(repo)
        payload = [{"name": "no_content"}]
        resp = client.post(
            "/admin/skills/import",
            content=json.dumps(payload),
            headers={"content-type": "application/json"},
        )
        data = resp.json()
        assert len(data["errors"]) == 1
        assert data["imported"] == 0

    def test_import_invalid_json(self) -> None:
        repo = AsyncMock()
        client = self._make_client(repo)
        resp = client.post(
            "/admin/skills/import",
            content=b"not valid json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_import_wrapped_in_skills_key(self) -> None:
        mock_skill = MagicMock()
        mock_skill.to_dict.return_value = {"id": "2", "name": "wrapped"}
        repo = AsyncMock()
        repo.create_skill = AsyncMock(return_value=mock_skill)

        client = self._make_client(repo)
        payload = {"skills": [{"name": "wrapped", "content": "wrapped content"}]}
        resp = client.post(
            "/admin/skills/import",
            content=json.dumps(payload),
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 201
        assert resp.json()["imported"] == 1


# ==================================================================
# D1: session_recorder batch insert
# ==================================================================

class TestSessionRecorderBatch:
    @pytest.mark.asyncio
    async def test_add_events_batch(self) -> None:
        from amiagi.interfaces.web.monitoring.session_recorder import SessionRecorder
        pool = AsyncMock()
        pool.executemany = AsyncMock()
        rec = SessionRecorder(pool)

        events = [
            {"session_id": "s1", "event_type": "created", "agent_id": "a1", "payload": {"k": "v"}},
            {"session_id": "s1", "event_type": "completed"},
        ]
        count = await rec.add_events(events)
        assert count == 2
        pool.executemany.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_events_empty(self) -> None:
        from amiagi.interfaces.web.monitoring.session_recorder import SessionRecorder
        pool = AsyncMock()
        rec = SessionRecorder(pool)
        count = await rec.add_events([])
        assert count == 0


# ==================================================================
# D3: /api/sessions/{id}/replay endpoint
# ==================================================================

class TestSessionReplayRoute:
    def _make_client(self, recorder_mock):
        from amiagi.interfaces.web.routes.monitoring_routes import api_session_replay
        app = Starlette(
            routes=[
                Route("/api/sessions/{session_id}/replay", api_session_replay, methods=["GET"]),
            ],
        )
        app.state.session_recorder = recorder_mock
        return TestClient(app)

    def test_replay_returns_events(self) -> None:
        from datetime import datetime
        from amiagi.interfaces.web.monitoring.session_recorder import SessionEvent

        evt = SessionEvent(
            id=1, session_id="s1", event_type="created",
            agent_id="polluks", payload={"msg": "hello"},
            created_at=datetime(2025, 1, 1, 12, 0, 0),
        )
        recorder = AsyncMock()
        recorder.get_session_events = AsyncMock(return_value=[evt])

        client = self._make_client(recorder)
        resp = client.get("/api/sessions/s1/replay")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "s1"
        assert data["event_count"] == 1
        assert data["events"][0]["type"] == "created"
        assert data["events"][0]["agent_id"] == "polluks"

    def test_replay_empty_session(self) -> None:
        recorder = AsyncMock()
        recorder.get_session_events = AsyncMock(return_value=[])

        client = self._make_client(recorder)
        resp = client.get("/api/sessions/empty/replay")
        data = resp.json()
        assert data["event_count"] == 0
        assert data["events"] == []


# ==================================================================
# D4: webhook httpx relay verification
# ==================================================================

class TestWebhookRelay:
    @pytest.mark.asyncio
    async def test_dispatch_uses_httpx_post(self) -> None:
        """Verify that dispatch() uses httpx.AsyncClient.post()."""
        from amiagi.interfaces.web.monitoring.webhook_manager import WebhookManager

        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {
                "id": "wh-1",
                "user_id": "u-1",
                "url": "https://example.com/hook",
                "events": ["test"],
                "secret": None,
                "is_active": True,
                "created_at": None,
            },
        ])

        mgr = WebhookManager(pool)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_success = True

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            results = await mgr.dispatch("test", {"data": "payload"})
            assert len(results) == 1
            assert results[0]["ok"] is True
            mock_client.post.assert_awaited_once()


# ==================================================================
# D5: /workspace/upload alias
# ==================================================================

class TestWorkspaceUploadAlias:
    def test_workspace_upload_route_exists(self) -> None:
        from amiagi.interfaces.web.routes.workspace_routes import workspace_routes
        paths = [r.path for r in workspace_routes]
        assert "/workspace/upload" in paths
        assert "/files/upload" in paths

    def test_workspace_upload_uses_same_handler(self) -> None:
        from amiagi.interfaces.web.routes.workspace_routes import workspace_routes
        from amiagi.interfaces.web.files.upload_handler import handle_upload
        upload_routes = [r for r in workspace_routes if r.path == "/workspace/upload"]
        assert len(upload_routes) == 1
        assert upload_routes[0].endpoint is handle_upload
