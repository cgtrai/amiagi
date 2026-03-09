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
import subprocess
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from amiagi.interfaces.web.routes.model_hub_routes import _read_cloud_config

logger = logging.getLogger(__name__)

_MODEL_CONFIG_PATH = Path("data/model_config.json")


def _apply_live_model(request: Request, agent_id: str, model_name: str) -> None:
    """Hot-swap the running OllamaClient model so the change is immediate."""
    if agent_id != "polluks":
        return
    adapter = getattr(request.app.state, "web_adapter", None)
    if adapter is None:
        return
    try:
        from amiagi.interfaces.shared_cli_helpers import _set_executor_model

        chat_svc = adapter.router_engine.chat_service
        ok, _prev = _set_executor_model(chat_svc, model_name)
        if ok:
            logger.info("Live model updated to '%s' for %s", model_name, agent_id)
        else:
            logger.warning("Failed to live-update model for %s", agent_id)
    except Exception as exc:
        logger.warning("_apply_live_model error: %s", exc)


# ------------------------------------------------------------------
# GET /models — available models
# ------------------------------------------------------------------

async def list_models(request: Request) -> Response:
    """Return all available models: Ollama-discovered + config-defined."""
    catalog = _build_model_catalog(request)
    return JSONResponse(catalog)


# ------------------------------------------------------------------
# GET /models/config — raw model_config.json
# ------------------------------------------------------------------

async def get_model_config(request: Request) -> Response:
    """Return the current model_config.json contents."""
    config = _read_model_config()
    if config is None:
        return JSONResponse({"error": "config_not_found"}, status_code=404)
    return JSONResponse(_normalize_model_config(config))


# ------------------------------------------------------------------
# PUT /models/config — update model_config.json
# ------------------------------------------------------------------

async def update_model_config(request: Request) -> Response:
    """Overwrite model_config.json with the provided body."""
    body = await request.json()

    filtered = _flatten_model_config_payload(body)

    if not filtered:
        return JSONResponse({"error": "no_valid_keys"}, status_code=400)

    # Merge with existing config
    existing = _read_model_config() or {}
    existing.update(filtered)
    _write_model_config(existing)

    _apply_registry_model_updates(request, existing, filtered)

    # Log activity
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is not None:
        try:
            user = getattr(request.state, "user", None)
            user_id = str(user.user_id) if user else "anonymous"
            await activity_logger.log(
                user_id=user_id,
                action="models.config_update",
                detail={"keys_updated": list(filtered.keys())},
            )
        except Exception:
            pass

    return JSONResponse({"status": "updated", "config": _normalize_model_config(existing)})


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

    _persist_agent_model(agent_id, model_name, model_backend or getattr(agent, "model_backend", "ollama"))

    # Update the live OllamaClient so the change takes effect immediately
    _apply_live_model(request, agent_id, model_name)

    # Log activity
    activity_logger = getattr(request.app.state, "activity_logger", None)
    if activity_logger is not None:
        try:
            user = getattr(request.state, "user", None)
            user_id = str(user.user_id) if user else "anonymous"
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


def _flatten_model_config_payload(body: dict[str, Any]) -> dict[str, str]:
    """Accept both flat and nested model config payloads."""
    filtered: dict[str, str] = {}
    for key, value in body.items():
        if key in {"polluks", "kastor"} and isinstance(value, dict):
            model_name = str(value.get("model_name", "") or "").strip()
            source = str(value.get("source", "") or "").strip()
            if model_name:
                filtered[f"{key}_model"] = model_name
            if source:
                filtered[f"{key}_source"] = source
            continue
        if key.endswith("_model") or key.endswith("_source"):
            text = str(value or "").strip()
            if text:
                filtered[key] = text
    return filtered


