"""Routes: Performance, notifications, session replay, API keys, webhooks.

Faza 13 monitoring & integration routes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import csv
import io
import json
import time
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route


_DEFAULT_MONITORING_PANEL_ORDER = [
    "performance-chart",
    "performance-summary",
    "costs",
    "comparison",
    "health",
    "alerts",
    "sessions",
    "event-ticker",
    "traces",
]


def _get_request_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        return "anonymous"
    if hasattr(user, "user_id"):
        return str(user.user_id)
    if isinstance(user, dict):
        return str(user.get("user_id") or user.get("sub") or "anonymous")
    return str(user)


def _notification_to_payload(notification: object) -> dict:
    if hasattr(notification, "to_dict"):
        payload = notification.to_dict()
    else:
        payload = dict(notification)

    is_read = bool(payload.get("is_read", payload.get("read", False)))
    body = payload.get("body") or ""
    title = payload.get("title") or ""
    notif_type = str(payload.get("type") or payload.get("event_type") or "system.info")
    severity = str(payload.get("severity") or "").lower().strip()
    if not severity:
        if any(token in notif_type for token in ("error", "failed", "critical")):
            severity = "error"
        elif any(token in notif_type for token in ("warn", "budget", "alert")):
            severity = "warning"
        else:
            severity = "info"
    source = str(payload.get("source") or payload.get("agent_id") or ("system" if notif_type.startswith("system") else "general"))
    payload["is_read"] = is_read
    payload["read"] = is_read
    payload["message"] = body or title
    payload["event_type"] = notif_type
    payload["severity"] = severity
    payload["source"] = source
    payload["group_key"] = f"{source}:{notif_type}"
    payload["actionability"] = bool(payload.get("action_url") or payload.get("task_id") or payload.get("workflow_run_id") or payload.get("session_id"))
    return payload


def _notification_preferences_from_settings(settings: dict | None) -> dict:
    settings = settings or {}
    channels = settings.get("notification_channels")
    if not isinstance(channels, dict):
        channels = {
            "task.done": ["ui"],
            "agent.error": ["ui", "email"],
            "budget.exceeded": ["ui", "webhook"],
            "system.info": ["ui"],
        }

    muted_agents = settings.get("notification_muted_agents")
    if not isinstance(muted_agents, list):
        muted_agents = []

    grouping = settings.get("notification_grouping")
    if not isinstance(grouping, dict):
        grouping = {"primary": "agent", "secondary": "event_type"}

    return {
        "channels": channels,
        "muted_agents": [str(agent).strip() for agent in muted_agents if str(agent).strip()],
        "grouping": grouping,
    }


async def _get_notification_preferences(request: Request) -> dict:
    repo = getattr(request.app.state, "user_settings_repo", None)
    if repo is None:
        return _notification_preferences_from_settings(None)
    settings = await repo.get_for_user(_get_request_user_id(request))
    return _notification_preferences_from_settings(settings)


async def _save_notification_preferences(request: Request, preferences: dict) -> dict:
    repo = getattr(request.app.state, "user_settings_repo", None)
    normalized = _notification_preferences_from_settings({
        "notification_channels": preferences.get("channels"),
        "notification_muted_agents": preferences.get("muted_agents"),
        "notification_grouping": preferences.get("grouping"),
    })
    if repo is None:
        return normalized
    user_id = _get_request_user_id(request)
    settings = await repo.get_for_user(user_id)
    settings["notification_channels"] = normalized["channels"]
    settings["notification_muted_agents"] = normalized["muted_agents"]
    settings["notification_grouping"] = normalized["grouping"]
    saved = await repo.save_for_user(user_id, settings)
    return _notification_preferences_from_settings(saved)


def _group_notifications(items: list[dict], grouping: dict) -> list[dict]:
    primary_key = grouping.get("primary") or "agent"
    secondary_key = grouping.get("secondary") or "event_type"
    grouped: dict[str, dict] = {}

    for item in items:
        primary_value = str(item.get("agent_id") or item.get("source") or "system") if primary_key == "agent" else str(item.get(primary_key) or "other")
        secondary_value = str(item.get("event_type") or item.get("type") or "general") if secondary_key == "event_type" else str(item.get(secondary_key) or "other")
        bucket = grouped.setdefault(primary_value, {"key": primary_value, "label": primary_value, "items": [], "groups": {}})
        subgroup = bucket["groups"].setdefault(secondary_value, {"key": secondary_value, "label": secondary_value, "items": []})
        subgroup["items"].append(item)
        bucket["items"].append(item)

    ordered = []
    for key in sorted(grouped.keys()):
        bucket = grouped[key]
        ordered.append({
            "key": bucket["key"],
            "label": bucket["label"],
            "items": bucket["items"],
            "groups": [bucket["groups"][subkey] for subkey in sorted(bucket["groups"].keys())],
        })
    return ordered


def _window_to_cutoff(raw: str | None) -> float | None:
    if not raw:
        return None
    value = raw.strip().lower()
    seconds = {
        "1h": 3600,
        "6h": 6 * 3600,
        "24h": 24 * 3600,
        "7d": 7 * 24 * 3600,
    }.get(value)
    if seconds is None:
        return None
    return time.time() - seconds


def _window_to_range(raw: str | None) -> tuple[str | None, str | None, float | None, float | None]:
    cutoff = _window_to_cutoff(raw)
    if cutoff is None:
        return None, None, None, None
    now_ts = time.time()
    since_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
    until_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    return since_dt.isoformat(), until_dt.isoformat(), cutoff, now_ts


def _coerce_timestamp(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            pass
        try:
            normalized = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _parse_limit(raw: str | None, *, default: int = 50, minimum: int = 1, maximum: int = 500) -> int:
    try:
        return max(minimum, min(int(raw or str(default)), maximum))
    except ValueError:
        return default


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, pct)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _normalise_record(record: object) -> dict[str, object]:
    data = record.to_dict() if hasattr(record, "to_dict") else dict(record)
    trace_id = str(data.get("trace_id") or data.get("id") or "")
    success = data.get("success")
    status = data.get("status")
    if not status:
        if success is True:
            status = "success"
        elif success is False:
            status = "error"
        else:
            status = "unknown"
    timestamp = data.get("created_at") or data.get("timestamp")
    data.update({
        "trace_id": trace_id,
        "id": trace_id or data.get("id"),
        "status": status,
        "timestamp": timestamp,
        "duration_ms": _to_float(data.get("duration_ms")),
        "tokens_in": int(_to_float(data.get("tokens_in"), 0)),
        "tokens_out": int(_to_float(data.get("tokens_out"), 0)),
        "cost_usd": _to_float(data.get("cost_usd")),
    })
    return data


def _record_matches_filters(record: dict[str, object], *, agent: str = "", model: str = "", q: str = "", since_ts: float | None = None, until_ts: float | None = None, status: str = "") -> bool:
    timestamp = _coerce_timestamp(record.get("timestamp") or record.get("created_at"))
    if since_ts is not None and timestamp is not None and timestamp < since_ts:
        return False
    if until_ts is not None and timestamp is not None and timestamp > until_ts:
        return False

    agent_value = str(record.get("agent_id") or record.get("agent_role") or "")
    model_value = str(record.get("model") or "")
    status_value = str(record.get("status") or "").lower()

    if agent and agent.lower() not in agent_value.lower():
        return False
    if model and model.lower() not in model_value.lower():
        return False
    if status and status.lower() != status_value:
        return False
    if q:
        haystack = " ".join([
            str(record.get("trace_id") or ""),
            agent_value,
            model_value,
            str(record.get("task_type") or record.get("name") or ""),
            str(record.get("operation") or ""),
            str(record.get("status") or ""),
        ]).lower()
        if q.lower() not in haystack:
            return False
    return True


def _build_comparison(records: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for record in records:
        agent = str(record.get("agent_role") or record.get("agent_id") or "unknown")
        model = str(record.get("model") or "—")
        key = (agent, model)
        entry = grouped.setdefault(key, {
            "agent_role": agent,
            "model": model,
            "durations": [],
            "successes": 0,
            "runs": 0,
            "cost_usd": 0.0,
            "tokens": 0,
        })
        duration = _to_float(record.get("duration_ms"), -1)
        if duration >= 0:
            entry["durations"].append(duration)
        entry["runs"] += 1
        if str(record.get("status") or "").lower() in {"success", "completed", "ok"} or record.get("success") is True:
            entry["successes"] += 1
        entry["cost_usd"] += _to_float(record.get("cost_usd"))
        entry["tokens"] += int(_to_float(record.get("tokens_in"), 0) + _to_float(record.get("tokens_out"), 0))

    comparison: list[dict[str, object]] = []
    for entry in grouped.values():
        durations = [float(v) for v in entry.pop("durations")]
        runs = int(entry["runs"])
        tokens = int(entry["tokens"])
        cost_usd = float(entry["cost_usd"])
        comparison.append({
            "agent_role": entry["agent_role"],
            "model": entry["model"],
            "runs": runs,
            "avg_time_ms": (sum(durations) / len(durations)) if durations else None,
            "p95_ms": _percentile(durations, 0.95),
            "success_rate": (int(entry["successes"]) / runs) if runs else None,
            "cost_per_1k_tokens": (cost_usd / tokens * 1000) if tokens else None,
            "cost_usd": cost_usd,
            "tokens": tokens,
        })
    comparison.sort(key=lambda item: (_to_float(item.get("avg_time_ms"), 0), -int(item.get("runs") or 0)))
    return comparison


def _build_sparkline(records: list[dict[str, object]], extractor) -> list[float]:
    if not records:
        return []
    timestamps = [ts for ts in (_coerce_timestamp(r.get("timestamp") or r.get("created_at")) for r in records) if ts is not None]
    if not timestamps:
        return []
    start = min(timestamps)
    end = max(timestamps)
    if end <= start:
        return [float(extractor(record)) for record in records[:1]]
    buckets = [0.0 for _ in range(8)]
    span = max(end - start, 1.0)
    for record in records:
        ts = _coerce_timestamp(record.get("timestamp") or record.get("created_at"))
        if ts is None:
            continue
        index = min(7, int(((ts - start) / span) * 7))
        buckets[index] += float(extractor(record))
    return [round(value, 3) for value in buckets]


def _summarise_records(records: list[dict[str, object]], previous_records: list[dict[str, object]] | None = None) -> dict[str, object]:
    previous_records = previous_records or []

    def _value_set(source: list[dict[str, object]]) -> dict[str, float]:
        return {
            "tasks": float(len(source)),
            "tokens": float(sum(int(_to_float(r.get("tokens_in"), 0) + _to_float(r.get("tokens_out"), 0)) for r in source)),
            "cost": float(sum(_to_float(r.get("cost_usd")) for r in source)),
            "errors": float(sum(1 for r in source if str(r.get("status") or "").lower() in {"error", "failed"} or r.get("success") is False)),
        }

    current = _value_set(records)
    previous = _value_set(previous_records)
    return {
        "cards": [
            {
                "key": "tasks",
                "label": "Tasks",
                "value": int(current["tasks"]),
                "delta": int(current["tasks"] - previous["tasks"]),
                "sparkline": _build_sparkline(records, lambda record: 1),
            },
            {
                "key": "tokens",
                "label": "Tokens",
                "value": int(current["tokens"]),
                "delta": int(current["tokens"] - previous["tokens"]),
                "sparkline": _build_sparkline(records, lambda record: int(_to_float(record.get("tokens_in"), 0) + _to_float(record.get("tokens_out"), 0))),
            },
            {
                "key": "cost",
                "label": "Cost",
                "value": round(current["cost"], 4),
                "delta": round(current["cost"] - previous["cost"], 4),
                "sparkline": _build_sparkline(records, lambda record: _to_float(record.get("cost_usd"))),
            },
            {
                "key": "errors",
                "label": "Errors",
                "value": int(current["errors"]),
                "delta": int(current["errors"] - previous["errors"]),
                "sparkline": _build_sparkline(records, lambda record: 1 if str(record.get("status") or "").lower() in {"error", "failed"} or record.get("success") is False else 0),
            },
        ],
        "comparison": _build_comparison(records),
        "totals": current,
    }


async def _query_normalised_performance_records(request: Request, *, agent: str = "", model: str = "", since: str | None = None, until: str | None = None, limit: int = 500) -> list[dict[str, object]]:
    tracker = getattr(request.app.state, "performance_tracker", None)
    if tracker is None:
        return []
    records = await tracker.query(agent_role=agent or None, model=model or None, since=since, until=until, limit=limit)
    return [_normalise_record(record) for record in records]


async def _load_trace_detail_payload(request: Request, trace_id: str) -> dict[str, object] | None:
    trace_viewer = getattr(request.app.state, "trace_viewer", None)
    if trace_viewer is not None:
        trace = None
        if hasattr(trace_viewer, "get_trace"):
            trace = trace_viewer.get_trace(trace_id)
        if trace is None and hasattr(trace_viewer, "load_trace"):
            trace = trace_viewer.load_trace(trace_id)
        if trace is not None:
            data = trace.to_dict() if hasattr(trace, "to_dict") else dict(trace)
            data["timeline"] = trace.timeline() if hasattr(trace, "timeline") else data.get("timeline", [])
            data["tree"] = trace.tree() if hasattr(trace, "tree") else data.get("tree", {})
            return data

    tracker = getattr(request.app.state, "performance_tracker", None)
    if tracker is None:
        return None

    if hasattr(tracker, "get_trace"):
        trace = await tracker.get_trace(trace_id)
        if trace is not None:
            data = trace.to_dict() if hasattr(trace, "to_dict") else dict(trace)
            data.setdefault("timeline", data.get("spans", []))
            data.setdefault("tree", data.get("spans", []))
            return data

    records = await tracker.query(limit=1000)
    for record in records:
        item = _normalise_record(record)
        if str(item.get("trace_id") or item.get("id") or "") != trace_id:
            continue
        pseudo_span = {
            "span_id": f"{trace_id}-root",
            "trace_id": trace_id,
            "parent_span_id": "",
            "operation": str(item.get("task_type") or item.get("name") or "run"),
            "agent_id": str(item.get("agent_role") or item.get("agent_id") or ""),
            "task_id": str(item.get("task_type") or ""),
            "start_time": item.get("timestamp") or item.get("created_at"),
            "end_time": item.get("timestamp") or item.get("created_at"),
            "duration_ms": item.get("duration_ms"),
            "status": item.get("status"),
            "tags": {
                "model": item.get("model"),
                "tokens_in": item.get("tokens_in"),
                "tokens_out": item.get("tokens_out"),
                "cost_usd": item.get("cost_usd"),
            },
            "logs": [],
        }
        return {
            "trace_id": trace_id,
            "root_span_id": pseudo_span["span_id"],
            "span_count": 1,
            "duration_ms": item.get("duration_ms"),
            "is_complete": True,
            "metadata": {
                "agent_role": item.get("agent_role"),
                "model": item.get("model"),
                "status": item.get("status"),
            },
            "spans": [pseudo_span],
            "timeline": [pseudo_span],
            "tree": {**pseudo_span, "children": []},
        }
    return None


# ── Performance ─────────────────────────────────────────────────

async def api_performance(request: Request) -> JSONResponse:
    tracker = request.app.state.performance_tracker
    agent = request.query_params.get("agent")
    model = request.query_params.get("model")
    since = request.query_params.get("from")
    until = request.query_params.get("to")
    if request.query_params.get("window") and not since and not until:
        since, until, _, _ = _window_to_range(request.query_params.get("window"))
    records = await tracker.query(agent_role=agent, model=model, since=since, until=until)
    return JSONResponse([r.to_dict() for r in records])


async def api_performance_summary(request: Request) -> JSONResponse:
    tracker = request.app.state.performance_tracker
    agent = request.query_params.get("agent")
    model = request.query_params.get("model")
    since = request.query_params.get("from")
    until = request.query_params.get("to")
    if request.query_params.get("window") and not since and not until:
        since, until, _, _ = _window_to_range(request.query_params.get("window"))

    if since or until:
        records = await _query_normalised_performance_records(
            request,
            agent=agent or "",
            model=model or "",
            since=since,
            until=until,
        )
        durations = [_to_float(record.get("duration_ms"), -1) for record in records]
        durations = [value for value in durations if value >= 0]
        total = len(records)
        summary = {
            "total": total,
            "avg_duration_ms": (sum(durations) / len(durations)) if durations else None,
            "p50_ms": _percentile(durations, 0.5),
            "p95_ms": _percentile(durations, 0.95),
            "success_rate": (sum(1 for record in records if str(record.get("status") or "").lower() in {"success", "completed", "ok"} or record.get("success") is True) / total) if total else None,
            "total_tokens_in": sum(int(_to_float(record.get("tokens_in"), 0)) for record in records),
            "total_tokens_out": sum(int(_to_float(record.get("tokens_out"), 0)) for record in records),
            "total_cost_usd": round(sum(_to_float(record.get("cost_usd")) for record in records), 6),
        }
    else:
        summary = await tracker.summary(agent_role=agent, model=model)
    return JSONResponse(summary)


async def api_monitoring_summary(request: Request) -> JSONResponse:
    agent = request.query_params.get("agent") or request.query_params.get("agent_id") or ""
    model = request.query_params.get("model") or ""
    since = request.query_params.get("from")
    until = request.query_params.get("to")
    since_ts = until_ts = prev_since_ts = prev_until_ts = None
    if request.query_params.get("window") and not since and not until:
        since, until, since_ts, until_ts = _window_to_range(request.query_params.get("window"))
        if since_ts is not None and until_ts is not None:
            span = until_ts - since_ts
            prev_since_ts = since_ts - span
            prev_until_ts = since_ts
    else:
        since_ts = _coerce_timestamp(since)
        until_ts = _coerce_timestamp(until)

    records = await _query_normalised_performance_records(
        request,
        agent=agent,
        model=model,
        since=since,
        until=until,
        limit=1000,
    )

    previous_records: list[dict[str, object]] = []
    if prev_since_ts is not None and prev_until_ts is not None:
        previous_records = await _query_normalised_performance_records(
            request,
            agent=agent,
            model=model,
            since=datetime.fromtimestamp(prev_since_ts, tz=timezone.utc).isoformat(),
            until=datetime.fromtimestamp(prev_until_ts, tz=timezone.utc).isoformat(),
            limit=1000,
        )

    payload = _summarise_records(records, previous_records)
    payload["filters"] = {
        "agent": agent,
        "model": model,
        "from": since,
        "to": until,
        "window": request.query_params.get("window") or "",
    }
    payload["time_range"] = {
        "from": since,
        "to": until,
    }
    return JSONResponse(payload)


def _normalise_monitoring_layout(payload: object | None) -> dict[str, object]:
    if isinstance(payload, dict):
        raw_order = payload.get("panel_order")
    else:
        raw_order = None
    allowed = set(_DEFAULT_MONITORING_PANEL_ORDER)
    order: list[str] = []
    if isinstance(raw_order, list):
        for item in raw_order:
            panel_id = str(item or "").strip()
            if not panel_id or panel_id not in allowed or panel_id in order:
                continue
            order.append(panel_id)
    for panel_id in _DEFAULT_MONITORING_PANEL_ORDER:
        if panel_id not in order:
            order.append(panel_id)
    return {
        "panel_order": order,
        "version": 1,
    }


async def api_monitoring_layout(request: Request) -> JSONResponse:
    repo = getattr(request.app.state, "user_settings_repo", None)
    user_id = _get_request_user_id(request)

    if request.method == "GET":
        if repo is None:
            return JSONResponse({"layout": _normalise_monitoring_layout(None)})
        settings = await repo.get_for_user(user_id)
        return JSONResponse({"layout": _normalise_monitoring_layout(settings.get("monitoring_layout"))})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    layout = _normalise_monitoring_layout(body if isinstance(body, dict) else None)
    if repo is None:
        return JSONResponse({"ok": True, "layout": layout})

    settings = await repo.get_for_user(user_id)
    settings["monitoring_layout"] = layout
    saved = await repo.save_for_user(user_id, settings)
    return JSONResponse({"ok": True, "layout": _normalise_monitoring_layout(saved.get("monitoring_layout"))})


async def api_alerts(request: Request) -> JSONResponse:
    """Recent alerts from the alert manager."""
    alert_manager = getattr(request.app.state, "alert_manager", None)
    if alert_manager is None or not hasattr(alert_manager, "recent_alerts"):
        return JSONResponse([])

    limit_raw = request.query_params.get("limit")
    try:
        limit = max(1, min(int(limit_raw or "50"), 200))
    except ValueError:
        limit = 50
    cutoff = _window_to_cutoff(request.query_params.get("window"))

    payload = []
    for alert in alert_manager.recent_alerts(last_n=limit):
        timestamp = getattr(alert, "timestamp", None)
        if cutoff is not None and timestamp is not None and timestamp < cutoff:
            continue
        severity = getattr(alert, "severity", "warning")
        payload.append({
            "rule_name": getattr(alert, "rule_name", "unknown"),
            "message": getattr(alert, "message", ""),
            "severity": severity.value if hasattr(severity, "value") else str(severity),
            "timestamp": timestamp,
        })
    return JSONResponse(payload)


# ── Notifications ───────────────────────────────────────────────

async def api_notifications(request: Request) -> JSONResponse:
    svc = getattr(request.app.state, "notification_service", None)
    preferences = await _get_notification_preferences(request)
    if svc is None:
        return JSONResponse({"unread_count": 0, "notifications": [], "groups": [], "preferences": preferences})

    user_id = _get_request_user_id(request)
    unread = request.query_params.get("unread") == "true"

    unread_count_only = request.query_params.get("unread_count") in {"1", "true", "yes"}
    muted_agents = set(preferences.get("muted_agents") or [])
    count = await svc.unread_count(user_id)
    if unread_count_only:
        return JSONResponse({"unread_count": count, "notifications": []})

    notifs = await svc.list_for_user(user_id, unread_only=unread)
    payloads = [_notification_to_payload(n) for n in notifs]
    if muted_agents:
        payloads = [item for item in payloads if str(item.get("agent_id") or item.get("source") or "") not in muted_agents]
    groups = _group_notifications(payloads, preferences.get("grouping") or {})
    filtered_unread = sum(1 for item in payloads if not item.get("is_read"))
    return JSONResponse({
        "unread_count": filtered_unread if muted_agents else count,
        "notifications": payloads,
        "groups": groups,
        "preferences": preferences,
    })


async def api_notification_preferences(request: Request) -> JSONResponse:
    if request.method == "GET":
        return JSONResponse({"preferences": await _get_notification_preferences(request)})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    preferences = await _save_notification_preferences(request, body if isinstance(body, dict) else {})
    return JSONResponse({"ok": True, "preferences": preferences})


async def api_notification_read(request: Request) -> JSONResponse:
    svc = getattr(request.app.state, "notification_service", None)
    if svc is None:
        return JSONResponse({"ok": False, "error": "notification_service unavailable"}, status_code=503)
    ok = await svc.mark_read(request.path_params["id"])
    return JSONResponse({"ok": ok})


async def api_notifications_read_all(request: Request) -> JSONResponse:
    svc = getattr(request.app.state, "notification_service", None)
    if svc is None:
        return JSONResponse({"marked": 0, "error": "notification_service unavailable"}, status_code=503)
    user_id = _get_request_user_id(request)
    count = await svc.mark_all_read(user_id)
    return JSONResponse({"marked": count})


# ── Session Replay ──────────────────────────────────────────────

async def api_sessions(request: Request) -> JSONResponse:
    recorder = request.app.state.session_recorder
    limit = _parse_limit(request.query_params.get("limit"), default=50, maximum=200)
    agent_id = request.query_params.get("agent_id") or None
    sessions = await recorder.list_sessions(
        limit=limit,
        agent_id=agent_id,
    )
    since, until, since_ts, until_ts = _window_to_range(request.query_params.get("window"))
    if request.query_params.get("from"):
        since = request.query_params.get("from")
        since_ts = _coerce_timestamp(since)
    if request.query_params.get("to"):
        until = request.query_params.get("to")
        until_ts = _coerce_timestamp(until)

    filtered = []
    for session in sessions:
        ts = _coerce_timestamp(session.get("ended_at") or session.get("started_at"))
        if since_ts is not None and ts is not None and ts < since_ts:
            continue
        if until_ts is not None and ts is not None and ts > until_ts:
            continue
        filtered.append(session)
    return JSONResponse({"sessions": filtered, "total": len(filtered), "filters": {"agent_id": agent_id, "from": since, "to": until}})


async def api_session_events(request: Request) -> JSONResponse:
    recorder = request.app.state.session_recorder
    sid = request.path_params["session_id"]
    events = await recorder.get_session_events(sid)
    return JSONResponse([e.to_dict() for e in events])


async def api_session_replay(request: Request) -> JSONResponse:
    """``GET /api/sessions/{session_id}/replay`` — events formatted for timeline playback."""
    recorder = request.app.state.session_recorder
    sid = request.path_params["session_id"]
    events = await recorder.get_session_events(sid, limit=2000)
    replay = {
        "session_id": sid,
        "event_count": len(events),
        "events": [
            {
                "id": e.id,
                "type": e.event_type,
                "agent_id": e.agent_id,
                "payload": e.payload,
                "timestamp": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }
    return JSONResponse(replay)


async def api_session_export(request: Request) -> Response:
    """Export session replay as JSON or a lightweight HTML report."""
    recorder = request.app.state.session_recorder
    sid = request.path_params["session_id"]
    fmt = (request.query_params.get("format") or "json").lower()
    events = await recorder.get_session_events(sid, limit=5000)
    payload = {
        "session_id": sid,
        "event_count": len(events),
        "events": [e.to_dict() for e in events],
    }
    if fmt == "html":
        rows = "".join(
            "<tr>"
            f"<td>{(e.created_at.isoformat() if e.created_at else '—')}</td>"
            f"<td>{e.event_type}</td>"
            f"<td>{e.agent_id or '—'}</td>"
            f"<td><pre>{json.dumps(e.payload, ensure_ascii=False, indent=2)}</pre></td>"
            "</tr>"
            for e in events
        )
        html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Session {sid}</title>"
            "<style>body{font-family:system-ui;padding:24px}table{width:100%;border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px;vertical-align:top}pre{margin:0;white-space:pre-wrap}</style>"
            "</head><body>"
            f"<h1>Session {sid}</h1><p>Events: {len(events)}</p>"
            "<table><thead><tr><th>Timestamp</th><th>Type</th><th>Agent</th><th>Payload</th></tr></thead><tbody>"
            f"{rows}</tbody></table></body></html>"
        )
        return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="session-{sid}.html"'})

    return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="session-{sid}.json"'})


# ── API Keys ────────────────────────────────────────────────────

async def api_keys_list(request: Request) -> JSONResponse:
    mgr = request.app.state.api_key_manager
    user_id = str(request.state.user.user_id)
    keys = await mgr.list_keys(user_id)
    return JSONResponse([k.to_dict() for k in keys])


async def api_keys_create(request: Request) -> JSONResponse:
    mgr = request.app.state.api_key_manager
    user_id = str(request.state.user.user_id)
    body = await request.json()
    expires_at_raw = body.get("expires_at")
    expires_at = None
    if isinstance(expires_at_raw, str) and expires_at_raw.strip():
        try:
            expires_at = datetime.fromisoformat(expires_at_raw.strip())
        except ValueError:
            return JSONResponse({"error": "invalid_expires_at"}, status_code=400)
    rate_limit_raw = body.get("rate_limit_per_min")
    rate_limit_per_min = None
    if rate_limit_raw not in (None, ""):
        try:
            rate_limit_per_min = max(1, int(rate_limit_raw))
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid_rate_limit"}, status_code=400)
    raw_key, record = await mgr.create_key(
        user_id, body.get("name", "Unnamed"),
        scopes=body.get("scopes", []),
        expires_at=expires_at,
        rate_limit_per_min=rate_limit_per_min,
    )
    result = record.to_dict()
    result["raw_key"] = raw_key  # Only shown once
    return JSONResponse(result, status_code=201)


async def api_keys_revoke(request: Request) -> JSONResponse:
    mgr = request.app.state.api_key_manager
    ok = await mgr.revoke_key(request.path_params["id"])
    return JSONResponse({"ok": ok})


async def api_keys_delete(request: Request) -> JSONResponse:
    mgr = request.app.state.api_key_manager
    ok = await mgr.delete_key(request.path_params["id"])
    return JSONResponse({"ok": ok})


# ── Webhooks ────────────────────────────────────────────────────

async def webhooks_list(request: Request) -> JSONResponse:
    mgr = request.app.state.webhook_manager
    user_id = str(request.state.user.user_id)
    hooks = await mgr.list_webhooks(user_id)
    return JSONResponse([h.to_dict() for h in hooks])


async def webhooks_create(request: Request) -> JSONResponse:
    mgr = request.app.state.webhook_manager
    user_id = str(request.state.user.user_id)
    body = await request.json()
    hook = await mgr.create_webhook(
        user_id, body.get("url", ""), body.get("events", []),
        secret=body.get("secret"),
        is_active=bool(body.get("is_active", True)),
    )
    return JSONResponse(hook.to_dict(), status_code=201)


async def webhooks_update(request: Request) -> JSONResponse:
    mgr = request.app.state.webhook_manager
    body = await request.json()
    hook = await mgr.update_webhook(
        request.path_params["id"],
        url=body.get("url"),
        events=body.get("events"),
        secret=body.get("secret"),
        is_active=body.get("is_active"),
    )
    if hook is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(hook.to_dict())


async def webhooks_delete(request: Request) -> JSONResponse:
    mgr = request.app.state.webhook_manager
    ok = await mgr.delete_webhook(request.path_params["id"])
    return JSONResponse({"ok": ok})


async def webhooks_test(request: Request) -> JSONResponse:
    """Send a test payload to a specific webhook."""
    mgr = request.app.state.webhook_manager
    hook = await mgr.get_webhook(request.path_params["id"])
    if not hook:
        return JSONResponse({"error": "not found"}, 404)
    result = await mgr.deliver_webhook(hook, "webhook.test", {"message": "Test from amiagi"})
    return JSONResponse({"result": result, "status": hook.status})


async def webhooks_test_api_alias(request: Request) -> JSONResponse:
    """Compatibility alias for legacy UI path /api/webhooks/{id}/test."""
    return await webhooks_test(request)


# ── Traces ──────────────────────────────────────────────────────

async def api_traces(request: Request) -> JSONResponse:
    """GET /api/traces — list performance traces."""
    limit = _parse_limit(request.query_params.get("limit"), default=50, maximum=500)
    agent = request.query_params.get("agent_id") or ""
    model = request.query_params.get("model") or ""
    q = request.query_params.get("q") or ""
    status = request.query_params.get("status") or ""
    since = request.query_params.get("since") or request.query_params.get("from")
    until = request.query_params.get("until") or request.query_params.get("to")
    since_ts = _coerce_timestamp(since)
    until_ts = _coerce_timestamp(until)
    if request.query_params.get("window") and not since and not until:
        since, until, since_ts, until_ts = _window_to_range(request.query_params.get("window"))

    trace_viewer = getattr(request.app.state, "trace_viewer", None)
    traces: list[dict[str, object]] = []
    if trace_viewer is not None and hasattr(trace_viewer, "list_traces"):
        try:
            raw_traces = trace_viewer.list_traces(limit=limit * 2)
        except Exception:
            raw_traces = []
        for item in raw_traces:
            normalised = _normalise_record(item)
            detail = await _load_trace_detail_payload(request, str(normalised.get("trace_id") or normalised.get("id") or ""))
            if detail:
                metadata = detail.get("metadata") or {}
                normalised.setdefault("agent_id", metadata.get("agent_id") or metadata.get("agent_role") or "")
                normalised.setdefault("model", metadata.get("model") or "")
                normalised["timestamp"] = metadata.get("started_at") or normalised.get("timestamp")
            if _record_matches_filters(normalised, agent=agent, model=model, q=q, since_ts=since_ts, until_ts=until_ts, status=status):
                traces.append(normalised)
    else:
        records = await _query_normalised_performance_records(
            request,
            agent=agent,
            model=model,
            since=since,
            until=until,
            limit=limit * 2,
        )
        traces = [record for record in records if _record_matches_filters(record, agent=agent, model=model, q=q, since_ts=since_ts, until_ts=until_ts, status=status)]

    traces.sort(key=lambda item: _coerce_timestamp(item.get("timestamp") or item.get("created_at")) or 0, reverse=True)
    return JSONResponse({"traces": traces[:limit], "total": len(traces), "filters": {"agent_id": agent, "model": model, "q": q, "status": status, "from": since, "to": until}})


async def api_trace_detail(request: Request) -> JSONResponse:
    """GET /api/traces/{id} — single trace detail with spans."""
    trace_id = request.path_params["id"]
    try:
        payload = await _load_trace_detail_payload(request, trace_id)
        if payload is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse(payload)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


async def api_trace_tree(request: Request) -> JSONResponse:
    trace_id = request.path_params["id"]
    payload = await _load_trace_detail_payload(request, trace_id)
    if payload is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({
        "trace_id": trace_id,
        "tree": payload.get("tree") or {},
        "timeline": payload.get("timeline") or [],
    })


async def api_metrics_export(request: Request) -> Response:
    fmt = (request.query_params.get("format") or "json").lower()
    agent = request.query_params.get("agent") or request.query_params.get("agent_id") or ""
    model = request.query_params.get("model") or ""
    since = request.query_params.get("from")
    until = request.query_params.get("to")
    if request.query_params.get("window") and not since and not until:
        since, until, _, _ = _window_to_range(request.query_params.get("window"))

    records = await _query_normalised_performance_records(
        request,
        agent=agent,
        model=model,
        since=since,
        until=until,
        limit=1000,
    )
    summary = _summarise_records(records)

    if fmt == "csv":
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=[
            "trace_id", "agent_role", "model", "status", "duration_ms",
            "tokens_in", "tokens_out", "cost_usd", "timestamp",
        ])
        writer.writeheader()
        for record in records:
            writer.writerow({
                "trace_id": record.get("trace_id") or record.get("id"),
                "agent_role": record.get("agent_role") or record.get("agent_id"),
                "model": record.get("model") or "",
                "status": record.get("status") or "",
                "duration_ms": record.get("duration_ms") or 0,
                "tokens_in": record.get("tokens_in") or 0,
                "tokens_out": record.get("tokens_out") or 0,
                "cost_usd": record.get("cost_usd") or 0,
                "timestamp": record.get("timestamp") or record.get("created_at") or "",
            })
        return Response(
            stream.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="metrics-export.csv"'},
        )

    payload = {
        "summary": summary,
        "records": records,
        "comparison": summary.get("comparison", []),
        "filters": {"agent": agent, "model": model, "from": since, "to": until},
    }
    return JSONResponse(payload, headers={"Content-Disposition": 'attachment; filename="metrics-export.json"'})


# ---------- N2 Alert Rules ----------

_alert_rules: list[dict] = []
_alert_rule_counter = 0


async def api_alert_rules_list(request: Request):
    """GET /api/alerts/rules — list alert rules."""
    return JSONResponse({"rules": _alert_rules})


async def api_alert_rules_create(request: Request):
    """POST /api/alerts/rules — create a new alert rule."""
    global _alert_rule_counter
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    _alert_rule_counter += 1
    rule = {
        "id": f"rule-{_alert_rule_counter}",
        "name": str(body.get("name", "")).strip() or "Rule",
        "metric": str(body.get("metric", "")).strip(),
        "event_type": str(body.get("event_type", "system.info")).strip() or "system.info",
        "severity": str(body.get("severity", "info")).strip() or "info",
        "operator": str(body.get("operator", "")).strip(),
        "threshold": body.get("threshold", 0),
        "channel": str(body.get("channel", "ui")).strip(),
        "channels": [str(channel).strip() for channel in (body.get("channels") or []) if str(channel).strip()] or [str(body.get("channel", "ui")).strip() or "ui"],
        "active": bool(body.get("active", True)),
    }
    _alert_rules.append(rule)
    return JSONResponse({"ok": True, "rule": rule}, status_code=201)


async def api_alert_rules_delete(request: Request):
    """DELETE /api/alerts/rules/{id} — delete an alert rule."""
    global _alert_rules
    rule_id = request.path_params["id"]
    before = len(_alert_rules)
    _alert_rules = [r for r in _alert_rules if r["id"] != rule_id]
    if len(_alert_rules) == before:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True})


# ---------- N5 Mute Agent ----------

_muted_agents: set[str] = set()


async def api_mute_agent(request: Request):
    """POST /api/notifications/mute/{agent_id} — mute notifications from agent."""
    agent_id = request.path_params["agent_id"]
    _muted_agents.add(agent_id)
    preferences = await _get_notification_preferences(request)
    muted = list(dict.fromkeys([*preferences.get("muted_agents", []), agent_id]))
    saved = await _save_notification_preferences(request, {**preferences, "muted_agents": muted})
    return JSONResponse({"ok": True, "muted": agent_id, "preferences": saved})


async def api_unmute_agent(request: Request):
    """DELETE /api/notifications/mute/{agent_id} — unmute agent notifications."""
    agent_id = request.path_params["agent_id"]
    _muted_agents.discard(agent_id)
    preferences = await _get_notification_preferences(request)
    muted = [item for item in preferences.get("muted_agents", []) if item != agent_id]
    saved = await _save_notification_preferences(request, {**preferences, "muted_agents": muted})
    return JSONResponse({"ok": True, "unmuted": agent_id, "preferences": saved})


monitoring_routes = [
    # Performance
    Route("/api/performance", api_performance, methods=["GET"]),
    Route("/api/performance/summary", api_performance_summary, methods=["GET"]),
    Route("/api/monitoring/summary", api_monitoring_summary, methods=["GET"]),
    Route("/api/monitoring/layout", api_monitoring_layout, methods=["GET", "PUT"]),
    Route("/api/metrics/export", api_metrics_export, methods=["GET"]),
    Route("/api/alerts", api_alerts, methods=["GET"]),
    # Alert Rules (N2)
    Route("/api/alerts/rules", api_alert_rules_list, methods=["GET"]),
    Route("/api/alerts/rules", api_alert_rules_create, methods=["POST"]),
    Route("/api/alerts/rules/{id}", api_alert_rules_delete, methods=["DELETE"]),
    # Notifications
    Route("/api/notifications/mute/{agent_id}", api_mute_agent, methods=["POST"]),
    Route("/api/notifications/mute/{agent_id}", api_unmute_agent, methods=["DELETE"]),
    Route("/api/notifications/preferences", api_notification_preferences, methods=["GET", "PUT"]),
    Route("/api/notifications", api_notifications, methods=["GET"]),
    Route("/api/notifications/read-all", api_notifications_read_all, methods=["PUT", "POST"]),
    Route("/api/notifications/{id}/read", api_notification_read, methods=["PUT", "POST"]),
    # Session Replay
    Route("/api/sessions", api_sessions, methods=["GET"]),
    Route("/api/sessions/{session_id}/events", api_session_events, methods=["GET"]),
    Route("/api/sessions/{session_id}/replay", api_session_replay, methods=["GET"]),
    Route("/api/sessions/{session_id}/export", api_session_export, methods=["GET"]),
    # API Keys
    Route("/settings/api-keys", api_keys_list, methods=["GET"]),
    Route("/settings/api-keys", api_keys_create, methods=["POST"]),
    Route("/settings/api-keys/{id}/revoke", api_keys_revoke, methods=["PUT"]),
    Route("/settings/api-keys/{id}", api_keys_delete, methods=["DELETE"]),
    # Webhooks
    Route("/settings/webhooks", webhooks_list, methods=["GET"]),
    Route("/settings/webhooks", webhooks_create, methods=["POST"]),
    Route("/settings/webhooks/{id}", webhooks_update, methods=["PUT"]),
    Route("/settings/webhooks/{id}", webhooks_delete, methods=["DELETE"]),
    Route("/settings/webhooks/{id}/test", webhooks_test, methods=["POST"]),
    Route("/api/webhooks/{id}/test", webhooks_test_api_alias, methods=["POST"]),
    # Traces
    Route("/api/traces", api_traces, methods=["GET"]),
    Route("/api/traces/{id}", api_trace_detail, methods=["GET"]),
    Route("/api/traces/{id}/tree", api_trace_tree, methods=["GET"]),
]
