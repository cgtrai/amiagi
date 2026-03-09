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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from amiagi.interfaces.web.db.knowledge_repository import (
    KnowledgeRepository,
    GLOBAL_BASE_UUID,
)

logger = logging.getLogger(__name__)

_PIPELINE_DEFAULTS = {
    "chunking": "paragraph",
    "chunk_size": 512,
    "overlap": 64,
    "embedding_model": "tfidf",
}
_SUPPORTED_EMBEDDINGS = {"tfidf"}

_REFRESH_DELTAS = {
    "manual": None,
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


# ── Helpers ──────────────────────────────────────────────────

def _get_knowledge_base(request: Request):
    """Return the global KnowledgeBase from app.state."""
    return getattr(request.app.state, "knowledge_base", None)


def _get_kb_repo(request: Request) -> KnowledgeRepository:
    """Return the DB-backed KnowledgeRepository from app.state."""
    return request.app.state.knowledge_repo


def _no_kb() -> JSONResponse:
    return JSONResponse({"error": "knowledge_base unavailable"}, status_code=503)


def _runtime_not_supported_response(error: str, detail: str, *, extra: dict | None = None) -> JSONResponse:
    payload = {"error": error, "detail": detail}
    if extra:
        payload.update(extra)
    return JSONResponse(payload, status_code=409)


def _resolve_base_id(raw_id: str) -> str:
    """Map the friendly alias 'global' to its well-known UUID."""
    if raw_id == "global":
        return GLOBAL_BASE_UUID
    return raw_id


def _is_global(base_id: str) -> bool:
    """Check whether *base_id* refers to the Global Knowledge Base."""
    return base_id in ("global", GLOBAL_BASE_UUID)


def _get_pipeline_state(request: Request) -> dict:
    state = getattr(request.app.state, "knowledge_pipeline_state", None)
    if state is None:
        state = {
            "status": "idle",
            "active_jobs": 0,
            "refresh_frequency": "manual",
            "last_refresh": None,
            "next_refresh": None,
            "config": dict(_PIPELINE_DEFAULTS),
        }
        request.app.state.knowledge_pipeline_state = state
    else:
        config = state.get("config")
        if not isinstance(config, dict):
            state["config"] = dict(_PIPELINE_DEFAULTS)
        else:
            normalized = dict(_PIPELINE_DEFAULTS)
            normalized.update({k: v for k, v in config.items() if v is not None})
            state["config"] = normalized
    return state


def _pipeline_runtime_available(request: Request) -> bool:
    return _get_knowledge_base(request) is not None


def _coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_pipeline_config(body: dict | None) -> tuple[dict, str | None]:
    raw = body or {}
    chunking = str(raw.get("chunking") or _PIPELINE_DEFAULTS["chunking"]).strip().lower()
    if chunking not in {"paragraph", "sentence", "fixed"}:
        return {}, "invalid_chunking"

    chunk_size = _coerce_int(raw.get("chunk_size"), _PIPELINE_DEFAULTS["chunk_size"])
    overlap = _coerce_int(raw.get("overlap"), _PIPELINE_DEFAULTS["overlap"])
    if chunk_size <= 0:
        return {}, "invalid_chunk_size"
    if overlap < 0:
        return {}, "invalid_overlap"
    if overlap >= chunk_size:
        overlap = max(chunk_size - 1, 0)

    embedding_model = str(raw.get("embedding_model") or raw.get("engine") or _PIPELINE_DEFAULTS["embedding_model"]).strip().lower()
    if embedding_model not in _SUPPORTED_EMBEDDINGS:
        return {}, "unsupported_embedding_model"

    return {
        "chunking": chunking,
        "chunk_size": chunk_size,
        "overlap": overlap,
        "embedding_model": embedding_model,
    }, None


def _build_chunking_strategy(config: dict):
    from amiagi.application.document_ingester import FixedSizeChunking, ParagraphChunking, SentenceChunking

    chunking = config.get("chunking", _PIPELINE_DEFAULTS["chunking"])
    chunk_size = _coerce_int(config.get("chunk_size"), _PIPELINE_DEFAULTS["chunk_size"])
    overlap = _coerce_int(config.get("overlap"), _PIPELINE_DEFAULTS["overlap"])

    if chunking == "fixed":
        return FixedSizeChunking(size=max(chunk_size, 1), overlap=min(max(overlap, 0), max(chunk_size - 1, 0)))
    if chunking == "sentence":
        return SentenceChunking(sentences_per_chunk=max(chunk_size // 256, 1))
    return ParagraphChunking(min_length=max(chunk_size // 8, 1))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _next_refresh_for(freq: str, *, now: datetime | None = None) -> str | None:
    delta = _REFRESH_DELTAS.get(freq)
    if delta is None:
        return None
    base = now or datetime.now(timezone.utc)
    return (base + delta).isoformat()


def _source_size(path_str: str) -> int:
    if not path_str:
        return 0
    path = Path(path_str)
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def _enrich_base(base: dict, sources: list[dict], *, kb=None) -> dict:
    entry = dict(base)
    entry["is_global"] = entry.get("id") == GLOBAL_BASE_UUID
    entry["engine"] = entry.get("embedding_model") or entry.get("engine") or "tfidf"
    entry["sources"] = sources
    entry["total_size_bytes"] = sum(_source_size(src.get("path", "")) for src in sources)
    entry["agents_using"] = entry.get("agents_using") or []
    source_dates = [_parse_iso(src.get("indexed_at") or src.get("created_at")) for src in sources]
    source_dates = [dt for dt in source_dates if dt is not None]
    updated_at = _parse_iso(entry.get("updated_at"))
    last_updated = max([dt for dt in [updated_at, *source_dates] if dt is not None], default=None)
    entry["last_updated"] = last_updated.isoformat() if last_updated else entry.get("updated_at")
    if kb is not None and entry.get("name") == "Global Knowledge Base":
        try:
            entry["chunks_count"] = kb.count()
        except Exception:
            entry["chunks_count"] = entry.get("chunks_count", 0)
    else:
        entry.setdefault("chunks_count", 0)
    entry["document_count"] = entry.get("chunks_count", 0)
    entry["supports_search"] = bool(entry["is_global"] and kb is not None)
    entry["supports_reindex"] = bool(entry["is_global"] and kb is not None)
    return entry


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
        sources = await repo.list_sources(base["id"])
        items.append(_enrich_base(base, sources, kb=kb))

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
        embedding_model=body.get("embedding_model") or body.get("engine"),
    )
    base_id = base["id"]

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "knowledge.base.created", {"id": base_id, "name": name})

    return JSONResponse({"id": base_id, "base": base}, status_code=201)


async def get_base(request: Request) -> JSONResponse:
    """GET /api/knowledge/bases/{id} (from DB)."""
    raw_id = request.path_params["id"]
    base_id = _resolve_base_id(raw_id)
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    kb = _get_knowledge_base(request) if _is_global(raw_id) else None
    entry = _enrich_base(base, await repo.list_sources(base_id), kb=kb)

    return JSONResponse({"base": entry})


async def update_base(request: Request) -> JSONResponse:
    """PUT /api/knowledge/bases/{id} (persisted in DB)."""
    base_id = _resolve_base_id(request.path_params["id"])
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    kwargs = {}
    if "engine" in body and "embedding_model" not in body:
        body["embedding_model"] = body.get("engine")

    for key in ("name", "description", "embedding_model"):
        if key in body:
            kwargs[key] = body[key]

    updated = await repo.update_base(base_id, **kwargs)
    return JSONResponse({"ok": True, "base": updated})


async def delete_base(request: Request) -> JSONResponse:
    """DELETE /api/knowledge/bases/{id} (from DB)."""
    raw_id = request.path_params["id"]
    if _is_global(raw_id):
        return JSONResponse({"error": "cannot delete the global knowledge base"}, status_code=400)

    base_id = _resolve_base_id(raw_id)
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
    raw_id = request.path_params["id"]
    base_id = _resolve_base_id(raw_id)
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

    kb = None
    pipeline_config = dict(_get_pipeline_state(request).get("config") or _PIPELINE_DEFAULTS)
    if _is_global(raw_id):
        kb = _get_knowledge_base(request)
        if kb is None:
            return _runtime_not_supported_response(
                "knowledge_ingest_not_supported",
                "knowledge ingestion runtime is not available in this environment",
                extra={"base_id": base_id, "path": path},
            )

    source = await repo.add_source(base_id, path, status="pending")
    source["type"] = source_type
    source["chunks_count"] = 0
    source["pipeline_config"] = pipeline_config

    # If this is the global base, ingest content into KnowledgeBase
    if _is_global(raw_id):
        try:
            from pathlib import Path
            from amiagi.application.document_ingester import DocumentIngester

            ingester = DocumentIngester(kb, strategy=_build_chunking_strategy(pipeline_config))
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
    base_id = _resolve_base_id(request.path_params["id"])
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
        from amiagi.application.document_ingester import DocumentIngester

        # Clear existing KB entries
        try:
            kb.clear()
        except Exception:
            logger.debug("kb.clear() not available or failed, proceeding with re-ingest")

        sources = await repo.list_sources(base_id)
        pipeline_config = dict(_PIPELINE_DEFAULTS)
        app_state = getattr(repo, "_app_state", None)
        if app_state is not None:
            raw_state = getattr(app_state, "knowledge_pipeline_state", None) or {}
            raw_config = raw_state.get("config") if isinstance(raw_state, dict) else None
            if isinstance(raw_config, dict):
                pipeline_config.update(raw_config)
        ingester = DocumentIngester(kb, strategy=_build_chunking_strategy(pipeline_config))

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
            await hub.broadcast("knowledge.reindex.completed", {"base_id": base_id})

        logger.info("Reindex of base %s completed (%d sources)", base_id, len(sources))

    except Exception:
        logger.exception("Reindex of base %s failed", base_id)
        if hub is not None:
            await hub.broadcast("knowledge.reindex.failed", {"base_id": base_id})



async def search_base(request: Request) -> JSONResponse:
    """GET /api/knowledge/bases/{id}/search?q=...&top=5"""
    raw_id = request.path_params["id"]
    base_id = _resolve_base_id(raw_id)
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    query = request.query_params.get("q", "").strip()
    top = min(int(request.query_params.get("top", "5")), 50)
    if not query:
        return JSONResponse({"error": "query parameter 'q' is required"}, status_code=400)

    if _is_global(raw_id):
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
        return _runtime_not_supported_response(
            "knowledge_search_not_supported",
            "search is only available for the active global knowledge base in this environment",
            extra={"base_id": base_id},
        )


async def reindex_base(request: Request) -> JSONResponse:
    """POST /api/knowledge/bases/{id}/reindex"""
    raw_id = request.path_params["id"]
    base_id = _resolve_base_id(raw_id)
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    # For the global base, re-ingest all sources
    if not _is_global(raw_id):
        return _runtime_not_supported_response(
            "knowledge_reindex_not_supported",
            "reindex is only available for the active global knowledge base in this environment",
            extra={"base_id": base_id},
        )

    kb = _get_knowledge_base(request)
    if kb is None:
        return _runtime_not_supported_response(
            "knowledge_reindex_not_supported",
            "knowledge reindex runtime is not available in this environment",
            extra={"base_id": base_id},
        )

    setattr(repo, "_app_state", request.app.state)

    hub = getattr(request.app.state, "event_hub", None)
    if hub is not None:
        await hub.broadcast("knowledge.reindex.started", {"base_id": base_id})

    # Launch actual reindexing in background
    asyncio.create_task(
        _reindex_background(repo, kb, base_id, hub=hub)
    )

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "knowledge.reindex.started", {"base_id": base_id})

    return JSONResponse({"ok": True, "status": "reindexing"})


async def base_stats(request: Request) -> JSONResponse:
    """GET /api/knowledge/bases/{id}/stats (from DB)."""
    raw_id = request.path_params["id"]
    base_id = _resolve_base_id(raw_id)
    repo = _get_kb_repo(request)
    base = await repo.get_base(base_id)
    if base is None:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    sources = await repo.list_sources(base_id)
    total_size_bytes = sum(_source_size(src.get("path", "")) for src in sources)
    stats = {
        "id": base_id,
        "name": base.get("name"),
        "engine": base.get("embedding_model") or "tfidf",
        "sources_count": len(sources),
        "chunks_count": 0,
        "total_size_bytes": total_size_bytes,
        "agents_using": [],
        "last_updated": _enrich_base(base, sources).get("last_updated"),
    }

    if _is_global(raw_id):
        kb = _get_knowledge_base(request)
        if kb:
            try:
                stats["chunks_count"] = kb.count()
            except Exception:
                pass

    return JSONResponse({"stats": stats})


async def pipeline_status(request: Request) -> JSONResponse:
    """GET /api/knowledge/pipeline/status"""
    state = _get_pipeline_state(request)
    payload = dict(state)
    payload["runtime_available"] = _pipeline_runtime_available(request)
    return JSONResponse(payload)


async def pipeline_schedule(request: Request) -> JSONResponse:
    """PUT /api/knowledge/pipeline/schedule"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    frequency = str(body.get("frequency") or "manual").strip().lower()
    if frequency not in _REFRESH_DELTAS:
        return JSONResponse({"error": "invalid_frequency"}, status_code=400)

    config, config_error = _normalize_pipeline_config(body)
    if config_error:
        return JSONResponse({"error": config_error}, status_code=400)

    state = _get_pipeline_state(request)
    state["refresh_frequency"] = frequency
    state["next_refresh"] = _next_refresh_for(frequency)
    state["config"] = config

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "knowledge.pipeline.schedule", {"frequency": frequency, **config})

    return JSONResponse({"ok": True, **state})


async def pipeline_refresh(request: Request) -> JSONResponse:
    """POST /api/knowledge/pipeline/refresh"""
    kb = _get_knowledge_base(request)
    if kb is None:
        return _runtime_not_supported_response(
            "knowledge_refresh_not_supported",
            "knowledge refresh runtime is not available in this environment",
            extra={"base_id": GLOBAL_BASE_UUID},
        )

    state = _get_pipeline_state(request)
    state["status"] = "indexing"
    state["active_jobs"] = 1
    state["last_refresh"] = datetime.now(timezone.utc).isoformat()
    state["next_refresh"] = _next_refresh_for(state.get("refresh_frequency", "manual"), now=datetime.now(timezone.utc))

    repo = _get_kb_repo(request)
    hub = getattr(request.app.state, "event_hub", None)
    base_id = GLOBAL_BASE_UUID
    setattr(repo, "_app_state", request.app.state)

    async def _run_refresh() -> None:
        try:
            if kb is not None:
                await _reindex_background(repo, kb, base_id, hub=hub)
        finally:
            state["status"] = "idle"
            state["active_jobs"] = 0

    asyncio.create_task(_run_refresh())

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "knowledge.pipeline.refresh", {"base_id": base_id})

    return JSONResponse({"ok": True, **state})


# ── Route table ──────────────────────────────────────────────

knowledge_routes: list[Route] = [
    Route("/knowledge", knowledge_page),
    # Pipeline
    Route("/api/knowledge/pipeline/status", pipeline_status, methods=["GET"]),
    Route("/api/knowledge/pipeline/schedule", pipeline_schedule, methods=["PUT"]),
    Route("/api/knowledge/pipeline/refresh", pipeline_refresh, methods=["POST"]),
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