def _normalize_model_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a backward-compatible flat+nested view of model config."""
    normalized = dict(config)
    for agent_id in ("polluks", "kastor"):
        normalized[agent_id] = {
            "model_name": str(config.get(f"{agent_id}_model", "") or ""),
            "source": str(config.get(f"{agent_id}_source", "ollama") or "ollama"),
        }
    return normalized


def _persist_agent_model(agent_id: str, model_name: str, model_backend: str) -> None:
    """Persist agent model assignment to disk so it survives reloads."""
    config = _read_model_config() or {}
    config[f"{agent_id}_model"] = model_name
    config[f"{agent_id}_source"] = model_backend or "ollama"
    _write_model_config(config)


def _apply_registry_model_updates(
    request: Request,
    full_config: dict[str, Any],
    changed: dict[str, str],
) -> None:
    """Keep runtime registry and live executor aligned with persisted config."""
    registry = getattr(request.app.state, "agent_registry", None)
    for key, value in changed.items():
        if not key.endswith("_model") or not value:
            continue
        agent_id = key[:-6]
        backend = str(full_config.get(f"{agent_id}_source", "ollama") or "ollama")
        if registry is not None:
            try:
                descriptor = registry.get(agent_id)
                if descriptor is not None:
                    registry.update_model(agent_id, value, model_backend=backend)
            except Exception as exc:
                logger.warning("Failed to sync registry model for %s: %s", agent_id, exc)
        if agent_id == "polluks":
            _apply_live_model(request, agent_id, value)


def _list_ollama_models(request: Request) -> list[str]:
    ollama_models: list[str] = []
    try:
        completed = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode == 0:
            lines = completed.stdout.strip().splitlines()
            for line in lines[1:]:
                parts = line.split()
                if parts:
                    ollama_models.append(parts[0])
        else:
            logger.warning("`ollama list` returned %s", completed.returncode)
    except FileNotFoundError:
        logger.info("`ollama` CLI not available, falling back to ollama_client.list_models()")
    except Exception as exc:
        logger.warning("Failed to list Ollama models via CLI: %s", exc)

    ollama_client = getattr(request.app.state, "ollama_client", None)
    if ollama_client is not None:
        try:
            raw_models = ollama_client.list_models()
            for model in raw_models:
                if isinstance(model, str):
                    ollama_models.append(model)
                elif isinstance(model, dict):
                    name = str(model.get("name", "") or "")
                    if name:
                        ollama_models.append(name)
                else:
                    name = str(getattr(model, "name", "") or "")
                    if name:
                        ollama_models.append(name)
        except Exception as exc:
            logger.warning("Failed to list Ollama models from client fallback: %s", exc)

    unique_models: list[str] = []
    seen: set[str] = set()
    for model in ollama_models:
        model_name = str(model or "").strip()
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)
        unique_models.append(model_name)
    return unique_models


def _list_cloud_models() -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, str]] = []
    for item in list(_read_cloud_config()):
        provider = str(item.get("provider", "") or "").strip().lower()
        model = str(item.get("model", "") or "").strip()
        if not provider or not model:
            continue
        key = (provider, model)
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            "provider": provider,
            "model": model,
            "display_name": str(item.get("display_name", model) or model),
        })
    return merged


def _build_model_catalog(request: Request) -> dict[str, Any]:
    """Build a single source of truth for all web model selectors."""
    ollama_models = _list_ollama_models(request)
    cloud_models = _list_cloud_models()
    config = _read_model_config() or {}
    registry = getattr(request.app.state, "agent_registry", None)

    all_models: list[dict[str, str]] = [
        {
            "name": model,
            "label": model,
            "backend": "ollama",
            "group": "local",
            "provider": "ollama",
        }
        for model in ollama_models
    ]
    all_models.extend(
        {
            "name": item["model"],
            "label": item["display_name"],
            "backend": item["provider"],
            "group": "cloud",
            "provider": item["provider"],
        }
        for item in cloud_models
    )

    assignments: list[dict[str, str]] = []
    seen_assignments: set[str] = set()
    agent_ids = {
        key[:-6]
        for key in config.keys()
        if key.endswith("_model") and config.get(key)
    }
    if registry is not None:
        try:
            agent_ids.update(descriptor.agent_id for descriptor in registry.list_all())
        except Exception:
            pass

    for agent_id in sorted(agent_ids):
        descriptor = _registry_descriptor(registry, agent_id)
        model_name = str(
            (getattr(descriptor, "model_name", "") if descriptor is not None else "")
            or config.get(f"{agent_id}_model", "")
            or ""
        )
        if not model_name or agent_id in seen_assignments:
            continue
        seen_assignments.add(agent_id)
        backend = str(
            (getattr(descriptor, "model_backend", "") if descriptor is not None else "")
            or config.get(f"{agent_id}_source", "ollama")
            or "ollama"
        )
        assignments.append({
            "agent_id": agent_id,
            "name": getattr(descriptor, "name", agent_id) if descriptor is not None else agent_id,
            "role": getattr(getattr(descriptor, "role", None), "value", "") if descriptor is not None else "",
            "model_name": model_name,
            "model_backend": backend,
            "source": backend,
            "persisted": f"{agent_id}_model" in config,
        })

    return {
        "ollama_models": ollama_models,
        "cloud_models": cloud_models,
        "config_models": [
            {
                "name": item["model_name"],
                "source": item["source"],
                "role": item["agent_id"],
            }
            for item in assignments
        ],
        "agent_assignments": assignments,
        "all_models": all_models,
        "backends": ["ollama"] + [
            provider
            for provider in sorted({item["provider"] for item in cloud_models})
            if provider != "ollama"
        ],
        "config": _normalize_model_config(config),
    }


def _registry_descriptor(registry: Any, agent_id: str) -> Any | None:
    """Return a registry descriptor only when it clearly matches the requested agent."""
    if registry is None:
        return None
    try:
        descriptor = registry.get(agent_id)
    except Exception:
        return None
    if descriptor is None:
        return None
    descriptor_id = getattr(descriptor, "agent_id", "")
    return descriptor if isinstance(descriptor_id, str) and descriptor_id == agent_id else None


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
