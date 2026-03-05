"""Tests for file upload and download handlers."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.interfaces.web.files.upload_handler import handle_upload
from amiagi.interfaces.web.files.download_handler import handle_download, handle_workspace_zip


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

@dataclass
class _FakeUser:
    user_id: str = "test-user"


class _InjectUser(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.user = _FakeUser()
        return await call_next(request)


def _make_binary_store(*, save_result: dict | None = None, metadata: dict | None = None):
    store = MagicMock()
    store.save = AsyncMock(return_value=save_result or {"id": "abc", "filename": "test.txt"})
    store.get_metadata = AsyncMock(return_value=metadata)
    store.get_disk_path = AsyncMock(return_value=None)
    store.delete = AsyncMock(return_value=True)
    store.browse_workspace = MagicMock(return_value=[])
    return store


def _make_app(store=None) -> Starlette:
    store = store or _make_binary_store()
    app = Starlette(
        routes=[
            Route("/files/upload", handle_upload, methods=["POST"]),
            Route("/files/{asset_id}/download", handle_download, methods=["GET"]),
            Route("/workspace/download-zip", handle_workspace_zip, methods=["GET"]),
        ],
        middleware=[Middleware(_InjectUser)],
    )
    app.state.binary_store = store
    return app


# ------------------------------------------------------------------
# Upload tests
# ------------------------------------------------------------------

class TestUpload:

    def test_upload_success(self) -> None:
        store = _make_binary_store()
        client = TestClient(_make_app(store))
        resp = client.post(
            "/files/upload",
            files={"file": ("hello.txt", b"content", "text/plain")},
        )
        assert resp.status_code == 201
        store.save.assert_awaited_once()

    def test_upload_no_file_field(self) -> None:
        client = TestClient(_make_app())
        resp = client.post("/files/upload", data={"other": "value"})
        assert resp.status_code == 400
        assert "No file field" in resp.json()["error"]

    def test_upload_too_large(self) -> None:
        store = _make_binary_store()
        client = TestClient(_make_app(store))
        from amiagi.interfaces.web.files.binary_store import MAX_UPLOAD_BYTES
        big_data = b"x" * (MAX_UPLOAD_BYTES + 1)
        resp = client.post(
            "/files/upload",
            files={"file": ("big.bin", big_data, "text/plain")},
        )
        assert resp.status_code == 413

    def test_upload_disallowed_mime(self) -> None:
        client = TestClient(_make_app())
        resp = client.post(
            "/files/upload",
            files={"file": ("evil.exe", b"MZ", "application/x-msdownload")},
        )
        assert resp.status_code == 415


# ------------------------------------------------------------------
# Download tests
# ------------------------------------------------------------------

class TestDownload:

    def test_download_not_found(self) -> None:
        store = _make_binary_store(metadata=None)
        client = TestClient(_make_app(store))
        resp = client.get("/files/abc123/download")
        assert resp.status_code == 404

    def test_download_found_but_disk_missing(self) -> None:
        store = _make_binary_store(metadata={"filename": "a.txt", "content_type": "text/plain"})
        store.get_disk_path = AsyncMock(return_value=None)
        client = TestClient(_make_app(store))
        resp = client.get("/files/abc123/download")
        assert resp.status_code == 404

    def test_download_found_success(self, tmp_path: Path) -> None:
        fp = tmp_path / "file.txt"
        fp.write_text("hello")
        store = _make_binary_store(metadata={"filename": "file.txt", "content_type": "text/plain"})
        store.get_disk_path = AsyncMock(return_value=fp)
        client = TestClient(_make_app(store))
        resp = client.get("/files/abc/download")
        assert resp.status_code == 200
        assert resp.text == "hello"


# ------------------------------------------------------------------
# Workspace ZIP tests
# ------------------------------------------------------------------

class TestWorkspaceZip:

    def test_zip_empty_workspace(self, tmp_path: Path) -> None:
        ws_dir = tmp_path / "user" / "default"
        ws_dir.mkdir(parents=True)
        store = _make_binary_store()
        store._user_dir = MagicMock(return_value=ws_dir)
        client = TestClient(_make_app(store))
        resp = client.get("/workspace/download-zip")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

    def test_zip_with_files(self, tmp_path: Path) -> None:
        ws_dir = tmp_path / "user" / "default"
        ws_dir.mkdir(parents=True)
        (ws_dir / "readme.md").write_text("# Hello")
        store = _make_binary_store()
        store._user_dir = MagicMock(return_value=ws_dir)
        client = TestClient(_make_app(store))
        resp = client.get("/workspace/download-zip")
        assert resp.status_code == 200
        assert len(resp.content) > 0


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

class TestEdgeCases:

    def test_upload_empty_filename(self) -> None:
        store = _make_binary_store()
        client = TestClient(_make_app(store))
        resp = client.post(
            "/files/upload",
            files={"file": ("", b"data", "text/plain")},
        )
        # Should handle gracefully (empty filename → "untitled")
        assert resp.status_code in (201, 400)

    def test_upload_image_type(self) -> None:
        store = _make_binary_store()
        client = TestClient(_make_app(store))
        resp = client.post(
            "/files/upload",
            files={"file": ("pic.png", b"\x89PNG", "image/png")},
        )
        assert resp.status_code == 201

    def test_upload_pdf_type(self) -> None:
        store = _make_binary_store()
        client = TestClient(_make_app(store))
        resp = client.post(
            "/files/upload",
            files={"file": ("doc.pdf", b"%PDF", "application/pdf")},
        )
        assert resp.status_code == 201

    def test_upload_special_characters_filename(self) -> None:
        """Upload with unicode / special characters in the filename."""
        store = _make_binary_store()
        client = TestClient(_make_app(store))
        resp = client.post(
            "/files/upload",
            files={"file": ("raport_\u0105\u0107\u0119_\u017c\u00f3\u0142w.txt", b"content", "text/plain")},
        )
        assert resp.status_code == 201
        store.save.assert_awaited_once()

    def test_upload_zero_byte_file(self) -> None:
        """A zero-byte file should be accepted (or gracefully rejected)."""
        store = _make_binary_store()
        client = TestClient(_make_app(store))
        resp = client.post(
            "/files/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert resp.status_code in (201, 400)

    def test_upload_two_sequential(self) -> None:
        """Two sequential uploads should both succeed independently."""
        store = _make_binary_store()
        client = TestClient(_make_app(store))
        r1 = client.post("/files/upload", files={"file": ("a.txt", b"AAA", "text/plain")})
        r2 = client.post("/files/upload", files={"file": ("b.txt", b"BBB", "text/plain")})
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert store.save.await_count == 2
