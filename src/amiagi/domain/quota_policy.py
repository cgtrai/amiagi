"""QuotaPolicy — configurable per-role resource quotas."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
class QuotaPolicy:
    """Holds per-role quotas for the entire system.

    Example::

        policy = QuotaPolicy()
        policy.set_role("executor", RoleQuota(daily_token_limit=100_000))
        q = policy.get_role("executor")
    """

    role_quotas: dict[str, RoleQuota] = field(default_factory=dict)

    def set_role(self, role: str, quota: RoleQuota) -> None:
        self.role_quotas[role] = quota

    def get_role(self, role: str) -> RoleQuota | None:
        return self.role_quotas.get(role)

    def list_roles(self) -> list[str]:
        return sorted(self.role_quotas.keys())

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
