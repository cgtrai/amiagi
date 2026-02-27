from __future__ import annotations

import json
import socket
import time
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from amiagi.application.model_queue_policy import ModelQueuePolicy
from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.model_io_logger import ModelIOLogger
from amiagi.infrastructure.vram_advisor import VramAdvisor


class OllamaClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class OllamaClient:
    base_url: str
    model: str
    io_logger: ModelIOLogger | None = None
    activity_logger: ActivityLogger | None = None
    default_num_ctx: int = 4096
    request_timeout_seconds: int = 300
    max_retries: int = 1
    retry_backoff_seconds: float = 0.75
    client_role: str = "executor"
    queue_policy: ModelQueuePolicy | None = None
    vram_advisor: VramAdvisor | None = None

    def _get_json(self, path: str) -> dict:
        request = Request(
            url=f"{self.base_url.rstrip('/')}{path}",
            headers={"Content-Type": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            raise OllamaClientError(f"HTTP {error.code}: {body}") from error
        except URLError as error:
            raise OllamaClientError(f"Cannot connect to Ollama: {error.reason}") from error

    def _post_json(self, path: str, payload: dict) -> dict:
        request = Request(
            url=f"{self.base_url.rstrip('/')}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.request_timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            raise OllamaClientError(f"HTTP {error.code}: {body}") from error
        except URLError as error:
            raise OllamaClientError(f"Cannot connect to Ollama: {error.reason}") from error
        except (TimeoutError, socket.timeout) as error:
            raise OllamaClientError(f"Ollama request timeout after {self.request_timeout_seconds}s") from error

    @staticmethod
    def _is_retryable_error(error: OllamaClientError) -> bool:
        message = str(error).lower()
        retry_markers = [
            "timeout",
            "timed out",
            "cannot connect to ollama",
            "connection reset",
            "connection aborted",
            "temporarily unavailable",
        ]
        return any(marker in message for marker in retry_markers)

    def ping(self) -> bool:
        try:
            _ = self._get_json("/api/tags")
            return True
        except OllamaClientError:
            return False

    def list_models(self) -> list[str]:
        payload = self._get_json("/api/tags")
        models = payload.get("models", [])
        if not isinstance(models, list):
            return []
        names: list[str] = []
        seen: set[str] = set()
        for item in models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    def chat(self, messages: list[dict[str, str]], system_prompt: str, num_ctx: int | None = None) -> str:
        endpoint = "/api/chat"
        request_id = str(uuid.uuid4())
        role = "supervisor" if self.client_role == "supervisor" else "executor"

        if self.queue_policy is not None:
            acquired = self.queue_policy.acquire(role)
            if not acquired:
                raise OllamaClientError(f"Model queue timeout for role={role}")
        else:
            acquired = False

        try:
            if self.queue_policy is not None:
                can_run, free_mb = self.queue_policy.can_run_for_vram(role, self.vram_advisor)
                if not can_run:
                    if self.activity_logger:
                        self.activity_logger.log(
                            action="model.chat.skipped.low_vram",
                            intent="Pominięto wywołanie modelu przez politykę kolejki i limit VRAM.",
                            details={
                                "request_id": request_id,
                                "client_role": self.client_role,
                                "free_mb": free_mb,
                            },
                        )
                    raise OllamaClientError(
                        f"Model call skipped: low VRAM for role={role}, free_mb={free_mb}"
                    )

            effective_num_ctx = num_ctx if num_ctx is not None else self.default_num_ctx
            payload = {
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *messages,
                ],
                "options": {
                    "num_ctx": effective_num_ctx,
                },
            }
            if self.activity_logger:
                self.activity_logger.log(
                    action="model.chat.request",
                    intent="Wysłanie zapytania do modelu z parametrami bezpiecznymi względem VRAM.",
                    details={
                        "request_id": request_id,
                        "num_ctx": effective_num_ctx,
                        "messages": len(messages),
                        "client_role": self.client_role,
                    },
                )
            if self.io_logger:
                self.io_logger.log_input(
                    request_id=request_id,
                    model=self.model,
                    base_url=self.base_url,
                    endpoint=endpoint,
                    payload=payload,
                )

            attempts = max(1, self.max_retries + 1)
            result: dict | None = None
            last_error: OllamaClientError | None = None
            for attempt in range(1, attempts + 1):
                try:
                    result = self._post_json(endpoint, payload)
                    break
                except OllamaClientError as error:
                    last_error = error
                    is_retryable = self._is_retryable_error(error)
                    if self.activity_logger:
                        self.activity_logger.log(
                            action="model.chat.retry" if is_retryable and attempt < attempts else "model.chat.error",
                            intent=(
                                "Próba ponowienia wywołania modelu po błędzie chwilowym."
                                if is_retryable and attempt < attempts
                                else "Zarejestrowanie błędu wywołania modelu."
                            ),
                            details={
                                "request_id": request_id,
                                "attempt": attempt,
                                "max_attempts": attempts,
                                "retryable": is_retryable,
                                "error": str(error),
                                "client_role": self.client_role,
                            },
                        )
                    if not is_retryable or attempt >= attempts:
                        if self.io_logger:
                            self.io_logger.log_error(
                                request_id=request_id,
                                model=self.model,
                                base_url=self.base_url,
                                endpoint=endpoint,
                                error=str(error),
                            )
                        raise
                    time.sleep(self.retry_backoff_seconds * attempt)

            if result is None and last_error is not None:
                raise last_error
            if result is None:
                raise OllamaClientError("Ollama returned no result")

            if self.io_logger:
                self.io_logger.log_output(
                    request_id=request_id,
                    model=self.model,
                    base_url=self.base_url,
                    endpoint=endpoint,
                    response=result,
                )
            if self.activity_logger:
                self.activity_logger.log(
                    action="model.chat.response",
                    intent="Zarejestrowanie odpowiedzi modelu.",
                    details={
                        "request_id": request_id,
                        "has_message": bool(result.get("message")),
                        "client_role": self.client_role,
                    },
                )

            message = result.get("message", {})
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content

            thinking = message.get("thinking")
            if isinstance(thinking, str) and thinking.strip():
                if self.activity_logger:
                    self.activity_logger.log(
                        action="model.chat.response.fallback",
                        intent="Użyto pola thinking jako fallback, bo content był pusty.",
                        details={"request_id": request_id, "client_role": self.client_role},
                    )
                return thinking

            raise OllamaClientError("Ollama returned empty response")
        finally:
            if acquired and self.queue_policy is not None:
                self.queue_policy.release(role)
