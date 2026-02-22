from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "tak", "t"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "nie"}:
        return False
    return default


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = (
        "hf.co/TeichAI/Qwen3-14B-Claude-4.5-Opus-High-Reasoning-Distill-GGUF:Q4_K_M"
    )
    db_path: Path = Path("./data/amiagi.db")
    model_io_log_path: Path = Path("./logs/model_io.jsonl")
    executor_model_io_log_path: Path = Path("./logs/model_io_executor.jsonl")
    supervisor_model_io_log_path: Path = Path("./logs/model_io_supervisor.jsonl")
    supervisor_dialogue_log_path: Path = Path("./logs/supervision_dialogue.jsonl")
    activity_log_path: Path = Path("./logs/activity.jsonl")
    shell_policy_path: Path = Path("./config/shell_allowlist.json")
    work_dir: Path = Path("./amiagi-my-work")
    max_context_memories: int = 5
    ollama_request_timeout_seconds: int = 300
    ollama_max_retries: int = 1
    ollama_retry_backoff_seconds: float = 0.75
    supervisor_enabled: bool = True
    supervisor_model: str = "cogito:14b"
    supervisor_max_repair_rounds: int = 2
    supervisor_request_timeout_seconds: int = 120
    supervisor_min_free_vram_mb: int = 3000
    model_queue_max_wait_seconds: float = 1.0
    autonomous_mode: bool = False
    max_idle_autoreactivations: int = 2

    @staticmethod
    def from_env() -> "Settings":
        retry_backoff_raw = os.getenv("OLLAMA_RETRY_BACKOFF_SECONDS", "0.75")
        try:
            retry_backoff = float(retry_backoff_raw)
        except (TypeError, ValueError):
            retry_backoff = 0.75

        queue_wait_raw = os.getenv("AMIAGI_MODEL_QUEUE_MAX_WAIT_SECONDS", "1.0")
        try:
            queue_wait = float(queue_wait_raw)
        except (TypeError, ValueError):
            queue_wait = 1.0

        return Settings(
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv(
                "OLLAMA_MODEL",
                "hf.co/TeichAI/Qwen3-14B-Claude-4.5-Opus-High-Reasoning-Distill-GGUF:Q4_K_M",
            ),
            db_path=Path(os.getenv("AMIAGI_DB_PATH", "./data/amiagi.db")),
            model_io_log_path=Path(
                os.getenv("AMIAGI_MODEL_IO_LOG_PATH", "./logs/model_io.jsonl")
            ),
            executor_model_io_log_path=Path(
                os.getenv(
                    "AMIAGI_EXECUTOR_MODEL_IO_LOG_PATH",
                    os.getenv("AMIAGI_MODEL_IO_LOG_PATH", "./logs/model_io_executor.jsonl"),
                )
            ),
            supervisor_model_io_log_path=Path(
                os.getenv(
                    "AMIAGI_SUPERVISOR_MODEL_IO_LOG_PATH",
                    "./logs/model_io_supervisor.jsonl",
                )
            ),
            supervisor_dialogue_log_path=Path(
                os.getenv(
                    "AMIAGI_SUPERVISOR_DIALOGUE_LOG_PATH",
                    "./logs/supervision_dialogue.jsonl",
                )
            ),
            activity_log_path=Path(
                os.getenv("AMIAGI_ACTIVITY_LOG_PATH", "./logs/activity.jsonl")
            ),
            shell_policy_path=Path(
                os.getenv("AMIAGI_SHELL_POLICY_PATH", "./config/shell_allowlist.json")
            ),
            work_dir=Path(os.getenv("AMIAGI_WORK_DIR", "./amiagi-my-work")),
            max_context_memories=_as_int(
                os.getenv("AMIAGI_MAX_CONTEXT_MEMORIES", "5"),
                default=5,
            ),
            ollama_request_timeout_seconds=_as_int(
                os.getenv("OLLAMA_REQUEST_TIMEOUT_SECONDS", "300"),
                default=300,
            ),
            ollama_max_retries=_as_int(
                os.getenv("OLLAMA_MAX_RETRIES", "1"),
                default=1,
            ),
            ollama_retry_backoff_seconds=max(0.0, retry_backoff),
            supervisor_enabled=_as_bool(
                os.getenv("AMIAGI_SUPERVISOR_ENABLED", "true"),
                default=True,
            ),
            supervisor_model=os.getenv("AMIAGI_SUPERVISOR_MODEL", "cogito:14b"),
            supervisor_max_repair_rounds=max(
                0,
                _as_int(
                    os.getenv("AMIAGI_SUPERVISOR_MAX_REPAIR_ROUNDS", "2"),
                    default=2,
                ),
            ),
            supervisor_request_timeout_seconds=max(
                5,
                _as_int(
                    os.getenv("AMIAGI_SUPERVISOR_REQUEST_TIMEOUT_SECONDS", "120"),
                    default=120,
                ),
            ),
            supervisor_min_free_vram_mb=max(
                0,
                _as_int(
                    os.getenv("AMIAGI_SUPERVISOR_MIN_FREE_VRAM_MB", "3000"),
                    default=3000,
                ),
            ),
            model_queue_max_wait_seconds=max(0.05, queue_wait),
            autonomous_mode=_as_bool(
                os.getenv("AMIAGI_AUTONOMOUS_MODE", "false"),
                default=False,
            ),
            max_idle_autoreactivations=max(
                0,
                _as_int(
                    os.getenv("AMIAGI_MAX_IDLE_AUTOREACTIVATIONS", "2"),
                    default=2,
                ),
            ),
        )
