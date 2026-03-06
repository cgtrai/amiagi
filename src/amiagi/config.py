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
    router_mailbox_log_path: Path = Path("./logs/router_mailbox.jsonl")
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
    supervisor_min_free_vram_mb: int = 0
    model_queue_max_wait_seconds: float = 1.0
    autonomous_mode: bool = False
    max_idle_autoreactivations: int = 2
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_request_timeout_seconds: int = 120
    skills_dir: Path = Path("./skills")
    input_history_path: Path = Path("./data/input_history.txt")
    model_config_path: Path = Path("./data/model_config.json")
    # v0.3+ — agent registry & lifecycle
    agent_lifecycle_log_path: Path = Path("./logs/agent_lifecycle.jsonl")
    blueprints_dir: Path = Path("./data/agents/blueprints")
    # v0.6+ — observability & dashboard
    metrics_db_path: Path = Path("./data/metrics.db")
    dashboard_port: int = 8080
    # v0.7+ — shared context & knowledge (Phase 5)
    shared_workspace_dir: Path = Path("./data/shared_workspace")
    knowledge_base_path: Path = Path("./data/knowledge.db")
    cross_memory_path: Path = Path("./data/cross_agent_memory.jsonl")
    context_window_max_tokens: int = 8000
    # v0.8+ — security & sandboxing (Phase 7)
    sandbox_dir: Path = Path("./data/sandboxes")
    vault_path: Path = Path("./data/vault.json")
    audit_log_path: Path = Path("./logs/audit.jsonl")
    # v0.9+ — workflow engine (Phase 6)
    workflows_dir: Path = Path("./data/workflows")
    workflow_checkpoint_dir: Path = Path("./data/workflow_checkpoints")
    # v0.10+ — resource & cost governance (Phase 8)
    quota_policy_path: Path = Path("./data/quota_policy.json")
    feedback_path: Path = Path("./data/human_feedback.jsonl")
    # v0.11+ — evaluation & quality (Phase 9)
    benchmarks_dir: Path = Path("./data/benchmarks")
    baselines_dir: Path = Path("./data/eval_baselines")
    # v0.12+ — external integration & API (Phase 10)
    rest_api_port: int = 8090
    rest_api_token: str = ""
    plugins_dir: Path = Path("./plugins")
    # OAuth 2.0 — web interface authentication
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:8080/auth/callback"
    oauth_scopes: str = "openid email profile"
    oauth_provider: str = "google"
    # PostgreSQL — web GUI data store (schemat dbo)
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "amiagi"
    db_schema: str = "dbo"
    db_user: str = ""
    db_password: str = ""
    db_min_pool: int = 2
    db_max_pool: int = 10
    # SQLite fallback (when db_user is empty)
    db_sqlite_path: str = "data/web.db"
    # v1.0 — team composition (Phase 11)
    teams_dir: Path = Path("./data/teams")

    @staticmethod
    def from_env() -> "Settings":
        # Load .env file (if present) so that AMIAGI_* / OAUTH_* variables
        # are available via os.getenv without manual ``export``.
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

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
            router_mailbox_log_path=Path(
                os.getenv("AMIAGI_ROUTER_MAILBOX_LOG_PATH", "./logs/router_mailbox.jsonl")
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
                    os.getenv("AMIAGI_SUPERVISOR_MIN_FREE_VRAM_MB", "0"),
                    default=0,
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
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            openai_request_timeout_seconds=max(
                5,
                _as_int(
                    os.getenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "120"),
                    default=120,
                ),
            ),
            skills_dir=Path(os.getenv("AMIAGI_SKILLS_DIR", "./skills")),
            input_history_path=Path(
                os.getenv("AMIAGI_INPUT_HISTORY_PATH", "./data/input_history.txt")
            ),
            model_config_path=Path(
                os.getenv("AMIAGI_MODEL_CONFIG_PATH", "./data/model_config.json")
            ),
            agent_lifecycle_log_path=Path(
                os.getenv("AMIAGI_AGENT_LIFECYCLE_LOG_PATH", "./logs/agent_lifecycle.jsonl")
            ),
            blueprints_dir=Path(
                os.getenv("AMIAGI_BLUEPRINTS_DIR", "./data/agents/blueprints")
            ),
            metrics_db_path=Path(
                os.getenv("AMIAGI_METRICS_DB_PATH", "./data/metrics.db")
            ),
            dashboard_port=_as_int(
                os.getenv("AMIAGI_DASHBOARD_PORT", "8080"),
                default=8080,
            ),
            # Phase 5
            shared_workspace_dir=Path(
                os.getenv("AMIAGI_SHARED_WORKSPACE_DIR", "./data/shared_workspace")
            ),
            knowledge_base_path=Path(
                os.getenv("AMIAGI_KNOWLEDGE_BASE_PATH", "./data/knowledge.db")
            ),
            cross_memory_path=Path(
                os.getenv("AMIAGI_CROSS_MEMORY_PATH", "./data/cross_agent_memory.jsonl")
            ),
            context_window_max_tokens=_as_int(
                os.getenv("AMIAGI_CONTEXT_WINDOW_MAX_TOKENS", "8000"),
                default=8000,
            ),
            # Phase 7
            sandbox_dir=Path(
                os.getenv("AMIAGI_SANDBOX_DIR", "./data/sandboxes")
            ),
            vault_path=Path(
                os.getenv("AMIAGI_VAULT_PATH", "./data/vault.json")
            ),
            audit_log_path=Path(
                os.getenv("AMIAGI_AUDIT_LOG_PATH", "./logs/audit.jsonl")
            ),
            # Phase 6
            workflows_dir=Path(
                os.getenv("AMIAGI_WORKFLOWS_DIR", "./data/workflows")
            ),
            workflow_checkpoint_dir=Path(
                os.getenv("AMIAGI_WORKFLOW_CHECKPOINT_DIR", "./data/workflow_checkpoints")
            ),
            # Phase 8
            quota_policy_path=Path(
                os.getenv("AMIAGI_QUOTA_POLICY_PATH", "./data/quota_policy.json")
            ),
            feedback_path=Path(
                os.getenv("AMIAGI_FEEDBACK_PATH", "./data/human_feedback.jsonl")
            ),
            # Phase 9
            benchmarks_dir=Path(
                os.getenv("AMIAGI_BENCHMARKS_DIR", "./data/benchmarks")
            ),
            baselines_dir=Path(
                os.getenv("AMIAGI_BASELINES_DIR", "./data/eval_baselines")
            ),
            # Phase 10
            rest_api_port=_as_int(
                os.getenv("AMIAGI_REST_API_PORT", "8090"),
                default=8090,
            ),
            rest_api_token=os.getenv("AMIAGI_REST_API_TOKEN", ""),
            plugins_dir=Path(
                os.getenv("AMIAGI_PLUGINS_DIR", "./plugins")
            ),
            # OAuth 2.0
            oauth_client_id=os.getenv("AMIAGI_OAUTH_CLIENT_ID", ""),
            oauth_client_secret=os.getenv("AMIAGI_OAUTH_CLIENT_SECRET", ""),
            oauth_redirect_uri=os.getenv(
                "AMIAGI_OAUTH_REDIRECT_URI",
                "http://localhost:8080/auth/callback",
            ),
            oauth_scopes=os.getenv("AMIAGI_OAUTH_SCOPES", "openid email profile"),
            oauth_provider=os.getenv("AMIAGI_OAUTH_PROVIDER", "google"),
            # PostgreSQL
            db_host=os.getenv("AMIAGI_DB_HOST", "localhost"),
            db_port=_as_int(
                os.getenv("AMIAGI_DB_PORT", "5432"),
                default=5432,
            ),
            db_name=os.getenv("AMIAGI_DB_NAME", "amiagi"),
            db_schema=os.getenv("AMIAGI_DB_SCHEMA", "dbo"),
            db_user=os.getenv("AMIAGI_DB_USER", ""),
            db_password=os.getenv("AMIAGI_DB_PASSWORD", ""),
            db_min_pool=_as_int(
                os.getenv("AMIAGI_DB_MIN_POOL", "2"),
                default=2,
            ),
            db_max_pool=_as_int(
                os.getenv("AMIAGI_DB_MAX_POOL", "10"),
                default=10,
            ),
            db_sqlite_path=os.getenv("AMIAGI_DB_SQLITE_PATH", "data/web.db"),
            # Phase 11
            teams_dir=Path(
                os.getenv("AMIAGI_TEAMS_DIR", "./data/teams")
            ),
        )
