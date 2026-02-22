from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ModelIOLogger:
    def __init__(self, log_path: Path, model_role: str = "executor") -> None:
        self._log_path = log_path
        self._model_role = model_role
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, payload: dict[str, Any]) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model_role": self._model_role,
            **payload,
        }
        with self._log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_input(
        self,
        *,
        request_id: str,
        model: str,
        base_url: str,
        endpoint: str,
        payload: dict[str, Any],
    ) -> None:
        self._write(
            {
                "event": "model_input",
                "request_id": request_id,
                "model": model,
                "base_url": base_url,
                "endpoint": endpoint,
                "payload": payload,
            }
        )

    def log_output(
        self,
        *,
        request_id: str,
        model: str,
        base_url: str,
        endpoint: str,
        response: dict[str, Any],
    ) -> None:
        self._write(
            {
                "event": "model_output",
                "request_id": request_id,
                "model": model,
                "base_url": base_url,
                "endpoint": endpoint,
                "response": response,
            }
        )

    def log_error(
        self,
        *,
        request_id: str,
        model: str,
        base_url: str,
        endpoint: str,
        error: str,
    ) -> None:
        self._write(
            {
                "event": "model_error",
                "request_id": request_id,
                "model": model,
                "base_url": base_url,
                "endpoint": endpoint,
                "error": error,
            }
        )
