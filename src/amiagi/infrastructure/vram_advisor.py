from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class VramProfile:
    free_mb: int | None
    total_mb: int | None
    suggested_num_ctx: int


class VramAdvisor:
    def detect(self) -> VramProfile:
        free_mb, total_mb = _query_nvidia_smi()
        suggested_num_ctx = _suggest_num_ctx(free_mb)
        return VramProfile(
            free_mb=free_mb,
            total_mb=total_mb,
            suggested_num_ctx=suggested_num_ctx,
        )


def _query_nvidia_smi() -> tuple[int | None, int | None]:
    command = [
        "nvidia-smi",
        "--query-gpu=memory.free,memory.total",
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

    free_values: list[int] = []
    total_values: list[int] = []
    for line in lines:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            continue
        try:
            free_values.append(int(parts[0]))
            total_values.append(int(parts[1]))
        except ValueError:
            continue

    if not free_values or not total_values:
        return None, None

    return min(free_values), max(total_values)


def _suggest_num_ctx(free_mb: int | None) -> int:
    if free_mb is None:
        return 4096
    if free_mb < 2048:
        return 1024
    if free_mb < 4096:
        return 2048
    if free_mb < 8192:
        return 3072
    return 4096
