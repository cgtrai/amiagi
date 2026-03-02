"""Tests for QuotaPolicy domain model."""

from __future__ import annotations

import json
from pathlib import Path

from amiagi.domain.quota_policy import QuotaPolicy, RoleQuota


class TestRoleQuota:
    def test_defaults(self) -> None:
        q = RoleQuota()
        assert q.daily_token_limit == 0
        assert q.daily_cost_limit_usd == 0.0
        assert q.max_requests_per_hour == 0

    def test_roundtrip_dict(self) -> None:
        q = RoleQuota(daily_token_limit=50_000, daily_cost_limit_usd=1.5, max_requests_per_hour=100)
        restored = RoleQuota.from_dict(q.to_dict())
        assert restored.daily_token_limit == 50_000
        assert restored.daily_cost_limit_usd == 1.5
        assert restored.max_requests_per_hour == 100


class TestQuotaPolicy:
    def test_set_and_get_role(self) -> None:
        p = QuotaPolicy()
        p.set_role("executor", RoleQuota(daily_token_limit=100_000))
        q = p.get_role("executor")
        assert q is not None
        assert q.daily_token_limit == 100_000

    def test_get_nonexistent_role_returns_none(self) -> None:
        p = QuotaPolicy()
        assert p.get_role("ghost") is None

    def test_list_roles(self) -> None:
        p = QuotaPolicy()
        p.set_role("b_role", RoleQuota())
        p.set_role("a_role", RoleQuota())
        assert p.list_roles() == ["a_role", "b_role"]

    def test_roundtrip_dict(self) -> None:
        p = QuotaPolicy()
        p.set_role("executor", RoleQuota(daily_token_limit=50_000))
        p.set_role("supervisor", RoleQuota(daily_cost_limit_usd=2.0))
        restored = QuotaPolicy.from_dict(p.to_dict())
        assert restored.get_role("executor") is not None
        assert restored.get_role("executor").daily_token_limit == 50_000  # type: ignore[union-attr]
        assert restored.get_role("supervisor").daily_cost_limit_usd == 2.0  # type: ignore[union-attr]

    def test_save_load_json(self, tmp_path: Path) -> None:
        p = QuotaPolicy()
        p.set_role("executor", RoleQuota(max_requests_per_hour=200))
        path = tmp_path / "quotas.json"
        p.save_json(path)
        loaded = QuotaPolicy.load_json(path)
        assert loaded.get_role("executor") is not None
        assert loaded.get_role("executor").max_requests_per_hour == 200  # type: ignore[union-attr]

    def test_overwrite_role(self) -> None:
        p = QuotaPolicy()
        p.set_role("x", RoleQuota(daily_token_limit=100))
        p.set_role("x", RoleQuota(daily_token_limit=999))
        assert p.get_role("x").daily_token_limit == 999  # type: ignore[union-attr]
