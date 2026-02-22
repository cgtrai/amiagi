from __future__ import annotations

import socket
import urllib.error

from amiagi.infrastructure.ollama_client import OllamaClient, OllamaClientError


class DummyLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.records.append(kwargs)


def test_chat_retries_on_timeout(monkeypatch) -> None:
    attempts = {"count": 0}

    def fake_post(self, path, payload):  # type: ignore[no-untyped-def]
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OllamaClientError("Ollama request timeout after 1s")
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(OllamaClient, "_post_json", fake_post)

    client = OllamaClient(
        base_url="http://127.0.0.1:11434",
        model="dummy",
        io_logger=None,
        activity_logger=None,
        max_retries=1,
        retry_backoff_seconds=0.0,
    )

    response = client.chat(messages=[{"role": "user", "content": "hi"}], system_prompt="sys")

    assert response == "ok"
    assert attempts["count"] == 2


def test_chat_does_not_retry_http_error(monkeypatch) -> None:
    attempts = {"count": 0}

    def fake_post(self, path, payload):  # type: ignore[no-untyped-def]
        attempts["count"] += 1
        raise OllamaClientError("HTTP 400: bad request")

    monkeypatch.setattr(OllamaClient, "_post_json", fake_post)

    client = OllamaClient(
        base_url="http://127.0.0.1:11434",
        model="dummy",
        io_logger=None,
        activity_logger=None,
        max_retries=3,
        retry_backoff_seconds=0.0,
    )

    try:
        client.chat(messages=[{"role": "user", "content": "hi"}], system_prompt="sys")
        assert False, "Expected OllamaClientError"
    except OllamaClientError as error:
        assert "HTTP 400" in str(error)

    assert attempts["count"] == 1


def test_is_retryable_error_handles_connectivity_markers() -> None:
    assert OllamaClient._is_retryable_error(OllamaClientError("Cannot connect to Ollama: timed out"))
    assert OllamaClient._is_retryable_error(OllamaClientError("Ollama request timeout after 30s"))
    assert not OllamaClient._is_retryable_error(OllamaClientError("HTTP 500: internal server error"))


def test_post_json_wraps_socket_timeout(monkeypatch) -> None:
    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        raise socket.timeout("timed out")

    monkeypatch.setattr("amiagi.infrastructure.ollama_client.urlopen", fake_urlopen)

    client = OllamaClient(base_url="http://127.0.0.1:11434", model="dummy", request_timeout_seconds=1)

    try:
        client._post_json("/api/chat", {"x": 1})
        assert False, "Expected OllamaClientError"
    except OllamaClientError as error:
        assert "timeout" in str(error).lower()


def test_post_json_wraps_urlerror_timeout(monkeypatch) -> None:
    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr("amiagi.infrastructure.ollama_client.urlopen", fake_urlopen)

    client = OllamaClient(base_url="http://127.0.0.1:11434", model="dummy", request_timeout_seconds=1)

    try:
        client._post_json("/api/chat", {"x": 1})
        assert False, "Expected OllamaClientError"
    except OllamaClientError as error:
        assert "cannot connect to ollama" in str(error).lower()
