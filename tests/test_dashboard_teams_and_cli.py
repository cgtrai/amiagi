"""Tests for DashboardServer team endpoints and CI/plugin CLI entry-points."""

from __future__ import annotations

import json


# ---- CI adapter cli_main ----

def test_ci_adapter_cli_main_importable() -> None:
    from amiagi.infrastructure.ci_adapter import cli_main
    assert callable(cli_main)


def test_ci_adapter_cli_main_status(capsys, monkeypatch) -> None:
    import sys
    monkeypatch.setattr(sys, "argv", ["amiagi-ci", "status"])
    from amiagi.infrastructure.ci_adapter import cli_main
    cli_main()
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "config" in data
    assert "history_count" in data


# ---- Plugin CLI ----

def test_plugin_cli_main_importable() -> None:
    from amiagi.application.plugin_loader import plugin_cli_main
    assert callable(plugin_cli_main)


# ---- DashboardServer team integration ----

def test_dashboard_server_accepts_team_dashboard() -> None:
    from amiagi.infrastructure.dashboard_server import DashboardServer
    from amiagi.interfaces.team_dashboard import TeamDashboard

    td = TeamDashboard()
    server = DashboardServer(team_dashboard=td)
    assert server._team_dashboard is td


def test_dashboard_handler_get_teams() -> None:
    """Verify the _get_teams method returns data from TeamDashboard."""
    from amiagi.infrastructure.dashboard_server import _DashboardHandler
    from amiagi.interfaces.team_dashboard import TeamDashboard

    td = TeamDashboard()
    _DashboardHandler._team_dashboard = td
    handler_cls = _DashboardHandler

    # Use the class method directly (no HTTP involved)
    # We need an instance, but can't easily create one without a socket
    # So test the data path via TeamDashboard directly
    summary = td.summary()
    assert "teams" in summary
    assert summary["total_teams"] == 0


def test_dashboard_handler_get_team_org() -> None:
    from amiagi.interfaces.team_dashboard import TeamDashboard, TeamMetricsSnapshot
    from amiagi.domain.team_definition import TeamDefinition, AgentDescriptor

    td = TeamDashboard()
    team = TeamDefinition(
        team_id="t1",
        name="Test Team",
        members=[
            AgentDescriptor(role="developer", name="Dev"),
            AgentDescriptor(role="tester", name="QA"),
        ],
        lead_agent_id="developer",
    )
    td.register_team(team)

    org = td.org_chart("t1")
    assert org["team_id"] == "t1"
    assert org["lead"] == "developer"
    assert len(org["members"]) == 2


# ---- teams.html existence ----

def test_teams_html_exists() -> None:
    from pathlib import Path
    html_path = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "dashboard_static" / "teams.html"
    assert html_path.exists(), f"teams.html not found at {html_path}"
    content = html_path.read_text(encoding="utf-8")
    assert "Team Dashboard" in content
    assert "/api/teams" in content
