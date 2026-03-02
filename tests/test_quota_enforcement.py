"""Tests for QuotaPolicy runtime enforcement methods."""

from __future__ import annotations

import time

from amiagi.domain.quota_policy import QuotaPolicy, RoleQuota


def _make_policy(**kw: int | float) -> QuotaPolicy:
    """Helper to create a QuotaPolicy with a single 'executor' role."""
    rq = RoleQuota(
        daily_token_limit=int(kw.get("daily_token_limit", 10000)),
        daily_cost_limit_usd=float(kw.get("daily_cost_limit_usd", 1.0)),
        max_requests_per_hour=int(kw.get("max_requests_per_hour", 100)),
    )
    return QuotaPolicy(role_quotas={"executor": rq})


# ---- check_request ----

def test_check_request_allowed() -> None:
    policy = _make_policy()
    ok, reason = policy.check_request("executor")
    assert ok is True
    assert reason == ""


def test_check_request_unknown_role_allowed() -> None:
    policy = _make_policy()
    ok, reason = policy.check_request("unknown_role")
    assert ok is True  # no quota means no restriction


def test_check_request_exceed_hourly_limit() -> None:
    policy = _make_policy(max_requests_per_hour=2)
    policy.check_request("executor")
    policy.record_usage("executor")
    policy.check_request("executor")
    policy.record_usage("executor")
    ok, reason = policy.check_request("executor")
    assert ok is False
    assert "hour" in reason.lower() or "godzin" in reason.lower() or "request" in reason.lower()


def test_check_request_exceed_daily_token_limit() -> None:
    policy = _make_policy(daily_token_limit=100)
    policy.record_usage("executor", tokens=100)
    ok, reason = policy.check_request("executor")
    assert ok is False
    assert "token" in reason.lower()


def test_check_request_exceed_daily_cost_limit() -> None:
    policy = _make_policy(daily_cost_limit_usd=0.50)
    policy.record_usage("executor", cost_usd=0.50)
    ok, reason = policy.check_request("executor")
    assert ok is False
    assert "cost" in reason.lower() or "koszt" in reason.lower() or "usd" in reason.lower()


# ---- record_usage ----

def test_record_usage_increments() -> None:
    policy = _make_policy()
    policy.record_usage("executor", tokens=100, cost_usd=0.01)
    usage = policy.get_usage("executor")
    assert usage["tokens_today"] == 100
    assert usage["cost_today_usd"] == 0.01
    assert usage["requests_this_hour"] == 1


def test_record_usage_accumulates() -> None:
    policy = _make_policy()
    policy.record_usage("executor", tokens=50)
    policy.record_usage("executor", tokens=30)
    usage = policy.get_usage("executor")
    assert usage["tokens_today"] == 80
    assert usage["requests_this_hour"] == 2


# ---- get_usage ----

def test_get_usage_empty() -> None:
    policy = _make_policy()
    usage = policy.get_usage("executor")
    assert usage["tokens_today"] == 0
    assert usage["cost_today_usd"] == 0.0
    assert usage["requests_this_hour"] == 0


def test_get_usage_unknown_role() -> None:
    policy = _make_policy()
    usage = policy.get_usage("nonexistent")
    assert usage["tokens_today"] == 0


# ---- reset_usage ----

def test_reset_usage() -> None:
    policy = _make_policy()
    policy.record_usage("executor", tokens=500, cost_usd=0.05)
    policy.reset_usage("executor")
    usage = policy.get_usage("executor")
    assert usage["tokens_today"] == 0
    assert usage["cost_today_usd"] == 0.0
    assert usage["requests_this_hour"] == 0


def test_reset_usage_nonexistent_role_no_error() -> None:
    policy = _make_policy()
    policy.reset_usage("nonexistent")  # Should not raise


# ---- combined flow ----

def test_flow_record_then_check_boundary() -> None:
    policy = _make_policy(daily_token_limit=1000, max_requests_per_hour=5)
    for _ in range(4):
        policy.record_usage("executor", tokens=100)
    ok, _ = policy.check_request("executor")
    assert ok is True
    policy.record_usage("executor", tokens=100)
    ok, reason = policy.check_request("executor")
    assert ok is False


def test_zero_quota_means_unlimited() -> None:
    """A quota field of 0 means no limit for that dimension."""
    policy = QuotaPolicy(role_quotas={
        "executor": RoleQuota(daily_token_limit=0, daily_cost_limit_usd=0.0, max_requests_per_hour=0)
    })
    for _ in range(100):
        policy.record_usage("executor", tokens=9999, cost_usd=99.99)
    ok, reason = policy.check_request("executor")
    assert ok is True
