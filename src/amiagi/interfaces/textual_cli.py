from __future__ import annotations

import collections
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import webbrowser
from datetime import datetime, timezone
from urllib.parse import quote_plus, unquote, urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, cast

from amiagi.application.chat_service import ChatService
from amiagi.application.communication_protocol import (
    format_conversation_excerpt,
    is_sponsor_readable,
    load_communication_rules,
    panels_for_target,
    parse_addressed_blocks,
    strip_tool_call_blocks,
)
from amiagi.application.discussion_sync import extract_dialogue_without_code
from amiagi.application.tool_calling import ToolCall, parse_tool_calls
from amiagi.application.tool_registry import list_registered_tools, resolve_registered_tool_script
from amiagi.application.shell_policy import default_shell_policy, load_shell_policy, parse_and_validate_shell_command
from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.agent_factory import AgentFactory
from amiagi.application.agent_wizard import AgentWizardService
from amiagi.application.task_queue import TaskQueue
from amiagi.application.work_assigner import WorkAssigner
from amiagi.application.alert_manager import AlertManager
from amiagi.application.audit_chain import AuditChain
from amiagi.application.context_window_manager import ContextWindowManager
from amiagi.application.cross_agent_memory import CrossAgentMemory
from amiagi.application.permission_enforcer import PermissionEnforcer
from amiagi.application.workflow_engine import WorkflowEngine
from amiagi.config import Settings
from amiagi.domain.agent import AgentRole, AgentState
from amiagi.domain.task import Task, TaskPriority, TaskStatus
from amiagi.infrastructure.ollama_client import OllamaClientError
from amiagi.infrastructure.openai_client import (
    OpenAIClient,
    OpenAIClientError,
    SUPPORTED_OPENAI_MODELS,
    mask_api_key,
)
from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.dashboard_server import DashboardServer
from amiagi.infrastructure.input_history import InputHistory
from amiagi.infrastructure.metrics_collector import MetricsCollector
from amiagi.infrastructure.script_executor import ScriptExecutor
from amiagi.infrastructure.session_model_config import SessionModelConfig
from amiagi.infrastructure.session_replay import SessionReplay
from amiagi.infrastructure.shared_workspace import SharedWorkspace
from amiagi.infrastructure.knowledge_base import KnowledgeBase
from amiagi.infrastructure.sandbox_manager import SandboxManager
from amiagi.infrastructure.secret_vault import SecretVault
from amiagi.infrastructure.workflow_checkpoint import WorkflowCheckpoint
from amiagi.infrastructure.usage_tracker import UsageTracker
# Phase 8
from amiagi.application.budget_manager import BudgetManager
from amiagi.domain.quota_policy import QuotaPolicy
from amiagi.infrastructure.rate_limiter import RateLimiter
from amiagi.infrastructure.vram_scheduler import VRAMScheduler
# Phase 9
from amiagi.application.eval_runner import EvalRunner
from amiagi.application.ab_test_runner import ABTestRunner
from amiagi.application.regression_detector import RegressionDetector
from amiagi.infrastructure.benchmark_suite import BenchmarkSuite
from amiagi.interfaces.human_feedback import HumanFeedbackCollector
# Phase 10
from amiagi.application.plugin_loader import PluginLoader
from amiagi.infrastructure.ci_adapter import CIAdapter
from amiagi.infrastructure.rest_server import RESTServer
from amiagi.infrastructure.webhook_dispatcher import WebhookDispatcher
# Phase 11
from amiagi.application.dynamic_scaler import DynamicScaler
from amiagi.application.skill_catalog import SkillCatalog
from amiagi.application.team_composer import TeamComposer
from amiagi.interfaces.team_dashboard import TeamDashboard
from amiagi.interfaces.cli import (
    _AMIAGI_LOGO,
    _build_landing_banner,
    _fetch_ollama_models,
    _format_user_facing_answer,
    _has_supported_tool_call,
    _is_path_within_work_dir,
    _network_resource_for_model,
    _parse_search_results_from_html,
    _select_executor_model_by_index,
    _set_executor_model,
    _resolve_tool_path,
    _read_plan_tracking_snapshot,
    _repair_plan_tracking_file,
)
from amiagi.interfaces.permission_manager import PermissionManager
from amiagi.i18n import _

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.events import Key
    from textual.widgets import Input, Static, TextArea
except (ImportError, ModuleNotFoundError) as error:  # pragma: no cover - runtime import guard
    raise RuntimeError(
        _("error.textual_import")
    ) from error


SUPERVISION_POLL_INTERVAL_SECONDS = 0.75
SUPERVISOR_WATCHDOG_INTERVAL_SECONDS = 5.0
SUPERVISOR_IDLE_THRESHOLD_SECONDS = 45.0
SUPERVISOR_WATCHDOG_MAX_ATTEMPTS = 5
SUPERVISOR_WATCHDOG_CAP_COOLDOWN_SECONDS = 60.0
INTERRUPT_AUTORESUME_IDLE_SECONDS = 180.0

_SUPPORTED_TEXTUAL_TOOLS = {
    "read_file",
    "list_dir",
    "run_shell",
    "run_python",
    "check_python_syntax",
    "fetch_web",
    "search_web",
    "download_file",
    "convert_pdf_to_markdown",
    "capture_camera_frame",
    "record_microphone_clip",
    "check_capabilities",
    "write_file",
    "append_file",
}

_CONVERSATIONAL_INTERRUPT_MARKERS = {
    "kim jesteś",
    "kim jestes",
    "kto jesteś",
    "kto jestes",
    "co potrafisz",
    "jak działasz",
    "jak dzialasz",
    "jak działa framework",
    "jak dziala framework",
}

_IDENTITY_QUERY_MARKERS = {
    "kim jesteś",
    "kim jestes",
    "kto jesteś",
    "kto jestes",
}


def _canonical_tool_name(name: str) -> str:
    cleaned = name.strip()
    _TOOL_ALIASES: dict[str, str] = {
        "run_command": "run_shell",
        "file_read": "read_file",
        "read": "read_file",
        "dir_list": "list_dir",
        "download": "download_file",
        "pdf_to_md": "convert_pdf_to_markdown",
        "convert_pdf": "convert_pdf_to_markdown",
        "pdf_to_markdown": "convert_pdf_to_markdown",
    }
    return _TOOL_ALIASES.get(cleaned, cleaned)


