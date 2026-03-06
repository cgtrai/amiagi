"""RouterEngine — UI-independent orchestration core for amiagi.

This module owns all routing logic that was previously duplicated across
``textual_cli.py`` and ``cli.py``.  Presentation adapters communicate via
the :class:`EventBus` — they subscribe to events and call the public API
methods to drive the engine.

Migration is incremental (Strangler-Fig):  methods start as stubs that
adapters bypass; once a method is fully extracted the adapters delegate to it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from amiagi.application.communication_protocol import (
    CommunicationRules,
    format_conversation_excerpt,
    is_sponsor_readable,
    load_communication_rules,
    panels_for_target,
    parse_addressed_blocks,
    strip_tool_call_blocks,
)
from amiagi.application.event_bus import (
    ActorStateEvent,
    CycleFinishedEvent,
    ErrorEvent,
    EventBus,
    LogEvent,
    SupervisorMessageEvent,
)
from amiagi.application.shell_policy import (
    default_shell_policy,
    load_shell_policy,
    parse_and_validate_shell_command,
)
from amiagi.application.tool_calling import ToolCall, parse_tool_calls
from amiagi.application.tool_helpers import (
    is_path_within_work_dir,
    parse_search_results_from_html,
    resolve_tool_path,
)
from amiagi.application.tool_registry import list_registered_tools, resolve_registered_tool_script
from amiagi.i18n import _
from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.ollama_client import OllamaClientError
from amiagi.infrastructure.script_executor import ScriptExecutor

if TYPE_CHECKING:
    from amiagi.application.audit_chain import AuditChain
    from amiagi.application.chat_service import ChatService
    from amiagi.application.permission_enforcer import PermissionEnforcer
    from amiagi.config import Settings
    from amiagi.interfaces.permission_manager import PermissionManager

__all__ = ["RouterEngine"]

# ---------------------------------------------------------------------------
# Shared constants (will be the single source of truth after migration)
# ---------------------------------------------------------------------------

SUPPORTED_TOOLS: frozenset[str] = frozenset({
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
    "ask_human",
    "review_request",
})

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

SUPERVISOR_WATCHDOG_MAX_ATTEMPTS = 5
SUPERVISOR_IDLE_THRESHOLD_SECONDS = 45.0
SUPERVISOR_WATCHDOG_CAP_COOLDOWN_SECONDS = 60.0
INTERRUPT_AUTORESUME_IDLE_SECONDS = 180.0

_IDLE_REACTIVATION_SECONDS = 30.0
_MAX_CODE_PATH_FAILURE_STREAK = 2
_PLAN_TRACKING_RELATIVE_PATH = "notes/main_plan.json"
_MAX_USER_TURNS_WITHOUT_PLAN_UPDATE = 2
_REACTIVATION_ALLOWED_STATES = {"RUNNING", "STALLED"}

_ALLOWED_TOOLS_TEXT = ", ".join(sorted(SUPPORTED_TOOLS))

_PYTHON_WORKFLOW_CHECKLIST = (
    "Jeżeli chcesz tworzyć i uruchamiać skrypty Python, stosuj tę procedurę:\n"
    "1) Zapisz plik: write_file(path=..., content=..., overwrite=true).\n"
    "2) Potwierdź zapis: read_file(path=...).\n"
    "3) Sprawdź składnię: check_python_syntax(path=...).\n"
    "4) Dopiero po poprawnej składni uruchom: run_python(path=..., args=[...]).\n"
    "5) Oceń wynik wykonania na podstawie TOOL_RESULT: exit_code/stdout/stderr.\n"
    "6) Przy błędzie popraw plik i powtórz kroki 3-5."
)

_TOOL_CALL_RESOLUTION_FAILED_MESSAGE = (
    "Nie udało się uzyskać poprawnego wywołania narzędzia frameworka w wymaganym formacie. "
    "Spróbuj ponownie poleceniem z jasnym krokiem operacyjnym (np. odczyt pliku lub zapis przez write_file)."
)

# --- Detection helpers for malformed model answers ---

_PSEUDO_TOOL_USAGE_PATTERN = re.compile(
    r"\b(read_file|list_dir|run_shell|run_command|run_python|check_python_syntax"
    r"|fetch_web|write_file|append_file)\s*\(",
    re.IGNORECASE,
)

_PYTHON_CODE_BLOCK_PATTERN = re.compile(
    r"```(?:python|py)?\s*[\s\S]*?"
    r"(?:def\s+\w+\(|class\s+\w+\(|import\s+\w+|from\s+\w+\s+import|print\()",
    re.IGNORECASE,
)


def _is_non_action_placeholder(answer: str) -> bool:
    """Return *True* if *answer* is an empty / null / placeholder response."""
    normalized = answer.strip().strip("`").strip().lower()
    return normalized in {"", "none", "null", "n/a", "brak"}


def _looks_like_unparsed_tool_call(answer: str) -> bool:
    """Return *True* if *answer* resembles a malformed tool_call attempt."""
    lower = answer.lower()
    if "tool_call" not in lower:
        return False
    yaml_like = "tool_call:" in lower and ("name:" in lower or "args:" in lower)
    fenced_yaml = "```yaml" in lower or "```yml" in lower
    return yaml_like or fenced_yaml


def _canonicalize_tool_calls(calls: list[ToolCall]) -> str:
    """Re-serialise *calls* into canonical ``tool_call`` fenced blocks."""
    blocks: list[str] = []
    for call in calls:
        payload = {"tool": call.tool, "args": call.args, "intent": call.intent}
        blocks.append("```tool_call\n" + json.dumps(payload, ensure_ascii=False) + "\n```")
    return "\n".join(blocks)


# --- Corrective prompt builders ---

def _build_pseudo_tool_corrective_prompt() -> str:
    return (
        "Twoja poprzednia odpowiedź zawiera pseudo-kod użycia narzędzi frameworka, "
        "ale nie uruchamia realnej operacji. "
        "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call jako następny krok wykonawczy. "
        "Bez opisu i bez kodu Python.\n"
        f"{_PYTHON_WORKFLOW_CHECKLIST}\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}."
    )


def _build_unparsed_tool_call_corrective_prompt() -> str:
    return (
        "Twoja poprzednia odpowiedź wygląda jak próba wywołania narzędzia, "
        "ale nie jest w poprawnym formacie wykonywalnym przez framework. "
        "Teraz zwróć WYŁĄCZNIE jeden blok w formacie:\n"
        "```tool_call\\n{\"tool\":\"...\",\"args\":{...},\"intent\":\"...\"}\\n```\n"
        "Bez YAML, bez dodatkowego opisu i bez pseudo-kodu.\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}."
    )


def _build_no_action_corrective_prompt(user_message: str, intro_hint: str) -> str:
    return (
        "Twoja poprzednia odpowiedź nie rozpoczęła realnych działań frameworka. "
        "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call jako pierwszy krok operacyjny "
        "dla tego polecenia użytkownika. Bez opisu, bez pytań, bez JSON statusu.\n\n"
        f"Polecenie użytkownika: {user_message}\n"
        f"Preferuj: read_file('{intro_hint}') jeśli dotyczy eksperymentu, "
        "w przeciwnym razie list_dir('.') jako krok startowy.\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}.\n"
        f"{_PYTHON_WORKFLOW_CHECKLIST}"
    )


def _build_python_code_corrective_prompt() -> str:
    return (
        "Twoja poprzednia odpowiedź zawiera kod źródłowy zamiast realnego kroku wykonawczego frameworka. "
        "Nie pokazuj kodu bezpośrednio użytkownikowi. "
        "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call, który zapisze ten kod do pliku przez write_file. "
        "Następnie framework sam wymusi walidację składni. "
        "Bez opisu, bez markdown poza blokiem tool_call.\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}.\n"
        f"{_PYTHON_WORKFLOW_CHECKLIST}"
    )


def _build_code_path_abort_prompt(last_user_message: str) -> str:
    return (
        "Wykryto powtarzające się błędy składni skryptu Python i brak postępu w tym wątku. "
        "Porzuć teraz wątek generowania kodu i wróć do głównego celu zadania. "
        "Wykonaj WYŁĄCZNIE jeden następny krok operacyjny przez tool_call, "
        "preferując narzędzia badawcze (np. list_dir/read_file/search_web/fetch_web) "
        "zamiast write_file/check_python_syntax.\n\n"
        f"Ostatnia wiadomość użytkownika: {last_user_message}\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}."
    )


def _build_unknown_tools_corrective_prompt(unknown_tools: list[str]) -> str:
    """Return corrective prompt for unsupported tool names."""
    return (
        "Twoje poprzednie wywołanie użyło nieobsługiwanych narzędzi: "
        + ", ".join(sorted(set(unknown_tools)))
        + ".\n"
        "Działaj proaktywnie: jeśli narzędzie nie istnieje, najpierw zaprojektuj je i zapisz plan do notes/tool_design_plan.json, "
        "następnie zarejestruj je w state/tool_registry.json, a potem użyj narzędzia do realizacji zadania.\n"
        "Plan musi zawierać: funkcjonalność narzędzia, procedurę debugowania, testowania i naprawy skryptów, "
        "procedurę dopisania narzędzia do listy dostępnych oraz procedurę użycia narzędzia.\n"
        f"Dostępne narzędzia bazowe: {_ALLOWED_TOOLS_TEXT}.\n"
        f"{_PYTHON_WORKFLOW_CHECKLIST}\n"
        "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call jako pierwszy krok tej procedury (preferuj write_file)."
    )


def _has_supported_tool_call(answer: str) -> bool:
    """Return *True* if *answer* contains at least one supported tool call."""
    calls = parse_tool_calls(answer)
    if not calls:
        return False
    return any(canonical_tool_name(call.tool) in SUPPORTED_TOOLS for call in calls)


def _has_unknown_tool_calls(answer: str) -> bool:
    """Return *True* if *answer* contains at least one unsupported tool call."""
    calls = parse_tool_calls(answer)
    if not calls:
        return False
    return any(canonical_tool_name(call.tool) not in SUPPORTED_TOOLS for call in calls)


def _build_plan_tracking_corrective_prompt(user_message: str) -> str:
    return (
        "Zanim przejdziesz do kolejnych działań, najpierw zainicjalizuj plan głównego wątku w notatkach. "
        "Zwróć WYŁĄCZNIE jeden poprawny blok tool_call przez write_file, który utworzy plik "
        f"'{_PLAN_TRACKING_RELATIVE_PATH}' z JSON-em zawierającym pola: "
        "goal, key_achievement, current_stage, tasks[]. "
        "Każde zadanie powinno mieć status: rozpoczęta | w trakcie realizacji | zakończona.\n\n"
        f"Polecenie użytkownika: {user_message}\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}."
    )


_AUTONOMY_PATTERN = re.compile(
    r"\b(kontynuuj|działaj|dzialaj|ty\s+decyduj|sam\s+decyduj|decyduj|nie\s+zatrzymuj\s+się|nie\s+zatrzymuj\s*sie|rozpocznij\s+.*eksperyment)\b",
    re.IGNORECASE,
)
_ACTION_PATTERN = re.compile(
    r"\b(przeczytaj|odczytaj|analizuj|przeanalizuj|rozpocznij|wykonaj|zapisz)\b",
    re.IGNORECASE,
)


def _build_idle_reactivation_prompt(last_user_message: str) -> str:
    return (
        "Wykryto okres bezczynności. Kontynuuj samodzielnie realizację ostatniego celu. "
        "Zwróć WYŁĄCZNIE jeden poprawny blok tool_call jako najbliższy krok operacyjny. "
        "Bez opisu i bez pytań.\n\n"
        f"Ostatnia wiadomość użytkownika: {last_user_message}\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}.\n"
        f"{_PYTHON_WORKFLOW_CHECKLIST}"
    )


def _network_resource_for_model(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return "network.local"
    return "network.internet"


def _build_plan_persistence_corrective_prompt(user_message: str) -> str:
    return (
        "Wykryto brak trwałego zapisu planu głównego albo plan bez zadań. "
        "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call przez write_file, "
        f"który zapisze pełny plan do '{_PLAN_TRACKING_RELATIVE_PATH}'.\n"
        "Wymagane pola JSON: goal, key_achievement, current_stage, tasks[].\n"
        "tasks[] musi zawierać co najmniej jedno zadanie z polami: id, title, status, next_step.\n"
        "Status zadania: rozpoczęta | w trakcie realizacji | zakończona.\n"
        "Nie zwracaj opisu, pytań ani pseudo-kodu.\n\n"
        f"Polecenie użytkownika: {user_message}\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}."
    )


def _build_plan_progress_update_prompt(user_message: str) -> str:
    return (
        "Wykryto brak aktualizacji postępu planu przez kolejne tury. "
        "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call przez write_file, "
        f"który aktualizuje istniejący plan w '{_PLAN_TRACKING_RELATIVE_PATH}'.\n"
        "Wymagane: zaktualizuj current_stage i co najmniej jeden status zadania lub key_achievement.\n"
        "Zachowaj poprawną strukturę JSON planu (goal, key_achievement, current_stage, tasks[]).\n"
        "Nie odpowiadaj tekstem ani pytaniem.\n\n"
        f"Polecenie użytkownika: {user_message}\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}."
    )

_CONVERSATIONAL_INTERRUPT_MARKERS: frozenset[str] = frozenset({
    "kim jesteś", "kim jestes", "kto jesteś", "kto jestes",
    "co potrafisz", "jak działasz", "jak dzialasz",
    "jak działa framework", "jak dziala framework",
})

_IDENTITY_QUERY_MARKERS: frozenset[str] = frozenset({
    "kim jesteś", "kim jestes", "kto jesteś", "kto jestes",
})

_MODEL_QUESTION_MARKERS: tuple[str, ...] = (
    "co chcesz", "czego oczekujesz", "jakie masz", "jakiego",
    "jak chcesz", "czy chcesz", "czy mam", "co mam zrobić",
    "proszę o wskazówki", "oczekuję na", "czekam na",
    "proszę o decyzję", "twoja decyzja", "co dalej",
    "jaki jest", "jaka jest",
)

_COMPLETION_SIGNAL = "zakończyłem zadanie"


def canonical_tool_name(name: str) -> str:
    """Normalise a tool name through known aliases."""
    cleaned = name.strip()
    return _TOOL_ALIASES.get(cleaned, cleaned)


# ---------------------------------------------------------------------------
# Microphone helpers
# ---------------------------------------------------------------------------


def _detect_preferred_microphone_device() -> str | None:
    """Auto-detect the preferred ALSA recording device via ``arecord -l``."""
    arecord = shutil.which("arecord")
    if arecord is None:
        return None
    try:
        completed = subprocess.run(
            [arecord, "-l"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    fallback: str | None = None
    preferred: str | None = None
    pattern = re.compile(
        r"^card\s+(?P<card>\d+):\s*(?P<name>[^\[]+)\[.*?\],\s*device\s+(?P<device>\d+):"
    )
    for line in completed.stdout.splitlines():
        match = pattern.search(line.strip())
        if match is None:
            continue
        card = match.group("card")
        device = match.group("device")
        name = match.group("name").strip().lower()
        candidate = f"hw:{card},{device}"
        if fallback is None:
            fallback = candidate
        if any(token in name for token in ("c920", "webcam", "usb")):
            preferred = candidate
            break
    return preferred or fallback


def _build_microphone_profiles(
    requested_rate: int, requested_channels: int
) -> list[tuple[int, int]]:
    """Return a list of (sample_rate, channels) profiles to try."""
    candidates = [
        (requested_rate, requested_channels),
        (48000, 2),
        (44100, 2),
        (32000, 2),
        (16000, 2),
        (48000, 1),
        (32000, 1),
        (16000, 1),
    ]
    normalized: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for rate, channels in candidates:
        safe_rate = max(8000, min(48000, int(rate)))
        safe_channels = max(1, min(2, int(channels)))
        profile = (safe_rate, safe_channels)
        if profile in seen:
            continue
        seen.add(profile)
        normalized.append(profile)
    return normalized


# ---------------------------------------------------------------------------
# RouterEngine
# ---------------------------------------------------------------------------


class RouterEngine:
    """UI-independent orchestration engine.

    Owns the full lifecycle of a user turn:

    1. ``submit_user_turn(text)`` — enqueue & dispatch to background thread
    2. Process: ask executor → supervise → resolve tools → deliver answer
    3. Emit events via ``event_bus`` for the adapter to render

    Adapters set up timers that call :meth:`watchdog_tick` and
    :meth:`poll_supervision_dialogue` periodically.
    """

    def __init__(
        self,
        *,
        chat_service: ChatService,
        permission_manager: PermissionManager,
        script_executor: ScriptExecutor,
        work_dir: Path,
        shell_policy_path: Path,
        event_bus: EventBus,
        activity_logger: ActivityLogger | None = None,
        settings: Settings | None = None,
        autonomous_mode: bool = False,
        router_mailbox_log_path: Path | None = None,
        supervisor_dialogue_log_path: Path | None = None,
        permission_enforcer: PermissionEnforcer | None = None,
        audit_chain: AuditChain | None = None,
    ) -> None:
        # --- injected services ---
        self.chat_service = chat_service
        self.permission_manager = permission_manager
        self.script_executor = script_executor
        self.work_dir = work_dir
        self.event_bus = event_bus
        self.activity_logger = activity_logger
        self.settings = settings
        self.autonomous_mode = autonomous_mode
        self._router_mailbox_log_path = router_mailbox_log_path or Path("./logs/router_mailbox.jsonl")
        self._supervisor_dialogue_log_path = supervisor_dialogue_log_path
        self._permission_enforcer = permission_enforcer
        self._audit_chain = audit_chain

        # --- shell policy ---
        try:
            self._shell_policy = load_shell_policy(shell_policy_path)
        except Exception:
            self._shell_policy = default_shell_policy()

        # --- orchestration state ---
        self._passive_turns: int = 0
        self._last_user_message: str = ""
        self._last_model_answer: str = ""
        self._last_progress_monotonic: float = time.monotonic()

        # watchdog
        self._watchdog_attempts: int = 0
        self._watchdog_capped_notified: bool = False
        self._last_watchdog_cap_autonudge_monotonic: float = 0.0
        self._watchdog_suspended_until_user_input: bool = False
        self._watchdog_idle_threshold_seconds: float = SUPERVISOR_IDLE_THRESHOLD_SECONDS

        # router cycle
        self._router_cycle_in_progress: bool = False
        self._last_background_worker: threading.Thread | None = None
        self._user_message_queue: deque[str] = deque()

        # supervisor outbox
        self._supervisor_outbox: list[dict[str, str]] = []

        # actor states
        self._actor_states: dict[str, str] = {
            "router": "INIT",
            "creator": "WAITING_INPUT",
            "supervisor": (
                "READY" if chat_service.supervisor_service is not None else "DISABLED"
            ),
            "terminal": "WAITING_INPUT",
        }

        # idle window
        self._idle_until_epoch: float | None = None
        self._idle_until_source: str = ""
        self._last_router_event: str = "Rozpoczęcie sesji"

        # plan pause state
        self._plan_pause_active: bool = False
        self._plan_pause_started_monotonic: float = 0.0
        self._plan_pause_reason: str = ""
        self._pending_user_decision: bool = False
        self._pending_decision_identity_query: bool = False

        # communication tracking
        self._unaddressed_turns: int = 0
        self._reminder_count: int = 0
        self._consultation_rounds_this_cycle: int = 0
        self._comm_rules: CommunicationRules = load_communication_rules()

        # threading
        self._background_enabled: bool = (
            os.environ.get("AMIAGI_TEXTUAL_BACKGROUND_USER_TURN", "1") != "0"
        )
        self._main_thread_id: int = threading.get_ident()

        # supervision dialogue polling
        self._dialogue_log_offset: int = 0

        # tool-flow state (shared across resolve_tool_calls invocations)
        self._code_path_failure_streak: int = 0
        self._last_tool_activity_monotonic: float = time.monotonic()
        self._idle_reactivation_attempts: int = 0
        self._idle_reactivation_capped_notified: bool = False
        self._last_idle_reactivation_monotonic: float = time.monotonic()
        self._max_idle_autoreactivations: int = 2
        self._last_work_state: str = "RUNNING"
        self._user_turns_without_plan_update: int = 0

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def router_cycle_in_progress(self) -> bool:
        return self._router_cycle_in_progress

    @property
    def actor_states(self) -> dict[str, str]:
        return dict(self._actor_states)

    @property
    def last_user_message(self) -> str:
        return self._last_user_message

    @property
    def last_model_answer(self) -> str:
        return self._last_model_answer

    @property
    def passive_turns(self) -> int:
        return self._passive_turns

    @property
    def watchdog_suspended(self) -> bool:
        return self._watchdog_suspended_until_user_input

    @property
    def plan_pause_active(self) -> bool:
        return self._plan_pause_active

    @property
    def pending_user_decision(self) -> bool:
        return self._pending_user_decision

    @property
    def last_progress_monotonic(self) -> float:
        return self._last_progress_monotonic

    @property
    def supervisor_outbox_size(self) -> int:
        return len(self._supervisor_outbox)

    @property
    def comm_rules(self):
        """Communication rules (read-only, loaded once at engine creation)."""
        return self._comm_rules

    def reset_watchdog_on_user_input(self) -> None:
        """Reset watchdog state when the user sends any input.

        Called by adapters at the top of their input handler so the engine's
        watchdog counters stay in sync even for non-turn messages (slash
        commands, wizard input, etc.).
        """
        if not self._watchdog_suspended_until_user_input:
            return
        self._watchdog_suspended_until_user_input = False
        self._watchdog_attempts = 0
        self._watchdog_capped_notified = False
        self._last_watchdog_cap_autonudge_monotonic = 0.0

    # ------------------------------------------------------------------
    # EventBus helpers (replace direct _append_log / _set_actor_state)
    # ------------------------------------------------------------------

    def _emit_log(self, panel: str, message: str) -> None:
        self.event_bus.emit(LogEvent(panel=panel, message=message))

    def _emit_actor_state(self, actor: str, state: str, event: str) -> None:
        self._actor_states[actor] = state
        self._last_router_event = event
        self.event_bus.emit(ActorStateEvent(actor=actor, state=state, event=event))

    def _emit_cycle_finished(self, event: str) -> None:
        self._router_cycle_in_progress = False
        self._emit_actor_state("router", "ACTIVE", event)
        self._emit_actor_state("terminal", "WAITING_INPUT", "Oczekiwanie na kolejną wiadomość użytkownika")
        if self._actor_states.get("creator") in {"THINKING", "ANSWER_READY"}:
            if self._last_model_answer.strip():
                self._emit_actor_state("creator", "PASSIVE", "Domknięto cykl wykonania bez aktywnego narzędzia")
            else:
                self._emit_actor_state("creator", "WAITING_INPUT", "Brak aktywnej pracy Twórcy")
        self.event_bus.emit(CycleFinishedEvent(event=event))
        self._drain_user_queue()

    def _emit_supervisor_message(
        self,
        *,
        stage: str,
        reason_code: str,
        notes: str,
        answer: str,
    ) -> None:
        self.event_bus.emit(
            SupervisorMessageEvent(
                stage=stage,
                reason_code=reason_code,
                notes=notes,
                answer=answer,
            )
        )

    def _emit_error(self, message: str) -> None:
        self.event_bus.emit(ErrorEvent(message=message))

    # ------------------------------------------------------------------
    # Activity logging helper
    # ------------------------------------------------------------------

    def _log_activity(
        self, *, action: str, intent: str, details: dict[str, Any] | None = None,
    ) -> None:
        if self.activity_logger is not None:
            self.activity_logger.log(action=action, intent=intent, details=details)

    # ------------------------------------------------------------------
    # Tool support helpers
    # ------------------------------------------------------------------

    def runtime_supported_tool_names(self) -> set[str]:
        """Return the full set of currently supported tool names."""
        return set(SUPPORTED_TOOLS).union(list_registered_tools(self.work_dir))

    def has_supported_tool_call(self, answer: str) -> bool:
        calls = parse_tool_calls(answer)
        if not calls:
            return False
        supported = self.runtime_supported_tool_names()
        return any(canonical_tool_name(c.tool) in supported for c in calls)

    def has_only_supported_tool_calls(self, answer: str) -> bool:
        calls = parse_tool_calls(answer)
        if not calls:
            return False
        supported = self.runtime_supported_tool_names()
        return all(canonical_tool_name(c.tool) in supported for c in calls)

    # ------------------------------------------------------------------
    # Actionable-autonomy enforcement
    # ------------------------------------------------------------------

    def _enforce_actionable_autonomy(
        self, user_message: str, model_answer: str,
    ) -> str:
        """Re-prompt the model when user expects action but answer has none."""
        should_enforce = bool(
            _AUTONOMY_PATTERN.search(user_message)
            or _ACTION_PATTERN.search(user_message),
        )
        if not should_enforce:
            return model_answer
        if self.has_only_supported_tool_calls(model_answer):
            return model_answer

        plan_snapshot = self._read_plan_tracking_snapshot()
        if not bool(plan_snapshot.get("exists")):
            corrective_prompt = _build_plan_tracking_corrective_prompt(user_message)
            forced = self._ask_executor_with_router_mailbox(corrective_prompt)
            forced = self._apply_supervisor(
                corrective_prompt, forced, stage="plan_tracking_init",
            )
            if self.has_only_supported_tool_calls(forced):
                return forced

        intro_candidates = [
            self.work_dir / "wprowadzenie.md",
            self.work_dir.parent / "wprowadzenie.md",
        ]
        intro_path = next((p for p in intro_candidates if p.exists()), None)
        intro_hint = (
            str(intro_path.resolve()) if intro_path is not None else "wprowadzenie.md"
        )

        forced_answer = model_answer
        for _attempt in range(2):
            corrective = _build_no_action_corrective_prompt(user_message, intro_hint)
            forced_answer = self._ask_executor_with_router_mailbox(corrective)
            forced_answer = self._apply_supervisor(
                corrective, forced_answer, stage="no_action_corrective",
            )
            if self.has_only_supported_tool_calls(forced_answer):
                return forced_answer
        return forced_answer

    # ------------------------------------------------------------------
    # Idle reactivation
    # ------------------------------------------------------------------

    def run_idle_reactivation_cycle(self) -> None:
        """Auto-reactivate model when idle period exceeded."""
        now = time.monotonic()
        plan_snapshot = self._read_plan_tracking_snapshot()
        actionable_plan = self._has_actionable_main_plan(plan_snapshot)
        if self._passive_turns <= 0 and not actionable_plan:
            return
        if self._last_work_state not in _REACTIVATION_ALLOWED_STATES:
            return
        if now - self._last_tool_activity_monotonic < _IDLE_REACTIVATION_SECONDS:
            return
        if now - self._last_idle_reactivation_monotonic < _IDLE_REACTIVATION_SECONDS:
            return
        if self._idle_reactivation_attempts >= self._max_idle_autoreactivations:
            if not self._idle_reactivation_capped_notified:
                self._emit_log(
                    "system",
                    f"Wstrzymuję kolejne autowzbudzenia po {self._max_idle_autoreactivations} próbach. "
                    "Oczekuję decyzji użytkownika lub nowej aktywności narzędziowej.",
                )
                self._log_activity(
                    action="idle.reactivation.capped",
                    intent="Wstrzymano kolejne auto-reaktywacje po osiągnięciu limitu prób.",
                    details={
                        "max_attempts": self._max_idle_autoreactivations,
                        "work_state": self._last_work_state,
                    },
                )
                self._idle_reactivation_capped_notified = True
            self._last_idle_reactivation_monotonic = now
            return

        network_resource = _network_resource_for_model(
            self.chat_service.ollama_client.base_url,
        )
        network_reason = (
            "Połączenie z lokalnym API modelu wymaga dostępu do sieci lokalnej."
            if network_resource == "network.local"
            else "Połączenie z modelem wymaga dostępu do internetu/sieci zewnętrznej."
        )
        if network_resource == "network.local":
            if not self.permission_manager.request_local_network(network_reason):
                self._log_activity(
                    action="idle.reactivation.denied",
                    intent="Pominięto auto-reaktywację po bezczynności z powodu odmowy zasobu sieciowego.",
                )
                self._last_idle_reactivation_monotonic = now
                return
        else:
            if not self.permission_manager.request_internet(network_reason):
                self._log_activity(
                    action="idle.reactivation.denied",
                    intent="Pominięto auto-reaktywację po bezczynności z powodu odmowy zasobu sieciowego.",
                )
                self._last_idle_reactivation_monotonic = now
                return

        idle_prompt = _build_idle_reactivation_prompt(
            self._last_user_message or "kontynuuj",
        )
        self._idle_reactivation_attempts += 1
        self._idle_reactivation_capped_notified = False
        self._log_activity(
            action="idle.reactivation.start",
            intent="Uruchomiono auto-reaktywację po przekroczeniu dopuszczalnego okresu bezczynności.",
            details={
                "idle_threshold_seconds": _IDLE_REACTIVATION_SECONDS,
                "passive_turns": self._passive_turns,
                "attempt": self._idle_reactivation_attempts,
                "max_attempts": self._max_idle_autoreactivations,
                "triggered_by_actionable_plan": actionable_plan,
                "tasks_total": plan_snapshot.get("tasks_total", 0),
                "tasks_done": plan_snapshot.get("tasks_done", 0),
            },
        )

        answer = self._ask_executor_with_router_mailbox(idle_prompt)
        answer = self._apply_supervisor(idle_prompt, answer, stage="idle_reactivation")
        answer = self._enforce_actionable_autonomy(
            self._last_user_message or "kontynuuj", answer,
        )
        tool_activity_before = self._last_tool_activity_monotonic
        answer = self.resolve_tool_calls(answer)
        answer = self._ensure_plan_persisted(
            self._last_user_message or "kontynuuj", answer,
        )
        had_runtime_progress = (
            self._last_tool_activity_monotonic > tool_activity_before
        )
        if had_runtime_progress and not self.has_supported_tool_call(answer):
            self._passive_turns = 0
        else:
            self._passive_turns += 1
        display_answer = self._format_user_facing_answer(answer)
        self._emit_log("model", display_answer)
        self._log_activity(
            action="idle.reactivation.done",
            intent="Zakończono cykl auto-reaktywacji po bezczynności.",
            details={"answer_chars": len(answer), "display_chars": len(display_answer)},
        )
        self._last_idle_reactivation_monotonic = time.monotonic()

    # ------------------------------------------------------------------
    # Permission / resource helpers
    # ------------------------------------------------------------------

    def _resource_allowed(self, resource: str) -> bool:
        if self.permission_manager.allow_all:
            return True
        if resource in getattr(self.permission_manager, "granted_once", set()):
            return True
        return False

    def _ensure_resource(self, resource: str, reason: str) -> bool:
        if self._resource_allowed(resource):
            return True
        self._emit_log("user_model_log", f"Brak uprawnień: {reason} (zasób: {resource})")
        return False

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _resolve_model_path(self, raw_path: str) -> Path:
        path = resolve_tool_path(raw_path, self.work_dir)
        cleaned = raw_path.strip().replace("\\", "/")
        if cleaned.startswith("amiagi-main/"):
            suffix = cleaned.split("/", 1)[1] if "/" in cleaned else ""
            if suffix == "notes/main_plan.json":
                return (self.work_dir / "notes" / "main_plan.json").resolve()
            if suffix:
                workspace_candidate = (Path.cwd() / suffix).resolve()
                if workspace_candidate.exists():
                    return workspace_candidate
        return path

    def _is_main_plan_path(self, path: Path) -> bool:
        """Return True if *path* points to the main plan tracking file."""
        try:
            return path.resolve(strict=False) == (
                self.work_dir / "notes" / "main_plan.json"
            ).resolve(strict=False)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Supervisor helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_supervisor_notes(base_note: str, supervisor_note: str) -> str:
        base_clean = " ".join(base_note.strip().split())
        supervisor_clean = " ".join(supervisor_note.strip().split())
        if not supervisor_clean:
            return base_clean[:500]
        if not base_clean:
            return supervisor_clean[:500]
        if supervisor_clean in base_clean:
            return base_clean[:500]
        return f"{base_clean} {supervisor_clean}"[:500]

    # ------------------------------------------------------------------
    # Supervision context & apply_supervisor
    # ------------------------------------------------------------------

    @staticmethod
    def _gpu_utilization_percent() -> int | None:
        """Return the max GPU utilization across all GPUs, or *None*."""
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        values: list[int] = []
        for line in completed.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                values.append(int(stripped.split(",")[0].strip()))
            except ValueError:
                continue
        return max(values) if values else None

    def _supervision_context(self, stage: str) -> dict:
        """Build runtime context dict for the supervisor."""
        gpu_util = self._gpu_utilization_percent()
        idle_seconds = int(max(0.0, time.monotonic() - self._last_tool_activity_monotonic))
        plan_snapshot = self._read_plan_tracking_snapshot()
        plan_persistence = self._plan_persistence_snapshot()
        return {
            "stage": stage,
            "passive_turns": self._passive_turns,
            "idle_seconds_since_tool_activity": idle_seconds,
            "work_state": self._last_work_state,
            "main_plan_tracking": plan_snapshot,
            "plan_persistence": plan_persistence,
            "gpu_utilization_percent": gpu_util,
            "gpu_busy_over_50": (gpu_util is not None and gpu_util > 50),
            "should_remind_continuation": self._passive_turns >= 2,
        }

    def _read_plan_tracking_snapshot(self) -> dict:
        """Return a rich plan summary (compatible with CLI's module-level helper)."""
        plan_path = self.work_dir / _PLAN_TRACKING_RELATIVE_PATH
        snapshot: dict = {
            "path": str(plan_path),
            "exists": False,
            "goal": "",
            "current_stage": "",
            "tasks_total": 0,
            "tasks_done": 0,
        }
        if not plan_path.exists() or not plan_path.is_file():
            return snapshot

        snapshot["exists"] = True
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            snapshot["parse_error"] = True
            return snapshot

        if not isinstance(payload, dict):
            snapshot["parse_error"] = True
            return snapshot

        goal = payload.get("goal")
        current_stage = payload.get("current_stage")
        tasks = payload.get("tasks", [])
        snapshot["goal"] = goal.strip() if isinstance(goal, str) else ""
        snapshot["current_stage"] = current_stage.strip() if isinstance(current_stage, str) else ""
        if isinstance(tasks, list):
            snapshot["tasks_total"] = len(tasks)
            snapshot["tasks_done"] = sum(
                1
                for task in tasks
                if isinstance(task, dict)
                and str(task.get("status", "")).strip().lower() == "zakończona"
            )
        return snapshot

    def _read_main_plan_payload(self) -> dict | None:
        """Read and parse the main plan JSON, or *None* on failure."""
        plan_path = self.work_dir / _PLAN_TRACKING_RELATIVE_PATH
        if not plan_path.exists() or not plan_path.is_file():
            return None
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _plan_persistence_snapshot(self) -> dict:
        """Return a dict describing the plan persistence state."""
        payload = self._read_main_plan_payload()
        if payload is None:
            return {
                "exists": False,
                "valid": False,
                "has_tasks": False,
                "required": True,
            }

        goal = payload.get("goal")
        current_stage = payload.get("current_stage")
        tasks = payload.get("tasks")

        valid_header = (
            isinstance(goal, str) and bool(goal.strip())
            and isinstance(current_stage, str) and bool(current_stage.strip())
        )
        has_tasks = isinstance(tasks, list) and len(tasks) > 0
        return {
            "exists": True,
            "valid": bool(valid_header and isinstance(tasks, list)),
            "has_tasks": bool(has_tasks),
            "required": not bool(has_tasks),
        }

    def _main_plan_fingerprint(self) -> str:
        """Return a deterministic JSON string of the main plan, or ``""``."""
        payload = self._read_main_plan_payload()
        if payload is None:
            return ""
        try:
            return json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except Exception:
            return ""

    @staticmethod
    def _has_actionable_main_plan(snapshot: dict) -> bool:
        """Return *True* if *snapshot* describes a plan with pending tasks."""
        if not bool(snapshot.get("exists")):
            return False
        if bool(snapshot.get("parse_error")):
            return False
        tasks_total = snapshot.get("tasks_total", 0)
        tasks_done = snapshot.get("tasks_done", 0)
        if not isinstance(tasks_total, int) or not isinstance(tasks_done, int):
            return False
        return tasks_total > 0 and tasks_done < tasks_total

    def _ensure_plan_persisted(self, user_message: str, answer: str) -> str:
        """Force plan creation if plan file is missing or has no tasks.

        Uses up to 2 corrective rounds asking the executor to write the plan,
        supervised and resolved each time.
        """
        snapshot = self._plan_persistence_snapshot()
        if not snapshot.get("exists"):
            return answer
        if snapshot.get("has_tasks"):
            return answer

        current_answer = answer
        for attempt in range(1, 3):
            corrective_prompt = _build_plan_persistence_corrective_prompt(user_message)
            self._log_activity(
                action="plan.persistence.enforce.start",
                intent="Wymuszono trwały zapis planu głównego po wykryciu braku planu lub zadań.",
                details={
                    "attempt": attempt,
                    "required": snapshot.get("required", True),
                    "exists": snapshot.get("exists", False),
                },
            )
            corrected = self._ask_executor_with_router_mailbox(corrective_prompt)
            corrected = self._apply_supervisor(
                corrective_prompt, corrected, stage="plan_persistence_corrective",
            )
            current_answer = self.resolve_tool_calls(corrected)

            snapshot = self._plan_persistence_snapshot()
            if snapshot.get("has_tasks"):
                self._log_activity(
                    action="plan.persistence.enforce.done",
                    intent="Zapis planu głównego został potwierdzony po wymuszeniu korekty.",
                    details={"attempt": attempt},
                )
                return current_answer

        self._log_activity(
            action="plan.persistence.enforce.failed",
            intent="Nie udało się potwierdzić trwałego zapisu planu po korektach.",
            details={
                "exists": snapshot.get("exists", False),
                "valid": snapshot.get("valid", False),
                "has_tasks": snapshot.get("has_tasks", False),
            },
        )
        return current_answer

    def _ensure_plan_progress_updated(self, user_message: str, answer: str) -> str:
        """Force plan update when the plan fingerprint hasn't changed.

        Uses up to 2 corrective rounds asking the executor to update the plan.
        """
        baseline = self._main_plan_fingerprint()
        if not baseline:
            return answer

        current_answer = answer
        for attempt in range(1, 3):
            corrective_prompt = _build_plan_progress_update_prompt(user_message)
            self._log_activity(
                action="plan.progress.enforce.start",
                intent="Wymuszono aktualizację postępu planu po wykryciu braku zmian przez kolejne tury.",
                details={"attempt": attempt},
            )
            corrected = self._ask_executor_with_router_mailbox(corrective_prompt)
            corrected = self._apply_supervisor(
                corrective_prompt, corrected, stage="plan_progress_corrective",
            )
            current_answer = self.resolve_tool_calls(corrected)

            current_fingerprint = self._main_plan_fingerprint()
            if current_fingerprint and current_fingerprint != baseline:
                self._log_activity(
                    action="plan.progress.enforce.done",
                    intent="Potwierdzono aktualizację planu po wymuszeniu korekty postępu.",
                    details={"attempt": attempt},
                )
                return current_answer

        self._log_activity(
            action="plan.progress.enforce.failed",
            intent="Nie udało się potwierdzić aktualizacji planu po korektach postępu.",
            details={},
        )
        return current_answer

    def _update_plan_fingerprint_tracking(
        self,
        user_message: str,
        answer: str,
        plan_fingerprint_before: str,
    ) -> str:
        """Compare plan fingerprint before/after and enforce update if stale.

        Updates ``_user_turns_without_plan_update`` and may call
        ``_ensure_plan_progress_updated`` if the threshold is reached.
        Returns (possibly modified) *answer*.
        """
        plan_fingerprint_after = self._main_plan_fingerprint()
        if plan_fingerprint_after and plan_fingerprint_after != plan_fingerprint_before:
            self._user_turns_without_plan_update = 0
        else:
            snapshot = self._read_plan_tracking_snapshot()
            if (
                self._has_actionable_main_plan(snapshot)
                and self._last_work_state in _REACTIVATION_ALLOWED_STATES
            ):
                self._user_turns_without_plan_update += 1
            else:
                self._user_turns_without_plan_update = 0

        if self._user_turns_without_plan_update >= _MAX_USER_TURNS_WITHOUT_PLAN_UPDATE:
            self._log_activity(
                action="plan.progress.stale.detected",
                intent="Wykryto brak aktualizacji planu przez kolejne tury; uruchomiono twardą korektę.",
                details={
                    "turns_without_update": self._user_turns_without_plan_update,
                    "threshold": _MAX_USER_TURNS_WITHOUT_PLAN_UPDATE,
                },
            )
            answer = self._ensure_plan_progress_updated(user_message, answer)
            refreshed_fingerprint = self._main_plan_fingerprint()
            if refreshed_fingerprint and refreshed_fingerprint != plan_fingerprint_after:
                self._user_turns_without_plan_update = 0
        return answer

    def _apply_supervisor(
        self,
        user_message: str,
        model_answer: str,
        stage: str,
    ) -> str:
        """Full supervisor gate — review or correct *model_answer*.

        Returns the (possibly modified) answer. If the supervisor injects an
        unsupported tool call, the original *model_answer* is preserved.
        """
        supervisor = self.chat_service.supervisor_service
        if supervisor is None:
            return model_answer
        user_message_with_context = (
            f"{user_message}\n\n"
            "[RUNTIME_SUPERVISION_CONTEXT]\n"
            + json.dumps(self._supervision_context(stage), ensure_ascii=False)
        )
        try:
            result = supervisor.refine(
                user_message=user_message_with_context,
                model_answer=model_answer,
                stage=stage,
            )
        except (OllamaClientError, OSError):
            return model_answer
        self._last_work_state = result.work_state
        if result.repairs_applied > 0:
            notes = ""
            if result.reason_code != "OK":
                notes = f"Korekta nadzorcza ({result.reason_code})."
            self.enqueue_supervisor_message(
                stage=stage,
                reason_code=result.reason_code,
                notes=self._merge_supervisor_notes(notes, result.notes),
                answer=result.answer,
            )
            self._log_activity(
                action="supervisor.repair.applied",
                intent="Zastosowano poprawkę odpowiedzi modelu wykonawczego przez nadzorcę.",
                details={
                    "stage": stage,
                    "repairs_applied": result.repairs_applied,
                    "reason_code": result.reason_code,
                    "work_state": result.work_state,
                },
            )
            runtime_supported = self.runtime_supported_tool_names()
            repaired_calls = parse_tool_calls(result.answer)
            has_unsupported = any(
                canonical_tool_name(c.tool) not in runtime_supported
                for c in repaired_calls
            )
            if has_unsupported:
                self._log_activity(
                    action="supervisor.repair.rejected",
                    intent="Odrzucono poprawkę nadzorcy zawierającą nieobsługiwane narzędzie.",
                    details={"stage": stage, "reason_code": result.reason_code},
                )
                return model_answer
        else:
            self.enqueue_supervisor_message(
                stage=stage,
                reason_code=result.reason_code,
                notes=self._merge_supervisor_notes("Ocena bez korekty.", result.notes),
                answer=result.answer,
            )
            self._log_activity(
                action="supervisor.review.done",
                intent="Nadzorca ocenił odpowiedź bez konieczności poprawek.",
                details={
                    "stage": stage,
                    "reason_code": result.reason_code,
                    "work_state": result.work_state,
                },
            )
        return result.answer

    def _has_unknown_tool_calls_runtime(self, answer: str) -> bool:
        """Return True if *answer* contains tool calls not in the runtime set."""
        calls = parse_tool_calls(answer)
        if not calls:
            return False
        supported = self.runtime_supported_tool_names()
        return any(canonical_tool_name(c.tool) not in supported for c in calls)

    # ------------------------------------------------------------------
    # Compact tool results helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _short_text(value: str, max_chars: int = 1800) -> dict:
        if len(value) <= max_chars:
            return {"text": value, "truncated": False, "total_chars": len(value)}
        return {"text": value[:max_chars], "truncated": True, "total_chars": len(value)}

    @classmethod
    def _compact_tool_result_for_model(cls, result: dict) -> dict:
        compact = dict(result)
        for key in ("content", "stdout", "stderr"):
            value = compact.get(key)
            if isinstance(value, str):
                shortened = cls._short_text(value)
                compact[key] = shortened["text"]
                if shortened["truncated"]:
                    compact[f"{key}_truncated"] = True
                    compact[f"{key}_total_chars"] = shortened["total_chars"]
        return compact

    @classmethod
    def _compact_tool_results_payload(cls, results: list[dict]) -> str:
        payload = {
            "results": [
                {
                    "tool": item.get("tool"),
                    "intent": item.get("intent", ""),
                    "result": cls._compact_tool_result_for_model(item.get("result", {})),
                }
                for item in results
            ]
        }
        serialized = json.dumps(payload, ensure_ascii=False)
        max_chars = 10000
        if len(serialized) <= max_chars:
            return serialized
        slim_results = []
        for item in payload["results"]:
            result = item.get("result", {})
            slim_results.append({
                "tool": item.get("tool"),
                "intent": item.get("intent", ""),
                "result": {
                    "ok": result.get("ok"),
                    "tool": result.get("tool", item.get("tool")),
                    "error": result.get("error"),
                    "path": result.get("path"),
                    "url": result.get("url"),
                    "exit_code": result.get("exit_code"),
                    "content_truncated": result.get("content_truncated", False),
                    "stdout_truncated": result.get("stdout_truncated", False),
                    "stderr_truncated": result.get("stderr_truncated", False),
                },
            })
        return json.dumps({"results": slim_results, "compact": True}, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Safe fallback & pseudo-call logging
    # ------------------------------------------------------------------

    def _log_rejected_pseudo_call(self, reason: str, answer: str) -> None:
        preview = self._short_text(answer, max_chars=400)
        self._log_activity(
            action="tool_call.pseudo_rejected",
            intent="Odrzucono pseudo-tool_call i uruchomiono ścieżkę korekty/fallback.",
            details={
                "reason": reason,
                "answer_preview": preview["text"],
                "answer_preview_truncated": preview["truncated"],
                "answer_total_chars": preview["total_chars"],
            },
        )

    def _run_safe_tool_fallback(self) -> str:
        """Execute ``list_dir('.')`` as a safe fallback and return the answer."""
        fallback_call = ToolCall(tool="list_dir", args={"path": "."}, intent="fallback_start")
        self._log_activity(
            action="tool_call.fallback.request",
            intent="Uruchomiono bezpieczny krok awaryjny po nieudanej normalizacji tool_call.",
            details={"tool": fallback_call.tool, "intent": fallback_call.intent},
        )
        tool_result = self.execute_tool_call(fallback_call)
        self._last_tool_activity_monotonic = time.monotonic()
        self._log_activity(
            action="tool_call.fallback.result",
            intent="Zakończono bezpieczny krok awaryjny narzędzia.",
            details={"tool": fallback_call.tool, "ok": bool(tool_result.get("ok"))},
        )
        if not bool(tool_result.get("ok")):
            return _TOOL_CALL_RESOLUTION_FAILED_MESSAGE
        followup = (
            "[TOOL_RESULT]\n"
            + self._compact_tool_results_payload(
                [{"tool": fallback_call.tool, "intent": fallback_call.intent, "result": tool_result}]
            )
            + "\nNa podstawie tego wyniku odpowiedz użytkownikowi."
        )
        post_fallback_answer = self._ask_executor_with_router_mailbox(followup)
        post_fallback_calls = parse_tool_calls(post_fallback_answer)
        if post_fallback_calls and not self._has_unknown_tool_calls_runtime(post_fallback_answer):
            return self.resolve_tool_calls(
                _canonicalize_tool_calls(post_fallback_calls),
                max_steps=1,
                allow_safe_fallback=False,
            )
        return post_fallback_answer

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
            self._emit_actor_state("router", "ROUTING", "Router dostarcza kolejkę zaleceń Kastora do Polluksa")
            enriched = wrapped + "\n\n" + mailbox_context
            return self.chat_service.ask(enriched, actor="Sponsor")
        return self.chat_service.ask(wrapped, actor="Sponsor")

    def enqueue_supervisor_message(
        self,
        *,
        stage: str,
        reason_code: str,
        notes: str,
        answer: str,
    ) -> None:
        """Build supervisor outbox payload, dedup, log, and emit event."""
        tool_calls = parse_tool_calls(answer)
        suggested_step = ""
        runtime_supported = self.runtime_supported_tool_names()
        if tool_calls:
            first_supported = next(
                (call for call in tool_calls if canonical_tool_name(call.tool) in runtime_supported),
                None,
            )
            if first_supported is not None:
                suggested_step = f"{canonical_tool_name(first_supported.tool)} ({first_supported.intent})"

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

        # Emit event so adapters can route addressed blocks to panels
        self._emit_supervisor_message(
            stage=stage,
            reason_code=reason_code,
            notes=notes,
            answer=answer,
        )

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def submit_user_turn(self, text: str) -> None:
        """Enqueue a user message and start processing.

        If a router cycle is already in progress the message is queued and
        will be drained automatically when the current cycle finishes.
        """
        if self._router_cycle_in_progress:
            self._user_message_queue.append(text)
            queue_pos = len(self._user_message_queue)
            self._emit_log(
                "user_model_log",
                f"Wiadomość zakolejkowana (pozycja {queue_pos}). "
                "Router obsłuży ją po zakończeniu bieżącego kroku.",
            )
            self._emit_actor_state(
                "terminal", "QUEUED",
                f"Zakolejkowano wiadomość ({queue_pos} w kolejce)",
            )
            return

        self._dispatch_user_turn(text)

    def _dispatch_user_turn(self, text: str) -> None:
        """Start background processing of a user turn."""
        if self._background_enabled:
            worker = threading.Thread(
                target=self._process_user_turn,
                args=(text,),
                daemon=True,
                name="amiagi-engine-user-turn",
            )
            self._last_background_worker = worker
            worker.start()
        else:
            self._process_user_turn(text)

    def _drain_user_queue(self) -> None:
        if not self._user_message_queue:
            return
        if self._router_cycle_in_progress:
            return
        next_text = self._user_message_queue.popleft()
        remaining = len(self._user_message_queue)
        if remaining:
            self._emit_actor_state(
                "terminal", "QUEUED", f"Pozostało {remaining} w kolejce"
            )
        self._dispatch_user_turn(next_text)

    # ------------------------------------------------------------------
    # Plan tracking helpers
    # ------------------------------------------------------------------

    def has_actionable_plan(self) -> bool:
        """Return True if a main_plan.json exists with incomplete tasks."""
        plan_path = self.work_dir / "notes" / "main_plan.json"
        if not plan_path.exists():
            return False
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
            tasks = data.get("tasks", [])
            return any(t.get("status", "") != "zakończona" for t in tasks)
        except Exception:
            return False

    def plan_requires_update(self) -> bool:
        """Return True if the plan file is missing, empty, or corrupt."""
        plan_path = self.work_dir / "notes" / "main_plan.json"
        if not plan_path.exists():
            return True
        try:
            text = plan_path.read_text(encoding="utf-8").strip()
            if not text:
                return True
            data = json.loads(text)
            return not data.get("tasks")
        except Exception:
            return True

    def set_plan_paused(self, *, paused: bool, reason: str, source: str) -> None:
        self._plan_pause_active = paused
        if paused:
            self._plan_pause_started_monotonic = time.monotonic()
        else:
            self._plan_pause_started_monotonic = 0.0
        self._plan_pause_reason = reason if paused else ""
        self._log_activity(
            action="plan.pause" if paused else "plan.resume",
            intent=f"Plan {'wstrzymany' if paused else 'wznowiony'}: {reason}",
            details={"source": source, "reason": reason},
        )
        self._append_plan_event(
            "plan_pause_changed",
            {"paused": paused, "reason": reason, "source": source},
        )

    def handle_user_decision(self, text: str) -> str | None:
        """Handle user input during pending_user_decision state.

        Returns a UI message string if the decision was handled, or None if
        the text was not recognized as a decision keyword (caller should
        treat it as a normal user turn).
        """
        if not self._pending_user_decision:
            return None

        decision = self._extract_pause_decision(text)
        if decision is None:
            return None

        if decision == "continue":
            self._record_collaboration_signal("cooperate", {"decision": "continue"})
            self._last_progress_monotonic = time.monotonic()
            self.auto_resume_tick(time.monotonic(), force=True)
            return _("user_turn.plan_continue")

        if decision == "stop":
            self._pending_user_decision = False
            self._pending_decision_identity_query = False
            self.set_plan_paused(paused=False, reason="user_stop", source="user")
            self._record_collaboration_signal("user_stopped_plan", {"decision": "stop"})
            return _("user_turn.plan_stopped")

        if decision == "new_task":
            self._pending_user_decision = False
            self._pending_decision_identity_query = False
            self.set_plan_paused(paused=False, reason="new_task", source="user")
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
            plan_path = self.work_dir / "notes" / "main_plan.json"
            try:
                plan_path.parent.mkdir(parents=True, exist_ok=True)
                plan_path.write_text(
                    json.dumps(new_plan, ensure_ascii=False, indent=2), encoding="utf-8",
                )
            except Exception:
                pass
            self._record_collaboration_signal("new_plan_created", {"goal": new_goal[:200]})
            return _("user_turn.new_plan_created")

        return None

    @staticmethod
    def _extract_pause_decision(text: str) -> str | None:
        normalized = " ".join(text.strip().lower().split())
        if normalized in {"kontynuuj", "wznów", "wznow", "wznow plan", "kontynuuj plan"}:
            return "continue"
        if normalized in {"przerwij", "stop", "przerwij plan", "zatrzymaj"}:
            return "stop"
        if normalized.startswith("nowe zadanie"):
            return "new_task"
        return None

    # ------------------------------------------------------------------
    # Conversational analysis helpers
    # ------------------------------------------------------------------

    def is_conversational_interrupt(self, text: str) -> bool:
        normalized = " ".join(text.strip().lower().split())
        return any(marker in normalized for marker in _CONVERSATIONAL_INTERRUPT_MARKERS)

    def is_identity_query(self, text: str) -> bool:
        normalized = " ".join(text.strip().lower().split())
        return any(marker in normalized for marker in _IDENTITY_QUERY_MARKERS)

    # ------------------------------------------------------------------
    # Mailbox / outbox
    # ------------------------------------------------------------------

    def append_router_mailbox_log(self, entry: dict[str, Any]) -> None:
        """Append a JSON line to the router mailbox log file (legacy compat)."""
        self._append_router_mailbox_log("raw", entry)

    # ------------------------------------------------------------------
    # Stubs — to be filled during Faza 3
    # ------------------------------------------------------------------

    # (Faza 1 stubs — execute_tool_call/resolve_tool_calls — are now implemented below)

    # ------------------------------------------------------------------
    # User-turn orchestration helpers (Faza 2)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_user_facing_answer(answer: str) -> str:
        """Convert a raw model answer to a user-friendly display string."""
        stripped = (answer or "").strip()
        if not stripped:
            return "Wykonałem krok operacyjny i kontynuuję pracę."
        tool_calls = parse_tool_calls(stripped)
        contains_tool_payload = (
            bool(tool_calls) or "[TOOL_CALL]" in stripped or "```tool_call" in stripped
        )
        if not contains_tool_payload:
            return answer
        if tool_calls:
            first_call = tool_calls[0]
            intent = (first_call.intent or "krok roboczy").strip()
            return (
                f"Wykonałem krok operacyjny narzędziem "
                f"'{first_call.tool}' ({intent}) i kontynuuję realizację zadania."
            )
        return "Wykonałem krok operacyjny i kontynuuję realizację zadania."

    @staticmethod
    def _render_single_tool_call_block(tool_call: ToolCall) -> str:
        canonical = canonical_tool_name(tool_call.tool)
        payload = {"tool": canonical, "args": tool_call.args, "intent": tool_call.intent}
        return "```tool_call\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

    @staticmethod
    def _single_sentence(text: str) -> str:
        compact = " ".join(text.strip().split())
        if not compact:
            return _("identity.reply")
        for idx, char in enumerate(compact):
            if char in ".!?":
                sentence = compact[: idx + 1].strip()
                return sentence or _("identity.reply")
        return compact[:220]

    def _is_model_access_allowed(self) -> tuple[bool, str]:
        """Check if permission manager allows network access to model."""
        base_url = self.chat_service.ollama_client.base_url
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        network_resource = "network.local" if host in {"127.0.0.1", "localhost", "::1"} else "network.internet"
        if self.permission_manager.allow_all:
            return True, network_resource
        if network_resource in getattr(self.permission_manager, "granted_once", set()):
            return True, network_resource
        return False, network_resource

    def _model_response_awaits_user(self, answer: str) -> bool:
        if not answer or not answer.strip():
            return False
        if self.has_supported_tool_call(answer):
            return False
        stripped = answer.rstrip()
        last_line = stripped.rsplit("\n", 1)[-1].strip()
        if last_line.endswith("?"):
            return True
        normalized = " ".join(stripped.lower().split())
        return any(marker in normalized for marker in _MODEL_QUESTION_MARKERS)

    def _is_premature_plan_completion(self, answer: str) -> bool:
        normalized = answer.strip().lower()
        if _COMPLETION_SIGNAL in normalized:
            return False
        plan_path = self.work_dir / "notes" / "main_plan.json"
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
                for t in tasks if isinstance(t, dict)
            )
            if all_done:
                return True
        return False

    def _redirect_premature_completion(self, user_message: str, polluks_answer: str) -> str | None:
        supervisor = self.chat_service.supervisor_service
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
            self._emit_actor_state("supervisor", "REDIRECT", "Kastor przekierowuje Polluksa po przedwczesnym zakończeniu planu")
            recent_msgs = self.chat_service.memory_repository.recent_messages(limit=6)
            conv_excerpt = format_conversation_excerpt(recent_msgs, limit=6)
            result = supervisor.refine(
                user_message=prompt, model_answer=polluks_answer,
                stage="premature_completion_redirect", conversation_excerpt=conv_excerpt,
            )
            self._emit_actor_state("supervisor", "READY", "Kastor zakończył przekierowanie")
            self.enqueue_supervisor_message(
                stage="premature_completion_redirect",
                reason_code=result.reason_code or "PREMATURE_COMPLETION",
                notes=self._merge_supervisor_notes(
                    "Kastor przekierował Polluksa po przedwczesnym zakończeniu planu.", result.notes,
                ),
                answer=result.answer,
            )
            self._emit_log(
                "supervisor_log",
                f"[Kastor -> Polluks] Plan nie jest ukończony wobec zadania Sponsora. {result.notes[:300]}",
            )
            return result.answer
        except (OllamaClientError, OSError):
            self._emit_actor_state("supervisor", "ERROR", "Kastor — przekierowanie przerwane błędem")
            return None

    def _append_plan_event(self, event_type: str, payload: dict) -> None:
        plan_path = self.work_dir / "notes" / "main_plan.json"
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
        history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type, "payload": payload,
        })
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

    def _apply_idle_hint_from_answer(self, answer: str, source: str) -> None:
        marker = "IDLE_UNTIL:"
        if marker not in answer:
            return
        tail = answer.split(marker, 1)[1].strip().splitlines()[0].strip()
        if tail.lower() in {"off", "none", "false", "0", "wyłącz", "wylacz"}:
            self._idle_until_epoch = None
            self._idle_until_source = ""
            return
        # Try parsing ISO timestamp
        try:
            parsed_dt = datetime.fromisoformat(tail)
            self._idle_until_epoch = parsed_dt.timestamp()
            self._idle_until_source = source
        except (ValueError, TypeError):
            pass

    def _enforce_supervised_progress(
        self,
        user_message: str,
        initial_answer: str,
        max_attempts: int = 3,
        allow_text_reply: bool = False,
    ) -> str:
        supervisor = self.chat_service.supervisor_service
        if supervisor is None:
            return initial_answer
        current = initial_answer
        if allow_text_reply and not self.has_supported_tool_call(current):
            return current
        for attempt in range(1, max_attempts + 1):
            has_supported_tool = self.has_supported_tool_call(current)
            plan_required = self.plan_requires_update()
            if has_supported_tool and not plan_required:
                return current
            self._emit_actor_state(
                "supervisor", "PROGRESS_GUARD",
                f"Kastor wymusza postęp (próba {attempt}/{max_attempts})",
            )
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
            available_tools_list = sorted(self.runtime_supported_tool_names())
            corrective_prompt = (
                "[RUNTIME_SUPERVISION_CONTEXT]\n"
                + json.dumps(supervision_context, ensure_ascii=False)
                + "\n[/RUNTIME_SUPERVISION_CONTEXT]\n"
                + corrective_instruction
                + "\nDOSTĘPNE NARZĘDZIA: " + ", ".join(available_tools_list) + "."
                + "\nUżywaj WYŁĄCZNIE narzędzi z powyższej listy. Nie używaj nazw, których tu nie ma."
                + "\nPolecenie użytkownika: " + user_message
            )
            try:
                result = supervisor.refine(
                    user_message=corrective_prompt, model_answer=current,
                    stage="textual_progress_guard",
                )
            except (OllamaClientError, OSError):
                self._emit_actor_state("supervisor", "READY", "Kastor — progress guard przerwany błędem")
                return current
            refined_calls = parse_tool_calls(result.answer)
            runtime_supported = self.runtime_supported_tool_names()
            first_supported = next(
                (call for call in refined_calls if canonical_tool_name(call.tool) in runtime_supported),
                None,
            )
            self._emit_actor_state("supervisor", "READY", "Kastor zakończył progress guard")
            if first_supported is not None:
                current = self._render_single_tool_call_block(first_supported)
            elif plan_required:
                fallback_plan = {
                    "goal": (user_message.strip() or "Kontynuacja głównego celu użytkownika")[:200],
                    "key_achievement": "Zainicjalizowany plan z kolejnym krokiem operacyjnym.",
                    "current_stage": "inicjalizacja_planu",
                    "tasks": [{"id": "T1", "title": "Zainicjalizować plan główny",
                               "status": "rozpoczęta",
                               "next_step": "Wykonać pierwszy krok narzędziowy po zapisie planu."}],
                }
                current = (
                    "```tool_call\n"
                    + json.dumps({
                        "tool": "write_file",
                        "args": {"path": "notes/main_plan.json",
                                 "content": json.dumps(fallback_plan, ensure_ascii=False),
                                 "overwrite": True},
                        "intent": "init_plan_fallback",
                    }, ensure_ascii=False)
                    + "\n```"
                )
            else:
                current = (
                    "```tool_call\n"
                    + json.dumps({
                        "tool": "list_dir", "args": {"path": "."},
                        "intent": "fallback_after_invalid_supervisor_repair",
                    }, ensure_ascii=False)
                    + "\n```"
                )
        return current

    # ------------------------------------------------------------------
    # Full user-turn cycle — CLI variant
    # ------------------------------------------------------------------

    def _process_cli_user_turn(self, user_message: str) -> bool:
        """Synchronous CLI user-turn pipeline.

        Returns ``True`` when the turn was processed normally,
        ``False`` when network access was denied (caller should skip).
        """
        self._log_activity(
            action="chat.message",
            intent="Przetwarzanie standardowej wiadomości użytkownika.",
        )
        self._last_user_message = user_message
        self._idle_reactivation_attempts = 0
        self._idle_reactivation_capped_notified = False

        # --- network permission ---
        network_resource = _network_resource_for_model(
            self.chat_service.ollama_client.base_url,
        )
        network_reason = (
            "Połączenie z lokalnym API modelu wymaga dostępu do sieci lokalnej."
            if network_resource == "network.local"
            else "Połączenie z modelem wymaga dostępu do internetu/sieci zewnętrznej."
        )
        if network_resource == "network.local":
            if not self.permission_manager.request_local_network(network_reason):
                self._log_activity(action="chat.denied", intent="Odmowa dostępu do sieci lokalnej.")
                return False
        else:
            if not self.permission_manager.request_internet(network_reason):
                self._log_activity(action="chat.denied", intent="Odmowa dostępu do sieci zewnętrznej.")
                return False

        plan_fingerprint_before = self._main_plan_fingerprint()

        answer = self._ask_executor_with_router_mailbox(user_message)
        answer = self._apply_supervisor(user_message, answer, stage="user_turn")

        # passive streak corrective
        if self._passive_turns >= 1 and not self.has_supported_tool_call(answer):
            intro_candidates = [
                self.work_dir / "wprowadzenie.md",
                self.work_dir.parent / "wprowadzenie.md",
            ]
            intro_path = next((p for p in intro_candidates if p.exists()), None)
            intro_hint = (
                str(intro_path.resolve()) if intro_path is not None else "wprowadzenie.md"
            )
            corrective_prompt = _build_no_action_corrective_prompt(user_message, intro_hint)
            answer = self._apply_supervisor(
                corrective_prompt, answer, stage="user_turn_passive_streak",
            )
            self._log_activity(
                action="chat.passive_streak.corrective",
                intent="Uruchomiono nadzorczą korektę po kolejnych pasywnych turach użytkownika.",
                details={"passive_turns_before": self._passive_turns},
            )

        answer = self._enforce_actionable_autonomy(user_message, answer)

        tool_activity_before = self._last_tool_activity_monotonic
        answer = self.resolve_tool_calls(answer)
        answer = self._ensure_plan_persisted(user_message, answer)

        had_runtime_progress = self._last_tool_activity_monotonic > tool_activity_before
        if had_runtime_progress and not self.has_supported_tool_call(answer):
            self._passive_turns = 0
        else:
            self._passive_turns += 1

        answer = self._update_plan_fingerprint_tracking(
            user_message, answer, plan_fingerprint_before,
        )

        display_answer = self._format_user_facing_answer(answer)
        self._emit_log("model", display_answer)
        self._log_activity(
            action="chat.response",
            intent="Zwrócono odpowiedź modelu użytkownikowi.",
            details={"chars": len(answer), "display_chars": len(display_answer)},
        )
        return True

    # ------------------------------------------------------------------
    # Full user-turn cycle (Faza 2)
    # ------------------------------------------------------------------

    def _process_user_turn(self, text: str) -> None:
        """Full user-turn orchestration extracted from textual_cli."""

        self._emit_actor_state("terminal", "INPUT_READY", "Wysłano wiadomość do routera")

        allowed, network_resource = self._is_model_access_allowed()
        if not allowed:
            network_label = "sieci lokalnej" if network_resource == "network.local" else "internetu"
            self._emit_log(
                "user_model_log",
                f"Odmowa: brak aktywnej zgody na dostęp do {network_label}. "
                "Użyj /permissions all, aby odblokować zapytania modelu.",
            )
            self._emit_actor_state("terminal", "WAITING_INPUT", "Odmowa dostępu — oczekiwanie na wiadomość")
            return

        if text.lower() in {"/quit", "/exit"}:
            # Signal adapter to exit via CycleFinishedEvent with special event
            self._emit_cycle_finished("quit_requested")
            return

        self._log_activity(
            action="user.input",
            intent="Wiadomość użytkownika przekazana do routera.",
            details={"chars": len(text)},
        )
        self._router_cycle_in_progress = True
        self._emit_actor_state("terminal", "BUSY", "Terminal przekazał wiadomość")
        self._emit_actor_state("router", "ROUTING", "Router przekazuje polecenie do Polluksa")
        self._emit_actor_state("creator", "THINKING", "Polluks analizuje polecenie")
        self._last_user_message = text
        self._watchdog_attempts = 0
        self._watchdog_capped_notified = False
        self._watchdog_suspended_until_user_input = False

        interrupt_mode = self.is_conversational_interrupt(text)
        identity_query = self.is_identity_query(text)

        if interrupt_mode:
            self.set_plan_paused(paused=True, reason="user_interrupt", source="on_input_submitted")
            self._pending_user_decision = True
            self._pending_decision_identity_query = identity_query
            self._emit_actor_state("router", "INTERRUPTED", "Wykryto pytanie wtrącające użytkownika")
            self._record_collaboration_signal("interrupt_enter", {"message": text[:200]})

        plan_fingerprint_before = self._main_plan_fingerprint()

        try:
            answer = self._ask_executor_with_router_mailbox(text)
            self._emit_actor_state("creator", "ANSWER_READY", "Polluks wygenerował odpowiedź")

            if self.chat_service.supervisor_service is not None:
                self._emit_actor_state("supervisor", "REVIEWING", "Kastor analizuje odpowiedź Polluksa")
                passive_turns_after = self._passive_turns + (0 if self.has_supported_tool_call(answer) else 1)
                should_remind = (passive_turns_after >= 2) and not interrupt_mode
                supervision_context = {
                    "passive_turns": passive_turns_after,
                    "should_remind_continuation": should_remind,
                    "gpu_busy_over_50": False,
                    "plan_persistence": {"required": False},
                    "interrupt_mode": interrupt_mode,
                    "identity_query": identity_query,
                }
                supervision_user_message = (
                    "[RUNTIME_SUPERVISION_CONTEXT]\n"
                    + json.dumps(supervision_context, ensure_ascii=False)
                    + "\n[/RUNTIME_SUPERVISION_CONTEXT]\n" + text
                )
                recent_msgs = self.chat_service.memory_repository.recent_messages(limit=6)
                conv_excerpt = format_conversation_excerpt(recent_msgs, limit=6)
                supervision_result = self.chat_service.supervisor_service.refine(
                    user_message=supervision_user_message,
                    model_answer=answer, stage="user_turn",
                    conversation_excerpt=conv_excerpt,
                )
                answer = supervision_result.answer
                self.enqueue_supervisor_message(
                    stage="user_turn",
                    reason_code=supervision_result.reason_code,
                    notes=self._merge_supervisor_notes(
                        "Ocena odpowiedzi Polluksa w turze użytkownika.",
                        supervision_result.notes,
                    ),
                    answer=answer,
                )
                self._emit_actor_state("supervisor", "READY", "Kastor zakończył analizę")

                if supervision_result.work_state == "WAITING_USER_DECISION" and not interrupt_mode:
                    self.set_plan_paused(paused=True, reason="supervisor_awaits_user", source="user_turn_supervision")
                    self._pending_user_decision = True
                    self._pending_decision_identity_query = False
                    self._watchdog_suspended_until_user_input = True
                    self._emit_actor_state("router", "PAUSED", "Kastor zgłosił WAITING_USER_DECISION")
                    self._record_collaboration_signal("supervisor_awaits_user", {"work_state": supervision_result.work_state})

                if interrupt_mode:
                    if identity_query:
                        self._record_collaboration_signal("cooperate", {"phase": "interrupt_user_turn", "reason": "identity_query"})
                        answer = _("identity.reply")
                    elif self.has_supported_tool_call(answer):
                        self._record_collaboration_signal("misalignment", {"phase": "interrupt_user_turn", "reason": "tool_call_in_interrupt"})
                        answer = _("identity.reply")
                    else:
                        self._record_collaboration_signal("cooperate", {"phase": "interrupt_user_turn", "reason_code": supervision_result.reason_code})
                    base_sentence = _("identity.reply") if identity_query else self._single_sentence(answer)
                    answer = base_sentence + _("identity.followup_question")

                if should_remind and not self.has_supported_tool_call(answer):
                    self._emit_actor_state("supervisor", "CORRECTING", "Kastor wymusza krok operacyjny po pasywnej odpowiedzi")
                    corrective_prompt = (
                        "Twoja poprzednia odpowiedź nie rozpoczęła realnego działania frameworka. "
                        "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call jako najbliższy krok operacyjny. "
                        "Bez opisu i bez pseudo-kodu.\n\n"
                        f"Polecenie użytkownika: {text}"
                    )
                    corrective_result = self.chat_service.supervisor_service.refine(
                        user_message=corrective_prompt, model_answer=answer,
                        stage="textual_no_action_corrective",
                    )
                    answer = corrective_result.answer
                    self.enqueue_supervisor_message(
                        stage="textual_no_action_corrective",
                        reason_code=corrective_result.reason_code,
                        notes=self._merge_supervisor_notes(
                            "Wymuszenie kroku operacyjnego po pasywnej odpowiedzi.",
                            corrective_result.notes,
                        ),
                        answer=answer,
                    )
                    self._emit_actor_state("supervisor", "READY", "Kastor zakończył korektę pasywnej odpowiedzi")

        except OllamaClientError as error:
            self._emit_log("user_model_log", f"Błąd modelu/Ollama: {error}. Sprawdź połączenie i dostępność modelu.")
            self._emit_actor_state("creator", "ERROR", "Błąd połączenia z modelem")
            self._emit_cycle_finished("Router zakończył cykl z błędem modelu")
            return
        except OSError as error:
            self._emit_log("user_model_log", f"Błąd systemowy: {error}")
            self._emit_actor_state("creator", "ERROR", "Błąd systemowy podczas wykonania")
            self._emit_cycle_finished("Router zakończył cykl z błędem systemowym")
            return

        self._apply_idle_hint_from_answer(answer, source="creator")

        self._emit_actor_state("router", "PROGRESS_GUARD", "Router weryfikuje postęp operacyjny")
        answer = self._enforce_supervised_progress(text, answer, allow_text_reply=interrupt_mode)

        self._emit_actor_state("router", "TOOL_FLOW", "Router realizuje wywołania narzędzi")
        answer = self.resolve_tool_calls(answer)
        self._apply_idle_hint_from_answer(answer, source="router")
        self._last_model_answer = answer

        # --- Plan persistence enforcement ---
        answer = self._ensure_plan_persisted(text, answer)

        if self.has_supported_tool_call(answer):
            self._passive_turns += 1
            self._emit_actor_state("creator", "PASSIVE", "Pozostał nierozwiązany krok narzędziowy")
        else:
            if interrupt_mode:
                self._passive_turns = 0
                self._last_progress_monotonic = time.monotonic()
            else:
                self._passive_turns += 1
            self._emit_actor_state("creator", "PASSIVE", "Brak kroku narzędziowego")

        # --- Plan fingerprint tracking & stale-plan detection ---
        answer = self._update_plan_fingerprint_tracking(text, answer, plan_fingerprint_before)

        # --- Model asks user a question → pause plan & suspend watchdog ---
        if not interrupt_mode and self._model_response_awaits_user(answer):
            if self._is_premature_plan_completion(answer):
                self._emit_actor_state("router", "REDIRECT", "Przedwczesne zakończenie planu — Kastor przekierowuje Polluksa")
                redirected = self._redirect_premature_completion(text, answer)
                if redirected is not None:
                    answer = redirected
                    self._passive_turns = 0
                    self._last_progress_monotonic = time.monotonic()
                else:
                    self._pause_for_user_decision(answer, "model_awaits_user", "process_user_turn")
            else:
                self._pause_for_user_decision(answer, "model_awaits_user", "process_user_turn")

        # --- Communication protocol: addressed block routing ---
        display_answer = self._format_user_facing_answer(answer)
        self._emit_actor_state("router", "DELIVERING", "Router kieruje bloki komunikacyjne na panele")
        self._route_addressed_blocks(answer, display_answer)

        self._emit_log("executor_log", f"[user_turn] {answer}")
        self._emit_cycle_finished("Router dostarczył odpowiedź użytkownikowi")

        if self.chat_service.supervisor_service is None:
            self._emit_log("supervisor_log", _("mount.kastor_inactive_panel"))
        self.poll_supervision_dialogue()

    def _pause_for_user_decision(self, answer: str, reason: str, source: str) -> None:
        """Set plan paused + watchdog suspended when model awaits user."""
        self.set_plan_paused(paused=True, reason=reason, source=source)
        self._pending_user_decision = True
        self._pending_decision_identity_query = False
        self._watchdog_suspended_until_user_input = True
        self._emit_actor_state("router", "PAUSED", "Model oczekuje na decyzję użytkownika")
        self._record_collaboration_signal("model_awaits_user", {"source": source, "excerpt": answer[-200:]})

    def _route_addressed_blocks(self, answer: str, display_answer: str) -> None:
        """Parse addressed blocks and route to appropriate log panels via events."""
        blocks = parse_addressed_blocks(answer)
        routed_to_user_panel = False
        self._consultation_rounds_this_cycle = 0

        if blocks:
            self._unaddressed_turns = 0
            panel_map = self._comm_rules.panel_mapping or None
            for block in blocks:
                target_panels = panels_for_target(block.target, panel_map)
                label = f"[{block.sender} -> {block.target}]" if block.sender else ""

                # Sanitize content for user panel
                sponsor_targeted = "user_model_log" in target_panels
                block_content = block.content
                if sponsor_targeted:
                    sanitized = strip_tool_call_blocks(block_content)
                    if not sanitized or not is_sponsor_readable(sanitized):
                        self._emit_log("executor_log", f"{label} {block_content}" if label else block_content)
                        self._emit_log("supervisor_log", _("coordinator.tool_redirected"))
                        continue
                    if sanitized != block_content:
                        self._emit_log("executor_log", f"{label} {block_content}" if label else block_content)
                    block_content = sanitized

                for panel_id in target_panels:
                    self._emit_log(panel_id, f"{label} {block_content}" if label else block_content)
                if sponsor_targeted:
                    routed_to_user_panel = True

                # Consultation: Polluks -> Kastor
                max_consult = getattr(self._comm_rules, "consultation_max_rounds", 1)
                if (
                    block.sender == "Polluks"
                    and block.target == "Kastor"
                    and self.chat_service.supervisor_service is not None
                    and self._consultation_rounds_this_cycle < max_consult
                ):
                    self._consultation_rounds_this_cycle += 1
                    self._emit_actor_state("supervisor", "CONSULTING", "Kastor otrzymał konsultację od Polluksa")
                    try:
                        consult_result = self.chat_service.supervisor_service.refine(
                            user_message=f"[Polluks -> Kastor] {block.content}",
                            model_answer=block.content, stage="consultation",
                        )
                        self.enqueue_supervisor_message(
                            stage="consultation",
                            reason_code=consult_result.reason_code,
                            notes=self._merge_supervisor_notes(
                                "Odpowiedź Kastora na konsultację Polluksa.",
                                consult_result.notes,
                            ),
                            answer=consult_result.answer,
                        )
                        self._emit_log("supervisor_log", f"[Kastor -> Polluks] {consult_result.answer}")
                    except (OllamaClientError, OSError):
                        self._emit_log("supervisor_log", _("watchdog.consult_error"))
                    self._emit_actor_state("supervisor", "READY", "Kastor zakończył konsultację")
        else:
            if not parse_tool_calls(answer):
                self._unaddressed_turns += 1
                reminder_threshold = self._comm_rules.missing_header_threshold
                max_reminders = self._comm_rules.max_reminders_per_session
                if self._unaddressed_turns >= reminder_threshold and self._reminder_count < max_reminders:
                    self._emit_actor_state("router", "REMINDING", "Koordynator wysyła przypomnienie o adresowaniu")
                    reminder_text = self._comm_rules.reminder_template or (
                        "[Kastor -> Polluks] Przypominam: każdy komunikat musi zaczynać się "
                        "od nagłówka [Polluks -> Odbiorca]. Popraw format odpowiedzi."
                    )
                    self._emit_log("supervisor_log", reminder_text)
                    self.enqueue_supervisor_message(
                        stage="addressing_reminder", reason_code="MISSING_HEADER",
                        notes=reminder_text[:500], answer="",
                    )
                    self._unaddressed_turns = 0
                    self._reminder_count += 1

        if not routed_to_user_panel:
            self._emit_log("user_model_log", f"Model: {display_answer}")


    def execute_tool_call(self, tool_call: ToolCall, *, agent_id: str = "") -> dict[str, Any]:
        """Execute a single tool call and return a result dict."""
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
            if not is_path_within_work_dir(path, self.work_dir):
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
            if path.suffix.lower() in {".json", ".jsonl"} and not content.strip():
                return {
                    "ok": False,
                    "error": "empty_content_not_allowed_for_json",
                    "path": str(path),
                }
            overwrite = bool(args.get("overwrite", False))
            # Auto-allow overwrite for the main plan tracking file
            if not overwrite and self._is_main_plan_path(path):
                overwrite = True
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
            if not is_path_within_work_dir(path, self.work_dir):
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
            if not is_path_within_work_dir(path, self.work_dir):
                return {"ok": False, "error": "path_outside_work_dir", "path": str(path)}
            run_args = args.get("args", [])
            if not isinstance(run_args, list):
                return {"ok": False, "error": "args_must_be_list"}
            if not path.exists() or not path.is_file():
                return {"ok": False, "error": "file_not_found", "path": str(path)}
            result = self.script_executor.execute_python(path, [str(item) for item in run_args])
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
            result = self.script_executor.execute_shell(command_text)
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
                downloads_dir = self.work_dir / "downloads"
                filename = Path(parsed.path).name or "download"
                output_path = downloads_dir / filename
            if not is_path_within_work_dir(output_path, self.work_dir):
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
                converted_dir = self.work_dir / "converted"
                out_path = converted_dir / (src_path.stem + ".md")
            if not is_path_within_work_dir(out_path, self.work_dir):
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
            results = parse_search_results_from_html(content, engine=engine, max_results=max_results)
            return {
                "ok": True,
                "tool": "search_web",
                "engine": engine,
                "query": query,
                "results": results,
                "results_count": len(results),
                "search_url": search_url,
            }

        if tool == "capture_camera_frame":
            output_arg = str(args.get("output_path", "")).strip()
            device = str(args.get("device", "/dev/video0")).strip() or "/dev/video0"
            if output_arg:
                output_path = self._resolve_model_path(output_arg)
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = self.work_dir / "artifacts" / f"camera_{timestamp}.jpg"
            if not is_path_within_work_dir(output_path, self.work_dir):
                return {"ok": False, "error": "path_outside_work_dir", "path": str(output_path), "work_dir": str(self.work_dir)}
            if not self._ensure_resource("camera", "Tool capture_camera_frame wymaga dostępu do kamery."):
                return {"ok": False, "error": "permission_denied:camera"}
            if not self._ensure_resource("disk.write", "Tool capture_camera_frame wymaga zapisu pliku obrazu."):
                return {"ok": False, "error": "permission_denied:disk.write"}
            if not self._ensure_resource("process.exec", "Tool capture_camera_frame wymaga wykonania procesu systemowego."):
                return {"ok": False, "error": "permission_denied:process.exec"}
            if not Path(device).exists():
                return {"ok": False, "error": "camera_device_not_found", "device": device}
            v4l2_ctl = shutil.which("v4l2-ctl")
            if v4l2_ctl is None:
                return {"ok": False, "error": "camera_init_tool_missing", "details": "Zainstaluj v4l-utils (v4l2-ctl)."}
            init_completed = subprocess.run(
                [v4l2_ctl, "-d", device, "--set-ctrl=auto_exposure=1"],
                check=False, capture_output=True, text=True, timeout=10,
            )
            if init_completed.returncode != 0:
                return {
                    "ok": False, "error": "camera_init_failed", "device": device,
                    "exit_code": init_completed.returncode,
                    "stdout": init_completed.stdout, "stderr": init_completed.stderr,
                }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            fswebcam = shutil.which("fswebcam")
            ffmpeg = shutil.which("ffmpeg")
            if fswebcam:
                command = [fswebcam, "-q", "-d", device, str(output_path)]
            elif ffmpeg:
                command = [ffmpeg, "-y", "-f", "video4linux2", "-i", device, "-frames:v", "1", str(output_path)]
            else:
                return {"ok": False, "error": "camera_backend_missing", "details": "Zainstaluj fswebcam lub ffmpeg."}
            completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
            if completed.returncode != 0 or not output_path.exists():
                return {
                    "ok": False, "error": "camera_capture_failed",
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout, "stderr": completed.stderr,
                }
            return {
                "ok": True, "tool": "capture_camera_frame",
                "path": str(output_path), "device": device,
                "size_bytes": output_path.stat().st_size,
            }

        if tool == "record_microphone_clip":
            output_arg = str(args.get("output_path", "")).strip()
            if output_arg:
                output_path = self._resolve_model_path(output_arg)
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = self.work_dir / "artifacts" / f"microphone_{timestamp}.wav"
            duration_seconds = max(1, min(60, int(args.get("duration_seconds", 5))))
            sample_rate_hz = max(8000, min(48000, int(args.get("sample_rate_hz", 16000))))
            channels = max(1, min(2, int(args.get("channels", 1))))
            explicit_device = str(args.get("device", "")).strip()
            if not is_path_within_work_dir(output_path, self.work_dir):
                return {"ok": False, "error": "path_outside_work_dir", "path": str(output_path), "work_dir": str(self.work_dir)}
            if not self._ensure_resource("microphone", "Tool record_microphone_clip wymaga dostępu do mikrofonu."):
                return {"ok": False, "error": "permission_denied:microphone"}
            if not self._ensure_resource("disk.write", "Tool record_microphone_clip wymaga zapisu pliku audio."):
                return {"ok": False, "error": "permission_denied:disk.write"}
            if not self._ensure_resource("process.exec", "Tool record_microphone_clip wymaga wykonania procesu systemowego."):
                return {"ok": False, "error": "permission_denied:process.exec"}
            arecord = shutil.which("arecord")
            if arecord is None:
                return {"ok": False, "error": "microphone_backend_missing", "details": "Zainstaluj pakiet ALSA (arecord)."}
            preferred_device = explicit_device or _detect_preferred_microphone_device()
            profiles = _build_microphone_profiles(sample_rate_hz, channels)
            self._emit_log("system", "[MIC] Przygotowanie nagrywania mikrofonu.")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            last_completed: subprocess.CompletedProcess[str] | None = None
            used_profile: tuple[int, int] | None = None
            used_device = preferred_device or "default"
            for index, (profile_rate, profile_channels) in enumerate(profiles, start=1):
                self._emit_log("system", "[MIC] Nagrywanie aktywne.")
                command = [
                    arecord, "-q", "-d", str(duration_seconds),
                    "-f", "S16_LE", "-r", str(profile_rate), "-c", str(profile_channels),
                ]
                if preferred_device:
                    command.extend(["-D", preferred_device])
                command.append(str(output_path))
                completed_proc = subprocess.run(
                    command, check=False, capture_output=True, text=True,
                    timeout=duration_seconds + 10,
                )
                last_completed = completed_proc
                if completed_proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                    used_profile = (profile_rate, profile_channels)
                    break
            if used_profile is None:
                self._emit_log("system", "[MIC] Nagrywanie nieudane.")
                return {
                    "ok": False, "error": "microphone_record_failed",
                    "exit_code": last_completed.returncode if last_completed is not None else -1,
                    "stdout": last_completed.stdout if last_completed is not None else "",
                    "stderr": last_completed.stderr if last_completed is not None else "",
                    "device": used_device,
                    "attempted_profiles": [{"sample_rate_hz": r, "channels": c} for r, c in profiles],
                }
            self._emit_log("system", "[MIC] Nagrywanie zakończone.")
            return {
                "ok": True, "tool": "record_microphone_clip",
                "path": str(output_path), "duration_seconds": duration_seconds,
                "sample_rate_hz": used_profile[0], "channels": used_profile[1],
                "device": used_device, "size_bytes": output_path.stat().st_size,
            }

        if tool == "check_capabilities":
            check_network = bool(args.get("check_network", False))
            payload = {
                "tool": "check_capabilities",
                "python": shutil.which("python") is not None,
                "fswebcam": shutil.which("fswebcam") is not None,
                "ffmpeg": shutil.which("ffmpeg") is not None,
                "arecord": shutil.which("arecord") is not None,
                "camera_devices": sorted(str(p) for p in Path("/dev").glob("video*")),
                "network_checked": check_network,
            }
            if check_network:
                payload["ollama_reachable"] = bool(self.chat_service.ollama_client.ping())
            return {"ok": True, **payload}

        # ---- Human-in-the-Loop tools (ask_human, review_request) ----
        if tool == "ask_human":
            bridge = getattr(self, "_human_bridge", None)
            if bridge is None:
                return {"ok": False, "tool": "ask_human", "error": "HumanInteractionBridge not configured"}
            return bridge.ask_human(
                question=str(args.get("question", args.get("message", ""))),
                agent_id=agent_id,
                context=str(args.get("context", "")),
                priority=int(args.get("priority", 5)),
            )

        if tool == "review_request":
            bridge = getattr(self, "_human_bridge", None)
            if bridge is None:
                return {"ok": False, "tool": "review_request", "error": "HumanInteractionBridge not configured"}
            return bridge.request_review(
                title=str(args.get("title", "Review requested")),
                description=str(args.get("description", "")),
                content=str(args.get("content", "")),
                agent_id=agent_id,
                priority=int(args.get("priority", 3)),
            )

        custom_script = resolve_registered_tool_script(self.work_dir, tool)
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
            if not is_path_within_work_dir(custom_script, self.work_dir):
                return {
                    "ok": False,
                    "error": "path_outside_work_dir",
                    "tool": tool,
                    "path": str(custom_script),
                }
            result = self.script_executor.execute_python(custom_script, [json.dumps(args, ensure_ascii=False)])
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

    def resolve_tool_calls(
        self,
        initial_answer: str,
        *,
        max_steps: int = 15,
        allow_safe_fallback: bool = True,
    ) -> str:
        """Iteratively resolve tool calls in *initial_answer*.

        Returns the final text answer after all tool call → result → followup
        cycles have been resolved (or the loop limit is reached).

        This method merges two feature sets:
        - Engine-native: loop detection, unknown-tool escalation, actor-state
          emissions, error resilience.
        - CLI-origin: corrective prompts (unparsed / pseudo / python / no-action),
          safe fallback, ``code_path_failure_streak``, post-write verification,
          plan-tracking coherence, compact tool results, pre-execution supervisor
          gate.
        """
        current = initial_answer
        iteration = 0
        unknown_tool_correction_attempts: dict[str, int] = {}
        _MAX_CORRECTIONS_PER_TOOL = 2
        _tool_call_history: list[str] = []
        _MAX_SAME_TOOL_CONSECUTIVE = 3

        while iteration < max_steps:
            iteration += 1

            # --- Pre-parse guard: python/pseudo code blocks are NOT real tool calls ---
            # _parse_python_direct_tool_invocations can extract write_file() etc.
            # from ```python blocks.  Detect this BEFORE parsing so we don't
            # accidentally execute pseudo-calls that the model wrapped in Python syntax.
            _is_pseudo_code_block = (
                _PYTHON_CODE_BLOCK_PATTERN.search(current)
                or _PSEUDO_TOOL_USAGE_PATTERN.search(current)
            ) and "```tool_call" not in current

            tool_calls = [] if _is_pseudo_code_block else parse_tool_calls(current)

            # --- Pre-execution supervisor gate (CLI-origin) ---
            if tool_calls:
                current = self._apply_supervisor("[TOOL_FLOW]", current, stage="tool_flow")
                tool_calls = parse_tool_calls(current)

            # --- No valid tool calls — try corrective paths (CLI-origin) ---
            if not tool_calls:
                corrected, matched = self._try_corrective_paths(current, allow_safe_fallback)
                if matched:
                    # Corrective produced a new answer — always re-iterate
                    # to give the model another chance (or hit max_steps).
                    current = corrected
                    continue
                # No corrective matched — return as plain text
                if self._actor_states.get("creator") in {"THINKING", "EXECUTING_TOOL"}:
                    self._emit_actor_state("creator", "PASSIVE", "Brak kolejnych tool_call po analizie wyniku")
                return current

            # --- Canonicalize before execution ---
            current = _canonicalize_tool_calls(tool_calls)

            # --- Execute tool batch ---
            aggregated_results: list[dict] = []
            unknown_tools: list[str] = []
            self._emit_actor_state("router", "TOOL_FLOW", "Router realizuje kolejkę tool_call")

            for tc in tool_calls:
                canonical_tool = canonical_tool_name(tc.tool)
                self._log_activity(
                    action="tool_call.request",
                    intent="Model zgłosił żądanie wykonania narzędzia.",
                    details={"tool": canonical_tool, "intent": tc.intent},
                )
                self._emit_actor_state("creator", "EXECUTING_TOOL", f"Wykonanie narzędzia: {tc.tool}")
                result = self.execute_tool_call(tc)
                self._last_tool_activity_monotonic = time.monotonic()
                self._idle_reactivation_attempts = 0
                self._idle_reactivation_capped_notified = False
                error = result.get("error")
                if isinstance(error, str) and error.startswith("unknown_tool:"):
                    unknown_tools.append(error.removeprefix("unknown_tool:"))
                self._log_activity(
                    action="tool_call.result",
                    intent="Framework zakończył wykonanie narzędzia.",
                    details={
                        "tool": canonical_tool,
                        "ok": bool(result.get("ok")),
                        "error": str(error) if error is not None else "",
                    },
                )
                aggregated_results.append(
                    {"tool": canonical_tool, "intent": tc.intent, "result": result}
                )

            # --- Loop detection (engine-native, known tools) ---
            if not unknown_tools:
                sig = "|".join(
                    f"{r['tool']}:{json.dumps(r.get('result', {}).get('ok', ''), ensure_ascii=False)}"
                    for r in aggregated_results
                )
                _tool_call_history.append(sig)
                if len(_tool_call_history) >= _MAX_SAME_TOOL_CONSECUTIVE:
                    recent = _tool_call_history[-_MAX_SAME_TOOL_CONSECUTIVE:]
                    if len(set(recent)) == 1:
                        self._emit_log(
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
                        self._emit_actor_state("router", "STALLED", "Pętla tool_flow — przerywam")
                        tools_used = ", ".join(r["tool"] for r in aggregated_results)
                        return (
                            f"Wykonano narzędzie {tools_used}, ale powstała pętla powtarzających się wywołań. "
                            "Proszę o doprecyzowanie polecenia lub ręczne wskazanie następnego kroku."
                        )

            # --- Unknown tools handling (engine-native escalation) ---
            if unknown_tools:
                for ut in unknown_tools:
                    unknown_tool_correction_attempts[ut] = unknown_tool_correction_attempts.get(ut, 0) + 1

                exhausted_tools = [
                    ut for ut in unknown_tools
                    if unknown_tool_correction_attempts.get(ut, 0) > _MAX_CORRECTIONS_PER_TOOL
                ]

                if exhausted_tools:
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
                    self._emit_log(
                        "supervisor_log",
                        f"[Koordynator] Wyczerpano próby naprawy narzędzia '{tool_name}'. "
                        "Wymuszam zapis planu tworzenia narzędzia do notes/tool_design_plan.json.",
                    )
                    continue

                available_tools = sorted(self.runtime_supported_tool_names())
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

                if self.chat_service.supervisor_service is not None:
                    self._emit_actor_state("supervisor", "CORRECTING", "Kastor naprawia nieobsługiwane narzędzie")
                    try:
                        corrected = self.chat_service.supervisor_service.refine(
                            user_message=corrective_prompt,
                            model_answer=current,
                            stage="textual_unknown_tool_corrective",
                        )
                        current = corrected.answer
                        self.enqueue_supervisor_message(
                            stage="textual_unknown_tool_corrective",
                            reason_code=corrected.reason_code,
                            notes=self._merge_supervisor_notes(
                                "Naprawa nieobsługiwanego narzędzia.",
                                corrected.notes,
                            ),
                            answer=current,
                        )
                        self._emit_actor_state("supervisor", "READY", "Kastor zakończył naprawę narzędzia")
                        continue
                    except (OllamaClientError, OSError):
                        self._emit_actor_state("supervisor", "READY", "Kastor — naprawa narzędzia przerwana błędem")

                current = (
                    '```tool_call\n'
                    '{"tool":"list_dir","args":{"path":"."},"intent":"fallback_after_unknown_tool"}'
                    '\n```'
                )
                continue

            # --- Code path failure streak (CLI-origin) ---
            syntax_failures = [
                item for item in aggregated_results
                if item.get("tool") == "check_python_syntax"
                and not bool(item.get("result", {}).get("ok"))
            ]
            if syntax_failures:
                self._code_path_failure_streak += len(syntax_failures)
            else:
                had_successful_tool = any(
                    bool(item.get("result", {}).get("ok")) for item in aggregated_results
                )
                if had_successful_tool:
                    self._code_path_failure_streak = 0

            if self._code_path_failure_streak >= _MAX_CODE_PATH_FAILURE_STREAK:
                self._log_activity(
                    action="tool_flow.code_path.aborted",
                    intent="Przerwano wątek generowania kodu po powtarzających się błędach składni.",
                    details={
                        "streak": self._code_path_failure_streak,
                        "threshold": _MAX_CODE_PATH_FAILURE_STREAK,
                    },
                )
                abort_prompt = _build_code_path_abort_prompt(
                    self._last_user_message or "kontynuuj"
                )
                redirected = self._ask_executor_with_router_mailbox(abort_prompt)
                redirected = self._apply_supervisor(
                    abort_prompt, redirected, stage="code_path_abort_corrective",
                )
                self._code_path_failure_streak = 0
                redirected_calls = parse_tool_calls(redirected)
                if redirected_calls and not self._has_unknown_tool_calls_runtime(redirected):
                    current = _canonicalize_tool_calls(redirected_calls)
                    continue
                return redirected

            # --- Post-write verification instructions (CLI-origin) ---
            post_write_instruction = ""
            successful_writes = [
                item for item in aggregated_results
                if item.get("tool") == "write_file"
                and bool(item.get("result", {}).get("ok"))
            ]
            if successful_writes:
                instructions: list[str] = []
                for item in successful_writes:
                    path = str(item.get("result", {}).get("path", "")).strip()
                    if not path:
                        continue
                    if path.endswith(".py"):
                        instructions.append(
                            f"- Dla {path}: wykonaj teraz bezpieczną walidację przez check_python_syntax. "
                            "Nie uruchamiaj pliku na tym etapie."
                        )
                    else:
                        instructions.append(
                            f"- Dla {path}: wykonaj teraz weryfikację przez read_file i potwierdź poprawny zapis."
                        )
                if instructions:
                    post_write_instruction = (
                        "\nINSTRUKCJA FRAMEWORKA (OBOWIĄZKOWA PO write_file):\n"
                        "Najpierw zweryfikuj/przetestuj właśnie zapisane pliki, zanim przejdziesz dalej.\n"
                        + "\n".join(instructions)
                        + "\nTeraz zwróć WYŁĄCZNIE blok tool_call realizujący ten krok weryfikacji/testu."
                    )

            # --- Known tools: followup with compact results ---
            followup = (
                "[TOOL_RESULT]\n"
                + self._compact_tool_results_payload(aggregated_results)
                + "\nNa podstawie tego wyniku odpowiedz użytkownikowi."
                + post_write_instruction
                + "\nINSTRUKCJA SPÓJNOŚCI GŁÓWNEGO WĄTKU: "
                + f"utrzymuj i aktualizuj plan w pliku '{_PLAN_TRACKING_RELATIVE_PATH}'. "
                + "Po zakończeniu etapu zaktualizuj current_stage oraz statusy zadań."
            )
            try:
                self._emit_actor_state("creator", "THINKING", "Polluks analizuje TOOL_RESULT")
                executor_answer = self._ask_executor_with_router_mailbox(followup)
                current = executor_answer
                if self.chat_service.supervisor_service is not None:
                    self._emit_actor_state("supervisor", "REVIEWING", "Kastor ocenia odpowiedź po TOOL_RESULT")
                    supervision_result = self.chat_service.supervisor_service.refine(
                        user_message="[TOOL_FLOW]",
                        model_answer=current,
                        stage="tool_flow",
                    )
                    supervisor_calls = parse_tool_calls(supervision_result.answer)
                    runtime_supported = self.runtime_supported_tool_names()
                    has_unsupported = any(
                        canonical_tool_name(c.tool) not in runtime_supported
                        for c in supervisor_calls
                    )
                    if has_unsupported:
                        self._log_activity(
                            action="supervisor.tool_flow.rejected",
                            intent="Odrzucono odpowiedź nadzorcy z nieobsługiwanym narzędziem.",
                            details={"rejected_tools": [c.tool for c in supervisor_calls]},
                        )
                    else:
                        current = supervision_result.answer
                    self.enqueue_supervisor_message(
                        stage="tool_flow",
                        reason_code=supervision_result.reason_code,
                        notes=self._merge_supervisor_notes(
                            "Ocena odpowiedzi po TOOL_RESULT.",
                            supervision_result.notes,
                        ),
                        answer=current,
                    )
                    self._emit_actor_state("supervisor", "READY", "Kastor zakończył ocenę TOOL_RESULT")
            except (OllamaClientError, OSError) as error:
                self._emit_log("user_model_log", f"Błąd kontynuacji po TOOL_RESULT: {error}")
                self._emit_actor_state("router", "ERROR", "Błąd kontynuacji po TOOL_RESULT")
                self._emit_actor_state("creator", "ERROR", "Polluks przerwał kontynuację po TOOL_RESULT")
                return current

        # --- Reached max iterations ---
        if parse_tool_calls(current):
            self._emit_log(
                "user_model_log",
                (
                    "Ostrzeżenie runtime: osiągnięto limit iteracji resolve_tool_calls, "
                    "pozostał nierozwiązany krok narzędziowy. "
                    "Użyj krótkiego polecenia wtrącającego (np. 'kontynuuj'), aby wznowić cykl."
                ),
            )
            self._log_activity(
                action="tool_flow.iteration_cap",
                intent="Osiągnięto limit iteracji resolve_tool_calls przy aktywnym tool_call.",
                details={"max_steps": max_steps},
            )
            self._emit_actor_state("router", "STALLED", "Osiągnięto limit iteracji tool_flow")
            if not allow_safe_fallback:
                return current
            return self._run_safe_tool_fallback()
        if _looks_like_unparsed_tool_call(current):
            if not allow_safe_fallback:
                return current
            return self._run_safe_tool_fallback()
        if _PYTHON_CODE_BLOCK_PATTERN.search(current) or _PSEUDO_TOOL_USAGE_PATTERN.search(current):
            if not allow_safe_fallback:
                return current
            return self._run_safe_tool_fallback()
        self._emit_actor_state("router", "ACTIVE", "Koniec przebiegu resolve_tool_calls")
        if self._actor_states.get("creator") in {"THINKING", "EXECUTING_TOOL"}:
            self._emit_actor_state("creator", "PASSIVE", "Zakończono przebieg tool flow")
        return current

    def _try_corrective_paths(
        self, current: str, allow_safe_fallback: bool,
    ) -> tuple[str, bool]:
        """Attempt corrective prompts for malformed model answers.

        Returns ``(corrected_answer, True)`` if a corrective path matched
        (even if the corrected text happens to equal *current*).
        Returns ``(current, False)`` when no corrective matched.

        .. note:: We used to rely on ``corrected is not current`` (identity),
           but CPython interns identical string literals so two distinct
           ``chat_service.ask()`` results can share the same ``id()``.
        """
        if _looks_like_unparsed_tool_call(current):
            self._log_rejected_pseudo_call("unparsed_tool_call", current)
            corrective_prompt = _build_unparsed_tool_call_corrective_prompt()
            corrected = self._ask_executor_with_router_mailbox(corrective_prompt)
            corrected = self._apply_supervisor(
                corrective_prompt, corrected, stage="unparsed_tool_call_corrective",
            )
            return corrected, True

        if _is_non_action_placeholder(current):
            intro_hint = str(self.work_dir / "wprowadzenie.md")
            corrective_prompt = _build_no_action_corrective_prompt("kontynuuj", intro_hint)
            corrected = self._ask_executor_with_router_mailbox(corrective_prompt)
            corrected = self._apply_supervisor(
                corrective_prompt, corrected, stage="empty_answer_corrective",
            )
            return corrected, True

        if _PYTHON_CODE_BLOCK_PATTERN.search(current):
            self._log_rejected_pseudo_call("python_code_block", current)
            corrective_prompt = _build_python_code_corrective_prompt()
            corrected = self._ask_executor_with_router_mailbox(corrective_prompt)
            corrected = self._apply_supervisor(
                corrective_prompt, corrected, stage="python_code_corrective",
            )
            return corrected, True

        if _PSEUDO_TOOL_USAGE_PATTERN.search(current):
            self._log_rejected_pseudo_call("pseudo_tool_usage", current)
            corrective_prompt = _build_pseudo_tool_corrective_prompt()
            corrected = self._ask_executor_with_router_mailbox(corrective_prompt)
            corrected = self._apply_supervisor(
                corrective_prompt, corrected, stage="pseudo_tool_corrective",
            )
            return corrected, True

        return current, False  # no corrective matched

    def watchdog_tick(self) -> None:
        """Periodic watchdog check — nudge supervisor after idle period."""
        supervisor = self.chat_service.supervisor_service
        if supervisor is None:
            return
        if self._watchdog_suspended_until_user_input:
            return
        if self._router_cycle_in_progress:
            return
        if not self._last_user_message:
            return

        now = time.monotonic()
        if self.auto_resume_tick(now):
            return
        if self._idle_until_epoch is not None:
            if time.time() < self._idle_until_epoch:
                self._emit_actor_state("router", "IDLE_SCHEDULED", "Router respektuje zaplanowane IDLE")
                return
            self._idle_until_epoch = None
            self._idle_until_source = ""
        idle_seconds = now - self._last_progress_monotonic
        actionable_plan = self.has_actionable_plan()
        plan_required = self.plan_requires_update()
        if idle_seconds < self._watchdog_idle_threshold_seconds:
            return
        if self._passive_turns <= 0 and not actionable_plan and not plan_required:
            return

        if self._watchdog_attempts >= SUPERVISOR_WATCHDOG_MAX_ATTEMPTS:
            if not self._watchdog_capped_notified:
                self._emit_log(
                    "supervisor_log",
                    "Watchdog Kastora osiągnął limit prób reaktywacji; "
                    "wstrzymuję auto-reaktywację do kolejnej wiadomości użytkownika.",
                )
                self._watchdog_capped_notified = True
            self._watchdog_suspended_until_user_input = True
            self._emit_actor_state("router", "PAUSED", "Watchdog wstrzymany do czasu nowej wiadomości użytkownika")
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

        # Dispatch heavy model work to background thread (non-blocking)
        self._router_cycle_in_progress = True
        self._emit_actor_state("router", "WATCHDOG", "Router wzbudza Kastora po bezczynności")
        self._emit_actor_state("supervisor", "REVIEWING", "Kastor sprawdza status działań Twórcy")

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

        if self._background_enabled:
            worker = threading.Thread(
                target=self._watchdog_background_work,
                args=(supervisor, context),
                daemon=True,
                name="amiagi-watchdog",
            )
            self._last_background_worker = worker
            worker.start()
        else:
            self._watchdog_background_work(supervisor, context)

    def _watchdog_background_work(
        self,
        supervisor: Any,
        context: dict[str, Any],
    ) -> None:
        """Execute watchdog model inference (may run in background thread)."""
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
            recent_msgs = self.chat_service.memory_repository.recent_messages(limit=6)
            conv_excerpt = format_conversation_excerpt(recent_msgs, limit=6)
            result = supervisor.refine(
                user_message=prompt,
                model_answer=model_answer,
                stage="textual_watchdog_nudge",
                conversation_excerpt=conv_excerpt,
            )
        except (OllamaClientError, OSError):
            self._emit_actor_state("supervisor", "ERROR", "Błąd podczas wzbudzenia Kastora")
            self._watchdog_suspended_until_user_input = True
            self._watchdog_capped_notified = True
            self._emit_log("supervisor_log", _("watchdog.error_suspended"))
            self._emit_actor_state("router", "PAUSED", "Watchdog zatrzymany po błędzie nadzorcy")
            self._emit_cycle_finished("Watchdog przerwany błędem")
            return

        self._emit_actor_state("supervisor", "READY", "Kastor zakończył wzbudzenie")
        self.enqueue_supervisor_message(
            stage="textual_watchdog_nudge",
            reason_code=result.reason_code,
            notes=self._merge_supervisor_notes(
                "Watchdog Kastora przekazał zalecenia Polluksowi.",
                result.notes,
            ),
            answer=result.answer,
        )
        self._emit_actor_state("router", "PROGRESS_GUARD", "Router weryfikuje postęp po watchdog")
        answer = self._enforce_supervised_progress(self._last_user_message, result.answer, max_attempts=2)
        self._apply_idle_hint_from_answer(answer, source="supervisor")

        answer = self.resolve_tool_calls(answer)
        self._last_model_answer = answer
        if self.has_supported_tool_call(answer):
            self._passive_turns += 1
            self._emit_actor_state("creator", "PASSIVE", "Watchdog: pozostał nierozwiązany krok narzędziowy")
        else:
            self._passive_turns += 1
            self._emit_actor_state("creator", "PASSIVE", "Brak akcji po wzbudzeniu")

        # Model asks user a question after watchdog → pause & suspend
        if self._model_response_awaits_user(answer):
            if self._is_premature_plan_completion(answer):
                redirected = self._redirect_premature_completion(self._last_user_message, answer)
                if redirected is not None:
                    answer = redirected
                    self._passive_turns = 0
                    self._last_progress_monotonic = time.monotonic()
                    answer = self.resolve_tool_calls(answer)
                    self._last_model_answer = answer
                else:
                    self._pause_for_user_decision(answer, "model_awaits_user", "watchdog")
            else:
                self._pause_for_user_decision(answer, "model_awaits_user", "watchdog")

        display_answer = self._format_user_facing_answer(answer)
        self._emit_log("user_model_log", f"Model(auto): {display_answer}")
        self._emit_log("executor_log", f"[watchdog] {answer}")
        self._emit_cycle_finished("Router zakończył cykl watchdog")
        self.poll_supervision_dialogue()

    # ------------------------------------------------------------------
    # Auto-resume (Faza 3)
    # ------------------------------------------------------------------

    def auto_resume_tick(self, now_monotonic: float, *, force: bool = False) -> bool:
        """Check if paused plan should be auto-resumed. Returns True if handled."""
        if not self._plan_pause_active:
            return False
        if not force and not self._pending_user_decision:
            return False
        if not force and self._pending_decision_identity_query:
            self._emit_actor_state("router", "PAUSED", "Plan wstrzymany po pytaniu tożsamościowym; oczekiwanie na decyzję użytkownika")
            return True
        idle_for = now_monotonic - self._plan_pause_started_monotonic
        if not force and idle_for < INTERRUPT_AUTORESUME_IDLE_SECONDS:
            self._emit_actor_state("router", "PAUSED", "Plan wstrzymany: oczekiwanie na decyzję użytkownika")
            return True

        resume_reason = "user_resume" if force else "auto_resume_after_idle"
        resume_source = "user" if force else "watchdog"
        self.set_plan_paused(paused=False, reason=resume_reason, source=resume_source)
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

        # Dispatch heavy model work to background thread (non-blocking)
        self._router_cycle_in_progress = True
        self._emit_actor_state("router", "RESUMING", f"Auto-wznowienie planu po {INTERRUPT_AUTORESUME_IDLE_SECONDS:.0f}s IDLE")
        self._emit_actor_state("creator", "THINKING", "Polluks wznawia plan")

        if self._background_enabled:
            worker = threading.Thread(
                target=self._auto_resume_background,
                daemon=True,
                name="amiagi-auto-resume",
            )
            self._last_background_worker = worker
            worker.start()
        else:
            self._auto_resume_background()
        return True

    def _auto_resume_background(self) -> None:
        """Execute auto-resume model inference (may run in background thread)."""
        resume_prompt = (
            "Wznów przerwany plan po timeout decyzji użytkownika. "
            "Jeśli nie ma aktywnego planu, zacznij od poznania zasobów frameworka przez pojedynczy tool_call "
            "(preferuj check_capabilities lub list_dir)."
        )
        try:
            answer = self._ask_executor_with_router_mailbox(resume_prompt)
            if self.chat_service.supervisor_service is not None:
                self._emit_actor_state("supervisor", "REVIEWING", "Kastor ocenia auto-wznowienie")
                supervision_result = self.chat_service.supervisor_service.refine(
                    user_message=resume_prompt,
                    model_answer=answer,
                    stage="textual_interrupt_autoresume",
                )
                answer = supervision_result.answer
                self.enqueue_supervisor_message(
                    stage="textual_interrupt_autoresume",
                    reason_code=supervision_result.reason_code,
                    notes=self._merge_supervisor_notes(
                        "Auto-wznowienie planu po timeout decyzji użytkownika.",
                        supervision_result.notes,
                    ),
                    answer=answer,
                )
                self._emit_actor_state("supervisor", "READY", "Kastor zakończył ocenę auto-wznowienia")
        except (OllamaClientError, OSError) as error:
            self._emit_log("user_model_log", f"Błąd auto-wznowienia planu: {error}")
            self._emit_cycle_finished("Auto-wznowienie przerwane błędem")
            return

        answer = self._enforce_supervised_progress(resume_prompt, answer)
        answer = self.resolve_tool_calls(answer)
        self._last_model_answer = answer
        display_answer = self._format_user_facing_answer(answer)
        self._emit_log("user_model_log", f"Model(auto): {display_answer}")
        self._emit_log("executor_log", f"[auto_resume] {answer}")
        if self.has_supported_tool_call(answer):
            self._passive_turns += 1
            self._emit_actor_state("creator", "PASSIVE", "Auto-wznowienie pozostawiło nierozwiązany krok narzędziowy")
            self._record_collaboration_signal("no_handoff", {"phase": "auto_resume", "tool": False, "residual_tool_call": True})
        else:
            self._passive_turns += 1
            self._emit_actor_state("creator", "PASSIVE", "Auto-wznowienie bez kroku narzędziowego")
            self._record_collaboration_signal("no_handoff", {"phase": "auto_resume", "tool": False})
        self._emit_cycle_finished("Router wznowił plan po timeout decyzji")

    # ------------------------------------------------------------------
    # Supervision dialogue polling (Faza 3)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_supervision_lane_label(*, stage: str, kind: str, direction: str) -> str:
        stage_label = stage or "unknown_stage"
        kind_label = kind or "unknown_type"
        return f"[{direction} | {stage_label}:{kind_label}]"

    def poll_supervision_dialogue(self) -> None:
        """Read new supervision dialogue log entries and emit events."""
        log_path = self._supervisor_dialogue_log_path
        if log_path is None or not log_path.exists():
            return
        try:
            with log_path.open("r", encoding="utf-8") as handle:
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
                    stage=stage, kind=kind, direction="POLLUKS→KASTOR",
                )
                self._emit_log("executor_log", f"{lane} {executor_answer}")

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
                    stage=stage, kind=kind, direction="KASTOR→ROUTER",
                )
                self._emit_log("supervisor_log", f"{lane} {rendered_supervisor}")

                # Route addressed blocks from supervisor notes/repaired_answer
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
                            if "user_model_log" in poll_extra:
                                sanitized = strip_tool_call_blocks(poll_content)
                                if not sanitized or not is_sponsor_readable(sanitized):
                                    poll_extra = [p for p in poll_extra if p != "user_model_log"]
                                    if not poll_extra:
                                        continue
                                else:
                                    poll_content = sanitized
                            for poll_panel_id in poll_extra:
                                self._emit_log(poll_panel_id, f"{poll_label} {poll_content}" if poll_label else poll_content)

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
                    stage=stage, kind=kind, direction="KASTOR→ROUTER",
                )
                self._emit_log("supervisor_log", f"{lane} {summary}")
