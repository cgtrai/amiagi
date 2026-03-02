"""Tests for TeamDashboard (Phase 11)."""

from __future__ import annotations

from amiagi.domain.team_definition import AgentDescriptor, TeamDefinition
from amiagi.interfaces.team_dashboard import TeamDashboard, TeamMetricsSnapshot


class TestTeamMetricsSnapshot:
    def test_to_dict(self) -> None:
        s = TeamMetricsSnapshot(team_id="t", member_count=3, tasks_completed=5)
        d = s.to_dict()
        assert d["team_id"] == "t"
        assert d["tasks_completed"] == 5


class TestTeamDashboard:
    def _make_team(self) -> TeamDefinition:
        t = TeamDefinition(team_id="t1", name="Team Alpha")
        t.add_member(AgentDescriptor(role="lead", name="Lead"))
        t.add_member(AgentDescriptor(role="dev", name="Dev"))
        t.lead_agent_id = "lead"
        return t

    def test_register_and_list(self) -> None:
        d = TeamDashboard()
        d.register_team(self._make_team())
        assert len(d.list_teams()) == 1

    def test_unregister(self) -> None:
        d = TeamDashboard()
        d.register_team(self._make_team())
        assert d.unregister_team("t1") is True
        assert d.unregister_team("t1") is False
        assert len(d.list_teams()) == 0

    def test_get_team(self) -> None:
        d = TeamDashboard()
        d.register_team(self._make_team())
        t = d.get_team("t1")
        assert t is not None
        assert t.name == "Team Alpha"

    def test_update_and_get_metrics(self) -> None:
        d = TeamDashboard()
        d.register_team(self._make_team())
        snap = TeamMetricsSnapshot(team_id="t1", tasks_completed=10)
        d.update_metrics(snap)
        m = d.get_metrics("t1")
        assert m is not None
        assert m.tasks_completed == 10

    def test_org_chart(self) -> None:
        d = TeamDashboard()
        d.register_team(self._make_team())
        chart = d.org_chart("t1")
        assert chart["lead"] == "lead"
        assert len(chart["members"]) == 2

    def test_org_chart_not_found(self) -> None:
        d = TeamDashboard()
        chart = d.org_chart("nonexistent")
        assert "error" in chart

    def test_summary(self) -> None:
        d = TeamDashboard()
        d.register_team(self._make_team())
        s = d.summary()
        assert s["total_teams"] == 1

    def test_to_dict(self) -> None:
        d = TeamDashboard()
        data = d.to_dict()
        assert data["total_teams"] == 0
