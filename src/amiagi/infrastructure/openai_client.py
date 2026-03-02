"""OpenAI API client for amiagi — satisfies ChatCompletionClient Protocol.

Uses only ``urllib.request`` (no third-party HTTP libraries) for consistency
with the existing ``OllamaClient``.
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.model_io_logger import ModelIOLogger
from amiagi.infrastructure.usage_tracker import UsageTracker


class OpenAIClientError(RuntimeError):
    """Raised on any OpenAI API failure (network, auth, rate-limit, …)."""


# Marker used by ``ChatService.is_api_model()`` to detect API backends.
_IS_API_CLIENT = True

# Models we officially expose in the selection wizard.
SUPPORTED_OPENAI_MODELS: list[str] = [
    "gpt-5.3-codex",
    "gpt-5-mini",
]


def mask_api_key(key: str) -> str:
    """Return a masked version of *key* safe for logging: ``sk-...abcd``."""
    if not key or len(key) < 8:
        return "***"
    return f"{key[:3]}...{key[-4:]}"


@dataclass(frozen=True)
class OpenAIClient:
    """OpenAI chat-completions client.

    Implements the ``ChatCompletionClient`` protocol so it can be used as a
    drop-in replacement for ``OllamaClient`` inside ``ChatService`` and
    ``SupervisorService``.
    """

    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    io_logger: ModelIOLogger | None = None
    activity_logger: ActivityLogger | None = None
    client_role: str = "executor"
    request_timeout_seconds: int = 120
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0
    usage_tracker: UsageTracker | None = None

    # Used by ChatService.is_api_model() for detection.
    _is_api_client: bool = True

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _post_json(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url.rstrip('/')}{path}"
        request = Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.request_timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            raise OpenAIClientError(
                f"HTTP {error.code}: {body}"
            ) from error
        except URLError as error:
            raise OpenAIClientError(
                f"Cannot connect to OpenAI: {error.reason}"
            ) from error
        except (TimeoutError, socket.timeout) as error:
            raise OpenAIClientError(
                f"OpenAI request timeout after {self.request_timeout_seconds}s"
            ) from error

    def _get_json(self, path: str) -> dict:
        url = f"{self.base_url.rstrip('/')}{path}"
        request = Request(
            url=url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            raise OpenAIClientError(
                f"HTTP {error.code}: {body}"
            ) from error
        except URLError as error:
            raise OpenAIClientError(
                f"Cannot connect to OpenAI: {error.reason}"
            ) from error

    @staticmethod
    def _is_retryable(error: OpenAIClientError) -> bool:
        msg = str(error).lower()
        return any(
            marker in msg
            for marker in ("timeout", "timed out", "429", "rate limit", "502", "503")
        )

    # ------------------------------------------------------------------
    # ChatCompletionClient protocol
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Verify the API key by listing available models."""
        try:
            result = self._get_json("/models")
            return isinstance(result.get("data"), list)
        except OpenAIClientError:
            return False

    def list_models(self) -> list[str]:
        """Return the static list of supported OpenAI models."""
        return list(SUPPORTED_OPENAI_MODELS)

    def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        num_ctx: int | None = None,
    ) -> str:
        """Send a chat-completion request and return the assistant's reply.

        ``num_ctx`` is accepted for protocol compatibility but ignored — the
        API manages context windows internally.
        """
        endpoint = "/chat/completions"
        request_id = str(uuid.uuid4())

        api_messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]
        payload: dict = {
            "model": self.model,
            "messages": api_messages,
        }

        if self.activity_logger:
            self.activity_logger.log(
                action="openai.chat.request",
                intent="Wysłanie zapytania do OpenAI API.",
                details={
                    "request_id": request_id,
                    "model": self.model,
                    "messages": len(messages),
                    "client_role": self.client_role,
                    "api_key_masked": mask_api_key(self.api_key),
                },
            )

        if self.io_logger:
            # Mask the API key in logged payloads.
            safe_payload = {**payload, "_api_key": mask_api_key(self.api_key)}
            self.io_logger.log_input(
                request_id=request_id,
                model=self.model,
                base_url=self.base_url,
                endpoint=endpoint,
                payload=safe_payload,
            )

        attempts = max(1, self.max_retries + 1)
        result: dict | None = None
        last_error: OpenAIClientError | None = None

        for attempt in range(1, attempts + 1):
            try:
                result = self._post_json(endpoint, payload)
                break
            except OpenAIClientError as error:
                last_error = error
                retryable = self._is_retryable(error)
                if self.activity_logger:
                    self.activity_logger.log(
                        action="openai.chat.retry" if retryable and attempt < attempts else "openai.chat.error",
                        intent=(
                            "Ponowienie po błędzie tymczasowym."
                            if retryable and attempt < attempts
                            else "Zarejestrowanie błędu OpenAI."
                        ),
                        details={
                            "request_id": request_id,
                            "attempt": attempt,
                            "max_attempts": attempts,
                            "retryable": retryable,
                            "error": str(error),
                            "client_role": self.client_role,
                        },
                    )
                if not retryable or attempt >= attempts:
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
            raise OpenAIClientError("OpenAI returned no result")

        # Log full response
        if self.io_logger:
            self.io_logger.log_output(
                request_id=request_id,
                model=self.model,
                base_url=self.base_url,
                endpoint=endpoint,
                response=result,
            )

        # Track token usage
        usage = result.get("usage")
        if isinstance(usage, dict) and self.usage_tracker is not None:
            prompt_tokens = int(usage.get("prompt_tokens", 0))
            completion_tokens = int(usage.get("completion_tokens", 0))
            self.usage_tracker.record(self.model, prompt_tokens, completion_tokens)
            if self.activity_logger:
                snap = self.usage_tracker.snapshot()
                self.activity_logger.log(
                    action="openai.usage.record",
                    intent="Zarejestrowanie zużycia tokenów i kosztów.",
                    details={
                        "request_id": request_id,
                        "model": self.model,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cost_usd": snap.last_request_cost_usd,
                        "cumulative_cost_usd": snap.total_cost_usd,
                    },
                )

        if self.activity_logger:
            self.activity_logger.log(
                action="openai.chat.response",
                intent="Zarejestrowanie odpowiedzi z OpenAI API.",
                details={
                    "request_id": request_id,
                    "has_choices": bool(result.get("choices")),
                    "client_role": self.client_role,
                },
            )

        # Extract content from OpenAI response format
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenAIClientError("OpenAI response contains no choices")

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content

        raise OpenAIClientError("OpenAI returned empty response content")
