"""Model management routes — list models, assign to agents, config CRUD.

Routes:
- GET  /models             — available models (Ollama + config)
- GET  /models/config      — raw model_config.json
- PUT  /models/config      — update model_config.json
- GET  /models/ollama/status — ping Ollama, list pulled models
- POST /agents/{agent_id}/model — change agent model assignment
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

_MODEL_CONFIG_PATH = Path("data/model_config.json")


# ------------------------------------------------------------------
# GET /models — available models
# ------------------------------------------------------------------

async def list_models(request: Request) -> Response:
    """Return all available models: Ollama-discovered + config-defined."""
    ollama_models: list[str] = []

    # Try to get Ollama models if client available
    ollama_client = getattr(request.app.state, "ollama_client", None)
    if ollama_client is not None:
        try:
            ollama_models = ollama_client.list_models()
        except Exception as exc:
            logger.warning("Failed to list Ollama models: %s", exc)

    # Read config-defined models
    config_models: list[dict[str, str]] = []
    config = _read_model_config()
    if config:
        for key in ("polluks_model", "kastor_model"):
            model = config.get(key, "")
            source = config.get(key.replace("_model", "_source"), "ollama")
            if model:
                config_models.append({"name": model, "source": source, "role": key.replace("_model", "")})

    return JSONResponse({
        "ollama_models": ollama_models,
        "config_models": config_models,
    })


# ------------------------------------------------------------------
# GET /models/config — raw model_config.json
# ------------------------------------------------------------------

async def get_model_config(request: Request) -> Response:
    """Return the current model_config.json contents."""
    config = _read_model_config()
    if config is None:
        return JSONResponse({"error": "config_not_found"}, status_code=404)
    return JSONResponse(config)


# ------------------------------------------------------------------
# PUT /models/config — update model_config.json
# ------------------------------------------------------------------

async def update_model_config(request: Request) -> Response:
    """Overwrite model_config.json with the provided body."""
    body = await request.json()

    # Validate required keys
    allowed_keys = {"polluks_model", "polluks_source", "kastor_model", "kastor_source"}
    filtered = {k: v for k, v in body.items() if k in allowed_keys}

    if not filtered:
        return JSONResponse({"error": "no_valid_keys"}, status_code=400)

    # Merge with existing config
    existing = _read_model_config() or {}
    existing.update(filtered)
    _write_model_config(existing)

    # Log activity
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is not None:
        user = getattr(request.state, "user", None)
        user_id = str(user.id) if user else "anonymous"
        try:
            await activity_logger.log(
                user_id=user_id,
                action="models.config_update",
                detail={"keys_updated": list(filtered.keys())},
            )
        except Exception:
            pass

    return JSONResponse({"status": "updated", "config": existing})


# ------------------------------------------------------------------
# GET /models/ollama/status — Ollama health + models
# ------------------------------------------------------------------

async def ollama_status(request: Request) -> Response:
    """Ping Ollama and return its status + model list."""
    ollama_client = getattr(request.app.state, "ollama_client", None)
    if ollama_client is None:
        return JSONResponse({
            "available": False,
            "error": "ollama_client_not_configured",
            "models": [],
        })

    try:
        alive = ollama_client.ping()
        models = ollama_client.list_models() if alive else []
        return JSONResponse({
            "available": alive,
            "models": models,
            "base_url": ollama_client.base_url,
        })
    except Exception as exc:
        return JSONResponse({
            "available": False,
            "error": str(exc),
            "models": [],
        })


# ------------------------------------------------------------------
# POST /agents/{agent_id}/model — change agent model
# ------------------------------------------------------------------

async def assign_agent_model(request: Request) -> Response:
    """Change the model assigned to an agent."""
    agent_id = request.path_params["agent_id"]
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return JSONResponse({"error": "agent_registry_not_available"}, status_code=503)

    body = await request.json()
    model_name = body.get("model_name", "").strip()
    model_backend = body.get("model_backend", "").strip()

    if not model_name:
        return JSONResponse({"error": "model_name_required"}, status_code=400)

    agent = registry.get(agent_id)
    if agent is None:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    try:
        registry.update_model(agent_id, model_name, model_backend=model_backend)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    # Log activity
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is not None:
        user = getattr(request.state, "user", None)
        user_id = str(user.id) if user else "anonymous"
        try:
            await activity_logger.log(
                user_id=user_id,
                action="agent.model_changed",
                detail={"agent_id": agent_id, "model_name": model_name, "model_backend": model_backend},
            )
        except Exception:
            pass

    updated = registry.get(agent_id)
    return JSONResponse({
        "status": "ok",
        "agent_id": agent_id,
        "model_name": updated.model_name if updated else model_name,
        "model_backend": updated.model_backend if updated else model_backend,
    })


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _read_model_config() -> dict[str, Any] | None:
    """Read model_config.json from disk."""
    if not _MODEL_CONFIG_PATH.exists():
        return None
    try:
        return json.loads(_MODEL_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_model_config(config: dict[str, Any]) -> None:
    """Write model_config.json to disk."""
    _MODEL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MODEL_CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------
# Route list
# ------------------------------------------------------------------

model_routes = [
    Route("/models", list_models, methods=["GET"]),
    Route("/models/config", get_model_config, methods=["GET"]),
    Route("/models/config", update_model_config, methods=["PUT"]),
    Route("/models/ollama/status", ollama_status, methods=["GET"]),
    Route("/agents/{agent_id}/model", assign_agent_model, methods=["POST"]),
]
