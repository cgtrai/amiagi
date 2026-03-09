"""Tests for workspace routes — browse, view_file, delete_file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.workspace_routes import (
    browse_workspace,
    delete_workspace_file,
    delete_file,
    workspace_uploads,
    view_file,
    workspace_routes,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

@dataclass
class _FakeUser:
    user_id: str = "test-user"
    permissions: list[str] | None = None

    def __post_init__(self):
        if self.permissions is None:
            self.permissions = ["files.manage"]

    def has_permission(self, codename: str) -> bool:
        return codename in (self.permissions or [])


class _InjectUser(BaseHTTPMiddleware):
    def __init__(self, app, user=None):
        super().__init__(app)
        self._user = user or _FakeUser()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.user = self._user
        return await call_next(request)


def _make_store(*, tree=None, file_exists=True, is_text=True):
    store = MagicMock()
    store.browse_workspace = MagicMock(return_value=tree or [])
    store.list_files = AsyncMock(return_value=[])
    store.delete = AsyncMock(return_value=True)
    store.delete_by_workspace_path = AsyncMock(return_value=True)
    return store


def _make_app(store=None, user=None, tmp_dir: Path | None = None) -> Starlette:
    store = store or _make_store()
    if tmp_dir:
        store._user_dir = MagicMock(return_value=tmp_dir)
    app = Starlette(
        routes=[
            Route("/workspace/browse", browse_workspace, methods=["GET"]),
            Route("/workspace/uploads", workspace_uploads, methods=["GET"]),
            Route("/workspace/file", view_file, methods=["GET"]),
            Route("/workspace/file", delete_workspace_file, methods=["DELETE"]),
            Route("/files/{asset_id}", delete_file, methods=["DELETE"]),
        ],
        middleware=[Middleware(_InjectUser, user=user)],
    )
    app.state.binary_store = store
    return app


# ------------------------------------------------------------------
# Browse tests
# ------------------------------------------------------------------

class TestBrowseWorkspace:

    def test_browse_empty(self) -> None:
        client = TestClient(_make_app())
        resp = client.get("/workspace/browse")
        assert resp.status_code == 200
        data = resp.json()
        assert data["files"] == []
        assert data["workspace"] == "default"

    def test_browse_with_files(self) -> None:
        store = _make_store(tree=["readme.md", "src/main.py"])
        client = TestClient(_make_app(store))
        resp = client.get("/workspace/browse")
        assert resp.status_code == 200
        assert len(resp.json()["files"]) == 2

    def test_browse_custom_workspace(self) -> None:
        client = TestClient(_make_app())
        resp = client.get("/workspace/browse?workspace=project-x")
        assert resp.status_code == 200
        assert resp.json()["workspace"] == "project-x"

    def test_browse_large_tree(self) -> None:
        """Browse should handle a workspace with many files."""
        big_tree = [f"dir/file_{i}.py" for i in range(50)]
        store = _make_store(tree=big_tree)
        client = TestClient(_make_app(store))
        resp = client.get("/workspace/browse")
        assert resp.status_code == 200
        assert len(resp.json()["files"]) == 50


# ------------------------------------------------------------------
# View file tests
# ------------------------------------------------------------------

class TestViewFile:

    def test_view_text_file(self, tmp_path: Path) -> None:
        ws_dir = tmp_path / "test-user" / "default"
        ws_dir.mkdir(parents=True)
        f = ws_dir / "hello.py"
        f.write_text("print('hello')")

        store = _make_store()
        store._user_dir = MagicMock(return_value=ws_dir)
        client = TestClient(_make_app(store, tmp_dir=ws_dir))
        resp = client.get("/workspace/file?path=hello.py")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "text"
        assert "print" in data["content"]

    def test_view_file_not_found(self, tmp_path: Path) -> None:
        ws_dir = tmp_path / "test-user" / "default"
        ws_dir.mkdir(parents=True)

        store = _make_store()
        store._user_dir = MagicMock(return_value=ws_dir)
        client = TestClient(_make_app(store, tmp_dir=ws_dir))
        resp = client.get("/workspace/file?path=nope.txt")
        assert resp.status_code == 404

    def test_view_binary_file(self, tmp_path: Path) -> None:
        ws_dir = tmp_path / "test-user" / "default"
        ws_dir.mkdir(parents=True)
        f = ws_dir / "image.png"
        f.write_bytes(b"\x89PNG\r\n")

        store = _make_store()
        store._user_dir = MagicMock(return_value=ws_dir)
        client = TestClient(_make_app(store, tmp_dir=ws_dir))
        resp = client.get("/workspace/file?path=image.png")
        assert resp.status_code == 200
        assert resp.json()["type"] == "binary"

    def test_view_file_rejects_path_traversal(self, tmp_path: Path) -> None:
        ws_dir = tmp_path / "test-user" / "default"
        ws_dir.mkdir(parents=True)

        store = _make_store()
        store._user_dir = MagicMock(return_value=ws_dir)
        client = TestClient(_make_app(store, tmp_dir=ws_dir))
        resp = client.get("/workspace/file?path=../secret.txt")
        assert resp.status_code == 400


# ------------------------------------------------------------------
# Delete file tests
# ------------------------------------------------------------------

class TestDeleteFile:

    def test_delete_success(self) -> None:
        store = _make_store()
        user = _FakeUser(permissions=["files.manage"])
        client = TestClient(_make_app(store, user=user))
        resp = client.delete("/files/abc123")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_not_found(self) -> None:
        store = _make_store()
        store.delete = AsyncMock(return_value=False)
        user = _FakeUser(permissions=["files.manage"])
        client = TestClient(_make_app(store, user=user))
        resp = client.delete("/files/xxx")
        assert resp.status_code == 404

    def test_delete_workspace_file_by_path(self, tmp_path: Path) -> None:
        ws_dir = tmp_path / "test-user" / "default"
        ws_dir.mkdir(parents=True)
        target = ws_dir / "report.txt"
        target.write_text("report")

        store = _make_store()
        store._user_dir = MagicMock(return_value=ws_dir)
        user = _FakeUser(permissions=["files.manage"])
        client = TestClient(_make_app(store, user=user, tmp_dir=ws_dir))
        resp = client.delete("/workspace/file?path=report.txt")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        store.delete_by_workspace_path.assert_awaited_once()


class TestWorkspaceUploads:

    def test_workspace_uploads_returns_recent_items(self) -> None:
        store = _make_store()
        store.list_files = AsyncMock(return_value=[{"id": "1", "filename": "report.txt", "size_bytes": 42}])
        client = TestClient(_make_app(store))
        resp = client.get("/workspace/uploads")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["files"][0]["path"] == "report.txt"


# ------------------------------------------------------------------
# Route list tests
# ------------------------------------------------------------------

class TestRouteDefinitions:

    def test_workspace_routes_list_defined(self) -> None:
        assert len(workspace_routes) >= 6
        paths = [r.path for r in workspace_routes]
        assert "/workspace/browse" in paths
        assert "/workspace/file" in paths
        assert "/workspace/uploads" in paths
