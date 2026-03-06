"""Extended model management API routes for Model Hub.

Endpoints:
    POST   /api/models/pull             — trigger Ollama model pull (streaming)
    GET    /api/models/vram             — VRAM usage / memory info
    POST   /api/models/benchmark        — run a quick benchmark for a model
    DELETE  /api/models/local/{name}    — delete a local model from Ollama
    GET    /api/models/local            — list local models
    GET    /api/models/cloud            — list configured cloud models
    POST   /api/models/cloud            — add / update a cloud model definition
    DELETE  /api/models/cloud/{provider}/{model} — remove a cloud model
    POST   /api/models/cloud/test       — test connection to a cloud provider
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

_CLOUD_CONFIG_PATH = Path("data/cloud_models.json")


# ── Helpers: cloud config persistence ─────────────────────────

def _read_cloud_config() -> list[dict]:
    """Read cloud_models.json — list of provider definitions."""
    if not _CLOUD_CONFIG_PATH.exists():
        return []
    try:
        data = json.loads(_CLOUD_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_cloud_config(models: list[dict]) -> None:
    _CLOUD_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CLOUD_CONFIG_PATH.write_text(
        json.dumps(models, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── POST /api/models/pull ────────────────────────────────────

async def model_pull(request: Request) -> StreamingResponse | JSONResponse:
    """Trigger Ollama to pull a model by name — streams progress via SSE.

    Body: { "model": "llama3:8b" }
    Returns: text/event-stream with JSON progress lines.
    """
    body = await request.json()
    model_name = (body.get("model") or body.get("name", "")).strip()

    if not model_name:
        return JSONResponse({"error": "model_name_required"}, status_code=400)

    ollama = getattr(request.app.state, "ollama_client", None)
    if ollama is None:
        return JSONResponse(
            {"error": "ollama_client_not_configured"},
            status_code=503,
        )

    async def _stream_pull():
        """Generator that streams pull progress from Ollama."""
        import httpx

        base_url = getattr(ollama, "base_url", "http://localhost:11434")
        url = f"{base_url.rstrip('/')}/api/pull"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
                async with client.stream(
                    "POST",
                    url,
                    json={"name": model_name, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        event = {
                            "status": chunk.get("status", ""),
                            "total": chunk.get("total", 0),
                            "completed": chunk.get("completed", 0),
                        }
                        yield f"data: {json.dumps(event)}\n\n"

            # Final done event
            yield f"data: {json.dumps({'status': 'success', 'done': True})}\n\n"

            # Log the pull action
            try:
                from amiagi.interfaces.web.audit.log_helpers import log_action
                await log_action(request, "models.pull", {
                    "model": model_name,
                    "status": "success",
                })
            except Exception:
                pass

        except Exception as exc:
            logger.exception("model.pull streaming failed for %s", model_name)
            yield f"data: {json.dumps({'status': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(
        _stream_pull(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── GET /api/models/vram ─────────────────────────────────────

async def model_vram(request: Request) -> JSONResponse:
    """Return VRAM / memory info for currently loaded models.

    Uses Ollama's /api/ps endpoint if available.
    """
    ollama = getattr(request.app.state, "ollama_client", None)
    if ollama is None:
        return JSONResponse(
            {"error": "ollama_client_not_configured", "models": []},
            status_code=503,
        )

    try:
        # /api/ps returns running models with memory info
        ps_data = ollama._get_json("/api/ps")
        running = ps_data.get("models", [])

        models_info: list[dict] = []
        total_vram_used = 0
        for m in running:
            if not isinstance(m, dict):
                continue
            sz = m.get("size_vram", m.get("size", 0))
            total_vram_used += sz
            models_info.append({
                "name": m.get("name", ""),
                "size": m.get("size", 0),
                "size_vram": sz,
                "digest": m.get("digest", ""),
                "expires_at": m.get("expires_at", ""),
            })

        # Try to get total GPU VRAM from VRAMScheduler if available
        gpu_total = 0
        vram_sched = getattr(request.app.state, "vram_scheduler", None)
        if vram_sched is not None:
            gpu_total = getattr(vram_sched, "total_vram_mb", 0) * 1024 * 1024

        return JSONResponse({
            "ok": True,
            "models": models_info,
            "gpu_total": gpu_total,
            "gpu_used": total_vram_used,
        })

    except Exception as exc:
        logger.warning("vram query failed: %s", exc)
        return JSONResponse({"ok": False, "models": [], "error": str(exc)})


# ── POST /api/models/benchmark ───────────────────────────────

async def model_benchmark(request: Request) -> JSONResponse:
    """Run a simple benchmark (single prompt, measure time-to-first-token
    and tokens/sec) for a given model.

    Body: { "model": "llama3:8b", "prompt": "Hello" }
    """
    body = await request.json()
    model_name = body.get("model", "").strip()
    prompt = body.get("prompt", "Say hello in one sentence.").strip()

    if not model_name:
        return JSONResponse({"error": "model_name_required"}, status_code=400)

    ollama = getattr(request.app.state, "ollama_client", None)
    if ollama is None:
        return JSONResponse(
            {"error": "ollama_client_not_configured"},
            status_code=503,
        )

    try:
        start = time.perf_counter()

        result = ollama._post_json("/api/generate", {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
        })

        elapsed = time.perf_counter() - start
        response_text = result.get("response", "")
        eval_count = result.get("eval_count", len(response_text.split()))
        eval_duration_ns = result.get("eval_duration", 0)

        tokens_per_sec = (
            (eval_count / (eval_duration_ns / 1e9))
            if eval_duration_ns > 0
            else (eval_count / elapsed if elapsed > 0 else 0)
        )

        return JSONResponse({
            "ok": True,
            "model": model_name,
            "elapsed_seconds": round(elapsed, 3),
            "tokens": eval_count,
            "tokens_per_second": round(tokens_per_sec, 1),
            "response_preview": response_text[:200],
        })

    except Exception as exc:
        logger.exception("model.benchmark failed for %s", model_name)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── DELETE /api/models/local/{name} ───────────────────────────

async def model_delete(request: Request) -> JSONResponse:
    """Delete a local model from Ollama."""
    model_name = request.path_params["name"]

    ollama = getattr(request.app.state, "ollama_client", None)
    if ollama is None:
        return JSONResponse({"error": "ollama_client_not_configured"}, status_code=503)

    try:
        from urllib.request import Request as UrlRequest, urlopen
        url = f"{ollama.base_url.rstrip('/')}/api/delete"
        req = UrlRequest(
            url=url,
            data=json.dumps({"name": model_name}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="DELETE",
        )
        with urlopen(req, timeout=30) as resp:
            resp.read()

        from amiagi.interfaces.web.audit.log_helpers import log_action
        await log_action(request, "models.delete", {"model": model_name})

        return JSONResponse({"ok": True, "model": model_name})
    except Exception as exc:
        logger.exception("model.delete failed for %s", model_name)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── GET /api/models/local ────────────────────────────────────

async def local_models_list(request: Request) -> JSONResponse:
    """List local models using ``ollama list`` shell command.

    Falls back to the Ollama API ``/api/tags`` if the CLI is unavailable.
    """
    models: list[dict] = []
    try:
        completed = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=15,
        )
        if completed.returncode == 0:
            lines = completed.stdout.strip().splitlines()
            # First line is header: NAME  ID  SIZE  MODIFIED
            for line in lines[1:]:
                parts = line.split()
                if not parts:
                    continue
                name = parts[0]
                size = parts[2] + " " + parts[3] if len(parts) >= 4 else ""
                models.append({"name": name, "size": size, "source": "ollama"})
        else:
            # Fallback to API
            ollama = getattr(request.app.state, "ollama_client", None)
            if ollama:
                for m in ollama.list_models():
                    models.append({"name": m, "size": "", "source": "ollama"})
    except FileNotFoundError:
        # ollama CLI not installed — fallback to API
        ollama = getattr(request.app.state, "ollama_client", None)
        if ollama:
            try:
                for m in ollama.list_models():
                    models.append({"name": m, "size": "", "source": "ollama"})
            except Exception:
                pass
    except Exception as exc:
        logger.warning("ollama list failed: %s", exc)

    return JSONResponse({"ok": True, "models": models})


# ── GET /api/models/cloud ────────────────────────────────────

async def cloud_models_list(request: Request) -> JSONResponse:
    """Return all configured cloud model definitions."""
    models = _read_cloud_config()
    return JSONResponse({"ok": True, "models": models})


# ── POST /api/models/cloud ───────────────────────────────────

async def cloud_model_save(request: Request) -> JSONResponse:
    """Add or update a cloud model definition.

    Body: {
        "provider": "openai",
        "model": "gpt-5-mini",
        "display_name": "GPT-5 Mini",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-...",
        "enabled": true
    }
    """
    body = await request.json()
    provider = (body.get("provider") or "").strip().lower()
    model = (body.get("model") or "").strip()
    api_key = (body.get("api_key") or "").strip()

    if not provider or not model:
        return JSONResponse({"error": "provider_and_model_required"}, status_code=400)
    if not api_key:
        return JSONResponse({"error": "api_key_required"}, status_code=400)

    entry = {
        "provider": provider,
        "model": model,
        "display_name": body.get("display_name", f"{provider}/{model}").strip(),
        "base_url": (body.get("base_url") or "").strip(),
        "api_key": api_key,
        "enabled": bool(body.get("enabled", True)),
    }

    models = _read_cloud_config()
    # Upsert: replace if same provider+model exists
    models = [m for m in models if not (m.get("provider") == provider and m.get("model") == model)]
    models.append(entry)
    _write_cloud_config(models)

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "models.cloud.save", {
        "provider": provider, "model": model,
    })

    return JSONResponse({"ok": True, "entry": {**entry, "api_key": _mask_key(api_key)}})


# ── DELETE /api/models/cloud/{provider}/{model} ──────────────

async def cloud_model_delete(request: Request) -> JSONResponse:
    """Remove a cloud model definition."""
    provider = request.path_params["provider"]
    model_name = request.path_params["model"]

    models = _read_cloud_config()
    before = len(models)
    models = [m for m in models if not (m.get("provider") == provider and m.get("model") == model_name)]

    if len(models) == before:
        return JSONResponse({"error": "not_found"}, status_code=404)

    _write_cloud_config(models)

    from amiagi.interfaces.web.audit.log_helpers import log_action
    await log_action(request, "models.cloud.delete", {
        "provider": provider, "model": model_name,
    })

    return JSONResponse({"ok": True})


# ── POST /api/models/cloud/test ──────────────────────────────

async def cloud_model_test(request: Request) -> JSONResponse:
    """Test connectivity & auth for a cloud API provider.

    Body: { "provider": "openai", "base_url": "...", "api_key": "sk-..." }
    Returns: { "ok": true, "latency_ms": 340, "models": [...] }
    """
    body = await request.json()
    provider = (body.get("provider") or "").strip().lower()
    api_key = (body.get("api_key") or "").strip()
    base_url = (body.get("base_url") or "").strip()

    if not api_key:
        return JSONResponse({"error": "api_key_required"}, status_code=400)

    # Default base URLs per provider
    if not base_url:
        defaults = {
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
        }
        base_url = defaults.get(provider, "https://api.openai.com/v1")

    try:
        from urllib.request import Request as UrlRequest, urlopen
        from urllib.error import HTTPError, URLError
        import socket

        start = time.perf_counter()

        if provider == "anthropic":
            # Anthropic: GET with x-api-key and anthropic-version headers
            url = f"{base_url.rstrip('/')}/v1/models"
            req = UrlRequest(url=url, headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }, method="GET")
        else:
            # OpenAI-compatible: GET /models with Bearer token
            url = f"{base_url.rstrip('/')}/models"
            req = UrlRequest(url=url, headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }, method="GET")

        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        latency = round((time.perf_counter() - start) * 1000)

        # Parse model list from response
        available: list[str] = []
        if isinstance(data, dict):
            items = data.get("data", data.get("models", []))
            for m in items:
                if isinstance(m, dict):
                    available.append(m.get("id", m.get("name", "")))
                elif isinstance(m, str):
                    available.append(m)

        return JSONResponse({
            "ok": True,
            "latency_ms": latency,
            "models": available[:50],  # cap at 50
        })

    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="ignore")[:300]
        return JSONResponse({
            "ok": False,
            "error": f"HTTP {exc.code}",
            "detail": body_text,
        }, status_code=200)  # 200 so JS can read the JSON
    except (URLError, socket.timeout, TimeoutError) as exc:
        return JSONResponse({
            "ok": False,
            "error": str(getattr(exc, "reason", exc)),
        }, status_code=200)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)


def _mask_key(key: str) -> str:
    """Mask an API key for safe display: ``sk-...abcd``."""
    if not key or len(key) < 8:
        return "***"
    return f"{key[:4]}…{key[-4:]}"


# ── Route table ──────────────────────────────────────────────

model_hub_routes: list[Route] = [
    Route("/api/models/pull", model_pull, methods=["POST"]),
    Route("/api/models/vram", model_vram, methods=["GET"]),
    Route("/api/models/benchmark", model_benchmark, methods=["POST"]),
    Route("/api/models/local", local_models_list, methods=["GET"]),
    Route("/api/models/local/{name:path}", model_delete, methods=["DELETE"]),
    Route("/api/models/cloud", cloud_models_list, methods=["GET"]),
    Route("/api/models/cloud", cloud_model_save, methods=["POST"]),
    Route("/api/models/cloud/test", cloud_model_test, methods=["POST"]),
    Route("/api/models/cloud/{provider}/{model:path}", cloud_model_delete, methods=["DELETE"]),
]
