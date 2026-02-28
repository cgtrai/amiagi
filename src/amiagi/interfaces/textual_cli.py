from __future__ import annotations

import collections
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, unquote, urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from amiagi.application.chat_service import ChatService
from amiagi.application.communication_protocol import (
    format_conversation_excerpt,
    is_sponsor_readable,
    load_communication_rules,
    panels_for_target,
    parse_addressed_blocks,
)
from amiagi.application.discussion_sync import extract_dialogue_without_code
from amiagi.application.tool_calling import ToolCall, parse_tool_calls
from amiagi.application.tool_registry import list_registered_tools, resolve_registered_tool_script
from amiagi.application.shell_policy import default_shell_policy, load_shell_policy, parse_and_validate_shell_command
from amiagi.infrastructure.ollama_client import OllamaClientError
from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.script_executor import ScriptExecutor
from amiagi.interfaces.cli import (
    _AMIAGI_LOGO,
    _build_landing_banner,
    _ensure_default_executor_model,
    _fetch_ollama_models,
    _format_user_facing_answer,
    _has_supported_tool_call,
    _is_path_within_work_dir,
    _network_resource_for_model,
    _parse_search_results_from_html,
    _select_executor_model_by_index,
    _resolve_tool_path,
    _read_plan_tracking_snapshot,
    _repair_plan_tracking_file,
)
from amiagi.interfaces.permission_manager import PermissionManager

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Input, Static, TextArea
except (ImportError, ModuleNotFoundError) as error:  # pragma: no cover - runtime import guard
    raise RuntimeError(
        "Tryb textual wymaga biblioteki 'textual'. Zainstaluj zależności runtime."
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

_TEXTUAL_HELP_COMMANDS: list[tuple[str, str]] = [
    ("/help", "pokaż dostępne komendy"),
    ("/cls", "wyczyść ekran główny (panel użytkownika)"),
    ("/cls all", "wyczyść wszystkie panele"),
    ("/models current", "pokaż aktualnie aktywny model dla Polluksa"),
    ("/models show", "pokaż modele dostępne w Ollama (1..x)"),
    ("/models chose <nr>", "wybierz model dla Polluksa po numerze z /models show"),
    ("/permissions", "pokaż aktualny tryb zgód"),
    ("/permissions all", "włącz globalną zgodę na zasoby"),
    ("/permissions ask", "wyłącz globalną zgodę (blokuj akcje wymagające zasobów)"),
    ("/permissions reset", "wyczyść zapamiętane zgody per zasób"),
    ("/queue-status", "pokaż stan kolejki modeli i decyzji polityki VRAM"),
    ("/capabilities [--network]", "pokaż gotowość narzędzi i backendów"),
    ("/show-system-context [tekst]", "pokaż kontekst systemowy przekazywany do modelu"),
    ("/goal-status", "pokaż cel główny i etap z notes/main_plan.json"),
    ("/goal", "alias: pokaż cel główny i etap"),
    ("/router-status", "pokaż status aktorów i okna IDLE"),
    ("/idle-until <ISO8601|off>", "ustaw/wyczyść planowane IDLE watchdoga"),
    ("/history [n]", "pokaż ostatnie wiadomości (domyślnie 10)"),
    ("/remember <tekst>", "zapisz notatkę do pamięci"),
    ("/memories [zapytanie]", "przeszukaj pamięć"),
    ("/import-dialog [plik]", "zapisz dialog (bez kodu) jako kontekst pamięci"),
    ("/create-python <plik> <opis>", "wygeneruj i zapisz skrypt Python przez model"),
    ("/run-python <plik> [arg ...]", "uruchom skrypt Python z argumentami"),
    ("/run-shell <polecenie>", "uruchom polecenie shell z polityką whitelist"),
    ("/bye", "zapisz podsumowanie sesji i zakończ"),
    ("/quit", "zakończ tryb textual"),
    ("/exit", "zakończ tryb textual"),
]


def _build_textual_help_text() -> str:
    command_width = max(len(command) for command, _ in _TEXTUAL_HELP_COMMANDS)
    lines = ["Komendy (textual):"]
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
        return False, "Brak treści do skopiowania."

    timeout_seconds = 0.35

    if os.environ.get("WAYLAND_DISPLAY"):
        wl_copy = shutil.which("wl-copy")
        if wl_copy is None:
            return False, "Brak narzędzia wl-copy (Wayland)."
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
            return False, "Przekroczono limit czasu kopiowania przez wl-copy."
        if completed.returncode == 0:
            return True, "Schowek systemowy (Wayland / wl-copy)."
        return False, "Nie udało się skopiować przez wl-copy."

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
                return False, "Przekroczono limit czasu kopiowania przez xclip."
            if completed.returncode == 0:
                return True, "Schowek systemowy (X11 / xclip)."

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
                return False, "Przekroczono limit czasu kopiowania przez xsel."
            if completed.returncode == 0:
                return True, "Schowek systemowy (X11 / xsel)."

        return False, "Brak narzędzia do schowka X11 (zainstaluj xclip albo xsel)."

    return False, "Nie wykryto środowiska schowka (WAYLAND_DISPLAY/DISPLAY)."


def _handle_textual_command(raw: str, permission_manager: PermissionLike) -> _CommandOutcome:
    command = raw.strip().lower()
    if command in {"/quit", "/exit"}:
        return _CommandOutcome(handled=True, messages=[], should_exit=True)

    if command == "/help":
        return _CommandOutcome(handled=True, messages=[TEXTUAL_HELP_TEXT])

    if command.startswith("/permissions"):
        parts = command.split()
        action = parts[1] if len(parts) > 1 else "status"

        if action in {"status", "show"}:
            granted_once_count = len(getattr(permission_manager, "granted_once", set()))
            return _CommandOutcome(
                handled=True,
                messages=[
                    "--- PERMISSIONS ---",
                    f"allow_all: {bool(getattr(permission_manager, 'allow_all', False))}",
                    f"granted_once_count: {granted_once_count}",
                ],
            )

        if action in {"all", "on", "global"}:
            permission_manager.allow_all = True
            return _CommandOutcome(
                handled=True,
                messages=["Włączono globalną zgodę na zasoby."],
            )

        if action in {"ask", "off", "interactive"}:
            permission_manager.allow_all = False
            return _CommandOutcome(
                handled=True,
                messages=[
                    "Włączono tryb pytań o zgodę per zasób.",
                    "W trybie textual zgoda interakcyjna nie jest wyświetlana; użyj /permissions all, aby wysyłać zapytania do modelu.",
                ],
            )

        if action in {"reset", "clear"}:
            granted_once = getattr(permission_manager, "granted_once", None)
            if isinstance(granted_once, set):
                granted_once.clear()
                return _CommandOutcome(
                    handled=True,
                    messages=["Wyczyszczono zapamiętane zgody per zasób."],
                )
            return _CommandOutcome(
                handled=True,
                messages=["Brak zapamiętanych zgód do wyczyszczenia."],
            )

        return _CommandOutcome(
            handled=True,
            messages=["Użycie: /permissions [status|all|ask|reset]"],
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
        ("ctrl+c", "copy_selection", "Kopiuj zaznaczenie"),
        ("ctrl+shift+c", "copy_selection", "Kopiuj zaznaczenie"),
        ("ctrl+q", "quit", "Wyjście"),
    ]

    CSS = """
    Screen { layout: horizontal; }
    #main_column { width: 60%; height: 100%; layout: vertical; }
    #tech_column { width: 40%; height: 100%; layout: vertical; }
    #user_model_log { height: 1fr; border: round #4ea1ff; }
    #busy_indicator { height: 3; border: round #9a6bff; padding: 0 1; }
    #input_box { dock: bottom; }
    #router_status { height: 8; border: round #9a6bff; }
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
    ) -> None:
        super().__init__()
        self._chat_service = chat_service
        self._supervisor_dialogue_log_path = supervisor_dialogue_log_path
        self._permission_manager = permission_manager
        self._shell_policy_path = shell_policy_path
        self._dialogue_log_offset = 0
        self._router_mailbox_log_path = router_mailbox_log_path or Path("./logs/router_mailbox.jsonl")
        self._activity_logger = activity_logger
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
        self._last_router_event: str = "Uruchomienie sesji"
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
                yield Static("Użytkownik ↔ Polluks", classes="title")
                yield TextArea("", id="user_model_log", read_only=True, show_line_numbers=False)
                yield Static("Status modelu: READY · możesz pisać", id="busy_indicator")
                yield Input(placeholder="Wpisz polecenie i Enter (/quit aby wyjść)", id="input_box")
            with Vertical(id="tech_column"):
                yield Static("Router", classes="title")
                yield Static("", id="router_status")
                yield Static("Kastor → Router", classes="title")
                yield TextArea("", id="supervisor_log", read_only=True, show_line_numbers=False)
                yield Static("Polluks → Kastor", classes="title")
                yield TextArea("", id="executor_log", read_only=True, show_line_numbers=False)

    def _format_idle_until(self) -> str:
        if self._idle_until_epoch is None:
            return "brak"
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
            "Aktorzy:",
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
            indicator.update("Status modelu: BUSY · trwa wykonywanie kroku")
            return
        indicator.update("Status modelu: READY · możesz pisać")

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
        return "Jestem Polluks, modelem wykonawczym frameworka amiagi."

    def _single_sentence(self, text: str) -> str:
        compact = " ".join(text.strip().split())
        if not compact:
            return "Jestem Polluks, modelem wykonawczym frameworka amiagi."
        for idx, char in enumerate(compact):
            if char in ".!?":
                sentence = compact[: idx + 1].strip()
                return sentence or "Jestem Polluks, modelem wykonawczym frameworka amiagi."
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
            " Czy chcesz, żebym kontynuował plan, przerwał go, czy przygotował nowe zadanie?"
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
        self._supervisor_outbox.append(
            {
                "actor": "Kastor",
                "target": "Polluks",
                "stage": stage,
                "reason_code": reason_code,
                "notes": notes[:500],
                "suggested_step": suggested_step,
            }
        )
        if len(self._supervisor_outbox) > 10:
            del self._supervisor_outbox[:-10]
        self._append_router_mailbox_log(
            "enqueue",
            {
                "stage": stage,
                "reason_code": reason_code,
                "notes": notes[:500],
                "suggested_step": suggested_step,
            },
        )

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
                    for panel_id in extra_panels:
                        self._append_log(panel_id, f"{label} {block.content}" if label else block.content)

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
                copied, details = _copy_to_system_clipboard(text)
                if copied:
                    self.notify(f"Skopiowano do schowka ({details}).")
                else:
                    self.copy_to_clipboard(text)
                    self.notify(
                        "Skopiowano przez tryb terminalowy (OSC52). "
                        f"Szczegóły środowiska: {details}",
                        severity="information",
                    )
                return
        self.notify(
            "Brak zaznaczonej treści do skopiowania. Kliknij w okno logu i zaznacz tekst.",
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
            (
                f"Odmowa: {reason} (zasób: {resource}). "
                "W trybie textual użyj /permissions all, aby odblokować operacje."
            ),
        )
        return False

    def _handle_cli_like_commands(self, raw_text: str) -> _CommandOutcome:
        text = raw_text.strip()
        lower = text.lower()

        if lower == "/cls":
            self._clear_textual_panels(clear_all=False)
            self.notify("Wyczyszczono ekran główny.", severity="information")
            return _CommandOutcome(True, [])

        if lower == "/cls all":
            self._clear_textual_panels(clear_all=True)
            self.notify("Wyczyszczono wszystkie panele.", severity="information")
            return _CommandOutcome(True, [])

        if lower.startswith("/models"):
            parts = text.split()
            if len(parts) < 2:
                return _CommandOutcome(True, ["Użycie: /models show | /models chose <nr>"])

            action = parts[1].lower()
            if action == "current":
                current_model = str(getattr(self._chat_service.ollama_client, "model", ""))
                return _CommandOutcome(True, [f"Aktywny model wykonawczy: {current_model}"])
            if action == "show":
                models, error = _fetch_ollama_models(self._chat_service)
                if error is not None:
                    return _CommandOutcome(True, [f"Nie udało się pobrać listy modeli: {error}"])
                if not models:
                    return _CommandOutcome(True, ["Brak modeli dostępnych w Ollama."])

                current_model = str(getattr(self._chat_service.ollama_client, "model", ""))
                messages = ["--- MODELE OLLAMA ---"]
                for index, model_name in enumerate(models, start=1):
                    marker = "  [aktywny]" if model_name == current_model else ""
                    messages.append(f"{index}. {model_name}{marker}")
                messages.append("Użycie: /models chose <nr>")
                return _CommandOutcome(True, messages)

            if action in {"chose", "choose"}:
                if len(parts) < 3:
                    return _CommandOutcome(True, ["Użycie: /models chose <nr>"])
                try:
                    index = int(parts[2])
                except ValueError:
                    return _CommandOutcome(
                        True,
                        ["Nieprawidłowy numer modelu. Użyj wartości całkowitej, np. /models chose 1"],
                    )

                ok, payload, _models = _select_executor_model_by_index(self._chat_service, index)
                if not ok:
                    return _CommandOutcome(True, [payload])
                return _CommandOutcome(True, [f"Aktywny model wykonawczy: {payload}"])

            return _CommandOutcome(True, ["Użycie: /models show | /models chose <nr>"])

        if lower == "/router-status":
            return _CommandOutcome(
                True,
                [
                    "--- ROUTER STATUS ---",
                    f"Router: {self._actor_states.get('router', 'UNKNOWN')}",
                    f"Polluks: {self._actor_states.get('creator', 'UNKNOWN')}",
                    f"Kastor: {self._actor_states.get('supervisor', 'UNKNOWN')}",
                    f"Terminal: {self._actor_states.get('terminal', 'UNKNOWN')}",
                    f"plan_pause: {'ON' if self._plan_pause_active else 'OFF'}",
                    f"pending_decision: {'YES' if self._pending_user_decision else 'NO'}",
                    f"IDLE until: {self._format_idle_until()}",
                    f"Ostatnie zdarzenie: {self._last_router_event}",
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
                        "Niepoprawny format. Użyj: /idle-until 2026-02-27T23:15:00Z lub /idle-until off",
                    ],
                )

            self._set_idle_until(parsed, source="terminal_command")
            if parsed is None:
                return _CommandOutcome(True, ["Wyczyszczono zaplanowane okno IDLE."])
            return _CommandOutcome(True, [f"Ustawiono IDLE until: {self._format_idle_until()}"])

        if lower == "/queue-status":
            policy = self._chat_service.ollama_client.queue_policy
            vram_advisor = self._chat_service.ollama_client.vram_advisor
            messages = []
            if policy is None:
                return _CommandOutcome(True, ["Polityka kolejki modeli jest wyłączona."])

            snapshot = policy.snapshot()
            messages.append("--- MODEL QUEUE STATUS ---")
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
                messages.append("vram: brak aktywnego doradcy VRAM")
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
            return _CommandOutcome(True, ["--- CAPABILITIES ---", json.dumps(capabilities, ensure_ascii=False, indent=2)])

        if lower.startswith("/show-system-context"):
            parts = text.split(maxsplit=1)
            sample_message = parts[1].strip() if len(parts) == 2 else "kontekst diagnostyczny"
            prompt = self._chat_service.build_system_prompt(sample_message)
            return _CommandOutcome(True, ["--- SYSTEM CONTEXT ---", prompt])

        if lower in {"/goal-status", "/goal"}:
            snapshot = _read_plan_tracking_snapshot(self._work_dir)
            repair_info: dict | None = None
            if snapshot.get("parse_error"):
                repair_info = _repair_plan_tracking_file(self._work_dir)
                snapshot = _read_plan_tracking_snapshot(self._work_dir)

            messages = [
                "--- GOAL STATUS ---",
                f"path: {snapshot.get('path')}",
                f"exists: {snapshot.get('exists')}",
                f"goal: {snapshot.get('goal', '')}",
                f"current_stage: {snapshot.get('current_stage', '')}",
                f"tasks: {snapshot.get('tasks_done', 0)}/{snapshot.get('tasks_total', 0)} zakończonych",
            ]
            if snapshot.get("parse_error"):
                messages.append("parse_error: true")
            if repair_info and repair_info.get("repaired"):
                messages.append("auto_repair: true")
                if repair_info.get("backup_path"):
                    messages.append(f"backup_path: {repair_info.get('backup_path')}")
            return _CommandOutcome(True, messages)

        if lower.startswith("/import-dialog"):
            if not self._ensure_resource("disk.read", "Import dialogu wymaga odczytu pliku z dysku"):
                return _CommandOutcome(True, [])

            parts = text.split(maxsplit=1)
            path = Path(parts[1].strip()) if len(parts) == 2 else Path("początkowe_konsultacje.md")
            if not path.exists():
                return _CommandOutcome(True, [f"Nie znaleziono pliku: {path}"])

            dialogue = extract_dialogue_without_code(path.read_text(encoding="utf-8"))
            self._chat_service.save_discussion_context(dialogue)
            return _CommandOutcome(True, ["Zapisano treść dialogu (bez kodu) do pamięci."])

        if lower.startswith("/create-python"):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                return _CommandOutcome(True, ["Użycie: /create-python <plik> <opis>"])

            network_resource = _network_resource_for_model(self._chat_service.ollama_client.base_url)
            if not self._ensure_resource(
                network_resource,
                "Połączenie z modelem wymaga zasobu sieciowego",
            ):
                return _CommandOutcome(True, [])
            if not self._ensure_resource("disk.write", "Zapis skryptu wymaga dostępu do dysku"):
                return _CommandOutcome(True, [])

            output_path = Path(parts[1].strip())
            description = parts[2].strip()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            code = self._chat_service.generate_python_code(description)
            output_path.write_text(code + "\n", encoding="utf-8")
            return _CommandOutcome(True, [f"Zapisano skrypt: {output_path}"])

        if lower.startswith("/run-python"):
            parts = shlex.split(text)
            if len(parts) < 2:
                return _CommandOutcome(True, ["Użycie: /run-python <plik> [arg ...]"])

            if not self._ensure_resource("disk.read", "Uruchomienie skryptu wymaga odczytu pliku"):
                return _CommandOutcome(True, [])
            if not self._ensure_resource("process.exec", "Uruchomienie skryptu wymaga wykonania procesu"):
                return _CommandOutcome(True, [])

            script_path = Path(parts[1])
            script_args = parts[2:]
            if not script_path.exists():
                return _CommandOutcome(True, [f"Nie znaleziono skryptu: {script_path}"])

            result = self._script_executor.execute_python(script_path, script_args)
            messages = [f"Polecenie: {' '.join(result.command)}", f"Kod wyjścia: {result.exit_code}"]
            if result.stdout.strip():
                messages.extend(["--- STDOUT ---", result.stdout])
            if result.stderr.strip():
                messages.extend(["--- STDERR ---", result.stderr])
            return _CommandOutcome(True, messages)

        if lower.startswith("/run-shell"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return _CommandOutcome(True, ["Użycie: /run-shell <polecenie>"])

            command_text = parts[1].strip()
            _, validation_error = parse_and_validate_shell_command(command_text, self._shell_policy)
            if validation_error is not None:
                return _CommandOutcome(True, [f"Odrzucono polecenie: {validation_error}"])

            if not self._ensure_resource("process.exec", "Uruchomienie shell wymaga wykonania procesu"):
                return _CommandOutcome(True, [])

            result = self._script_executor.execute_shell(command_text)
            messages = [f"Polecenie: {' '.join(result.command)}", f"Kod wyjścia: {result.exit_code}"]
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
                return _CommandOutcome(True, ["Brak historii."])
            rendered = [
                f"[{message.created_at.isoformat(timespec='seconds')}] {message.role}: {message.content}"
                for message in messages
            ]
            return _CommandOutcome(True, rendered)

        if lower.startswith("/remember"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                return _CommandOutcome(True, ["Użycie: /remember <tekst>"])
            self._chat_service.remember(parts[1].strip())
            return _CommandOutcome(True, ["Zapisano notatkę."])

        if lower.startswith("/memories"):
            parts = text.split(maxsplit=1)
            query = parts[1].strip() if len(parts) == 2 else None
            records = self._chat_service.memory_repository.search_memories(query=query, limit=20)
            if not records:
                return _CommandOutcome(True, ["Brak wyników."])
            rendered = [
                f"[{record.created_at.isoformat(timespec='seconds')}] {record.kind}/{record.source}: {record.content}"
                for record in records
            ]
            return _CommandOutcome(True, rendered)

        if lower == "/bye":
            network_resource = _network_resource_for_model(self._chat_service.ollama_client.base_url)
            if not self._ensure_resource(
                network_resource,
                "Podsumowanie sesji wymaga dostępu sieciowego do modelu",
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
                    "Zapisano podsumowanie sesji do kontynuacji po restarcie.",
                    "--- START POINT ---",
                    summary,
                    "Do zobaczenia.",
                ],
                should_exit=True,
            )

        return _CommandOutcome(False, [])

    def on_mount(self) -> None:
        self._set_actor_state("router", "ACTIVE", "Inicjalizacja panelu statusu")

        # --- Auto-select default executor model (silent) ---
        _ensure_default_executor_model(self._chat_service)

        # --- Landing page ---
        banner = _build_landing_banner(mode="textual")
        self._append_log("user_model_log", banner)

        self._append_log("executor_log", "Oczekiwanie na odpowiedź modelu wykonawczego.")
        if self._chat_service.supervisor_service is None:
            self._append_log(
                "supervisor_log",
                "Kastor jest nieaktywny w tej sesji (brak supervisor_service).",
            )
            self._supervisor_notice_shown = True
        else:
            self._append_log("supervisor_log", "Oczekiwanie na wpisy Kastora.")
        self.set_focus(self.query_one("#input_box", Input))
        self.set_interval(SUPERVISION_POLL_INTERVAL_SECONDS, self._poll_supervision_dialogue)
        self.set_interval(SUPERVISOR_WATCHDOG_INTERVAL_SECONDS, self._run_supervisor_idle_watchdog)
        self.set_interval(1.0, self._refresh_router_runtime_state)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return

        if self._watchdog_suspended_until_user_input:
            self._watchdog_suspended_until_user_input = False
            self._watchdog_attempts = 0
            self._watchdog_capped_notified = False
            self._last_watchdog_cap_autonudge_monotonic = 0.0
            self._append_log(
                "supervisor_log",
                "Watchdog Kastora został ponownie aktywowany po nowej wiadomości użytkownika.",
            )

        command_outcome = _handle_textual_command(text, self._permission_manager)
        if command_outcome.handled:
            for message in command_outcome.messages:
                self._append_log("user_model_log", message)
            if command_outcome.should_exit:
                self.exit()
            return

        cli_like_outcome = self._handle_cli_like_commands(text)
        if cli_like_outcome.handled:
            for message in cli_like_outcome.messages:
                self._append_log("user_model_log", message)
            if cli_like_outcome.should_exit:
                self.exit()
            return

        if self._pending_user_decision:
            decision = self._extract_pause_decision(text)
            if decision == "continue":
                self._record_collaboration_signal("cooperate", {"decision": "continue"})
                self._append_log("user_model_log", "Wznawiam plan i kontynuuję pracę.")
                self._last_progress_monotonic = time.monotonic()
                self._auto_resume_paused_plan_if_needed(time.monotonic(), force=True)
                return
            if decision == "stop":
                self._pending_user_decision = False
                self._pending_decision_identity_query = False
                self._set_plan_paused(paused=False, reason="user_stop", source="user")
                self._record_collaboration_signal("user_stopped_plan", {"decision": "stop"})
                self._append_log("user_model_log", "Plan został przerwany. Podaj nowe zadanie, abym utworzył nowy plan.")
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
                self._append_log("user_model_log", "Utworzyłem nowy plan. Możesz wpisać kolejne polecenie, a rozpocznę realizację.")
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
                for panel_id in target_panels:
                    self._append_log(panel_id, f"{label} {block.content}" if label else block.content)
                if "user_model_log" in target_panels:
                    routed_to_user_panel = True

                # Sponsor readability check
                if block.target in ("Sponsor", "all") and not is_sponsor_readable(block.content):
                    self._append_log(
                        "supervisor_log",
                        f"[Koordynator] Uwaga: treść kierowana do Sponsora zawiera surowy JSON/markup — proszę przeformułuj.",
                    )

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
                        self._append_log("supervisor_log", "[Kastor] Błąd konsultacji — pomijam.")
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
                "Kastor jest nieaktywny; panel pokazuje tylko komunikaty techniczne.",
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
                "Watchdog Kastora wstrzymany po błędzie nadzorcy; oczekuję nowej wiadomości użytkownika.",
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
                            for poll_panel_id in poll_extra:
                                self._append_log(poll_panel_id, f"{poll_label} {poll_block.content}" if poll_label else poll_block.content)

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

            corrective_prompt = (
                "[RUNTIME_SUPERVISION_CONTEXT]\n"
                + json.dumps(supervision_context, ensure_ascii=False)
                + "\n[/RUNTIME_SUPERVISION_CONTEXT]\n"
                + corrective_instruction
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

    def _execute_tool_call(self, tool_call: ToolCall) -> dict:
        tool = tool_call.tool.strip()
        if tool == "run_command":
            tool = "run_shell"
        args = tool_call.args

        if tool == "read_file":
            if not self._ensure_resource("disk.read", "Tool read_file wymaga odczytu pliku"):
                return {"ok": False, "error": "permission_denied:disk.read"}
            path = self._resolve_model_path(str(args.get("path", "")))
            max_chars = int(args.get("max_chars", 12000))
            if not path.exists() or not path.is_file():
                return {"ok": False, "error": "file_not_found", "path": str(path)}
            try:
                content = path.read_text(encoding="utf-8")
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(path)}
            return {
                "ok": True,
                "tool": "read_file",
                "path": str(path),
                "content": content[:max_chars],
                "truncated": len(content) > max_chars,
                "total_chars": len(content),
            }

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
            _, validation_error = parse_and_validate_shell_command(command_text, self._shell_policy)
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
            return {
                "ok": True,
                "tool": "fetch_web",
                "url": url,
                "content": content[:max_chars],
                "truncated": len(content) > max_chars,
                "total_chars": len(content),
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
                current = self._ask_executor_with_router_mailbox(followup)
                if self._chat_service.supervisor_service is not None:
                    self._set_actor_state("supervisor", "REVIEWING", "Kastor ocenia odpowiedź po TOOL_RESULT")
                    supervision_result = self._chat_service.supervisor_service.refine(
                        user_message="[TOOL_FLOW]",
                        model_answer=current,
                        stage="tool_flow",
                    )
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
) -> None:
    _AmiagiTextualApp(
        chat_service=chat_service,
        supervisor_dialogue_log_path=supervisor_dialogue_log_path,
        permission_manager=PermissionManager(),
        shell_policy_path=shell_policy_path,
        router_mailbox_log_path=router_mailbox_log_path,
        activity_logger=activity_logger,
    ).run()
