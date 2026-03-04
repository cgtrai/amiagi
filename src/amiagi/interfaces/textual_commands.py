"""Slash command handlers for the Textual TUI adapter (extracted mixin).

Part of the v1.0.3 Strangler Fig migration — Faza 5.2 dead code / LOC reduction.
Extracts ~1650 LOC of command-handling methods from textual_cli.py into a
reusable mixin class, keeping the main adapter file focused on UI and composition.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import threading
import webbrowser
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

from amiagi.application.agent_wizard import AgentWizardService
from amiagi.application.discussion_sync import extract_dialogue_without_code
from amiagi.application.shell_policy import parse_and_validate_shell_command
from amiagi.domain.agent import AgentState
from amiagi.domain.task import Task, TaskPriority
from amiagi.infrastructure.openai_client import (
    OpenAIClient,
    SUPPORTED_OPENAI_MODELS,
    mask_api_key,
)
from amiagi.infrastructure.dashboard_server import DashboardServer
from amiagi.interfaces.shared_cli_helpers import (
    _network_resource_for_model,
    _read_plan_tracking_snapshot,
    _repair_plan_tracking_file,
    _select_executor_model_by_index,
)
from amiagi.i18n import _

if TYPE_CHECKING:
    from amiagi.application.ab_test_runner import ABTestRunner
    from amiagi.application.agent_factory import AgentFactory
    from amiagi.application.agent_registry import AgentRegistry
    from amiagi.application.alert_manager import AlertManager
    from amiagi.application.audit_chain import AuditChain
    from amiagi.application.budget_manager import BudgetManager
    from amiagi.application.chat_service import ChatService
    from amiagi.application.dynamic_scaler import DynamicScaler
    from amiagi.application.eval_runner import EvalRunner
    from amiagi.application.plugin_loader import PluginLoader
    from amiagi.application.regression_detector import RegressionDetector
    from amiagi.application.router_engine import RouterEngine
    from amiagi.application.shell_policy import ShellPolicy
    from amiagi.application.task_queue import TaskQueue
    from amiagi.application.team_composer import TeamComposer
    from amiagi.application.workflow_engine import WorkflowEngine
    from amiagi.config import Settings
    from amiagi.domain.quota_policy import QuotaPolicy
    from amiagi.infrastructure.activity_logger import ActivityLogger
    from amiagi.infrastructure.benchmark_suite import BenchmarkSuite
    from amiagi.infrastructure.knowledge_base import KnowledgeBase
    from amiagi.infrastructure.metrics_collector import MetricsCollector
    from amiagi.infrastructure.rest_server import RESTServer
    from amiagi.infrastructure.sandbox_manager import SandboxManager
    from amiagi.infrastructure.script_executor import ScriptExecutor
    from amiagi.infrastructure.session_replay import SessionReplay
    from amiagi.infrastructure.shared_workspace import SharedWorkspace
    from amiagi.infrastructure.usage_tracker import UsageTracker
    from amiagi.interfaces.human_feedback import HumanFeedbackCollector
    from amiagi.interfaces.team_dashboard import TeamDashboard


@dataclass
class _CommandOutcome:
    handled: bool
    messages: list[str]
    should_exit: bool = False


class TextualCommandsMixin:
    """Mixin providing slash command handlers for ``_AmiagiTextualApp``.

    All methods access services and state via ``self``, which at runtime
    is the full ``_AmiagiTextualApp`` instance that also inherits from
    ``TextualWizardMixin`` and ``App[None]``.
    """

    # -- Type stubs so Pylance can resolve attributes from the host class --
    if TYPE_CHECKING:
        _chat_service: ChatService
        _router_engine: RouterEngine
        _settings: Settings | None
        _activity_logger: ActivityLogger | None
        _usage_tracker: UsageTracker
        _script_executor: ScriptExecutor
        _work_dir: Path
        _actor_states: dict[str, str]
        _last_router_event: str
        _shell_policy: ShellPolicy
        _unaddressed_turns: int
        _agent_registry: AgentRegistry | None
        _agent_factory: AgentFactory | None
        _task_queue: TaskQueue | None
        _rest_server: RESTServer | None
        _metrics_collector: MetricsCollector | None
        _alert_manager: AlertManager | None
        _session_replay: SessionReplay | None
        _budget_manager: BudgetManager | None
        _team_dashboard: TeamDashboard | None
        _knowledge_base: KnowledgeBase | None
        _shared_workspace: SharedWorkspace | None
        _audit_chain: AuditChain | None
        _sandbox_manager: SandboxManager | None
        _workflow_engine: WorkflowEngine | None
        _quota_policy: QuotaPolicy | None
        _eval_runner: EvalRunner | None
        _benchmark_suite: BenchmarkSuite | None
        _ab_test_runner: ABTestRunner | None
        _regression_detector: RegressionDetector | None
        _human_feedback: HumanFeedbackCollector | None
        _plugin_loader: PluginLoader | None
        _team_composer: TeamComposer | None
        _dynamic_scaler: DynamicScaler | None

        # Methods from _AmiagiTextualApp
        def notify(self, message: str, *, title: str = "", severity: str = "information", timeout: float | None = None) -> None: ...
        def _clear_textual_panels(self, *, clear_all: bool) -> None: ...
        def _format_idle_until(self) -> str: ...
        def _parse_idle_until(self, raw_value: str) -> float | None: ...
        def _set_idle_until(self, idle_until_epoch: float | None, source: str) -> None: ...
        def _ensure_resource(self, resource: str, reason: str) -> bool: ...

        # Methods from TextualWizardMixin
        def _build_wizard_model_list(self) -> list[tuple[str, str]]: ...
        def _format_wizard_model_list(self, models: list[tuple[str, str]], *, default_name: str = "") -> str: ...
        def _sync_agent_model(self, agent_id: str, model_name: str, source: str = "ollama") -> None: ...
        def _persist_model_config(self) -> None: ...
        def _show_api_usage_bar(self) -> None: ...

    # ------------------------------------------------------------------
    # Central CLI-like command dispatcher
    # ------------------------------------------------------------------

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
                    f"plan_pause: {'ON' if self._router_engine.plan_pause_active else 'OFF'}",
                    f"pending_decision: {'YES' if self._router_engine.pending_user_decision else 'NO'}",
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
                "passive_turns": self._router_engine.passive_turns,
                "supervisor_outbox_size": self._router_engine.supervisor_outbox_size,
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
        # Phase 8 — /budget, /quota, /energy commands
        # ==================================================================
        if lower.startswith("/budget"):
            return self._handle_budget_command(text)
        if lower.startswith("/quota"):
            return self._handle_quota_command(text)
        if lower.startswith("/energy") or lower.startswith("/koszt"):
            return self._handle_energy_command(text)

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

            static_dir = Path(__file__).resolve().parent / "dashboard_static"
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

    def _handle_energy_command(self, raw_text: str) -> _CommandOutcome:
        """Handle ``/energy`` (EN) and ``/koszt`` (PL) commands."""
        tracker = getattr(self._chat_service, "energy_tracker", None)
        if tracker is None:
            return _CommandOutcome(True, [_("energy.inactive")])

        parts = raw_text.strip().split()
        # Normalise: '/koszt energii set 0.85' -> action='set', or '/energy set 0.85'
        action = ""
        value_idx = 2
        if len(parts) > 1:
            second = parts[1].lower()
            if second == "energii" and len(parts) > 2:
                action = parts[2].lower()
                value_idx = 3
            else:
                action = second
        if not action:
            action = "status"

        if action in {"set", "ustaw"}:
            if len(parts) <= value_idx:
                return _CommandOutcome(True, [_("energy.set_usage")])
            try:
                price = float(parts[value_idx])
            except ValueError:
                return _CommandOutcome(True, [_("energy.set_usage")])
            currency = parts[value_idx + 1].upper() if len(parts) > value_idx + 1 else ""
            tracker.set_price_per_kwh(price, currency)
            cur = tracker.currency
            return _CommandOutcome(True, [
                _("energy.set_done", price=f"{price:.2f}", currency=cur),
            ])

        if action == "reset":
            tracker.reset()
            return _CommandOutcome(True, [_("energy.reset_done")])

        # status (default)
        s = tracker.summary()
        if s.total_requests == 0 and s.price_per_kwh == 0:
            return _CommandOutcome(True, [_("energy.no_data")])

        msgs = [_("energy.header")]
        gpu_info = f"{s.gpu_power_limit_w:.0f} W" if s.gpu_power_limit_w else _("energy.gpu_unknown")
        msgs.append(_("energy.gpu_tdp", tdp=gpu_info))
        msgs.append(_("energy.price", price=f"{s.price_per_kwh:.2f}", currency=s.currency))
        msgs.append(_("energy.requests", n=str(s.total_requests)))
        msgs.append(_("energy.inference_time", seconds=f"{s.total_inference_seconds:.1f}"))
        avg_draw = f"{s.avg_power_draw_w:.1f} W" if s.avg_power_draw_w else "—"
        msgs.append(_("energy.avg_power", watts=avg_draw))
        msgs.append(_("energy.total_energy", wh=f"{s.total_energy_wh:.4f}"))
        if s.price_per_kwh > 0:
            msgs.append(_("energy.total_cost", cost=f"{s.total_cost_local:.6f}", currency=s.currency))
        else:
            msgs.append(_("energy.no_price_set"))
        return _CommandOutcome(True, msgs)

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
            import shutil as _shutil
            from pathlib import Path as _Path
            src = _Path(plugin_path)
            if not src.exists():
                return _CommandOutcome(True, [_("plugins.src_missing", path=plugin_path)])
            plugins_dir = _Path("plugins")
            plugins_dir.mkdir(exist_ok=True)
            dest = plugins_dir / src.name
            _shutil.copy2(src, dest)
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
