"""Tests for memory routes — P13 Memory Management."""

from __future__ import annotations

import threading
import time

import pytest
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from amiagi.interfaces.web.routes.memory_routes import memory_routes
from amiagi.application.cross_agent_memory import CrossAgentMemory, MemoryItem


def _make_app(mem: CrossAgentMemory | None = None) -> Starlette:
    app = Starlette(routes=list(memory_routes))
    if mem is not None:
        app.state.cross_memory = mem
    return app


def _sample_memory() -> CrossAgentMemory:
    """Return a CrossAgentMemory pre-loaded with 3 items."""
    cam = CrossAgentMemory()
    cam.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="found A", tags=["web"]))
    cam.store(MemoryItem(agent_id="a2", task_id="t1", key_findings="found B", tags=["api"]))
    cam.store(MemoryItem(agent_id="a1", task_id="t2", key_findings="found C", tags=["web", "api"]))
    return cam


class TestListMemory:
    def test_empty_when_no_memory(self) -> None:
        client = TestClient(_make_app())  # no cross_memory on state
        r = client.get("/api/memory")
        assert r.status_code == 200
        assert r.json() == {"items": [], "total": 0}

    def test_returns_all_items(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.get("/api/memory")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    def test_filter_by_agent_id(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.get("/api/memory", params={"agent_id": "a1"})
        data = r.json()
        assert all(i["agent_id"] == "a1" for i in data["items"])

    def test_filter_by_task_id(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.get("/api/memory", params={"task_id": "t1"})
        data = r.json()
        assert all(i["task_id"] == "t1" for i in data["items"])

    def test_limit(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.get("/api/memory", params={"limit": "1"})
        data = r.json()
        assert len(data["items"]) == 1


class TestCreateMemoryItem:
    def test_create_item(self) -> None:
        cam = CrossAgentMemory()
        client = TestClient(_make_app(cam))
        r = client.post(
            "/api/memory",
            json={
                "agent_id": "a1",
                "task_id": "t1",
                "key_findings": "created",
                "tags": ["new"],
                "metadata": {"type": "note"},
            },
        )
        assert r.status_code == 201
        assert r.json()["ok"] is True
        assert cam.count() == 1
        assert r.json()["item"]["index"] == 0
        assert r.json()["item"]["item_type"] == "note"

    def test_create_requires_agent_id(self) -> None:
        cam = CrossAgentMemory()
        client = TestClient(_make_app(cam))
        r = client.post("/api/memory", json={"key_findings": "created"})
        assert r.status_code == 400

    def test_create_requires_key_findings(self) -> None:
        cam = CrossAgentMemory()
        client = TestClient(_make_app(cam))
        r = client.post("/api/memory", json={"agent_id": "a1"})
        assert r.status_code == 400


class TestDeleteMemoryItem:
    def test_delete_valid_index(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.delete("/api/memory/0")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert cam.count() == 2

    def test_delete_out_of_range(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.delete("/api/memory/99")
        assert r.status_code == 404

    def test_delete_no_memory(self) -> None:
        client = TestClient(_make_app())
        r = client.delete("/api/memory/0")
        assert r.status_code == 503


class TestClearMemory:
    def test_clear_all(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.delete("/api/memory")
        assert r.status_code == 200
        assert r.json()["cleared"] is True
        assert cam.count() == 0

    def test_clear_no_memory(self) -> None:
        client = TestClient(_make_app())
        r = client.delete("/api/memory")
        assert r.status_code == 503


class TestEditMemoryItem:
    """Tests for PUT /api/memory/{index} — edit a memory item."""

    def test_edit_key_findings(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.put("/api/memory/0", json={"key_findings": "updated"})
        assert r.status_code == 200
        items = cam.query(limit=100)
        assert any(i.key_findings == "updated" for i in items)

    def test_edit_tags(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.put("/api/memory/1", json={"tags": ["new-tag"]})
        assert r.status_code == 200

    def test_edit_task_id_and_type(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.put(
            "/api/memory/1",
            json={"task_id": "t-updated", "metadata": {"type": "fact"}},
        )
        assert r.status_code == 200
        data = r.json()["updated"]
        assert data["task_id"] == "t-updated"
        assert data["item_type"] == "fact"

    def test_edit_out_of_range(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))
        r = client.put("/api/memory/99", json={"key_findings": "nope"})
        assert r.status_code == 404

    def test_edit_no_memory(self) -> None:
        client = TestClient(_make_app())
        r = client.put("/api/memory/0", json={"key_findings": "x"})
        assert r.status_code == 503


class TestMemorySerializationAndStableIndexes:
    def test_filtered_list_returns_backing_store_indexes(self) -> None:
        cam = _sample_memory()
        client = TestClient(_make_app(cam))

        r = client.get("/api/memory", params={"agent_id": "a2"})

        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["index"] == 1
        assert data["items"][0]["links"]["agent"] == "/agents/a2"

    def test_search_and_shared_items_include_indexes_and_scope(self) -> None:
        cam = CrossAgentMemory()
        cam.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="shared note", tags=["shared"], metadata={"type": "context", "shared": True}))
        cam.store(MemoryItem(agent_id="a2", task_id="t2", key_findings="plain note", tags=["local"], metadata={"type": "note"}))
        client = TestClient(_make_app(cam))

        shared = client.get("/api/memory/shared")
        search = client.get("/api/memory/search", params={"q": "shared"})

        assert shared.status_code == 200
        assert shared.json()["items"][0]["memory_scope"] == "shared"
        assert "index" in shared.json()["items"][0]
        assert search.status_code == 200
        assert search.json()["items"][0]["item_type"] == "context"
