"""EnergyCostTracker — accumulates GPU energy usage and calculates electricity cost."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from amiagi.infrastructure.gpu_power_monitor import GpuPowerMonitor, GpuPowerSnapshot


@dataclass
class EnergyRecord:
    """A single inference energy measurement."""

    request_id: str
    model: str
    client_role: str
    start_time: float
    end_time: float
    power_before_w: float | None
    power_after_w: float | None
    energy_wh: float  # watt-hours consumed


@dataclass(frozen=True)
class EnergySummary:
    """Snapshot of accumulated energy & cost metrics."""

    total_energy_wh: float
    total_cost_local: float  # in user's currency
    price_per_kwh: float
    currency: str
    total_requests: int
    gpu_power_limit_w: float | None
    avg_power_draw_w: float | None
    total_inference_seconds: float


class EnergyCostTracker:
    """Thread-safe tracker of GPU energy consumption and electricity cost.

    Usage::

        tracker = EnergyCostTracker(gpu_monitor=GpuPowerMonitor())
        tracker.set_price_per_kwh(0.85)  # PLN per kWh

        # Around each inference call:
        snap_before = tracker.begin_request()
        ... LLM call ...
        tracker.end_request(request_id, model, role, snap_before, start_time)
    """

    def __init__(
        self,
        *,
        gpu_monitor: GpuPowerMonitor | None = None,
        price_per_kwh: float = 0.0,
        currency: str = "PLN",
    ) -> None:
        self._gpu = gpu_monitor or GpuPowerMonitor()
        self._price_per_kwh = price_per_kwh
        self._currency = currency
        self._lock = threading.Lock()
        self._total_energy_wh: float = 0.0
        self._total_inference_s: float = 0.0
        self._total_requests: int = 0
        self._power_sum_w: float = 0.0  # sum of avg power per request (for avg calc)
        self._power_limit_w: float | None = None
        self._records: list[EnergyRecord] = []

    # ---- configuration ----

    def set_price_per_kwh(self, price: float, currency: str = "") -> None:
        """Update the electricity price (per kWh)."""
        with self._lock:
            self._price_per_kwh = price
            if currency:
                self._currency = currency

    @property
    def price_per_kwh(self) -> float:
        return self._price_per_kwh

    @property
    def currency(self) -> str:
        return self._currency

    # ---- measurement API ----

    def begin_request(self) -> tuple[float, GpuPowerSnapshot]:
        """Call before LLM inference. Returns ``(start_time, power_snapshot)``."""
        snap = self._gpu.read()
        if snap.power_limit_w is not None:
            self._power_limit_w = snap.power_limit_w
        return time.monotonic(), snap

    def end_request(
        self,
        request_id: str,
        model: str,
        client_role: str,
        start_time: float,
        snap_before: GpuPowerSnapshot,
    ) -> EnergyRecord:
        """Call after LLM inference. Records the energy consumed."""
        end_time = time.monotonic()
        snap_after = self._gpu.read()
        duration_s = max(0.0, end_time - start_time)

        # Average power draw during this request
        powers = [
            p for p in (snap_before.power_draw_w, snap_after.power_draw_w)
            if p is not None
        ]
        if powers:
            avg_power_w = sum(powers) / len(powers)
        elif self._power_limit_w is not None:
            # Fallback: use TDP as upper bound
            avg_power_w = self._power_limit_w
        else:
            avg_power_w = 0.0

        energy_wh = (avg_power_w * duration_s) / 3600.0

        record = EnergyRecord(
            request_id=request_id,
            model=model,
            client_role=client_role,
            start_time=start_time,
            end_time=end_time,
            power_before_w=snap_before.power_draw_w,
            power_after_w=snap_after.power_draw_w,
            energy_wh=energy_wh,
        )

        with self._lock:
            self._total_energy_wh += energy_wh
            self._total_inference_s += duration_s
            self._total_requests += 1
            if avg_power_w > 0:
                self._power_sum_w += avg_power_w
            self._records.append(record)

        return record

    # ---- summaries ----

    def summary(self) -> EnergySummary:
        """Return a snapshot of accumulated energy metrics."""
        with self._lock:
            avg_power = (
                self._power_sum_w / self._total_requests
                if self._total_requests > 0
                else None
            )
            return EnergySummary(
                total_energy_wh=self._total_energy_wh,
                total_cost_local=self._total_energy_wh / 1000.0 * self._price_per_kwh,
                price_per_kwh=self._price_per_kwh,
                currency=self._currency,
                total_requests=self._total_requests,
                gpu_power_limit_w=self._power_limit_w,
                avg_power_draw_w=avg_power,
                total_inference_seconds=self._total_inference_s,
            )

    def summary_dict(self) -> dict[str, Any]:
        """Return summary as a plain dict (for JSON serialisation)."""
        s = self.summary()
        return {
            "total_energy_wh": round(s.total_energy_wh, 4),
            "total_cost_local": round(s.total_cost_local, 6),
            "price_per_kwh": s.price_per_kwh,
            "currency": s.currency,
            "total_requests": s.total_requests,
            "gpu_power_limit_w": s.gpu_power_limit_w,
            "avg_power_draw_w": round(s.avg_power_draw_w, 1) if s.avg_power_draw_w else None,
            "total_inference_seconds": round(s.total_inference_seconds, 2),
        }

    def recent_records(self, n: int = 10) -> list[EnergyRecord]:
        """Return the last *n* energy records."""
        with self._lock:
            return list(self._records[-n:])

    def reset(self) -> None:
        """Reset all counters."""
        with self._lock:
            self._total_energy_wh = 0.0
            self._total_inference_s = 0.0
            self._total_requests = 0
            self._power_sum_w = 0.0
            self._records.clear()
