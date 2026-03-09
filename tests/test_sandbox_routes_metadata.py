from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes


class _SandboxManager:
    def __init__(self, root: Path) -> None:
        self._root = root

    def list_sandboxes(self):
        return {"agent-1": self._root / "agent-1"}

    def sandbox_size(self, agent_id: str) -> int:
        return sum(p.stat().st_size for p in (self._root / agent_id).rglob("*") if p.is_file())

    def create(self, agent_id: str) -> Path:
        path = self._root / agent_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get(self, agent_id: str) -> Path | None:
        path = self._root / agent_id
        return path if path.exists() else None

    def list_files(self, agent_id: str):
        path = self.get(agent_id)
        return sorted(path.iterdir()) if path and path.exists() else []


class _SandboxMonitor:
    def __init__(self, entries: list[dict[str, object]] | None = None) -> None:
        self.list_executions = AsyncMock(return_value=list(entries or []))


def test_sandbox_list_exposes_created_and_last_write(tmp_path: Path) -> None:
    sandbox_dir = tmp_path / "agent-1"
    sandbox_dir.mkdir()
    (sandbox_dir / "note.txt").write_text("hello", encoding="utf-8")

    app = Starlette(routes=list(sandbox_routes))
    app.state.sandbox_manager = _SandboxManager(tmp_path)

    client = TestClient(app)
    response = client.get("/api/sandboxes")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["created_at"] is not None
    assert item["last_write_at"] is not None
    assert item["sandbox_id"] == "agent-1"


def test_sandbox_detail_exposes_created_and_last_write(tmp_path: Path) -> None:
    sandbox_dir = tmp_path / "agent-1"
    sandbox_dir.mkdir()
    (sandbox_dir / "note.txt").write_text("hello", encoding="utf-8")

    app = Starlette(routes=list(sandbox_routes))
    app.state.sandbox_manager = _SandboxManager(tmp_path)

    client = TestClient(app)
    response = client.get("/api/sandboxes/agent-1")
    assert response.status_code == 200
    data = response.json()
    assert data["created_at"] is not None
    assert data["last_write_at"] is not None
    assert data["sandbox_id"] == "agent-1"


def test_sandbox_files_lists_top_level_entries(tmp_path: Path) -> None:
    sandbox_dir = tmp_path / "agent-1"
    sandbox_dir.mkdir()
    (sandbox_dir / "note.txt").write_text("hello", encoding="utf-8")
    (sandbox_dir / "data").mkdir()

    app = Starlette(routes=list(sandbox_routes))
    app.state.sandbox_manager = _SandboxManager(tmp_path)

    client = TestClient(app)
    response = client.get("/api/sandboxes/agent-1/files")
    assert response.status_code == 200
    files = response.json()["files"]
    assert [item["name"] for item in files] == ["data", "note.txt"]
    assert files[0]["is_dir"] is True
    assert files[1]["size"] == 5


def test_sandbox_log_reads_entries_from_monitor(tmp_path: Path) -> None:
    sandbox_dir = tmp_path / "agent-1"
    sandbox_dir.mkdir()

    app = Starlette(routes=list(sandbox_routes))
    app.state.sandbox_manager = _SandboxManager(tmp_path)
    app.state.sandbox_monitor = _SandboxMonitor([
        {
            "agent_id": "agent-1",
            "command": "python main.py",
            "blocked": False,
            "exit_code": 0,
        }
    ])

    client = TestClient(app)
    response = client.get("/api/sandboxes/agent-1/log")
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert entries[0]["agent_id"] == "agent-1"
    assert entries[0]["command"] == "python main.py"