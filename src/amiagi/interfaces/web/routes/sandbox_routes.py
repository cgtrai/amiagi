"""Sandbox & Shell Policy management routes.

Endpoints for managing agent sandboxes, shell execution policy,
and viewing execution logs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


# ── Sandbox CRUD ──────────────────────────────────────────────

async def sandbox_list(request: Request) -> JSONResponse:
    """GET /api/sandboxes — list all sandboxes with metadata."""
    mgr = getattr(request.app.state, "sandbox_manager", None)
    monitor = getattr(request.app.state, "sandbox_monitor", None)

    if mgr is None:
        return JSONResponse({"items": [], "error": "SandboxManager not available"})

    sandboxes = mgr.list_sandboxes()
    items: list[dict[str, Any]] = []
    for aid, path in sandboxes.items():
        snap = monitor.scan(aid) if monitor else None
        items.append({
            "agent_id": aid,
            "path": str(path),
            "size_bytes": snap.size_bytes if snap else mgr.sandbox_size(aid),
            "file_count": snap.file_count if snap else 0,
            "max_size_bytes": snap.max_size_bytes if snap else 268_435_456,
            "utilization_pct": snap.utilization_pct if snap else 0,
        })
    return JSONResponse({"items": items, "count": len(items)})


async def sandbox_create(request: Request) -> JSONResponse:
    """POST /api/sandboxes — create sandbox for an agent."""
    mgr = getattr(request.app.state, "sandbox_manager", None)
    if mgr is None:
        return JSONResponse({"error": "SandboxManager not available"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    agent_id = body.get("agent_id", "").strip()
    if not agent_id:
        return JSONResponse({"error": "agent_id is required"}, status_code=400)

    path = mgr.create(agent_id)
    return JSONResponse({
        "agent_id": agent_id,
        "path": str(path),
        "created": True,
    }, status_code=201)


async def sandbox_detail(request: Request) -> JSONResponse:
    """GET /api/sandboxes/{agent_id} — sandbox detail."""
    agent_id = request.path_params["agent_id"]
    mgr = getattr(request.app.state, "sandbox_manager", None)
    monitor = getattr(request.app.state, "sandbox_monitor", None)
    if mgr is None:
        return JSONResponse({"error": "SandboxManager not available"}, status_code=503)

    path = mgr.get(agent_id)
    if path is None:
        return JSONResponse({"error": "Sandbox not found"}, status_code=404)

    snap = monitor.scan(agent_id) if monitor else None
    # List top-level files
    files: list[dict[str, Any]] = []
    try:
        for item in sorted(path.iterdir()):
            files.append({
                "name": item.name,
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else 0,
            })
    except Exception:
        pass

    return JSONResponse({
        "agent_id": agent_id,
        "path": str(path),
        "size_bytes": snap.size_bytes if snap else mgr.sandbox_size(agent_id),
        "file_count": snap.file_count if snap else len(files),
        "max_size_bytes": snap.max_size_bytes if snap else 268_435_456,
        "utilization_pct": snap.utilization_pct if snap else 0,
        "files": files,
    })


async def sandbox_destroy(request: Request) -> JSONResponse:
    """DELETE /api/sandboxes/{agent_id} — destroy sandbox."""
    agent_id = request.path_params["agent_id"]
    mgr = getattr(request.app.state, "sandbox_manager", None)
    if mgr is None:
        return JSONResponse({"error": "SandboxManager not available"}, status_code=503)

    removed = mgr.destroy(agent_id)
    if not removed:
        return JSONResponse({"error": "Sandbox not found"}, status_code=404)
    return JSONResponse({"agent_id": agent_id, "destroyed": True})


async def sandbox_reset(request: Request) -> JSONResponse:
    """POST /api/sandboxes/{agent_id}/reset — reset sandbox to clean state."""
    agent_id = request.path_params["agent_id"]
    monitor = getattr(request.app.state, "sandbox_monitor", None)
    if monitor is None:
        return JSONResponse({"error": "SandboxMonitor not available"}, status_code=503)

    ok = monitor.reset(agent_id)
    if not ok:
        return JSONResponse({"error": "Sandbox not found"}, status_code=404)
    return JSONResponse({"agent_id": agent_id, "reset": True})


async def sandbox_cleanup(request: Request) -> JSONResponse:
    """POST /api/sandboxes/{agent_id}/cleanup — remove temp files."""
    agent_id = request.path_params["agent_id"]
    monitor = getattr(request.app.state, "sandbox_monitor", None)
    if monitor is None:
        return JSONResponse({"error": "SandboxMonitor not available"}, status_code=503)

    freed = monitor.cleanup_tmp(agent_id)
    return JSONResponse({"agent_id": agent_id, "bytes_freed": freed})


# ── Shell Policy ──────────────────────────────────────────────

async def shell_policy_get(request: Request) -> JSONResponse:
    """GET /api/shell-policy — current shell policy as JSON."""
    settings = getattr(request.app.state, "settings", None)
    policy_path = Path(
        getattr(settings, "shell_policy_path", "config/shell_allowlist.json")
    )

    if not policy_path.exists():
        return JSONResponse({"error": "Policy file not found"}, status_code=404)

    try:
        with open(policy_path) as f:
            data = json.load(f)
        return JSONResponse({"policy": data, "path": str(policy_path)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def shell_policy_update(request: Request) -> JSONResponse:
    """PUT /api/shell-policy — update shell policy JSON."""
    settings = getattr(request.app.state, "settings", None)
    policy_path = Path(
        getattr(settings, "shell_policy_path", "config/shell_allowlist.json")
    )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    policy_data = body.get("policy")
    if not isinstance(policy_data, dict):
        return JSONResponse({"error": "policy must be a JSON object"}, status_code=400)

    # Validate required keys
    valid_keys = {
        "no_arg_commands", "arg_subset_commands", "exact_commands",
        "ip_allowed_subcommands", "cat_allowed_files",
    }
    unknown = set(policy_data.keys()) - valid_keys
    if unknown:
        return JSONResponse(
            {"error": f"Unknown policy keys: {', '.join(unknown)}"},
            status_code=400,
        )

    try:
        # Backup current policy
        if policy_path.exists():
            backup = policy_path.with_suffix(".json.bak")
            backup.write_text(policy_path.read_text())

        with open(policy_path, "w") as f:
            json.dump(policy_data, f, indent=2, ensure_ascii=False)

        # Reload policy in-memory
        _reload_shell_policy(request)

        return JSONResponse({"updated": True, "path": str(policy_path)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _reload_shell_policy(request: Request) -> None:
    """Attempt to reload the shell policy into app.state."""
    try:
        from amiagi.application.shell_policy import load_shell_policy
        settings = getattr(request.app.state, "settings", None)
        policy_path = Path(
            getattr(settings, "shell_policy_path", "config/shell_allowlist.json")
        )
        if policy_path.exists():
            policy = load_shell_policy(policy_path)
            request.app.state.shell_policy = policy
    except Exception:
        logger.debug("Failed to reload shell policy", exc_info=True)


# ── Shell Execution Log ──────────────────────────────────────

async def shell_executions_list(request: Request) -> JSONResponse:
    """GET /api/shell-executions — list recent shell executions."""
    monitor = getattr(request.app.state, "sandbox_monitor", None)
    if monitor is None:
        return JSONResponse({"items": []})

    agent_id = request.query_params.get("agent_id")
    blocked_only = request.query_params.get("blocked", "").lower() in ("1", "true")
    limit = min(int(request.query_params.get("limit", "50")), 200)

    items = await monitor.list_executions(
        agent_id=agent_id, blocked_only=blocked_only, limit=limit,
    )
    # Serialize datetime/UUID objects
    for item in items:
        for k, v in item.items():
            if hasattr(v, "isoformat"):
                item[k] = v.isoformat()
            elif hasattr(v, "hex"):  # UUID
                item[k] = str(v)
    return JSONResponse({"items": items, "count": len(items)})


# ── Route table ───────────────────────────────────────────────

sandbox_routes: list[Route] = [
    Route("/api/sandboxes", sandbox_list, methods=["GET"]),
    Route("/api/sandboxes", sandbox_create, methods=["POST"]),
    Route("/api/sandboxes/{agent_id}", sandbox_detail, methods=["GET"]),
    Route("/api/sandboxes/{agent_id}", sandbox_destroy, methods=["DELETE"]),
    Route("/api/sandboxes/{agent_id}/reset", sandbox_reset, methods=["POST"]),
    Route("/api/sandboxes/{agent_id}/cleanup", sandbox_cleanup, methods=["POST"]),
    Route("/api/shell-policy", shell_policy_get, methods=["GET"]),
    Route("/api/shell-policy", shell_policy_update, methods=["PUT"]),
    Route("/api/shell-executions", shell_executions_list, methods=["GET"]),
]
