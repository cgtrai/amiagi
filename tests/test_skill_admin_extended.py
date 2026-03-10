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
from amiagi.interfaces.web.skills.skill_repository import SkillRecord, TraitRecord


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
        self._skill = SkillRecord(
            id="skill-1",
            name="python-expert",
            display_name="Python Expert",
            category="coding",
            description="Expert Python assistance",
            content="## Python Expert\nAlways explain tradeoffs.",
            trigger_keywords=["python", "refactor"],
            compatible_tools=["shell"],
            compatible_roles=["executor"],
            token_cost=120,
            priority=70,
            is_active=True,
            version=2,
        )
        self._trait = TraitRecord(
            id="trait-1",
            trait_type="style",
            agent_role="executor",
            name="friendly",
            content="Use a friendly tone.",
            token_cost=40,
            priority=55,
            is_active=True,
        )
        self.list_skills = AsyncMock(return_value=[self._skill])
        self.get_skill = AsyncMock(return_value=self._skill)
        self.create_skill = AsyncMock(return_value=self._skill)
        self.update_skill = AsyncMock(return_value=self._skill)
        self.delete_skill = AsyncMock(return_value=True)
        self.skill_usage_stats = AsyncMock(return_value={"total_uses": 5, "useful_count": 4, "total_tokens": 280})
        self.skill_usage_map = AsyncMock(return_value=[{
            "skill_id": "skill-1",
            "name": "python-expert",
            "display_name": "Python Expert",
            "total_uses": 5,
            "useful_count": 4,
            "total_tokens": 280,
            "agents": [{"agent_role": "executor", "total_uses": 5, "useful_count": 4, "total_tokens": 280}],
        }])
        self.list_traits = AsyncMock(return_value=[self._trait])
        self.create_trait = AsyncMock(return_value=self._trait)
        self.get_trait = AsyncMock(return_value=self._trait)
        self.update_trait = AsyncMock(return_value=self._trait)
        self.delete_trait = AsyncMock(return_value=True)


def _make_client() -> tuple[TestClient, _Repo, AsyncMock]:
    repo = _Repo()
    provider = AsyncMock()
    app = Starlette(
        routes=list(skill_admin_routes),
        middleware=[Middleware(_InjectUserMiddleware, user=_FakeUser())],
    )
    app.state.skill_repository = repo
    app.state.runtime_skill_provider = provider
    return TestClient(app, raise_server_exceptions=False), repo, provider


def test_skill_preview_route_returns_context_payload() -> None:
    client, repo, _provider = _make_client()

    response = client.get('/admin/skills/skill-1/preview')

    assert response.status_code == 200
    payload = response.json()
    assert 'prompt_preview' in payload
    assert payload['linked_agents'][0]['agent_role'] == 'executor'
    repo.get_skill.assert_awaited_once()
    repo.skill_usage_map.assert_awaited_once()


def test_skill_edit_html_route_is_available() -> None:
    client, _repo, _provider = _make_client()

    response = client.get('/admin/skills/skill-1/edit', headers={'accept': 'text/html'})

    assert response.status_code == 503
    assert response.json() == {'error': 'templates_not_available'}


def test_traits_route_allows_required_payload_shape() -> None:
    client, repo, _provider = _make_client()

    response = client.post('/admin/traits', json={
        'name': 'friendly',
        'trait_type': 'style',
        'agent_role': 'executor',
        'content': 'Use a friendly tone.',
        'token_cost': 40,
        'priority': 55,
    })

    assert response.status_code == 201
    repo.create_trait.assert_awaited_once()


def test_skill_create_refreshes_runtime_skill_provider() -> None:
    client, repo, provider = _make_client()

    response = client.post('/admin/skills', json={
        'name': 'planning-discipline',
        'content': 'Create and maintain a staged plan.',
    })

    assert response.status_code == 201
    provider.refresh.assert_awaited_once_with(repo, None)


def test_skills_template_contains_preview_stats_and_filters() -> None:
    template = Path('src/amiagi/interfaces/web/templates/admin/skills.html').read_text(encoding='utf-8')

    assert 'skill-category-filter' in template
    assert 'skill-role-filter' in template
    assert 'openSkillPreview' in template
    assert 'openSkillStats' in template
    assert '/admin/skills/' in template
    assert '/admin/skills/import' in template
    assert 'data-drag-handle="true"' in template
    assert 'persistSkillOrdering' in template
    assert 'moveSkillBeforeTarget' in template
    assert 'Skill order updated.' in template


