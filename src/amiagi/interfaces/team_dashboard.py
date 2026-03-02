"""Phase 11 — Team dashboard extension (interfaces).

Provides summary views and org-chart data for a managed team.
Designed to integrate with the existing DashboardServer if available.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

from amiagi.domain.team_definition import TeamDefinition


@dataclass
class TeamMetricsSnapshot:
    """Point-in-time metrics for a team."""

    team_id: str
    member_count: int = 0
    tasks_completed: int = 0
    tasks_pending: int = 0
    total_cost_usd: float = 0.0
    avg_quality_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "member_count": self.member_count,
            "tasks_completed": self.tasks_completed,
            "tasks_pending": self.tasks_pending,
            "total_cost_usd": self.total_cost_usd,
            "avg_quality_score": self.avg_quality_score,
            "metadata": self.metadata,
        }


class TeamDashboard:
    """Manages team-level views and metrics."""

    def __init__(self) -> None:
        self._teams: dict[str, TeamDefinition] = {}
        self._metrics: dict[str, TeamMetricsSnapshot] = {}
        self._lock = threading.Lock()

    # ---- team registration ----

    def register_team(self, team: TeamDefinition) -> None:
        with self._lock:
            self._teams[team.team_id] = team

    def unregister_team(self, team_id: str) -> bool:
        with self._lock:
            removed = self._teams.pop(team_id, None) is not None
            self._metrics.pop(team_id, None)
            return removed

    def get_team(self, team_id: str) -> TeamDefinition | None:
        with self._lock:
            return self._teams.get(team_id)

    def list_teams(self) -> list[TeamDefinition]:
        with self._lock:
            return list(self._teams.values())

    # ---- metrics ----

    def update_metrics(self, snapshot: TeamMetricsSnapshot) -> None:
        with self._lock:
            self._metrics[snapshot.team_id] = snapshot

    def get_metrics(self, team_id: str) -> TeamMetricsSnapshot | None:
        with self._lock:
            return self._metrics.get(team_id)

    # ---- org chart ----

    def org_chart(self, team_id: str) -> dict[str, Any]:
        """Return a simple tree of lead → members for the given team."""
        with self._lock:
            team = self._teams.get(team_id)
        if team is None:
            return {"error": "Team not found"}

        lead = team.lead_agent_id
        members = []
        for m in team.members:
            members.append({
                "role": m.role,
                "name": m.name,
                "is_lead": m.role == lead,
            })
        return {
            "team_id": team.team_id,
            "name": team.name,
            "lead": lead,
            "members": members,
        }

    # ---- summary ----

    def summary(self) -> dict[str, Any]:
        with self._lock:
            teams_data = []
            for tid, team in self._teams.items():
                entry: dict[str, Any] = {
                    "team_id": tid,
                    "name": team.name,
                    "size": team.size,
                }
                metrics = self._metrics.get(tid)
                if metrics is not None:
                    entry["metrics"] = metrics.to_dict()
                teams_data.append(entry)
            return {"teams": teams_data, "total_teams": len(self._teams)}

    def to_dict(self) -> dict[str, Any]:
        return self.summary()
