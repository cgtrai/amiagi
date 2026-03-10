"""Shared stream metadata contract for web/operator event rendering."""

from __future__ import annotations

from typing import Any


SUPERVISOR_STREAM_EVENT_CATALOG: tuple[dict[str, str], ...] = (
    {"type": "stream.config", "category": "protocol", "producer": "ws.events", "audience": "supervisor+agents"},
    {"type": "stream.history", "category": "protocol", "producer": "ws.events", "audience": "supervisor+agents"},
    {"type": "stream.history.truncated", "category": "protocol", "producer": "live-stream", "audience": "supervisor+agents"},
    {"type": "ping", "category": "protocol", "producer": "ws.events", "audience": "transport-only"},
    {"type": "pong", "category": "protocol", "producer": "browser", "audience": "transport-only"},
    {"type": "log", "category": "communication", "producer": "event-bus:web-adapter", "audience": "supervisor-or-agent"},
    {"type": "actor_state", "category": "communication", "producer": "event-bus:web-adapter", "audience": "supervisor-or-agent"},
    {"type": "supervisor_message", "category": "communication", "producer": "event-bus:web-adapter", "audience": "supervisor+kastor"},
    {"type": "error", "category": "technical", "producer": "event-bus:web-adapter", "audience": "supervisor-or-agent"},
    {"type": "cycle_finished", "category": "technical", "producer": "event-bus:web-adapter", "audience": "supervisor"},
    {"type": "operator.input.accepted", "category": "operator", "producer": "system_routes", "audience": "supervisor-or-agent"},
    {"type": "operator.command.executed", "category": "operator", "producer": "system_routes", "audience": "supervisor"},
    {"type": "system.current_task.paused", "category": "operator", "producer": "system_routes", "audience": "supervisor-or-agent"},
    {"type": "system.current_task.stopped", "category": "operator", "producer": "system_routes", "audience": "supervisor-or-agent"},
    {"type": "system.current_task.retried", "category": "operator", "producer": "system_routes", "audience": "supervisor-or-agent"},
    {"type": "system.reset", "category": "operator", "producer": "supervisor.js", "audience": "supervisor"},
    {"type": "inbox.delegated", "category": "operator", "producer": "system_routes", "audience": "supervisor-or-agent"},
    {"type": "agent.spawned", "category": "operator", "producer": "system_routes", "audience": "supervisor-or-agent"},
    {"type": "agent.spawn.manual", "category": "operator", "producer": "supervisor.js", "audience": "supervisor-or-agent"},
    {"type": "agent.lifecycle", "category": "operator", "producer": "inbox_routes", "audience": "agent"},
    {"type": "agent.lifecycle.manual", "category": "operator", "producer": "supervisor.js", "audience": "agent"},
    {"type": "agent.lifecycle.manual.failed", "category": "operator", "producer": "supervisor.js", "audience": "agent"},
    {"type": "operator.command.output", "category": "operator", "producer": "supervisor.js", "audience": "supervisor"},
    {"type": "operator.command.failed", "category": "operator", "producer": "supervisor.js", "audience": "supervisor"},
    {"type": "inbox.new", "category": "workflow", "producer": "app.workflow_hook", "audience": "supervisor"},
    {"type": "inbox.resolved", "category": "workflow", "producer": "inbox_routes", "audience": "supervisor"},
    {"type": "inbox.batch_resolved", "category": "workflow", "producer": "inbox_routes", "audience": "supervisor"},
    {"type": "inbox.secret_granted", "category": "workflow", "producer": "inbox_routes", "audience": "supervisor"},
    {"type": "workflow.started", "category": "workflow", "producer": "workflow_routes", "audience": "supervisor"},
    {"type": "workflow.gate.approved", "category": "workflow", "producer": "workflow_routes", "audience": "supervisor"},
    {"type": "workflow.paused", "category": "workflow", "producer": "workflow_routes", "audience": "supervisor"},
    {"type": "workflow.resumed", "category": "workflow", "producer": "workflow_routes", "audience": "supervisor"},
    {"type": "workflow.aborted", "category": "workflow", "producer": "workflow_routes", "audience": "supervisor"},
    {"type": "eval.started", "category": "evaluation", "producer": "eval_routes", "audience": "agent"},
    {"type": "eval.completed", "category": "evaluation", "producer": "eval_routes", "audience": "agent"},
    {"type": "eval.failed", "category": "evaluation", "producer": "eval_routes", "audience": "agent"},
    {"type": "ab.started", "category": "evaluation", "producer": "eval_routes", "audience": "supervisor"},
    {"type": "ab.completed", "category": "evaluation", "producer": "eval_routes", "audience": "supervisor"},
    {"type": "ab.failed", "category": "evaluation", "producer": "eval_routes", "audience": "supervisor"},
    {"type": "knowledge.reindex.started", "category": "technical", "producer": "knowledge_routes", "audience": "supervisor"},
    {"type": "knowledge.reindex.completed", "category": "technical", "producer": "knowledge_routes", "audience": "supervisor"},
    {"type": "knowledge.reindex.failed", "category": "technical", "producer": "knowledge_routes", "audience": "supervisor"},
)


