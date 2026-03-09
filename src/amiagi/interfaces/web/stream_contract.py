"""Shared stream metadata contract for web/operator event rendering."""

from __future__ import annotations

from typing import Any


_PANEL_AGENT_MAP = {
    "executor_log": "polluks",
    "user_model_log": "polluks",
    "supervisor_log": "kastor",
}

_PANEL_STREAM_META = {
    "executor_log": {"channel": "executor", "source_kind": "agent", "source_label": "Polluks"},
    "user_model_log": {"channel": "user", "source_kind": "operator", "source_label": "Operator"},
    "supervisor_log": {"channel": "supervisor", "source_kind": "agent", "source_label": "Kastor"},
    "model": {"channel": "executor", "source_kind": "agent", "source_label": "Model"},
    "system": {"channel": "system", "source_kind": "system", "source_label": "System"},
}

_ACTOR_AGENT_MAP = {
    "creator": "polluks",
    "polluks": "polluks",
    "supervisor": "kastor",
    "kastor": "kastor",
}

_ACTOR_STREAM_META = {
    "creator": {"channel": "executor", "source_kind": "agent", "source_label": "Polluks"},
    "polluks": {"channel": "executor", "source_kind": "agent", "source_label": "Polluks"},
    "supervisor": {"channel": "supervisor", "source_kind": "agent", "source_label": "Kastor"},
    "kastor": {"channel": "supervisor", "source_kind": "agent", "source_label": "Kastor"},
    "router": {"channel": "system", "source_kind": "system", "source_label": "Router"},
    "terminal": {"channel": "system", "source_kind": "system", "source_label": "Terminal"},
}


def infer_agent_id(*, panel: str | None = None, actor: str | None = None) -> str | None:
    if panel is not None:
        return _PANEL_AGENT_MAP.get(str(panel).lower())
    if actor is not None:
        return _ACTOR_AGENT_MAP.get(str(actor).lower())
    return None


def stream_meta_for_panel(panel: str | None) -> dict[str, Any]:
    key = str(panel or "").lower()
    payload = dict(_PANEL_STREAM_META.get(key, {"channel": "system", "source_kind": "system", "source_label": key or "System"}))
    agent_id = infer_agent_id(panel=panel)
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return payload


def stream_meta_for_actor(actor: str | None) -> dict[str, Any]:
    key = str(actor or "").lower()
    payload = dict(_ACTOR_STREAM_META.get(key, {"channel": "system", "source_kind": "system", "source_label": key or "System"}))
    agent_id = infer_agent_id(actor=actor)
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return payload


def summarize_log_event(panel: str, message: str, source_label: str | None = None) -> str:
    resolved_label = source_label or str(stream_meta_for_panel(panel).get("source_label", "System"))
    cleaned = str(message or "").strip()
    if not cleaned:
        return resolved_label
    return f"{resolved_label}: {cleaned}"


def summarize_actor_state(actor: str, state: str, event: str, source_label: str | None = None) -> str:
    resolved_label = source_label or str(stream_meta_for_actor(actor).get("source_label", actor or "Actor"))
    parts = [resolved_label.strip(), str(state or "").strip(), str(event or "").strip()]
    return " · ".join(part for part in parts if part)