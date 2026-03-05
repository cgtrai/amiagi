"""Tests for Faza 7 — File management.

Covers:
- Upload handler (multipart, size limit, MIME check)
- BinaryStore (SHA-256 dedup, save, browse, delete)
- Download handler
- Workspace routes (browse, view, delete permission)
"""

from __future__ import annotations

import io
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# BinaryStore unit tests (no DB — mock pool)
# ---------------------------------------------------------------------------

class TestBinaryStoreSaveDedup:
    def test_sha256_hash(self):
        from amiagi.interfaces.web.files.binary_store import sha256_hash
        data = b"hello world"
        expected = hashlib.sha256(data).hexdigest()
        assert sha256_hash(data) == expected

    def test_sha256_consistent(self):
        from amiagi.interfaces.web.files.binary_store import sha256_hash
        assert sha256_hash(b"abc") == sha256_hash(b"abc")

    def test_sha256_different(self):
        from amiagi.interfaces.web.files.binary_store import sha256_hash
        assert sha256_hash(b"abc") != sha256_hash(b"def")


class TestBinaryStoreWorkspace:
    def test_browse_empty_workspace(self):
        from amiagi.interfaces.web.files.binary_store import BinaryStore
        with tempfile.TemporaryDirectory() as tmp:
            store = BinaryStore(pool=MagicMock(), base_dir=tmp)
            result = store.browse_workspace("user1", "default")
            assert result == []

    def test_browse_with_files(self):
        from amiagi.interfaces.web.files.binary_store import BinaryStore
        with tempfile.TemporaryDirectory() as tmp:
            store = BinaryStore(pool=MagicMock(), base_dir=tmp)
            # Create a file
            user_dir = store._user_dir("user1", "default")
            (user_dir / "test.txt").write_text("hello")
            result = store.browse_workspace("user1", "default")
            names = [r["name"] for r in result]
            assert "test.txt" in names

    def test_user_dir_creates_directory(self):
        from amiagi.interfaces.web.files.binary_store import BinaryStore
        with tempfile.TemporaryDirectory() as tmp:
            store = BinaryStore(pool=MagicMock(), base_dir=tmp)
            d = store._user_dir("u1", "ws1")
            assert d.exists()
            assert d.is_dir()


class TestBinaryStoreMaxUpload:
    def test_max_upload_bytes(self):
        from amiagi.interfaces.web.files.binary_store import MAX_UPLOAD_BYTES
        assert MAX_UPLOAD_BYTES == 50 * 1024 * 1024


class TestAllowedMimes:
    def test_text_allowed(self):
        from amiagi.interfaces.web.files.binary_store import ALLOWED_MIME_PREFIXES
        assert any("text/" == p for p in ALLOWED_MIME_PREFIXES)

    def test_image_allowed(self):
        from amiagi.interfaces.web.files.binary_store import ALLOWED_MIME_PREFIXES
        assert any("image/" == p for p in ALLOWED_MIME_PREFIXES)


# ---------------------------------------------------------------------------
# Upload handler (HTTP tests)
# ---------------------------------------------------------------------------

