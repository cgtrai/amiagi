"""RateLimiter — token-bucket rate limiting per backend."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class BucketConfig:
    """Configuration for a single token bucket."""

    max_tokens: float = 60.0  # burst capacity
    refill_rate: float = 1.0  # tokens per second
    name: str = ""


@dataclass
class _Bucket:
    """Internal token bucket state."""

    config: BucketConfig
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.monotonic)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(
            self.config.max_tokens,
            self.tokens + elapsed * self.config.refill_rate,
        )
        self.last_refill = now

    def try_acquire(self, cost: float = 1.0) -> bool:
        self._refill()
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    def wait_time(self, cost: float = 1.0) -> float:
        """Seconds to wait until *cost* tokens are available."""
        self._refill()
        if self.tokens >= cost:
            return 0.0
        deficit = cost - self.tokens
        return deficit / max(self.config.refill_rate, 1e-9)


class RateLimiter:
    """Token-bucket rate limiter, one bucket per named backend.

    Thread-safe.  Supports both non-blocking ``try_acquire`` and blocking
    ``acquire`` with exponential backoff.

    Usage::

        limiter = RateLimiter()
        limiter.add_backend("ollama", BucketConfig(max_tokens=10, refill_rate=2))
        if limiter.try_acquire("ollama"):
            ... # proceed
        else:
            limiter.acquire("ollama")  # blocks until token available
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def add_backend(self, name: str, config: BucketConfig) -> None:
        """Register a new backend bucket or update existing."""
        with self._lock:
            config.name = name
            self._buckets[name] = _Bucket(config=config, tokens=config.max_tokens)

    def remove_backend(self, name: str) -> None:
        with self._lock:
            self._buckets.pop(name, None)

    def try_acquire(self, backend: str, cost: float = 1.0) -> bool:
        """Non-blocking: return ``True`` if token acquired."""
        with self._lock:
            bucket = self._buckets.get(backend)
            if bucket is None:
                return True  # no limiter = allow
            return bucket.try_acquire(cost)

    def acquire(
        self,
        backend: str,
        cost: float = 1.0,
        *,
        max_wait_seconds: float = 60.0,
        backoff_factor: float = 1.5,
    ) -> bool:
        """Blocking acquire with exponential backoff.

        Returns ``True`` if acquired within *max_wait_seconds*, else ``False``.
        """
        deadline = time.monotonic() + max_wait_seconds
        wait = 0.05  # initial wait

        while True:
            with self._lock:
                bucket = self._buckets.get(backend)
                if bucket is None:
                    return True
                if bucket.try_acquire(cost):
                    return True
                estimated_wait = bucket.wait_time(cost)

            sleep_time = min(wait, estimated_wait, deadline - time.monotonic())
            if sleep_time <= 0:
                return False
            time.sleep(sleep_time)
            wait = min(wait * backoff_factor, 5.0)

            if time.monotonic() >= deadline:
                return False

    def wait_time(self, backend: str, cost: float = 1.0) -> float:
        """Estimated seconds until *cost* tokens are available."""
        with self._lock:
            bucket = self._buckets.get(backend)
            if bucket is None:
                return 0.0
            return bucket.wait_time(cost)

    def status(self) -> dict[str, dict[str, float]]:
        """Per-backend status: available tokens, refill rate."""
        result: dict[str, dict[str, float]] = {}
        with self._lock:
            for name, bucket in self._buckets.items():
                bucket._refill()
                result[name] = {
                    "available_tokens": round(bucket.tokens, 2),
                    "max_tokens": bucket.config.max_tokens,
                    "refill_rate": bucket.config.refill_rate,
                }
        return result

    def list_backends(self) -> list[str]:
        return sorted(self._buckets.keys())
