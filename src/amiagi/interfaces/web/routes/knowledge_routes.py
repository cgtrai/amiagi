"""Knowledge Management API routes.

Endpoints:
    GET    /knowledge                          — Knowledge Management page
    GET    /api/knowledge/bases                — list knowledge bases
    POST   /api/knowledge/bases                — create a knowledge base
    GET    /api/knowledge/bases/{id}           — get base details
    PUT    /api/knowledge/bases/{id}           — update base config
    DELETE /api/knowledge/bases/{id}           — delete a base
    POST   /api/knowledge/bases/{id}/sources   — add a source
    DELETE /api/knowledge/bases/{id}/sources/{sid} — remove a source
    POST   /api/knowledge/bases/{id}/reindex   — rebuild index
    GET    /api/knowledge/bases/{id}/search    — search within a base
    GET    /api/knowledge/bases/{id}/stats     — base statistics
    GET    /api/knowledge/pipeline/status      — pipeline status
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from amiagi.interfaces.web.db.knowledge_repository import KnowledgeRepository

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────

def _get_knowledge_base(request: Request):
    """Return the global KnowledgeBase from app.state."""
    return getattr(request.app.state, "knowledge_base", None)


def _get_kb_repo(request: Request) -> KnowledgeRepository:
    """Return the DB-backed KnowledgeRepository from app.state."""
    return request.app.state.knowledge_repo


def _no_kb() -> JSONResponse:
    return JSONResponse({"error": "knowledge_base unavailable"}, status_code=503)


# ── Page view ────────────────────────────────────────────────

async def knowledge_page(request: Request):
    """GET /knowledge — render Knowledge Management page."""
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        return JSONResponse({"error": "templates unavailable"}, status_code=500)
    return templates.TemplateResponse(request, "knowledge.html")


# ── Knowledge Base CRUD ──────────────────────────────────────

async def list_bases(request: Request) -> JSONResponse:
    """GET /api/knowledge/bases (from DB)."""
    repo = _get_kb_repo(request)
    kb = _get_knowledge_base(request)

    # Ensure the global base row exists
    await repo.ensure_global_base()

    bases = await repo.list_bases()
    items = []
    for base in bases:
        entry = dict(base)
        # Enrich global base with live stats
        if base["id"] == "global" and kb is not None:
            try:
                entry["chunks_count"] = kb.count()
            except Exception:
                entry["chunks_count"] = 0
        else:
            entry.setdefault("chunks_count", 0)

        # Attach sources from DB
        sources = await repo.list_sources(base["id"])
        entry["sources"] = sources
        items.append(entry)

    return JSONResponse({"bases": items, "total": len(items)})


async def create_base(request: Request) -> JSONResponse:
    """POST /api/knowledge/bases — create a new knowledge base (persisted in DB)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)

    repo = _get_kb_repo(request)

    # Check uniqueness via DB
    existing = await repo.get_base_by_name(name)
    if existing is not None:
        return JSONResponse({"error": f"base '{name}' already exists"}, status_code=409)

    base = await repo.create_base(
        name=name,
        description=body.get("description", ""),
        embedding_model=body.get("embedding_model"),
    )
    base_id = base["id"]

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "knowledge.base.created", {"id": base_id, "name": name})

    return JSONResponse({"id": base_id, "base": base}, status_code=201)


async def get_base(request: Request) -> JSONResponse:
    """GET /api/knowledge/bases/{id} (from DB)."""
    base_id = request.path_params["id"]
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    entry = dict(base)
    if base_id == "global":
        kb = _get_knowledge_base(request)
        if kb:
            try:
                entry["chunks_count"] = kb.count()
            except Exception:
                pass

    # Attach sources
    entry["sources"] = await repo.list_sources(base_id)

    return JSONResponse({"base": entry})


async def update_base(request: Request) -> JSONResponse:
    """PUT /api/knowledge/bases/{id} (persisted in DB)."""
    base_id = request.path_params["id"]
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    kwargs = {}
    for key in ("name", "description", "embedding_model"):
        if key in body:
            kwargs[key] = body[key]

    updated = await repo.update_base(base_id, **kwargs)
    return JSONResponse({"ok": True, "base": updated})


