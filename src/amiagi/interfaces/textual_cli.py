from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from amiagi.application.chat_service import ChatService
from amiagi.application.communication_protocol import (
    is_sponsor_readable,
    panels_for_target,
    parse_addressed_blocks,
    strip_tool_call_blocks,
)
from amiagi.application.shell_policy import default_shell_policy, load_shell_policy
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
from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.dashboard_server import DashboardServer
from amiagi.infrastructure.input_history import InputHistory
from amiagi.infrastructure.metrics_collector import MetricsCollector
from amiagi.infrastructure.script_executor import ScriptExecutor
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
from amiagi.application.event_bus import (
    ActorStateEvent,
    CycleFinishedEvent,
    EventBus,
    LogEvent,
    SupervisorMessageEvent,
)
from amiagi.application.router_engine import RouterEngine
from amiagi.interfaces.shared_cli_helpers import (
    _build_landing_banner,
    _network_resource_for_model,
)
from amiagi.interfaces.permission_manager import PermissionManager
from amiagi.interfaces.textual_commands import TextualCommandsMixin, _CommandOutcome
from amiagi.interfaces.textual_wizard import TextualWizardMixin
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
        ("/energy status", _("help.cmd.energy_status")),
        ("/energy set <#.##>", _("help.cmd.energy_set")),
        ("/energy reset", _("help.cmd.energy_reset")),
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