def supervisor_stream_event_catalog() -> tuple[dict[str, str], ...]:
    return SUPERVISOR_STREAM_EVENT_CATALOG


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
    "router": "router",
    "terminal": "router",
}

_ACTOR_STREAM_META = {
    "creator": {"channel": "executor", "source_kind": "agent", "source_label": "Polluks"},
    "polluks": {"channel": "executor", "source_kind": "agent", "source_label": "Polluks"},
    "supervisor": {"channel": "supervisor", "source_kind": "agent", "source_label": "Kastor"},
    "kastor": {"channel": "supervisor", "source_kind": "agent", "source_label": "Kastor"},
    "router": {"channel": "system", "source_kind": "system", "source_label": "Router"},
    "terminal": {"channel": "system", "source_kind": "system", "source_label": "Terminal"},
}

_SUPERVISOR_ACTORS = {"supervisor", "kastor"}
_SUPERVISOR_ONLY_EVENT_PREFIXES = ("workflow.", "ab.", "knowledge.reindex.")
_SUPERVISOR_ONLY_EVENT_TYPES = {
    "stream.config",
    "stream.history",
    "stream.history.truncated",
    "cycle_finished",
    "error",
    "operator.command.executed",
    "operator.command.output",
    "operator.command.failed",
    "system.reset",
    "inbox.new",
    "inbox.resolved",
    "inbox.batch_resolved",
    "inbox.secret_granted",
}
_AGENT_EVENT_PREFIXES = ("eval.",)
_AGENT_EVENT_TYPES = {
    "agent.lifecycle",
    "agent.lifecycle.manual",
    "agent.lifecycle.manual.failed",
    "agent.spawned",
    "agent.spawn.manual",
    "inbox.delegated",
    "system.current_task.paused",
    "system.current_task.stopped",
    "system.current_task.retried",
}
_SUPERVISOR_REPORT_EVENT_TYPES = {
    "eval.completed",
    "eval.failed",
    "inbox.new",
    "inbox.resolved",
    "inbox.secret_granted",
}
_TASK_TERMINAL_EVENT_TYPES = {"task.cancelled", "task.completed", "task.failed"}
_TASK_TERMINAL_STATUSES = {"done", "failed", "cancelled", "completed"}


def agent_thread_owner(agent_id: str | None) -> str | None:
    resolved = str(agent_id or "").strip()
    if not resolved:
        return None
    return f"agent:{resolved}"


def supervisor_thread_owner() -> str:
    return "supervisor"


def kastor_thread_owner() -> str:
    return "agent:kastor"


def router_thread_owner() -> str:
    return "agent:router"


