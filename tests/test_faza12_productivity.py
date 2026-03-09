"""Tests for Faza 12 — User productivity features.

Covers audit criteria 12.1–12.9:
- 12.1: dbo.prompts CRUD
- 12.2: Template parameters ({placeholder})
- 12.3: Ctrl+K opens spotlight search
- 12.4: Full-text search via tsvector
- 12.5: Copy button (clipboard API usage in JS)
- 12.6: Save to snippets with context
- 12.7: Command palette ≥ 10 commands
- 12.8: Keyboard shortcuts
- 12.9: Tests ≥ 8 prompts, ≥ 8 search, ≥ 5 snippets
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

_WEB_ROOT = Path(__file__).parent.parent / "src/amiagi/interfaces/web"


# ═══════════════════════════════════════════════════════════════
# 12.1 — dbo.prompts CRUD  (tests ≥ 8)
# ═══════════════════════════════════════════════════════════════

class TestPromptRepository:
    """12.1: Prompt CRUD operations."""

    def test_prompt_record_to_dict(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRecord
        p = PromptRecord(
            id="p1", user_id="u1", title="Code Review",
            template="Review {filename} for {aspect}",
            tags=["review"], is_public=True, use_count=5,
        )
        d = p.to_dict()
        assert d["id"] == "p1"
        assert d["is_public"] is True
        assert d["parameters"] == ["filename", "aspect"]
        assert d["usage_count"] == 5

    def test_prompt_parameters_extraction(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRecord
        p = PromptRecord(id="", user_id="", title="", template="Fix {file} line {line}",
                         tags=[], is_public=False, use_count=0)
        assert p.parameters == ["file", "line"]

    def test_prompt_render(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRecord
        p = PromptRecord(id="", user_id="", title="", template="Review {filename} for {aspect}",
                         tags=[], is_public=False, use_count=0)
        result = p.render({"filename": "app.py", "aspect": "security"})
        assert result == "Review app.py for security"

    def test_prompt_render_partial(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRecord
        p = PromptRecord(id="", user_id="", title="", template="Review {filename} for {aspect}",
                         tags=[], is_public=False, use_count=0)
        result = p.render({"filename": "app.py"})
        assert result == "Review app.py for {aspect}"

    @pytest.mark.asyncio
    async def test_list_prompts(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRepository
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = PromptRepository(pool)
        result = await repo.list_prompts(user_id="u1")
        assert result == []
        sql = pool.fetch.call_args[0][0]
        assert "dbo.prompts" in sql

    @pytest.mark.asyncio
    async def test_get_prompt_not_found(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRepository
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        repo = PromptRepository(pool)
        assert await repo.get_prompt("nonexistent") is None

    @pytest.mark.asyncio
    async def test_create_prompt(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRepository
        mock_row = {
            "id": "new-id", "user_id": "u1", "title": "Test",
            "template": "Hello {name}", "tags": ["greet"],
            "is_public": False, "use_count": 0,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=mock_row)
        repo = PromptRepository(pool)
        p = await repo.create_prompt(user_id="u1", title="Test", template="Hello {name}")
        assert p.title == "Test"

    @pytest.mark.asyncio
    async def test_delete_prompt(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRepository
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 1")
        repo = PromptRepository(pool)
        assert await repo.delete_prompt("p1") is True

    @pytest.mark.asyncio
    async def test_delete_prompt_not_found(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRepository
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 0")
        repo = PromptRepository(pool)
        assert await repo.delete_prompt("nope") is False

    @pytest.mark.asyncio
    async def test_increment_use_count(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRepository
        pool = MagicMock()
        pool.execute = AsyncMock()
        repo = PromptRepository(pool)
        await repo.increment_use_count("p1")
        sql = pool.execute.call_args[0][0]
        assert "use_count" in sql

    @pytest.mark.asyncio
    async def test_list_prompts_with_tag(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRepository
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = PromptRepository(pool)
        await repo.list_prompts(user_id="u1", tag="review")
        sql = pool.fetch.call_args[0][0]
        assert "ANY(tags)" in sql

    @pytest.mark.asyncio
    async def test_record_prompt_use_tracks_agent(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRepository
        pool = MagicMock()
        pool.execute = AsyncMock()
        repo = PromptRepository(pool)
        await repo.record_prompt_use("p1", agent_id="polluks")
        assert pool.execute.await_count == 2
        assert "prompt_usage" in pool.execute.await_args_list[1].args[0]

    @pytest.mark.asyncio
    async def test_get_prompt_stats(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRepository
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"total_uses": 7, "agent_count": 2, "avg_rating": None})
        repo = PromptRepository(pool)
        stats = await repo.get_prompt_stats("p1")
        assert stats == {"total_uses": 7, "agent_count": 2, "avg_rating": None}


# ═══════════════════════════════════════════════════════════════
# 12.2 — Template parameters  (covered above by render tests)
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 12.3 + 12.7 + 12.8 — Keyboard shortcuts & command palette
# ═══════════════════════════════════════════════════════════════

class TestKeybindings:
    """12.3/12.7/12.8: Keyboard shortcuts and command palette."""

    def _read_keybindings(self) -> str:
        return (_WEB_ROOT / "static/js/keybindings.js").read_text()

    def test_keybindings_file_exists(self):
        assert (_WEB_ROOT / "static/js/keybindings.js").exists()

    def test_ctrl_k_search(self):
        js = self._read_keybindings()
        assert "Ctrl+K" in js
        assert "openSearch" in js

    def test_ctrl_shift_p_palette(self):
        js = self._read_keybindings()
        assert "Ctrl+Shift+P" in js
        assert "openCommandPalette" in js

    def test_ctrl_enter_send(self):
        js = self._read_keybindings()
        assert "Ctrl+Enter" in js
        assert "sendPrompt" in js

    def test_esc_close(self):
        js = self._read_keybindings()
        assert "Escape" in js
        assert "closeOverlay" in js

    def test_ctrl_number_tabs(self):
        js = self._read_keybindings()
        assert "switchTab" in js
        assert "Ctrl+1" in js

    def test_help_shortcut(self):
        js = self._read_keybindings()
        assert "showHelp" in js

    def test_at_least_10_commands(self):
        """12.7: ≥ 10 commands registered."""
        js = self._read_keybindings()
        # Count unique command registrations in COMMANDS array
        matches = re.findall(r"id:\s*'[^']+'", js)
        assert len(matches) >= 10, f"Found {len(matches)} commands, need ≥10"

    def test_keybindings_linked_in_dashboard(self):
        html = (_WEB_ROOT / "templates/dashboard.html").read_text()
        assert "keybindings.js" in html

    def test_command_palette_css_in_dashboard(self):
        # CSS was extracted from inline <style> to a dedicated stylesheet
        css = (_WEB_ROOT / "static/css/dashboard.css").read_text()
        assert "command-palette-overlay" in css
        # Dashboard template links the stylesheet
        html = (_WEB_ROOT / "templates/dashboard.html").read_text()
        assert "dashboard.css" in html


# ═══════════════════════════════════════════════════════════════
# 12.4 — Full-text search (tsvector)  (tests ≥ 8)
# ═══════════════════════════════════════════════════════════════

class TestSearchService:
    """12.4: Full-text search."""

    def test_search_result_to_dict(self):
        from amiagi.interfaces.web.productivity.search_service import SearchResult
        r = SearchResult(entity_type="agent", entity_id="a1",
                        title="Test Agent", snippet="found text", rank=0.5)
        d = r.to_dict()
        assert d["entity_type"] == "agent"
        assert d["rank"] == 0.5
        assert d["url"] == "/agents/a1"

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        from amiagi.interfaces.web.productivity.search_service import SearchService
        pool = MagicMock()
        svc = SearchService(pool)
        result = await svc.search("")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_calls_db(self):
        from amiagi.interfaces.web.productivity.search_service import SearchService
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        svc = SearchService(pool)
        await svc.search("test query")
        sql = pool.fetch.call_args[0][0]
        assert "content_tsv" in sql
        assert "to_tsquery" in sql

    @pytest.mark.asyncio
    async def test_search_with_type_filter(self):
        from amiagi.interfaces.web.productivity.search_service import SearchService
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        svc = SearchService(pool)
        await svc.search("query", entity_type="agent")
        sql = pool.fetch.call_args[0][0]
        assert "entity_type" in sql

    @pytest.mark.asyncio
    async def test_index_entity(self):
        from amiagi.interfaces.web.productivity.search_service import SearchService
        pool = MagicMock()
        pool.execute = AsyncMock()
        svc = SearchService(pool)
        await svc.index_entity("agent", "a1", "Test", "content")
        sql = pool.execute.call_args[0][0]
        assert "search_index" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_remove_entity(self):
        from amiagi.interfaces.web.productivity.search_service import SearchService
        pool = MagicMock()
        pool.execute = AsyncMock()
        svc = SearchService(pool)
        await svc.remove_entity("agent", "a1")
        sql = pool.execute.call_args[0][0]
        assert "DELETE" in sql

    @pytest.mark.asyncio
    async def test_search_results_parsed(self):
        from amiagi.interfaces.web.productivity.search_service import SearchService
        rows = [
            {"entity_type": "task", "entity_id": "t1", "title": "Fix bug",
             "snippet": "fix <b>bug</b>", "rank": 0.8},
        ]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)
        svc = SearchService(pool)
        results = await svc.search("fix bug")
        assert len(results) == 1
        assert results[0].entity_type == "task"

    @pytest.mark.asyncio
    async def test_search_whitespace_query(self):
        from amiagi.interfaces.web.productivity.search_service import SearchService
        pool = MagicMock()
        svc = SearchService(pool)
        result = await svc.search("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_tracks_recent_queries(self):
        from amiagi.interfaces.web.productivity.search_service import SearchService
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        svc = SearchService(pool)
        await svc.search("session replay")
        assert svc.get_recent_queries(limit=1) == ["session replay"]

    @pytest.mark.asyncio
    async def test_search_suggestions_do_not_pollute_recent_history(self):
        from amiagi.interfaces.web.productivity.search_service import SearchService
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        svc = SearchService(pool)
        await svc.search("workflow gate")
        await svc.search("wo", remember=False)
        assert svc.get_recent_queries(limit=5) == ["workflow gate"]

    def test_search_frequent_queries_prioritize_popular_prefix_matches(self):
        from amiagi.interfaces.web.productivity.search_service import SearchService
        svc = SearchService(MagicMock())
        svc.remember_query("workflow gate")
        svc.remember_query("workflow gate")
        svc.remember_query("workflow review")
        svc.remember_query("session replay")
        assert svc.get_frequent_queries("wo", limit=2) == ["workflow gate", "workflow review"]


class _FakeSessionRecorder:
    async def list_sessions(self, *, limit: int = 50, agent_id: str | None = None):
        return [{"session_id": "sess-1", "agent_id": "polluks", "event_count": 4}]


class _FakeInboxItem:
    id = "ibox-1"
    title = "Review deployment"
    body = "Needs approval"
    agent_id = "kastor"
    status = "pending"
    item_type = "gate"


class _FakeInboxService:
    async def list_items(self, *, status: str | None = None, limit: int = 50, offset: int = 0):
        return [_FakeInboxItem()]


class _FakeUser:
    def __init__(self):
        self.user_id = "u1"
        self.permissions = ["admin.users", "admin.roles", "admin.settings"]


class _FakeRbacRepo:
    async def list_users(self, page: int = 1, per_page: int = 20, search: str | None = None):
        return type("Page", (), {
            "items": [type("User", (), {"id": "u1", "email": "alice@example.com", "display_name": "Alice"})()],
        })()

    async def list_roles(self):
        return [type("Role", (), {"id": "r1", "name": "Operator", "description": "Ops"})()]


class _FakeWorkflow:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description


class _FakeRun:
    def __init__(self):
        self.run_id = "run-1"
        self.workflow = _FakeWorkflow("Deploy Flow")
        self.status = "running"


class _FakeWorkflowEngine:
    def list_runs(self):
        return [_FakeRun()]


class _FakePool:
    async def fetch(self, query: str, *args):
        if "vault_secrets" in query:
            return [{"agent_id": "polluks", "key": "OPENAI_API_KEY", "updated_at": None}]
        return []


def test_api_search_merges_federated_results_with_urls():
    from amiagi.interfaces.web.routes.search_routes import search_routes

    app = Starlette(routes=list(search_routes))
    app.state.search_service = type("Svc", (), {
        "search": AsyncMock(return_value=[]),
        "get_recent_queries": lambda self=None, limit=5: [],
    })()
    app.state.session_recorder = _FakeSessionRecorder()
    app.state.inbox_service = _FakeInboxService()
    app.state.rbac_repo = _FakeRbacRepo()
    app.state.workflow_engine = _FakeWorkflowEngine()
    app.state._workflow_definitions = {"wf-1": _FakeWorkflow("Deploy Flow", "Pipeline")}
    app.state.db_pool = _FakePool()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/api/search?q=deploy")
    assert response.status_code == 200
    payload = response.json()
    types = {item["entity_type"] for item in payload}
    assert "workflow" in types
    assert "workflow_run" in types
    assert any(item["url"] == "/workflows?run_id=run-1" for item in payload)


def test_api_search_type_filter_returns_session_results():
    from amiagi.interfaces.web.routes.search_routes import search_routes

    app = Starlette(routes=list(search_routes))
    app.state.search_service = type("Svc", (), {
        "search": AsyncMock(return_value=[]),
        "get_recent_queries": lambda self=None, limit=5: [],
    })()
    app.state.session_recorder = _FakeSessionRecorder()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/api/search?q=sess-1&type=session")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["entity_type"] == "session"
    assert payload[0]["url"] == "/sessions?session_id=sess-1"


def test_api_search_suggestions_include_recent_and_frequent_queries():
    from amiagi.interfaces.web.routes.search_routes import search_routes

    app = Starlette(routes=list(search_routes))
    app.state.search_service = type("Svc", (), {
        "search": AsyncMock(return_value=[type("R", (), {"to_dict": lambda self: {"title": "Workflow Deploy", "entity_type": "workflow"}})()]),
        "get_recent_queries": lambda self=None, limit=5: ["workflow gate"],
        "get_frequent_queries": lambda self=None, partial="", limit=5: ["workflow review"],
    })()

    client = TestClient(app)
    response = client.get("/api/search/suggestions?q=wo")
    assert response.status_code == 200
    payload = response.json()
    assert payload["recent"] == ["workflow gate"]
    assert payload["queries"] == ["workflow review"]
    assert payload["suggestions"][0]["title"] == "Workflow Deploy"


class _FakePromptRepo:
    def __init__(self):
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRecord
        self.prompt = PromptRecord(
            id="p1",
            user_id="u1",
            title="Deploy prompt",
            template="Deploy {service}",
            tags=["ops"],
            is_public=True,
            use_count=3,
        )
        self.used_agent_id = None

    async def list_prompts(self, **kwargs):
        prompt_list = [self.prompt]
        tag = kwargs.get("tag")
        query = kwargs.get("query")
        if tag:
            prompt_list = [prompt for prompt in prompt_list if tag in prompt.tags]
        if query:
            needle = query.casefold()
            prompt_list = [
                prompt for prompt in prompt_list
                if needle in prompt.title.casefold()
                or needle in prompt.template.casefold()
                or any(needle in tag.casefold() for tag in prompt.tags)
            ]
        return prompt_list

    async def get_prompt(self, prompt_id: str):
        return self.prompt if prompt_id == "p1" else None

    async def get_prompt_stats(self, prompt_id: str):
        return {"total_uses": 3, "agent_count": 2, "avg_rating": None}

    async def record_prompt_use(self, prompt_id: str, agent_id: str | None = None):
        self.used_agent_id = agent_id


class _FakeSnippetRepo:
    def __init__(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRecord
        self.snippet = SnippetRecord(
            id="s1",
            user_id="u1",
            content="print('hello')",
            tags=["python"],
            source_agent="polluks",
            source_task_id="t1",
            pinned=False,
        )

    async def list_snippets(self, user_id: str, *, tag: str | None = None, query: str | None = None):
        snippets = [self.snippet]
        if tag:
            snippets = [snippet for snippet in snippets if tag in snippet.tags]
        if query:
            needle = query.casefold()
            snippets = [
                snippet for snippet in snippets
                if needle in snippet.content.casefold()
                or needle in (snippet.source_agent or "").casefold()
                or any(needle in tag.casefold() for tag in snippet.tags)
            ]
        return snippets

    async def create_snippet(self, **kwargs):
        return self.snippet

    async def get_snippet(self, snippet_id: str):
        return self.snippet if snippet_id == "s1" else None

    async def update_snippet(self, snippet_id: str, **kwargs):
        if snippet_id != "s1":
            return None
        if "content" in kwargs and kwargs["content"] is not None:
            self.snippet.content = kwargs["content"]
        if "tags" in kwargs and kwargs["tags"] is not None:
            self.snippet.tags = list(kwargs["tags"])
        if "source_agent" in kwargs and kwargs["source_agent"] is not None:
            self.snippet.source_agent = kwargs["source_agent"]
        if "source_task_id" in kwargs and kwargs["source_task_id"] is not None:
            self.snippet.source_task_id = kwargs["source_task_id"]
        if "pinned" in kwargs and kwargs["pinned"] is not None:
            self.snippet.pinned = bool(kwargs["pinned"])
        return self.snippet

    async def toggle_pin(self, snippet_id: str, pinned: bool | None = None):
        self.snippet.pinned = (not self.snippet.pinned) if pinned is None else bool(pinned)
        return self.snippet.pinned

    async def delete_snippet(self, snippet_id: str):
        return snippet_id == "s1"


def test_prompt_api_alias_list_includes_agent_stats():
    from amiagi.interfaces.web.routes.prompt_routes import prompt_routes

    app = Starlette(routes=list(prompt_routes))
    app.state.prompt_repository = _FakePromptRepo()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/api/prompts")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["usage_count"] == 3
    assert payload[0]["agent_count"] == 2


def test_prompt_api_alias_supports_query_filter():
    from amiagi.interfaces.web.routes.prompt_routes import prompt_routes

    app = Starlette(routes=list(prompt_routes))
    app.state.prompt_repository = _FakePromptRepo()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/api/prompts?q=deploy")
    assert response.status_code == 200
    assert len(response.json()) == 1

    response = client.get("/api/prompts?q=security")
    assert response.status_code == 200
    assert response.json() == []


def test_prompt_use_api_alias_tracks_agent_id():
    from amiagi.interfaces.web.routes.prompt_routes import prompt_routes

    repo = _FakePromptRepo()
    app = Starlette(routes=list(prompt_routes))
    app.state.prompt_repository = repo

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)
    response = client.post("/api/prompts/p1/use", json={"values": {"service": "api"}, "agent_id": "polluks"})
    assert response.status_code == 200
    assert response.json()["agent_id"] == "polluks"
    assert repo.used_agent_id == "polluks"


# ═══════════════════════════════════════════════════════════════
# 12.5 + 12.6 — Snippets (tests ≥ 5)
# ═══════════════════════════════════════════════════════════════

class TestSnippetRepository:
    """12.6: Snippets with context."""

    def test_snippet_record_to_dict(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRecord
        s = SnippetRecord(
            id="s1", user_id="u1", content="code",
            tags=["python"], source_agent="executor", source_task_id="t1",
        )
        d = s.to_dict()
        assert d["source_agent"] == "executor"
        assert d["tags"] == ["python"]
        assert d["title"] == "code"
        assert d["pinned"] is False

    @pytest.mark.asyncio
    async def test_list_snippets(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRepository
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = SnippetRepository(pool)
        result = await repo.list_snippets("u1")
        assert result == []

    @pytest.mark.asyncio
    async def test_create_snippet(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRepository
        mock_row = {
            "id": "s1", "user_id": "u1", "content": "code",
            "tags": [], "source_agent": "executor",
            "source_task_id": "t1", "created_at": datetime.now(timezone.utc),
        }
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=mock_row)
        repo = SnippetRepository(pool)
        s = await repo.create_snippet(user_id="u1", content="code", source_agent="executor")
        assert s.content == "code"
        assert s.source_agent == "executor"

    @pytest.mark.asyncio
    async def test_delete_snippet(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRepository
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 1")
        repo = SnippetRepository(pool)
        assert await repo.delete_snippet("s1") is True

    @pytest.mark.asyncio
    async def test_delete_snippet_not_found(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRepository
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 0")
        repo = SnippetRepository(pool)
        assert await repo.delete_snippet("nope") is False

    @pytest.mark.asyncio
    async def test_list_snippets_with_tag(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRepository
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = SnippetRepository(pool)
        await repo.list_snippets("u1", tag="python")
        sql = pool.fetch.call_args[0][0]
        assert "ANY(tags)" in sql

    @pytest.mark.asyncio
    async def test_list_snippets_with_query(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRepository
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = SnippetRepository(pool)
        await repo.list_snippets("u1", query="executor")
        sql = pool.fetch.call_args[0][0]
        assert "ILIKE" in sql
        assert "array_to_string(tags, ' ')" in sql

    @pytest.mark.asyncio
    async def test_get_snippet(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRepository
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        repo = SnippetRepository(pool)
        assert await repo.get_snippet("nope") is None

    @pytest.mark.asyncio
    async def test_toggle_pin(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRecord, SnippetRepository
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=[
            {
                "id": "s1", "user_id": "u1", "content": "code", "tags": [],
                "source_agent": None, "source_task_id": None, "pinned": False, "created_at": None,
            },
            {
                "id": "s1", "user_id": "u1", "content": "code", "tags": [],
                "source_agent": None, "source_task_id": None, "pinned": True, "created_at": None,
            },
        ])
        repo = SnippetRepository(pool)
        assert await repo.toggle_pin("s1") is True

    @pytest.mark.asyncio
    async def test_update_snippet_content(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRepository
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={
            "id": "s1", "user_id": "u1", "content": "updated", "tags": ["python"],
            "source_agent": "polluks", "source_task_id": "t1", "pinned": False, "created_at": None,
        })
        repo = SnippetRepository(pool)
        snippet = await repo.update_snippet("s1", content="updated", tags=["python"])
        assert snippet is not None
        assert snippet.content == "updated"
        sql = pool.fetchrow.call_args[0][0]
        assert "content = $1" in sql
        assert "tags = $2" in sql


def test_snippet_api_alias_create_pin_and_export():
    from amiagi.interfaces.web.routes.snippet_routes import snippet_routes

    app = Starlette(routes=list(snippet_routes))
    app.state.snippet_repository = _FakeSnippetRepo()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)

    create_response = client.post("/api/snippets", json={"content": "print('hello')", "source": "stream"})
    assert create_response.status_code == 201
    assert create_response.json()["source"] == "polluks"

    pin_response = client.put("/api/snippets/s1/pin", json={"pinned": True})
    assert pin_response.status_code == 200
    assert pin_response.json()["pinned"] is True

    export_response = client.get("/api/snippets/export?format=markdown")
    assert export_response.status_code == 200
    assert "snippets.md" in export_response.headers.get("content-disposition", "")
    assert "print('hello')" in export_response.text


def test_snippet_api_alias_supports_query_and_update():
    from amiagi.interfaces.web.routes.snippet_routes import snippet_routes

    app = Starlette(routes=list(snippet_routes))
    app.state.snippet_repository = _FakeSnippetRepo()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)

    search_response = client.get("/api/snippets?q=hello")
    assert search_response.status_code == 200
    assert len(search_response.json()) == 1

    update_response = client.put(
        "/api/snippets/s1",
        json={"content": "updated snippet", "tags": ["updated"], "source": "kastor"},
    )
    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["content"] == "updated snippet"
    assert payload["tags"] == ["updated"]
    assert payload["source_agent"] == "kastor"


# ═══════════════════════════════════════════════════════════════
# Migration & wiring
# ═══════════════════════════════════════════════════════════════

class TestMigrationAndWiring:
    """Migration 003 and app.py wiring."""

    def test_migration_003_exists(self):
        path = _WEB_ROOT / "db/migrations/003_productivity.sql"
        assert path.exists()

    def test_migration_creates_prompts_table(self):
        sql = (_WEB_ROOT / "db/migrations/003_productivity.sql").read_text()
        assert "CREATE TABLE IF NOT EXISTS dbo.prompts" in sql

    def test_migration_creates_search_index(self):
        sql = (_WEB_ROOT / "db/migrations/003_productivity.sql").read_text()
        assert "CREATE TABLE IF NOT EXISTS dbo.search_index" in sql
        assert "tsvector" in sql

    def test_migration_creates_snippets(self):
        sql = (_WEB_ROOT / "db/migrations/003_productivity.sql").read_text()
        assert "CREATE TABLE IF NOT EXISTS dbo.snippets" in sql

    def test_migration_gin_index_on_tsvector(self):
        sql = (_WEB_ROOT / "db/migrations/003_productivity.sql").read_text()
        assert "USING GIN(content_tsv)" in sql

    def test_productivity_enhancement_migration_exists(self):
        path = _WEB_ROOT / "db/migrations/014_productivity_enhancements.sql"
        assert path.exists()

    def test_productivity_enhancement_migration_adds_prompt_usage_and_pinned(self):
        sql = (_WEB_ROOT / "db/migrations/014_productivity_enhancements.sql").read_text()
        assert "prompt_usage" in sql
        assert "ADD COLUMN IF NOT EXISTS pinned" in sql

    def test_routes_wired_in_app(self):
        import inspect
        from amiagi.interfaces.web.app import create_app
        source = inspect.getsource(create_app)
        assert "prompt_routes" in source
        assert "search_routes" in source
        assert "snippet_routes" in source
        assert "prompt_repository" in source
        assert "search_service" in source
        assert "snippet_repository" in source

    def test_prompt_routes_exist(self):
        from amiagi.interfaces.web.routes.prompt_routes import prompt_routes
        paths = [r.path for r in prompt_routes]
        assert "/prompts" in paths
        assert "/prompts/{id}" in paths
        assert "/prompts/{id}/use" in paths
        assert "/api/prompts/{id}/stats" in paths

    def test_search_routes_exist(self):
        from amiagi.interfaces.web.routes.search_routes import search_routes
        paths = [r.path for r in search_routes]
        assert "/api/search" in paths

    def test_snippet_routes_exist(self):
        from amiagi.interfaces.web.routes.snippet_routes import snippet_routes
        paths = [r.path for r in snippet_routes]
        assert "/snippets" in paths
        assert "/snippets/{id}" in paths
        assert "/api/snippets/{id}/pin" in paths
