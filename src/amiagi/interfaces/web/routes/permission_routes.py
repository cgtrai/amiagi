"""Permissions management API routes.

Endpoints:
    GET  /api/permissions         — current permissions state
    PUT  /api/permissions         — update permissions (allow_all, resources)
    GET  /api/permissions/resources — available resource definitions
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

# All known resource keys recognised by PermissionManager.
_RESOURCE_DEFINITIONS = [
    {"key": "network.local", "label": "permissions.res_network_local", "category": "network"},
    {"key": "network.internet", "label": "permissions.res_network_internet", "category": "network"},
    {"key": "disk.read", "label": "permissions.res_disk_read", "category": "disk"},
    {"key": "disk.write", "label": "permissions.res_disk_write", "category": "disk"},
    {"key": "process.exec", "label": "permissions.res_process_exec", "category": "system"},
    {"key": "camera", "label": "permissions.res_camera", "category": "device"},
    {"key": "microphone", "label": "permissions.res_microphone", "category": "device"},
    {"key": "clipboard.read", "label": "permissions.res_clipboard_read", "category": "system"},
    {"key": "clipboard.write", "label": "permissions.res_clipboard_write", "category": "system"},
]

_PERMISSIONS_PATH = Path("config/permissions.json")


def _get_permission_manager(request: Request):
    """Retrieve the PermissionManager from the running RouterEngine."""
    adapter = getattr(request.app.state, "web_adapter", None)
    if adapter is None:
        return None
    router_engine = getattr(adapter, "router_engine", None) or getattr(adapter, "_router_engine", None)
    if router_engine is None:
        return None
    return getattr(router_engine, "permission_manager", None)


def _persist(pm) -> None:
    """Persist current PermissionManager state to disk."""
    _PERMISSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "allow_all": bool(pm.allow_all),
        "granted_once": sorted(getattr(pm, "granted_once", set())),
    }
    _PERMISSIONS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def get_permissions(request: Request) -> JSONResponse:
    """GET /api/permissions — return current permissions state."""
    pm = _get_permission_manager(request)
    if pm is None:
        return JSONResponse({"error": "permission_manager unavailable"}, status_code=503)

    return JSONResponse({
        "allow_all": bool(pm.allow_all),
        "granted_once": sorted(getattr(pm, "granted_once", set())),
    })


async def update_permissions(request: Request) -> JSONResponse:
    """PUT /api/permissions — update permissions and persist.

    Expected body::

        {
          "allow_all": true,
          "granted_once": ["network.local", "disk.read"]
        }

    Both fields are optional; missing fields keep current value.
    """
    pm = _get_permission_manager(request)
    if pm is None:
        return JSONResponse({"error": "permission_manager unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    # -- apply changes ---
    if "allow_all" in body:
        pm.allow_all = bool(body["allow_all"])

    if "granted_once" in body:
        val = body["granted_once"]
        if isinstance(val, list):
            pm.granted_once = set(val)

    # -- persist ---
    _persist(pm)

    # -- audit ---
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "permissions.updated", {
        "allow_all": bool(pm.allow_all),
        "granted_once": sorted(pm.granted_once),
    })

    return JSONResponse({
        "ok": True,
        "allow_all": bool(pm.allow_all),
        "granted_once": sorted(pm.granted_once),
    })


async def get_resources(request: Request) -> JSONResponse:
    """GET /api/permissions/resources — available resource definitions."""
    return JSONResponse({"resources": _RESOURCE_DEFINITIONS})


permission_routes = [
    Route("/api/permissions", get_permissions, methods=["GET"]),
    Route("/api/permissions", update_permissions, methods=["PUT"]),
    Route("/api/permissions/resources", get_resources, methods=["GET"]),
]
