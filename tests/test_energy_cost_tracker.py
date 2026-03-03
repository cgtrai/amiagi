"""Tests for GpuPowerMonitor and EnergyCostTracker."""

from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest

from amiagi.infrastructure.gpu_power_monitor import (
    GpuPowerMonitor,
    GpuPowerSnapshot,
    _query_power,
)
from amiagi.infrastructure.energy_cost_tracker import (
    EnergyCostTracker,
    EnergySummary,
)


# ── GpuPowerMonitor ──────────────────────────────────────────

class TestQueryPower:
    def test_parses_valid_output(self) -> None:
        mock_result = MagicMock(returncode=0, stdout="120.45, 300.00\n")
        with patch("amiagi.infrastructure.gpu_power_monitor.subprocess.run", return_value=mock_result):
            draw, limit = _query_power()
        assert draw == pytest.approx(120.45)
        assert limit == pytest.approx(300.0)

    def test_returns_none_on_failure(self) -> None:
        mock_result = MagicMock(returncode=1, stdout="")
        with patch("amiagi.infrastructure.gpu_power_monitor.subprocess.run", return_value=mock_result):
            draw, limit = _query_power()
        assert draw is None
        assert limit is None

    def test_returns_none_on_exception(self) -> None:
        with patch("amiagi.infrastructure.gpu_power_monitor.subprocess.run", side_effect=FileNotFoundError):
            draw, limit = _query_power()
        assert draw is None
        assert limit is None

    def test_returns_none_on_empty(self) -> None:
        mock_result = MagicMock(returncode=0, stdout="\n")
        with patch("amiagi.infrastructure.gpu_power_monitor.subprocess.run", return_value=mock_result):
            draw, limit = _query_power()
        assert draw is None
        assert limit is None

    def test_returns_none_on_malformed(self) -> None:
        mock_result = MagicMock(returncode=0, stdout="abc, def\n")
        with patch("amiagi.infrastructure.gpu_power_monitor.subprocess.run", return_value=mock_result):
            draw, limit = _query_power()
        assert draw is None
        assert limit is None


class TestGpuPowerMonitor:
    def test_read_returns_snapshot(self) -> None:
        mock_result = MagicMock(returncode=0, stdout="85.12, 250.00\n")
        with patch("amiagi.infrastructure.gpu_power_monitor.subprocess.run", return_value=mock_result):
            snap = GpuPowerMonitor().read()
        assert isinstance(snap, GpuPowerSnapshot)
        assert snap.power_draw_w == pytest.approx(85.12)
        assert snap.power_limit_w == pytest.approx(250.0)
        assert snap.gpu_index == 0


# ── EnergyCostTracker ─────────────────────────────────────────

def _mock_monitor(draw: float = 100.0, limit: float = 300.0) -> GpuPowerMonitor:
    """Return a GpuPowerMonitor that always returns fixed readings."""
    m = GpuPowerMonitor()
    m.read = lambda gpu_index=0: GpuPowerSnapshot(  # type: ignore[assignment]
        power_draw_w=draw, power_limit_w=limit
    )
    return m


