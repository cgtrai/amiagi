"""Tests for RateLimiter."""

from __future__ import annotations

import time

from amiagi.infrastructure.rate_limiter import BucketConfig, RateLimiter


class TestRateLimiter:
    def test_no_backend_allows(self) -> None:
        limiter = RateLimiter()
        assert limiter.try_acquire("unknown") is True

    def test_acquire_within_burst(self) -> None:
        limiter = RateLimiter()
        limiter.add_backend("ollama", BucketConfig(max_tokens=5, refill_rate=1))
        for _ in range(5):
            assert limiter.try_acquire("ollama") is True
        assert limiter.try_acquire("ollama") is False

    def test_refill_over_time(self) -> None:
        limiter = RateLimiter()
        limiter.add_backend("api", BucketConfig(max_tokens=2, refill_rate=100))
        # Exhaust
        limiter.try_acquire("api")
        limiter.try_acquire("api")
        assert limiter.try_acquire("api") is False
        # Wait for refill (100 tokens/s → 10ms should give ~1 token)
        time.sleep(0.02)
        assert limiter.try_acquire("api") is True

    def test_wait_time(self) -> None:
        limiter = RateLimiter()
        limiter.add_backend("x", BucketConfig(max_tokens=1, refill_rate=10))
        limiter.try_acquire("x")
        wt = limiter.wait_time("x")
        assert wt > 0
        assert wt < 1.0

    def test_wait_time_no_backend(self) -> None:
        limiter = RateLimiter()
        assert limiter.wait_time("nope") == 0.0

    def test_remove_backend(self) -> None:
        limiter = RateLimiter()
        limiter.add_backend("x", BucketConfig())
        limiter.remove_backend("x")
        assert limiter.try_acquire("x") is True

    def test_status(self) -> None:
        limiter = RateLimiter()
        limiter.add_backend("a", BucketConfig(max_tokens=10, refill_rate=2))
        s = limiter.status()
        assert "a" in s
        assert s["a"]["max_tokens"] == 10
        assert s["a"]["refill_rate"] == 2

    def test_list_backends(self) -> None:
        limiter = RateLimiter()
        limiter.add_backend("b", BucketConfig())
        limiter.add_backend("a", BucketConfig())
        assert limiter.list_backends() == ["a", "b"]

    def test_blocking_acquire(self) -> None:
        limiter = RateLimiter()
        limiter.add_backend("fast", BucketConfig(max_tokens=1, refill_rate=200))
        limiter.try_acquire("fast")  # exhaust
        result = limiter.acquire("fast", max_wait_seconds=0.5)
        assert result is True

    def test_acquire_timeout(self) -> None:
        limiter = RateLimiter()
        limiter.add_backend("slow", BucketConfig(max_tokens=1, refill_rate=0.01))
        limiter.try_acquire("slow")  # exhaust
        result = limiter.acquire("slow", max_wait_seconds=0.05)
        assert result is False
