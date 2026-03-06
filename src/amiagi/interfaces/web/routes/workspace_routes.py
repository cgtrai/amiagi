"""Workspace & file management routes.

Provides endpoints for browsing, uploading, downloading, and deleting
files in user workspaces.
"""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from amiagi.interfaces.web.files.download_handler import handle_download, handle_workspace_zip
from amiagi.interfaces.web.files.upload_handler import handle_upload
from amiagi.interfaces.web.rbac.middleware import require_permission

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# List files (GET /files)
# ------------------------------------------------------------------

async def list_files(request: Request) -> JSONResponse:
    """``GET /files`` — list uploaded files for the current user."""
    store = request.app.state.binary_store
    user = getattr(request.state, "user", None)
    user_id = str(user.user_id) if user else "anonymous"
    workspace = request.query_params.get("workspace", "default")

    tree = store.browse_workspace(user_id, workspace)
    return JSONResponse({"files": tree, "workspace": workspace, "count": len(tree)})


# ------------------------------------------------------------------
# Browse workspace
# ------------------------------------------------------------------

async def browse_workspace(request: Request) -> JSONResponse:
    """``GET /workspace/browse`` — JSON tree of user's workspace."""
    store = request.app.state.binary_store
    user = getattr(request.state, "user", None)
    user_id = str(user.user_id) if user else "anonymous"
    workspace = request.query_params.get("workspace", "default")

    tree = store.browse_workspace(user_id, workspace)
    return JSONResponse({"files": tree, "workspace": workspace})


# ------------------------------------------------------------------
# View file (inline preview)
# ------------------------------------------------------------------

async def view_file(request: Request) -> JSONResponse:
    """``GET /workspace/file`` — preview file content (text/markdown)."""
    store = request.app.state.binary_store
    path = request.query_params.get("path", "")
    user = getattr(request.state, "user", None)
    user_id = str(user.user_id) if user else "anonymous"
    workspace = request.query_params.get("workspace", "default")

    from pathlib import Path as P
    fp = store._user_dir(user_id, workspace) / path
    if not fp.exists() or not fp.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)

    # Safety: only serve text-like files inline
    suffix = fp.suffix.lower()
    text_exts = {".md", ".txt", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
                 ".html", ".css", ".xml", ".csv", ".toml", ".cfg", ".ini", ".sh"}
    if suffix in text_exts:
        content = fp.read_text(errors="replace")
        return JSONResponse({"path": path, "content": content, "type": "text"})
    else:
        return JSONResponse({"path": path, "type": "binary", "size": fp.stat().st_size})


# ------------------------------------------------------------------
# Delete file (requires permission)
# ------------------------------------------------------------------

@require_permission("files.manage")
async def delete_file(request: Request) -> JSONResponse:
    """``DELETE /files/{asset_id}`` — delete file (requires ``files.manage``)."""
    asset_id = request.path_params["asset_id"]
    store = request.app.state.binary_store
    deleted = await store.delete(asset_id)
    if not deleted:
        return JSONResponse({"error": "File not found"}, status_code=404)

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "file.delete", {"asset_id": asset_id})

    return JSONResponse({"deleted": True, "id": asset_id})


# ------------------------------------------------------------------
# Route table
# ------------------------------------------------------------------

workspace_routes: list[Route] = [
    Route("/files", list_files, methods=["GET"]),
    Route("/files/upload", handle_upload, methods=["POST"]),
    Route("/files/{asset_id}/download", handle_download, methods=["GET"]),
    Route("/files/{asset_id}", delete_file, methods=["DELETE"]),
    Route("/workspace/browse", browse_workspace, methods=["GET"]),
    Route("/workspace/file", view_file, methods=["GET"]),
    Route("/workspace/download-zip", handle_workspace_zip, methods=["GET"]),
    Route("/workspace/upload", handle_upload, methods=["POST"]),
]
