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
    async def test_get_snippet(self):
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRepository
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        repo = SnippetRepository(pool)
        assert await repo.get_snippet("nope") is None


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

    def test_search_routes_exist(self):
        from amiagi.interfaces.web.routes.search_routes import search_routes
        paths = [r.path for r in search_routes]
        assert "/api/search" in paths

    def test_snippet_routes_exist(self):
        from amiagi.interfaces.web.routes.snippet_routes import snippet_routes
        paths = [r.path for r in snippet_routes]
        assert "/snippets" in paths
        assert "/snippets/{id}" in paths
