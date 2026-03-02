"""Token usage tracking and cost estimation for paid API backends."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class UsageSnapshot:
    """Immutable snapshot of cumulative and per-request token usage."""

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0
    last_request_prompt_tokens: int = 0
    last_request_completion_tokens: int = 0
    last_request_cost_usd: float = 0.0
    model: str = ""
    request_count: int = 0


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


@dataclass
class UsageTracker:
    """Thread-safe, per-session tracker of API token consumption and costs.

    Pricing is expressed as USD per 1 million tokens.
    """

    PRICING: ClassVar[dict[str, dict[str, float]]] = {
        "gpt-5.3-codex": {"prompt_per_1m": 2.00, "completion_per_1m": 8.00},
        "gpt-5-mini": {"prompt_per_1m": 0.40, "completion_per_1m": 1.60},
    }

    _total_prompt: int = field(default=0, init=False, repr=False)
    _total_completion: int = field(default=0, init=False, repr=False)
    _total_cost: float = field(default=0.0, init=False, repr=False)
    _last_prompt: int = field(default=0, init=False, repr=False)
    _last_completion: int = field(default=0, init=False, repr=False)
    _last_cost: float = field(default=0.0, init=False, repr=False)
    _last_model: str = field(default="", init=False, repr=False)
    _request_count: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # ------------------------------------------------------------------

    def record(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Register usage from a single API request."""
        pricing = self.PRICING.get(model, {})
        prompt_rate = pricing.get("prompt_per_1m", 0.0)
        completion_rate = pricing.get("completion_per_1m", 0.0)
        cost = (prompt_tokens * prompt_rate + completion_tokens * completion_rate) / 1_000_000

        with self._lock:
            self._total_prompt += prompt_tokens
            self._total_completion += completion_tokens
            self._total_cost += cost
            self._last_prompt = prompt_tokens
            self._last_completion = completion_tokens
            self._last_cost = cost
            self._last_model = model
            self._request_count += 1

    def snapshot(self) -> UsageSnapshot:
        """Return an immutable copy of the current state."""
        with self._lock:
            return UsageSnapshot(
                total_prompt_tokens=self._total_prompt,
                total_completion_tokens=self._total_completion,
                total_cost_usd=self._total_cost,
                last_request_prompt_tokens=self._last_prompt,
                last_request_completion_tokens=self._last_completion,
                last_request_cost_usd=self._last_cost,
                model=self._last_model,
                request_count=self._request_count,
            )

    def format_status_line(self) -> str:
        """One-line summary for the UI status bar.

        Example: ``☁ gpt-5.3-codex │ ⬆ 12.4k ⬇ 3.2k │ $0.23``
        """
        snap = self.snapshot()
        if snap.request_count == 0:
            return ""
        prompt_str = _format_tokens(snap.total_prompt_tokens)
        comp_str = _format_tokens(snap.total_completion_tokens)
        return (
            f"☁ {snap.model} │ "
            f"⬆ {prompt_str} ⬇ {comp_str} │ "
            f"${snap.total_cost_usd:.2f}"
        )

    def format_detailed(self) -> str:
        """Multi-line summary for the ``/api-usage`` command."""
        snap = self.snapshot()
        if snap.request_count == 0:
            return "Brak zarejestrowanego zużycia API w tej sesji."
        return (
            f"Model:               {snap.model}\n"
            f"Zapytania:           {snap.request_count}\n"
            f"Tokeny prompt:       {snap.total_prompt_tokens:,}\n"
            f"Tokeny completion:   {snap.total_completion_tokens:,}\n"
            f"Koszt łączny:        ${snap.total_cost_usd:.4f}\n"
            f"Ostatni request:     ⬆ {snap.last_request_prompt_tokens} ⬇ {snap.last_request_completion_tokens}  "
            f"${snap.last_request_cost_usd:.4f}"
        )
