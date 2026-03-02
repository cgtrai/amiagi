"""VRAMScheduler — priority-based GPU/VRAM request scheduling for multi-agent Ollama."""

from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class VRAMRequest:
    """A queued VRAM-allocation request.

    Lower *priority* value = higher urgency.
    """

    priority: int
    agent_id: str = field(compare=False)
    model_name: str = field(compare=False)
    estimated_vram_mb: int = field(compare=False, default=0)
    enqueued_at: float = field(compare=False, default_factory=time.time)
    request_id: str = field(compare=False, default="")


class VRAMScheduler:
    """Schedules VRAM access across multiple Ollama agents.

    When multiple agents need the GPU simultaneously, requests are queued
    by task priority.  An *eviction policy* prefers removing idle agents
    first.

    Usage::

        scheduler = VRAMScheduler(total_vram_mb=8192)
        scheduler.request_slot("polluks", model="qwen3:14b", vram_mb=5000, priority=1)
        active = scheduler.active_slots()
    """

    def __init__(self, *, total_vram_mb: int = 0) -> None:
        self._total_vram_mb = total_vram_mb
        self._lock = threading.Lock()
        self._active: dict[str, VRAMRequest] = {}  # agent_id -> slot
        self._queue: list[VRAMRequest] = []  # min-heap
        self._used_vram_mb: int = 0
        self._idle_since: dict[str, float] = {}  # agent_id -> monotonic timestamp
        self._request_counter = 0

    @property
    def total_vram_mb(self) -> int:
        return self._total_vram_mb

    @total_vram_mb.setter
    def total_vram_mb(self, value: int) -> None:
        self._total_vram_mb = max(0, value)

    @property
    def used_vram_mb(self) -> int:
        return self._used_vram_mb

    @property
    def free_vram_mb(self) -> int:
        if self._total_vram_mb <= 0:
            return 0
        return max(0, self._total_vram_mb - self._used_vram_mb)

    # ---- public API ----

    def request_slot(
        self,
        agent_id: str,
        *,
        model: str = "",
        vram_mb: int = 0,
        priority: int = 10,
    ) -> bool:
        """Request a VRAM slot.  Returns ``True`` if immediately granted."""
        with self._lock:
            # Already active?
            if agent_id in self._active:
                self._idle_since.pop(agent_id, None)
                return True

            req = VRAMRequest(
                priority=priority,
                agent_id=agent_id,
                model_name=model,
                estimated_vram_mb=vram_mb,
                request_id=f"vr-{self._request_counter}",
            )
            self._request_counter += 1

            if self._can_fit(vram_mb):
                self._grant(req)
                return True

            # Try eviction
            if self._try_evict(vram_mb, priority):
                self._grant(req)
                return True

            # Queue for later
            heapq.heappush(self._queue, req)
            return False

    def release_slot(self, agent_id: str) -> bool:
        """Release the VRAM slot for *agent_id*."""
        with self._lock:
            slot = self._active.pop(agent_id, None)
            if slot is None:
                return False
            self._used_vram_mb -= slot.estimated_vram_mb
            self._idle_since.pop(agent_id, None)
            # Try granting queued requests
            self._process_queue()
            return True

    def mark_idle(self, agent_id: str) -> None:
        """Mark an agent as idle (candidate for eviction)."""
        with self._lock:
            if agent_id in self._active:
                self._idle_since[agent_id] = time.monotonic()

    def mark_active(self, agent_id: str) -> None:
        """Mark an agent as actively using its slot."""
        with self._lock:
            self._idle_since.pop(agent_id, None)

    def active_slots(self) -> dict[str, VRAMRequest]:
        return dict(self._active)

    def queue_depth(self) -> int:
        return len(self._queue)

    def status(self) -> dict[str, Any]:
        return {
            "total_vram_mb": self._total_vram_mb,
            "used_vram_mb": self._used_vram_mb,
            "free_vram_mb": self.free_vram_mb,
            "active_agents": list(self._active.keys()),
            "queued": len(self._queue),
            "idle_agents": list(self._idle_since.keys()),
        }

    # ---- internals ----

    def _can_fit(self, vram_mb: int) -> bool:
        if self._total_vram_mb <= 0:
            return True  # unlimited / unknown
        return (self._used_vram_mb + vram_mb) <= self._total_vram_mb

    def _grant(self, req: VRAMRequest) -> None:
        """Must be called with lock held."""
        self._active[req.agent_id] = req
        self._used_vram_mb += req.estimated_vram_mb
        self._idle_since.pop(req.agent_id, None)

    def _try_evict(self, needed_mb: int, requester_priority: int) -> bool:
        """Evict idle agents (longest idle first) to free *needed_mb*.

        Only evicts agents with lower priority (higher numeric value)
        than the requester.

        Must be called with lock held.
        """
        if not self._idle_since:
            return False

        # Sort idle agents: longest idle first
        idle_sorted = sorted(
            self._idle_since.items(),
            key=lambda item: item[1],  # oldest idle first
        )

        freed = 0
        to_evict: list[str] = []
        for aid, _since in idle_sorted:
            slot = self._active.get(aid)
            if slot is None:
                continue
            if slot.priority > requester_priority:
                to_evict.append(aid)
                freed += slot.estimated_vram_mb
                if self._can_fit(needed_mb - freed + self._used_vram_mb):
                    break

        if not self._can_fit_with_freed(needed_mb, freed):
            return False

        for aid in to_evict:
            slot = self._active.pop(aid, None)
            if slot:
                self._used_vram_mb -= slot.estimated_vram_mb
            self._idle_since.pop(aid, None)

        return True

    def _can_fit_with_freed(self, needed_mb: int, freed_mb: int) -> bool:
        if self._total_vram_mb <= 0:
            return True
        return (self._used_vram_mb - freed_mb + needed_mb) <= self._total_vram_mb

    def _process_queue(self) -> None:
        """Try granting queued requests. Must be called with lock held."""
        still_queued: list[VRAMRequest] = []
        while self._queue:
            req = heapq.heappop(self._queue)
            if self._can_fit(req.estimated_vram_mb):
                self._grant(req)
            else:
                still_queued.append(req)
                break
        # Restore remaining
        for req in still_queued:
            heapq.heappush(self._queue, req)
        while self._queue:
            # drain remainder
            break
