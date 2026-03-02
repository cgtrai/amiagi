"""QuotaPolicy — configurable per-role resource quotas with runtime enforcement."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]

    _HAS_YAML = True
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False


@dataclass
class RoleQuota:
    """Resource limits for a specific agent role."""

    daily_token_limit: int = 0  # 0 = unlimited
    daily_cost_limit_usd: float = 0.0  # 0 = unlimited
    max_requests_per_hour: int = 0  # 0 = unlimited

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily_token_limit": self.daily_token_limit,
            "daily_cost_limit_usd": self.daily_cost_limit_usd,
            "max_requests_per_hour": self.max_requests_per_hour,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "RoleQuota":
        return RoleQuota(
            daily_token_limit=int(data.get("daily_token_limit", 0)),
            daily_cost_limit_usd=float(data.get("daily_cost_limit_usd", 0.0)),
            max_requests_per_hour=int(data.get("max_requests_per_hour", 0)),
        )


@dataclass
class _UsageRecord:
    """Internal usage tracking for quota enforcement."""

    tokens_today: int = 0
    cost_today_usd: float = 0.0
    requests_this_hour: int = 0
    day_start: float = field(default_factory=time.time)
    hour_start: float = field(default_factory=time.time)


@dataclass
class QuotaPolicy:
    """Holds per-role quotas with runtime enforcement.

    Example::

        policy = QuotaPolicy()
        policy.set_role("executor", RoleQuota(daily_token_limit=100_000))
        ok, reason = policy.check_request("executor")
        if ok:
            policy.record_usage("executor", tokens=500, cost_usd=0.01)
    """

    role_quotas: dict[str, RoleQuota] = field(default_factory=dict)
    _usage: dict[str, _UsageRecord] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_role(self, role: str, quota: RoleQuota) -> None:
        self.role_quotas[role] = quota

    def get_role(self, role: str) -> RoleQuota | None:
        return self.role_quotas.get(role)

    def list_roles(self) -> list[str]:
        return sorted(self.role_quotas.keys())

    # ---- runtime enforcement ----

    def check_request(self, role: str) -> tuple[bool, str]:
        """Check if a new request is allowed under the role's quota.

        Returns ``(True, "")`` if allowed, ``(False, reason)`` if denied.
        """
        quota = self.role_quotas.get(role)
        if quota is None:
            return True, ""  # no quota defined = unlimited

        with self._lock:
            usage = self._get_or_create_usage(role)
            self._maybe_reset_counters(usage)

            if quota.max_requests_per_hour > 0 and usage.requests_this_hour >= quota.max_requests_per_hour:
                return False, f"hourly request limit reached ({quota.max_requests_per_hour}/h)"

            if quota.daily_token_limit > 0 and usage.tokens_today >= quota.daily_token_limit:
                return False, f"daily token limit reached ({quota.daily_token_limit})"

            if quota.daily_cost_limit_usd > 0 and usage.cost_today_usd >= quota.daily_cost_limit_usd:
                return False, f"daily cost limit reached (${quota.daily_cost_limit_usd:.2f})"

        return True, ""

    def record_usage(
        self,
        role: str,
        *,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Record usage after a successful request."""
        with self._lock:
            usage = self._get_or_create_usage(role)
            self._maybe_reset_counters(usage)
            usage.tokens_today += tokens
            usage.cost_today_usd += cost_usd
            usage.requests_this_hour += 1

    def get_usage(self, role: str) -> dict[str, Any]:
        """Return current usage stats for a role."""
        with self._lock:
            usage = self._usage.get(role)
            if usage is None:
                return {"tokens_today": 0, "cost_today_usd": 0.0, "requests_this_hour": 0}
            self._maybe_reset_counters(usage)
            return {
                "tokens_today": usage.tokens_today,
                "cost_today_usd": round(usage.cost_today_usd, 6),
                "requests_this_hour": usage.requests_this_hour,
            }

    def reset_usage(self, role: str) -> None:
        """Reset usage counters for a role."""
        with self._lock:
            self._usage.pop(role, None)

    def _get_or_create_usage(self, role: str) -> _UsageRecord:
        if role not in self._usage:
            self._usage[role] = _UsageRecord()
        return self._usage[role]

    def _maybe_reset_counters(self, usage: _UsageRecord) -> None:
        """Reset counters if a new day/hour has started."""
        now = time.time()
        if now - usage.day_start > 86400:
            usage.tokens_today = 0
            usage.cost_today_usd = 0.0
            usage.day_start = now
        if now - usage.hour_start > 3600:
            usage.requests_this_hour = 0
            usage.hour_start = now

    def to_dict(self) -> dict[str, Any]:
        return {role: q.to_dict() for role, q in self.role_quotas.items()}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "QuotaPolicy":
        policy = QuotaPolicy()
        for role, q_data in data.items():
            policy.set_role(role, RoleQuota.from_dict(q_data))
        return policy

    # ---- persistence ----

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def load_json(path: Path) -> "QuotaPolicy":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return QuotaPolicy.from_dict(raw)

    def save_yaml(self, path: Path) -> None:
        """Save quotas to a YAML file."""
        if not _HAS_YAML:
            raise RuntimeError("PyYAML is required: pip install pyyaml")
        assert yaml is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(self.to_dict(), default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

    @staticmethod
    def load_yaml(path: Path) -> "QuotaPolicy":
        """Load quotas from a YAML file."""
        if not _HAS_YAML:
            raise RuntimeError("PyYAML is required: pip install pyyaml")
        assert yaml is not None
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return QuotaPolicy.from_dict(raw)