async def delete_base(request: Request) -> JSONResponse:
    """DELETE /api/knowledge/bases/{id} (from DB)."""
    base_id = request.path_params["id"]
    if base_id == "global":
        return JSONResponse({"error": "cannot delete the global knowledge base"}, status_code=400)

    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    await repo.delete_base(base_id)

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "knowledge.base.deleted", {"id": base_id, "name": base.get("name")})

    return JSONResponse({"ok": True})


# ── Sources ──────────────────────────────────────────────────

async def add_source(request: Request) -> JSONResponse:
    """POST /api/knowledge/bases/{id}/sources (persisted in DB)."""
    base_id = request.path_params["id"]
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    source_type = body.get("type", "file")  # file, dir, url, repo
    path = body.get("path", "").strip()
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)

    source = await repo.add_source(base_id, path, status="pending")
    source["type"] = source_type
    source["chunks_count"] = 0

    # If this is the global base, ingest content into KnowledgeBase
    if base_id == "global":
        kb = _get_knowledge_base(request)
        if kb is not None:
            try:
                from pathlib import Path
                from amiagi.application.document_ingester import DocumentIngester, ParagraphChunking

                ingester = DocumentIngester(kb, strategy=ParagraphChunking())
                p = Path(path)
                if p.is_file() and p.exists():
                    res = ingester.ingest_file(p)
                    status = "indexed" if not res.errors else "partial"
                    source["status"] = status
                    source["chunks_count"] = res.chunks_count
                    await repo.update_source_status(source["id"], status, indexed_at_now=True)
                elif p.is_dir() and p.exists():
                    results = ingester.ingest_directory(p, glob="**/*")
                    total_chunks = sum(r.chunks_count for r in results)
                    source["status"] = "indexed"
                    source["chunks_count"] = total_chunks
                    await repo.update_source_status(source["id"], "indexed", indexed_at_now=True)
                else:
                    source["status"] = "error"
                    await repo.update_source_status(source["id"], "error")
            except Exception as exc:
                logger.warning("Failed to ingest source %s: %s", path, exc)
                source["status"] = "error"
                await repo.update_source_status(source["id"], "error")

    return JSONResponse({"source": source}, status_code=201)


async def remove_source(request: Request) -> JSONResponse:
    """DELETE /api/knowledge/bases/{id}/sources/{sid} (from DB)."""
    base_id = request.path_params["id"]
    sid = request.path_params["sid"]
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    removed = await repo.remove_source(sid)
    if not removed:
        return JSONResponse({"error": "source not found"}, status_code=404)

    return JSONResponse({"ok": True})


# ── Search & Reindex ─────────────────────────────────────────


async def _reindex_background(repo: KnowledgeRepository, kb, base_id: str, *, hub=None) -> None:
    """Background task — clear knowledge base and re-ingest all sources."""
    try:
        from pathlib import Path
        from amiagi.application.document_ingester import DocumentIngester, ParagraphChunking

        # Clear existing KB entries
        try:
            kb.clear()
        except Exception:
            logger.debug("kb.clear() not available or failed, proceeding with re-ingest")

        sources = await repo.list_sources(base_id)
        ingester = DocumentIngester(kb, strategy=ParagraphChunking())

        for src in sources:
            src_path = src.get("path", "")
            source_id = src.get("id", "")
            if not src_path:
                continue

            try:
                p = Path(src_path)
                if p.is_file() and p.exists():
                    res = await asyncio.to_thread(ingester.ingest_file, p)
                    status = "indexed" if not res.errors else "partial"
                    await repo.update_source_status(source_id, status, indexed_at_now=True)
                elif p.is_dir() and p.exists():
                    results = await asyncio.to_thread(ingester.ingest_directory, p, glob="**/*")
                    await repo.update_source_status(source_id, "indexed", indexed_at_now=True)
                else:
                    await repo.update_source_status(source_id, "error")
            except Exception:
                logger.exception("Failed to re-ingest source %s", src_path)
                try:
                    await repo.update_source_status(source_id, "error")
                except Exception:
                    pass

        if hub is not None:
            hub.broadcast("knowledge.reindex.completed", {"base_id": base_id})

        logger.info("Reindex of base %s completed (%d sources)", base_id, len(sources))

    except Exception:
        logger.exception("Reindex of base %s failed", base_id)
        if hub is not None:
            hub.broadcast("knowledge.reindex.failed", {"base_id": base_id})



