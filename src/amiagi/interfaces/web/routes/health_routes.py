"""Health check routes — GET /health, GET /health/detailed, /vram, /connections."""

from __future__ import annotations

import os
import time
from typing import Any

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

    # ── Ollama connectivity (uses shared 30-s cache) ──
    try:
        from amiagi.interfaces.web.routes.api_routes import _check_ollama_cached
        alive, models = await _check_ollama_cached()
        checks["ollama"] = {"available": alive, "models": models}
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


async def health_vram(request: Request) -> JSONResponse:
    """Return GPU/VRAM information and per-model allocation."""
    data: dict[str, Any] = {"available": False}

    # ── VramAdvisor (if it exists on app state) ──
    vram_advisor = getattr(request.app.state, "vram_advisor", None)
    if vram_advisor is not None:
        try:
            info = vram_advisor.detect()
            data["available"] = True
            data["total_mb"] = getattr(info, "total_mb", 0)
            data["used_mb"] = getattr(info, "used_mb", 0)
            data["free_mb"] = getattr(info, "free_mb", 0)
        except Exception:
            pass

    # ── VRAMScheduler per-agent allocation ──
    vram_sched = getattr(request.app.state, "vram_scheduler", None)
    if vram_sched is not None:
        try:
            alloc = vram_sched.allocations() if hasattr(vram_sched, "allocations") else {}
            data["allocations"] = alloc
        except Exception:
            data["allocations"] = {}

    # ── Ollama model sizes (list loaded models with size info) ──
    try:
        from amiagi.interfaces.web.routes.api_routes import _check_ollama_cached
        alive, model_count = await _check_ollama_cached()
        data["ollama_alive"] = alive
        data["ollama_model_count"] = model_count
    except Exception:
        data["ollama_alive"] = False

    return JSONResponse(data)


async def health_connections(request: Request) -> JSONResponse:
    """Return database pool and connection statistics."""
    data: dict[str, Any] = {}

    # ── DB pool stats ──
    pool = getattr(request.app.state, "db_pool", None)
    if pool is not None and hasattr(pool, "get_size"):
        data["db_pool"] = {
            "size": pool.get_size(),
            "idle": pool.get_idle_size(),
            "min": pool.get_min_size(),
            "max": pool.get_max_size(),
            "utilization_pct": round(
                (pool.get_size() - pool.get_idle_size()) / max(pool.get_max_size(), 1) * 100, 1
            ),
        }
    elif pool is not None:
        # SQLite fallback
        data["db_pool"] = {"type": "sqlite", "size": 1}
    else:
        data["db_pool"] = None

    # ── WebSocket connections ──
    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        try:
            data["websocket_clients"] = len(hub._clients) if hasattr(hub, "_clients") else 0
        except Exception:
            data["websocket_clients"] = 0

    # ── Rate limiter status ──
    rate_limiter = getattr(request.app.state, "rate_limiter", None)
    if rate_limiter is not None:
        try:
            data["rate_limiter"] = {"active": True}
        except Exception:
            data["rate_limiter"] = None

    # ── Active agents/tasks ──
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is not None:
        try:
            agents = registry.list_all()
            data["agent_count"] = len(agents)
        except Exception:
            data["agent_count"] = 0

    # ── Uptime ──
    startup_ts = getattr(request.app.state, "_startup_time", None)
    if startup_ts is not None:
        data["uptime_seconds"] = round(time.time() - startup_ts, 1)

    return JSONResponse(data)


health_routes = [
    Route("/health", health, methods=["GET"]),
    Route("/health/detailed", health_detailed, methods=["GET"]),
    Route("/api/health/vram", health_vram, methods=["GET"]),
    Route("/api/health/connections", health_connections, methods=["GET"]),
]
