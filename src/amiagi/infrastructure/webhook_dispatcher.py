"""Phase 10 — Webhook dispatcher (infrastructure).

Sends event-driven HTTP POST notifications to registered webhook URLs
with configurable retry and exponential backoff.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


@dataclass
class WebhookTarget:
    url: str
    events: list[str] = field(default_factory=list)  # empty → all events
    secret: str = ""
    max_retries: int = 3
    backoff_seconds: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "events": self.events,
            "secret": self.secret,
            "max_retries": self.max_retries,
            "backoff_seconds": self.backoff_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WebhookTarget":
        return cls(
            url=d["url"],
            events=d.get("events", []),
            secret=d.get("secret", ""),
            max_retries=d.get("max_retries", 3),
            backoff_seconds=d.get("backoff_seconds", 1.0),
        )


@dataclass
class DeliveryResult:
    url: str
    event: str
    success: bool
    status_code: int = 0
    attempts: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "event": self.event,
            "success": self.success,
            "status_code": self.status_code,
            "attempts": self.attempts,
            "error": self.error,
        }


class WebhookDispatcher:
    """Dispatches event payloads to registered webhook targets."""

    def __init__(self) -> None:
        self._targets: list[WebhookTarget] = []
        self._history: list[DeliveryResult] = []
        self._lock = threading.Lock()

    # ---- target management ----

    def register(self, target: WebhookTarget) -> None:
        with self._lock:
            self._targets.append(target)

    def unregister(self, url: str) -> bool:
        with self._lock:
            before = len(self._targets)
            self._targets = [t for t in self._targets if t.url != url]
            return len(self._targets) < before

    def list_targets(self) -> list[WebhookTarget]:
        with self._lock:
            return list(self._targets)

    # ---- dispatch ----

    def dispatch(self, event: str, payload: dict[str, Any]) -> list[DeliveryResult]:
        """Send *event* to all matching targets (synchronously).

        Returns a list of :class:`DeliveryResult` for each target.
        """
        targets = self._matching_targets(event)
        results: list[DeliveryResult] = []
        for target in targets:
            result = self._deliver(target, event, payload)
            results.append(result)
            with self._lock:
                self._history.append(result)
        return results

    def dispatch_async(self, event: str, payload: dict[str, Any]) -> None:
        """Fire-and-forget webhook delivery in a background thread."""
        threading.Thread(
            target=self.dispatch,
            args=(event, payload),
            daemon=True,
        ).start()

    # ---- history ----

    def history(self, limit: int = 50) -> list[DeliveryResult]:
        with self._lock:
            return list(reversed(self._history[-limit:]))

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()

    # ---- internals ----

    def _matching_targets(self, event: str) -> list[WebhookTarget]:
        with self._lock:
            return [
                t for t in self._targets
                if not t.events or event in t.events
            ]

    def _deliver(
        self,
        target: WebhookTarget,
        event: str,
        payload: dict[str, Any],
    ) -> DeliveryResult:
        body = json.dumps({"event": event, "payload": payload}, ensure_ascii=False).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if target.secret:
            headers["X-Webhook-Secret"] = target.secret

        last_error = ""
        for attempt in range(1, target.max_retries + 1):
            try:
                req = urllib.request.Request(
                    target.url,
                    data=body,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    status = resp.status
                if 200 <= status < 300:
                    return DeliveryResult(
                        url=target.url,
                        event=event,
                        success=True,
                        status_code=status,
                        attempts=attempt,
                    )
                last_error = f"HTTP {status}"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)

            if attempt < target.max_retries:
                time.sleep(target.backoff_seconds * (2 ** (attempt - 1)))

        return DeliveryResult(
            url=target.url,
            event=event,
            success=False,
            attempts=target.max_retries,
            error=last_error,
        )

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "targets": [t.to_dict() for t in self._targets],
                "history_count": len(self._history),
            }
