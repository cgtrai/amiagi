"""Tests for Knowledge Management routes — Sprint P3."""

from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from amiagi.interfaces.web.routes.knowledge_routes import knowledge_routes
from amiagi.interfaces.web.db.knowledge_repository import GLOBAL_BASE_UUID


# ── Helpers ─────────────────────────────────────────────────

class _FakeQueryResult:
    def __init__(self, entry_id, text, score, metadata=None):
        self.entry_id = entry_id
        self.text = text
        self.score = score
        self.metadata = metadata or {}


class _FakeKB:
    """Minimal KnowledgeBase stand-in with same API as real KB."""

    def __init__(self):
        self._entries: list[dict] = []

    def store(self, text, metadata=None):
        eid = len(self._entries)
        self._entries.append({"id": eid, "text": text, "metadata": metadata or {}})
        return eid

    def query(self, query_text, top_k=5):
        results = []
        for e in self._entries:
            if query_text.lower() in e["text"].lower():
                results.append(_FakeQueryResult(
                    entry_id=e["id"],
                    text=e["text"],
                    score=0.99,
                    metadata=e["metadata"],
                ))
        return results[:top_k]

    def count(self):
        return len(self._entries)

    def clear(self):
        self._entries.clear()

    def __len__(self):
        return len(self._entries)


class _FakeKnowledgeRepo:
    """In-memory stand-in for KnowledgeRepository.  Same async API."""

    def __init__(self):
        self._bases: dict = {}
        self._sources: dict = {}

    async def list_bases(self):
        return list(self._bases.values())

    async def get_base(self, base_id):
        return self._bases.get(base_id)

    async def get_base_by_name(self, name):
        for b in self._bases.values():
            if b["name"] == name:
                return b
        return None

    async def create_base(self, *, name, description="", embedding_model=None, base_id=None):
        bid = base_id or str(uuid.uuid4())
        entry = {"id": bid, "name": name, "description": description,
                 "embedding_model": embedding_model}
        self._bases[bid] = entry
        return entry

    async def update_base(self, base_id, **kwargs):
        base = self._bases.get(base_id)
        if base is None:
            return None
        for k, v in kwargs.items():
            base[k] = v
        return base

    async def delete_base(self, base_id):
        return self._bases.pop(base_id, None) is not None

    async def list_sources(self, base_id):
        return self._sources.get(base_id, [])

    async def add_source(self, base_id, file_path, *, status="pending", source_id=None):
        sid = source_id or str(uuid.uuid4())
        source = {"id": sid, "base_id": base_id, "path": file_path, "status": status}
        self._sources.setdefault(base_id, []).append(source)
        return source

    async def remove_source(self, source_id):
        for base_sources in self._sources.values():
            for i, s in enumerate(base_sources):
                if s["id"] == source_id:
                    base_sources.pop(i)
                    return True
        return False

    async def update_source_status(self, source_id, status, *, indexed_at_now=False):
        for base_sources in self._sources.values():
            for s in base_sources:
                if s["id"] == source_id:
                    s["status"] = status
                    return

    async def ensure_global_base(self):
        existing = await self.get_base_by_name("Global Knowledge Base")
        if existing:
            return existing["id"]
        base = await self.create_base(
            name="Global Knowledge Base",
            description="Default TF-IDF knowledge base",
            base_id=GLOBAL_BASE_UUID,
        )
        return base["id"]


def _make_app(kb=None) -> Starlette:
    app = Starlette(routes=list(knowledge_routes))
    app.state.knowledge_repo = _FakeKnowledgeRepo()
    if kb is not None:
        app.state.knowledge_base = kb
    return app


# ── Tests ───────────────────────────────────────────────────

class TestListBases:
    def test_no_kb(self) -> None:
        """Even without a live KB, the global seed row is always present."""
        client = TestClient(_make_app())
        r = client.get("/api/knowledge/bases")
        assert r.status_code == 200
        bases = r.json()["bases"]
        # The global base is always seeded in DB
        assert any(b["id"] == GLOBAL_BASE_UUID for b in bases)

    def test_global_base_seeded(self) -> None:
        kb = _FakeKB()
        kb.store("hello world")
        client = TestClient(_make_app(kb))
        r = client.get("/api/knowledge/bases")
        bases = r.json()["bases"]
        assert any(b["id"] == GLOBAL_BASE_UUID for b in bases)


class TestCreateBase:
    def test_create_ok(self) -> None:
        client = TestClient(_make_app())
        r = client.post(
            "/api/knowledge/bases",
            json={"name": "my-kb", "description": "Test base"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["base"]["name"] == "my-kb"
        assert "id" in data

    def test_duplicate_name(self) -> None:
        client = TestClient(_make_app())
        client.post("/api/knowledge/bases", json={"name": "dup"})
        r = client.post("/api/knowledge/bases", json={"name": "dup"})
        assert r.status_code == 409


class TestDeleteBase:
    def test_delete(self) -> None:
        client = TestClient(_make_app())
        create = client.post("/api/knowledge/bases", json={"name": "del-me"})
        base_id = create.json()["id"]
        r = client.delete(f"/api/knowledge/bases/{base_id}")
        assert r.status_code == 200

    def test_cannot_delete_global(self) -> None:
        kb = _FakeKB()
        client = TestClient(_make_app(kb))
        client.get("/api/knowledge/bases")  # seed global
        r = client.delete("/api/knowledge/bases/global")
        assert r.status_code == 400


class TestSearchKnowledge:
    def test_search_global(self) -> None:
        kb = _FakeKB()
        kb.store("artificial intelligence is great", {"source": "test.txt"})
        client = TestClient(_make_app(kb))
        client.get("/api/knowledge/bases")  # seed global
        r = client.get("/api/knowledge/bases/global/search?q=artificial&top=3")
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) >= 1

    def test_search_empty(self) -> None:
        kb = _FakeKB()
        client = TestClient(_make_app(kb))
        client.get("/api/knowledge/bases")  # seed global
        r = client.get("/api/knowledge/bases/global/search?q=nonexistent99&top=3")
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_search_missing_query(self) -> None:
        kb = _FakeKB()
        client = TestClient(_make_app(kb))
        client.get("/api/knowledge/bases")  # seed
        r = client.get("/api/knowledge/bases/global/search")
        assert r.status_code == 400


class TestPipelineStatus:
    def test_pipeline(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/knowledge/pipeline/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "idle"
        assert data["active_jobs"] == 0


class TestBaseStats:
    def test_global_stats(self) -> None:
        kb = _FakeKB()
        kb.store("entry one")
        kb.store("entry two")
        client = TestClient(_make_app(kb))
        client.get("/api/knowledge/bases")  # seed global
        r = client.get("/api/knowledge/bases/global/stats")
        assert r.status_code == 200
        data = r.json()["stats"]
        assert data["chunks_count"] == 2

    def test_notfound(self) -> None:
        client = TestClient(_make_app())
        r = client.get("/api/knowledge/bases/nonexistent/stats")
        assert r.status_code == 404
