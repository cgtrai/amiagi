"""Helpers for runtime usage and cost metrics used by web surfaces."""

from __future__ import annotations

from typing import Any


def _get_energy_tracker(state: Any) -> Any:
    chat_service = getattr(state, "chat_service", None)
    if chat_service is None:
        return None
    return getattr(chat_service, "energy_tracker", None)


def get_session_usage_metrics(state: Any) -> dict[str, Any]:
    """Return consolidated session usage metrics for the current app state."""
    budget_mgr = getattr(state, "budget_manager", None)
    session_budget = getattr(budget_mgr, "session_budget", None) if budget_mgr is not None else None

    tokens_used = int(getattr(session_budget, "tokens_used", 0) or 0)
    budget_cost = float(getattr(session_budget, "spent_usd", 0.0) or 0.0)
    budget_limit = float(getattr(session_budget, "limit_usd", 0.0) or 0.0)
    budget_pct = float(getattr(session_budget, "utilization_pct", 0.0) or 0.0)
    currency = str(getattr(budget_mgr, "currency", "USD") or "USD")

    energy_cost = 0.0
    tracker = _get_energy_tracker(state)
    if tracker is not None and hasattr(tracker, "summary"):
        try:
            summary = tracker.summary()
            energy_cost = float(getattr(summary, "total_cost_local", 0.0) or 0.0)
            tracker_currency = str(getattr(summary, "currency", "") or "").strip()
            if tracker_currency:
                currency = tracker_currency
        except Exception:
            energy_cost = 0.0

    return {
        "tokens_used": tokens_used,
        "budget_cost": budget_cost,
        "energy_cost": energy_cost,
        "total_cost": budget_cost + energy_cost,
        "budget_limit": budget_limit,
        "budget_pct": budget_pct,
        "currency": currency,
    }


def apply_budget_config(
    state: Any,
    *,
    currency: str | None = None,
    energy_price_kwh: float | None = None,
    token_cost_1k: float | None = None,
) -> dict[str, Any]:
    """Apply cost configuration to runtime budget and energy trackers."""
    budget_mgr = getattr(state, "budget_manager", None)
    if budget_mgr is not None:
        if currency is not None:
            budget_mgr.currency = str(currency or "USD")
        if energy_price_kwh is not None:
            budget_mgr.energy_price_kwh = max(0.0, float(energy_price_kwh))
        if token_cost_1k is not None:
            budget_mgr.token_cost_1k = max(0.0, float(token_cost_1k))

    resolved_currency = str(
        currency
        if currency is not None
        else getattr(budget_mgr, "currency", "USD")
        or "USD"
    )
    resolved_energy_price = max(
        0.0,
        float(
            energy_price_kwh
            if energy_price_kwh is not None
            else getattr(budget_mgr, "energy_price_kwh", 0.0)
            or 0.0
        ),
    )
    resolved_token_cost = max(
        0.0,
        float(
            token_cost_1k
            if token_cost_1k is not None
            else getattr(budget_mgr, "token_cost_1k", 0.0)
            or 0.0
        ),
    )

    tracker = _get_energy_tracker(state)
    if tracker is not None and hasattr(tracker, "set_price_per_kwh"):
        tracker.set_price_per_kwh(resolved_energy_price, resolved_currency)

    return {
        "currency": resolved_currency,
        "energy_price_kwh": resolved_energy_price,
        "token_cost_1k": resolved_token_cost,
    }