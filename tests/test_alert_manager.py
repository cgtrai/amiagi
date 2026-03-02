"""Tests for AlertManager — rules, evaluation, cooldown, listeners."""

from __future__ import annotations

import time

from amiagi.application.alert_manager import Alert, AlertManager, AlertRule, AlertSeverity


class TestAlertManager:
    def test_add_and_evaluate_rule(self) -> None:
        mgr = AlertManager()
        mgr.add_rule(AlertRule(
            name="always_fire",
            check_fn=lambda: "alert triggered",
            severity=AlertSeverity.WARNING,
            cooldown_seconds=0,
        ))
        fired: list[Alert] = []
        mgr.add_listener(fired.append)
        mgr.evaluate()
        assert len(fired) == 1
        assert fired[0].rule_name == "always_fire"
        assert fired[0].message == "alert triggered"
        assert fired[0].severity == AlertSeverity.WARNING

    def test_check_fn_returns_none_no_alert(self) -> None:
        mgr = AlertManager()
        mgr.add_rule(AlertRule(
            name="never_fire",
            check_fn=lambda: None,
            cooldown_seconds=0,
        ))
        fired: list[Alert] = []
        mgr.add_listener(fired.append)
        mgr.evaluate()
        assert not fired

    def test_cooldown_prevents_rapid_fire(self) -> None:
        mgr = AlertManager()
        mgr.add_rule(AlertRule(
            name="cooldown",
            check_fn=lambda: "fire!",
            cooldown_seconds=60,  # 60s cooldown
        ))
        fired: list[Alert] = []
        mgr.add_listener(fired.append)
        mgr.evaluate()
        assert len(fired) == 1
        mgr.evaluate()  # should be suppressed by cooldown
        assert len(fired) == 1

    def test_multiple_listeners(self) -> None:
        mgr = AlertManager()
        mgr.add_rule(AlertRule(
            name="multi",
            check_fn=lambda: "msg",
            cooldown_seconds=0,
        ))
        a: list[Alert] = []
        b: list[Alert] = []
        mgr.add_listener(a.append)
        mgr.add_listener(b.append)
        mgr.evaluate()
        assert len(a) == 1
        assert len(b) == 1

    def test_remove_rule(self) -> None:
        mgr = AlertManager()
        mgr.add_rule(AlertRule(name="removeme", check_fn=lambda: "x", cooldown_seconds=0))
        mgr.remove_rule("removeme")
        fired: list[Alert] = []
        mgr.add_listener(fired.append)
        mgr.evaluate()
        assert not fired

    def test_history(self) -> None:
        mgr = AlertManager()
        mgr.add_rule(AlertRule(name="h", check_fn=lambda: "hist", cooldown_seconds=0))
        mgr.evaluate()
        history = mgr.recent_alerts()
        assert len(history) == 1
        assert history[0].message == "hist"

    def test_alert_severity_values(self) -> None:
        assert AlertSeverity.INFO.value == "info"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.CRITICAL.value == "critical"

    def test_disabled_rule(self) -> None:
        mgr = AlertManager()
        mgr.add_rule(AlertRule(
            name="disabled",
            check_fn=lambda: "should not fire",
            enabled=False,
            cooldown_seconds=0,
        ))
        fired: list[Alert] = []
        mgr.add_listener(fired.append)
        mgr.evaluate()
        assert not fired

    def test_start_stop(self) -> None:
        mgr = AlertManager()
        mgr.add_rule(AlertRule(name="bg", check_fn=lambda: None, cooldown_seconds=0))
        mgr.start(interval_seconds=0.1)
        assert mgr.running
        time.sleep(0.3)
        mgr.stop()
        assert not mgr.running

    def test_register_cost_alerts_adds_two_rules(self) -> None:
        from amiagi.application.budget_manager import BudgetManager

        mgr = AlertManager()
        bm = BudgetManager()
        mgr.register_cost_alerts(bm)
        rule_names = [r.name for r in mgr._rules]
        assert "budget_warning_80pct" in rule_names
        assert "budget_exhausted_100pct" in rule_names

    def test_cost_alert_warning_fires_at_80pct(self) -> None:
        from amiagi.application.budget_manager import BudgetManager

        bm = BudgetManager()
        bm.set_budget("agent1", 10.0)
        bm.record_usage("agent1", cost_usd=8.5)  # 85%

        mgr = AlertManager()
        mgr.register_cost_alerts(bm)
        fired: list[Alert] = []
        mgr.add_listener(fired.append)
        mgr.evaluate()

        warning_alerts = [a for a in fired if a.rule_name == "budget_warning_80pct"]
        assert len(warning_alerts) == 1
        assert "agent1" in warning_alerts[0].message
        assert warning_alerts[0].severity == AlertSeverity.WARNING

    def test_cost_alert_exhausted_fires_at_100pct(self) -> None:
        from amiagi.application.budget_manager import BudgetManager

        bm = BudgetManager()
        bm.set_budget("agent1", 10.0)
        bm.record_usage("agent1", cost_usd=10.5)  # 105%

        mgr = AlertManager()
        mgr.register_cost_alerts(bm)
        fired: list[Alert] = []
        mgr.add_listener(fired.append)
        mgr.evaluate()

        critical_alerts = [a for a in fired if a.rule_name == "budget_exhausted_100pct"]
        assert len(critical_alerts) == 1
        assert critical_alerts[0].severity == AlertSeverity.CRITICAL

    def test_cost_alert_no_fire_below_80pct(self) -> None:
        from amiagi.application.budget_manager import BudgetManager

        bm = BudgetManager()
        bm.set_budget("agent1", 10.0)
        bm.record_usage("agent1", cost_usd=5.0)  # 50%

        mgr = AlertManager()
        mgr.register_cost_alerts(bm)
        fired: list[Alert] = []
        mgr.add_listener(fired.append)
        mgr.evaluate()
        assert len(fired) == 0
