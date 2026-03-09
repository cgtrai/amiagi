"""Tests for Knowledge Management routes — Sprint P3."""

from __future__ import annotations

import uuid
from pathlib import Path

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
                    if indexed_at_now:
                        s["indexed_at"] = "2026-03-09T00:00:00+00:00"
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

    def test_bases_include_enriched_metadata(self, tmp_path) -> None:
        file_path = tmp_path / "doc.txt"
        file_path.write_text("hello", encoding="utf-8")
        app = _make_app(_FakeKB())
        client = TestClient(app)
        create = client.post("/api/knowledge/bases", json={"name": "docs", "engine": "tfidf"})
        base_id = create.json()["id"]
        client.post(f"/api/knowledge/bases/{base_id}/sources", json={"path": str(file_path), "type": "file"})
        response = client.get("/api/knowledge/bases")
        base = next(item for item in response.json()["bases"] if item["id"] == base_id)
        assert base["engine"] == "tfidf"
        assert base["document_count"] >= 0
        assert "total_size_bytes" in base
        assert "agents_using" in base
        assert "last_updated" in base


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
        assert data["refresh_frequency"] == "manual"
        assert data["last_refresh"] is None
        assert data["next_refresh"] is None
        assert data["config"]["chunking"] == "paragraph"
        assert data["config"]["embedding_model"] == "tfidf"
        assert data["runtime_available"] is False

    def test_pipeline_schedule_updates_frequency(self) -> None:
        client = TestClient(_make_app())
        response = client.put(
            "/api/knowledge/pipeline/schedule",
            json={"frequency": "daily", "chunking": "fixed", "chunk_size": 256, "overlap": 32, "embedding_model": "tfidf"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["refresh_frequency"] == "daily"
        assert data["next_refresh"] is not None
        assert data["config"]["chunking"] == "fixed"
        assert data["config"]["chunk_size"] == 256
        assert data["config"]["overlap"] == 32

    def test_pipeline_schedule_manual_clears_next_refresh(self) -> None:
        client = TestClient(_make_app())
        client.put("/api/knowledge/pipeline/schedule", json={"frequency": "weekly"})
        response = client.put("/api/knowledge/pipeline/schedule", json={"frequency": "manual"})
        assert response.status_code == 200
        data = response.json()
        assert data["refresh_frequency"] == "manual"
        assert data["next_refresh"] is None

    def test_pipeline_refresh_endpoint_exists(self) -> None:
        client = TestClient(_make_app(_FakeKB()))
        response = client.post("/api/knowledge/pipeline/refresh")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["status"] == "indexing"
        assert data["last_refresh"] is not None

    def test_pipeline_refresh_requires_runtime(self) -> None:
        client = TestClient(_make_app())
        response = client.post("/api/knowledge/pipeline/refresh")
        assert response.status_code == 409
        assert response.json()["error"] == "knowledge_refresh_not_supported"


class TestKnowledgeRuntimeSemantics:
    def test_global_add_source_uses_saved_pipeline_chunking(self, tmp_path) -> None:
        file_path = tmp_path / "doc.txt"
        file_path.write_text("A" * 60, encoding="utf-8")
        client = TestClient(_make_app(_FakeKB()))
        client.get("/api/knowledge/bases")
        response = client.put(
            "/api/knowledge/pipeline/schedule",
            json={"frequency": "manual", "chunking": "fixed", "chunk_size": 20, "overlap": 0, "embedding_model": "tfidf"},
        )
        assert response.status_code == 200

        added = client.post(
            "/api/knowledge/bases/global/sources",
            json={"path": str(file_path), "type": "file"},
        )

        assert added.status_code == 201
        data = added.json()["source"]
        assert data["status"] == "indexed"
        assert data["chunks_count"] == 3
        assert data["pipeline_config"]["chunking"] == "fixed"

    def test_global_add_source_requires_runtime(self, tmp_path) -> None:
        file_path = tmp_path / "doc.txt"
        file_path.write_text("hello", encoding="utf-8")
        app = _make_app()
        client = TestClient(app)
        client.get("/api/knowledge/bases")

        added = client.post(
            "/api/knowledge/bases/global/sources",
            json={"path": str(file_path), "type": "file"},
        )

        assert added.status_code == 409
        assert added.json()["error"] == "knowledge_ingest_not_supported"
        assert app.state.knowledge_repo._sources.get(GLOBAL_BASE_UUID) in (None, [])

    def test_non_global_search_is_explicitly_not_supported(self) -> None:
        client = TestClient(_make_app(_FakeKB()))
        create = client.post("/api/knowledge/bases", json={"name": "docs"})
        base_id = create.json()["id"]

        response = client.get(f"/api/knowledge/bases/{base_id}/search?q=test")

        assert response.status_code == 409
        assert response.json()["error"] == "knowledge_search_not_supported"

    def test_non_global_reindex_is_explicitly_not_supported(self) -> None:
        client = TestClient(_make_app(_FakeKB()))
        create = client.post("/api/knowledge/bases", json={"name": "docs"})
        base_id = create.json()["id"]

        response = client.post(f"/api/knowledge/bases/{base_id}/reindex")

        assert response.status_code == 409
        assert response.json()["error"] == "knowledge_reindex_not_supported"


class TestKnowledgePageAssets:
    def test_knowledge_template_contains_refresh_schedule_controls(self) -> None:
        template = Path("src/amiagi/interfaces/web/templates/knowledge.html").read_text(encoding="utf-8")
        assert 'id="kb-refresh-freq"' in template
        assert 'id="btn-save-schedule"' in template
        assert 'id="btn-refresh-now"' in template
        assert 'id="kb-last-refresh"' in template
        assert 'id="kb-next-refresh"' in template

    def test_knowledge_js_renders_extended_metadata_and_schedule_flow(self) -> None:
        script = Path("src/amiagi/interfaces/web/static/js/knowledge.js").read_text(encoding="utf-8")
        assert "total_size_bytes" in script
        assert "agents_using" in script
        assert "last_updated" in script
        assert "formatBytes" in script
        assert "/api/knowledge/pipeline/schedule" in script
        assert "/api/knowledge/pipeline/refresh" in script
        assert "cfg-chunking" in script
        assert "embedding_model" in script
        assert "supports_reindex" in script
        assert "sourceStatusLabel" in script
        assert "sourceTypeLabel" in script
        assert "function notify(message, level)" in script
        assert 'notify("Reindex started", "success")' in script
        assert 'notify("Source added", "success")' in script
        assert 'notify("Knowledge base created", "success")' in script
        assert 'notify(await responseErrorMessage(res, "Failed to save pipeline settings"), "error")' in script
        assert 'notify(await responseErrorMessage(res, "Failed to start reindex"), "error")' in script
        assert 'notify(await responseErrorMessage(res, "Failed to add source"), "error")' in script
        assert "alert(" not in script

    def test_knowledge_ui_avoids_colorful_emoji_action_labels(self) -> None:
        template = Path("src/amiagi/interfaces/web/templates/knowledge.html").read_text(encoding="utf-8")
        script = Path("src/amiagi/interfaces/web/static/js/knowledge.js").read_text(encoding="utf-8")

        assert "🔄 {{ _(\"knowledge.refresh_now\") }}" not in template
        assert "📘" not in script
        assert "📗" not in script
        assert "🗑" not in script
        assert "📁" not in script


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
