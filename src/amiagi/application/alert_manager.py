"""AlertManager — rule-based alerting on system conditions."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AlertRule:
    """A single alerting rule.

    *check_fn* is called periodically; when it returns a truthy string
    (the alert message), the alert is fired.
    """

    name: str
    check_fn: Callable[[], str | None]
    severity: AlertSeverity = AlertSeverity.WARNING
    cooldown_seconds: float = 60.0
    enabled: bool = True


@dataclass(frozen=True)
class Alert:
    """A fired alert."""

    rule_name: str
    message: str
    severity: AlertSeverity
    timestamp: float = field(default_factory=time.time)


class AlertManager:
    """Evaluates alert rules and dispatches notifications.

    Supports pluggable listeners: ``on_alert(alert)`` callbacks.

    Usage::

        mgr = AlertManager()
        mgr.add_rule(AlertRule(
            name="agent_unresponsive",
            check_fn=lambda: "Agent polluks unresponsive" if agent_is_stuck() else None,
            severity=AlertSeverity.CRITICAL,
        ))
        mgr.add_listener(lambda alert: print(f"ALERT: {alert.message}"))
        mgr.evaluate()  # or start() for background loop
    """

    def __init__(self) -> None:
        self._rules: list[AlertRule] = []
        self._listeners: list[Callable[[Alert], None]] = []
        self._history: list[Alert] = []
        self._last_fired: dict[str, float] = {}  # rule_name → timestamp
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ---- configuration ----

    def add_rule(self, rule: AlertRule) -> None:
        with self._lock:
            self._rules.append(rule)

    def remove_rule(self, name: str) -> None:
        with self._lock:
            self._rules = [r for r in self._rules if r.name != name]

    def add_listener(self, listener: Callable[[Alert], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    # ---- evaluation ----

    def evaluate(self) -> list[Alert]:
        """Check all rules and fire alerts. Returns list of new alerts."""
        now = time.time()
        new_alerts: list[Alert] = []

        with self._lock:
            rules_snapshot = list(self._rules)

        for rule in rules_snapshot:
            if not rule.enabled:
                continue
            # Cooldown check
            last = self._last_fired.get(rule.name, 0.0)
            if now - last < rule.cooldown_seconds:
                continue

            try:
                message = rule.check_fn()
            except Exception:
                continue

            if message:
                alert = Alert(
                    rule_name=rule.name,
                    message=message,
                    severity=rule.severity,
                    timestamp=now,
                )
                with self._lock:
                    self._history.append(alert)
                    self._last_fired[rule.name] = now
                    listeners = list(self._listeners)
                new_alerts.append(alert)
                for listener in listeners:
                    try:
                        listener(alert)
                    except Exception:
                        pass

        return new_alerts

    # ---- history ----

    def recent_alerts(self, last_n: int = 50) -> list[Alert]:
        with self._lock:
            return list(self._history[-last_n:])

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()

    # ---- background loop ----

    def start(self, interval_seconds: float = 30.0) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            args=(interval_seconds,),
            name="alert-manager",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._running

    def _loop(self, interval: float) -> None:
        while self._running:
            try:
                self.evaluate()
            except Exception:
                pass
            self._stop_event.wait(timeout=interval)