class TestEnergyCostTracker:
    def test_initial_summary_is_zero(self) -> None:
        tracker = EnergyCostTracker(gpu_monitor=_mock_monitor())
        s = tracker.summary()
        assert s.total_energy_wh == 0.0
        assert s.total_requests == 0

    def test_set_price(self) -> None:
        tracker = EnergyCostTracker()
        tracker.set_price_per_kwh(0.85, "PLN")
        assert tracker.price_per_kwh == pytest.approx(0.85)
        assert tracker.currency == "PLN"

    def test_begin_end_request_records_energy(self) -> None:
        tracker = EnergyCostTracker(
            gpu_monitor=_mock_monitor(draw=150.0, limit=300.0),
            price_per_kwh=1.0,
            currency="PLN",
        )
        start, snap = tracker.begin_request()
        # Simulate ~1 second of inference
        time.sleep(0.05)
        record = tracker.end_request("req-1", "qwen:14b", "executor", start, snap)

        assert record.request_id == "req-1"
        assert record.model == "qwen:14b"
        assert record.energy_wh > 0
        assert record.power_before_w == pytest.approx(150.0)
        assert record.power_after_w == pytest.approx(150.0)

        s = tracker.summary()
        assert s.total_requests == 1
        assert s.total_energy_wh > 0
        assert s.total_cost_local > 0  # price=1.0/kWh, so cost > 0
        assert s.avg_power_draw_w == pytest.approx(150.0)
        assert s.gpu_power_limit_w == pytest.approx(300.0)

    def test_multiple_requests_accumulate(self) -> None:
        tracker = EnergyCostTracker(
            gpu_monitor=_mock_monitor(draw=100.0, limit=250.0),
            price_per_kwh=0.50,
        )
        for i in range(3):
            start, snap = tracker.begin_request()
            time.sleep(0.02)
            tracker.end_request(f"req-{i}", "test-model", "executor", start, snap)

        s = tracker.summary()
        assert s.total_requests == 3
        assert s.total_energy_wh > 0
        assert s.total_inference_seconds > 0

    def test_summary_dict_serialisable(self) -> None:
        import json
        tracker = EnergyCostTracker(
            gpu_monitor=_mock_monitor(),
            price_per_kwh=0.85,
        )
        start, snap = tracker.begin_request()
        time.sleep(0.01)
        tracker.end_request("req-x", "model", "executor", start, snap)

        d = tracker.summary_dict()
        # Must be JSON-serialisable without errors
        text = json.dumps(d)
        parsed = json.loads(text)
        assert parsed["total_requests"] == 1
        assert parsed["currency"] == "PLN"

    def test_reset_clears_counters(self) -> None:
        tracker = EnergyCostTracker(gpu_monitor=_mock_monitor())
        start, snap = tracker.begin_request()
        time.sleep(0.01)
        tracker.end_request("req-r", "m", "executor", start, snap)
        assert tracker.summary().total_requests == 1
        tracker.reset()
        assert tracker.summary().total_requests == 0
        assert tracker.summary().total_energy_wh == 0.0

    def test_none_gpu_readings_uses_tdp_fallback(self) -> None:
        """When nvidia-smi is unavailable, power_draw is None but TDP may be cached."""
        monitor = GpuPowerMonitor()
        # First read returns data (caches TDP), second returns None
        call_count = [0]
        def _mock_read(gpu_index: int = 0) -> GpuPowerSnapshot:
            call_count[0] += 1
            if call_count[0] <= 1:
                return GpuPowerSnapshot(power_draw_w=100.0, power_limit_w=300.0)
            return GpuPowerSnapshot(power_draw_w=None, power_limit_w=None)

        monitor.read = _mock_read  # type: ignore[assignment]
        tracker = EnergyCostTracker(gpu_monitor=monitor, price_per_kwh=1.0)

        # First request — caches TDP=300
        start1, snap1 = tracker.begin_request()
        time.sleep(0.01)
        tracker.end_request("r1", "m", "executor", start1, snap1)

        # Second request — nvidia-smi unavailable, should use TDP as fallback
        start2, snap2 = tracker.begin_request()
        time.sleep(0.01)
        rec2 = tracker.end_request("r2", "m", "executor", start2, snap2)

        # energy_wh should be > 0 even without direct power readings
        assert rec2.energy_wh > 0

    def test_recent_records(self) -> None:
        tracker = EnergyCostTracker(gpu_monitor=_mock_monitor())
        for i in range(5):
            start, snap = tracker.begin_request()
            tracker.end_request(f"req-{i}", "m", "executor", start, snap)

        recent = tracker.recent_records(3)
        assert len(recent) == 3
        assert recent[0].request_id == "req-2"
        assert recent[2].request_id == "req-4"

    def test_energy_calculation_accuracy(self) -> None:
        """100W for 36 seconds = 1 Wh."""
        tracker = EnergyCostTracker(
            gpu_monitor=_mock_monitor(draw=100.0, limit=100.0),
            price_per_kwh=1.0,
        )
        # Manually create a record to test exact calculation
        from amiagi.infrastructure.gpu_power_monitor import GpuPowerSnapshot as Snap
        snap = Snap(power_draw_w=100.0, power_limit_w=100.0)
        start = time.monotonic()
        # Monkey-patch time.monotonic for controlled duration
        original = time.monotonic
        time.monotonic = lambda: start + 36.0  # type: ignore[assignment]
        try:
            record = tracker.end_request("acc", "m", "executor", start, snap)
        finally:
            time.monotonic = original  # type: ignore[assignment]

        # 100W × 36s / 3600 = 1.0 Wh
        assert record.energy_wh == pytest.approx(1.0, abs=0.01)
