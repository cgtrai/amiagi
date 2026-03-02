"""Tests for VRAMScheduler."""

from __future__ import annotations

from amiagi.infrastructure.vram_scheduler import VRAMScheduler


class TestVRAMScheduler:
    def test_grant_when_unlimited(self) -> None:
        s = VRAMScheduler(total_vram_mb=0)
        assert s.request_slot("a", vram_mb=5000) is True
        assert "a" in s.active_slots()

    def test_grant_fits(self) -> None:
        s = VRAMScheduler(total_vram_mb=8000)
        assert s.request_slot("a", vram_mb=4000) is True
        assert s.used_vram_mb == 4000

    def test_reject_no_room(self) -> None:
        s = VRAMScheduler(total_vram_mb=4000)
        s.request_slot("a", vram_mb=3000)
        result = s.request_slot("b", vram_mb=2000)
        assert result is False
        assert s.queue_depth() == 1

    def test_release_frees_vram(self) -> None:
        s = VRAMScheduler(total_vram_mb=4000)
        s.request_slot("a", vram_mb=3000)
        s.release_slot("a")
        assert s.used_vram_mb == 0
        assert "a" not in s.active_slots()

    def test_release_processes_queue(self) -> None:
        s = VRAMScheduler(total_vram_mb=4000)
        s.request_slot("a", vram_mb=3000)
        s.request_slot("b", vram_mb=3000)  # queued
        assert s.queue_depth() >= 1
        s.release_slot("a")
        assert "b" in s.active_slots()

    def test_eviction_idle_agent(self) -> None:
        s = VRAMScheduler(total_vram_mb=5000)
        s.request_slot("low", vram_mb=4000, priority=20)
        s.mark_idle("low")
        result = s.request_slot("high", vram_mb=4000, priority=1)
        assert result is True
        assert "high" in s.active_slots()
        assert "low" not in s.active_slots()

    def test_no_eviction_if_not_idle(self) -> None:
        s = VRAMScheduler(total_vram_mb=5000)
        s.request_slot("active_agent", vram_mb=4000, priority=20)
        # Not marked idle
        result = s.request_slot("new", vram_mb=4000, priority=1)
        assert result is False

    def test_already_active_agent_returns_true(self) -> None:
        s = VRAMScheduler(total_vram_mb=8000)
        s.request_slot("a", vram_mb=4000)
        assert s.request_slot("a", vram_mb=4000) is True

    def test_mark_idle_and_active(self) -> None:
        s = VRAMScheduler(total_vram_mb=8000)
        s.request_slot("a", vram_mb=4000)
        s.mark_idle("a")
        status = s.status()
        assert "a" in status["idle_agents"]
        s.mark_active("a")
        status = s.status()
        assert "a" not in status["idle_agents"]

    def test_free_vram(self) -> None:
        s = VRAMScheduler(total_vram_mb=8000)
        s.request_slot("a", vram_mb=3000)
        assert s.free_vram_mb == 5000

    def test_status(self) -> None:
        s = VRAMScheduler(total_vram_mb=8000)
        s.request_slot("a", vram_mb=2000)
        st = s.status()
        assert st["total_vram_mb"] == 8000
        assert st["used_vram_mb"] == 2000
        assert "a" in st["active_agents"]

    def test_release_nonexistent_returns_false(self) -> None:
        s = VRAMScheduler()
        assert s.release_slot("ghost") is False
