from __future__ import annotations

import json
from pathlib import Path

from amiagi.infrastructure.model_io_logger import ModelIOLogger


def test_model_io_logger_writes_jsonl_records(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "model_io.jsonl"
    logger = ModelIOLogger(log_path, model_role="executor")

    logger.log_input(
        request_id="req-1",
        model="test-model",
        base_url="http://127.0.0.1:11434",
        endpoint="/api/chat",
        payload={"messages": [{"role": "user", "content": "hello"}]},
    )
    logger.log_output(
        request_id="req-1",
        model="test-model",
        base_url="http://127.0.0.1:11434",
        endpoint="/api/chat",
        response={"message": {"content": "hi"}},
    )

    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    second = json.loads(lines[1])

    assert first["event"] == "model_input"
    assert second["event"] == "model_output"
    assert first["request_id"] == second["request_id"] == "req-1"
    assert first["model_role"] == second["model_role"] == "executor"
