from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from typing import Literal

from amiagi.infrastructure.vram_advisor import VramAdvisor

ModelRole = Literal["executor", "supervisor"]


@dataclass
class ModelQueuePolicy:
    supervisor_min_free_vram_mb: int = 3000
    queue_max_wait_seconds: float = 1.0
    _wait_queue: deque[ModelRole] = field(default_factory=deque)
    _recent_decisions: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=30))
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def _record_decision(self, decision: str, role: ModelRole, details: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision": decision,
            "role": role,
        }
        if details:
            payload.update(details)
        with self._lock:
            self._recent_decisions.append(payload)

    def acquire(self, role: ModelRole) -> bool:
        deadline = time.monotonic() + max(0.05, self.queue_max_wait_seconds)
        enqueued = False
        while time.monotonic() <= deadline:
            with self._lock:
                if not enqueued:
                    self._wait_queue.append(role)
                    enqueued = True
                if self._wait_queue and self._wait_queue[0] == role:
                    self._record_decision("acquire_ok", role, {"queue_len": len(self._wait_queue)})
                    return True
            time.sleep(0.01)

        if enqueued:
            with self._lock:
                try:
                    self._wait_queue.remove(role)
                except ValueError:
                    pass
        self._record_decision("acquire_timeout", role, {"queue_len": len(self._wait_queue)})
        return False

    def release(self, role: ModelRole) -> None:
        with self._lock:
            if self._wait_queue and self._wait_queue[0] == role:
                self._wait_queue.popleft()
                self._recent_decisions.append(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "decision": "release_head",
                        "role": role,
                        "queue_len": len(self._wait_queue),
                    }
                )
                return
            try:
                self._wait_queue.remove(role)
                self._recent_decisions.append(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "decision": "release_removed",
                        "role": role,
                        "queue_len": len(self._wait_queue),
                    }
                )
            except ValueError:
                self._recent_decisions.append(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "decision": "release_not_found",
                        "role": role,
                        "queue_len": len(self._wait_queue),
                    }
                )

    def can_run_for_vram(self, role: ModelRole, vram_advisor: VramAdvisor | None) -> tuple[bool, int | None]:
        if role != "supervisor" or vram_advisor is None:
            self._record_decision("vram_skip_check", role)
            return True, None
        profile = vram_advisor.detect()
        if profile.free_mb is None:
            self._record_decision("vram_unknown", role)
            return False, None
        allowed = profile.free_mb >= self.supervisor_min_free_vram_mb
        self._record_decision(
            "vram_allowed" if allowed else "vram_blocked",
            role,
            {
                "free_mb": profile.free_mb,
                "required_mb": self.supervisor_min_free_vram_mb,
            },
        )
        return allowed, profile.free_mb

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "queue_max_wait_seconds": self.queue_max_wait_seconds,
                "supervisor_min_free_vram_mb": self.supervisor_min_free_vram_mb,
                "queue_length": len(self._wait_queue),
                "queue": list(self._wait_queue),
                "recent_decisions": list(self._recent_decisions),
            }
