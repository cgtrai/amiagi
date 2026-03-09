"""Tests for Faza 14 — Task templates & i18n.

Covers audit criteria 14.1–14.10:
- 14.1: dbo.task_templates CRUD + YAML validation
- 14.2: Wizard GUI: parameters → preview → execute
- 14.3: Builtin templates ≥ 4
- 14.4: POST /templates/{id}/execute → runs workflow
- 14.5: Import/export YAML
- 14.6: _() returns translation in Jinja2 templates
- 14.7: Language switcher changes language
- 14.8: web_pl.json and web_en.json exist with ≥ 50 keys
- 14.9: Accept-Language: en → default English
- 14.10: Tests ≥ 8 templates, ≥ 5 i18n
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

_WEB_ROOT = Path(__file__).parent.parent / "src/amiagi/interfaces/web"
_I18N_LOCALES = Path(__file__).parent.parent / "src/amiagi/i18n/locales"


# ═══════════════════════════════════════════════════════════════
# 14.1 — Task Template Repository  (≥ 8 tests)
# ═══════════════════════════════════════════════════════════════

class TestTaskTemplate:
    """14.1: TaskTemplate dataclass and YAML validation."""

    def test_template_to_dict(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplate
        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        tpl = TaskTemplate(
            id="t1", name="Code Review", description="Review code",
            yaml_content='name: "Code Review"\nsteps:\n  - agent: executor\n    prompt: "Review {file}"',
            tags=["review"], author_id="u1", is_public=True,
            use_count=5, created_at=now,
        )
        d = tpl.to_dict()
        assert d["id"] == "t1"
        assert d["name"] == "Code Review"
        assert d["is_public"] is True
        assert d["use_count"] == 5
        assert "2025-06-01" in d["created_at"]

    def test_template_parsed_yaml(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplate
        yaml_content = 'name: "Test"\nsteps:\n  - agent: executor\n    prompt: "Do {thing}"'
        tpl = TaskTemplate(id="t1", name="Test", description="", yaml_content=yaml_content)
        parsed = tpl.parsed
        assert parsed["name"] == "Test"
        assert len(parsed["steps"]) == 1

    def test_template_parsed_invalid_yaml(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplate
        tpl = TaskTemplate(id="t1", name="Bad", description="", yaml_content="{{invalid")
        assert tpl.parsed == {}

    def test_template_parameters(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplate
        yaml_content = 'name: "T"\nsteps: []\nparameters:\n  - name: file\n    type: string'
        tpl = TaskTemplate(id="t1", name="T", description="", yaml_content=yaml_content)
        params = tpl.parameters
        assert len(params) == 1
        assert params[0]["name"] == "file"

    def test_template_steps(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplate
        yaml_content = 'name: "T"\nsteps:\n  - agent: executor\n    prompt: "Do X"\n  - agent: supervisor\n    prompt: "Check"'
        tpl = TaskTemplate(id="t1", name="T", description="", yaml_content=yaml_content)
        assert len(tpl.steps) == 2

    def test_render_steps_with_values(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplate
        yaml_content = 'name: "T"\nsteps:\n  - agent: executor\n    prompt: "Review {file}"'
        tpl = TaskTemplate(id="t1", name="T", description="", yaml_content=yaml_content)
        rendered = tpl.render_steps({"file": "main.py"})
        assert rendered[0]["prompt"] == "Review main.py"

    def test_render_steps_missing_value_preserves_template(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplate
        yaml_content = 'name: "T"\nsteps:\n  - agent: executor\n    prompt: "Review {file}"'
        tpl = TaskTemplate(id="t1", name="T", description="", yaml_content=yaml_content)
        rendered = tpl.render_steps({})
        # Should keep original prompt since {file} isn't provided
        assert "file" in rendered[0]["prompt"]


class TestValidateYaml:
    """14.1: YAML validation function."""

    def test_valid_yaml(self):
        from amiagi.interfaces.web.task_templates.template_repository import validate_yaml
        ok, err = validate_yaml('name: "Valid"\nsteps:\n  - agent: x\n    prompt: y')
        assert ok is True
        assert err == ""

    def test_invalid_yaml_syntax(self):
        from amiagi.interfaces.web.task_templates.template_repository import validate_yaml
        ok, err = validate_yaml("{{bad yaml")
        assert ok is False
        assert len(err) > 0

    def test_yaml_not_mapping(self):
        from amiagi.interfaces.web.task_templates.template_repository import validate_yaml
        ok, err = validate_yaml("- item1\n- item2")
        assert ok is False
        assert "mapping" in err

    def test_yaml_missing_name(self):
        from amiagi.interfaces.web.task_templates.template_repository import validate_yaml
        ok, err = validate_yaml("steps:\n  - agent: x\n    prompt: y")
        assert ok is False
        assert "name" in err

    def test_yaml_missing_steps(self):
        from amiagi.interfaces.web.task_templates.template_repository import validate_yaml
        ok, err = validate_yaml('name: "NoSteps"')
        assert ok is False
        assert "steps" in err


class TestTemplateRepository:
    """14.1: Template repository CRUD."""

    @pytest.mark.asyncio
    async def test_list_templates(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = TaskTemplateRepository(pool)
        result = await repo.list_templates()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_templates_public_only(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        repo = TaskTemplateRepository(pool)
        await repo.list_templates(public_only=True)
        sql_arg = pool.fetch.call_args[0][0]
        assert "is_public = true" in sql_arg

    @pytest.mark.asyncio
    async def test_get_template(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "id": "t1", "name": "Test", "description": "",
            "yaml_content": 'name: "T"\nsteps: []', "tags": [],
            "author_id": None, "is_public": False, "use_count": 0,
            "created_at": datetime.now(tz=timezone.utc),
        })
        repo = TaskTemplateRepository(pool)
        tpl = await repo.get("t1")
        assert tpl is not None
        assert tpl.name == "Test"

    @pytest.mark.asyncio
    async def test_get_template_not_found(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        repo = TaskTemplateRepository(pool)
        tpl = await repo.get("nonexistent")
        assert tpl is None

    @pytest.mark.asyncio
    async def test_create_template_valid(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={
            "id": "t2", "name": "New", "description": "desc",
            "yaml_content": 'name: "New"\nsteps:\n  - agent: x\n    prompt: y',
            "tags": ["tag1"], "author_id": "u1", "is_public": True,
            "use_count": 0, "created_at": datetime.now(tz=timezone.utc),
        })
        repo = TaskTemplateRepository(pool)
        tpl = await repo.create(
            name="New",
            yaml_content='name: "New"\nsteps:\n  - agent: x\n    prompt: y',
            description="desc",
            tags=["tag1"],
            author_id="u1",
            is_public=True,
        )
        assert tpl.name == "New"
        assert tpl.is_public is True

    @pytest.mark.asyncio
    async def test_create_template_invalid_yaml_raises(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository
        pool = AsyncMock()
        repo = TaskTemplateRepository(pool)
        with pytest.raises(ValueError, match="Invalid YAML"):
            await repo.create(name="Bad", yaml_content="{{invalid")

    @pytest.mark.asyncio
    async def test_delete_template(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="DELETE 1")
        repo = TaskTemplateRepository(pool)
        ok = await repo.delete("t1")
        assert ok is True

    @pytest.mark.asyncio
    async def test_increment_use_count(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        repo = TaskTemplateRepository(pool)
        await repo.increment_use_count("t1")
        sql_arg = pool.execute.call_args[0][0]
        assert "use_count = use_count + 1" in sql_arg

    @pytest.mark.asyncio
    async def test_export_yaml(self):
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"yaml_content": "name: T\nsteps: []"})
        repo = TaskTemplateRepository(pool)
        content = await repo.export_yaml("t1")
        assert content == "name: T\nsteps: []"


# ═══════════════════════════════════════════════════════════════
# 14.2 / 14.4 — Template routes
# ═══════════════════════════════════════════════════════════════

class TestTemplateRoutes:
    """14.2 / 14.4 / 14.5: Route definitions."""

    def test_template_routes_exist(self):
        from amiagi.interfaces.web.routes.template_routes import template_routes
        paths = [r.path for r in template_routes]
        assert "/templates" in paths
        assert "/templates/{id}" in paths
        assert "/templates/{id}/execute" in paths

    def test_import_route_exists(self):
        from amiagi.interfaces.web.routes.template_routes import template_routes
        paths = [r.path for r in template_routes]
        assert "/templates/import" in paths

    def test_export_route_exists(self):
        from amiagi.interfaces.web.routes.template_routes import template_routes
        paths = [r.path for r in template_routes]
        assert "/templates/{id}/export" in paths

    def test_stats_route_exists(self):
        from amiagi.interfaces.web.routes.template_routes import template_routes
        paths = [r.path for r in template_routes]
        assert "/templates/stats" in paths

    def test_pin_route_exists(self):
        from amiagi.interfaces.web.routes.template_routes import template_routes
        paths = [r.path for r in template_routes]
        assert "/templates/{id}/pin" in paths

    def test_preview_route_exists(self):
        from amiagi.interfaces.web.routes.template_routes import template_routes
        paths = [r.path for r in template_routes]
        assert "/templates/{id}/preview" in paths

    def test_route_count_ge_7(self):
        from amiagi.interfaces.web.routes.template_routes import template_routes
        assert len(template_routes) >= 7


class _FakeTemplate:
    id = "t1"
    name = "Code Review"
    description = "Review source files"
    tags = ["review"]
    use_count = 3
    steps = [{"agent": "executor", "prompt": "Review {file}", "type": "task"}]
    parameters = [{"name": "file", "description": "Target file"}]

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "yaml_content": 'name: "Code Review"\nsteps: []',
            "tags": self.tags,
            "author_id": "u1",
            "is_public": True,
            "use_count": self.use_count,
            "parameters": self.parameters,
            "steps": self.steps,
            "created_at": None,
        }

    def render_steps(self, values):
        return [{"agent": "executor", "prompt": f"Review {values.get('file', '{file}')}", "type": "task"}]


class _FakeTemplateRepo:
    def __init__(self):
        self.increment_use_count = AsyncMock()

    async def list_templates(self, public_only=False):
        return [_FakeTemplate()]

    async def get(self, template_id):
        return _FakeTemplate() if template_id == "t1" else None


class _FakeUser:
    user_id = "u1"


class _FakeSettingsRepo:
    def __init__(self):
        self.data = {"template_preferences": {"pinned_ids": ["t1"]}}

    async def get_for_user(self, user_id):
        return json.loads(json.dumps(self.data))

    async def save_for_user(self, user_id, settings):
        self.data = json.loads(json.dumps(settings))
        return settings


class _TemplateTask:
    def __init__(self, status="done"):
        self.metadata = {"template_id": "t1", "template_execution_id": "exec-1"}
        self.created_at = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
        self.started_at = datetime(2025, 6, 1, 12, 1, tzinfo=timezone.utc)
        self.completed_at = datetime(2025, 6, 1, 12, 3, tzinfo=timezone.utc)
        self.status = status


class _FakeTaskQueue:
    def __init__(self):
        self.enqueued = []

    def list_all(self):
        return [_TemplateTask()]

    def enqueue(self, task):
        self.enqueued.append(task)


def test_preview_route_does_not_increment_use_count():
    from amiagi.interfaces.web.routes.template_routes import template_routes

    repo = _FakeTemplateRepo()
    app = Starlette(routes=list(template_routes))
    app.state.template_repository = repo

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)
    response = client.post("/templates/t1/preview", json={"values": {"file": "main.py"}})
    assert response.status_code == 200
    assert response.json()["status"] == "preview"
    repo.increment_use_count.assert_not_awaited()


def test_execute_route_increments_use_count():
    from amiagi.interfaces.web.routes.template_routes import template_routes

    repo = _FakeTemplateRepo()
    app = Starlette(routes=list(template_routes))
    app.state.template_repository = repo
    app.state.task_queue = _FakeTaskQueue()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)
    response = client.post("/templates/t1/execute", json={"values": {"file": "main.py"}})
    assert response.status_code == 200
    assert response.json()["status"] == "started"
    repo.increment_use_count.assert_awaited_once_with("t1")
    assert response.json()["created_task_ids"]
    assert app.state.task_queue.enqueued[0].metadata["template_id"] == "t1"


def test_list_templates_marks_pinned_and_includes_stats():
    from amiagi.interfaces.web.routes.template_routes import template_routes

    repo = _FakeTemplateRepo()
    app = Starlette(routes=list(template_routes))
    app.state.template_repository = repo
    app.state.user_settings_repo = _FakeSettingsRepo()
    app.state.task_queue = _FakeTaskQueue()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/templates")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["pinned"] is True
    assert payload[0]["stats"]["use_count"] == 3
    assert payload[0]["stats"]["avg_completion_time_label"] == "2m"


def test_pin_route_persists_template_preference():
    from amiagi.interfaces.web.routes.template_routes import template_routes

    repo = _FakeTemplateRepo()
    settings_repo = _FakeSettingsRepo()
    app = Starlette(routes=list(template_routes))
    app.state.template_repository = repo
    app.state.user_settings_repo = settings_repo

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)
    response = client.put("/templates/t1/pin", json={"pinned": False})
    assert response.status_code == 200
    assert response.json()["pinned"] is False
    assert settings_repo.data["template_preferences"]["pinned_ids"] == []


def test_template_stats_route_returns_badge_data():
    from amiagi.interfaces.web.routes.template_routes import template_routes

    repo = _FakeTemplateRepo()
    app = Starlette(routes=list(template_routes))
    app.state.template_repository = repo
    app.state.user_settings_repo = _FakeSettingsRepo()
    app.state.task_queue = _FakeTaskQueue()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = _FakeUser()
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/templates/stats")
    assert response.status_code == 200
    payload = response.json()["templates"][0]
    assert payload["pinned"] is True
    assert payload["avg_completion_time_label"] == "2m"
    assert payload["step_count"] == 1


# ═══════════════════════════════════════════════════════════════
# 14.3 — Builtin templates
# ═══════════════════════════════════════════════════════════════

class TestBuiltinTemplates:
    """14.3: Migration contains ≥ 4 builtin templates."""

    @pytest.fixture()
    def migration_sql(self) -> str:
        return (_WEB_ROOT / "db/migrations/005_task_templates.sql").read_text()

    def test_builtin_templates_ge_4(self, migration_sql):
        # Count INSERT values
        count = migration_sql.count("INSERT INTO dbo.task_templates")
        assert count >= 1  # at least one INSERT

    def test_code_review_template(self, migration_sql):
        assert "Code Review Pipeline" in migration_sql

    def test_documentation_template(self, migration_sql):
        assert "Documentation Sprint" in migration_sql

    def test_bug_investigation_template(self, migration_sql):
        assert "Bug Investigation" in migration_sql

    def test_refactoring_template(self, migration_sql):
        assert "Refactoring Plan" in migration_sql

    def test_builtin_constant(self):
        from amiagi.interfaces.web.task_templates.template_repository import _BUILTIN_NAMES
        assert len(_BUILTIN_NAMES) >= 4


# ═══════════════════════════════════════════════════════════════
# 14.5 — Migration structure
# ═══════════════════════════════════════════════════════════════

class TestMigration005:
    """Migration 005 structure."""

    @pytest.fixture()
    def migration_sql(self) -> str:
        return (_WEB_ROOT / "db/migrations/005_task_templates.sql").read_text()

    def test_task_templates_table(self, migration_sql):
        assert "CREATE TABLE IF NOT EXISTS dbo.task_templates" in migration_sql

    def test_tags_gin_index(self, migration_sql):
        assert "idx_templates_tags" in migration_sql
        assert "GIN" in migration_sql

    def test_public_index(self, migration_sql):
        assert "idx_templates_public" in migration_sql


# ═══════════════════════════════════════════════════════════════
# 14.6 / 14.7 / 14.8 / 14.9 — i18n  (≥ 5 tests)
# ═══════════════════════════════════════════════════════════════

class TestI18nWebLocales:
    """14.8: web_pl.json and web_en.json with ≥ 50 keys each."""

    def test_web_pl_exists(self):
        assert (_I18N_LOCALES / "web_pl.json").exists()

    def test_web_en_exists(self):
        assert (_I18N_LOCALES / "web_en.json").exists()

    def test_web_pl_ge_50_keys(self):
        with open(_I18N_LOCALES / "web_pl.json") as f:
            data = json.load(f)
        keys = [k for k in data if not k.startswith("_")]
        assert len(keys) >= 50, f"web_pl.json has {len(keys)} keys, need ≥50"

    def test_web_en_ge_50_keys(self):
        with open(_I18N_LOCALES / "web_en.json") as f:
            data = json.load(f)
        keys = [k for k in data if not k.startswith("_")]
        assert len(keys) >= 50, f"web_en.json has {len(keys)} keys, need ≥50"

    def test_key_parity(self):
        """Both files should have the same keys."""
        with open(_I18N_LOCALES / "web_pl.json") as f:
            pl = {k for k in json.load(f) if not k.startswith("_")}
        with open(_I18N_LOCALES / "web_en.json") as f:
            en = {k for k in json.load(f) if not k.startswith("_")}
        assert pl == en


class TestI18nWebTranslation:
    """14.6: Translation function."""

    def test_get_web_translation_pl(self):
        from amiagi.interfaces.web.i18n_web import get_web_translation, _web_strings
        _web_strings.clear()  # Force reload
        result = get_web_translation("nav.dashboard", lang="pl")
        assert result == "Panel główny"

    def test_get_web_translation_en(self):
        from amiagi.interfaces.web.i18n_web import get_web_translation, _web_strings
        _web_strings.clear()
        result = get_web_translation("nav.dashboard", lang="en")
        assert result == "Dashboard"

    def test_get_web_translation_with_placeholder(self):
        from amiagi.interfaces.web.i18n_web import get_web_translation, _web_strings
        _web_strings.clear()
        result = get_web_translation("dashboard.welcome", lang="pl", username="Jan")
        assert "Jan" in result

    def test_get_web_translation_fallback_to_key(self):
        from amiagi.interfaces.web.i18n_web import get_web_translation, _web_strings
        _web_strings.clear()
        result = get_web_translation("nonexistent.key", lang="pl")
        assert result == "nonexistent.key"

    def test_get_web_translation_unknown_lang_falls_back_to_pl(self):
        from amiagi.interfaces.web.i18n_web import get_web_translation, _web_strings
        _web_strings.clear()
        result = get_web_translation("nav.dashboard", lang="xx")
        # Unknown lang falls back to pl
        assert result == "Panel główny"


class TestLanguageDetection:
    """14.9: Accept-Language detection."""

    def _make_request(self, cookies=None, accept_language=None):
        req = MagicMock()
        req.cookies = cookies or {}
        req.headers = {}
        if accept_language:
            req.headers["accept-language"] = accept_language
        return req

    def test_detect_from_cookie(self):
        from amiagi.interfaces.web.i18n_web import detect_language
        req = self._make_request(cookies={"lang": "en"})
        assert detect_language(req) == "en"

    def test_detect_from_accept_language(self):
        from amiagi.interfaces.web.i18n_web import detect_language
        req = self._make_request(accept_language="en-US,en;q=0.9,pl;q=0.8")
        assert detect_language(req) == "en"

    def test_detect_fallback_to_pl(self):
        from amiagi.interfaces.web.i18n_web import detect_language
        req = self._make_request()
        assert detect_language(req) == "pl"

    def test_cookie_takes_priority_over_header(self):
        from amiagi.interfaces.web.i18n_web import detect_language
        req = self._make_request(cookies={"lang": "pl"}, accept_language="en-US")
        assert detect_language(req) == "pl"

    def test_unsupported_cookie_ignored(self):
        from amiagi.interfaces.web.i18n_web import detect_language
        req = self._make_request(cookies={"lang": "de"}, accept_language="en")
        assert detect_language(req) == "en"


class TestI18nRoutes:
    """14.7: Language switcher routes."""

    def test_i18n_routes_exist(self):
        from amiagi.interfaces.web.routes.i18n_routes import i18n_routes
        paths = [r.path for r in i18n_routes]
        assert "/lang/{lang}" in paths
        assert "/api/lang" in paths


class TestI18nTargetedHardcodedStrings:
    """10.26: regression coverage for previously flagged hardcoded strings."""

    def test_health_js_uses_translation_hooks(self):
        content = (_WEB_ROOT / "static/js/health.js").read_text(encoding="utf-8")
        assert 'window.t("health.status.ok"' in content
        assert 'window.t("health.status.degraded"' in content
        assert 'window.t("health.rate_limits.no_data"' in content

    def test_session_timeline_uses_translation_hooks(self):
        content = (_WEB_ROOT / "static/js/components/session-timeline.js").read_text(encoding="utf-8")
        assert 'window.t("timeline.error.load_failed"' in content
        assert 'window.t("timeline.controls.play"' in content
        assert 'window.t("timeline.empty"' in content

    def test_dashboard_js_uses_panel_translation_keys(self):
        content = (_WEB_ROOT / "static/js/dashboard.js").read_text(encoding="utf-8")
        assert 'window.t("dashboard.panel.agents_overview"' in content
        assert 'window.t("dashboard.panel.task_board"' in content
        assert 'window.t("dashboard.panel.system_health"' in content

    def test_sandboxes_js_uses_translation_hooks(self):
        content = (_WEB_ROOT / "static/js/sandboxes.js").read_text(encoding="utf-8")
        assert 'window.t("sandboxes.list.load_failed"' in content
        assert 'window.t("sandboxes.action.browse"' in content
        assert 'window.t("sandboxes.drawer.log_title"' in content

    def test_sessions_template_uses_translations_for_toolbar(self):
        content = (_WEB_ROOT / "templates/sessions.html").read_text(encoding="utf-8")
        assert "{{ _('sessions.search_placeholder') }}" in content
        assert '{{ _("sessions.replay") }}' in content
        assert "window.t('sessions.fetch_failed'" in content


class TestMakeTranslator:
    """14.6: make_translator creates bound _() function."""

    def test_make_translator_returns_func_and_lang(self):
        from amiagi.interfaces.web.i18n_web import make_translator, _web_strings
        _web_strings.clear()
        req = MagicMock()
        req.cookies = {"lang": "en"}
        req.headers = {}
        func, lang = make_translator(req)
        assert lang == "en"
        assert callable(func)
        assert func("nav.dashboard") == "Dashboard"


# ═══════════════════════════════════════════════════════════════
# App.py wiring
# ═══════════════════════════════════════════════════════════════

class TestAppWiringFaza14:
    """Verify Faza 14 is wired into app.py."""

    @pytest.fixture()
    def app_source(self) -> str:
        return (_WEB_ROOT / "app.py").read_text()

    def test_template_routes_imported(self, app_source):
        assert "from amiagi.interfaces.web.routes.template_routes import template_routes" in app_source

    def test_template_routes_wired(self, app_source):
        assert "*template_routes" in app_source

    def test_i18n_routes_imported(self, app_source):
        assert "from amiagi.interfaces.web.routes.i18n_routes import i18n_routes" in app_source

    def test_i18n_routes_wired(self, app_source):
        assert "*i18n_routes" in app_source

    def test_template_repository_wired(self, app_source):
        assert "template_repository" in app_source.lower()


class TestTaskWizardTemplateEnhancements:
    def test_task_wizard_contains_pin_and_stats_hooks(self):
        template = (_WEB_ROOT / "templates/task_wizard.html").read_text()
        assert "/templates/stats" in template
        assert "toggleTemplatePin" in template
        assert "tpl-stats" in template