def routing_for_supervisor_report(agent_id: str | None = None) -> dict[str, Any]:
    supervisor_owner = supervisor_thread_owner()
    agent_owner = agent_thread_owner(agent_id)
    if agent_owner is None:
        return {
            "thread_owners": [supervisor_owner],
            "direction_per_owner": {supervisor_owner: "incoming"},
        }
    return {
        "thread_owners": [supervisor_owner, agent_owner],
        "direction_per_owner": {
            supervisor_owner: "incoming",
            agent_owner: "internal",
        },
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


def routing_for_panel(panel: str | None, *, agent_id: str | None = None) -> dict[str, Any]:
    key = str(panel or "").lower()
    resolved_agent_id = agent_id or infer_agent_id(panel=panel)
    owner = agent_thread_owner(resolved_agent_id)
    if key == "supervisor_log":
        return {
            "thread_owners": [kastor_thread_owner()],
            "direction_per_owner": {
                kastor_thread_owner(): "internal",
            },
        }
    if key == "user_model_log":
        return routing_for_supervisor_report(resolved_agent_id)
    if owner is not None:
        return {
            "thread_owners": [owner],
            "direction_per_owner": {owner: "internal"},
        }
    return {
        "thread_owners": [supervisor_thread_owner()],
        "direction_per_owner": {supervisor_thread_owner(): "internal"},
    }


def routing_for_actor(actor: str | None, *, agent_id: str | None = None) -> dict[str, Any]:
    key = str(actor or "").lower()
    resolved_agent_id = agent_id or infer_agent_id(actor=actor)
    owner = agent_thread_owner(resolved_agent_id)
    if key in {"supervisor", "kastor"}:
        return {
            "thread_owners": [kastor_thread_owner()],
            "direction_per_owner": {
                kastor_thread_owner(): "internal",
            },
        }
    if key in {"router", "terminal"}:
        return {
            "thread_owners": [router_thread_owner()],
            "direction_per_owner": {router_thread_owner(): "internal"},
        }
    if key in _SUPERVISOR_ACTORS:
        return {
            "thread_owners": [supervisor_thread_owner()],
            "direction_per_owner": {supervisor_thread_owner(): "incoming"},
        }
    if owner is not None:
        return {
            "thread_owners": [owner],
            "direction_per_owner": {owner: "internal"},
        }
    return {
        "thread_owners": [supervisor_thread_owner()],
        "direction_per_owner": {supervisor_thread_owner(): "internal"},
    }


def routing_for_operator_input(target_agent: str | None = None) -> dict[str, Any]:
    normalized_target = str(target_agent or "").strip()
    supervisor_owner = supervisor_thread_owner()
    if not normalized_target:
        return {
            "thread_owners": [supervisor_owner],
            "direction_per_owner": {supervisor_owner: "outgoing"},
        }
    target_owner = agent_thread_owner(normalized_target)
    return {
        "thread_owners": [target_owner],
        "direction_per_owner": {
            target_owner: "incoming",
        },
    }


def routing_for_supervisor_message() -> dict[str, Any]:
    return routing_for_supervisor_report("kastor")


def routing_for_system_event(*, agent_id: str | None = None) -> dict[str, Any]:
    owner = agent_thread_owner(agent_id)
    if owner is not None:
        return {
            "thread_owners": [owner],
            "direction_per_owner": {owner: "internal"},
        }
    supervisor_owner = supervisor_thread_owner()
    return {
        "thread_owners": [supervisor_owner],
        "direction_per_owner": {supervisor_owner: "internal"},
    }


def routing_for_task_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    lowered_type = str(event_type or "task.event").strip().lower()
    assigned_agent_id = str(
        payload.get("assigned_agent_id")
        or payload.get("agent_id")
        or ""
    ).strip()
    status_value = str(payload.get("to_status") or payload.get("status") or "").strip().lower()
    action_value = str(payload.get("action") or "").strip().lower()

    if lowered_type in {"task.created", "task.reassigned"}:
        if assigned_agent_id:
            return routing_for_operator_input(assigned_agent_id)
        return routing_for_system_event()

    if lowered_type == "task.bulk_updated":
        if action_value == "reassign" and assigned_agent_id:
            return routing_for_operator_input(assigned_agent_id)
        if action_value == "cancel" and assigned_agent_id:
            return routing_for_supervisor_report(assigned_agent_id)
        return routing_for_system_event()

    if lowered_type in _TASK_TERMINAL_EVENT_TYPES:
        return routing_for_supervisor_report(assigned_agent_id or None)

    if lowered_type == "task.moved":
        if status_value in _TASK_TERMINAL_STATUSES:
            return routing_for_supervisor_report(assigned_agent_id or None)
        if assigned_agent_id:
            owner = agent_thread_owner(assigned_agent_id)
            if owner is not None:
                return {
                    "thread_owners": [owner],
                    "direction_per_owner": {owner: "internal"},
                }
        return routing_for_system_event()

    if lowered_type == "task.decomposed":
        return routing_for_system_event()

    return {}


def reporting_agent_id_for_event(event_type: str, payload: dict[str, Any]) -> str | None:
    lowered_type = str(event_type or payload.get("message_type") or payload.get("type") or "event").strip().lower()
    direct_agent_id = str(payload.get("agent_id") or "").strip()
    if direct_agent_id:
        return direct_agent_id

    if lowered_type.startswith("task."):
        assigned_agent_id = str(payload.get("assigned_agent_id") or "").strip()
        if assigned_agent_id:
            return assigned_agent_id

    if lowered_type == "inbox.secret_granted":
        entity_type = str(payload.get("entity_type") or "").strip().lower()
        entity_id = str(payload.get("entity_id") or "").strip()
        if entity_type == "agent" and entity_id:
            return entity_id

    return None


def default_routing_for_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    lowered_type = str(event_type or payload.get("message_type") or payload.get("type") or "event").strip().lower()
    normalized_target = str(payload.get("target_agent") or "").strip()
    if lowered_type == "supervisor_message":
        return routing_for_supervisor_message()
    if lowered_type == "operator.input.accepted":
        return routing_for_operator_input(normalized_target or None)
    if lowered_type.startswith("task."):
        task_routing = routing_for_task_event(lowered_type, payload)
        if task_routing:
            return task_routing
    if normalized_target:
        target_owner = agent_thread_owner(normalized_target)
        if target_owner is not None:
            return {
                "thread_owners": [target_owner],
                "direction_per_owner": {target_owner: "incoming"},
            }

    if lowered_type in _SUPERVISOR_REPORT_EVENT_TYPES:
        return routing_for_supervisor_report(reporting_agent_id_for_event(lowered_type, payload))

    normalized_agent_id = str(payload.get("agent_id") or "").strip()
    if normalized_agent_id and (lowered_type in _AGENT_EVENT_TYPES or lowered_type.startswith(_AGENT_EVENT_PREFIXES)):
        owner = agent_thread_owner(normalized_agent_id)
        if owner is not None:
            return {
                "thread_owners": [owner],
                "direction_per_owner": {owner: "internal"},
            }

    if lowered_type in _SUPERVISOR_ONLY_EVENT_TYPES or lowered_type.startswith(_SUPERVISOR_ONLY_EVENT_PREFIXES):
        owner = supervisor_thread_owner()
        return {
            "thread_owners": [owner],
            "direction_per_owner": {owner: "internal"},
        }

    return {}


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


def _owner_label(owner: str | None) -> str:
    normalized_owner = str(owner or "").strip()
    if not normalized_owner:
        return ""
    if normalized_owner == supervisor_thread_owner():
        return "Supervisor"
    if normalized_owner.startswith("agent:"):
        return normalized_owner.split(":", 1)[1]
    return normalized_owner


def normalize_stream_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    resolved_type = str(normalized.get("message_type") or normalized.get("type") or event_type or "event").strip() or "event"
    normalized["message_type"] = resolved_type

    owners = [str(owner or "").strip() for owner in normalized.get("thread_owners", []) if str(owner or "").strip()]
    if not owners and normalized.get("thread_owner"):
        legacy_owner = str(normalized.get("thread_owner") or "").strip()
        if legacy_owner:
            owners = [legacy_owner]
    if not owners:
        routing = default_routing_for_event(resolved_type, normalized)
        if routing:
            normalized.update(routing)

    timestamp = (
        normalized.get("timestamp")
        or normalized.get("ts")
        or normalized.get("created_at")
        or normalized.get("time")
    )
    if timestamp:
        normalized["timestamp"] = timestamp

    source_value = (
        normalized.get("from")
        or normalized.get("source_label")
        or normalized.get("actor")
        or normalized.get("agent_id")
        or normalized.get("source_kind")
        or "System"
    )
    normalized["from"] = str(source_value).strip() or "System"

    target_value = normalized.get("to")
    if not target_value:
        if normalized.get("target_agent"):
            target_value = normalized.get("target_agent")
        elif normalized.get("target_scope") == "broadcast":
            target_value = "all"
        else:
            owners = [str(owner or "").strip() for owner in normalized.get("thread_owners", []) if str(owner or "").strip()]
            candidate_labels = [_owner_label(owner) for owner in owners]
            candidate_labels = [label for label in candidate_labels if label and label != normalized["from"]]
            if candidate_labels:
                target_value = candidate_labels[0]
    if target_value:
        normalized["to"] = str(target_value).strip()

    status_value = normalized.get("status")
    if not status_value and resolved_type == "actor_state":
        status_value = normalized.get("state")
    if not status_value and normalized.get("resolution"):
        status_value = normalized.get("resolution")
    if not status_value:
        lowered_type = resolved_type.lower()
        if lowered_type == "error" or lowered_type.endswith(".failed"):
            status_value = "error"
        elif lowered_type.endswith(".paused"):
            status_value = "paused"
        elif lowered_type.endswith(".stopped"):
            status_value = "stopped"
        elif lowered_type.endswith(".retried") or lowered_type.endswith(".retry"):
            status_value = "retried"
        elif lowered_type in {"operator.input.accepted", "operator.command.executed", "agent.spawned"}:
            status_value = "accepted"
        elif lowered_type == "inbox.delegated":
            status_value = "delegated"
        elif lowered_type.startswith("workflow."):
            status_value = lowered_type.split(".")[-1]
        elif lowered_type.startswith("eval.") or lowered_type.startswith("ab."):
            status_value = lowered_type.split(".")[-1]
    if status_value:
        normalized["status"] = str(status_value).strip().lower()

    return normalized