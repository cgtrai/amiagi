from __future__ import annotations

from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.application.team_composer import TeamComposer
from amiagi.domain.team_definition import AgentDescriptor, TeamDefinition
from amiagi.interfaces.team_dashboard import TeamDashboard
from amiagi.interfaces.web.routes.team_routes import team_routes


def _make_app(team_dashboard=None, team_composer=None) -> Starlette:
    app = Starlette(routes=list(team_routes))
    app.state.team_dashboard = team_dashboard or TeamDashboard()
    app.state.team_composer = team_composer or TeamComposer()
    return app


def test_create_team_with_members() -> None:
    client = TestClient(_make_app())
    response = client.post('/api/teams', json={
        'name': 'Alpha',
        'description': 'Core team',
        'members': [
            {'role': 'lead', 'name': 'Lead'},
            {'role': 'tester', 'name': 'QA'},
        ],
        'lead_agent_id': 'lead',
    })
    assert response.status_code == 201
    data = response.json()['team']
    assert data['name'] == 'Alpha'
    assert len(data['members']) == 2


def test_update_team() -> None:
    client = TestClient(_make_app())
    created = client.post('/api/teams', json={
        'name': 'Alpha',
        'members': [{'role': 'lead', 'name': 'Lead'}],
    }).json()['team']
    response = client.put(f"/api/teams/{created['team_id']}", json={
        'name': 'Beta',
        'description': 'Updated',
        'members': [{'role': 'architect', 'name': 'Arch'}],
        'lead_agent_id': 'architect',
    })
    assert response.status_code == 200
    team = response.json()['team']
    assert team['name'] == 'Beta'
    assert team['lead_agent_id'] == 'architect'


def test_delete_team() -> None:
    client = TestClient(_make_app())
    created = client.post('/api/teams', json={
        'name': 'Alpha',
        'members': [{'role': 'lead', 'name': 'Lead'}],
    }).json()['team']
    deleted = client.delete(f"/api/teams/{created['team_id']}")
    assert deleted.status_code == 200
    listing = client.get('/api/teams').json()
    assert listing['total_teams'] == 0


def test_create_team_from_template() -> None:
    composer = TeamComposer()
    template = composer.build_team('backend api test', team_id='tmpl-1')
    template.name = 'Template Team'
    composer.register_template(template)
    client = TestClient(_make_app(team_composer=composer))
    response = client.post('/api/teams', json={
        'template_id': 'tmpl-1',
        'name': 'Instanced Team',
    })
    assert response.status_code == 201
    team = response.json()['team']
    assert team['name'] == 'Instanced Team'
    assert len(team['members']) >= 1


def test_list_templates() -> None:
    composer = TeamComposer()
    composer.register_template(composer.build_team('frontend ui', team_id='tmpl-2'))
    client = TestClient(_make_app(team_composer=composer))
    response = client.get('/api/teams/templates')
    assert response.status_code == 200
    assert response.json()['total'] == 1


def test_recommend_team_with_sync_composer() -> None:
    class SyncComposer:
        def recommend(self, project_context: str):
            return {
                'recommended_roles': ['supervisor', 'executor'],
                'reasoning': f'recommend for {project_context}',
                'confidence': 0.91,
            }

        def build_team(self, project_context: str):
            return TeamDefinition(
                team_id='preview-1',
                name='Preview Team',
                members=[
                    AgentDescriptor(role='supervisor', name='Lead Agent'),
                    AgentDescriptor(role='executor', name='Exec Agent'),
                ],
                lead_agent_id='supervisor',
                workflow='collaborative',
                project_context=project_context,
            )

    client = TestClient(_make_app(team_composer=SyncComposer()))
    response = client.get('/api/teams/recommend?project_context=launch%20project')
    assert response.status_code == 200
    data = response.json()
    assert data['ok'] is True
    assert data['project_context'] == 'launch project'
    assert data['recommendation']['recommended_roles'] == ['supervisor', 'executor']
    assert data['preview_team']['member_roles'] == ['supervisor', 'executor']


def test_deploy_team_updates_status_for_sync_dashboard() -> None:
    class DeployDashboard(TeamDashboard):
        def deploy(self, team_id: str):
            return {'team_id': team_id, 'status': 'deployed', 'note': 'sync deploy ok'}

    dashboard = DeployDashboard()
    team = TeamDefinition(
        team_id='team-sync',
        name='Sync Team',
        members=[AgentDescriptor(role='supervisor', name='Lead')],
        lead_agent_id='supervisor',
    )
    dashboard.register_team(team)
    client = TestClient(_make_app(team_dashboard=dashboard))

    response = client.post('/api/teams/team-sync/deploy')
    assert response.status_code == 200
    data = response.json()
    assert data['result']['status'] == 'deployed'
    assert data['team']['status'] == 'deployed'
    assert data['team']['metadata']['deploy_note'] == 'sync deploy ok'


def test_deploy_team_without_runtime_support_returns_saved_only() -> None:
    dashboard = TeamDashboard()
    team = TeamDefinition(
        team_id='team-draft',
        name='Draft Team',
        members=[AgentDescriptor(role='supervisor', name='Lead')],
        lead_agent_id='supervisor',
    )
    dashboard.register_team(team)
    client = TestClient(_make_app(team_dashboard=dashboard))

    response = client.post('/api/teams/team-draft/deploy')

    assert response.status_code == 409
    data = response.json()
    assert data['ok'] is False
    assert data['error'] == 'deploy_not_supported'
    assert data['result']['status'] == 'saved_only'
    assert data['team']['status'] == 'saved_only'
    assert data['team']['metadata']['deploy_note'] == 'runtime deploy not available'