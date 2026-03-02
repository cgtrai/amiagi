"""Tests for SDK client extensions — events_stream method."""

from __future__ import annotations

from amiagi.infrastructure.sdk_client import AmiagiClient


def test_events_stream_method_exists() -> None:
    client = AmiagiClient("http://localhost:9999")
    assert hasattr(client, "events_stream")
    assert callable(client.events_stream)


def test_events_stream_unreachable_yields_nothing() -> None:
    """If server is unreachable, events_stream yields nothing and returns."""
    client = AmiagiClient("http://localhost:1", timeout=1)
    events = list(client.events_stream())
    assert events == []
