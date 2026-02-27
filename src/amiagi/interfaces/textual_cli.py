from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, unquote, urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from amiagi.application.chat_service import ChatService
from amiagi.application.discussion_sync import extract_dialogue_without_code
from amiagi.application.tool_calling import ToolCall, parse_tool_calls
from amiagi.application.shell_policy import default_shell_policy, load_shell_policy, parse_and_validate_shell_command
from amiagi.infrastructure.ollama_client import OllamaClientError
from amiagi.infrastructure.script_executor import ScriptExecutor
from amiagi.interfaces.cli import (
    HELP_TEXT,
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
    ("/help", "pokaż pełną pomoc CLI"),
    ("/cls", "wyczyść ekran główny (panel użytkownika)"),
    ("/cls all", "wyczyść wszystkie panele (użytkownik, nadzorca, wykonawca, router)"),
    ("/models current", "pokaż aktualnie aktywny model wykonawczy"),
    ("/models show", "pokaż modele dostępne w Ollama (1..x)"),
    ("/models chose <nr>", "wybierz model wykonawczy po numerze z /models show"),
    ("/permissions", "pokaż aktualny tryb zgód"),
    ("/permissions all", "włącz globalną zgodę na zasoby"),
    ("/permissions ask", "wyłącz globalną zgodę (blokuj akcje wymagające zasobów)"),
    ("/permissions reset", "wyczyść zapamiętane zgody per zasób"),
    ("/queue-status", "pokaż stan kolejki modeli i decyzji polityki VRAM"),
    ("/capabilities [--network]", "pokaż gotowość narzędzi i backendów"),
    ("/show-system-context [tekst]", "pokaż kontekst systemowy przekazywany do modelu"),
    ("/goal-status", "pokaż cel główny i etap z notes/main_plan.json"),
    ("/goal", "alias: pokaż cel główny i etap"),
    ("/router-status", "pokaż status aktorów: Router/Twórca/Nadzorca/Terminal"),
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
        return _CommandOutcome(handled=True, messages=[HELP_TEXT, "", TEXTUAL_HELP_TEXT])

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
    ) -> None:
        super().__init__()
        self._chat_service = chat_service
        self._supervisor_dialogue_log_path = supervisor_dialogue_log_path
        self._permission_manager = permission_manager
        self._shell_policy_path = shell_policy_path
        self._dialogue_log_offset = 0
        self._router_mailbox_log_path = router_mailbox_log_path or Path("./logs/router_mailbox.jsonl")
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
        try:
            self._shell_policy = load_shell_policy(shell_policy_path)
        except Exception:
            self._shell_policy = default_shell_policy()

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="main_column"):
                yield Static("Użytkownik ↔ Model badany", classes="title")
                yield TextArea("", id="user_model_log", read_only=True, show_line_numbers=False)
                yield Input(placeholder="Wpisz polecenie i Enter (/quit aby wyjść)", id="input_box")
            with Vertical(id="tech_column"):
                yield Static("Router", classes="title")
                yield Static("", id="router_status")
                yield Static("Nadzorca", classes="title")
                yield TextArea("", id="supervisor_log", read_only=True, show_line_numbers=False)
                yield Static("Model badany → Nadzorca", classes="title")
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
        lines = [
            "Aktorzy:",
            f"- Router: {self._actor_states.get('router', 'UNKNOWN')}",
            f"- Twórca: {self._actor_states.get('creator', 'UNKNOWN')}",
            f"- Nadzorca: {self._actor_states.get('supervisor', 'UNKNOWN')}",
            f"- Terminal: {self._actor_states.get('terminal', 'UNKNOWN')}",
            f"IDLE until: {self._format_idle_until()}",
            f"Ostatnie zdarzenie: {self._last_router_event}",
        ]
        area.update("\n".join(lines))

    def _set_actor_state(self, actor: str, state: str, event: str | None = None) -> None:
        self._actor_states[actor] = state
        if event:
            self._last_router_event = event
        self._render_router_status()

    def _set_idle_until(self, idle_until_epoch: float | None, source: str) -> None:
        self._idle_until_epoch = idle_until_epoch
        self._idle_until_source = source if idle_until_epoch is not None else ""
        self._set_actor_state("router", "IDLE_WINDOW_SET" if idle_until_epoch is not None else "ACTIVE", "Aktualizacja okna IDLE")

    def _finalize_router_cycle(self, *, event: str) -> None:
        self._router_cycle_in_progress = False
        self._set_actor_state("router", "ACTIVE", event)
        self._set_actor_state("terminal", "WAITING_INPUT", "Oczekiwanie na kolejną wiadomość użytkownika")
        if self._actor_states.get("creator") in {"THINKING", "ANSWER_READY"}:
            if self._last_model_answer.strip():
                self._set_actor_state("creator", "PASSIVE", "Domknięto cykl wykonania bez aktywnego narzędzia")
            else:
                self._set_actor_state("creator", "WAITING_INPUT", "Brak aktywnej pracy Twórcy")

    def _refresh_router_runtime_state(self) -> None:
        if self._router_cycle_in_progress:
            return
        if self._actor_states.get("terminal") != "WAITING_INPUT":
            self._set_actor_state("terminal", "WAITING_INPUT", "Synchronizacja stanu terminala")
        now = time.monotonic()
        idle_seconds = now - self._last_progress_monotonic
        creator_state = self._actor_states.get("creator", "")
        if creator_state in {"THINKING", "ANSWER_READY", "EXECUTING_TOOL"} and idle_seconds > 2.0:
            fallback_state = "PASSIVE" if self._last_model_answer.strip() else "WAITING_INPUT"
            self._set_actor_state("creator", fallback_state, "Korekta stanu po zakończonym cyklu")

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
        if tool_calls:
            first_call = tool_calls[0]
            suggested_step = f"{first_call.tool} ({first_call.intent})"
        self._supervisor_outbox.append(
            {
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

    def _drain_supervisor_outbox_context(self) -> str:
        if not self._supervisor_outbox:
            return ""
        queued_messages = [dict(message) for message in self._supervisor_outbox]
        lines = ["[ROUTER_MAILBOX_FROM_SUPERVISOR]"]
        for index, message in enumerate(self._supervisor_outbox, start=1):
            lines.append(
                f"{index}) stage={message.get('stage','')}; reason={message.get('reason_code','')}; "
                f"notes={message.get('notes','')}; suggested_step={message.get('suggested_step','')}"
            )
        lines.append("[/ROUTER_MAILBOX_FROM_SUPERVISOR]")
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
        if mailbox_context:
            self._set_actor_state("router", "ROUTING", "Router dostarcza kolejkę nadzorczą do Twórcy")
            enriched = message + "\n\n" + mailbox_context
            return self._chat_service.ask(enriched)
        return self._chat_service.ask(message)

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
        area = self.query_one(f"#{widget_id}", TextArea)
        payload = message.rstrip("\n")
        if not payload:
            return
        if area.text:
            area.load_text(f"{area.text}\n{payload}")
        else:
            area.load_text(payload)
        area.scroll_end(animate=False)

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
                    f"Twórca: {self._actor_states.get('creator', 'UNKNOWN')}",
                    f"Nadzorca: {self._actor_states.get('supervisor', 'UNKNOWN')}",
                    f"Terminal: {self._actor_states.get('terminal', 'UNKNOWN')}",
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
            summary = self._chat_service.summarize_session_for_restart()
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
        self._append_log("user_model_log", "Tryb Textual aktywny")
        selected_default_model, discovered_models, default_error = _ensure_default_executor_model(self._chat_service)
        if selected_default_model is not None:
            self._append_log(
                "user_model_log",
                (
                    "Domyślny model wykonawczy: "
                    f"{selected_default_model} (pozycja 1/{len(discovered_models)} z listy Ollama)."
                ),
            )
        elif default_error:
            self._append_log(
                "user_model_log",
                f"Uwaga: nie udało się pobrać listy modeli z Ollama: {default_error}",
            )
        self._append_log(
            "user_model_log",
            "Wpisz /help, aby zobaczyć wszystkie komendy CLI. Treść okien można zaznaczać i kopiować.",
        )
        self._append_log("user_model_log", "Wpisz /models show, aby zobaczyć modele z Ollama (1..x).")
        self._append_log("user_model_log", "Wpisz /models chose X, aby wybrać model do testów.")
        self._append_log("executor_log", "Oczekiwanie na odpowiedź modelu wykonawczego.")
        if self._chat_service.supervisor_service is None:
            self._append_log(
                "supervisor_log",
                "Nadzorca jest nieaktywny w tej sesji (brak supervisor_service).",
            )
            self._supervisor_notice_shown = True
        else:
            self._append_log("supervisor_log", "Oczekiwanie na wpisy nadzorcy.")
        self.set_focus(self.query_one("#input_box", Input))
        self.set_interval(SUPERVISION_POLL_INTERVAL_SECONDS, self._poll_supervision_dialogue)
        self.set_interval(SUPERVISOR_WATCHDOG_INTERVAL_SECONDS, self._run_supervisor_idle_watchdog)
        self.set_interval(1.0, self._refresh_router_runtime_state)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return

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
            return

        if text.lower() in {"/quit", "/exit"}:
            self.exit()
            return

        self._append_log("user_model_log", f"Użytkownik: {text}")
        self._router_cycle_in_progress = True
        self._set_actor_state("terminal", "BUSY", "Terminal przekazał wiadomość")
        self._set_actor_state("router", "ROUTING", "Router przekazuje polecenie do Twórcy")
        self._set_actor_state("creator", "THINKING", "Twórca analizuje polecenie")
        self._last_user_message = text
        self._watchdog_attempts = 0
        self._watchdog_capped_notified = False
        try:
            answer = self._ask_executor_with_router_mailbox(text)
            self._set_actor_state("creator", "ANSWER_READY", "Twórca wygenerował odpowiedź")
            if self._chat_service.supervisor_service is not None:
                self._set_actor_state("supervisor", "REVIEWING", "Nadzorca analizuje odpowiedź Twórcy")
                passive_turns_after_current = self._passive_turns + (0 if _has_supported_tool_call(answer) else 1)
                should_remind_continuation = passive_turns_after_current >= 2
                supervision_context = {
                    "passive_turns": passive_turns_after_current,
                    "should_remind_continuation": should_remind_continuation,
                    "gpu_busy_over_50": False,
                    "plan_persistence": {"required": False},
                }
                supervision_user_message = (
                    "[RUNTIME_SUPERVISION_CONTEXT]\n"
                    + json.dumps(supervision_context, ensure_ascii=False)
                    + "\n[/RUNTIME_SUPERVISION_CONTEXT]\n"
                    + text
                )

                supervision_result = self._chat_service.supervisor_service.refine(
                    user_message=supervision_user_message,
                    model_answer=answer,
                    stage="user_turn",
                )
                answer = supervision_result.answer
                self._enqueue_supervisor_message(
                    stage="user_turn",
                    reason_code=supervision_result.reason_code,
                    notes="Ocena odpowiedzi Twórcy w turze użytkownika.",
                    answer=answer,
                )
                self._set_actor_state("supervisor", "READY", "Nadzorca zakończył analizę")

                if should_remind_continuation and not _has_supported_tool_call(answer):
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
                        notes="Wymuszenie kroku operacyjnego po pasywnej odpowiedzi.",
                        answer=answer,
                    )
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

        if _has_supported_tool_call(answer):
            self._passive_turns = 0
            self._last_progress_monotonic = time.monotonic()
            self._set_actor_state("creator", "TOOL_READY", "Twórca przekazał krok narzędziowy")
        else:
            self._passive_turns += 1
            self._set_actor_state("creator", "PASSIVE", "Brak kroku narzędziowego")

        self._apply_idle_hint_from_answer(answer, source="creator")

        answer = self._enforce_supervised_progress(text, answer)

        answer = self._resolve_tool_calls(answer)
        self._apply_idle_hint_from_answer(answer, source="router")
        self._last_model_answer = answer

        display_answer = _format_user_facing_answer(answer)
        self._append_log("user_model_log", f"Model: {display_answer}")
        self._append_log("executor_log", f"[user_turn] {answer}")
        self._finalize_router_cycle(event="Router dostarczył odpowiedź użytkownikowi")
        if self._chat_service.supervisor_service is None and not self._supervisor_notice_shown:
            self._append_log(
                "supervisor_log",
                "Nadzorca jest nieaktywny; panel pokazuje tylko komunikaty techniczne.",
            )
            self._supervisor_notice_shown = True
        self._poll_supervision_dialogue()

    def _run_supervisor_idle_watchdog(self) -> None:
        supervisor = self._chat_service.supervisor_service
        if supervisor is None:
            return
        if not self._last_user_message:
            return

        now = time.monotonic()
        if self._idle_until_epoch is not None:
            if time.time() < self._idle_until_epoch:
                self._set_actor_state("router", "IDLE_SCHEDULED", "Router respektuje zaplanowane IDLE")
                return
            self._idle_until_epoch = None
            self._idle_until_source = ""
        idle_seconds = now - self._last_progress_monotonic
        plan_required = self._plan_requires_update()
        if idle_seconds < self._watchdog_idle_threshold_seconds:
            return
        if self._passive_turns <= 0 and not plan_required:
            return

        if self._watchdog_attempts >= SUPERVISOR_WATCHDOG_MAX_ATTEMPTS:
            if not self._watchdog_capped_notified:
                self._append_log(
                    "supervisor_log",
                    (
                        "Watchdog nadzorcy osiągnął limit prób reaktywacji; "
                        "oczekuję nowej aktywności użytkownika lub skutecznego kroku narzędziowego."
                    ),
                )
                self._watchdog_capped_notified = True
            return

        self._watchdog_attempts += 1
        self._watchdog_capped_notified = False
        self._set_actor_state("router", "WATCHDOG", "Router wzbudza Nadzorcę po bezczynności")
        self._set_actor_state("supervisor", "REVIEWING", "Nadzorca sprawdza status działań Twórcy")

        context = {
            "idle_seconds": round(idle_seconds, 2),
            "idle_threshold_seconds": self._watchdog_idle_threshold_seconds,
            "passive_turns": self._passive_turns,
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
            result = supervisor.refine(
                user_message=prompt,
                model_answer=model_answer,
                stage="textual_watchdog_nudge",
            )
        except (OllamaClientError, OSError):
            self._set_actor_state("supervisor", "ERROR", "Błąd podczas wzbudzenia nadzorcy")
            return

        self._set_actor_state("supervisor", "READY", "Nadzorca zakończył wzbudzenie")
        self._enqueue_supervisor_message(
            stage="textual_watchdog_nudge",
            reason_code=result.reason_code,
            notes="Watchdog nadzorczy przekazał zalecenia Twórcy.",
            answer=result.answer,
        )
        answer = self._enforce_supervised_progress(self._last_user_message, result.answer, max_attempts=2)
        self._apply_idle_hint_from_answer(answer, source="supervisor")
        if _has_supported_tool_call(answer):
            self._passive_turns = 0
            self._set_actor_state("creator", "TOOL_READY", "Twórca otrzymał krok po wzbudzeniu")
        else:
            self._passive_turns += 1
            self._set_actor_state("creator", "PASSIVE", "Brak akcji po wzbudzeniu")

        answer = self._resolve_tool_calls(answer)
        self._last_model_answer = answer
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
                self._append_log("executor_log", f"[{stage}:{kind}] {executor_answer}")

            supervisor_output = str(payload.get("supervisor_raw_output", "")).strip()
            if supervisor_output:
                self._append_log("supervisor_log", f"[{stage}:{kind}] {supervisor_output}")

            status = str(payload.get("status", "")).strip()
            reason = str(payload.get("reason_code", "")).strip()
            repaired = str(payload.get("repaired_answer", "")).strip()
            if status:
                summary = f"status={status}"
                if reason:
                    summary += f", reason={reason}"
                if repaired:
                    summary += f", repaired={repaired}"
                self._append_log("supervisor_log", f"[{stage}:{kind}] {summary}")

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

    def _enforce_supervised_progress(self, user_message: str, initial_answer: str, max_attempts: int = 3) -> str:
        supervisor = self._chat_service.supervisor_service
        if supervisor is None:
            return initial_answer

        current = initial_answer
        for attempt in range(1, max_attempts + 1):
            has_supported_tool = _has_supported_tool_call(current)
            plan_required = self._plan_requires_update()
            if has_supported_tool and not plan_required:
                return current

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
                return current

            current = result.answer
            self._enqueue_supervisor_message(
                stage="textual_progress_guard",
                reason_code=result.reason_code,
                notes="Nadzorca wymusił postęp operacyjny.",
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

        return {"ok": False, "error": f"unknown_tool:{tool}"}

    def _resolve_tool_calls(self, initial_answer: str, max_steps: int = 2) -> str:
        current = initial_answer
        for _ in range(max_steps):
            tool_calls = parse_tool_calls(current)
            if not tool_calls:
                if self._actor_states.get("creator") in {"THINKING", "EXECUTING_TOOL"}:
                    self._set_actor_state("creator", "PASSIVE", "Brak kolejnych tool_call po analizie wyniku")
                return current

            aggregated_results: list[dict] = []
            unknown_tools: list[str] = []
            self._set_actor_state("router", "TOOL_FLOW", "Router realizuje kolejkę tool_call")
            for tool_call in tool_calls:
                self._set_actor_state("creator", "EXECUTING_TOOL", f"Wykonanie narzędzia: {tool_call.tool}")
                result = self._execute_tool_call(tool_call)
                error = result.get("error")
                if isinstance(error, str) and error.startswith("unknown_tool:"):
                    unknown_tools.append(error.removeprefix("unknown_tool:"))
                aggregated_results.append(
                    {
                        "tool": tool_call.tool,
                        "intent": tool_call.intent,
                        "result": result,
                    }
                )

            if unknown_tools:
                corrective_prompt = (
                    "W poprzednim kroku użyto nieobsługiwanych narzędzi: "
                    + ", ".join(sorted(set(unknown_tools)))
                    + ".\n"
                    + "Dostępne narzędzia to: read_file, list_dir, run_shell, run_python, check_python_syntax, "
                    + "fetch_web, search_web, capture_camera_frame, record_microphone_clip, check_capabilities, write_file, append_file.\n"
                    + "Zwróć WYŁĄCZNIE jeden poprawny blok tool_call z tej listy."
                )

                if self._chat_service.supervisor_service is not None:
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
                            notes="Naprawa nieobsługiwanego narzędzia.",
                            answer=current,
                        )
                        continue
                    except (OllamaClientError, OSError):
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
                self._set_actor_state("creator", "THINKING", "Twórca analizuje TOOL_RESULT")
                current = self._ask_executor_with_router_mailbox(followup)
                if self._chat_service.supervisor_service is not None:
                    self._set_actor_state("supervisor", "REVIEWING", "Nadzorca ocenia odpowiedź po TOOL_RESULT")
                    supervision_result = self._chat_service.supervisor_service.refine(
                        user_message="[TOOL_FLOW]",
                        model_answer=current,
                        stage="tool_flow",
                    )
                    current = supervision_result.answer
                    self._enqueue_supervisor_message(
                        stage="tool_flow",
                        reason_code=supervision_result.reason_code,
                        notes="Ocena odpowiedzi po TOOL_RESULT.",
                        answer=current,
                    )
                    self._set_actor_state("supervisor", "READY", "Nadzorca zakończył ocenę TOOL_RESULT")
            except (OllamaClientError, OSError) as error:
                self._append_log("user_model_log", f"Błąd kontynuacji po TOOL_RESULT: {error}")
                self._set_actor_state("router", "ERROR", "Błąd kontynuacji po TOOL_RESULT")
                self._set_actor_state("creator", "ERROR", "Twórca przerwał kontynuację po TOOL_RESULT")
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
) -> None:
    _AmiagiTextualApp(
        chat_service=chat_service,
        supervisor_dialogue_log_path=supervisor_dialogue_log_path,
        permission_manager=PermissionManager(),
        shell_policy_path=shell_policy_path,
        router_mailbox_log_path=router_mailbox_log_path,
    ).run()