async def search_base(request: Request) -> JSONResponse:
    """GET /api/knowledge/bases/{id}/search?q=...&top=5"""
    base_id = request.path_params["id"]
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    query = request.query_params.get("q", "").strip()
    top = min(int(request.query_params.get("top", "5")), 50)
    if not query:
        return JSONResponse({"error": "query parameter 'q' is required"}, status_code=400)

    if base_id == "global":
        kb = _get_knowledge_base(request)
        if kb is None:
            return _no_kb()
        try:
            results = kb.query(query, top_k=top)
            items = [
                {
                    "entry_id": r.entry_id,
                    "text": r.text[:500],
                    "score": round(r.score, 4),
                    "metadata": r.metadata,
                }
                for r in results
            ]
            return JSONResponse({"results": items, "total": len(items)})
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
    else:
        # Non-global bases don't have a real engine yet
        return JSONResponse({"results": [], "total": 0})


async def reindex_base(request: Request) -> JSONResponse:
    """POST /api/knowledge/bases/{id}/reindex"""
    base_id = request.path_params["id"]
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    # For the global base, re-ingest all sources
    kb = None
    if base_id == "global":
        kb = _get_knowledge_base(request)
        if kb is None:
            return _no_kb()

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        hub.broadcast("knowledge.reindex.started", {"base_id": base_id})

    # Launch actual reindexing in background
    if base_id == "global" and kb is not None:
        asyncio.create_task(
            _reindex_background(repo, kb, base_id, hub=hub)
        )

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "knowledge.reindex.started", {"base_id": base_id})

    return JSONResponse({"ok": True, "status": "reindexing"})


async def base_stats(request: Request) -> JSONResponse:
    """GET /api/knowledge/bases/{id}/stats (from DB)."""
    base_id = request.path_params["id"]
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    sources = await repo.list_sources(base_id)
    stats = {
        "id": base_id,
        "name": base.get("name"),
        "engine": base.get("embedding_model") or "tfidf",
        "sources_count": len(sources),
        "chunks_count": 0,
    }

    if base_id == "global":
        kb = _get_knowledge_base(request)
        if kb:
            try:
                stats["chunks_count"] = kb.count()
            except Exception:
                pass

    return JSONResponse({"stats": stats})


async def pipeline_status(request: Request) -> JSONResponse:
    """GET /api/knowledge/pipeline/status"""
    # For now, return idle; a real ingestion pipeline would track state
    return JSONResponse({
        "status": "idle",
        "active_jobs": 0,
        "last_run": None,
    })


# ── Route table ──────────────────────────────────────────────

knowledge_routes: list[Route] = [
    Route("/knowledge", knowledge_page),
    # Pipeline
    Route("/api/knowledge/pipeline/status", pipeline_status, methods=["GET"]),
    # Sources (nested)
    Route("/api/knowledge/bases/{id}/sources/{sid}", remove_source, methods=["DELETE"]),
    Route("/api/knowledge/bases/{id}/sources", add_source, methods=["POST"]),
    # Operations
    Route("/api/knowledge/bases/{id}/reindex", reindex_base, methods=["POST"]),
    Route("/api/knowledge/bases/{id}/search", search_base, methods=["GET"]),
    Route("/api/knowledge/bases/{id}/stats", base_stats, methods=["GET"]),
    # CRUD
    Route("/api/knowledge/bases/{id}", get_base, methods=["GET"]),
    Route("/api/knowledge/bases/{id}", update_base, methods=["PUT"]),
    Route("/api/knowledge/bases/{id}", delete_base, methods=["DELETE"]),
    Route("/api/knowledge/bases", list_bases, methods=["GET"]),
    Route("/api/knowledge/bases", create_base, methods=["POST"]),
]
