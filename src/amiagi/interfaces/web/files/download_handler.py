"""Download handler — single file and ZIP streaming."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse

if TYPE_CHECKING:
    from amiagi.interfaces.web.files.binary_store import BinaryStore

logger = logging.getLogger(__name__)


async def handle_download(request: Request) -> FileResponse | JSONResponse:
    """``GET /files/{asset_id}/download`` — return single file."""
    asset_id = request.path_params["asset_id"]
    store: BinaryStore = request.app.state.binary_store
    meta = await store.get_metadata(asset_id)
    if meta is None:
        return JSONResponse({"error": "File not found"}, status_code=404)

    disk_path = await store.get_disk_path(asset_id)
    if disk_path is None:
        return JSONResponse({"error": "File missing from disk"}, status_code=404)

    # Activity log
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "file.download", {"asset_id": asset_id, "filename": meta["filename"]})

    return FileResponse(
        path=str(disk_path),
        filename=meta["filename"],
        media_type=meta.get("content_type", "application/octet-stream"),
    )


async def handle_workspace_zip(request: Request) -> StreamingResponse:
    """``GET /workspace/download-zip`` — stream a ZIP of the user's workspace."""
    store: BinaryStore = request.app.state.binary_store
    user = getattr(request.state, "user", None)
    user_id = user.user_id if user else "anonymous"
    workspace = request.query_params.get("workspace", "default")

    user_dir = store._user_dir(user_id, workspace)

    def _generate_zip():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in user_dir.rglob("*"):
                if fp.is_file():
                    arcname = str(fp.relative_to(user_dir))
                    zf.write(fp, arcname)
        buf.seek(0)
        yield buf.read()

    return StreamingResponse(
        _generate_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="workspace-{workspace}.zip"'},
    )