def _render_single_tool_call_block(tool_call: ToolCall) -> str:
    canonical_tool = _canonical_tool_name(tool_call.tool)
    payload = {
        "tool": canonical_tool,
        "args": tool_call.args,
        "intent": tool_call.intent,
    }
    return "```tool_call\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def _adaptive_watchdog_idle_threshold_seconds(
    *,
    activity_log_path: Path,
    default_seconds: float,
    min_samples: int = 50,
) -> float:
    try:
        with activity_log_path.open("r", encoding="utf-8") as file:
            timestamps: list[float] = []
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                raw_timestamp = str(payload.get("timestamp", "")).strip()
                if not raw_timestamp:
                    continue
                if raw_timestamp.endswith("Z"):
                    raw_timestamp = raw_timestamp[:-1] + "+00:00"
                try:
                    parsed = datetime.fromisoformat(raw_timestamp)
                except Exception:
                    continue
                timestamps.append(parsed.timestamp())
    except OSError:
        return default_seconds

    if len(timestamps) < min_samples:
        return default_seconds

    timestamps.sort()
    deltas = [next_ts - prev_ts for prev_ts, next_ts in zip(timestamps, timestamps[1:]) if next_ts >= prev_ts]
    if len(deltas) < max(10, min_samples // 2):
        return default_seconds

    deltas.sort()
    p99_index = int((len(deltas) - 1) * 0.99)
    p99 = deltas[p99_index]
    adaptive = max(default_seconds, p99 * 1.5)
    return float(max(default_seconds, min(180.0, round(adaptive, 1))))

def _build_help_commands() -> list[tuple[str, str]]:
    return [
        ("/help", _("help.cmd.help")),
        ("/cls", _("help.cmd.cls")),
        ("/cls all", _("help.cmd.cls_all")),
        # --- Model management ---
        ("/models current", _("help.cmd.models_current")),
        ("/models show", _("help.cmd.models_show")),
        ("/models chose <nr>", _("help.cmd.models_chose")),
        ("/kastor-model show", _("help.cmd.kastor_model_show")),
        ("/kastor-model chose <nr>", _("help.cmd.kastor_model_chose")),
        # --- Permissions ---
        ("/permissions", _("help.cmd.permissions")),
        ("/permissions all", _("help.cmd.permissions_all")),
        ("/permissions ask", _("help.cmd.permissions_ask")),
        ("/permissions reset", _("help.cmd.permissions_reset")),
        # --- System info ---
        ("/queue-status", _("help.cmd.queue_status")),
        ("/capabilities [--network]", _("help.cmd.capabilities")),
        ("/show-system-context [tekst]", _("help.cmd.show_system_context")),
        ("/goal-status", _("help.cmd.goal_status")),
        ("/goal", _("help.cmd.goal")),
        ("/router-status", _("help.cmd.router_status")),
        ("/idle-until <ISO8601|off>", _("help.cmd.idle_until")),
        # --- Memory & history ---
        ("/history [n]", _("help.cmd.history")),
        ("/remember <tekst>", _("help.cmd.remember")),
        ("/memories [zapytanie]", _("help.cmd.memories")),
        ("/import-dialog [plik]", _("help.cmd.import_dialog")),
        # --- Code & shell ---
        ("/create-python <plik> <opis>", _("help.cmd.create_python")),
        ("/run-python <plik> [arg ...]", _("help.cmd.run_python")),
        ("/run-shell <polecenie>", _("help.cmd.run_shell")),
        # --- API usage ---
        ("/api-usage", _("help.cmd.api_usage")),
        ("/api-key verify", _("help.cmd.api_key_verify")),
        # --- Skills ---
        ("/skills", _("help.cmd.skills")),
        ("/skills reload", _("help.cmd.skills_reload")),
        # --- Phase 1: Agent Registry ---
        ("/agents list", _("help.cmd.agents_list")),
        ("/agents info <id|name>", _("help.cmd.agents_info")),
        ("/agents pause <id>", _("help.cmd.agents_pause")),
        ("/agents resume <id>", _("help.cmd.agents_resume")),
        ("/agents terminate <id>", _("help.cmd.agents_terminate")),
        # --- Phase 2: Agent Wizard ---
        ("/agent-wizard create <opis>", _("help.cmd.agent_wizard_create")),
        ("/agent-wizard blueprints", _("help.cmd.agent_wizard_blueprints")),
        ("/agent-wizard load <nazwa>", _("help.cmd.agent_wizard_load")),
        # --- Phase 3: Task Queue ---
        ("/tasks list", _("help.cmd.tasks_list")),
        ("/tasks add <opis>", _("help.cmd.tasks_add")),
        ("/tasks info <id>", _("help.cmd.tasks_info")),
        ("/tasks cancel <id>", _("help.cmd.tasks_cancel")),
        ("/tasks stats", _("help.cmd.tasks_stats")),
        # --- Phase 4: Dashboard ---
        ("/dashboard start [--port N]", _("help.cmd.dashboard_start")),
        ("/dashboard stop", _("help.cmd.dashboard_stop")),
        ("/dashboard status", _("help.cmd.dashboard_status")),
        # --- Phase 5: Knowledge & Workspace ---
        ("/knowledge store <tekst>", _("help.cmd.knowledge_store")),
        ("/knowledge query <pytanie>", _("help.cmd.knowledge_query")),
        ("/knowledge count", _("help.cmd.knowledge_count")),
        ("/workspace list", _("help.cmd.workspace_list")),
        ("/workspace read <plik>", _("help.cmd.workspace_read")),
        ("/workspace write <plik> <treść>", _("help.cmd.workspace_write")),
        # --- Phase 6: Workflow Engine ---
        ("/workflow list", _("help.cmd.workflow_list")),
        ("/workflow run <nazwa>", _("help.cmd.workflow_run")),
        ("/workflow status", _("help.cmd.workflow_status")),
        ("/workflow pause", _("help.cmd.workflow_pause")),
        ("/workflow resume", _("help.cmd.workflow_resume")),
        # --- Phase 7: Security ---
        ("/audit query [agent]", _("help.cmd.audit_query")),
        ("/audit last [n]", _("help.cmd.audit_last")),
        ("/sandbox list", _("help.cmd.sandbox_list")),
        ("/sandbox create <agent>", _("help.cmd.sandbox_create")),
        ("/sandbox destroy", _("help.cmd.sandbox_destroy")),
        # --- Phase 8: Budget & Quota ---
        ("/budget status", _("help.cmd.budget_status")),
        ("/budget set <agent> <limit>", _("help.cmd.budget_set")),
        ("/budget reset <agent>", _("help.cmd.budget_reset")),
        ("/quota status", _("help.cmd.quota_status")),
        ("/quota set <rola> <tokens> <cost> <req/h>", _("help.cmd.quota_set")),
        # --- Phase 9: Evaluation & Feedback ---
        ("/eval run <agent> [--benchmark X]", _("help.cmd.eval_run")),
        ("/eval compare <agent_a> <agent_b>", _("help.cmd.eval_compare")),
        ("/eval history [agent]", _("help.cmd.eval_history")),
        ("/eval baselines", _("help.cmd.eval_baselines")),
        ("/feedback summary", _("help.cmd.feedback_summary")),
        ("/feedback up <agent> [komentarz]", _("help.cmd.feedback_up")),
        ("/feedback down <agent> [komentarz]", _("help.cmd.feedback_down")),
        # --- Phase 10: API & Plugins ---
        ("/api start", _("help.cmd.api_start")),
        ("/api stop", _("help.cmd.api_stop")),
        ("/api status", _("help.cmd.api_status")),
        ("/plugins list", _("help.cmd.plugins_list")),
        ("/plugins load", _("help.cmd.plugins_load")),
        ("/plugins install <path>", _("help.cmd.plugins_install")),
        # --- Phase 11: Teams ---
        ("/team list", _("help.cmd.team_list")),
        ("/team templates", _("help.cmd.team_templates")),
        ("/team create <szablon>", _("help.cmd.team_create")),
        ("/team compose <cel>", _("help.cmd.team_compose")),
        ("/team status <id>", _("help.cmd.team_status")),
        ("/team scale <id> up|down", _("help.cmd.team_scale")),
        # --- i18n ---
        ("/lang <code>", _("help.cmd.lang")),
        # --- Session ---
        ("/bye", _("help.cmd.bye")),
        ("/quit", _("help.cmd.quit")),
        ("/exit", _("help.cmd.exit")),
    ]


_TEXTUAL_HELP_COMMANDS: list[tuple[str, str]] = _build_help_commands()


def _build_textual_help_text() -> str:
    command_width = max(len(command) for command, _desc in _TEXTUAL_HELP_COMMANDS)
    lines = [_("help.header.textual")]
    for command, description in _TEXTUAL_HELP_COMMANDS:
        lines.append(f"  {command.ljust(command_width)}  - {description}")
    return "\n".join(lines)


TEXTUAL_HELP_TEXT = _build_textual_help_text()


@dataclass
class _CommandOutcome:
    handled: bool
    messages: list[str]
    should_exit: bool = False


class PermissionLike(Protocol):
    allow_all: bool
    granted_once: set[str]


def _copy_to_system_clipboard(text: str) -> tuple[bool, str]:
    if not text:
        return False, _("clipboard.empty")

    timeout_seconds = 0.35

    if os.environ.get("WAYLAND_DISPLAY"):
        wl_copy = shutil.which("wl-copy")
        if wl_copy is None:
            return False, _("clipboard.no_wlcopy")
        try:
            completed = subprocess.run(
                [wl_copy],
                input=text,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return False, _("clipboard.timeout_wlcopy")
        if completed.returncode == 0:
            return True, _("clipboard.ok_wayland")
        return False, _("clipboard.fail_wlcopy")

    if os.environ.get("DISPLAY"):
        xclip = shutil.which("xclip")
        if xclip is not None:
            try:
                completed = subprocess.run(
                    [xclip, "-selection", "clipboard", "-in"],
                    input=text,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                return False, _("clipboard.timeout_xclip")
            if completed.returncode == 0:
                return True, _("clipboard.ok_xclip")

        xsel = shutil.which("xsel")
        if xsel is not None:
            try:
                completed = subprocess.run(
                    [xsel, "--clipboard", "--input"],
                    input=text,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                return False, _("clipboard.timeout_xsel")
            if completed.returncode == 0:
                return True, _("clipboard.ok_xsel")

        return False, _("clipboard.no_x11_tool")

    return False, _("clipboard.no_display")


def _handle_textual_command(raw: str, permission_manager: PermissionLike) -> _CommandOutcome:
    global TEXTUAL_HELP_TEXT, _TEXTUAL_HELP_COMMANDS
    command = raw.strip().lower()
    if command in {"/quit", "/exit"}:
        return _CommandOutcome(handled=True, messages=[], should_exit=True)

    if command == "/help":
        _TEXTUAL_HELP_COMMANDS = _build_help_commands()
        TEXTUAL_HELP_TEXT = _build_textual_help_text()
        return _CommandOutcome(handled=True, messages=[TEXTUAL_HELP_TEXT])

    if command.startswith("/permissions"):
        parts = command.split()
        action = parts[1] if len(parts) > 1 else "status"

        if action in {"status", "show"}:
            granted_once_count = len(getattr(permission_manager, "granted_once", set()))
            return _CommandOutcome(
                handled=True,
                messages=[
                    _("permissions.header"),
                    f"allow_all: {bool(getattr(permission_manager, 'allow_all', False))}",
                    f"granted_once_count: {granted_once_count}",
                ],
            )

        if action in {"all", "on", "global"}:
            permission_manager.allow_all = True
            return _CommandOutcome(
                handled=True,
                messages=[_("permissions.global_on")],
            )

        if action in {"ask", "off", "interactive"}:
            permission_manager.allow_all = False
            return _CommandOutcome(
                handled=True,
                messages=[
                    _("permissions.ask_on"),
                    _("permissions.ask_textual_hint"),
                ],
            )

        if action in {"reset", "clear"}:
            granted_once = getattr(permission_manager, "granted_once", None)
            if isinstance(granted_once, set):
                granted_once.clear()
                return _CommandOutcome(
                    handled=True,
                    messages=[_("permissions.reset_done")],
                )
            return _CommandOutcome(
                handled=True,
                messages=[_("permissions.reset_empty")],
            )

        return _CommandOutcome(
            handled=True,
            messages=[_("permissions.usage")],
        )

    if command.startswith("/lang"):
        from amiagi.i18n import set_language, get_language, available_languages

        parts = command.split()
        if len(parts) < 2:
            return _CommandOutcome(
                handled=True,
                messages=[
                    _("lang.current", lang=get_language()),
                    _("lang.usage"),
                ],
            )
        code = parts[1].strip().lower()
        if code not in available_languages():
            available = ", ".join(sorted(available_languages()))
            return _CommandOutcome(
                handled=True,
                messages=[_("lang.not_found", lang=code, available=available)],
            )
        set_language(code)
        # Rebuild global help text after language switch
        _TEXTUAL_HELP_COMMANDS = _build_help_commands()
        TEXTUAL_HELP_TEXT = _build_textual_help_text()
        return _CommandOutcome(
            handled=True,
            messages=[_("lang.switched", lang=code)],
        )

    return _CommandOutcome(handled=False, messages=[])


def _is_model_access_allowed(permission_manager: PermissionLike, model_base_url: str) -> tuple[bool, str]:
    network_resource = _network_resource_for_model(model_base_url)
    if permission_manager.allow_all:
        return True, network_resource

    if network_resource in getattr(permission_manager, "granted_once", set()):
        return True, network_resource

    return False, network_resource


class _AmiagiTextualApp(App[None]):
    BINDINGS = [
        ("ctrl+c", "copy_selection", _("binding.copy_selection")),
        ("ctrl+shift+c", "copy_selection", _("binding.copy_selection")),
        ("ctrl+q", "quit", _("binding.quit")),
    ]

    CSS = """
    Screen { layout: horizontal; }
    #main_column { width: 60%; height: 100%; layout: vertical; }
    #tech_column { width: 40%; height: 100%; layout: vertical; }
    #user_model_log { height: 1fr; border: round #4ea1ff; }
    #busy_indicator { height: 3; border: round #9a6bff; padding: 0 1; }
    #input_box { dock: bottom; border: tall $success-lighten-2; }
    #input_box:focus { border: tall #4ea1ff; }
    #router_status { height: 8; border: round #9a6bff; }
    #api_usage_bar { height: 2; border: round #ff9500; padding: 0 1; display: none; }
    #supervisor_log { height: 1fr; border: round #47c26b; }
    #executor_log { height: 1fr; border: round #f5a623; }
    .title { padding: 0 1; }
    """

    def __init__(
        self,
        *,
        chat_service: ChatService,
        supervisor_dialogue_log_path: Path,
        permission_manager: PermissionManager,
        shell_policy_path: Path,
        router_mailbox_log_path: Path | None = None,
        activity_logger: ActivityLogger | None = None,
        settings: Settings | None = None,
        agent_registry: AgentRegistry | None = None,
        agent_factory: AgentFactory | None = None,
        task_queue: TaskQueue | None = None,
        work_assigner: WorkAssigner | None = None,
        metrics_collector: MetricsCollector | None = None,
        alert_manager: AlertManager | None = None,
        session_replay: SessionReplay | None = None,
        # Phase 5
        shared_workspace: SharedWorkspace | None = None,
        knowledge_base: KnowledgeBase | None = None,
        cross_memory: CrossAgentMemory | None = None,
        context_window_manager: ContextWindowManager | None = None,
        # Phase 7
        permission_enforcer: PermissionEnforcer | None = None,
        sandbox_manager: SandboxManager | None = None,
        secret_vault: SecretVault | None = None,
        audit_chain: AuditChain | None = None,
        # Phase 6
        workflow_engine: WorkflowEngine | None = None,
        workflow_checkpoint: WorkflowCheckpoint | None = None,
        # Phase 8
        budget_manager: BudgetManager | None = None,
        quota_policy: QuotaPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        vram_scheduler: VRAMScheduler | None = None,
        # Phase 9
        eval_runner: EvalRunner | None = None,
        benchmark_suite: BenchmarkSuite | None = None,
        ab_test_runner: ABTestRunner | None = None,
        regression_detector: RegressionDetector | None = None,
        human_feedback: HumanFeedbackCollector | None = None,
        # Phase 10
        rest_server: RESTServer | None = None,
        webhook_dispatcher: WebhookDispatcher | None = None,
        plugin_loader: PluginLoader | None = None,
        ci_adapter: CIAdapter | None = None,
        # Phase 11
        team_composer: TeamComposer | None = None,
        skill_catalog: SkillCatalog | None = None,
        dynamic_scaler: DynamicScaler | None = None,
        team_dashboard: TeamDashboard | None = None,
    ) -> None:
        super().__init__()
        self._chat_service = chat_service
        self._supervisor_dialogue_log_path = supervisor_dialogue_log_path
        self._permission_manager = permission_manager
        self._shell_policy_path = shell_policy_path
        self._dialogue_log_offset = 0
        self._router_mailbox_log_path = router_mailbox_log_path or Path("./logs/router_mailbox.jsonl")
        self._activity_logger = activity_logger
        self._settings = settings
        # ---- Phase 1-4 services ----
        self._agent_registry = agent_registry
        self._agent_factory = agent_factory
        self._task_queue = task_queue
        self._work_assigner = work_assigner
        self._metrics_collector = metrics_collector
        self._alert_manager = alert_manager
        self._session_replay = session_replay
        self._dashboard_server: DashboardServer | None = None
        # ---- Phase 5 services ----
        self._shared_workspace = shared_workspace
        self._knowledge_base = knowledge_base
        self._cross_memory = cross_memory
        self._context_window_manager = context_window_manager
        # ---- Phase 7 services ----
        self._permission_enforcer = permission_enforcer
        self._sandbox_manager = sandbox_manager
        self._secret_vault = secret_vault
        self._audit_chain = audit_chain
        # ---- Phase 6 services ----
        self._workflow_engine = workflow_engine
        self._workflow_checkpoint = workflow_checkpoint
        # ---- Phase 8 services ----
        self._budget_manager = budget_manager
        self._quota_policy = quota_policy
        self._rate_limiter = rate_limiter
        self._vram_scheduler = vram_scheduler
        # ---- Phase 9 services ----
        self._eval_runner = eval_runner
        self._benchmark_suite = benchmark_suite
        self._ab_test_runner = ab_test_runner
        self._regression_detector = regression_detector
        self._human_feedback = human_feedback
        # ---- Phase 10 services ----
        self._rest_server = rest_server
        self._webhook_dispatcher = webhook_dispatcher
        self._plugin_loader = plugin_loader
        self._ci_adapter = ci_adapter
        # ---- Phase 11 services ----
        self._team_composer = team_composer
        self._skill_catalog = skill_catalog
        self._dynamic_scaler = dynamic_scaler
        self._team_dashboard = team_dashboard
        self._wizard_service: AgentWizardService | None = None
        self._usage_tracker = UsageTracker()
        self._model_configured = False
        self._wizard_phase = 0  # 0=inactive, 1=polluks, 2=kastor, 3=api_key_check
        self._wizard_models: list[tuple[str, str]] = []  # (name, source) e.g. ("qwen3:14b","ollama")
        self._wizard_kastor_models: list[tuple[str, str]] = []
        self._wizard_polluks_choice: tuple[str, str] = ("", "")  # (name, source)
        # --- Input history (up/down arrows like terminal readline) ---
        history_path = Path("./data/input_history.txt")
        if settings is not None:
            history_path = getattr(settings, "input_history_path", history_path)
        self._input_history = InputHistory(history_path)
        # --- Session model config (persisted between restarts) ---
        self._model_config_path = Path("./data/model_config.json")
        if settings is not None:
            self._model_config_path = getattr(settings, "model_config_path", self._model_config_path)
        self._script_executor = ScriptExecutor()
        self._work_dir = chat_service.work_dir.resolve()
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._supervisor_notice_shown = False
        self._passive_turns = 0
        self._last_user_message = ""
        self._last_model_answer = ""
        self._last_progress_monotonic = time.monotonic()
        self._watchdog_attempts = 0
        self._watchdog_capped_notified = False
        self._last_watchdog_cap_autonudge_monotonic = 0.0
        self._watchdog_suspended_until_user_input = False
        self._background_user_turn_enabled = os.environ.get("AMIAGI_TEXTUAL_BACKGROUND_USER_TURN", "1") != "0"
        self._main_thread_id = threading.get_ident()
        self._watchdog_idle_threshold_seconds = _adaptive_watchdog_idle_threshold_seconds(
            activity_log_path=Path("./logs/activity.jsonl"),
            default_seconds=SUPERVISOR_IDLE_THRESHOLD_SECONDS,
        )
        self._router_cycle_in_progress = False
        self._supervisor_outbox: list[dict[str, str]] = []
        self._actor_states: dict[str, str] = {
            "router": "INIT",
            "creator": "WAITING_INPUT",
            "supervisor": "READY" if chat_service.supervisor_service is not None else "DISABLED",
            "terminal": "WAITING_INPUT",
        }
        self._idle_until_epoch: float | None = None
        self._idle_until_source: str = ""
        self._last_router_event: str = _("router.session_start_event")
        self._plan_pause_active = False
        self._plan_pause_started_monotonic = 0.0
        self._plan_pause_reason = ""
        self._pending_user_decision = False
        self._pending_decision_identity_query = False
        self._unaddressed_turns = 0
        self._reminder_count = 0
        self._consultation_rounds_this_cycle = 0
        self._comm_rules = load_communication_rules()
        self._user_message_queue: collections.deque[str] = collections.deque(maxlen=20)
        try:
            self._shell_policy = load_shell_policy(shell_policy_path)
        except Exception:
            self._shell_policy = default_shell_policy()

    def _log_activity(self, *, action: str, intent: str, details: dict | None = None) -> None:
        if self._activity_logger is None:
            return
        try:
            self._activity_logger.log(action=action, intent=intent, details=details)
        except Exception:
            return

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="main_column"):
                yield Static(_("widget.user_model_title"), classes="title")
                yield TextArea("", id="user_model_log", read_only=True, show_line_numbers=False)
                yield Static(_("widget.busy_ready"), id="busy_indicator")
                yield Input(placeholder=_("widget.input_placeholder"), id="input_box")
            with Vertical(id="tech_column"):
                yield Static(_("widget.router_title"), classes="title")
                yield Static("", id="router_status")
                yield Static("", id="api_usage_bar")
                yield Static(_("widget.supervisor_title"), classes="title")
                yield TextArea("", id="supervisor_log", read_only=True, show_line_numbers=False)
                yield Static(_("widget.executor_title"), classes="title")
                yield TextArea("", id="executor_log", read_only=True, show_line_numbers=False)

    def on_key(self, event: Key) -> None:
        """Handle up/down arrows for input history (readline-like)."""
        focused = self.focused
        if not isinstance(focused, Input) or focused.id != "input_box":
            return
        if event.key == "up":
            entry = self._input_history.older(focused.value)
            if entry is not None:
                focused.value = entry
                focused.cursor_position = len(entry)
            event.prevent_default()
        elif event.key == "down":
            entry = self._input_history.newer()
            if entry is not None:
                focused.value = entry
                focused.cursor_position = len(entry)
            else:
                focused.value = ""
            event.prevent_default()

    def _format_idle_until(self) -> str:
        if self._idle_until_epoch is None:
            return _("router.idle_until_none")
        dt = datetime.fromtimestamp(self._idle_until_epoch, tz=timezone.utc)
        rendered = dt.isoformat().replace("+00:00", "Z")
        if self._idle_until_source:
            return f"{rendered} ({self._idle_until_source})"
        return rendered

    def _render_router_status(self) -> None:
        try:
            area = self.query_one("#router_status", Static)
        except Exception:
            return
        idle_for_seconds = max(0.0, time.monotonic() - self._last_progress_monotonic)
        lines = [
            _("router.actors_header"),
            f"- Router: {self._actor_states.get('router', 'UNKNOWN')}",
            f"- Polluks: {self._actor_states.get('creator', 'UNKNOWN')}",
            f"- Kastor: {self._actor_states.get('supervisor', 'UNKNOWN')}",
            f"- Terminal: {self._actor_states.get('terminal', 'UNKNOWN')}",
            f"Plan pause: {'ON' if self._plan_pause_active else 'OFF'}",
            f"Decision pending: {'YES' if self._pending_user_decision else 'NO'}",
            f"IDLE for: {idle_for_seconds:.1f}s",
            f"IDLE until: {self._format_idle_until()}",
            f"Ostatnie zdarzenie: {self._last_router_event}",
        ]
        area.update("\n".join(lines))
        self._render_busy_indicator()

    def _render_busy_indicator(self) -> None:
        try:
            indicator = self.query_one("#busy_indicator", Static)
        except Exception:
            return
        if self._router_cycle_in_progress:
            indicator.update(_("widget.busy_working"))
            return
        indicator.update(_("widget.busy_ready"))

    def _set_actor_state(self, actor: str, state: str, event: str | None = None) -> None:
        def _apply() -> None:
            self._actor_states[actor] = state
            if event:
                self._last_router_event = event
            self._render_router_status()

        self._run_on_ui_thread(_apply)

    def _run_on_ui_thread(self, callback) -> None:
        if threading.get_ident() == self._main_thread_id:
            callback()
            return
        try:
            self.call_from_thread(callback)
        except Exception:
            return

    def _set_idle_until(self, idle_until_epoch: float | None, source: str) -> None:
        self._idle_until_epoch = idle_until_epoch
        self._idle_until_source = source if idle_until_epoch is not None else ""
        self._set_actor_state("router", "IDLE_WINDOW_SET" if idle_until_epoch is not None else "ACTIVE", "Aktualizacja okna IDLE")

    def _is_conversational_interrupt(self, text: str) -> bool:
        normalized = " ".join(text.strip().lower().split())
        if not normalized:
            return False
        if normalized in {"kontynuuj", "przerwij", "stop", "wznów", "wznow", "wznow plan"}:
            return False
        return any(marker in normalized for marker in _CONVERSATIONAL_INTERRUPT_MARKERS)

    def _extract_pause_decision(self, text: str) -> str | None:
        normalized = " ".join(text.strip().lower().split())
        if normalized in {"kontynuuj", "wznów", "wznow", "wznow plan", "kontynuuj plan"}:
            return "continue"
        if normalized in {"przerwij", "stop", "przerwij plan", "zatrzymaj"}:
            return "stop"
        if normalized.startswith("nowe zadanie"):
            return "new_task"
        return None

    def _is_identity_query(self, text: str) -> bool:
        normalized = " ".join(text.strip().lower().split())
        if not normalized:
            return False
        return any(marker in normalized for marker in _IDENTITY_QUERY_MARKERS)

    def _model_response_awaits_user(self, answer: str) -> bool:
        """Detect if model's response asks the user a question or awaits a decision.

        Returns True when the plan should pause because the model is waiting
        for user input (even though it was the model that sent the message, not
        the user triggering an interrupt).
        """
        if not answer or not answer.strip():
            return False
        # If the answer contains an actionable tool call, the model is NOT waiting
        if _has_supported_tool_call(answer):
            return False
        stripped = answer.rstrip()
        # Simple heuristic: last non-empty line ends with '?'
        last_line = stripped.rsplit("\n", 1)[-1].strip()
        if last_line.endswith("?"):
            return True
        # Polish-specific question markers directed at the user
        _MODEL_QUESTION_MARKERS = (
            "co chcesz",
            "czego oczekujesz",
            "jakie masz",
            "jakiego",
            "jak chcesz",
            "czy chcesz",
            "czy mam",
            "co mam zrobić",
            "proszę o wskazówki",
            "oczekuję na",
            "czekam na",
            "proszę o decyzję",
            "twoja decyzja",
            "co dalej",
            "jaki jest",
            "jaka jest",
        )
        normalized = " ".join(stripped.lower().split())
        return any(marker in normalized for marker in _MODEL_QUESTION_MARKERS)

    def _identity_reply(self) -> str:
        return _("identity.reply")

    # ------------------------------------------------------------------
    # Premature plan completion detection & Kastor-based redirection
    # ------------------------------------------------------------------

    _COMPLETION_SIGNAL = "zakończyłem zadanie"

    def _is_premature_plan_completion(self, answer: str) -> bool:
        """Return True when Polluks considers the plan done but hasn't sent
        the expected completion signal to the Sponsor.

        The Sponsor's task requires Polluks to send "Zakończyłem zadanie"
        when ALL work is finished.  If the plan's current_stage is
        'completed' (or all tasks are done) and that signal is absent,
        the completion is premature and Kastor should redirect.
        """
        normalized = answer.strip().lower()
        if self._COMPLETION_SIGNAL in normalized:
            return False  # genuine completion

        plan_path = self._work_dir / "notes" / "main_plan.json"
        if not plan_path.exists():
            return False
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False

        current_stage = str(payload.get("current_stage", "")).strip().lower()
        if current_stage == "completed":
            return True

        tasks = payload.get("tasks")
        if isinstance(tasks, list) and tasks:
            all_done = all(
                str(t.get("status", "")).strip().lower() == "zakończona"
                for t in tasks
                if isinstance(t, dict)
            )
            if all_done:
                return True
        return False

    def _redirect_premature_completion(
        self, user_message: str, polluks_answer: str
    ) -> str | None:
        """Ask Kastor to redirect Polluks when plan completion is premature.

        Returns the redirected answer (with tool_call) or None if
        the supervisor is unavailable or fails.
        """
        supervisor = self._chat_service.supervisor_service
        if supervisor is None:
            return None

        supervision_context = {
            "passive_turns": self._passive_turns,
            "should_remind_continuation": True,
            "gpu_busy_over_50": False,
            "plan_persistence": {"required": True},
            "premature_completion": True,
            "interrupt_mode": False,
        }

        prompt = (
            "[RUNTIME_SUPERVISION_CONTEXT]\n"
            + json.dumps(supervision_context, ensure_ascii=False)
            + "\n[/RUNTIME_SUPERVISION_CONTEXT]\n"
            "Polluks oznaczył plan jako zakończony i pyta Sponsora o dalsze kroki, "
            "ale NIE wysłał komunikatu 'Zakończyłem zadanie'. "
            "Zadanie Sponsora NIE zostało w pełni zrealizowane. "
            "Przeanalizuj SPONSOR_TASK i wskaż Polluksowi konkretne następne kroki. "
            "Zwróć status=repair z repaired_answer zawierającym write_file do notes/main_plan.json "
            "z nowym planem obejmującym niezrealizowane elementy zadania Sponsora.\n"
            f"Polecenie użytkownika: {user_message}"
        )

        try:
            self._set_actor_state("supervisor", "REDIRECT", "Kastor przekierowuje Polluksa po przedwczesnym zakończeniu planu")
            recent_msgs = self._chat_service.memory_repository.recent_messages(limit=6)
            conv_excerpt = format_conversation_excerpt(recent_msgs, limit=6)
            result = supervisor.refine(
                user_message=prompt,
                model_answer=polluks_answer,
                stage="premature_completion_redirect",
                conversation_excerpt=conv_excerpt,
            )
            self._set_actor_state("supervisor", "READY", "Kastor zakończył przekierowanie")

            self._enqueue_supervisor_message(
                stage="premature_completion_redirect",
                reason_code=result.reason_code or "PREMATURE_COMPLETION",
                notes=self._merge_supervisor_notes(
                    "Kastor przekierował Polluksa po przedwczesnym zakończeniu planu.",
                    result.notes,
                ),
                answer=result.answer,
            )
            self._append_log(
                "supervisor_log",
                f"[Kastor -> Polluks] Plan nie jest ukończony wobec zadania Sponsora. {result.notes[:300]}",
            )
            return result.answer
        except (OllamaClientError, OSError):
            self._set_actor_state("supervisor", "ERROR", "Kastor — przekierowanie przerwane błędem")
            return None

    def _single_sentence(self, text: str) -> str:
        compact = " ".join(text.strip().split())
        if not compact:
            return _("identity.reply")
        for idx, char in enumerate(compact):
            if char in ".!?":
                sentence = compact[: idx + 1].strip()
                return sentence or _("identity.reply")
        return compact[:220]

    def _append_plan_event(self, event_type: str, payload: dict) -> None:
        plan_path = self._work_dir / "notes" / "main_plan.json"
        if not plan_path.exists() or not plan_path.is_file():
            return
        try:
            current = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(current, dict):
            return
        history = current.get("collaboration_log")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event_type,
                "payload": payload,
            }
        )
        if len(history) > 100:
            history = history[-100:]
        current["collaboration_log"] = history
        if self._plan_pause_active:
            current["plan_state"] = "PAUSED"
            current["paused_reason"] = self._plan_pause_reason
        else:
            current["plan_state"] = "ACTIVE"
            current.pop("paused_reason", None)
        try:
            plan_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return

    def _record_collaboration_signal(self, signal: str, details: dict | None = None) -> None:
        payload = details or {}
        self._log_activity(
            action=f"collaboration.{signal}",
            intent="Rejestracja współpracy lub braku współpracy między Polluksem i Kastorem.",
            details=payload,
        )
        self._append_plan_event(signal, payload)

    def _runtime_supported_tool_names(self) -> set[str]:
        return set(_SUPPORTED_TEXTUAL_TOOLS).union(list_registered_tools(self._work_dir))

    def _answer_has_supported_tool_call(self, answer: str) -> bool:
        calls = parse_tool_calls(answer)
        if not calls:
            return False
        runtime_tools = self._runtime_supported_tool_names()
        return any(_canonical_tool_name(call.tool) in runtime_tools for call in calls)

    def _set_plan_paused(self, *, paused: bool, reason: str, source: str) -> None:
        self._plan_pause_active = paused
        self._plan_pause_reason = reason if paused else ""
        self._plan_pause_started_monotonic = time.monotonic() if paused else 0.0
        self._append_plan_event(
            "plan_pause_changed",
            {
                "paused": paused,
                "reason": reason,
                "source": source,
            },
        )

    def _interrupt_followup_question(self) -> str:
        return (
            _("identity.followup_question")
        )

    def _merge_supervisor_notes(self, base_note: str, supervisor_note: str) -> str:
        base_clean = " ".join(base_note.strip().split())
        supervisor_clean = " ".join(supervisor_note.strip().split())
        if not supervisor_clean:
            return base_clean[:500]
        if not base_clean:
            return supervisor_clean[:500]
        if supervisor_clean in base_clean:
            return base_clean[:500]
        return f"{base_clean} {supervisor_clean}"[:500]

    def _auto_resume_paused_plan_if_needed(self, now_monotonic: float, *, force: bool = False) -> bool:
        if not self._plan_pause_active:
            return False
        if not force and not self._pending_user_decision:
            return False
        if not force and self._pending_decision_identity_query:
            self._set_actor_state("router", "PAUSED", "Plan wstrzymany po pytaniu tożsamościowym; oczekiwanie na decyzję użytkownika")
            return True
        idle_for = now_monotonic - self._plan_pause_started_monotonic
        if not force and idle_for < INTERRUPT_AUTORESUME_IDLE_SECONDS:
            self._set_actor_state("router", "PAUSED", "Plan wstrzymany: oczekiwanie na decyzję użytkownika")
            return True

        resume_reason = "user_resume" if force else "auto_resume_after_idle"
        resume_source = "user" if force else "watchdog"
        self._set_plan_paused(paused=False, reason=resume_reason, source=resume_source)
        self._pending_user_decision = False
        self._pending_decision_identity_query = False
        self._record_collaboration_signal(
            "auto_resume" if not force else "resume_after_user_decision",
            {
                "idle_seconds": round(idle_for, 2),
                "threshold_seconds": INTERRUPT_AUTORESUME_IDLE_SECONDS,
                "force": force,
            },
        )

        resume_prompt = (
            "Wznów przerwany plan po timeout decyzji użytkownika. "
            "Jeśli nie ma aktywnego planu, zacznij od poznania zasobów frameworka przez pojedynczy tool_call "
            "(preferuj check_capabilities lub list_dir)."
        )
        self._router_cycle_in_progress = True
        self._set_actor_state("router", "RESUMING", f"Auto-wznowienie planu po {INTERRUPT_AUTORESUME_IDLE_SECONDS:.0f}s IDLE")
        self._set_actor_state("creator", "THINKING", "Polluks wznawia plan")
        try:
            answer = self._ask_executor_with_router_mailbox(resume_prompt)
            if self._chat_service.supervisor_service is not None:
                self._set_actor_state("supervisor", "REVIEWING", "Kastor ocenia auto-wznowienie")
                supervision_result = self._chat_service.supervisor_service.refine(
                    user_message=resume_prompt,
                    model_answer=answer,
                    stage="textual_interrupt_autoresume",
                )
                answer = supervision_result.answer
                self._enqueue_supervisor_message(
                    stage="textual_interrupt_autoresume",
                    reason_code=supervision_result.reason_code,
                    notes=self._merge_supervisor_notes(
                        "Auto-wznowienie planu po timeout decyzji użytkownika.",
                        supervision_result.notes,
                    ),
                    answer=answer,
                )
                self._set_actor_state("supervisor", "READY", "Kastor zakończył ocenę auto-wznowienia")
        except (OllamaClientError, OSError) as error:
            self._append_log("user_model_log", f"Błąd auto-wznowienia planu: {error}")
            self._finalize_router_cycle(event="Auto-wznowienie przerwane błędem")
            return True

        answer = self._enforce_supervised_progress(resume_prompt, answer)
        answer = self._resolve_tool_calls(answer)
        self._last_model_answer = answer
        display_answer = _format_user_facing_answer(answer)
        self._append_log("user_model_log", f"Model(auto): {display_answer}")
        self._append_log("executor_log", f"[auto_resume] {answer}")
        if _has_supported_tool_call(answer):
            self._passive_turns += 1
            self._set_actor_state("creator", "PASSIVE", "Auto-wznowienie pozostawiło nierozwiązany krok narzędziowy")
            self._record_collaboration_signal("no_handoff", {"phase": "auto_resume", "tool": False, "residual_tool_call": True})
        else:
            self._passive_turns += 1
            self._set_actor_state("creator", "PASSIVE", "Auto-wznowienie bez kroku narzędziowego")
            self._record_collaboration_signal("no_handoff", {"phase": "auto_resume", "tool": False})
        self._finalize_router_cycle(event="Router wznowił plan po timeout decyzji")
        return True

    def _finalize_router_cycle(self, *, event: str) -> None:
        self._router_cycle_in_progress = False
        self._set_actor_state("router", "ACTIVE", event)
        self._set_actor_state("terminal", "WAITING_INPUT", "Oczekiwanie na kolejną wiadomość użytkownika")
        if self._actor_states.get("creator") in {"THINKING", "ANSWER_READY"}:
            if self._last_model_answer.strip():
                self._set_actor_state("creator", "PASSIVE", "Domknięto cykl wykonania bez aktywnego narzędzia")
            else:
                self._set_actor_state("creator", "WAITING_INPUT", "Brak aktywnej pracy Twórcy")
        self._drain_user_queue()

    def _refresh_router_runtime_state(self) -> None:
        if self._router_cycle_in_progress:
            self._render_router_status()
            return
        if self._actor_states.get("terminal") != "WAITING_INPUT":
            self._set_actor_state("terminal", "WAITING_INPUT", "Synchronizacja stanu terminala")
        now = time.monotonic()
        idle_seconds = now - self._last_progress_monotonic
        creator_state = self._actor_states.get("creator", "")
        if creator_state in {"THINKING", "ANSWER_READY", "EXECUTING_TOOL"} and idle_seconds > 2.0:
            fallback_state = "PASSIVE" if self._last_model_answer.strip() else "WAITING_INPUT"
            self._set_actor_state("creator", fallback_state, "Korekta stanu po zakończonym cyklu")
        self._render_router_status()

    def _append_router_mailbox_log(self, event: str, payload: dict) -> None:
        try:
            self._router_mailbox_log_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "payload": payload,
            }
            with self._router_mailbox_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return

    def _enqueue_supervisor_message(self, *, stage: str, reason_code: str, notes: str, answer: str) -> None:
        tool_calls = parse_tool_calls(answer)
        suggested_step = ""
        runtime_supported = self._runtime_supported_tool_names()
        if tool_calls:
            first_supported = next(
                (call for call in tool_calls if _canonical_tool_name(call.tool) in runtime_supported),
                None,
            )
            if first_supported is not None:
                suggested_step = f"{_canonical_tool_name(first_supported.tool)} ({first_supported.intent})"

        payload = {
            "stage": stage,
            "reason_code": reason_code,
            "notes": notes[:500],
            "suggested_step": suggested_step,
        }

        # --- Dedup: skip if identical to the last enqueued message ---
        if self._supervisor_outbox:
            last = self._supervisor_outbox[-1]
            if (
                last.get("stage") == stage
                and last.get("reason_code") == reason_code
                and last.get("notes") == notes[:500]
                and last.get("suggested_step") == suggested_step
            ):
                return  # duplicate — skip

        self._supervisor_outbox.append(
            {
                "actor": "Kastor",
                "target": "Polluks",
                **payload,
            }
        )
        if len(self._supervisor_outbox) > 10:
            del self._supervisor_outbox[:-10]
        self._append_router_mailbox_log("enqueue", payload)

        # --- Route Kastor's addressed blocks to correct panels ---
        # Notes and answer may contain [Kastor -> Sponsor] or other addressed
        # headers that should reach the user's main screen, not only the
        # supervisor panel.
        panel_map = self._comm_rules.panel_mapping or None
        for text_fragment in (notes, answer):
            if not text_fragment:
                continue
            blocks = parse_addressed_blocks(text_fragment)
            for block in blocks:
                if not block.sender and not block.target:
                    continue  # skip unaddressed fragments
                target_panels = panels_for_target(block.target, panel_map)
                # Only route blocks targeting panels other than supervisor_log,
                # because the supervisor panel already receives the full output.
                extra_panels = [p for p in target_panels if p != "supervisor_log"]
                if extra_panels:
                    label = f"[{block.sender} -> {block.target}]" if block.sender else ""
                    block_content = block.content
                    # Sanitize tool_call content before sending to Sponsor panel
                    if "user_model_log" in extra_panels:
                        sanitized = self._sanitize_block_for_sponsor(block_content, label)
                        if sanitized is None:
                            # Nothing readable — already redirected to executor_log
                            extra_panels = [p for p in extra_panels if p != "user_model_log"]
                            if not extra_panels:
                                continue
                        else:
                            block_content = sanitized
                    for panel_id in extra_panels:
                        self._append_log(panel_id, f"{label} {block_content}" if label else block_content)

    def _drain_supervisor_outbox_context(self) -> str:
        if not self._supervisor_outbox:
            return ""
        queued_messages = [dict(message) for message in self._supervisor_outbox]
        lines = ["[Kastor -> Polluks]", "[ROUTER_MAILBOX_FROM_KASTOR]"]
        for index, message in enumerate(self._supervisor_outbox, start=1):
            lines.append(
                f"{index}) stage={message.get('stage','')}; reason={message.get('reason_code','')}; "
                f"notes={message.get('notes','')}; suggested_step={message.get('suggested_step','')}"
            )
        lines.append("[/ROUTER_MAILBOX_FROM_KASTOR]")
        self._supervisor_outbox.clear()
        self._append_router_mailbox_log(
            "deliver",
            {
                "messages_count": len(queued_messages),
                "messages": queued_messages,
            },
        )
        return "\n".join(lines)

    def _ask_executor_with_router_mailbox(self, message: str) -> str:
        mailbox_context = self._drain_supervisor_outbox_context()
        wrapped = f"[Sponsor -> all] {message}" if not message.startswith("[") else message
        if mailbox_context:
            self._set_actor_state("router", "ROUTING", "Router dostarcza kolejkę zaleceń Kastora do Polluksa")
            enriched = wrapped + "\n\n" + mailbox_context
            return self._chat_service.ask(enriched, actor="Sponsor")
        return self._chat_service.ask(wrapped, actor="Sponsor")

    def _parse_idle_until(self, raw_value: str) -> float | None:
        value = raw_value.strip()
        if not value:
            return None
        lowered = value.lower()
        if lowered in {"off", "none", "false", "0", "wyłącz", "wylacz"}:
            return None
        normalized = value.replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    def _apply_idle_hint_from_answer(self, answer: str, source: str) -> None:
        marker = "IDLE_UNTIL:"
        if marker not in answer:
            return
        tail = answer.split(marker, 1)[1].strip().splitlines()[0].strip()
        parsed = self._parse_idle_until(tail)
        if parsed is None and tail.lower() not in {"off", "none", "false", "0", "wyłącz", "wylacz"}:
            return
        self._set_idle_until(parsed, source)

    def _append_log(self, widget_id: str, message: str) -> None:
        def _apply() -> None:
            area = self.query_one(f"#{widget_id}", TextArea)
            payload = message.rstrip("\n")
            if not payload:
                return
            if area.text:
                area.load_text(f"{area.text}\n{payload}")
            else:
                area.load_text(payload)
            area.scroll_end(animate=False)

        self._run_on_ui_thread(_apply)

    def _sanitize_block_for_sponsor(
        self,
        block_content: str,
        label: str,
    ) -> str | None:
        """Strip tool_call blocks from content destined for the Sponsor panel.

        Returns sanitised content ready for ``user_model_log``, or ``None``
        when nothing human-readable remains (caller should redirect / skip).

        Side-effect: if any tool_call material was removed, the *original*
        (full) content is echoed to ``executor_log`` so the information is
        not lost.
        """
        sanitized = strip_tool_call_blocks(block_content)
        if not sanitized or not is_sponsor_readable(sanitized):
            # Nothing readable for Sponsor — redirect entirely to executor
            self._append_log(
                "executor_log",
                f"{label} {block_content}" if label else block_content,
            )
            self._append_log(
                "supervisor_log",
                _("coordinator.tool_redirected"),
            )
            return None
        if sanitized != block_content:
            # Had tool_call material stripped — echo full version to executor
            self._append_log(
                "executor_log",
                f"{label} {block_content}" if label else block_content,
            )
        return sanitized

    def _clear_textual_panels(self, *, clear_all: bool) -> None:
        panel_ids = ["user_model_log"] if not clear_all else ["user_model_log", "supervisor_log", "executor_log"]
        for panel_id in panel_ids:
            try:
                area = self.query_one(f"#{panel_id}", TextArea)
                area.load_text("")
            except Exception:
                continue
        if clear_all:
            try:
                router = self.query_one("#router_status", Static)
                router.update("")
            except Exception:
                pass

    def action_copy_selection(self) -> None:
        focused = self.focused
        if isinstance(focused, TextArea):
            text = focused.selected_text or focused.text
            if text:
                copied, _details = _copy_to_system_clipboard(text)
                if copied:
                    self.notify(_("clipboard.copied_notify"))
                else:
                    self.copy_to_clipboard(text)
                    self.notify(
                        _("clipboard.copied_notify"),
                        severity="information",
                    )
                return
        self.notify(
            _("clipboard.no_selection"),
            severity="information",
        )

    def _resource_allowed(self, resource: str) -> bool:
        if self._permission_manager.allow_all:
            return True
        if resource in getattr(self._permission_manager, "granted_once", set()):
            return True
        return False

    def _ensure_resource(self, resource: str, reason: str) -> bool:
        if self._resource_allowed(resource):
            return True
        self._append_log(
            "user_model_log",
            _("resource.denied", reason=reason, resource=resource),
        )
        return False

    def _handle_cli_like_commands(self, raw_text: str) -> _CommandOutcome:
        text = raw_text.strip()
        lower = text.lower()

        if lower == "/cls":
            self._clear_textual_panels(clear_all=False)
            self.notify(_("cls.main_done"), severity="information")
            return _CommandOutcome(True, [])

        if lower == "/cls all":
            self._clear_textual_panels(clear_all=True)
            self.notify(_("cls.all_done"), severity="information")
            return _CommandOutcome(True, [])

        if lower.startswith("/models"):
            parts = text.split()
            if len(parts) < 2:
                return _CommandOutcome(True, [_("models.usage")])

            action = parts[1].lower()
            if action == "current":
                # Polluks (executor)
                polluks_model = str(getattr(self._chat_service.ollama_client, "model", "")) or _("models.not_set")
                polluks_api = getattr(self._chat_service.ollama_client, "_is_api_client", False)
                polluks_label = f"☁ {polluks_model}" if polluks_api else polluks_model

                # Kastor (supervisor)
                supervisor = self._chat_service.supervisor_service
                if supervisor is not None:
                    kastor_model = str(getattr(supervisor.ollama_client, "model", "")) or _("models.not_set")
                    kastor_api = getattr(supervisor.ollama_client, "_is_api_client", False)
                    kastor_label = f"☁ {kastor_model}" if kastor_api else kastor_model
                else:
                    kastor_label = _("models.kastor_inactive")

                return _CommandOutcome(True, [
                    _("models.active_header"),
                    _("models.polluks_label", label=polluks_label),
                    _("models.kastor_label", label=kastor_label),
                ])
            if action == "show":
                combined = self._build_wizard_model_list()
                if not combined:
                    return _CommandOutcome(True, [_("models.none_available")])

                current_model = str(getattr(self._chat_service.ollama_client, "model", ""))
                is_api = getattr(self._chat_service.ollama_client, "_is_api_client", False)
                messages = [_("models.header_polluks")]
                idx = 1
                ollama_models = [(n, s) for n, s in combined if s == "ollama"]
                api_models = [(n, s) for n, s in combined if s != "ollama"]
                if ollama_models:
                    messages.append(_("models.local_header"))
                    for name, _size in ollama_models:
                        marker = _("models.active_marker") if name == current_model and not is_api else ""
                        messages.append(f"  {idx}. {name}{marker}")
                        idx += 1
                if api_models:
                    messages.append(_("models.api_header"))
                    for name, source in api_models:
                        marker = _("models.active_marker") if name == current_model and is_api else ""
                        messages.append(f"  {idx}. ☁ {name}  [{source.upper()}]{marker}")
                        idx += 1
                messages.append(_("models.chose_usage"))
                return _CommandOutcome(True, messages)

            if action in {"chose", "choose"}:
                if len(parts) < 3:
                    return _CommandOutcome(True, [_("models.chose_usage")])
                try:
                    index = int(parts[2])
                except ValueError:
                    return _CommandOutcome(
                        True,
                        [_("models.invalid_number")],
                    )

                combined = self._build_wizard_model_list()
                if index < 1 or index > len(combined):
                    return _CommandOutcome(
                        True,
                        [_("models.invalid_range", max=len(combined))],
                    )
                name, source = combined[index - 1]
                if source == "ollama":
                    ok, payload, _models = _select_executor_model_by_index(self._chat_service, index)
                    if not ok:
                        return _CommandOutcome(True, [payload])
                    self._sync_agent_model("polluks", payload, "ollama")
                    self._persist_model_config()
                    return _CommandOutcome(True, [_("models.active_executor", model=payload)])
                else:
                    # Switch Polluks to API model
                    api_key = os.environ.get("OPENAI_API_KEY", "")
                    settings = self._settings
                    if not api_key and settings is not None:
                        api_key = settings.openai_api_key
                    if not api_key:
                        return _CommandOutcome(True, [_("models.no_api_key")])
                    base_url = "https://api.openai.com/v1"
                    timeout = 120
                    if settings is not None:
                        base_url = settings.openai_base_url or base_url
                        timeout = settings.openai_request_timeout_seconds or timeout
                    openai_client = OpenAIClient(
                        api_key=api_key,
                        model=name,
                        base_url=base_url,
                        io_logger=getattr(self._chat_service.ollama_client, "io_logger", None),
                        activity_logger=self._activity_logger,
                        client_role="executor",
                        request_timeout_seconds=timeout,
                        usage_tracker=self._usage_tracker,
                    )
                    self._chat_service.ollama_client = openai_client
                    self._sync_agent_model("polluks", name, source)
                    self._show_api_usage_bar()
                    self._persist_model_config()
                    return _CommandOutcome(True, [_("models.active_executor_api", name=name, source=source.upper())])

            return _CommandOutcome(True, [_("models.usage")])

        if lower == "/router-status":
            return _CommandOutcome(
                True,
                [
                    _("router_status.header"),
                    f"Router: {self._actor_states.get('router', 'UNKNOWN')}",
                    f"Polluks: {self._actor_states.get('creator', 'UNKNOWN')}",
                    f"Kastor: {self._actor_states.get('supervisor', 'UNKNOWN')}",
                    f"Terminal: {self._actor_states.get('terminal', 'UNKNOWN')}",
                    f"plan_pause: {'ON' if self._plan_pause_active else 'OFF'}",
                    f"pending_decision: {'YES' if self._pending_user_decision else 'NO'}",
                    f"IDLE until: {self._format_idle_until()}",
                    f"{_('router.last_event_label')} {self._last_router_event}",
                ],
            )

        if lower.startswith("/idle-until"):
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                return _CommandOutcome(True, [f"IDLE until: {self._format_idle_until()}"])

            raw_value = parts[1].strip()
            parsed = self._parse_idle_until(raw_value)
            if parsed is None and raw_value.lower() not in {"off", "none", "false", "0", "wyłącz", "wylacz"}:
                return _CommandOutcome(
                    True,
                    [
                        _("idle_until.invalid_format"),
                    ],
                )

            self._set_idle_until(parsed, source="terminal_command")
            if parsed is None:
                return _CommandOutcome(True, [_("idle_until.cleared")])
            return _CommandOutcome(True, [_("idle_until.set", value=self._format_idle_until())])

        if lower == "/queue-status":
            policy = self._chat_service.ollama_client.queue_policy
            vram_advisor = self._chat_service.ollama_client.vram_advisor
            messages = []
            if policy is None:
                return _CommandOutcome(True, [_("queue.disabled")])

            snapshot = policy.snapshot()
            messages.append(_("queue.header"))
            messages.append(f"queue_length: {snapshot.get('queue_length', 0)}")
            messages.append(f"queue: {snapshot.get('queue', [])}")
            messages.append(f"queue_max_wait_seconds: {snapshot.get('queue_max_wait_seconds')}")
            messages.append(f"supervisor_min_free_vram_mb: {snapshot.get('supervisor_min_free_vram_mb')}")

            if vram_advisor is not None:
                profile = vram_advisor.detect()
                messages.append(
                    "vram: "
                    f"free_mb={profile.free_mb}, total_mb={profile.total_mb}, "
                    f"suggested_num_ctx={profile.suggested_num_ctx}"
                )
            else:
                messages.append(_("queue.no_vram_advisor"))
            return _CommandOutcome(True, messages)

        if lower.startswith("/capabilities"):
            check_network = "--network" in lower.split()
            capabilities = {
                "tool": "check_capabilities",
                "python": shutil.which("python") is not None,
                "fswebcam": shutil.which("fswebcam") is not None,
                "ffmpeg": shutil.which("ffmpeg") is not None,
                "arecord": shutil.which("arecord") is not None,
                "camera_devices": sorted(str(path) for path in Path("/dev").glob("video*")),
                "network_checked": check_network,
            }
            if check_network:
                capabilities["ollama_reachable"] = bool(self._chat_service.ollama_client.ping())
            return _CommandOutcome(True, [_("capabilities.header"), json.dumps(capabilities, ensure_ascii=False, indent=2)])

        if lower.startswith("/show-system-context"):
            parts = text.split(maxsplit=1)
            sample_message = parts[1].strip() if len(parts) == 2 else "kontekst diagnostyczny"
            prompt = self._chat_service.build_system_prompt(sample_message)
            return _CommandOutcome(True, [_("system_context.header"), prompt])

        if lower in {"/goal-status", "/goal"}:
            snapshot = _read_plan_tracking_snapshot(self._work_dir)
            repair_info: dict | None = None
            if snapshot.get("parse_error"):
                repair_info = _repair_plan_tracking_file(self._work_dir)
                snapshot = _read_plan_tracking_snapshot(self._work_dir)

            tasks_lbl = _("goal.tasks_label", done=snapshot.get('tasks_done', 0), total=snapshot.get('tasks_total', 0))
            messages = [
                _("goal.header"),
                f"path: {snapshot.get('path')}",
                f"exists: {snapshot.get('exists')}",
                f"goal: {snapshot.get('goal', '')}",
                f"current_stage: {snapshot.get('current_stage', '')}",
                f"tasks: {tasks_lbl}",
            ]
            if snapshot.get("parse_error"):
                messages.append("parse_error: true")
            if repair_info and repair_info.get("repaired"):
                messages.append("auto_repair: true")
                if repair_info.get("backup_path"):
                    messages.append(f"backup_path: {repair_info.get('backup_path')}")
            return _CommandOutcome(True, messages)

        if lower.startswith("/import-dialog"):
            if not self._ensure_resource("disk.read", _("resource.import_dialog_read")):
                return _CommandOutcome(True, [])

            parts = text.split(maxsplit=1)
            path = Path(parts[1].strip()) if len(parts) == 2 else Path("początkowe_konsultacje.md")
            if not path.exists():
                return _CommandOutcome(True, [_("import_dialog.file_not_found", path=path)])

            dialogue = extract_dialogue_without_code(path.read_text(encoding="utf-8"))
            self._chat_service.save_discussion_context(dialogue)
            return _CommandOutcome(True, [_("import_dialog.done")])

        if lower.startswith("/create-python"):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                return _CommandOutcome(True, [_("create_python.usage")])

            network_resource = _network_resource_for_model(self._chat_service.ollama_client.base_url)
            if not self._ensure_resource(
                network_resource,
                _("resource.model_network"),
            ):
                return _CommandOutcome(True, [])
            if not self._ensure_resource("disk.write", _("resource.script_write")):
                return _CommandOutcome(True, [])

            output_path = Path(parts[1].strip())
            description = parts[2].strip()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            code = self._chat_service.generate_python_code(description)
            output_path.write_text(code + "\n", encoding="utf-8")
            return _CommandOutcome(True, [_("create_python.saved", path=output_path)])

        if lower.startswith("/run-python"):
            parts = shlex.split(text)
            if len(parts) < 2:
                return _CommandOutcome(True, [_("run_python.usage")])

            if not self._ensure_resource("disk.read", _("resource.script_read")):
                return _CommandOutcome(True, [])
            if not self._ensure_resource("process.exec", _("resource.script_exec")):
                return _CommandOutcome(True, [])

            script_path = Path(parts[1])
            script_args = parts[2:]
            if not script_path.exists():
                return _CommandOutcome(True, [_("run_python.script_not_found", path=script_path)])

            result = self._script_executor.execute_python(script_path, script_args)
            cmd_lbl = _("run_python.command_label", command=' '.join(result.command))
            exit_lbl = _("run_python.exit_code_label", code=result.exit_code)
            messages = [cmd_lbl, exit_lbl]
            if result.stdout.strip():
                messages.extend(["--- STDOUT ---", result.stdout])
            if result.stderr.strip():
                messages.extend(["--- STDERR ---", result.stderr])
            return _CommandOutcome(True, messages)

        if lower.startswith("/run-shell"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return _CommandOutcome(True, [_("run_shell.usage")])

            command_text = parts[1].strip()
            _ok, validation_error = parse_and_validate_shell_command(command_text, self._shell_policy)
            if validation_error is not None:
                return _CommandOutcome(True, [_("run_shell.rejected", error=validation_error)])

            if not self._ensure_resource("process.exec", _("resource.shell_exec")):
                return _CommandOutcome(True, [])

            result = self._script_executor.execute_shell(command_text)
            cmd_lbl = _("run_python.command_label", command=' '.join(result.command))
            exit_lbl = _("run_python.exit_code_label", code=result.exit_code)
            messages = [cmd_lbl, exit_lbl]
            if result.stdout.strip():
                messages.extend(["--- STDOUT ---", result.stdout])
            if result.stderr.strip():
                messages.extend(["--- STDERR ---", result.stderr])
            return _CommandOutcome(True, messages)

        if lower.startswith("/history"):
            parts = text.split(maxsplit=1)
            limit = 10
            if len(parts) == 2 and parts[1].isdigit():
                limit = max(1, min(200, int(parts[1])))
            messages = self._chat_service.memory_repository.recent_messages(limit=limit)
            if not messages:
                return _CommandOutcome(True, [_("history.empty")])
            rendered = [
                f"[{message.created_at.isoformat(timespec='seconds')}] {message.role}: {message.content}"
                for message in messages
            ]
            return _CommandOutcome(True, rendered)

        if lower.startswith("/remember"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                return _CommandOutcome(True, [_("remember.usage")])
            self._chat_service.remember(parts[1].strip())
            return _CommandOutcome(True, [_("remember.saved")])

        if lower.startswith("/memories"):
            parts = text.split(maxsplit=1)
            query = parts[1].strip() if len(parts) == 2 else None
            records = self._chat_service.memory_repository.search_memories(query=query, limit=20)
            if not records:
                return _CommandOutcome(True, [_("memories.empty")])
            rendered = [
                f"[{record.created_at.isoformat(timespec='seconds')}] {record.kind}/{record.source}: {record.content}"
                for record in records
            ]
            return _CommandOutcome(True, rendered)

        if lower == "/bye":
            network_resource = _network_resource_for_model(self._chat_service.ollama_client.base_url)
            if not self._ensure_resource(
                network_resource,
                _("resource.session_summary_network"),
            ):
                return _CommandOutcome(True, [])
            comm_state = {
                "unaddressed_turns": self._unaddressed_turns,
                "passive_turns": self._passive_turns,
                "supervisor_outbox_size": len(self._supervisor_outbox),
            }
            summary = self._chat_service.summarize_session_for_restart(communication_state=comm_state)
            return _CommandOutcome(
                True,
                [
                    _("bye.saved"),
                    _("bye.start_point"),
                    summary,
                    _("bye.farewell"),
                ],
                should_exit=True,
            )

        if lower.startswith("/kastor-model"):
            parts = text.split()
            supervisor = self._chat_service.supervisor_service
            if supervisor is None:
                return _CommandOutcome(True, [_("kastor.inactive")])

            action = parts[1].lower() if len(parts) > 1 else "show"
            if action in {"show", "current"}:
                current = str(getattr(supervisor.ollama_client, "model", ""))
                is_api = getattr(supervisor.ollama_client, "_is_api_client", False)
                label = f"☁ {current}" if is_api else current
                return _CommandOutcome(True, [_("kastor.active_model", label=label)])

            if action in {"chose", "choose"}:
                if len(parts) < 3:
                    return _CommandOutcome(True, [_("kastor.usage")])
                try:
                    idx = int(parts[2])
                except ValueError:
                    return _CommandOutcome(True, [_("kastor.give_number")])

                combined = self._build_wizard_model_list()
                if idx < 1 or idx > len(combined):
                    return _CommandOutcome(
                        True,
                        [_("models.invalid_range", max=len(combined))],
                    )
                name, source = combined[idx - 1]
                if source == "ollama":
                    try:
                        supervisor.ollama_client = replace(
                            cast(Any, supervisor.ollama_client), model=name
                        )
                    except Exception:
                        return _CommandOutcome(True, [_("kastor.switch_failed", name=name)])
                    self._persist_model_config()
                    return _CommandOutcome(True, [_("kastor.switched", name=name)])
                else:
                    api_key = os.environ.get("OPENAI_API_KEY", "")
                    settings = self._settings
                    if not api_key and settings is not None:
                        api_key = settings.openai_api_key
                    if not api_key:
                        return _CommandOutcome(True, [_("models.no_api_key")])
                    base_url = "https://api.openai.com/v1"
                    timeout = 120
                    if settings is not None:
                        base_url = settings.openai_base_url or base_url
                        timeout = settings.openai_request_timeout_seconds or timeout
                    kastor_openai = OpenAIClient(
                        api_key=api_key,
                        model=name,
                        base_url=base_url,
                        io_logger=getattr(supervisor.ollama_client, "io_logger", None),
                        activity_logger=self._activity_logger,
                        client_role="supervisor",
                        request_timeout_seconds=timeout,
                        usage_tracker=self._usage_tracker,
                    )
                    supervisor.ollama_client = cast(Any, kastor_openai)
                    self._show_api_usage_bar()
                    self._persist_model_config()
                    return _CommandOutcome(True, [_("kastor.switched_api", name=name, source=source.upper())])

            # Show available models list
            combined = self._build_wizard_model_list()
            body = self._format_wizard_model_list(combined)
            return _CommandOutcome(True, [_("kastor.models_header"), body, _("kastor.usage")])

        if lower == "/api-usage":
            detailed = self._usage_tracker.format_detailed()
            if not detailed:
                return _CommandOutcome(True, [_("api_usage.empty")])
            return _CommandOutcome(True, [_("api_usage.header"), detailed])

        if lower == "/api-key verify":
            api_key = os.environ.get("OPENAI_API_KEY", "")
            settings = self._settings
            if not api_key and settings is not None:
                api_key = settings.openai_api_key
            if not api_key:
                return _CommandOutcome(True, [_("api_key.missing")])
            masked = mask_api_key(api_key)
            try:
                test_client = OpenAIClient(api_key=api_key, model="gpt-5-mini")
                reachable = test_client.ping()
            except Exception:
                reachable = False
            if reachable:
                return _CommandOutcome(True, [_("api_key.ok", masked=masked)])
            return _CommandOutcome(True, [_("api_key.fail", masked=masked)])

        # ==================================================================
        # v0.2.0 — /skills commands
        # ==================================================================
        if lower == "/skills reload":
            sl = self._chat_service.skills_loader if self._chat_service else None
            if sl is None:
                return _CommandOutcome(True, [_("skills.no_loader")])
            sl.reload()
            available = sl.list_available()
            total = sum(len(v) for v in available.values())
            return _CommandOutcome(True, [_("skills.reloaded", total=total, roles=len(available))])

        if lower == "/skills":
            sl = self._chat_service.skills_loader if self._chat_service else None
            if sl is None:
                return _CommandOutcome(True, [_("skills.no_loader")])
            available = sl.list_available()
            if not available:
                return _CommandOutcome(True, [_("skills.empty")])
            lines = [_("skills.header")]
            for role, names in sorted(available.items()):
                lines.append(f"  {role}/: {', '.join(names)}")
            return _CommandOutcome(True, lines)

        # ==================================================================
        # Phase 1 — /agents commands
        # ==================================================================
        if lower.startswith("/agents"):
            return self._handle_agents_command(text)

        # ==================================================================
        # Phase 2 — /agent-wizard commands
        # ==================================================================
        if lower.startswith("/agent-wizard"):
            return self._handle_agent_wizard_command(text)

        # ==================================================================
        # Phase 3 — /tasks commands
        # ==================================================================
        if lower.startswith("/tasks"):
            return self._handle_tasks_command(text)

        # ==================================================================
        # Phase 4 — /dashboard commands
        # ==================================================================
        if lower.startswith("/dashboard"):
            return self._handle_dashboard_command(text)

        # ==================================================================
        # Phase 5 — /knowledge, /workspace commands
        # ==================================================================
        if lower.startswith("/knowledge"):
            return self._handle_knowledge_command(text)
        if lower.startswith("/workspace"):
            return self._handle_workspace_command(text)

        # ==================================================================
        # Phase 7 — /audit, /permissions, /sandbox commands (security)
        # ==================================================================
        if lower.startswith("/audit"):
            return self._handle_audit_command(text)
        if lower.startswith("/sandbox"):
            return self._handle_sandbox_command(text)

        # ==================================================================
        # Phase 6 — /workflow commands
        # ==================================================================
        if lower.startswith("/workflow"):
            return self._handle_workflow_command(text)

        # ==================================================================
        # Phase 8 — /budget, /quota commands
        # ==================================================================
        if lower.startswith("/budget"):
            return self._handle_budget_command(text)
        if lower.startswith("/quota"):
            return self._handle_quota_command(text)

        # ==================================================================
        # Phase 9 — /eval, /feedback commands
        # ==================================================================
        if lower.startswith("/eval"):
            return self._handle_eval_command(text)
        if lower.startswith("/feedback"):
            return self._handle_feedback_command(text)

        # ==================================================================
        # Phase 10 — /api, /plugins commands
        # ==================================================================
        if lower.startswith("/api"):
            return self._handle_api_command(text)
        if lower.startswith("/plugins"):
            return self._handle_plugins_command(text)

        # ==================================================================
        # Phase 11 — /team commands
        # ==================================================================
        if lower.startswith("/team"):
            return self._handle_team_command(text)

        return _CommandOutcome(False, [])

    # ------------------------------------------------------------------
    # Agent Management Commands (Phase 1)
    # ------------------------------------------------------------------

    def _handle_agents_command(self, raw_text: str) -> _CommandOutcome:
        """Dispatch ``/agents`` subcommands."""
        parts = raw_text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "list"

        if self._agent_registry is None:
            return _CommandOutcome(True, [_("agents.no_registry")])

        if action == "list":
            agents = self._agent_registry.list_all()
            if not agents:
                return _CommandOutcome(True, [_("agents.empty")])
            messages = [_("agents.header")]
            for a in agents:
                model_label = a.model_name or _("agents.no_model")
                messages.append(
                    f"  {a.agent_id}  {a.name:12s}  rola={a.role.value:10s}  "
                    f"stan={a.state.value:10s}  model={model_label}"
                )
            return _CommandOutcome(True, messages)

        if action == "info":
            if len(parts) < 3:
                return _CommandOutcome(True, [_("agents.info_usage")])
            query = parts[2]
            agent = self._agent_registry.get(query)
            if agent is None:
                # Try matching by name
                for a in self._agent_registry.list_all():
                    if a.name.lower() == query.lower():
                        agent = a
                        break
            if agent is None:
                return _CommandOutcome(True, [_("agents.not_found", query=query)])
            no_val = _("agents.info.no_skills")
            messages = [
                _("agents.info_header"),
                f"  ID:        {agent.agent_id}",
                f"  {_('agents.label.name')}     {agent.name}",
                f"  {_('agents.label.role')}      {agent.role.value}",
                f"  {_('agents.label.state')}      {agent.state.value}",
                f"  {_('agents.label.backend')}   {agent.model_backend}",
                f"  {_('agents.label.model')}     {agent.model_name or no_val}",
                f"  {_('agents.label.skills')} {', '.join(agent.skills) or no_val}",
                f"  {_('agents.label.tools')} {', '.join(agent.tools) or no_val}",
                f"  {_('agents.label.created')}  {agent.created_at.isoformat(timespec='seconds')}",
            ]
            if agent.persona_prompt:
                messages.append(f"  {_('agents.label.persona')}   {agent.persona_prompt[:120]}...")
            if agent.metadata:
                messages.append(f"  {_('agents.label.metadata')}  {json.dumps(agent.metadata, ensure_ascii=False)}")
            return _CommandOutcome(True, messages)

        if action == "pause":
            if len(parts) < 3:
                return _CommandOutcome(True, [_("agents.pause_usage")])
            agent_id = parts[2]
            try:
                self._agent_registry.update_state(agent_id, AgentState.PAUSED, reason="manual")
                return _CommandOutcome(True, [_("agents.paused", id=agent_id)])
            except (KeyError, ValueError) as exc:
                return _CommandOutcome(True, [_("agents.error", error=exc)])

        if action == "resume":
            if len(parts) < 3:
                return _CommandOutcome(True, [_("agents.resume_usage")])
            agent_id = parts[2]
            try:
                self._agent_registry.update_state(agent_id, AgentState.IDLE, reason="manual_resume")
                return _CommandOutcome(True, [_("agents.resumed", id=agent_id)])
            except (KeyError, ValueError) as exc:
                return _CommandOutcome(True, [_("agents.error", error=exc)])

        if action == "terminate":
            if len(parts) < 3:
                return _CommandOutcome(True, [_("agents.terminate_usage")])
            agent_id = parts[2]
            try:
                self._agent_registry.update_state(agent_id, AgentState.TERMINATED, reason="manual_terminate")
                return _CommandOutcome(True, [_("agents.terminated", id=agent_id)])
            except (KeyError, ValueError) as exc:
                return _CommandOutcome(True, [_("agents.error", error=exc)])

        return _CommandOutcome(True, [
            _("agents.usage_full")
        ])

    # ------------------------------------------------------------------
    # Agent Wizard Commands (Phase 2)
    # ------------------------------------------------------------------

    def _handle_agent_wizard_command(self, raw_text: str) -> _CommandOutcome:
        """Dispatch ``/agent-wizard`` subcommands."""
        parts = raw_text.strip().split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "help"

        if self._agent_factory is None:
            return _CommandOutcome(True, [_("wizard.no_factory")])

        # Lazy-init the wizard service
        if self._wizard_service is None:
            planner = self._chat_service.ollama_client if hasattr(self._chat_service.ollama_client, "chat") else None
            self._wizard_service = AgentWizardService(
                planner_client=planner,
                factory=self._agent_factory,
                blueprints_dir=self._settings.blueprints_dir if self._settings else Path("./data/agents/blueprints"),
            )

        if action == "create":
            if len(parts) < 3:
                return _CommandOutcome(True, [_("wizard.create_usage")])
            need = parts[2]
            try:
                blueprint = self._wizard_service.generate_blueprint(need)
                runtime = self._wizard_service.create_agent(blueprint)
                saved_path = self._wizard_service.save_blueprint(blueprint)
                return _CommandOutcome(True, [
                    _("wizard.created_header"),
                    _("wizard.created", name=blueprint.name, id=runtime.agent_id),
                    f"  {_('agents.label.role')}  {blueprint.role}",
                    f"  {_('agents.label.function')} {blueprint.team_function}",
                    f"  {_('agents.label.tools')} {', '.join(blueprint.required_tools)}",
                    f"  Blueprint: {saved_path}",
                ])
            except Exception as exc:
                return _CommandOutcome(True, [_("wizard.create_error", error=exc)])

        if action == "blueprints":
            names = self._wizard_service.list_blueprints()
            if not names:
                return _CommandOutcome(True, [_("wizard.no_blueprints")])
            messages = [_("wizard.blueprints_header")]
            for i, name in enumerate(names, 1):
                messages.append(f"  {i}. {name}")
            return _CommandOutcome(True, messages)

        if action == "load":
            if len(parts) < 3:
                return _CommandOutcome(True, [_("wizard.load_usage")])
            bp_name = parts[2].strip()
            blueprint = self._wizard_service.load_blueprint(bp_name)
            if blueprint is None:
                return _CommandOutcome(True, [_("wizard.load_not_found", name=bp_name)])
            return _CommandOutcome(True, [
                _("wizard.blueprint_header"),
                f"  {_('agents.label.name')}  {blueprint.name}",
                f"  {_('agents.label.role')}  {blueprint.role}",
                f"  {_('agents.label.function')} {blueprint.team_function}",
                f"  {_('agents.label.skills')} {', '.join(blueprint.required_skills)}",
                f"  {_('agents.label.tools')} {', '.join(blueprint.required_tools)}",
                f"  {_('agents.label.persona')}   {blueprint.persona_prompt[:120]}...",
            ])

        return _CommandOutcome(True, [
            _("wizard.usage_full")
        ])

    # ------------------------------------------------------------------
    # Task Management Commands (Phase 3)
    # ------------------------------------------------------------------

    def _handle_tasks_command(self, raw_text: str) -> _CommandOutcome:
        """Dispatch ``/tasks`` subcommands."""
        parts = raw_text.strip().split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "list"

        if self._task_queue is None:
            return _CommandOutcome(True, [_("tasks.no_queue")])

        if action == "list":
            tasks = self._task_queue.list_all()
            if not tasks:
                return _CommandOutcome(True, [_("tasks.empty")])
            messages = [_("tasks.header")]
            for t in tasks:
                agent_info = f" → {t.assigned_agent_id}" if t.assigned_agent_id else ""
                messages.append(
                    f"  {t.task_id[:8]}  [{t.priority.name:8s}]  {t.status.value:11s}  "
                    f"{t.title[:40]}{agent_info}"
                )
            return _CommandOutcome(True, messages)

        if action == "add":
            if len(parts) < 3:
                return _CommandOutcome(True, [_("tasks.add_usage")])
            title = parts[2].strip()
            import uuid as _uuid
            task = Task(
                task_id=_uuid.uuid4().hex[:12],
                title=title,
                description=title,
                priority=TaskPriority.NORMAL,
            )
            self._task_queue.enqueue(task)
            return _CommandOutcome(True, [_("tasks.added", id=task.task_id, title=title)])

        if action == "info":
            if len(parts) < 3:
                return _CommandOutcome(True, [_("tasks.info_usage")])
            query = parts[2].strip()
            # Support partial ID match
            task = self._task_queue.get(query)
            if task is None:
                for t in self._task_queue.list_all():
                    if t.task_id.startswith(query):
                        task = t
                        break
            if task is None:
                return _CommandOutcome(True, [_("tasks.not_found", query=query)])
            not_assigned = _("tasks.info_not_assigned")
            messages = [
                _("tasks.info_header"),
                f"  ID:        {task.task_id}",
                f"  {_('tasks.label.title')}     {task.title}",
                f"  {_('tasks.label.desc')}      {task.description[:200]}",
                f"  {_('tasks.label.priority')} {task.priority.name}",
                f"  {_('tasks.label.status')}    {task.status.value}",
                f"  {_('tasks.label.agent')}     {task.assigned_agent_id or not_assigned}",
                f"  {_('tasks.label.created')}  {task.created_at.isoformat(timespec='seconds')}",
            ]
            if task.dependencies:
                messages.append(f"  {_('tasks.label.deps')} {', '.join(task.dependencies)}")
            if task.result:
                messages.append(f"  {_('tasks.label.result')}     {task.result[:200]}")
            return _CommandOutcome(True, messages)

        if action == "cancel":
            if len(parts) < 3:
                return _CommandOutcome(True, [_("tasks.cancel_usage")])
            task_id = parts[2].strip()
            task = self._task_queue.get(task_id)
            if task is None:
                return _CommandOutcome(True, [_("tasks.not_found", query=task_id)])
            try:
                task.cancel()
                return _CommandOutcome(True, [_("tasks.cancelled", id=task_id)])
            except ValueError as exc:
                return _CommandOutcome(True, [_("agents.error", error=exc)])

        if action == "stats":
            stats = self._task_queue.stats()
            messages = [_("tasks.stats_header")]
            for status_name, count in sorted(stats.items()):
                messages.append(f"  {status_name}: {count}")
            return _CommandOutcome(True, messages)

        return _CommandOutcome(True, [
            _("tasks.usage_full")
        ])

    # ------------------------------------------------------------------
    # Dashboard Commands (Phase 4)
    # ------------------------------------------------------------------

    def _handle_dashboard_command(self, raw_text: str) -> _CommandOutcome:
        """Dispatch ``/dashboard`` subcommands.

        ``/dashboard start [port]`` performs a full bootstrap:
        1. Starts the REST API backend (if not already running).
        2. Verifies that routes are registered.
        3. Starts the dashboard frontend HTTP server.
        4. Opens the default web browser.

        Legacy sub-commands ``/dashboard stop`` and ``/dashboard status``
        remain available for diagnostics.
        """
        parts = raw_text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "status"

        if action == "start":
            if self._dashboard_server is not None and self._dashboard_server.running:
                port = self._dashboard_server.port
                return _CommandOutcome(True, [_("dashboard.already_running", port=port)])

            msgs: list[str] = []

            # --- Step 1: ensure REST API backend is running ---
            if self._rest_server is not None:
                if not self._rest_server.is_running:
                    self._rest_server.start()
                    msgs.append(_("dashboard.rest_started", address=self._rest_server.address))
                else:
                    msgs.append(_("dashboard.rest_active", address=self._rest_server.address))
                route_count = len(self._rest_server.list_routes())
                msgs.append(_("dashboard.rest_routes", count=route_count))
            else:
                msgs.append(_("dashboard.rest_missing"))

            # --- Step 2: start dashboard frontend ---
            port = 8080
            if len(parts) > 2:
                try:
                    port = int(parts[2])
                except ValueError:
                    return _CommandOutcome(True, [_("dashboard.invalid_port")])
            elif self._settings is not None:
                port = self._settings.dashboard_port

            static_dir = Path(__file__).parent / "dashboard_static"
            self._dashboard_server = DashboardServer(
                registry=self._agent_registry,
                task_queue=self._task_queue,
                metrics_collector=self._metrics_collector,
                alert_manager=self._alert_manager,
                session_replay=self._session_replay,
                budget_manager=self._budget_manager,
                team_dashboard=self._team_dashboard,
                static_dir=static_dir,
            )
            try:
                self._dashboard_server.start(port=port)
                dashboard_url = f"http://localhost:{port}"
                msgs.append(_("dashboard.started", url=dashboard_url))
            except Exception as exc:
                self._dashboard_server = None
                msgs.append(_("dashboard.start_failed", error=exc))
                return _CommandOutcome(True, msgs)

            # --- Step 3: open default browser ---
            try:
                webbrowser.open(dashboard_url)
                msgs.append(_("dashboard.browser_opened"))
            except Exception:
                msgs.append(_("dashboard.browser_failed", url=dashboard_url))

            msgs.append(_("dashboard.stop_hint"))
            return _CommandOutcome(True, msgs)

        if action == "stop":
            if self._dashboard_server is None or not self._dashboard_server.running:
                return _CommandOutcome(True, [_("dashboard.not_running")])
            self._dashboard_server.stop()
            self._dashboard_server = None
            return _CommandOutcome(True, [_("dashboard.stopped")])

        if action == "status":
            if self._dashboard_server is not None and self._dashboard_server.running:
                port = self._dashboard_server.port
                return _CommandOutcome(True, [_("dashboard.active", port=port)])
            return _CommandOutcome(True, [_("dashboard.inactive")])

        return _CommandOutcome(True, [
            _("dashboard.usage")
        ])

    # ------------------------------------------------------------------
    # Knowledge & Workspace Commands (Phase 5)
    # ------------------------------------------------------------------

    def _handle_knowledge_command(self, raw_text: str) -> _CommandOutcome:
        """Dispatch ``/knowledge`` subcommands."""
        parts = raw_text.strip().split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "status"

        if self._knowledge_base is None:
            return _CommandOutcome(True, [_("knowledge.inactive")])

        if action == "status":
            count = self._knowledge_base.count()
            return _CommandOutcome(True, [_("knowledge.count", count=count)])

        if action == "search" and len(parts) > 2:
            query_text = parts[2]
            results = self._knowledge_base.query(query_text, top_k=5)
            if not results:
                return _CommandOutcome(True, [_("memories.empty")])
            msgs = [_("knowledge.search_header", count=len(results))]
            for r in results:
                snippet = r.text[:120].replace("\n", " ")
                msgs.append(f"  [{r.entry_id}] (score={r.score:.3f}) {snippet}")
            return _CommandOutcome(True, msgs)

        if action == "add" and len(parts) > 2:
            text_to_add = parts[2]
            entry_id = self._knowledge_base.store(text_to_add)
            return _CommandOutcome(True, [_("knowledge.added", id=entry_id)])

        return _CommandOutcome(True, [
            _("knowledge.usage")
        ])

    def _handle_workspace_command(self, raw_text: str) -> _CommandOutcome:
        """Dispatch ``/workspace`` subcommands."""
        parts = raw_text.strip().split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "list"

        if self._shared_workspace is None:
            return _CommandOutcome(True, [_("workspace.inactive")])

        if action == "list":
            files = self._shared_workspace.list_files()
            if not files:
                return _CommandOutcome(True, [_("workspace.empty")])
            msgs = [_("workspace.files_header", count=len(files))]
            for f in files:
                author = self._shared_workspace.last_author(f) or "?"
                msgs.append(f"  {f}  (autor: {author})")
            return _CommandOutcome(True, msgs)

        if action == "read" and len(parts) > 2:
            content = self._shared_workspace.read_file(parts[2])
            if content is None:
                return _CommandOutcome(True, [_("workspace.file_not_found", name=parts[2])])
            return _CommandOutcome(True, [f"--- {parts[2]} ---", content[:2000]])

        if action == "log":
            changes = self._shared_workspace.changes()
            if not changes:
                return _CommandOutcome(True, [_("workspace.changes_empty")])
            msgs = [_("workspace.changes_header", count=len(changes))]
            for c in changes[-20:]:
                msgs.append(f"  {c.action:6s}  {c.path}  agent={c.agent_id}")
            return _CommandOutcome(True, msgs)

        return _CommandOutcome(True, [
            _("workspace.usage")
        ])

    # ------------------------------------------------------------------
    # Security & Audit Commands (Phase 7)
    # ------------------------------------------------------------------

    def _handle_audit_command(self, raw_text: str) -> _CommandOutcome:
        """Dispatch ``/audit`` subcommands."""
        parts = raw_text.strip().split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "list"

        if self._audit_chain is None:
            return _CommandOutcome(True, [_("audit.inactive")])

        if action == "list":
            entries = self._audit_chain.query(limit=20)
            if not entries:
                return _CommandOutcome(True, [_("audit.empty")])
            msgs = [_("audit.header", count=self._audit_chain.count())]
            for e in entries:
                msgs.append(
                    f"  [{e.outcome}] {e.agent_id}: {e.action} → {e.target}"
                )
            return _CommandOutcome(True, msgs)

        if action == "agent" and len(parts) > 2:
            agent_id = parts[2]
            entries = self._audit_chain.query(agent_id=agent_id, limit=20)
            if not entries:
                return _CommandOutcome(True, [_("audit.agent_empty", agent_id=agent_id)])
            msgs = [_("audit.agent_header", agent_id=agent_id)]
            for e in entries:
                msgs.append(f"  [{e.outcome}] {e.action} → {e.target}")
            return _CommandOutcome(True, msgs)

        return _CommandOutcome(True, [
            _("audit.usage")
        ])

    def _handle_sandbox_command(self, raw_text: str) -> _CommandOutcome:
        """Dispatch ``/sandbox`` subcommands."""
        parts = raw_text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "list"

        if self._sandbox_manager is None:
            return _CommandOutcome(True, [_("sandbox.inactive")])

        if action == "list":
            sandboxes = self._sandbox_manager.list_sandboxes()
            if not sandboxes:
                return _CommandOutcome(True, [_("sandbox.empty")])
            msgs = [_("sandbox.header")]
            for agent_id, path in sandboxes.items():
                size = self._sandbox_manager.sandbox_size(agent_id)
                msgs.append(f"  {agent_id}: {path}  ({size} B)")
            return _CommandOutcome(True, msgs)

        if action == "create" and len(parts) > 2:
            agent_id = parts[2]
            path = self._sandbox_manager.create(agent_id)
            return _CommandOutcome(True, [_("sandbox.created", path=path)])

        if action == "destroy" and len(parts) > 2:
            agent_id = parts[2]
            ok = self._sandbox_manager.destroy(agent_id)
            if ok:
                return _CommandOutcome(True, [_("sandbox.destroyed", agent_id=agent_id)])
            return _CommandOutcome(True, [_("sandbox.not_found", agent_id=agent_id)])

        return _CommandOutcome(True, [
            _("sandbox.usage")
        ])

    # ------------------------------------------------------------------
    # Workflow Commands (Phase 6)
    # ------------------------------------------------------------------

    def _handle_workflow_command(self, raw_text: str) -> _CommandOutcome:
        """Dispatch ``/workflow`` subcommands."""
        parts = raw_text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "list"

        if self._workflow_engine is None:
            return _CommandOutcome(True, [_("workflow.inactive")])

        if action == "list":
            runs = self._workflow_engine.list_runs()
            if not runs:
                return _CommandOutcome(True, [_("workflow.empty")])
            msgs = [_("workflow.header")]
            for r in runs:
                total = len(r.workflow.nodes)
                done = sum(1 for n in r.workflow.nodes if n.status.value in ("completed", "skipped"))
                msgs.append(f"  {r.run_id}: {r.workflow.name} [{r.status}] ({done}/{total} węzłów)")
            return _CommandOutcome(True, msgs)

        if action == "run" and len(parts) > 2:
            from amiagi.domain.workflow import WorkflowDefinition
            template_name = parts[2]
            workflows_dir = Path("./data/workflows")
            if self._settings is not None:
                workflows_dir = self._settings.workflows_dir
            template_path = workflows_dir / f"{template_name}.yaml"
            if not template_path.exists():
                template_path = workflows_dir / f"{template_name}.json"
            if not template_path.exists():
                available = sorted({p.stem for p in list(workflows_dir.glob("*.yaml")) + list(workflows_dir.glob("*.json"))})
                return _CommandOutcome(True, [
                    _("workflow.template_not_found", name=template_name),
                    _("workflow.available_templates", list=', '.join(available) or 'brak'),
                ])
            try:
                wf = WorkflowDefinition.load_file(template_path)
                run = self._workflow_engine.start(wf)
                return _CommandOutcome(True, [
                    _("workflow.started", name=wf.name, run_id=run.run_id),
                ])
            except Exception as exc:
                return _CommandOutcome(True, [_("workflow.start_error", error=exc)])

        if action == "status" and len(parts) > 2:
            run_id = parts[2]
            run = self._workflow_engine.get_run(run_id)
            if run is None:
                return _CommandOutcome(True, [_("workflow.not_found", run_id=run_id)])
            msgs = [f"--- Workflow {run.run_id}: {run.workflow.name} [{run.status}] ---"]
            for n in run.workflow.nodes:
                msgs.append(f"  {n.node_id:20s} [{n.node_type.value:12s}] → {n.status.value}")
            return _CommandOutcome(True, msgs)

        if action == "approve" and len(parts) > 3:
            run_id = parts[2]
            node_id = parts[3]
            ok = self._workflow_engine.approve_gate(run_id, node_id)
            if ok:
                return _CommandOutcome(True, [_("workflow.gate_approved", node_id=node_id, run_id=run_id)])
            return _CommandOutcome(True, [_("workflow.gate_failed", node_id=node_id)])

        if action == "pause" and len(parts) > 2:
            ok = self._workflow_engine.pause(parts[2])
            return _CommandOutcome(True, [
                _("workflow.paused", id=parts[2]) if ok else _("workflow.pause_failed", id=parts[2])
            ])

        if action == "resume" and len(parts) > 2:
            ok = self._workflow_engine.resume(parts[2])
            return _CommandOutcome(True, [
                _("workflow.resumed", id=parts[2]) if ok else _("workflow.resume_failed", id=parts[2])
            ])

        if action == "templates":
            workflows_dir = Path("./data/workflows")
            if self._settings is not None:
                workflows_dir = self._settings.workflows_dir
            templates = sorted({p.stem for p in list(workflows_dir.glob("*.yaml")) + list(workflows_dir.glob("*.json"))})
            if not templates:
                return _CommandOutcome(True, [_("workflow.no_templates")])
            msgs = [_("workflow.templates_header")]
            for t in templates:
                msgs.append(f"  {t}")
            return _CommandOutcome(True, msgs)

        return _CommandOutcome(True, [
            _("workflow.usage")
        ])

    # ------------------------------------------------------------------
    # Budget & Quota Commands (Phase 8)
    # ------------------------------------------------------------------

    def _handle_budget_command(self, raw_text: str) -> _CommandOutcome:
        parts = raw_text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "status"

        if self._budget_manager is None:
            return _CommandOutcome(True, [_("budget.inactive")])

        if action == "status":
            summary = self._budget_manager.summary()
            if not summary:
                return _CommandOutcome(True, [_("budget.no_data")])
            msgs = [_("budget.header")]
            for agent_id, info in summary.items():
                msgs.append(f"  {agent_id}: spent=${info['spent_usd']:.4f} / limit=${info['limit_usd']:.2f} ({info['utilization_pct']:.1f}%)")
            return _CommandOutcome(True, msgs)

        if action == "set" and len(parts) >= 4:
            agent_id = parts[2]
            try:
                limit = float(parts[3])
            except ValueError:
                return _CommandOutcome(True, [_("budget.set_usage")])
            self._budget_manager.set_budget(agent_id, limit)
            return _CommandOutcome(True, [_("budget.set_done", agent_id=agent_id, limit=f"{limit:.2f}")])

        if action == "reset" and len(parts) >= 3:
            self._budget_manager.reset_agent(parts[2])
            return _CommandOutcome(True, [_("budget.reset_done", agent_id=parts[2])])

        if action == "dashboard":
            msgs = ["╔══════════════════════════════════════════════════════╗"]
            msgs.append("║            COST DASHBOARD                            ║")
            msgs.append("╠══════════════════════════════════════════════════════╣")
            # Session budget
            ss = self._budget_manager.session_summary()
            sess_pct = ss["utilization_pct"]
            bar_len = 20
            filled = int(bar_len * sess_pct / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            msgs.append(f"║ SESSION  ${ss['spent_usd']:>8.4f} / ${ss['limit_usd']:>7.2f}  [{bar}] {sess_pct:5.1f}% ║")
            msgs.append(f"║          tokens={ss['tokens_used']}  requests={ss['requests_count']}")
            msgs.append("╠──────────────────────────────────────────────────────╣")
            # Per-agent budgets
            summary = self._budget_manager.summary()
            if summary:
                msgs.append("║ AGENTS                                               ║")
                for aid, info in summary.items():
                    pct = info["utilization_pct"]
                    af = int(bar_len * pct / 100)
                    abar = "█" * af + "░" * (bar_len - af)
                    status = "⚠" if pct >= 80 else ("🛑" if pct >= 100 else "✓")
                    msgs.append(f"║  {status} {aid:<14} ${info['spent_usd']:>7.4f}/${info['limit_usd']:>6.2f} [{abar}] {pct:5.1f}%")
            else:
                no_agents_msg = _("budget.no_agents")
                msgs.append(f"║ {no_agents_msg:<53}║")
            # Per-task budgets
            task_summary = self._budget_manager.task_summary()
            if task_summary:
                msgs.append("╠──────────────────────────────────────────────────────╣")
                msgs.append("║ TASKS                                                ║")
                for tid, tinfo in task_summary.items():
                    tpct = tinfo["utilization_pct"]
                    tf = int(bar_len * tpct / 100)
                    tbar = "█" * tf + "░" * (bar_len - tf)
                    msgs.append(f"║  {tid:<16} ${tinfo['spent_usd']:>7.4f}/${tinfo['limit_usd']:>6.2f} [{tbar}] {tpct:5.1f}%")
            msgs.append("╚══════════════════════════════════════════════════════╝")
            return _CommandOutcome(True, msgs)

        if action == "session":
            if len(parts) >= 3:
                sub = parts[2].lower()
                if sub == "set" and len(parts) >= 4:
                    try:
                        limit = float(parts[3])
                    except ValueError:
                        return _CommandOutcome(True, [_("budget.session_set_usage")])
                    self._budget_manager.set_session_budget(limit)
                    return _CommandOutcome(True, [_("budget.session_set_done", limit=f"{limit:.2f}")])
            ss = self._budget_manager.session_summary()
            return _CommandOutcome(True, [
                f"Sesja: spent=${ss['spent_usd']:.4f} / limit=${ss['limit_usd']:.2f} ({ss['utilization_pct']:.1f}%)",
                f"  tokens={ss['tokens_used']}, requests={ss['requests_count']}",
            ])

        if action == "task" and len(parts) >= 3:
            sub = parts[2].lower() if len(parts) > 2 else "status"
            if sub == "set" and len(parts) >= 5:
                task_id = parts[3]
                try:
                    limit = float(parts[4])
                except ValueError:
                    return _CommandOutcome(True, [_("budget.task_set_usage")])
                self._budget_manager.set_task_budget(task_id, limit)
                return _CommandOutcome(True, [_("budget.task_set_done", task_id=task_id, limit=f"{limit:.2f}")])
            # show specific task
            task_id = parts[2]
            tb = self._budget_manager.get_task_budget(task_id)
            if tb is None:
                return _CommandOutcome(True, [_("budget.task_not_found", task_id=task_id)])
            return _CommandOutcome(True, [
                f"Zadanie {task_id}: spent=${tb.spent_usd:.4f} / limit=${tb.limit_usd:.2f} ({tb.utilization_pct:.1f}%)",
            ])

        return _CommandOutcome(True, [
            _("budget.usage")
        ])

    def _handle_quota_command(self, raw_text: str) -> _CommandOutcome:
        if self._quota_policy is None:
            return _CommandOutcome(True, [_("quota.inactive")])

        parts = raw_text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "status"

        if action == "set" and len(parts) >= 5:
            role = parts[2]
            try:
                tokens = int(parts[3])
                cost = float(parts[4])
                req_h = int(parts[5]) if len(parts) > 5 else 0
            except (ValueError, IndexError):
                return _CommandOutcome(True, [_("quota.set_usage")])
            from amiagi.domain.quota_policy import RoleQuota
            self._quota_policy.set_role(role, RoleQuota(
                daily_token_limit=tokens,
                daily_cost_limit_usd=cost,
                max_requests_per_hour=req_h,
            ))
            return _CommandOutcome(True, [_("quota.set_done", role=role, tokens=tokens, cost=f"{cost:.2f}", req_h=req_h)])

        # Default: status
        roles = self._quota_policy.list_roles()
        if not roles:
            return _CommandOutcome(True, [_("quota.empty")])
        msgs = [_("quota.header")]
        for role in roles:
            q = self._quota_policy.get_role(role)
            if q:
                msgs.append(f"  {role}: tokens={q.daily_token_limit}, cost=${q.daily_cost_limit_usd:.2f}, req/h={q.max_requests_per_hour}")
        return _CommandOutcome(True, msgs)

    # ------------------------------------------------------------------
    # Evaluation & Feedback Commands (Phase 9)
    # ------------------------------------------------------------------

    def _handle_eval_command(self, raw_text: str) -> _CommandOutcome:
        parts = raw_text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "help"

        if action == "run":
            if self._eval_runner is None:
                return _CommandOutcome(True, [_("eval.inactive")])
            agent_id = parts[2] if len(parts) > 2 else None
            if not agent_id:
                return _CommandOutcome(True, [_("eval.run_usage")])
            # Check for --benchmark flag
            benchmark_name = None
            if "--benchmark" in parts:
                idx = parts.index("--benchmark")
                if idx + 1 < len(parts):
                    benchmark_name = parts[idx + 1]
            # Build scenarios
            scenarios: list = []
            if benchmark_name and self._benchmark_suite is not None:
                scenarios = self._benchmark_suite.get_scenarios(benchmark_name)
                if not scenarios:
                    return _CommandOutcome(True, [_("eval.benchmark_empty", name=benchmark_name)])
            if not scenarios:
                # Use a trivial default scenario
                from amiagi.application.eval_runner import EvalScenario
                scenarios = [EvalScenario(
                    scenario_id="default_1",
                    prompt="Opisz swoją rolę i możliwości.",
                    expected_keywords=["agent", "pomoc"],
                    category="general",
                )]
            # Create an agent callable — use real ChatService when possible
            if self._chat_service is not None:
                _cs = self._chat_service
                def _agent_fn(prompt: str) -> str:
                    try:
                        return _cs.ask(prompt, actor="EvalRunner")
                    except Exception as exc:  # noqa: BLE001
                        return f"[error] {exc}"
            else:
                def _agent_fn(prompt: str) -> str:
                    return f"[eval-stub] Agent {agent_id} response to: {prompt[:100]}"
            result = self._eval_runner.run(agent_id, _agent_fn, scenarios)
            msgs = [
                _("eval.run_header", agent_id=agent_id),
                f"  Scenarios: {result.scenarios_count}",
                f"  Passed: {result.passed}, Failed: {result.failed}",
                f"  Aggregate score: {result.aggregate_score:.1f}",
            ]
            return _CommandOutcome(True, msgs)

        if action == "compare":
            if self._ab_test_runner is None:
                return _CommandOutcome(True, [_("eval.compare_inactive")])
            if len(parts) < 4:
                return _CommandOutcome(True, [_("eval.compare_usage")])
            agent_a = parts[2]
            agent_b = parts[3]
            from amiagi.application.eval_runner import EvalScenario
            scenarios = [EvalScenario(
                scenario_id=f"cmp_{i}",
                prompt=p,
                expected_keywords=["agent"],
                category="comparison",
            ) for i, p in enumerate([
                "Jaka jest Twoja główna rola?",
                "Podaj przykład zadania, które możesz wykonać.",
                "Opisz swoje narzędzia.",
            ], 1)]
            def _fn_a(prompt: str) -> str:
                return f"[{agent_a}] response"
            def _fn_b(prompt: str) -> str:
                return f"[{agent_b}] response"
            result = self._ab_test_runner.compare(agent_a, _fn_a, agent_b, _fn_b, scenarios)
            msgs = [
                _("eval.compare_header", a=agent_a, b=agent_b),
                f"  A wins: {result.a_wins}, B wins: {result.b_wins}, Ties: {result.ties}",
                f"  Score delta: {result.score_delta:+.2f}",
            ]
            return _CommandOutcome(True, msgs)

        if action == "history":
            if self._eval_runner is None:
                return _CommandOutcome(True, [_("eval.inactive")])
            agent_id = parts[2] if len(parts) > 2 else None
            history = self._eval_runner.history(agent_id)
            if not history:
                return _CommandOutcome(True, [_("eval.history_empty")])
            msgs = [_("eval.history_header")]
            for r in history[-10:]:
                msgs.append(f"  {r.agent_id}: passed={r.passed}/{r.passed + r.failed} score={r.aggregate_score:.1f}")
            return _CommandOutcome(True, msgs)

        if action == "baselines":
            if self._regression_detector is None:
                return _CommandOutcome(True, [_("eval.regression_inactive")])
            baselines = self._regression_detector.list_baselines()
            if not baselines:
                return _CommandOutcome(True, [_("eval.baselines_empty")])
            msgs = [_("eval.baselines_header")]
            for b in baselines:
                msgs.append(f"  {b}")
            return _CommandOutcome(True, msgs)

        return _CommandOutcome(True, [
            _("eval.usage"),
        ])

    def _handle_feedback_command(self, raw_text: str) -> _CommandOutcome:
        if self._human_feedback is None:
            return _CommandOutcome(True, [_("feedback.inactive")])

        parts = raw_text.strip().split(maxsplit=3)
        action = parts[1].lower() if len(parts) > 1 else "summary"

        if action == "summary":
            s = self._human_feedback.summary()
            if not s:
                return _CommandOutcome(True, [_("feedback.empty")])
            msgs = [_("feedback.header")]
            for agent_id, info in s.items():
                msgs.append(f"  {agent_id}: +{info['positive']} / -{info['negative']} (total={info['total']})")
            return _CommandOutcome(True, msgs)

        if action in {"up", "down"} and len(parts) >= 3:
            agent_id = parts[2]
            comment = parts[3] if len(parts) > 3 else ""
            if action == "up":
                self._human_feedback.thumbs_up(agent_id, comment=comment)
            else:
                self._human_feedback.thumbs_down(agent_id, comment=comment)
            return _CommandOutcome(True, [_("feedback.recorded", action=action, agent_id=agent_id)])

        return _CommandOutcome(True, [
            _("feedback.usage"),
        ])

    # ------------------------------------------------------------------
    # API & Plugins Commands (Phase 10)
    # ------------------------------------------------------------------

    def _handle_api_command(self, raw_text: str) -> _CommandOutcome:
        parts = raw_text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "status"

        if self._rest_server is None:
            return _CommandOutcome(True, [_("api.inactive")])

        if action == "status":
            d = self._rest_server.to_dict()
            msgs = [_("api.status_header")]
            msgs.append(f"  running: {d['is_running']}")
            msgs.append(f"  address: {self._rest_server.address}")
            msgs.append(f"  routes: {len(d['routes'])}")
            return _CommandOutcome(True, msgs)

        if action == "start":
            self._rest_server.start()
            return _CommandOutcome(True, [_("api.started", address=self._rest_server.address)])

        if action == "stop":
            self._rest_server.stop()
            return _CommandOutcome(True, [_("api.stopped")])

        return _CommandOutcome(True, [_("api.usage")])

    def _handle_plugins_command(self, raw_text: str) -> _CommandOutcome:
        if self._plugin_loader is None:
            return _CommandOutcome(True, [_("plugins.inactive")])

        parts = raw_text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "list"

        if action == "list":
            plugins = self._plugin_loader.list_plugins()
            if not plugins:
                return _CommandOutcome(True, [_("plugins.empty")])
            msgs = [_("plugins.header")]
            for p in plugins:
                status = "✓" if p.loaded else "✗"
                msgs.append(f"  {status} {p.name} v{p.version or '?'}: {p.description[:60] if p.description else '-'}")
            return _CommandOutcome(True, msgs)

        if action == "load":
            results = self._plugin_loader.load_all()
            loaded = sum(1 for r in results if r.loaded)
            return _CommandOutcome(True, [_("plugins.loaded", loaded=loaded, total=len(results))])

        if action == "install" and len(parts) > 2:
            plugin_path = parts[2]
            import shutil
            from pathlib import Path as _Path
            src = _Path(plugin_path)
            if not src.exists():
                return _CommandOutcome(True, [_("plugins.src_missing", path=plugin_path)])
            plugins_dir = _Path("plugins")
            plugins_dir.mkdir(exist_ok=True)
            dest = plugins_dir / src.name
            shutil.copy2(src, dest)
            return _CommandOutcome(True, [_("plugins.installed", name=src.name)])

        return _CommandOutcome(True, [_("plugins.usage")])

    # ------------------------------------------------------------------
    # Team Commands (Phase 11)
    # ------------------------------------------------------------------

    def _handle_team_command(self, raw_text: str) -> _CommandOutcome:
        parts = raw_text.strip().split()
        action = parts[1].lower() if len(parts) > 1 else "list"

        if action == "list":
            if self._team_dashboard is None:
                return _CommandOutcome(True, [_("team.dashboard_inactive")])
            teams = self._team_dashboard.list_teams()
            if not teams:
                return _CommandOutcome(True, [_("team.empty")])
            msgs = [_("team.header")]
            for t in teams:
                msgs.append(f"  {t.team_id}: {t.name} ({t.size} członków)")
            return _CommandOutcome(True, msgs)

        if action == "templates":
            if self._team_composer is None:
                return _CommandOutcome(True, [_("team.composer_inactive")])
            templates = self._team_composer.list_templates()
            if not templates:
                return _CommandOutcome(True, [_("team.no_templates")])
            msgs = [_("team.templates_header")]
            for t in templates:
                msgs.append(f"  {t}")
            return _CommandOutcome(True, msgs)

        if action == "create" and len(parts) > 2:
            template_id = parts[2]
            if self._team_composer is None:
                return _CommandOutcome(True, [_("team.composer_inactive")])
            team = self._team_composer.from_template(template_id)
            if team is None:
                return _CommandOutcome(True, [_("team.template_not_found", id=template_id)])
            if self._team_dashboard is not None:
                self._team_dashboard.register_team(team)
            return _CommandOutcome(True, [_("team.created", name=team.name, template=template_id, size=team.size)])

        if action == "status" and len(parts) > 2:
            team_id = parts[2]
            if self._team_dashboard is None:
                return _CommandOutcome(True, [_("team.dashboard_inactive")])
            chart = self._team_dashboard.org_chart(team_id)
            if "error" in chart:
                return _CommandOutcome(True, [_("team.not_found", id=team_id)])
            msgs = [f"--- TEAM {chart['name']} ---", f"  Lead: {chart['lead']}"]
            for m in chart["members"]:
                lead_marker = " (lead)" if m["is_lead"] else ""
                msgs.append(f"  {m['role']}: {m['name']}{lead_marker}")
            return _CommandOutcome(True, msgs)

        if action == "compose" and len(parts) > 2:
            goal = " ".join(parts[2:])
            if self._team_composer is None:
                return _CommandOutcome(True, [_("team.composer_inactive")])
            team = self._team_composer.build_team(goal)
            if self._team_dashboard is not None:
                self._team_dashboard.register_team(team)
            msgs = [
                _("team.composed_header"),
                f"  Nazwa: {team.name}",
                f"  Członkowie ({team.size}):",
            ]
            for m in team.members:
                msgs.append(f"    {m.role}: {m.name}")
            return _CommandOutcome(True, msgs)

        if action == "scale" and len(parts) > 3:
            team_id = parts[2]
            direction = parts[3].lower()
            if direction not in ("up", "down", "+1", "-1"):
                return _CommandOutcome(True, [_("team.scale_usage")])
            if self._dynamic_scaler is None:
                return _CommandOutcome(True, [_("team.scaler_inactive")])
            scale_dir = "up" if direction in ("up", "+1") else "down"
            from amiagi.application.dynamic_scaler import ScaleEvent
            event = ScaleEvent(
                direction=scale_dir,
                team_id=team_id,
                reason=f"Manual scale {scale_dir} via TUI",
            )
            self._dynamic_scaler._events.append(event)
            return _CommandOutcome(True, [_("team.scaled", id=team_id, direction=scale_dir)])

        return _CommandOutcome(True, [
            _("team.usage"),
        ])

    # ------------------------------------------------------------------
    # Model Selection Wizard
    # ------------------------------------------------------------------

    def _build_wizard_model_list(self) -> list[tuple[str, str]]:
        """Return a combined list of (model_name, source) for the wizard."""
        entries: list[tuple[str, str]] = []
        # Ollama local models
        models, error = _fetch_ollama_models(self._chat_service)
        if error is None and models:
            for name in models:
                entries.append((name, "ollama"))
        # OpenAI API models
        for name in SUPPORTED_OPENAI_MODELS:
            entries.append((name, "openai"))
        return entries

    def _format_wizard_model_list(
        self, models: list[tuple[str, str]], *, default_name: str = ""
    ) -> str:
        lines: list[str] = []
        ollama_models = [(n, s) for n, s in models if s == "ollama"]
        api_models = [(n, s) for n, s in models if s != "ollama"]
        idx = 1
        if ollama_models:
            lines.append(_("wizard.model_list_local"))
            for name, _size in ollama_models:
                marker = _("wizard.default_marker") if name == default_name else ""
                lines.append(f"    {idx}. {name}{marker}")
                idx += 1
        if api_models:
            lines.append(_("wizard.model_list_api"))
            for name, source in api_models:
                marker = _("wizard.default_marker") if name == default_name else ""
                lines.append(f"    {idx}. ☁ {name}  [{source.upper()}]{marker}")
                idx += 1
        return "\n".join(lines)

    def _start_model_selection_wizard(self) -> None:
        """Begin the interactive model selection wizard on mount.

        If a saved model config exists, auto-restore it and skip the wizard.
        """
        # --- Try to restore from previous session ---
        saved = SessionModelConfig.load(self._model_config_path)
        if saved and saved.polluks_model:
            available = self._build_wizard_model_list()
            available_names = {n for n, _s in available}
            polluks_ok = saved.polluks_model in available_names or saved.polluks_source != "ollama"
            kastor_ok = (
                not saved.kastor_model
                or saved.kastor_model in available_names
                or saved.kastor_source != "ollama"
            )
            if polluks_ok:
                self._wizard_polluks_choice = (saved.polluks_model, saved.polluks_source)
                self._wizard_kastor_models = available
                self._wizard_models = available
                self._wizard_finalize(saved.kastor_model if kastor_ok else "", saved.kastor_source if kastor_ok else "ollama")
                self._append_log(
                    "user_model_log",
                    _("wizard.restored"),
                )
                return

        self._wizard_models = self._build_wizard_model_list()
        if not self._wizard_models:
            self._append_log(
                "user_model_log",
                _("wizard.no_models"),
            )
            self._model_configured = True  # Unblock input
            return

        self._wizard_phase = 1
        self._wizard_show_polluks_prompt()

    def _wizard_show_polluks_prompt(self) -> None:
        """Display (or re-display) the Polluks model selection prompt."""
        header = _("wizard.polluks_header")
        footer = "╰───────────────────────────────────────────────────────────╯ \n"
        body = self._format_wizard_model_list(self._wizard_models)
        b1 = _("wizard.polluks_body1")
        b2 = _("wizard.polluks_body2")
        b3 = _("wizard.polluks_body3")
        b4 = _("wizard.polluks_body4")
        hint = _("wizard.polluks_hint")
        self._append_log(
            "user_model_log",
            f"\n{header}\n"
            f"{b1}\n"
            f"{b2}\n\n"
            f"{b3}\n\n"
            f"{body}\n\n"
            f"{b4}\n"
            f"{hint}\n"
            f"{footer}",
        )

    def _wizard_show_kastor_prompt(self, default_name: str = "") -> None:
        """Display (or re-display) the Kastor model selection prompt."""
        header = _("wizard.kastor_header")
        footer = "╰───────────────────────────────────────────────────────────╯ \n"
        body = self._format_wizard_model_list(
            self._wizard_kastor_models, default_name=default_name
        )
        kb1 = _("wizard.kastor_body1")
        kb2 = _("wizard.kastor_body2")
        kb3 = _("wizard.kastor_body3")
        hint = _("wizard.polluks_hint")
        self._append_log(
            "user_model_log",
            f"\n{header}\n"
            f"{kb1}\n\n"
            f"{kb2}\n\n"
            f"{body}\n\n"
            f"{kb3}\n"
            f"{hint}\n"
            f"{footer}",
        )

    def _wizard_redisplay_prompt(self) -> None:
        """Re-show the current wizard step after a / command."""
        if self._wizard_phase == 1:
            self._wizard_show_polluks_prompt()
        elif self._wizard_phase == 2:
            default_kastor = self._wizard_get_default_kastor()
            self._wizard_show_kastor_prompt(default_kastor)

    def _wizard_get_default_kastor(self) -> str:
        """Return the default Kastor model name for the wizard prompt."""
        default_kastor = ""
        settings = self._settings
        if settings is not None:
            default_kastor = settings.supervisor_model or ""
        if not default_kastor and self._wizard_kastor_models:
            default_kastor = self._wizard_kastor_models[0][0]
        return default_kastor

    def _wizard_handle_input(self, text: str) -> bool:
        """Process wizard-phase input. Return True if consumed."""
        if self._wizard_phase == 0:
            return False

        if self._wizard_phase == 1:
            return self._wizard_handle_polluks_choice(text)
        if self._wizard_phase == 2:
            return self._wizard_handle_kastor_choice(text)
        return False

    def _wizard_handle_polluks_choice(self, text: str) -> bool:
        """Phase 1: user picks the executor (Polluks) model."""
        try:
            idx = int(text.strip())
        except ValueError:
            total = len(self._wizard_models)
            self._append_log(
                "user_model_log",
                _("wizard.polluks_expect_number", total=total),
            )
            return True

        if idx < 1 or idx > len(self._wizard_models):
            self._append_log(
                "user_model_log",
                _("wizard.invalid_range", max=len(self._wizard_models)),
            )
            return True

        name, source = self._wizard_models[idx - 1]
        self._wizard_polluks_choice = (name, source)
        self._append_log("user_model_log", f"  → Polluks: {name} ({source})")

        # Move to phase 2: Kastor model
        self._wizard_phase = 2
        self._wizard_kastor_models = self._build_wizard_model_list()
        default_kastor = self._wizard_get_default_kastor()
        self._wizard_show_kastor_prompt(default_kastor)
        return True

    def _wizard_handle_kastor_choice(self, text: str) -> bool:
        """Phase 2: user picks the supervisor (Kastor) model or presses Enter for default."""
        stripped = text.strip()

        # Default selection (empty input)
        if stripped == "":
            kastor_name = ""
            kastor_source = "ollama"
            settings = self._settings
            if settings is not None and settings.supervisor_model:
                kastor_name = settings.supervisor_model
                kastor_source = "ollama"
            elif self._wizard_kastor_models:
                kastor_name = self._wizard_kastor_models[0][0]
                kastor_source = self._wizard_kastor_models[0][1]
        else:
            try:
                idx = int(stripped)
            except ValueError:
                total = len(self._wizard_kastor_models)
                self._append_log(
                    "user_model_log",
                    _("wizard.kastor_expect_number", total=total),
                )
                return True
            if idx < 1 or idx > len(self._wizard_kastor_models):
                self._append_log(
                    "user_model_log",
                    _("wizard.invalid_range", max=len(self._wizard_kastor_models)),
                )
                return True
            kastor_name, kastor_source = self._wizard_kastor_models[idx - 1]

        self._append_log("user_model_log", f"  → Kastor: {kastor_name} ({kastor_source})")
        self._wizard_finalize(kastor_name, kastor_source)
        return True

    def _wizard_finalize(self, kastor_name: str, kastor_source: str) -> None:
        """Apply wizard selections and unblock the UI."""
        polluks_name, polluks_source = self._wizard_polluks_choice
        errors: list[str] = []

        # --- Apply Polluks model ---
        if polluks_source == "ollama":
            ok, _prev = _set_executor_model(self._chat_service, polluks_name)
            if not ok:
                errors.append(_("wizard.finalize_polluks_fail", name=polluks_name))
        else:
            # OpenAI model for Polluks
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                settings = self._settings
                if settings is not None:
                    api_key = settings.openai_api_key
            if not api_key:
                errors.append(
                    _("wizard.finalize_no_api_key")
                )
                # Fallback to first local model
                local = [n for n, s in self._wizard_models if s == "ollama"]
                if local:
                    _set_executor_model(self._chat_service, local[0])
                    polluks_name = local[0]
                    polluks_source = "ollama"
            else:
                base_url = "https://api.openai.com/v1"
                timeout = 120
                settings = self._settings
                if settings is not None:
                    base_url = settings.openai_base_url or base_url
                    timeout = settings.openai_request_timeout_seconds or timeout

                openai_client = OpenAIClient(
                    api_key=api_key,
                    model=polluks_name,
                    base_url=base_url,
                    io_logger=getattr(self._chat_service.ollama_client, "io_logger", None),
                    activity_logger=self._activity_logger,
                    client_role="executor",
                    request_timeout_seconds=timeout,
                    usage_tracker=self._usage_tracker,
                )
                # Validate API key
                try:
                    reachable = openai_client.ping()
                except Exception:
                    reachable = False

                if reachable:
                    self._chat_service.ollama_client = openai_client
                    self._append_log(
                        "user_model_log",
                        _("wizard.finalize_api_verified", masked=mask_api_key(api_key)),
                    )
                else:
                    errors.append(
                        _("wizard.finalize_api_fail", masked=mask_api_key(api_key))
                    )
                    local = [n for n, s in self._wizard_models if s == "ollama"]
                    if local:
                        _set_executor_model(self._chat_service, local[0])
                        polluks_name = local[0]
                        polluks_source = "ollama"

        # --- Apply Kastor model ---
        supervisor = self._chat_service.supervisor_service
        if supervisor is not None and kastor_name:
            if kastor_source == "ollama":
                try:
                    supervisor.ollama_client = replace(
                        cast(Any, supervisor.ollama_client), model=kastor_name
                    )
                except Exception:
                    try:
                        supervisor.ollama_client.model = kastor_name  # type: ignore[attr-defined]
                    except Exception:
                        errors.append(_("wizard.finalize_kastor_fail", name=kastor_name))
            else:
                # OpenAI for Kastor
                api_key = os.environ.get("OPENAI_API_KEY", "")
                settings = self._settings
                if not api_key and settings is not None:
                    api_key = settings.openai_api_key
                if api_key:
                    base_url = "https://api.openai.com/v1"
                    timeout = 120
                    if settings is not None:
                        base_url = settings.openai_base_url or base_url
                        timeout = settings.openai_request_timeout_seconds or timeout

                    kastor_openai = OpenAIClient(
                        api_key=api_key,
                        model=kastor_name,
                        base_url=base_url,
                        io_logger=getattr(supervisor.ollama_client, "io_logger", None),
                        activity_logger=self._activity_logger,
                        client_role="supervisor",
                        request_timeout_seconds=timeout,
                        usage_tracker=self._usage_tracker,
                    )
                    supervisor.ollama_client = cast(Any, kastor_openai)
                else:
                    errors.append(
                        _("wizard.finalize_kastor_no_key")
                    )

        # --- Sync AgentDescriptor in registry ---
        self._sync_agent_model("polluks", polluks_name, polluks_source)
        if kastor_name:
            self._sync_agent_model("kastor", kastor_name, kastor_source)

        # --- Show errors ---
        for msg in errors:
            self._append_log("user_model_log", msg)

        # --- Configuration summary ---
        polluks_label = polluks_name
        if polluks_source != "ollama":
            polluks_label = f"☁ {polluks_name} [{polluks_source.upper()}]"
        kastor_label = kastor_name or _("wizard.finalize_kastor_disabled")
        if kastor_source != "ollama" and kastor_name:
            kastor_label = f"☁ {kastor_name} [{kastor_source.upper()}]"

        summary_hdr = _("wizard.finalize_summary_header")
        ready_msg = _("wizard.finalize_ready")
        summary = (
            f"\n{summary_hdr}\n"
            f"  Polluks: {polluks_label}\n"
            f"  Kastor:  {kastor_label}\n"
            "╰──────────────────────────────────────────────────────────╯\n"
            f"\n{ready_msg}"
        )
        self._append_log("user_model_log", summary)

        # --- Activate API usage bar if API model ---
        if polluks_source != "ollama" or (kastor_source != "ollama" and kastor_name):
            self._show_api_usage_bar()

        # --- Persist model assignment for next session ---
        SessionModelConfig(
            polluks_model=polluks_name,
            polluks_source=polluks_source,
            kastor_model=kastor_name,
            kastor_source=kastor_source,
        ).save(self._model_config_path)

        # --- Unblock ---
        self._wizard_phase = 0
        self._model_configured = True
        self._log_activity(
            action="wizard.completed",
            intent="Użytkownik wybrał modele w wizardzie startowym.",
            details={
                "polluks_model": polluks_name,
                "polluks_source": polluks_source,
                "kastor_model": kastor_name,
                "kastor_source": kastor_source,
            },
        )

    def _sync_agent_model(
        self, agent_id: str, model_name: str, source: str = "ollama"
    ) -> None:
        """Update the agent descriptor in the registry so the dashboard shows the correct model."""
        if self._agent_registry is None:
            return
        try:
            self._agent_registry.update_model(
                agent_id, model_name=model_name, model_backend=source
            )
        except KeyError:
            pass  # agent not registered — nothing to sync

    def _persist_model_config(self) -> None:
        """Snapshot current model assignments and save to disk."""
        polluks_model = str(getattr(self._chat_service.ollama_client, "model", ""))
        polluks_api = getattr(self._chat_service.ollama_client, "_is_api_client", False)
        polluks_source = "openai" if polluks_api else "ollama"

        kastor_model = ""
        kastor_source = "ollama"
        supervisor = self._chat_service.supervisor_service
        if supervisor is not None:
            kastor_model = str(getattr(supervisor.ollama_client, "model", ""))
            kastor_api = getattr(supervisor.ollama_client, "_is_api_client", False)
            kastor_source = "openai" if kastor_api else "ollama"

        SessionModelConfig(
            polluks_model=polluks_model,
            polluks_source=polluks_source,
            kastor_model=kastor_model,
            kastor_source=kastor_source,
        ).save(self._model_config_path)

    def _show_api_usage_bar(self) -> None:
        """Make the API usage status bar visible and start refresh timer."""
        try:
            bar = self.query_one("#api_usage_bar", Static)
            bar.styles.display = "block"
            self.set_interval(2.0, self._refresh_api_usage_bar)
        except Exception:
            pass

    def _refresh_api_usage_bar(self) -> None:
        """Update the API usage status bar with current token/cost info."""
        try:
            bar = self.query_one("#api_usage_bar", Static)
        except Exception:
            return
        line = self._usage_tracker.format_status_line()
        if line:
            bar.update(line)

    def on_mount(self) -> None:
        self._set_actor_state("router", "ACTIVE", "Inicjalizacja panelu statusu")

        # --- Landing page ---
        banner = _build_landing_banner(mode="textual")
        self._append_log("user_model_log", banner)

        # --- Start model selection wizard ---
        self._start_model_selection_wizard()

        self._append_log("executor_log", _("mount.executor_waiting"))
        if self._chat_service.supervisor_service is None:
            self._append_log(
                "supervisor_log",
                _("mount.kastor_inactive"),
            )
            self._supervisor_notice_shown = True
        else:
            self._append_log("supervisor_log", _("mount.kastor_waiting"))
        self.set_focus(self.query_one("#input_box", Input))
        self.set_interval(SUPERVISION_POLL_INTERVAL_SECONDS, self._poll_supervision_dialogue)
        self.set_interval(SUPERVISOR_WATCHDOG_INTERVAL_SECONDS, self._run_supervisor_idle_watchdog)
        self.set_interval(1.0, self._refresh_router_runtime_state)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""

        # --- Record in history (all non-empty inputs) ---
        if text:
            self._input_history.add(text)

        # --- Wizard input intercept ---
        if self._wizard_phase > 0 and not text.startswith("/"):
            # Phase 2 (Kastor) allows empty input for default
            if self._wizard_phase == 2 and text == "":
                self._wizard_handle_input("")
                return
            if not text:
                return
            if self._wizard_handle_input(text):
                return

        if not text:
            return

        # --- During wizard, handle / commands and then re-show the prompt ---
        _wizard_active = self._wizard_phase > 0

        if self._watchdog_suspended_until_user_input:
            self._watchdog_suspended_until_user_input = False
            self._watchdog_attempts = 0
            self._watchdog_capped_notified = False
            self._last_watchdog_cap_autonudge_monotonic = 0.0
            self._append_log(
                "supervisor_log",
                _("mount.watchdog_reactivated"),
            )

        command_outcome = _handle_textual_command(text, self._permission_manager)
        if command_outcome.handled:
            for message in command_outcome.messages:
                self._append_log("user_model_log", message)
            if command_outcome.should_exit:
                self.exit()
                return
            if _wizard_active:
                self._wizard_redisplay_prompt()
            return

        cli_like_outcome = self._handle_cli_like_commands(text)
        if cli_like_outcome.handled:
            for message in cli_like_outcome.messages:
                self._append_log("user_model_log", message)
            if cli_like_outcome.should_exit:
                self.exit()
                return
            if _wizard_active:
                self._wizard_redisplay_prompt()
            return

        if self._pending_user_decision:
            decision = self._extract_pause_decision(text)
            if decision == "continue":
                self._record_collaboration_signal("cooperate", {"decision": "continue"})
                self._append_log("user_model_log", _("user_turn.plan_continue"))
                self._last_progress_monotonic = time.monotonic()
                self._auto_resume_paused_plan_if_needed(time.monotonic(), force=True)
                return
            if decision == "stop":
                self._pending_user_decision = False
                self._pending_decision_identity_query = False
                self._set_plan_paused(paused=False, reason="user_stop", source="user")
                self._record_collaboration_signal("user_stopped_plan", {"decision": "stop"})
                self._append_log("user_model_log", _("user_turn.plan_stopped"))
                return
            if decision == "new_task":
                self._pending_user_decision = False
                self._pending_decision_identity_query = False
                self._set_plan_paused(paused=False, reason="new_task", source="user")
                new_goal = text.split(":", 1)[1].strip() if ":" in text else "Nowe zadanie od użytkownika"
                new_plan = {
                    "goal": new_goal[:200] or "Nowe zadanie od użytkownika",
                    "key_achievement": "Nowy plan utworzony po przerwaniu poprzedniego.",
                    "current_stage": "inicjalizacja_nowego_zadania",
                    "tasks": [
                        {
                            "id": "N1",
                            "title": "Doprecyzować nowe zadanie",
                            "status": "rozpoczęta",
                            "next_step": "Uruchomić pierwszy krok narzędziowy dla nowego zadania.",
                        }
                    ],
                }
                plan_path = self._work_dir / "notes" / "main_plan.json"
                try:
                    plan_path.parent.mkdir(parents=True, exist_ok=True)
                    plan_path.write_text(json.dumps(new_plan, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                self._record_collaboration_signal("new_plan_created", {"goal": new_goal[:200]})
                self._append_log("user_model_log", _("user_turn.new_plan_created"))
                return

        # --- Immediate echo + queue-based dispatch ---
        self._append_log("user_model_log", f"[Sponsor -> all] Użytkownik: {text}")

        if self._router_cycle_in_progress:
            self._user_message_queue.append(text)
            queue_pos = len(self._user_message_queue)
            self._append_log(
                "user_model_log",
                f"Wiadomość zakolejkowana (pozycja {queue_pos}). Router obsłuży ją po zakończeniu bieżącego kroku.",
            )
            self._set_actor_state("terminal", "QUEUED", f"Zakolejkowano wiadomość ({queue_pos} w kolejce)")
            return

        self._dispatch_user_turn(text)

    def _dispatch_user_turn(self, text: str) -> None:
        """Start processing a user turn — background thread if available, else sync."""
        if self._background_user_turn_enabled and bool(getattr(self, "is_running", False)):
            worker = threading.Thread(
                target=self._process_user_turn,
                args=(text,),
                daemon=True,
                name="amiagi-textual-user-turn",
            )
            worker.start()
        else:
            self._process_user_turn(text)

    def _drain_user_queue(self) -> None:
        """Process the next queued user message if any."""
        if not self._user_message_queue:
            return
        if self._router_cycle_in_progress:
            return
        next_text = self._user_message_queue.popleft()
        remaining = len(self._user_message_queue)
        if remaining:
            self._set_actor_state("terminal", "QUEUED", f"Pozostało {remaining} w kolejce")
        self._dispatch_user_turn(next_text)

    def _process_user_turn(self, text: str) -> None:

        self._set_actor_state("terminal", "INPUT_READY", "Wysłano wiadomość do routera")

        allowed, network_resource = _is_model_access_allowed(
            self._permission_manager,
            self._chat_service.ollama_client.base_url,
        )
        if not allowed:
            network_label = "sieci lokalnej" if network_resource == "network.local" else "internetu"
            self._append_log(
                "user_model_log",
                (
                    "Odmowa: brak aktywnej zgody na dostęp do "
                    f"{network_label}. Użyj /permissions all, aby odblokować zapytania modelu."
                ),
            )
            self._set_actor_state("terminal", "WAITING_INPUT", "Odmowa dostępu — oczekiwanie na wiadomość")
            return

        if text.lower() in {"/quit", "/exit"}:
            self.exit()
            return

        self._log_activity(
            action="user.input",
            intent="Wiadomość użytkownika przekazana do pętli Textual.",
            details={"chars": len(text)},
        )
        self._router_cycle_in_progress = True
        self._set_actor_state("terminal", "BUSY", "Terminal przekazał wiadomość")
        self._set_actor_state("router", "ROUTING", "Router przekazuje polecenie do Polluksa")
        self._set_actor_state("creator", "THINKING", "Polluks analizuje polecenie")
        self._last_user_message = text
        self._watchdog_attempts = 0
        self._watchdog_capped_notified = False
        self._watchdog_suspended_until_user_input = False
        interrupt_mode = self._is_conversational_interrupt(text)
        identity_query = self._is_identity_query(text)
        if interrupt_mode:
            self._set_plan_paused(paused=True, reason="user_interrupt", source="on_input_submitted")
            self._pending_user_decision = True
            self._pending_decision_identity_query = identity_query
            self._set_actor_state("router", "INTERRUPTED", "Wykryto pytanie wtrącające użytkownika")
            self._record_collaboration_signal("interrupt_enter", {"message": text[:200]})
        try:
            answer = self._ask_executor_with_router_mailbox(text)
            self._set_actor_state("creator", "ANSWER_READY", "Polluks wygenerował odpowiedź")
            if self._chat_service.supervisor_service is not None:
                self._set_actor_state("supervisor", "REVIEWING", "Kastor analizuje odpowiedź Polluksa")
                passive_turns_after_current = self._passive_turns + (0 if _has_supported_tool_call(answer) else 1)
                should_remind_continuation = (passive_turns_after_current >= 2) and not interrupt_mode
                supervision_context = {
                    "passive_turns": passive_turns_after_current,
                    "should_remind_continuation": should_remind_continuation,
                    "gpu_busy_over_50": False,
                    "plan_persistence": {"required": False},
                    "interrupt_mode": interrupt_mode,
                    "identity_query": identity_query,
                }
                supervision_user_message = (
                    "[RUNTIME_SUPERVISION_CONTEXT]\n"
                    + json.dumps(supervision_context, ensure_ascii=False)
                    + "\n[/RUNTIME_SUPERVISION_CONTEXT]\n"
                    + text
                )

                recent_msgs = self._chat_service.memory_repository.recent_messages(limit=6)
                conv_excerpt = format_conversation_excerpt(recent_msgs, limit=6)

                supervision_result = self._chat_service.supervisor_service.refine(
                    user_message=supervision_user_message,
                    model_answer=answer,
                    stage="user_turn",
                    conversation_excerpt=conv_excerpt,
                )
                answer = supervision_result.answer
                self._enqueue_supervisor_message(
                    stage="user_turn",
                    reason_code=supervision_result.reason_code,
                    notes=self._merge_supervisor_notes(
                        "Ocena odpowiedzi Polluksa w turze użytkownika.",
                        supervision_result.notes,
                    ),
                    answer=answer,
                )
                self._set_actor_state("supervisor", "READY", "Kastor zakończył analizę")

                # React to supervisor work_state indicating user decision needed
                if supervision_result.work_state == "WAITING_USER_DECISION" and not interrupt_mode:
                    self._set_plan_paused(paused=True, reason="supervisor_awaits_user", source="user_turn_supervision")
                    self._pending_user_decision = True
                    self._pending_decision_identity_query = False
                    self._watchdog_suspended_until_user_input = True
                    self._set_actor_state("router", "PAUSED", "Kastor zgłosił WAITING_USER_DECISION")
                    self._record_collaboration_signal("supervisor_awaits_user", {"work_state": supervision_result.work_state})

                if interrupt_mode:
                    if identity_query:
                        self._record_collaboration_signal(
                            "cooperate",
                            {"phase": "interrupt_user_turn", "reason": "identity_query"},
                        )
                        answer = self._identity_reply()
                    elif _has_supported_tool_call(answer):
                        self._record_collaboration_signal(
                            "misalignment",
                            {"phase": "interrupt_user_turn", "reason": "tool_call_in_interrupt"},
                        )
                        answer = self._identity_reply()
                    else:
                        self._record_collaboration_signal(
                            "cooperate",
                            {"phase": "interrupt_user_turn", "reason_code": supervision_result.reason_code},
                        )
                    base_sentence = self._identity_reply() if identity_query else self._single_sentence(answer)
                    answer = base_sentence + self._interrupt_followup_question()

                if should_remind_continuation and not _has_supported_tool_call(answer):
                    self._set_actor_state("supervisor", "CORRECTING", "Kastor wymusza krok operacyjny po pasywnej odpowiedzi")
                    corrective_prompt = (
                        "Twoja poprzednia odpowiedź nie rozpoczęła realnego działania frameworka. "
                        "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call jako najbliższy krok operacyjny. "
                        "Bez opisu i bez pseudo-kodu.\n\n"
                        f"Polecenie użytkownika: {text}"
                    )
                    corrective_result = self._chat_service.supervisor_service.refine(
                        user_message=corrective_prompt,
                        model_answer=answer,
                        stage="textual_no_action_corrective",
                    )
                    answer = corrective_result.answer
                    self._enqueue_supervisor_message(
                        stage="textual_no_action_corrective",
                        reason_code=corrective_result.reason_code,
                        notes=self._merge_supervisor_notes(
                            "Wymuszenie kroku operacyjnego po pasywnej odpowiedzi.",
                            corrective_result.notes,
                        ),
                        answer=answer,
                    )
                    self._set_actor_state("supervisor", "READY", "Kastor zakończył korektę pasywnej odpowiedzi")
        except OllamaClientError as error:
            self._append_log(
                "user_model_log",
                f"Błąd modelu/Ollama: {error}. Sprawdź połączenie i dostępność modelu.",
            )
            self._set_actor_state("creator", "ERROR", "Błąd połączenia z modelem")
            self._finalize_router_cycle(event="Router zakończył cykl z błędem modelu")
            return
        except OSError as error:
            self._append_log("user_model_log", f"Błąd systemowy: {error}")
            self._set_actor_state("creator", "ERROR", "Błąd systemowy podczas wykonania")
            self._finalize_router_cycle(event="Router zakończył cykl z błędem systemowym")
            return

        self._apply_idle_hint_from_answer(answer, source="creator")

        self._set_actor_state("router", "PROGRESS_GUARD", "Router weryfikuje postęp operacyjny")
        answer = self._enforce_supervised_progress(text, answer, allow_text_reply=interrupt_mode)

        self._set_actor_state("router", "TOOL_FLOW", "Router realizuje wywołania narzędzi")
        answer = self._resolve_tool_calls(answer)
        self._apply_idle_hint_from_answer(answer, source="router")
        self._last_model_answer = answer

        if _has_supported_tool_call(answer):
            self._passive_turns += 1
            self._set_actor_state("creator", "PASSIVE", "Pozostał nierozwiązany krok narzędziowy")
        else:
            if interrupt_mode:
                self._passive_turns = 0
                self._last_progress_monotonic = time.monotonic()
            else:
                self._passive_turns += 1
            self._set_actor_state("creator", "PASSIVE", "Brak kroku narzędziowego")

        # --- Model asks user a question → pause plan & suspend watchdog ---
        if not interrupt_mode and self._model_response_awaits_user(answer):
            # Check for premature completion: plan is "completed" but task isn't truly done
            if self._is_premature_plan_completion(answer):
                self._set_actor_state("router", "REDIRECT", "Przedwczesne zakończenie planu — Kastor przekierowuje Polluksa")
                redirected = self._redirect_premature_completion(text, answer)
                if redirected is not None:
                    answer = redirected
                    self._passive_turns = 0
                    self._last_progress_monotonic = time.monotonic()
                else:
                    # Redirect failed — fall through to normal pause
                    self._set_plan_paused(paused=True, reason="model_awaits_user", source="process_user_turn")
                    self._pending_user_decision = True
                    self._pending_decision_identity_query = False
                    self._watchdog_suspended_until_user_input = True
                    self._set_actor_state("router", "PAUSED", "Model oczekuje na decyzję użytkownika")
                    self._record_collaboration_signal("model_awaits_user", {"excerpt": answer[-200:]})
            else:
                self._set_plan_paused(paused=True, reason="model_awaits_user", source="process_user_turn")
                self._pending_user_decision = True
                self._pending_decision_identity_query = False
                self._watchdog_suspended_until_user_input = True
                self._set_actor_state("router", "PAUSED", "Model oczekuje na decyzję użytkownika")
                self._record_collaboration_signal("model_awaits_user", {"excerpt": answer[-200:]})

        display_answer = _format_user_facing_answer(answer)

        # --- Communication protocol: addressed block routing ---
        self._set_actor_state("router", "DELIVERING", "Router kieruje bloki komunikacyjne na panele")
        blocks = parse_addressed_blocks(answer)
        routed_to_user_panel = False
        self._consultation_rounds_this_cycle = 0
        if blocks:
            self._unaddressed_turns = 0
            panel_map = self._comm_rules.panel_mapping or None
            for block in blocks:
                target_panels = panels_for_target(block.target, panel_map)
                label = f"[{block.sender} -> {block.target}]" if block.sender else ""

                # --- Sanitize content for Sponsor panel ---
                sponsor_targeted = "user_model_log" in target_panels
                block_content = block.content
                if sponsor_targeted:
                    sanitized = self._sanitize_block_for_sponsor(block_content, label)
                    if sanitized is None:
                        # Nothing readable — already redirected to executor_log
                        continue
                    block_content = sanitized

                for panel_id in target_panels:
                    self._append_log(panel_id, f"{label} {block_content}" if label else block_content)
                if sponsor_targeted:
                    routed_to_user_panel = True

                # Consultation: Polluks -> Kastor (with round limit)
                max_consult = getattr(self._comm_rules, 'consultation_max_rounds', 1)
                if (
                    block.sender == "Polluks"
                    and block.target == "Kastor"
                    and self._chat_service.supervisor_service is not None
                    and self._consultation_rounds_this_cycle < max_consult
                ):
                    self._consultation_rounds_this_cycle += 1
                    self._set_actor_state("supervisor", "CONSULTING", "Kastor otrzymał konsultację od Polluksa")
                    try:
                        consult_result = self._chat_service.supervisor_service.refine(
                            user_message=f"[Polluks -> Kastor] {block.content}",
                            model_answer=block.content,
                            stage="consultation",
                        )
                        consult_reply = consult_result.answer
                        self._enqueue_supervisor_message(
                            stage="consultation",
                            reason_code=consult_result.reason_code,
                            notes=self._merge_supervisor_notes(
                                "Odpowiedź Kastora na konsultację Polluksa.",
                                consult_result.notes,
                            ),
                            answer=consult_reply,
                        )
                        self._append_log("supervisor_log", f"[Kastor -> Polluks] {consult_reply}")
                    except (OllamaClientError, OSError):
                        self._append_log("supervisor_log", _("watchdog.consult_error"))
                    self._set_actor_state("supervisor", "READY", "Kastor zakończył konsultację")
        else:
            # No addressed blocks — check if tool_call (exempt) or unaddressed
            if not parse_tool_calls(answer):
                self._unaddressed_turns += 1
                reminder_threshold = self._comm_rules.missing_header_threshold
                max_reminders = self._comm_rules.max_reminders_per_session
                if self._unaddressed_turns >= reminder_threshold and self._reminder_count < max_reminders:
                    self._set_actor_state("router", "REMINDING", "Koordynator wysyła przypomnienie o adresowaniu")
                    reminder_text = self._comm_rules.reminder_template or (
                        "[Kastor -> Polluks] Przypominam: każdy komunikat musi zaczynać się "
                        "od nagłówka [Polluks -> Odbiorca]. Popraw format odpowiedzi."
                    )
                    self._append_log("supervisor_log", reminder_text)
                    self._enqueue_supervisor_message(
                        stage="addressing_reminder",
                        reason_code="MISSING_HEADER",
                        notes=reminder_text[:500],
                        answer="",
                    )
                    self._unaddressed_turns = 0
                    self._reminder_count += 1

        if not routed_to_user_panel:
            self._append_log("user_model_log", f"Model: {display_answer}")
        self._append_log("executor_log", f"[user_turn] {answer}")
        self._finalize_router_cycle(event="Router dostarczył odpowiedź użytkownikowi")
        if self._chat_service.supervisor_service is None and not self._supervisor_notice_shown:
            self._append_log(
                "supervisor_log",
                _("mount.kastor_inactive_panel"),
            )
            self._supervisor_notice_shown = True
        self._poll_supervision_dialogue()

    def _format_supervision_lane_label(self, *, stage: str, kind: str, direction: str) -> str:
        stage_label = stage or "unknown_stage"
        kind_label = kind or "unknown_type"
        return f"[{direction} | {stage_label}:{kind_label}]"

    def _run_supervisor_idle_watchdog(self) -> None:
        supervisor = self._chat_service.supervisor_service
        if supervisor is None:
            return
        if self._watchdog_suspended_until_user_input:
            return
        if self._router_cycle_in_progress:
            return
        if not self._last_user_message:
            return

        now = time.monotonic()
        if self._auto_resume_paused_plan_if_needed(now):
            return
        if self._idle_until_epoch is not None:
            if time.time() < self._idle_until_epoch:
                self._set_actor_state("router", "IDLE_SCHEDULED", "Router respektuje zaplanowane IDLE")
                return
            self._idle_until_epoch = None
            self._idle_until_source = ""
        idle_seconds = now - self._last_progress_monotonic
        actionable_plan = self._has_actionable_plan()
        plan_required = self._plan_requires_update()
        if idle_seconds < self._watchdog_idle_threshold_seconds:
            return
        if self._passive_turns <= 0 and not actionable_plan and not plan_required:
            return

        if self._watchdog_attempts >= SUPERVISOR_WATCHDOG_MAX_ATTEMPTS:
            if not self._watchdog_capped_notified:
                self._append_log(
                    "supervisor_log",
                    (
                        "Watchdog Kastora osiągnął limit prób reaktywacji; "
                        "wstrzymuję auto-reaktywację do kolejnej wiadomości użytkownika."
                    ),
                )
                self._watchdog_capped_notified = True
            self._watchdog_suspended_until_user_input = True
            self._set_actor_state("router", "PAUSED", "Watchdog wstrzymany do czasu nowej wiadomości użytkownika")
            self._log_activity(
                action="watchdog.suspended.await_user_input",
                intent="Wstrzymano watchdog po limicie prób; oczekiwanie na nową wiadomość użytkownika.",
                details={
                    "max_attempts": SUPERVISOR_WATCHDOG_MAX_ATTEMPTS,
                    "idle_seconds": round(idle_seconds, 2),
                },
            )
            return

        self._watchdog_attempts += 1
        self._watchdog_capped_notified = False
        self._set_actor_state("router", "WATCHDOG", "Router wzbudza Kastora po bezczynności")
        self._set_actor_state("supervisor", "REVIEWING", "Kastor sprawdza status działań Twórcy")

        context = {
            "idle_seconds": round(idle_seconds, 2),
            "idle_threshold_seconds": self._watchdog_idle_threshold_seconds,
            "passive_turns": self._passive_turns,
            "actionable_plan": actionable_plan,
            "plan_persistence": {"required": plan_required},
            "watchdog_attempt": self._watchdog_attempts,
            "watchdog_max_attempts": SUPERVISOR_WATCHDOG_MAX_ATTEMPTS,
            "should_remind_continuation": True,
            "gpu_busy_over_50": False,
        }

        prompt = (
            "[RUNTIME_SUPERVISION_CONTEXT]\n"
            + json.dumps(context, ensure_ascii=False)
            + "\n[/RUNTIME_SUPERVISION_CONTEXT]\n"
            + "Wykryto dłuższą bezczynność modelu wykonawczego. "
            + "Przekaż komunikat korygujący do modelu głównego i wymuś kolejny krok operacyjny przez pojedynczy tool_call. "
            + "Nie kończ na opisie.\n"
            + "Ostatnie polecenie użytkownika: "
            + self._last_user_message
        )

        model_answer = self._last_model_answer or "Brak postępu narzędziowego od dłuższego czasu."
        try:
            recent_msgs = self._chat_service.memory_repository.recent_messages(limit=6)
            conv_excerpt = format_conversation_excerpt(recent_msgs, limit=6)
            result = supervisor.refine(
                user_message=prompt,
                model_answer=model_answer,
                stage="textual_watchdog_nudge",
                conversation_excerpt=conv_excerpt,
            )
        except (OllamaClientError, OSError):
            self._set_actor_state("supervisor", "ERROR", "Błąd podczas wzbudzenia Kastora")
            self._watchdog_suspended_until_user_input = True
            self._watchdog_capped_notified = True
            self._append_log(
                "supervisor_log",
                _("watchdog.error_suspended"),
            )
            self._set_actor_state("router", "PAUSED", "Watchdog zatrzymany po błędzie nadzorcy")
            return

        self._set_actor_state("supervisor", "READY", "Kastor zakończył wzbudzenie")
        self._enqueue_supervisor_message(
            stage="textual_watchdog_nudge",
            reason_code=result.reason_code,
            notes=self._merge_supervisor_notes(
                "Watchdog Kastora przekazał zalecenia Polluksowi.",
                result.notes,
            ),
            answer=result.answer,
        )
        self._set_actor_state("router", "PROGRESS_GUARD", "Router weryfikuje postęp po watchdog")
        answer = self._enforce_supervised_progress(self._last_user_message, result.answer, max_attempts=2)
        self._apply_idle_hint_from_answer(answer, source="supervisor")

        answer = self._resolve_tool_calls(answer)
        self._last_model_answer = answer
        if _has_supported_tool_call(answer):
            self._passive_turns += 1
            self._set_actor_state("creator", "PASSIVE", "Watchdog: pozostał nierozwiązany krok narzędziowy")
        else:
            self._passive_turns += 1
            self._set_actor_state("creator", "PASSIVE", "Brak akcji po wzbudzeniu")

        # --- Model asks user a question after watchdog → pause & suspend ---
        if self._model_response_awaits_user(answer):
            if self._is_premature_plan_completion(answer):
                redirected = self._redirect_premature_completion(self._last_user_message, answer)
                if redirected is not None:
                    answer = redirected
                    self._passive_turns = 0
                    self._last_progress_monotonic = time.monotonic()
                    answer = self._resolve_tool_calls(answer)
                    self._last_model_answer = answer
                else:
                    self._set_plan_paused(paused=True, reason="model_awaits_user", source="watchdog")
                    self._pending_user_decision = True
                    self._pending_decision_identity_query = False
                    self._watchdog_suspended_until_user_input = True
                    self._set_actor_state("router", "PAUSED", "Model oczekuje na decyzję użytkownika po watchdog")
                    self._record_collaboration_signal("model_awaits_user", {"source": "watchdog", "excerpt": answer[-200:]})
            else:
                self._set_plan_paused(paused=True, reason="model_awaits_user", source="watchdog")
                self._pending_user_decision = True
                self._pending_decision_identity_query = False
                self._watchdog_suspended_until_user_input = True
                self._set_actor_state("router", "PAUSED", "Model oczekuje na decyzję użytkownika po watchdog")
                self._record_collaboration_signal("model_awaits_user", {"source": "watchdog", "excerpt": answer[-200:]})

        display_answer = _format_user_facing_answer(answer)
        self._append_log("user_model_log", f"Model(auto): {display_answer}")
        self._append_log("executor_log", f"[watchdog] {answer}")
        self._finalize_router_cycle(event="Router zakończył cykl watchdog")
        self._poll_supervision_dialogue()

    def _poll_supervision_dialogue(self) -> None:
        if not self._supervisor_dialogue_log_path.exists():
            return
        try:
            with self._supervisor_dialogue_log_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._dialogue_log_offset)
                lines = handle.readlines()
                self._dialogue_log_offset = handle.tell()
        except OSError:
            return

        if not lines:
            return

        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            kind = str(payload.get("type", ""))
            stage = str(payload.get("stage", ""))

            executor_answer = str(payload.get("executor_answer", "")).strip()
            if executor_answer:
                lane = self._format_supervision_lane_label(
                    stage=stage,
                    kind=kind,
                    direction="POLLUKS→KASTOR",
                )
                self._append_log("executor_log", f"{lane} {executor_answer}")

            supervisor_output = str(payload.get("supervisor_raw_output", "")).strip()
            if supervisor_output:
                rendered_supervisor = supervisor_output
                try:
                    supervisor_payload = json.loads(supervisor_output)
                except Exception:
                    supervisor_payload = None

                notes_txt = ""
                repaired_txt = ""
                if isinstance(supervisor_payload, dict):
                    status_txt = str(supervisor_payload.get("status", "")).strip()
                    reason_txt = str(supervisor_payload.get("reason_code", "")).strip()
                    state_txt = str(supervisor_payload.get("work_state", "")).strip()
                    notes_txt = str(supervisor_payload.get("notes", "")).strip()
                    repaired_txt = str(supervisor_payload.get("repaired_answer", "")).strip()

                    details: list[str] = []
                    if status_txt:
                        details.append(f"status={status_txt}")
                    if reason_txt:
                        details.append(f"reason={reason_txt}")
                    if state_txt:
                        details.append(f"work_state={state_txt}")
                    if repaired_txt:
                        details.append("repaired_answer=present")
                    if notes_txt:
                        details.append(f"notes={notes_txt[:240]}")
                    rendered_supervisor = ", ".join(details) if details else "supervisor_output=empty"
                elif len(rendered_supervisor) > 500:
                    rendered_supervisor = rendered_supervisor[:500] + "…"

                lane = self._format_supervision_lane_label(
                    stage=stage,
                    kind=kind,
                    direction="KASTOR→ROUTER",
                )
                self._append_log("supervisor_log", f"{lane} {rendered_supervisor}")

                # Route addressed blocks from supervisor notes/repaired_answer
                # to the correct panels (e.g. [Kastor -> Sponsor] → user_model_log)
                poll_panel_map = self._comm_rules.panel_mapping or None
                for poll_fragment in (notes_txt, repaired_txt) if isinstance(supervisor_payload, dict) else ():
                    if not poll_fragment:
                        continue
                    poll_blocks = parse_addressed_blocks(poll_fragment)
                    for poll_block in poll_blocks:
                        if not poll_block.sender and not poll_block.target:
                            continue
                        poll_target_panels = panels_for_target(poll_block.target, poll_panel_map)
                        poll_extra = [p for p in poll_target_panels if p != "supervisor_log"]
                        if poll_extra:
                            poll_label = f"[{poll_block.sender} -> {poll_block.target}]" if poll_block.sender else ""
                            poll_content = poll_block.content
                            # Sanitize tool_call content before sending to Sponsor panel
                            if "user_model_log" in poll_extra:
                                sanitized = self._sanitize_block_for_sponsor(poll_content, poll_label)
                                if sanitized is None:
                                    poll_extra = [p for p in poll_extra if p != "user_model_log"]
                                    if not poll_extra:
                                        continue
                                else:
                                    poll_content = sanitized
                            for poll_panel_id in poll_extra:
                                self._append_log(poll_panel_id, f"{poll_label} {poll_content}" if poll_label else poll_content)

            status = str(payload.get("status", "")).strip()
            reason = str(payload.get("reason_code", "")).strip()
            repaired = str(payload.get("repaired_answer", "")).strip()
            if status:
                summary = f"status={status}"
                if reason:
                    summary += f", reason={reason}"
                if repaired:
                    summary += f", repaired={repaired}"
                lane = self._format_supervision_lane_label(
                    stage=stage,
                    kind=kind,
                    direction="KASTOR→ROUTER",
                )
                self._append_log("supervisor_log", f"{lane} {summary}")

    def _resolve_model_path(self, raw_path: str) -> Path:
        path = _resolve_tool_path(raw_path, self._work_dir)
        cleaned = raw_path.strip().replace("\\", "/")
        if cleaned.startswith("amiagi-main/"):
            suffix = cleaned.split("/", 1)[1] if "/" in cleaned else ""
            if suffix == "notes/main_plan.json":
                return (self._work_dir / "notes" / "main_plan.json").resolve()
            if suffix:
                workspace_candidate = (Path.cwd() / suffix).resolve()
                if workspace_candidate.exists():
                    return workspace_candidate
        return path

    def _plan_requires_update(self) -> bool:
        plan_path = self._work_dir / "notes" / "main_plan.json"
        if not plan_path.exists() or not plan_path.is_file():
            return True

        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            return True

        if not isinstance(payload, dict):
            return True

        goal = payload.get("goal")
        current_stage = payload.get("current_stage")
        tasks = payload.get("tasks")
        if not isinstance(goal, str) or not goal.strip():
            return True
        if not isinstance(current_stage, str) or not current_stage.strip():
            return True
        if not isinstance(tasks, list) or not tasks:
            return True

        allowed_statuses = {"rozpoczęta", "w trakcie realizacji", "zakończona"}
        for task in tasks:
            if not isinstance(task, dict):
                return True
            status = str(task.get("status", "")).strip().lower()
            if status not in allowed_statuses:
                return True
            for field in ("id", "title", "next_step"):
                value = task.get(field)
                if not isinstance(value, str) or not value.strip():
                    return True

        return False

    def _has_actionable_plan(self) -> bool:
        plan_path = self._work_dir / "notes" / "main_plan.json"
        if not plan_path.exists() or not plan_path.is_file():
            return False
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        tasks = payload.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return False
        for task in tasks:
            if not isinstance(task, dict):
                continue
            status = str(task.get("status", "")).strip().lower()
            if status in {"rozpoczęta", "w trakcie realizacji"}:
                return True
        return False

    def _enforce_supervised_progress(
        self,
        user_message: str,
        initial_answer: str,
        max_attempts: int = 3,
        allow_text_reply: bool = False,
    ) -> str:
        supervisor = self._chat_service.supervisor_service
        if supervisor is None:
            return initial_answer

        current = initial_answer
        if allow_text_reply and not self._answer_has_supported_tool_call(current):
            return current
        for attempt in range(1, max_attempts + 1):
            has_supported_tool = self._answer_has_supported_tool_call(current)
            plan_required = self._plan_requires_update()
            if has_supported_tool and not plan_required:
                return current

            self._set_actor_state("supervisor", "PROGRESS_GUARD", f"Kastor wymusza postęp (próba {attempt}/{max_attempts})")
            supervision_context = {
                "passive_turns": self._passive_turns,
                "should_remind_continuation": not has_supported_tool,
                "gpu_busy_over_50": False,
                "plan_persistence": {"required": plan_required},
                "progress_guard": {"attempt": attempt, "max_attempts": max_attempts},
            }

            corrective_instruction = "Wymuś postęp operacyjny: zwróć WYŁĄCZNIE jeden poprawny blok tool_call."
            if plan_required:
                corrective_instruction += (
                    " Najpierw napraw/zainicjalizuj plan w notes/main_plan.json "
                    "(goal, key_achievement, current_stage, tasks[] ze statusami: "
                    "rozpoczęta|w trakcie realizacji|zakończona)."
                )
            else:
                corrective_instruction += " Odpowiedź musi uruchamiać realne działanie narzędziowe."

            available_tools_list = sorted(self._runtime_supported_tool_names())
            corrective_prompt = (
                "[RUNTIME_SUPERVISION_CONTEXT]\n"
                + json.dumps(supervision_context, ensure_ascii=False)
                + "\n[/RUNTIME_SUPERVISION_CONTEXT]\n"
                + corrective_instruction
                + "\nDOSTĘPNE NARZĘDZIA: " + ", ".join(available_tools_list) + "."
                + "\nUżywaj WYŁĄCZNIE narzędzi z powyższej listy. Nie używaj nazw, których tu nie ma."
                + "\nPolecenie użytkownika: "
                + user_message
            )

            try:
                result = supervisor.refine(
                    user_message=corrective_prompt,
                    model_answer=current,
                    stage="textual_progress_guard",
                )
            except (OllamaClientError, OSError):
                self._set_actor_state("supervisor", "READY", "Kastor — progress guard przerwany błędem")
                return current

            refined_calls = parse_tool_calls(result.answer)
            runtime_supported = self._runtime_supported_tool_names()
            first_supported = next(
                (call for call in refined_calls if _canonical_tool_name(call.tool) in runtime_supported),
                None,
            )

            self._set_actor_state("supervisor", "READY", "Kastor zakończył progress guard")
            if first_supported is not None:
                current = _render_single_tool_call_block(first_supported)
            elif plan_required:
                fallback_plan = {
                    "goal": (user_message.strip() or "Kontynuacja głównego celu użytkownika")[:200],
                    "key_achievement": "Zainicjalizowany plan z kolejnym krokiem operacyjnym.",
                    "current_stage": "inicjalizacja_planu",
                    "tasks": [
                        {
                            "id": "T1",
                            "title": "Zainicjalizować plan główny",
                            "status": "rozpoczęta",
                            "next_step": "Wykonać pierwszy krok narzędziowy po zapisie planu.",
                        }
                    ],
                }
                current = (
                    "```tool_call\n"
                    + json.dumps(
                        {
                            "tool": "write_file",
                            "args": {
                                "path": "notes/main_plan.json",
                                "content": json.dumps(fallback_plan, ensure_ascii=False),
                                "overwrite": True,
                            },
                            "intent": "init_plan_fallback",
                        },
                        ensure_ascii=False,
                    )
                    + "\n```"
                )
            else:
                current = (
                    "```tool_call\n"
                    + json.dumps(
                        {
                            "tool": "list_dir",
                            "args": {"path": "."},
                            "intent": "fallback_after_invalid_supervisor_repair",
                        },
                        ensure_ascii=False,
                    )
                    + "\n```"
                )

            self._enqueue_supervisor_message(
                stage="textual_progress_guard",
                reason_code=result.reason_code,
                notes=self._merge_supervisor_notes(
                    "Kastor wymusił postęp operacyjny.",
                    result.notes,
                ),
                answer=current,
            )

        return current

    def _execute_tool_call(self, tool_call: ToolCall, *, agent_id: str = "") -> dict:
        tool = tool_call.tool.strip()
        if tool == "run_command":
            tool = "run_shell"
        args = tool_call.args

        # ---- Phase 7: per-agent permission enforcement ----
        if agent_id and self._permission_enforcer is not None:
            result = self._permission_enforcer.check_tool(agent_id, tool)
            if not result.allowed:
                if self._audit_chain is not None:
                    self._audit_chain.record_action(
                        agent_id=agent_id,
                        action=f"tool.denied:{tool}",
                        target=tool,
                        approved_by="permission_enforcer",
                    )
                return {"ok": False, "error": f"permission_denied:{result.reason}"}
            # Path-based checks for file tools
            if tool in ("read_file", "list_dir", "check_python_syntax", "run_python"):
                path_arg = str(args.get("path", ""))
                if path_arg:
                    path_result = self._permission_enforcer.check_path(
                        agent_id, path_arg, write=False
                    )
                    if not path_result.allowed:
                        return {"ok": False, "error": f"permission_denied:{path_result.reason}"}
            if tool in ("write_file", "append_file"):
                path_arg = str(args.get("path", ""))
                if path_arg:
                    path_result = self._permission_enforcer.check_path(
                        agent_id, path_arg, write=True
                    )
                    if not path_result.allowed:
                        return {"ok": False, "error": f"permission_denied:{path_result.reason}"}

        if tool == "read_file":
            if not self._ensure_resource("disk.read", "Tool read_file wymaga odczytu pliku"):
                return {"ok": False, "error": "permission_denied:disk.read"}
            path = self._resolve_model_path(str(args.get("path", "")))
            max_chars = int(args.get("max_chars", 12000))
            offset = max(0, int(args.get("offset", 0)))
            if not path.exists() or not path.is_file():
                return {"ok": False, "error": "file_not_found", "path": str(path)}
            try:
                content = path.read_text(encoding="utf-8")
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(path)}
            total_chars = len(content)
            chunk = content[offset : offset + max_chars]
            chunk_end = offset + len(chunk)
            has_more = chunk_end < total_chars
            read_result: dict = {
                "ok": True,
                "tool": "read_file",
                "path": str(path),
                "content": chunk,
                "total_chars": total_chars,
                "offset": offset,
                "chunk_end": chunk_end,
                "has_more": has_more,
            }
            if has_more:
                read_result["next_offset"] = chunk_end
                read_result["hint"] = (
                    "Plik jest dłuższy niż jeden chunk. "
                    "Użyj read_file z offset=" + str(chunk_end) + " aby kontynuować. "
                    "Zalecenie: rób notatki w notes/ z kluczowymi informacjami z każdego chunka."
                )
            return read_result

        if tool == "list_dir":
            if not self._ensure_resource("disk.read", "Tool list_dir wymaga odczytu katalogu"):
                return {"ok": False, "error": "permission_denied:disk.read"}
            path = self._resolve_model_path(str(args.get("path", "")))
            if not path.exists() or not path.is_dir():
                return {"ok": False, "error": "dir_not_found", "path": str(path)}
            items = sorted(child.name for child in path.iterdir())
            return {"ok": True, "tool": "list_dir", "path": str(path), "items": items}

        if tool == "write_file":
            if not self._ensure_resource("disk.write", "Tool write_file wymaga zapisu pliku"):
                return {"ok": False, "error": "permission_denied:disk.write"}
            path = self._resolve_model_path(str(args.get("path", "")))
            if not _is_path_within_work_dir(path, self._work_dir):
                return {"ok": False, "error": "path_outside_work_dir", "path": str(path)}
            raw_content = args.get("content")
            if raw_content is None and "data" in args:
                raw_content = args.get("data")
            if isinstance(raw_content, str):
                content = raw_content
            elif raw_content is None:
                content = ""
            else:
                content = json.dumps(raw_content, ensure_ascii=False, indent=2)
            overwrite = bool(args.get("overwrite", False))
            if path.exists() and not overwrite:
                return {"ok": False, "error": "file_exists_overwrite_required", "path": str(path)}
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(path)}
            return {"ok": True, "tool": "write_file", "path": str(path), "chars": len(content)}

        if tool == "append_file":
            if not self._ensure_resource("disk.write", "Tool append_file wymaga zapisu pliku"):
                return {"ok": False, "error": "permission_denied:disk.write"}
            path = self._resolve_model_path(str(args.get("path", "")))
            if not _is_path_within_work_dir(path, self._work_dir):
                return {"ok": False, "error": "path_outside_work_dir", "path": str(path)}
            content = str(args.get("content", ""))
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(content)
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(path)}
            return {"ok": True, "tool": "append_file", "path": str(path), "chars": len(content)}

        if tool == "check_python_syntax":
            if not self._ensure_resource("disk.read", "Tool check_python_syntax wymaga odczytu pliku"):
                return {"ok": False, "error": "permission_denied:disk.read"}
            path = self._resolve_model_path(str(args.get("path", "")))
            if not path.exists() or not path.is_file():
                return {"ok": False, "error": "file_not_found", "path": str(path)}
            try:
                source = path.read_text(encoding="utf-8")
                compile(source, str(path), "exec")
            except SyntaxError as error:
                return {
                    "ok": False,
                    "tool": "check_python_syntax",
                    "path": str(path),
                    "syntax_ok": False,
                    "message": str(error),
                    "line": error.lineno,
                    "offset": error.offset,
                }
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(path)}
            return {"ok": True, "tool": "check_python_syntax", "path": str(path), "syntax_ok": True}

        if tool == "run_python":
            if not self._ensure_resource("disk.read", "Tool run_python wymaga odczytu skryptu"):
                return {"ok": False, "error": "permission_denied:disk.read"}
            if not self._ensure_resource("process.exec", "Tool run_python wymaga wykonania procesu"):
                return {"ok": False, "error": "permission_denied:process.exec"}
            path = self._resolve_model_path(str(args.get("path", "")))
            run_args = args.get("args", [])
            if not isinstance(run_args, list):
                return {"ok": False, "error": "args_must_be_list"}
            if not path.exists() or not path.is_file():
                return {"ok": False, "error": "file_not_found", "path": str(path)}
            result = self._script_executor.execute_python(path, [str(item) for item in run_args])
            return {
                "ok": True,
                "tool": "run_python",
                "command": result.command,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        if tool == "run_shell":
            if not self._ensure_resource("process.exec", "Tool run_shell wymaga wykonania procesu"):
                return {"ok": False, "error": "permission_denied:process.exec"}
            command_text = str(args.get("command", "")).strip()
            if not command_text:
                return {"ok": False, "error": "missing_command"}
            _ok, validation_error = parse_and_validate_shell_command(command_text, self._shell_policy)
            if validation_error is not None:
                return {"ok": False, "error": f"policy_rejected:{validation_error}"}
            result = self._script_executor.execute_shell(command_text)
            return {
                "ok": True,
                "tool": "run_shell",
                "command": result.command,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        if tool == "fetch_web":
            if not self._ensure_resource("network.internet", "Tool fetch_web wymaga dostępu do internetu"):
                return {"ok": False, "error": "permission_denied:network.internet"}
            url = str(args.get("url", "")).strip()
            max_chars = int(args.get("max_chars", 12000))
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return {"ok": False, "error": "invalid_url_scheme"}
            try:
                request = Request(url=url, headers={"User-Agent": "amiagi/0.1"}, method="GET")
                with urlopen(request, timeout=20) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    content = response.read().decode(charset, errors="replace")
            except (HTTPError, URLError, TimeoutError) as error:
                return {"ok": False, "error": str(error), "url": url}
            total_chars = len(content)
            offset = max(0, int(args.get("offset", 0)))
            chunk = content[offset : offset + max_chars]
            chunk_end = offset + len(chunk)
            has_more = chunk_end < total_chars
            result_payload: dict = {
                "ok": True,
                "tool": "fetch_web",
                "url": url,
                "content": chunk,
                "total_chars": total_chars,
                "offset": offset,
                "chunk_end": chunk_end,
                "has_more": has_more,
            }
            if has_more:
                result_payload["next_offset"] = chunk_end
                result_payload["hint"] = (
                    "Treść strony jest dłuższa niż jeden chunk. "
                    "Użyj fetch_web z tym samym url i offset=" + str(chunk_end) + " aby kontynuować. "
                    "Zalecenie: rób notatki w notes/ z kluczowymi informacjami z każdego chunka."
                )
            return result_payload

        if tool == "download_file":
            if not self._ensure_resource("network.internet", "Tool download_file wymaga dostępu do internetu"):
                return {"ok": False, "error": "permission_denied:network.internet"}
            if not self._ensure_resource("disk.write", "Tool download_file wymaga zapisu pliku"):
                return {"ok": False, "error": "permission_denied:disk.write"}
            url = str(args.get("url", "")).strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return {"ok": False, "error": "invalid_url_scheme", "url": url}
            max_size_mb = max(1, min(200, int(args.get("max_size_mb", 50))))
            max_bytes = max_size_mb * 1024 * 1024
            raw_output = str(args.get("output_path", "")).strip()
            if raw_output:
                output_path = self._resolve_model_path(raw_output)
            else:
                downloads_dir = self._work_dir / "downloads"
                filename = Path(parsed.path).name or "download"
                output_path = downloads_dir / filename
            if not _is_path_within_work_dir(output_path, self._work_dir):
                return {"ok": False, "error": "path_outside_work_dir", "path": str(output_path)}
            try:
                request = Request(url=url, headers={"User-Agent": "amiagi/0.1"}, method="GET")
                with urlopen(request, timeout=60) as response:
                    content_type = response.headers.get("Content-Type", "")
                    data = response.read(max_bytes + 1)
                    if len(data) > max_bytes:
                        return {"ok": False, "error": "file_too_large", "max_size_mb": max_size_mb, "url": url}
            except (HTTPError, URLError, TimeoutError) as error:
                return {"ok": False, "error": str(error), "url": url}
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(data)
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(output_path)}
            return {
                "ok": True,
                "tool": "download_file",
                "url": url,
                "path": str(output_path),
                "size_bytes": len(data),
                "content_type": content_type,
            }

        if tool == "convert_pdf_to_markdown":
            if not self._ensure_resource("disk.read", "Tool convert_pdf_to_markdown wymaga odczytu pliku"):
                return {"ok": False, "error": "permission_denied:disk.read"}
            if not self._ensure_resource("disk.write", "Tool convert_pdf_to_markdown wymaga zapisu pliku"):
                return {"ok": False, "error": "permission_denied:disk.write"}
            src_path = self._resolve_model_path(str(args.get("path", "")))
            if not src_path.exists() or not src_path.is_file():
                return {"ok": False, "error": "file_not_found", "path": str(src_path)}
            raw_out = str(args.get("output_path", "")).strip()
            if raw_out:
                out_path = self._resolve_model_path(raw_out)
            else:
                converted_dir = self._work_dir / "converted"
                out_path = converted_dir / (src_path.stem + ".md")
            if not _is_path_within_work_dir(out_path, self._work_dir):
                return {"ok": False, "error": "path_outside_work_dir", "path": str(out_path)}
            md_content = ""
            method_used = ""
            # Strategy 1: markitdown CLI
            try:
                import subprocess as _sp
                proc = _sp.run(
                    ["markitdown", str(src_path)],
                    capture_output=True, text=True, timeout=120,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    md_content = proc.stdout
                    method_used = "markitdown"
            except Exception:
                pass
            # Strategy 2: PyPDF2 page-by-page text extraction
            if not md_content:
                try:
                    from pypdf import PdfReader as _PdfReader
                    reader = _PdfReader(str(src_path))
                    pages_text: list[str] = []
                    for page_num, page in enumerate(reader.pages, 1):
                        text = page.extract_text() or ""
                        if text.strip():
                            pages_text.append(f"<!-- page {page_num} -->\n{text}")
                    if pages_text:
                        md_content = "\n\n---\n\n".join(pages_text)
                        method_used = "PyPDF2"
                except Exception:
                    pass
            # Strategy 3: pdftotext CLI
            if not md_content:
                try:
                    import subprocess as _sp
                    proc = _sp.run(
                        ["pdftotext", "-layout", str(src_path), "-"],
                        capture_output=True, text=True, timeout=120,
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        md_content = proc.stdout
                        method_used = "pdftotext"
                except Exception:
                    pass
            if not md_content:
                return {"ok": False, "error": "conversion_failed", "path": str(src_path),
                        "hint": "Nie udało się wydobyć tekstu. Plik może być zeskanowany — spróbuj OCR (glm-ocr via Ollama)."}
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(md_content, encoding="utf-8")
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(out_path)}
            return {
                "ok": True,
                "tool": "convert_pdf_to_markdown",
                "source_path": str(src_path),
                "output_path": str(out_path),
                "chars": len(md_content),
                "method": method_used,
                "hint": (
                    "Plik skonwertowany. Użyj read_file z output_path aby przeczytać treść. "
                    "Jeśli plik jest duży, użyj offset do przeglądania chunkami."
                ),
            }

        if tool == "search_web":
            if not self._ensure_resource("network.internet", "Tool search_web wymaga dostępu do internetu"):
                return {"ok": False, "error": "permission_denied:network.internet"}
            query = str(args.get("query", "")).strip()
            engine = str(args.get("engine", "duckduckgo")).strip().lower() or "duckduckgo"
            max_results = max(1, min(10, int(args.get("max_results", 5))))
            if not query:
                return {"ok": False, "error": "missing_query"}
            if engine not in {"duckduckgo", "google"}:
                return {"ok": False, "error": "unsupported_engine", "engine": engine}
            if engine == "duckduckgo":
                search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
            else:
                search_url = f"https://www.google.com/search?q={quote_plus(query)}"
            try:
                request = Request(url=search_url, headers={"User-Agent": "amiagi/0.1"}, method="GET")
                with urlopen(request, timeout=20) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    content = response.read().decode(charset, errors="replace")
            except (HTTPError, URLError, TimeoutError) as error:
                return {"ok": False, "error": str(error), "url": search_url}
            results = _parse_search_results_from_html(content, engine=engine, max_results=max_results)
            return {
                "ok": True,
                "tool": "search_web",
                "engine": engine,
                "query": query,
                "results": results,
                "results_count": len(results),
                "search_url": search_url,
            }

        if tool == "check_capabilities":
            check_network = bool(args.get("check_network", False))
            payload = {
                "tool": "check_capabilities",
                "python": shutil.which("python") is not None,
                "fswebcam": shutil.which("fswebcam") is not None,
                "ffmpeg": shutil.which("ffmpeg") is not None,
                "arecord": shutil.which("arecord") is not None,
                "camera_devices": sorted(str(path) for path in Path("/dev").glob("video*")),
                "network_checked": check_network,
            }
            if check_network:
                payload["ollama_reachable"] = bool(self._chat_service.ollama_client.ping())
            return {"ok": True, **payload}

        custom_script = resolve_registered_tool_script(self._work_dir, tool)
        if custom_script is not None:
            if not self._ensure_resource("disk.read", "Niestandardowe narzędzie wymaga odczytu skryptu"):
                return {"ok": False, "error": "permission_denied:disk.read"}
            if not self._ensure_resource("process.exec", "Niestandardowe narzędzie wymaga wykonania procesu"):
                return {"ok": False, "error": "permission_denied:process.exec"}
            if not custom_script.exists() or not custom_script.is_file():
                return {
                    "ok": False,
                    "error": "custom_tool_script_not_found",
                    "tool": tool,
                    "path": str(custom_script),
                }
            if not _is_path_within_work_dir(custom_script, self._work_dir):
                return {
                    "ok": False,
                    "error": "path_outside_work_dir",
                    "tool": tool,
                    "path": str(custom_script),
                }
            result = self._script_executor.execute_python(custom_script, [json.dumps(args, ensure_ascii=False)])
            return {
                "ok": result.exit_code == 0,
                "tool": tool,
                "runner": "python_custom",
                "script_path": str(custom_script),
                "command": result.command,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        return {"ok": False, "error": f"unknown_tool:{tool}"}

    def _resolve_tool_calls(self, initial_answer: str, max_steps: int = 15) -> str:
        current = initial_answer
        iteration = 0
        unknown_tool_correction_attempts: dict[str, int] = {}
        _MAX_CORRECTIONS_PER_TOOL = 2
        # --- Loop-detection: track consecutive identical tool invocations ---
        _tool_call_history: list[str] = []
        _MAX_SAME_TOOL_CONSECUTIVE = 3
        while iteration < max_steps:
            iteration += 1
            tool_calls = parse_tool_calls(current)
            if not tool_calls:
                if self._actor_states.get("creator") in {"THINKING", "EXECUTING_TOOL"}:
                    self._set_actor_state("creator", "PASSIVE", "Brak kolejnych tool_call po analizie wyniku")
                return current

            aggregated_results: list[dict] = []
            unknown_tools: list[str] = []
            self._set_actor_state("router", "TOOL_FLOW", "Router realizuje kolejkę tool_call")
            for tool_call in tool_calls:
                canonical_tool = _canonical_tool_name(tool_call.tool)
                self._log_activity(
                    action="tool_call.request",
                    intent="Model w Textual zgłosił żądanie wykonania narzędzia.",
                    details={"tool": canonical_tool, "intent": tool_call.intent},
                )
                self._set_actor_state("creator", "EXECUTING_TOOL", f"Wykonanie narzędzia: {tool_call.tool}")
                result = self._execute_tool_call(tool_call)
                error = result.get("error")
                if isinstance(error, str) and error.startswith("unknown_tool:"):
                    unknown_tools.append(error.removeprefix("unknown_tool:"))
                self._log_activity(
                    action="tool_call.result",
                    intent="Framework Textual zakończył wykonanie narzędzia.",
                    details={
                        "tool": canonical_tool,
                        "ok": bool(result.get("ok")),
                        "error": str(error) if error is not None else "",
                    },
                )
                aggregated_results.append(
                    {
                        "tool": canonical_tool,
                        "intent": tool_call.intent,
                        "result": result,
                    }
                )

            if unknown_tools:
                # --- Loop detection: check for repeated tool signatures ---
                pass  # handled below in the unknown_tools block
            else:
                # Track tool call signatures for loop detection
                sig = "|".join(
                    f"{r['tool']}:{json.dumps(r.get('result', {}).get('ok', ''), ensure_ascii=False)}"
                    for r in aggregated_results
                )
                _tool_call_history.append(sig)
                if len(_tool_call_history) >= _MAX_SAME_TOOL_CONSECUTIVE:
                    recent = _tool_call_history[-_MAX_SAME_TOOL_CONSECUTIVE:]
                    if len(set(recent)) == 1:
                        self._append_log(
                            "user_model_log",
                            (
                                f"Ostrzeżenie runtime: wykryto pętlę — to samo narzędzie ({tool_calls[0].tool}) "
                                f"wywołane {_MAX_SAME_TOOL_CONSECUTIVE} razy z identycznym wynikiem. "
                                "Przerywam pętlę tool_flow."
                            ),
                        )
                        self._log_activity(
                            action="tool_flow.loop_detected",
                            intent="Przerwanie pętli tool_flow po wykryciu powtarzających się wywołań.",
                            details={
                                "tool": tool_calls[0].tool,
                                "consecutive_repeats": _MAX_SAME_TOOL_CONSECUTIVE,
                                "iteration": iteration,
                            },
                        )
                        self._set_actor_state("router", "STALLED", "Pętla tool_flow — przerywam")
                        # Return a text summary instead of continuing the loop
                        tools_used = ", ".join(r["tool"] for r in aggregated_results)
                        return (
                            f"Wykonano narzędzie {tools_used}, ale powstała pętla powtarzających się wywołań. "
                            "Proszę o doprecyzowanie polecenia lub ręczne wskazanie następnego kroku."
                        )

            if unknown_tools:
                # Track per-tool correction attempts to prevent infinite loops
                for ut in unknown_tools:
                    unknown_tool_correction_attempts[ut] = unknown_tool_correction_attempts.get(ut, 0) + 1

                exhausted_tools = [
                    ut for ut in unknown_tools
                    if unknown_tool_correction_attempts.get(ut, 0) > _MAX_CORRECTIONS_PER_TOOL
                ]

                if exhausted_tools:
                    # After N failed corrections, force tool-creation workflow by writing a plan
                    tool_name = exhausted_tools[0]
                    tool_plan = {
                        "tool_name": tool_name,
                        "status": "design_required",
                        "workflow": [
                            "1. Zaprojektuj funkcjonalność narzędzia",
                            "2. Napisz skrypt Python w amiagi-my-work/src/<nazwa>.py",
                            "3. Sprawdź składnię: check_python_syntax",
                            "4. Uruchom test: run_python",
                            "5. Debuguj i napraw błędy",
                            "6. Zarejestruj w state/tool_registry.json",
                            "7. Użyj narzędzia do realizacji zadania",
                        ],
                        "description": f"Plan tworzenia narzędzia '{tool_name}' po wyczerpaniu prób naprawy.",
                    }
                    current = (
                        "```tool_call\n"
                        + json.dumps(
                            {
                                "tool": "write_file",
                                "args": {
                                    "path": "notes/tool_design_plan.json",
                                    "content": json.dumps(tool_plan, ensure_ascii=False, indent=2),
                                    "overwrite": True,
                                },
                                "intent": f"force_tool_creation_plan:{tool_name}",
                            },
                            ensure_ascii=False,
                        )
                        + "\n```"
                    )
                    self._append_log(
                        "supervisor_log",
                        f"[Koordynator] Wyczerpano próby naprawy narzędzia '{tool_name}'. "
                        "Wymuszam zapis planu tworzenia narzędzia do notes/tool_design_plan.json.",
                    )
                    continue

                available_tools = sorted(self._runtime_supported_tool_names())
                corrective_prompt = (
                    "W poprzednim kroku użyto nieobsługiwanych narzędzi: "
                    + ", ".join(sorted(set(unknown_tools)))
                    + ".\n"
                    + "LISTA DOSTĘPNYCH NARZĘDZI: " + ", ".join(available_tools) + ".\n"
                    + "Jeśli dane narzędzie nie istnieje, przypomnij Polluksowi o trybie proaktywnym: "
                    + "zaprojektuj narzędzie, zapisz plan do notes/tool_design_plan.json oraz zarejestruj narzędzie w state/tool_registry.json.\n"
                    + "Plan musi zawierać: funkcjonalność narzędzia, procedurę debugowania, testowania i naprawy skryptów, "
                    + "procedurę dopisania do listy dostępnych narzędzi oraz procedurę użycia narzędzia do realizacji zadania.\n"
                    + "Zwróć WYŁĄCZNIE jeden poprawny blok tool_call jako pierwszy krok tej procedury (najczęściej write_file).\n"
                    + "NIE UŻYWAJ narzędzia " + ", ".join(sorted(set(unknown_tools))) + " — ono nie istnieje."
                )

                if self._chat_service.supervisor_service is not None:
                    self._set_actor_state("supervisor", "CORRECTING", "Kastor naprawia nieobsługiwane narzędzie")
                    try:
                        corrected = self._chat_service.supervisor_service.refine(
                            user_message=corrective_prompt,
                            model_answer=current,
                            stage="textual_unknown_tool_corrective",
                        )
                        current = corrected.answer
                        self._enqueue_supervisor_message(
                            stage="textual_unknown_tool_corrective",
                            reason_code=corrected.reason_code,
                            notes=self._merge_supervisor_notes(
                                "Naprawa nieobsługiwanego narzędzia.",
                                corrected.notes,
                            ),
                            answer=current,
                        )
                        self._set_actor_state("supervisor", "READY", "Kastor zakończył naprawę narzędzia")
                        continue
                    except (OllamaClientError, OSError):
                        self._set_actor_state("supervisor", "READY", "Kastor — naprawa narzędzia przerwana błędem")
                        pass

                current = (
                    "```tool_call\n"
                    "{\"tool\":\"list_dir\",\"args\":{\"path\":\".\"},\"intent\":\"fallback_after_unknown_tool\"}"
                    "\n```"
                )
                continue

            followup = (
                "[TOOL_RESULT]\n"
                + json.dumps(aggregated_results, ensure_ascii=False)
                + "\nNa podstawie tego wyniku odpowiedz użytkownikowi."
            )

            try:
                self._set_actor_state("creator", "THINKING", "Polluks analizuje TOOL_RESULT")
                executor_answer = self._ask_executor_with_router_mailbox(followup)
                current = executor_answer
                if self._chat_service.supervisor_service is not None:
                    self._set_actor_state("supervisor", "REVIEWING", "Kastor ocenia odpowiedź po TOOL_RESULT")
                    supervision_result = self._chat_service.supervisor_service.refine(
                        user_message="[TOOL_FLOW]",
                        model_answer=current,
                        stage="tool_flow",
                    )
                    # Validate: do not accept supervisor answer that introduces unsupported tools
                    supervisor_calls = parse_tool_calls(supervision_result.answer)
                    runtime_supported = self._runtime_supported_tool_names()
                    has_unsupported = any(
                        _canonical_tool_name(c.tool) not in runtime_supported
                        for c in supervisor_calls
                    )
                    if has_unsupported:
                        # Supervisor suggested an unsupported tool; keep executor's answer
                        self._log_activity(
                            action="supervisor.tool_flow.rejected",
                            intent="Odrzucono odpowiedź nadzorcy z nieobsługiwanym narzędziem.",
                            details={
                                "rejected_tools": [c.tool for c in supervisor_calls],
                            },
                        )
                    else:
                        current = supervision_result.answer
                    self._enqueue_supervisor_message(
                        stage="tool_flow",
                        reason_code=supervision_result.reason_code,
                        notes=self._merge_supervisor_notes(
                            "Ocena odpowiedzi po TOOL_RESULT.",
                            supervision_result.notes,
                        ),
                        answer=current,
                    )
                    self._set_actor_state("supervisor", "READY", "Kastor zakończył ocenę TOOL_RESULT")
            except (OllamaClientError, OSError) as error:
                self._append_log("user_model_log", f"Błąd kontynuacji po TOOL_RESULT: {error}")
                self._set_actor_state("router", "ERROR", "Błąd kontynuacji po TOOL_RESULT")
                self._set_actor_state("creator", "ERROR", "Polluks przerwał kontynuację po TOOL_RESULT")
                return current
        if parse_tool_calls(current):
            self._append_log(
                "user_model_log",
                (
                    "Ostrzeżenie runtime: osiągnięto limit iteracji resolve_tool_calls, "
                    "pozostał nierozwiązany krok narzędziowy. "
                    "Użyj krótkiego polecenia wtrącającego (np. 'kontynuuj'), aby wznowić cykl."
                ),
            )
            self._set_actor_state("router", "STALLED", "Osiągnięto limit iteracji tool_flow")
            return current
        self._set_actor_state("router", "ACTIVE", "Koniec przebiegu resolve_tool_calls")
        if self._actor_states.get("creator") in {"THINKING", "EXECUTING_TOOL"}:
            self._set_actor_state("creator", "PASSIVE", "Zakończono przebieg tool flow")
        return current


def run_textual_cli(
    *,
    chat_service: ChatService,
    supervisor_dialogue_log_path: Path,
    shell_policy_path: Path = Path("config/shell_allowlist.json"),
    router_mailbox_log_path: Path | None = None,
    activity_logger: ActivityLogger | None = None,
    settings: Settings | None = None,
    agent_registry: AgentRegistry | None = None,
    agent_factory: AgentFactory | None = None,
    task_queue: TaskQueue | None = None,
    work_assigner: WorkAssigner | None = None,
    metrics_collector: MetricsCollector | None = None,
    alert_manager: AlertManager | None = None,
    session_replay: SessionReplay | None = None,
    # Phase 5
    shared_workspace: SharedWorkspace | None = None,
    knowledge_base: KnowledgeBase | None = None,
    cross_memory: CrossAgentMemory | None = None,
    context_window_manager: ContextWindowManager | None = None,
    # Phase 7
    permission_enforcer: PermissionEnforcer | None = None,
    sandbox_manager: SandboxManager | None = None,
    secret_vault: SecretVault | None = None,
    audit_chain: AuditChain | None = None,
    # Phase 6
    workflow_engine: WorkflowEngine | None = None,
    workflow_checkpoint: WorkflowCheckpoint | None = None,
    # Phase 8
    budget_manager: BudgetManager | None = None,
    quota_policy: QuotaPolicy | None = None,
    rate_limiter: RateLimiter | None = None,
    vram_scheduler: VRAMScheduler | None = None,
    # Phase 9
    eval_runner: EvalRunner | None = None,
    benchmark_suite: BenchmarkSuite | None = None,
    regression_detector: RegressionDetector | None = None,
    ab_test_runner: ABTestRunner | None = None,
    human_feedback: HumanFeedbackCollector | None = None,
    # Phase 10
    rest_server: RESTServer | None = None,
    webhook_dispatcher: WebhookDispatcher | None = None,
    plugin_loader: PluginLoader | None = None,
    ci_adapter: CIAdapter | None = None,
    # Phase 11
    team_composer: TeamComposer | None = None,
    skill_catalog: SkillCatalog | None = None,
    dynamic_scaler: DynamicScaler | None = None,
    team_dashboard: TeamDashboard | None = None,
) -> None:
    _AmiagiTextualApp(
        chat_service=chat_service,
        supervisor_dialogue_log_path=supervisor_dialogue_log_path,
        permission_manager=PermissionManager(),
        shell_policy_path=shell_policy_path,
        router_mailbox_log_path=router_mailbox_log_path,
        activity_logger=activity_logger,
        settings=settings,
        agent_registry=agent_registry,
        agent_factory=agent_factory,
        task_queue=task_queue,
        work_assigner=work_assigner,
        metrics_collector=metrics_collector,
        alert_manager=alert_manager,
        session_replay=session_replay,
        shared_workspace=shared_workspace,
        knowledge_base=knowledge_base,
        cross_memory=cross_memory,
        context_window_manager=context_window_manager,
        permission_enforcer=permission_enforcer,
        sandbox_manager=sandbox_manager,
        secret_vault=secret_vault,
        audit_chain=audit_chain,
        workflow_engine=workflow_engine,
        workflow_checkpoint=workflow_checkpoint,
        budget_manager=budget_manager,
        quota_policy=quota_policy,
        rate_limiter=rate_limiter,
        vram_scheduler=vram_scheduler,
        eval_runner=eval_runner,
        benchmark_suite=benchmark_suite,
        regression_detector=regression_detector,
        ab_test_runner=ab_test_runner,
        human_feedback=human_feedback,
        rest_server=rest_server,
        webhook_dispatcher=webhook_dispatcher,
        plugin_loader=plugin_loader,
        ci_adapter=ci_adapter,
        team_composer=team_composer,
        skill_catalog=skill_catalog,
        dynamic_scaler=dynamic_scaler,
        team_dashboard=team_dashboard,
    ).run()
