"""Upload handler — multipart upload with validation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse

from amiagi.interfaces.web.files.binary_store import (
    ALLOWED_MIME_PREFIXES,
    MAX_UPLOAD_BYTES,
)

if TYPE_CHECKING:
    from amiagi.interfaces.web.files.binary_store import BinaryStore

logger = logging.getLogger(__name__)


async def handle_upload(request: Request) -> JSONResponse:
    """Process ``POST /files/upload`` multipart form data.

    Expects a ``file`` field in the multipart body.
    Validates size (≤ 50 MB) and MIME type whitelist.
    """
    store: BinaryStore = request.app.state.binary_store
    user = getattr(request.state, "user", None)
    user_id = user.user_id if user else "anonymous"

    form = await request.form()
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        return JSONResponse({"error": "No file field in request"}, status_code=400)

    filename = upload.filename or "untitled"
    content_type = upload.content_type or "application/octet-stream"
    data = await upload.read()

    # --- Size check ---
    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"error": f"File too large ({len(data)} bytes). Max {MAX_UPLOAD_BYTES} bytes."},
            status_code=413,
        )

    # --- MIME check ---
    allowed = any(content_type.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES)
    if not allowed:
        return JSONResponse(
            {"error": f"Content type '{content_type}' not allowed."},
            status_code=415,
        )

    result = await store.save(
        user_id=user_id,
        workspace="default",
        filename=filename,
        content_type=content_type,
        data=data,
    )

    # Activity log
    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "file.upload", {"filename": filename, "size": len(data), "content_type": content_type})

    return JSONResponse(result, status_code=201)
