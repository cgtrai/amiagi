"""Tests for DynamicScaler (Phase 11)."""

from __future__ import annotations

from amiagi.application.dynamic_scaler import DynamicScaler, ScaleEvent


class TestScaleEvent:
    def test_to_dict(self) -> None:
        e = ScaleEvent(direction="up", agent_role="dev", team_id="t1")
        d = e.to_dict()
        assert d["direction"] == "up"
        assert d["team_id"] == "t1"


class TestDynamicScaler:
    def test_scale_up(self) -> None:
        s = DynamicScaler(scale_up_threshold=3, cooldown_seconds=0)
        event = s.evaluate(pending_tasks=5, active_agents=2, team_id="t")
        assert event is not None
        assert event.direction == "up"

    def test_scale_down(self) -> None:
        s = DynamicScaler(scale_down_threshold=1, cooldown_seconds=0)
        event = s.evaluate(pending_tasks=0, active_agents=3, team_id="t")
        assert event is not None
        assert event.direction == "down"

    def test_no_scale_needed(self) -> None:
        s = DynamicScaler(scale_up_threshold=10, scale_down_threshold=0, cooldown_seconds=0)
        event = s.evaluate(pending_tasks=5, active_agents=2)
        assert event is None

    def test_no_scale_down_single_agent(self) -> None:
        s = DynamicScaler(scale_down_threshold=5, cooldown_seconds=0)
        event = s.evaluate(pending_tasks=0, active_agents=1)
        assert event is None

    def test_cooldown_blocks_rapid_scale(self) -> None:
        s = DynamicScaler(scale_up_threshold=3, cooldown_seconds=9999)
        e1 = s.evaluate(pending_tasks=5, active_agents=2)
        assert e1 is not None
        e2 = s.evaluate(pending_tasks=5, active_agents=2)
        assert e2 is None  # cooldown active

    def test_history(self) -> None:
        s = DynamicScaler(scale_up_threshold=2, cooldown_seconds=0)
        s.evaluate(pending_tasks=5, active_agents=1)
        h = s.history()
        assert len(h) == 1

    def test_clear_history(self) -> None:
        s = DynamicScaler(scale_up_threshold=2, cooldown_seconds=0)
        s.evaluate(pending_tasks=5, active_agents=1)
        s.clear_history()
        assert len(s.history()) == 0

    def test_threshold_setters(self) -> None:
        s = DynamicScaler()
        s.scale_up_threshold = 10
        assert s.scale_up_threshold == 10
        s.scale_down_threshold = -1
        assert s.scale_down_threshold == 0  # clamped

    def test_to_dict(self) -> None:
        s = DynamicScaler()
        d = s.to_dict()
        assert "scale_up_threshold" in d
        assert d["events_count"] == 0