class TestUploadHandler:
    def _make_app(self, store_mock):
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.testclient import TestClient
        from amiagi.interfaces.web.files.upload_handler import handle_upload

        app = Starlette(routes=[Route("/files/upload", handle_upload, methods=["POST"])])
        app.state.binary_store = store_mock
        return TestClient(app)

    def test_upload_no_file_field(self):
        store = MagicMock()
        client = self._make_app(store)
        resp = client.post("/files/upload", data={"other": "value"})
        assert resp.status_code == 400

    def test_upload_too_large(self):
        store = MagicMock()
        client = self._make_app(store)
        # 51 MB file
        big_data = b"x" * (51 * 1024 * 1024)
        resp = client.post(
            "/files/upload",
            files={"file": ("big.bin", io.BytesIO(big_data), "application/octet-stream")},
        )
        assert resp.status_code == 413

    def test_upload_bad_mime(self):
        store = MagicMock()
        client = self._make_app(store)
        resp = client.post(
            "/files/upload",
            files={"file": ("test.exe", io.BytesIO(b"MZ"), "application/x-executable")},
        )
        assert resp.status_code == 415

    def test_upload_success(self):
        store = AsyncMock()
        store.save = AsyncMock(return_value={
            "id": "abc123", "sha256": "deadbeef", "size": 5,
            "path": "/tmp/test.txt", "deduplicated": False,
        })
        client = self._make_app(store)
        resp = client.post(
            "/files/upload",
            files={"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "abc123"
        assert data["deduplicated"] is False


# ---------------------------------------------------------------------------
# Download handler
# ---------------------------------------------------------------------------

class TestDownloadHandler:
    def _make_app(self, store_mock):
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.testclient import TestClient
        from amiagi.interfaces.web.files.download_handler import handle_download

        app = Starlette(routes=[Route("/files/{asset_id}/download", handle_download, methods=["GET"])])
        app.state.binary_store = store_mock
        return TestClient(app)

    def test_download_not_found(self):
        store = AsyncMock()
        store.get_metadata = AsyncMock(return_value=None)
        client = self._make_app(store)
        resp = client.get("/files/missing/download")
        assert resp.status_code == 404

    def test_download_success(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"file content")
            f.flush()
            tmp_path = f.name

        try:
            store = AsyncMock()
            store.get_metadata = AsyncMock(return_value={
                "filename": "test.txt", "content_type": "text/plain",
            })
            store.get_disk_path = AsyncMock(return_value=Path(tmp_path))
            client = self._make_app(store)
            resp = client.get("/files/abc/download")
            assert resp.status_code == 200
            assert b"file content" in resp.content
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Workspace browse route
# ---------------------------------------------------------------------------

class TestWorkspaceBrowse:
    def _make_app(self, store_mock):
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.testclient import TestClient
        from amiagi.interfaces.web.routes.workspace_routes import browse_workspace

        app = Starlette(routes=[Route("/workspace/browse", browse_workspace, methods=["GET"])])
        app.state.binary_store = store_mock
        return TestClient(app)

    def test_browse_returns_json(self):
        store = MagicMock()
        store.browse_workspace = MagicMock(return_value=[
            {"name": "readme.md", "path": "readme.md", "size": 100, "is_dir": False},
        ])
        client = self._make_app(store)
        resp = client.get("/workspace/browse")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 1
        assert data["files"][0]["name"] == "readme.md"


# ---------------------------------------------------------------------------
# Delete file (RBAC)
# ---------------------------------------------------------------------------

class TestDeleteFile:
    def test_delete_without_permission_returns_401_or_403(self):
        """Without user on request.state, require_permission returns 401."""
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.testclient import TestClient
        from amiagi.interfaces.web.routes.workspace_routes import delete_file

        app = Starlette(routes=[Route("/files/{asset_id}", delete_file, methods=["DELETE"])])
        app.state.binary_store = MagicMock()
        client = TestClient(app)
        resp = client.delete("/files/some-id")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# SHA-256 dedup logic
# ---------------------------------------------------------------------------

class TestSHA256Dedup:
    @pytest.mark.asyncio
    async def test_identical_uploads_share_hash(self):
        """After 2 uploads with same content, DB has 2 rows but same hash."""
        from amiagi.interfaces.web.files.binary_store import BinaryStore, sha256_hash
        data = b"identical content"
        h = sha256_hash(data)

        pool = AsyncMock()
        # First upload: no existing row
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock()

        with tempfile.TemporaryDirectory() as tmp:
            store = BinaryStore(pool, tmp)
            r1 = await store.save(user_id="u1", workspace="default",
                                  filename="a.txt", content_type="text/plain", data=data)
            assert r1["sha256"] == h
            assert r1["deduplicated"] is False

            # Second upload: fetchrow returns existing row
            disk_path = str(store._user_dir("u1", "default") / "a.txt")
            pool.fetchrow = AsyncMock(return_value={"id": "old", "disk_path": disk_path})
            r2 = await store.save(user_id="u1", workspace="default",
                                  filename="b.txt", content_type="text/plain", data=data)
            assert r2["sha256"] == h
            assert r2["deduplicated"] is True


# ---------------------------------------------------------------------------
# File preview (view)
# ---------------------------------------------------------------------------

class TestFileView:
    def test_view_text_file(self):
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.testclient import TestClient
        from amiagi.interfaces.web.routes.workspace_routes import view_file
        from amiagi.interfaces.web.files.binary_store import BinaryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = BinaryStore(pool=MagicMock(), base_dir=tmp)
            # view_file reads user_id from request.state.user — anonymous fallback
            user_dir = store._user_dir("anonymous", "default")
            (user_dir / "readme.md").write_text("# Hello")

            app = Starlette(routes=[Route("/workspace/file", view_file, methods=["GET"])])
            app.state.binary_store = store
            client = TestClient(app)
            resp = client.get("/workspace/file?path=readme.md")
            assert resp.status_code == 200
            data = resp.json()
            assert data["type"] == "text"
            assert "# Hello" in data["content"]
