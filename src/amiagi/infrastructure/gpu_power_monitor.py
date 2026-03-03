"""GpuPowerMonitor — read real-time GPU power draw via nvidia-smi."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class GpuPowerSnapshot:
    """A single GPU power reading."""

    power_draw_w: float | None  # current draw in watts
    power_limit_w: float | None  # TDP / power limit in watts
    gpu_index: int = 0


class GpuPowerMonitor:
    """Lightweight wrapper around ``nvidia-smi`` for power telemetry.

    Every call to :meth:`read` spawns a short-lived ``nvidia-smi`` process.
    Typical latency: 5–15 ms.  If ``nvidia-smi`` is unavailable (no NVIDIA
    GPU or driver), :meth:`read` returns ``None`` values gracefully.
    """

    def read(self, gpu_index: int = 0) -> GpuPowerSnapshot:
        """Return the current power draw and power limit (watts)."""
        draw, limit = _query_power()
        return GpuPowerSnapshot(
            power_draw_w=draw,
            power_limit_w=limit,
            gpu_index=gpu_index,
        )


def _query_power() -> tuple[float | None, float | None]:
    """Query ``nvidia-smi`` for *power.draw* and *power.limit*.

    Returns ``(draw_watts, limit_watts)`` or ``(None, None)`` on failure.
    """
    command = [
        "nvidia-smi",
        "--query-gpu=power.draw,power.limit",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return None, None

    if completed.returncode != 0:
        return None, None

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return None, None

    # Use first GPU
    parts = [p.strip() for p in lines[0].split(",")]
    if len(parts) < 2:
        return None, None
    try:
        draw = float(parts[0])
        limit = float(parts[1])
        return draw, limit
    except (ValueError, TypeError):
        return None, None
