from __future__ import annotations

from pathlib import Path


def test_teams_page_contains_recommendation_and_org_chart_hooks() -> None:
    path = Path(__file__).resolve().parent.parent / 'src/amiagi/interfaces/web/templates/teams.html'
    content = path.read_text(encoding='utf-8')

    assert 'btn-recommend-team' in content
    assert 'loadTeamRecommendation' in content
    assert 'useRecommendationInWizard' in content
    assert 'btn-org-team' in content
    assert 'wizard-member-assignment-row' in content
    assert '/api/teams/recommend' in content


def test_teams_page_contains_deployment_and_status_cards() -> None:
    path = Path(__file__).resolve().parent.parent / 'src/amiagi/interfaces/web/templates/teams.html'
    content = path.read_text(encoding='utf-8')

    assert 'teamStatusBadge' in content
    assert 'Saved only' in content
    assert 'deployTeam(teamId)' in content
    assert 'team-card-insights' in content
    assert 'recommendation preview will appear here'.lower() in content.lower()
    assert '>Deploy</button>' in content
    assert '🚀 Deploy' not in content
    assert 'notifyTeam' in content
    assert 'responseErrorMessage' in content
    assert 'Team deleted' in content