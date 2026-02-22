from __future__ import annotations

from dataclasses import dataclass

from amiagi.application.model_queue_policy import ModelQueuePolicy


@dataclass
class FakeProfile:
    free_mb: int


class FakeVramAdvisor:
    def __init__(self, free_mb: int) -> None:
        self._free_mb = free_mb

    def detect(self) -> FakeProfile:
        return FakeProfile(free_mb=self._free_mb)


def test_queue_policy_allows_executor_without_vram_gate() -> None:
    policy = ModelQueuePolicy(supervisor_min_free_vram_mb=3000)

    ok, free_mb = policy.can_run_for_vram("executor", FakeVramAdvisor(100))

    assert ok is True
    assert free_mb is None


def test_queue_policy_blocks_supervisor_on_low_vram() -> None:
    policy = ModelQueuePolicy(supervisor_min_free_vram_mb=3000)

    ok, free_mb = policy.can_run_for_vram("supervisor", FakeVramAdvisor(1200))

    assert ok is False
    assert free_mb == 1200


def test_queue_policy_acquire_release_roundtrip() -> None:
    policy = ModelQueuePolicy(queue_max_wait_seconds=0.2)

    assert policy.acquire("executor") is True
    policy.release("executor")
    assert policy.acquire("supervisor") is True
    policy.release("supervisor")


def test_queue_policy_snapshot_contains_recent_decisions() -> None:
    policy = ModelQueuePolicy(queue_max_wait_seconds=0.2)

    assert policy.acquire("executor") is True
    policy.release("executor")
    snapshot = policy.snapshot()

    assert snapshot["queue_length"] == 0
    assert isinstance(snapshot["recent_decisions"], list)
    assert len(snapshot["recent_decisions"]) >= 2
