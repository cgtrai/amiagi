"""Health check routes — GET /health, GET /health/detailed."""

from __future__ import annotations

import os
import time

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from amiagi import __version__


async def health(request: Request) -> JSONResponse:
    """Return application health status and version."""
    return JSONResponse({
        "status": "ok",
        "version": __version__,
    })


async def health_detailed(request: Request) -> JSONResponse:
    """Return extended diagnostics: RAM, CPU, DB pool, Ollama, uptime."""
    checks: dict = {"status": "ok", "version": __version__}

    # ── Uptime ──
    startup_ts: float | None = getattr(request.app.state, "_startup_time", None)
    if startup_ts is not None:
        checks["uptime_seconds"] = round(time.time() - startup_ts, 1)

    # ── System resources ──
    try:
        import psutil  # optional — graceful degrade if missing

        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        checks["ram_rss_mb"] = round(mem.rss / (1024 * 1024), 1)
        checks["ram_vms_mb"] = round(mem.vms / (1024 * 1024), 1)
        checks["cpu_percent"] = psutil.cpu_percent(interval=0)
    except Exception:
        checks["ram_rss_mb"] = None
        checks["cpu_percent"] = None

    # ── DB pool ──
    pool = getattr(request.app.state, "db_pool", None)
    if pool is not None:
        checks["db_pool"] = {
            "size": pool.get_size(),
            "free": pool.get_idle_size(),
            "min": pool.get_min_size(),
            "max": pool.get_max_size(),
        }
    else:
        checks["db_pool"] = None

    # ── Ollama connectivity (fast check, timeout 2s) ──
    try:
        import httpx  # noqa: F811

        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            checks["ollama"] = {
                "available": r.status_code == 200,
                "models": len(r.json().get("models", [])) if r.status_code == 200 else 0,
            }
    except Exception:
        checks["ollama"] = {"available": False, "models": 0}

    # ── Disk usage (workspace directory) ──
    try:
        import shutil

        settings = getattr(request.app.state, "settings", None)
        ws_dir = getattr(settings, "workspace_base_dir", None) or "data/workspaces"
        usage = shutil.disk_usage(ws_dir)
        checks["disk"] = {
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "used_pct": round(usage.used / usage.total * 100, 1),
        }
    except Exception:
        checks["disk"] = None

    # ── Agent state counts ──
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is not None:
        try:
            agents = registry.list_all()
            counts: dict[str, int] = {}
            for a in agents:
                state = str(getattr(a.state, "value", a.state)).lower()
                counts[state] = counts.get(state, 0) + 1
            checks["agents"] = {"total": len(agents), **counts}
        except Exception:
            checks["agents"] = None
    else:
        checks["agents"] = None

    # ── Overall status ──
    if checks.get("db_pool") is None:
        checks["status"] = "degraded"

    return JSONResponse(checks)


health_routes = [
    Route("/health", health, methods=["GET"]),
    Route("/health/detailed", health_detailed, methods=["GET"]),
]
