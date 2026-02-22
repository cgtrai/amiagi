from __future__ import annotations

import json
from pathlib import Path

from amiagi.infrastructure.activity_logger import ActivityLogger


def test_activity_logger_writes_jsonl_record(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "activity.jsonl"
    logger = ActivityLogger(log_path)

    logger.log(
        action="chat.ask",
        intent="Obsługa wiadomości użytkownika.",
        details={"chars": 12},
    )

    line = log_path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)

    assert payload["action"] == "chat.ask"
    assert payload["intent"]
    assert payload["details"]["chars"] == 12