class _AmiagiTextualApp(TextualCommandsMixin, TextualWizardMixin, App[None]):
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
        autonomous_mode: bool = False,
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
        self._autonomous_mode = autonomous_mode
        if autonomous_mode:
            permission_manager.allow_all = True
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
        self._watchdog_suspended_until_user_input = False
        self._main_thread_id = threading.get_ident()
        self._watchdog_idle_threshold_seconds = _adaptive_watchdog_idle_threshold_seconds(
            activity_log_path=Path("./logs/activity.jsonl"),
            default_seconds=SUPERVISOR_IDLE_THRESHOLD_SECONDS,
        )
        self._last_background_worker: threading.Thread | None = None
        self._actor_states: dict[str, str] = {
            "router": "INIT",
            "creator": "WAITING_INPUT",
            "supervisor": "READY" if chat_service.supervisor_service is not None else "DISABLED",
            "terminal": "WAITING_INPUT",
        }
        self._idle_until_epoch: float | None = None
        self._idle_until_source: str = ""
        self._last_router_event: str = _("router.session_start_event")
        self._unaddressed_turns = 0
        self._reminder_count = 0
        self._consultation_rounds_this_cycle = 0
        try:
            self._shell_policy = load_shell_policy(shell_policy_path)
        except Exception:
            self._shell_policy = default_shell_policy()

        # ---- RouterEngine (Strangler-Fig: tool execution delegated) ----
        self._event_bus = EventBus()
        self._router_engine = RouterEngine(
            chat_service=chat_service,
            permission_manager=permission_manager,
            script_executor=self._script_executor,
            work_dir=self._work_dir,
            shell_policy_path=shell_policy_path,
            event_bus=self._event_bus,
            activity_logger=activity_logger,
            settings=settings,
            autonomous_mode=autonomous_mode,
            router_mailbox_log_path=self._router_mailbox_log_path,
            supervisor_dialogue_log_path=supervisor_dialogue_log_path,
            permission_enforcer=permission_enforcer,
            audit_chain=audit_chain,
        )

        # ---- EventBus subscriptions: bridge engine events → Textual UI ----
        self._event_bus.on(LogEvent, self._on_engine_log)
        self._event_bus.on(ActorStateEvent, self._on_engine_actor_state)
        self._event_bus.on(SupervisorMessageEvent, self._on_engine_supervisor_message)
        self._event_bus.on(CycleFinishedEvent, self._on_engine_cycle_finished)

    # ---- EventBus handlers (bridge RouterEngine → Textual UI) ----

    def _on_engine_log(self, event: LogEvent) -> None:
        """Forward engine log events to the Textual TextArea panel."""
        self._append_log(event.panel, event.message)

    def _on_engine_actor_state(self, event: ActorStateEvent) -> None:
        """Sync engine actor-state changes into Textual status bar."""
        self._set_actor_state(event.actor, event.state, event.event)

    def _on_engine_supervisor_message(self, event: SupervisorMessageEvent) -> None:
        """Route Kastor's addressed blocks to correct UI panels.

        The RouterEngine already handled outbox management (dedup, trim, log).
        Here we only do the UI-specific addressed-block routing.
        """
        panel_map = self._router_engine.comm_rules.panel_mapping or None
        for text_fragment in (event.notes, event.answer):
            if not text_fragment:
                continue
            blocks = parse_addressed_blocks(text_fragment)
            for block in blocks:
                if not block.sender and not block.target:
                    continue
                target_panels = panels_for_target(block.target, panel_map)
                extra_panels = [p for p in target_panels if p != "supervisor_log"]
                if extra_panels:
                    label = f"[{block.sender} -> {block.target}]" if block.sender else ""
                    block_content = block.content
                    if "user_model_log" in extra_panels:
                        sanitized = self._sanitize_block_for_sponsor(block_content, label)
                        if sanitized is None:
                            extra_panels = [p for p in extra_panels if p != "user_model_log"]
                            if not extra_panels:
                                continue
                        else:
                            block_content = sanitized
                    for panel_id in extra_panels:
                        self._append_log(panel_id, f"{label} {block_content}" if label else block_content)

    def _on_engine_cycle_finished(self, event: CycleFinishedEvent) -> None:
        """Handle engine cycle completion — UI refresh + queue drain."""
        if event.event == "quit_requested":
            self.exit()
            return
        if not self._supervisor_notice_shown and self._chat_service.supervisor_service is None:
            self._supervisor_notice_shown = True
        self._render_router_status()

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
        eng = self._router_engine
        idle_for_seconds = max(0.0, time.monotonic() - eng.last_progress_monotonic)
        lines = [
            _("router.actors_header"),
            f"- Router: {self._actor_states.get('router', 'UNKNOWN')}",
            f"- Polluks: {self._actor_states.get('creator', 'UNKNOWN')}",
            f"- Kastor: {self._actor_states.get('supervisor', 'UNKNOWN')}",
            f"- Terminal: {self._actor_states.get('terminal', 'UNKNOWN')}",
            f"Plan pause: {'ON' if eng.plan_pause_active else 'OFF'}",
            f"Decision pending: {'YES' if eng.pending_user_decision else 'NO'}",
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
        if self._router_engine.router_cycle_in_progress:
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

    def _refresh_router_runtime_state(self) -> None:
        eng = self._router_engine
        if eng.router_cycle_in_progress:
            self._render_router_status()
            return
        if self._actor_states.get("terminal") != "WAITING_INPUT":
            self._set_actor_state("terminal", "WAITING_INPUT", "Synchronizacja stanu terminala")
        now = time.monotonic()
        idle_seconds = now - eng.last_progress_monotonic
        creator_state = self._actor_states.get("creator", "")
        if creator_state in {"THINKING", "ANSWER_READY", "EXECUTING_TOOL"} and idle_seconds > 2.0:
            fallback_state = "PASSIVE" if eng.last_model_answer.strip() else "WAITING_INPUT"
            self._set_actor_state("creator", fallback_state, "Korekta stanu po zakończonym cyklu")
        self._render_router_status()

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

    # _handle_cli_like_commands and all _handle_*_command methods
    # are inherited from TextualCommandsMixin (textual_commands.py)

    # _build_wizard_model_list, _format_wizard_model_list,
    # _start_model_selection_wizard, _wizard_*, _sync_agent_model,
    # _persist_model_config, _show_api_usage_bar, _refresh_api_usage_bar
    # are inherited from TextualWizardMixin (textual_wizard.py)


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
            self._router_engine.reset_watchdog_on_user_input()
            self._watchdog_suspended_until_user_input = False
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

        if self._router_engine.pending_user_decision:
            result = self._router_engine.handle_user_decision(text)
            if result is not None:
                self._append_log("user_model_log", result)
                return

        # --- Immediate echo + delegate to engine ---
        self._append_log("user_model_log", f"[Sponsor -> all] Użytkownik: {text}")
        self._router_engine.submit_user_turn(text)

    def _run_supervisor_idle_watchdog(self) -> None:
        """Delegate to RouterEngine (Strangler Fig — Faza 3)."""
        self._router_engine.watchdog_tick()

    def _poll_supervision_dialogue(self) -> None:
        """Delegate to RouterEngine (Strangler Fig — Faza 3)."""
        self._router_engine.poll_supervision_dialogue()


def run_textual_cli(
    *,
    chat_service: ChatService,
    supervisor_dialogue_log_path: Path,
    shell_policy_path: Path = Path("config/shell_allowlist.json"),
    router_mailbox_log_path: Path | None = None,
    activity_logger: ActivityLogger | None = None,
    settings: Settings | None = None,
    autonomous_mode: bool = False,
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
        autonomous_mode=autonomous_mode,
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