def test_traits_template_contains_grouped_cards_and_filters() -> None:
    template = Path('src/amiagi/interfaces/web/templates/admin/traits.html').read_text(encoding='utf-8')

    assert 'trait-type-filter' in template
    assert 'trait-role-filter' in template
    assert 'details class="glass-card trait-group"' in template
    assert 'openTraitPreview' in template
    assert 'tr-content' in template


def test_skill_edit_template_contains_live_preview() -> None:
    template = Path('src/amiagi/interfaces/web/templates/admin/skill_edit.html').read_text(encoding='utf-8')

    assert 'sk-preview' in template
    assert 'renderPreview' in template
    assert 'skill-edit-layout' in template


class _ProjectSkillRepo:
    def __init__(self) -> None:
        self._skills = {
            ("polluks", "web-research-local"): {
                "role": "polluks",
                "name": "web-research-local",
                "display_name": "Web Research Local",
                "description": "Temporary project workflow",
                "content": "Research websites and compare offers.",
                "trigger_keywords": ["internet"],
                "compatible_tools": ["search_web"],
                "compatible_roles": ["polluks"],
                "priority": 60,
                "path": "/tmp/polluks/web-research-local.md",
                "updated_at": "2026-03-10T00:00:00+00:00",
                "source": "file",
            },
        }

    def list_skills(self, *, role=None):
        items = list(self._skills.values())
        if role:
            items = [item for item in items if item["role"] == role]
        return [type("Record", (), {"to_dict": lambda self, item=item: dict(item)})() for item in items]

    def get_skill(self, role, name):
        item = self._skills.get((role, name))
        if item is None:
            return None
        return type("Record", (), {"to_dict": lambda self, item=item: dict(item), **item})()

    def upsert_skill(self, **payload):
        item = {
            "role": payload["role"],
            "name": payload["name"],
            "display_name": payload.get("display_name") or payload["name"],
            "description": payload.get("description", ""),
            "content": payload["content"],
            "trigger_keywords": list(payload.get("trigger_keywords", [])),
            "compatible_tools": list(payload.get("compatible_tools", [])),
            "compatible_roles": list(payload.get("compatible_roles", [])),
            "priority": int(payload.get("priority", 50)),
            "path": f"/tmp/{payload['role']}/{payload['name']}.md",
            "updated_at": "2026-03-10T00:00:00+00:00",
            "source": "file",
        }
        self._skills[(payload["role"], payload["name"])] = item
        return type("Record", (), {"to_dict": lambda self, item=item: dict(item), **item})()

    def delete_skill(self, role, name):
        return self._skills.pop((role, name), None) is not None


def _make_project_skill_client() -> tuple[TestClient, AsyncMock]:
    app = Starlette(
        routes=list(skill_admin_routes),
        middleware=[Middleware(_InjectUserMiddleware, user=_FakeUser())],
    )
    app.state.skill_repository = _Repo()
    app.state.project_skill_repository = _ProjectSkillRepo()
    runtime_provider = AsyncMock()
    app.state.runtime_skill_provider = runtime_provider
    return TestClient(app, raise_server_exceptions=False), runtime_provider


def test_project_skill_routes_support_crud_and_refresh_runtime() -> None:
    client, runtime_provider = _make_project_skill_client()

    page = client.get('/admin/project-skills', headers={'accept': 'text/html'})
    assert page.status_code == 200

    listing = client.get('/admin/project-skills')
    assert listing.status_code == 200
    assert listing.json()['skills'][0]['source'] == 'file'

    created = client.post('/admin/project-skills', json={
        'role': 'polluks',
        'name': 'xlsx-export-local',
        'content': 'Use openpyxl or fallback.',
        'compatible_tools': ['run_python'],
        'compatible_roles': ['polluks'],
    })
    assert created.status_code == 201

    updated = client.put('/admin/project-skills/polluks/xlsx-export-local', json={
        'content': 'Use openpyxl and verify output.',
        'priority': 75,
    })
    assert updated.status_code == 200

    deleted = client.delete('/admin/project-skills/polluks/xlsx-export-local')
    assert deleted.status_code == 200
    assert runtime_provider.refresh.await_count >= 3


def test_project_skills_template_and_nav_expose_section() -> None:
    template = Path('src/amiagi/interfaces/web/templates/admin/project_skills.html').read_text(encoding='utf-8')
    nav = Path('src/amiagi/interfaces/web/templates/partials/admin_nav.html').read_text(encoding='utf-8')

    assert '/admin/project-skills' in template
    assert 'Project Skills' in template
    assert '/admin/project-skills' in nav
