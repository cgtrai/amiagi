"""CLI slash command handlers (extracted from run_cli's main loop).

Part of the v1.0.3 Strangler Fig migration — Faza 4.2 / 5.2.
Moves ~600 LOC of inline command handling out of cli.py, keeping the main
adapter file focused on setup, the I/O loop, and RouterEngine delegation.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from amiagi.application.chat_service import ChatService
from amiagi.application.discussion_sync import extract_dialogue_without_code
from amiagi.application.framework_directives import FrameworkDirective, parse_framework_directive
from amiagi.application.shell_policy import parse_and_validate_shell_command
from amiagi.infrastructure.script_executor import ScriptExecutor
from amiagi.interfaces.permission_manager import PermissionManager
from amiagi.interfaces.shared_cli_helpers import (
    _fetch_ollama_models,
    _network_resource_for_model,
    _read_plan_tracking_snapshot,
    _repair_plan_tracking_file,
    _select_executor_model_by_index,
)
from amiagi.i18n import _

import re

_FILE_REF_PATTERN = re.compile(r"(?P<path>[\w./-]+\.[A-Za-z0-9]+)")
_READ_THIS_FILE_PATTERN = re.compile(
    r"(przeczytaj|odczytaj).*(tego\s+pliku|ten\s+plik)",
    re.IGNORECASE,
)


# ── Result types ─────────────────────────────────────────────────────
@dataclass
class CliCommandResult:
    """Outcome of a single CLI command dispatch."""
    handled: bool
    should_exit: bool = False      # True → break out of the main loop (bye/exit)


@dataclass
class CliContext:
    """All dependencies the CLI command handlers need."""
    chat_service: ChatService
    permission_manager: PermissionManager
    script_executor: ScriptExecutor
    work_dir: Path
    workspace_root: Path
    shell_policy: Any
    autonomous_mode: bool
    log_action: Callable[..., None]
    collect_capabilities: Callable[..., dict]

    # Mutable state shared with the main loop
    pending_goal_candidate: str | None = field(default=None, repr=False)
    last_referenced_file: Path | None = field(default=None, repr=False)


# ── Helpers ──────────────────────────────────────────────────────────

def _resolve_workspace_path(ctx: CliContext, path: Path) -> Path:
    if path.is_absolute():
        return path
    return ctx.workspace_root / path


def _extract_goal_candidate_from_message(user_message: str) -> str | None:
    lowered = user_message.lower()
    markers = [
        "twój cel",
        "twoj cel",
        "twoim celem",
        "twoje zadanie",
        "twoim zadaniem",
    ]
    marker = next((item for item in markers if item in lowered), None)
    if marker is None:
        return None

    start = lowered.find(marker)
    if start < 0:
        return None
    segment = user_message[start + len(marker) :].strip()
    segment = re.sub(r"^[\s:=-]+", "", segment).strip()
    segment = re.sub(r"^(jest|to)\b", "", segment, flags=re.IGNORECASE).strip()
    segment = segment.strip("'\" ")
    if len(segment) < 8:
        return None
    return segment


def _is_goal_confirmation_message(user_message: str) -> bool:
    normalized = user_message.strip().lower()
    confirmations = {"tak", "t", "yes", "y", "potwierdzam", "zgadza się", "zgadza sie", "dokładnie", "dokladnie"}
    return normalized in confirmations


def _is_goal_rejection_message(user_message: str) -> bool:
    normalized = user_message.strip().lower()
    rejections = {"nie", "n", "no", "anuluj", "zmień", "zmien", "to nie to"}
    return normalized in rejections


# ── Main dispatcher ──────────────────────────────────────────────────

def dispatch_cli_command(
    raw: str,
    ctx: CliContext,
    *,
    router_engine: Any,
    help_text: str,
) -> CliCommandResult:
    """Handle a single user input line from the CLI main loop.

    Returns a ``CliCommandResult`` indicating whether the command was handled
    and whether the loop should exit.  The caller should **continue** the loop
    if ``result.handled`` is True, or fall through to ``router_engine`` if
    False.
    """
    raw_lower = raw.lower()

    # ── help / cls ───────────────────────────────────────────────────
    if raw_lower == "/help":
        print(help_text)
        ctx.log_action("help.show", "Wyświetlenie listy dostępnych komend.")
        return CliCommandResult(True)

    if raw_lower == "/cls":
        _clear_cli_screen(clear_scrollback=False)
        ctx.log_action("screen.clear.main", "Wyczyszczono ekran główny terminala.")
        return CliCommandResult(True)

    if raw_lower == "/cls all":
        _clear_cli_screen(clear_scrollback=True)
        ctx.log_action("screen.clear.all", "Wyczyszczono ekran i historię przewijania terminala.")
        return CliCommandResult(True)

    # ── /lang ────────────────────────────────────────────────────────
    if raw_lower.startswith("/lang"):
        return _handle_lang_command(raw_lower, ctx)

    # ── /models ──────────────────────────────────────────────────────
    if raw_lower.startswith("/models"):
        return _handle_models_command(raw, ctx)

    # ── /permissions ─────────────────────────────────────────────────
    if raw_lower.startswith("/permissions"):
        return _handle_permissions_command(raw_lower, ctx)

    # ── goal confirmation flow ───────────────────────────────────────
    if ctx.pending_goal_candidate is not None:
        return _handle_goal_flow(raw, ctx, router_engine=router_engine)

    # ── goal detection ───────────────────────────────────────────────
    detected_goal = _extract_goal_candidate_from_message(raw)
    if detected_goal is not None:
        ctx.pending_goal_candidate = detected_goal
        print(
            "Czy dobrze rozumiem, że moim głównym celem jest: "
            f"{ctx.pending_goal_candidate}? Odpowiedz 'tak' lub 'nie'."
        )
        ctx.log_action(
            "goal.candidate.detected",
            "Wykryto kandydat głównego celu i poproszono o potwierdzenie parafrazy.",
            {"goal_candidate": ctx.pending_goal_candidate},
        )
        return CliCommandResult(True)

    # ── /bye, /exit ──────────────────────────────────────────────────
    if raw_lower == "/bye":
        return _handle_bye(ctx)

    if raw_lower == "/exit":
        print(_("cli.session.bye_farewell"))
        ctx.log_action("session.exit", "Zakończenie sesji bez tworzenia podsumowania.")
        return CliCommandResult(True, should_exit=True)

    # ── /queue-status ────────────────────────────────────────────────
    if raw_lower == "/queue-status":
        return _handle_queue_status(ctx)

    # ── /capabilities ────────────────────────────────────────────────
    if raw_lower.startswith("/capabilities"):
        check_network = "--network" in raw.split()
        capabilities = ctx.collect_capabilities(check_network=check_network)
        print("\n--- CAPABILITIES ---")
        print(json.dumps(capabilities, ensure_ascii=False, indent=2))
        ctx.log_action("capabilities.show", "Wyświetlenie gotowości narzędzi i backendów runtime.", {"check_network": check_network})
        return CliCommandResult(True)

    # ── /show-system-context ─────────────────────────────────────────
    if raw_lower.startswith("/show-system-context"):
        parts = raw.split(maxsplit=1)
        sample_message = parts[1].strip() if len(parts) == 2 else "kontekst diagnostyczny"
        prompt = ctx.chat_service.build_system_prompt(sample_message)
        print("\n--- SYSTEM CONTEXT ---")
        print(prompt)
        ctx.log_action("context.show", "Wyświetlenie kontekstu systemowego przekazywanego do modelu.", {"sample_message": sample_message})
        return CliCommandResult(True)

    # ── /goal-status, /goal ──────────────────────────────────────────
    if raw_lower in {"/goal-status", "/goal"}:
        return _handle_goal_status(ctx)

    # ── file reference tracking ──────────────────────────────────────
    for match in _FILE_REF_PATTERN.finditer(raw):
        token = match.group("path")
        candidate = _resolve_workspace_path(ctx, Path(token))
        if candidate.exists() and candidate.is_file():
            ctx.last_referenced_file = candidate

    # ── framework directives ─────────────────────────────────────────
    directive = parse_framework_directive(raw)
    if directive is None and _READ_THIS_FILE_PATTERN.search(raw) and ctx.last_referenced_file is not None:
        directive = FrameworkDirective(action="read_file", path=ctx.last_referenced_file)

    if directive is not None:
        return _handle_framework_directive(directive, ctx)

    # ── /import-dialog ───────────────────────────────────────────────
    if raw_lower.startswith("/import-dialog"):
        return _handle_import_dialog(raw, ctx)

    # ── /create-python ───────────────────────────────────────────────
    if raw_lower.startswith("/create-python"):
        return _handle_create_python(raw, ctx)

    # ── /run-python ──────────────────────────────────────────────────
    if raw_lower.startswith("/run-python"):
        return _handle_run_python(raw, ctx)

    # ── /run-shell ───────────────────────────────────────────────────
    if raw_lower.startswith("/run-shell"):
        return _handle_run_shell(raw, ctx)

    # ── /history ─────────────────────────────────────────────────────
    if raw_lower.startswith("/history"):
        return _handle_history(raw, ctx)

    # ── /remember ────────────────────────────────────────────────────
    if raw_lower.startswith("/remember"):
        return _handle_remember(raw, ctx)

    # ── /memories ────────────────────────────────────────────────────
    if raw_lower.startswith("/memories"):
        return _handle_memories(raw, ctx)

    # ── not handled — fall through to RouterEngine ───────────────────
    return CliCommandResult(False)


# ── Clear screen helper ──────────────────────────────────────────────
import sys

def _clear_cli_screen(*, clear_scrollback: bool) -> None:
    sequence = "\033[3J\033[2J\033[H" if clear_scrollback else "\033[2J\033[H"
    sys.stdout.write(sequence)
    sys.stdout.flush()


# ── Individual command handlers ──────────────────────────────────────

def _handle_lang_command(raw_lower: str, ctx: CliContext) -> CliCommandResult:
    from amiagi.i18n import set_language, get_language, available_languages
    parts = raw_lower.split()
    if len(parts) < 2:
        print(_("lang.current", lang=get_language()))
        print(_("lang.usage"))
    else:
        code = parts[1].strip()
        if code not in available_languages():
            available = ", ".join(sorted(available_languages()))
            print(_("lang.not_found", lang=code, available=available))
        else:
            set_language(code)
            # Rebuild global help text after language switch — caller is responsible
            print(_("lang.switched", lang=code))
    return CliCommandResult(True)


def _handle_models_command(raw: str, ctx: CliContext) -> CliCommandResult:
    parts = raw.split()
    if len(parts) < 2:
        print(_("cli.models_usage"))
        return CliCommandResult(True)

    action = parts[1].lower()
    if action == "current":
        current_model = str(getattr(ctx.chat_service.ollama_client, "model", ""))
        print(f"Aktywny model wykonawczy: {current_model}")
        ctx.log_action("models.current", "Wyświetlono aktualnie aktywny model wykonawczy.", {"model": current_model})
        return CliCommandResult(True)

    if action == "show":
        models, error = _fetch_ollama_models(ctx.chat_service)
        if error is not None:
            print(f"Nie udało się pobrać listy modeli: {error}")
            ctx.log_action("models.show.error", "Błąd pobierania listy modeli z Ollama.", {"error": error})
            return CliCommandResult(True)
        if not models:
            print(_("cli.models_empty"))
            ctx.log_action("models.show.empty", "Lista modeli Ollama jest pusta.")
            return CliCommandResult(True)
        current_model = str(getattr(ctx.chat_service.ollama_client, "model", ""))
        print("\n--- MODELE OLLAMA ---")
        for index, model_name in enumerate(models, start=1):
            marker = "  [aktywny]" if model_name == current_model else ""
            print(f"{index}. {model_name}{marker}")
        print(_("cli.models_chose_usage"))
        ctx.log_action("models.show", "Wyświetlono listę modeli dostępnych przez Ollama.", {"count": len(models), "current_model": current_model})
        return CliCommandResult(True)

    if action in {"chose", "choose"}:
        if len(parts) < 3:
            print(_("cli.models_chose_usage"))
            return CliCommandResult(True)
        try:
            index = int(parts[2])
        except ValueError:
            print(_("cli.models_invalid_number"))
            return CliCommandResult(True)
        ok, payload, models = _select_executor_model_by_index(ctx.chat_service, index)
        if not ok:
            print(payload)
            ctx.log_action("models.choose.error", "Nie udało się przełączyć modelu wykonawczego.", {"error": payload, "index": index, "available_count": len(models)})
            return CliCommandResult(True)
        print(f"Aktywny model wykonawczy: {payload}")
        ctx.log_action("models.choose", "Przełączono model wykonawczy na wskazany numer listy Ollama.", {"selected_model": payload, "index": index})
        return CliCommandResult(True)

    print(_("cli.models_usage"))
    return CliCommandResult(True)


def _handle_permissions_command(raw_lower: str, ctx: CliContext) -> CliCommandResult:
    parts = raw_lower.split()
    action = parts[1] if len(parts) > 1 else "status"

    if action in {"status", "show"}:
        granted_once_count = len(getattr(ctx.permission_manager, "granted_once", set()))
        print("\n--- PERMISSIONS ---")
        print(f"allow_all: {bool(getattr(ctx.permission_manager, 'allow_all', False))}")
        print(f"granted_once_count: {granted_once_count}")
        ctx.log_action("permissions.status", "Wyświetlono aktualny stan trybu zgód na zasoby.", {"allow_all": bool(getattr(ctx.permission_manager, "allow_all", False)), "granted_once_count": granted_once_count})
        return CliCommandResult(True)

    if action in {"all", "on", "global"}:
        ctx.permission_manager.allow_all = True
        print(_("cli.permissions.global_on"))
        ctx.log_action("permissions.mode.global", _("cli.permissions.global_on"))
        return CliCommandResult(True)

    if action in {"ask", "off", "interactive"}:
        ctx.permission_manager.allow_all = False
        print(_("cli.permissions.ask_on"))
        ctx.log_action("permissions.mode.ask", "Włączono interakcyjny tryb zgód per zasób.")
        return CliCommandResult(True)

    if action in {"reset", "clear"}:
        granted_once = getattr(ctx.permission_manager, "granted_once", None)
        if isinstance(granted_once, set):
            granted_once.clear()
            print(_("cli.permissions.reset_done"))
            ctx.log_action("permissions.reset", _("cli.permissions.reset_done"))
        else:
            print(_("cli.permissions.reset_empty"))
            ctx.log_action("permissions.reset.unavailable", "Brak obsługi listy zapamiętanych zgód w aktywnym menedżerze uprawnień.")
        return CliCommandResult(True)

    print(_("cli.permissions.usage"))
    ctx.log_action("permissions.invalid", "Niepoprawne użycie komendy zarządzania zgodami.", {"raw": raw_lower})
    return CliCommandResult(True)


def _handle_goal_flow(
    raw: str,
    ctx: CliContext,
    *,
    router_engine: Any,
) -> CliCommandResult:
    """Handle the goal confirmation / rejection / re-detection flow."""
    from amiagi.interfaces.shared_cli_helpers import _PLAN_TRACKING_RELATIVE_PATH

    if _is_goal_confirmation_message(raw):
        confirmed_goal = ctx.pending_goal_candidate
        ctx.pending_goal_candidate = None

        plan_path = _upsert_main_plan_goal(ctx.work_dir, confirmed_goal)  # type: ignore[arg-type]
        print(f"Zarejestrowano główny cel: {confirmed_goal}")
        ctx.log_action("goal.confirmed", "Użytkownik potwierdził główny cel, zapisano plan główny w notatkach.", {"goal": confirmed_goal, "plan_path": str(plan_path)})

        planning_prompt = (
            "Użytkownik potwierdził główny cel pracy. "
            "Najpierw zaplanuj realizację krok po kroku i rozpocznij wykonanie przez realny tool_call.\n\n"
            f"Główny cel: {confirmed_goal}\n"
            f"Plan bazowy znajduje się w: {_PLAN_TRACKING_RELATIVE_PATH}. "
            "Aktualizuj current_stage i statusy po każdym potwierdzonym etapie."
        )
        router_engine._user_turns_without_plan_update = 0
        processed = router_engine._process_cli_user_turn(planning_prompt)
        if not processed:
            ctx.log_action("goal.plan.denied", "Odmowa dostępu do sieci podczas planowania celu.")
            return CliCommandResult(True)
        router_engine._last_user_message = confirmed_goal
        ctx.log_action("goal.plan.done", "Uruchomiono planowanie i realizację po potwierdzeniu głównego celu.", {"goal": confirmed_goal})
        return CliCommandResult(True)

    if _is_goal_rejection_message(raw):
        ctx.pending_goal_candidate = None
        print("Anulowano kandydat celu. Podaj nowy cel frazą typu: 'Twoim celem jest ...'.")
        ctx.log_action("goal.rejected", "Użytkownik odrzucił parafrazę celu.")
        return CliCommandResult(True)

    replacement_candidate = _extract_goal_candidate_from_message(raw)
    if replacement_candidate is not None:
        ctx.pending_goal_candidate = replacement_candidate
        print(
            "Czy dobrze rozumiem, że moim głównym celem jest: "
            f"{ctx.pending_goal_candidate}? Odpowiedz 'tak' lub 'nie'."
        )
        ctx.log_action("goal.candidate.updated", "Zaktualizowano kandydata celu na podstawie doprecyzowania użytkownika.", {"goal_candidate": ctx.pending_goal_candidate})
        return CliCommandResult(True)

    print("Oczekuję potwierdzenia celu: odpowiedz 'tak' lub 'nie'.")
    return CliCommandResult(True)


def _handle_bye(ctx: CliContext) -> CliCommandResult:
    ctx.log_action("session.bye.request", "Zakończenie sesji z podsumowaniem i zapisem punktu startowego.")
    network_resource = _network_resource_for_model(ctx.chat_service.ollama_client.base_url)
    network_reason = (
        "Podsumowanie sesji wymaga wywołania lokalnego modelu."
        if network_resource == "network.local"
        else "Podsumowanie sesji wymaga dostępu do modelu przez sieć zewnętrzną."
    )
    if network_resource == "network.local":
        granted = ctx.permission_manager.request_local_network(network_reason)
    else:
        granted = ctx.permission_manager.request_internet(network_reason)
    if not granted:
        ctx.log_action("session.bye.denied", "Użytkownik odmówił zasobu do utworzenia podsumowania sesji.")
        return CliCommandResult(True)

    summary = ctx.chat_service.summarize_session_for_restart()
    print(_("cli.session.bye_saved"))
    print("\n--- START POINT ---")
    print(summary)
    print("\nDo zobaczenia.")
    ctx.log_action("session.bye.done", "Sesja zakończona po zapisaniu podsumowania startowego.", {"summary_chars": len(summary)})
    return CliCommandResult(True, should_exit=True)


def _handle_queue_status(ctx: CliContext) -> CliCommandResult:
    policy = ctx.chat_service.ollama_client.queue_policy
    vram_advisor = ctx.chat_service.ollama_client.vram_advisor
    if policy is None:
        print(_("cli.queue.disabled"))
        ctx.log_action("queue.status", "Wyświetlenie statusu kolejki modeli (wyłączona).")
        return CliCommandResult(True)

    snapshot = policy.snapshot()
    print("\n--- MODEL QUEUE STATUS ---")
    print(f"queue_length: {snapshot.get('queue_length', 0)}")
    print(f"queue: {snapshot.get('queue', [])}")
    print(f"queue_max_wait_seconds: {snapshot.get('queue_max_wait_seconds')}")
    print(f"supervisor_min_free_vram_mb: {snapshot.get('supervisor_min_free_vram_mb')}")

    if vram_advisor is not None:
        profile = vram_advisor.detect()
        print(f"vram: free_mb={profile.free_mb}, total_mb={profile.total_mb}, suggested_num_ctx={profile.suggested_num_ctx}")
    else:
        print(_("cli.queue.no_vram"))

    recent = snapshot.get("recent_decisions", [])
    print("recent_decisions:")
    if isinstance(recent, list) and recent:
        for item in recent[-10:]:
            print(f"- {item}")
    else:
        print(_("cli.queue.no_decisions"))

    ctx.log_action("queue.status", "Wyświetlenie statusu kolejki modeli i ostatnich decyzji polityki.", {"queue_length": snapshot.get("queue_length", 0), "recent_decisions": len(recent) if isinstance(recent, list) else 0})
    return CliCommandResult(True)


def _handle_goal_status(ctx: CliContext) -> CliCommandResult:
    snapshot = _read_plan_tracking_snapshot(ctx.work_dir)
    repair_info: dict | None = None
    if snapshot.get("parse_error"):
        repair_info = _repair_plan_tracking_file(ctx.work_dir)
        snapshot = _read_plan_tracking_snapshot(ctx.work_dir)
    print("\n--- GOAL STATUS ---")
    print(f"path: {snapshot.get('path')}")
    print(f"exists: {snapshot.get('exists')}")
    print(f"goal: {snapshot.get('goal', '')}")
    print(f"current_stage: {snapshot.get('current_stage', '')}")
    print(f"tasks: {snapshot.get('tasks_done', 0)}/{snapshot.get('tasks_total', 0)} zakończonych")
    if snapshot.get("parse_error"):
        print("parse_error: true")
    if repair_info and repair_info.get("repaired"):
        print("auto_repair: true")
        if repair_info.get("backup_path"):
            print(f"backup_path: {repair_info.get('backup_path')}")
    ctx.log_action("goal.status", "Wyświetlono status głównego celu i etapu realizacji.", {
        "exists": snapshot.get("exists"),
        "goal": snapshot.get("goal", ""),
        "current_stage": snapshot.get("current_stage", ""),
        "tasks_total": snapshot.get("tasks_total", 0),
        "tasks_done": snapshot.get("tasks_done", 0),
        "auto_repaired": bool(repair_info and repair_info.get("repaired")),
    })
    return CliCommandResult(True)


def _handle_framework_directive(directive: FrameworkDirective, ctx: CliContext) -> CliCommandResult:
    resolved = _resolve_workspace_path(ctx, directive.path)
    if directive.action == "read_file":
        ctx.log_action("framework.read_file.request", "Wykonanie dyrektywy frameworka: odczyt zawartości pliku.", {"path": str(resolved)})
        if not ctx.permission_manager.request_disk_read("Odczyt zawartości pliku wymaga dostępu do dysku."):
            ctx.log_action("framework.read_file.denied", "Użytkownik odmówił odczytu pliku.", {"path": str(directive.path)})
            return CliCommandResult(True)
        if not resolved.exists() or not resolved.is_file():
            print(f"Nie znaleziono pliku: {resolved}")
            ctx.log_action("framework.read_file.missing", "Wskazany plik nie istnieje lub nie jest plikiem regularnym.", {"path": str(resolved)})
            return CliCommandResult(True)
        try:
            content = resolved.read_text(encoding="utf-8")
        except Exception as error:
            print(f"Błąd odczytu pliku: {error}")
            ctx.log_action("framework.read_file.error", "Błąd podczas odczytu pliku.", {"path": str(resolved), "error": str(error)})
            return CliCommandResult(True)
        max_chars = 12000
        if len(content) > max_chars:
            content_to_show = content[:max_chars] + "\n\n[TRUNCATED]"
        else:
            content_to_show = content
        print("\n--- FILE CONTENT ---")
        print(content_to_show)
        ctx.log_action("framework.read_file.done", "Zwrócono użytkownikowi zawartość pliku.", {"path": str(resolved), "chars": len(content), "truncated": len(content) > max_chars})
        return CliCommandResult(True)
    # Unknown directive — not handled
    return CliCommandResult(False)


def _handle_import_dialog(raw: str, ctx: CliContext) -> CliCommandResult:
    ctx.log_action("import_dialog.request", "Import treści dyskusji bez kodu do pamięci.")
    if not ctx.permission_manager.request_disk_read("Import dialogu wymaga odczytu pliku z dysku."):
        ctx.log_action("import_dialog.denied", "Odmowa odczytu pliku przez użytkownika.")
        return CliCommandResult(True)
    parts = raw.split(maxsplit=1)
    path = Path(parts[1].strip()) if len(parts) == 2 else Path("początkowe_konsultacje.md")
    if not path.exists():
        print(f"Nie znaleziono pliku: {path}")
        ctx.log_action("import_dialog.missing", "Nie znaleziono wskazanego pliku.", {"path": str(path)})
        return CliCommandResult(True)
    text = path.read_text(encoding="utf-8")
    discussion = extract_dialogue_without_code(text)
    ctx.chat_service.save_discussion_context(discussion)
    print(_("cli.import.done"))
    ctx.log_action("import_dialog.done", "Zapisano kontekst dyskusji do pamięci.", {"path": str(path)})
    return CliCommandResult(True)


def _handle_create_python(raw: str, ctx: CliContext) -> CliCommandResult:
    ctx.log_action("create_python.request", "Generowanie i zapis kodu Python.")
    parts = raw.split(maxsplit=2)
    if len(parts) < 3:
        print(_("cli.create_python.usage"))
        ctx.log_action("create_python.invalid", "Niepoprawne użycie komendy create-python.")
        return CliCommandResult(True)

    output_path = Path(parts[1].strip())
    description = parts[2].strip()
    network_resource = _network_resource_for_model(ctx.chat_service.ollama_client.base_url)
    network_reason = (
        "Połączenie z lokalnym API modelu wymaga dostępu do sieci lokalnej."
        if network_resource == "network.local"
        else "Połączenie z modelem wymaga dostępu do internetu/sieci zewnętrznej."
    )
    if network_resource == "network.local":
        if not ctx.permission_manager.request_local_network(network_reason):
            ctx.log_action("create_python.denied", "Odmowa dostępu do sieci lokalnej.")
            return CliCommandResult(True)
    else:
        if not ctx.permission_manager.request_internet(network_reason):
            ctx.log_action("create_python.denied", "Odmowa dostępu do sieci zewnętrznej.")
            return CliCommandResult(True)

    if not ctx.permission_manager.request_disk_write("Zapis wygenerowanego skryptu wymaga zapisu na dysku."):
        ctx.log_action("create_python.denied", "Odmowa zapisu pliku skryptu.")
        return CliCommandResult(True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    code = ctx.chat_service.generate_python_code(description)
    output_path.write_text(code + "\n", encoding="utf-8")
    print(f"Zapisano skrypt: {output_path}")
    ctx.log_action("create_python.done", "Wygenerowano i zapisano skrypt Python.", {"path": str(output_path), "chars": len(code)})
    return CliCommandResult(True)


def _handle_run_python(raw: str, ctx: CliContext) -> CliCommandResult:
    ctx.log_action("run_python.request", "Uruchomienie skryptu Python.")
    parts = shlex.split(raw)
    if len(parts) < 2:
        print(_("cli.run_python.usage"))
        ctx.log_action("run_python.invalid", "Niepoprawne użycie komendy run-python.")
        return CliCommandResult(True)

    script_path = Path(parts[1])
    script_args = parts[2:]
    if not ctx.permission_manager.request_disk_read("Uruchomienie skryptu wymaga odczytu pliku z dysku."):
        ctx.log_action("run_python.denied", "Odmowa odczytu pliku skryptu.")
        return CliCommandResult(True)
    if not ctx.permission_manager.request_process_exec("Uruchomienie skryptu wymaga wykonania procesu systemowego."):
        ctx.log_action("run_python.denied", "Odmowa wykonania procesu systemowego.")
        return CliCommandResult(True)
    if not script_path.exists():
        print(f"Nie znaleziono skryptu: {script_path}")
        ctx.log_action("run_python.missing", "Nie znaleziono wskazanego skryptu.", {"path": str(script_path)})
        return CliCommandResult(True)

    try:
        result = ctx.script_executor.execute_python(script_path, script_args)
    except Exception as error:
        print(f"Błąd uruchomienia: {error}")
        ctx.log_action("run_python.error", "Błąd podczas uruchomienia skryptu Python.", {"error": str(error)})
        return CliCommandResult(True)

    print(f"Polecenie: {' '.join(result.command)}")
    print(f"Kod wyjścia: {result.exit_code}")
    if result.stdout.strip():
        print("\n--- STDOUT ---")
        print(result.stdout)
    if result.stderr.strip():
        print("\n--- STDERR ---")
        print(result.stderr)
    ctx.log_action("run_python.done", "Zakończono wykonanie skryptu Python.", {"path": str(script_path), "exit_code": result.exit_code})
    return CliCommandResult(True)


def _handle_run_shell(raw: str, ctx: CliContext) -> CliCommandResult:
    ctx.log_action("run_shell.request", "Uruchomienie polecenia shell z polityką whitelist.")
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        print(_("cli.run_shell.usage"))
        ctx.log_action("run_shell.invalid", "Niepoprawne użycie komendy run-shell.")
        return CliCommandResult(True)

    command_text = parts[1].strip()
    _ok, validation_error = parse_and_validate_shell_command(command_text, ctx.shell_policy)
    if validation_error is not None:
        print(f"Odrzucono polecenie: {validation_error}")
        ctx.log_action("run_shell.rejected", "Odrzucono polecenie shell przez politykę.", {"error": validation_error})
        return CliCommandResult(True)

    if not ctx.permission_manager.request_process_exec("Uruchomienie polecenia shell wymaga wykonania procesu systemowego."):
        ctx.log_action("run_shell.denied", "Odmowa wykonania procesu shell.")
        return CliCommandResult(True)

    try:
        result = ctx.script_executor.execute_shell(command_text)
    except Exception as error:
        print(f"Błąd uruchomienia: {error}")
        ctx.log_action("run_shell.error", "Błąd wykonania polecenia shell.", {"error": str(error)})
        return CliCommandResult(True)

    print(f"Polecenie: {' '.join(result.command)}")
    print(f"Kod wyjścia: {result.exit_code}")
    if result.stdout.strip():
        print("\n--- STDOUT ---")
        print(result.stdout)
    if result.stderr.strip():
        print("\n--- STDERR ---")
        print(result.stderr)
    ctx.log_action("run_shell.done", "Zakończono wykonanie polecenia shell.", {"command": command_text, "exit_code": result.exit_code})
    return CliCommandResult(True)


def _handle_history(raw: str, ctx: CliContext) -> CliCommandResult:
    ctx.log_action("history.show", "Odczyt historii wiadomości z pamięci.")
    parts = raw.split(maxsplit=1)
    limit = 10
    if len(parts) == 2 and parts[1].isdigit():
        limit = max(1, min(200, int(parts[1])))
    messages = ctx.chat_service.memory_repository.recent_messages(limit=limit)
    if not messages:
        print(_("cli.history.empty"))
        return CliCommandResult(True)
    for message in messages:
        print(f"[{message.created_at.isoformat(timespec='seconds')}] {message.role}: {message.content}")
    return CliCommandResult(True)


def _handle_remember(raw: str, ctx: CliContext) -> CliCommandResult:
    ctx.log_action("remember.request", "Zapis notatki użytkownika.")
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        print(_("cli.remember.usage"))
        ctx.log_action("remember.invalid", "Niepoprawne użycie komendy remember.")
        return CliCommandResult(True)
    ctx.chat_service.remember(parts[1].strip())
    print(_("cli.remember.done"))
    return CliCommandResult(True)


def _handle_memories(raw: str, ctx: CliContext) -> CliCommandResult:
    ctx.log_action("memories.search", "Przegląd zawartości pamięci.")
    parts = raw.split(maxsplit=1)
    query = parts[1].strip() if len(parts) == 2 else None
    records = ctx.chat_service.memory_repository.search_memories(query=query, limit=20)
    if not records:
        print(_("cli.memories.empty"))
        return CliCommandResult(True)
    for record in records:
        print(f"[{record.created_at.isoformat(timespec='seconds')}] {record.kind}/{record.source}: {record.content}")
    return CliCommandResult(True)


# ── Utility functions moved from cli.py ──────────────────────────────

def _upsert_main_plan_goal(work_dir: Path, goal: str) -> Path:
    from amiagi.interfaces.shared_cli_helpers import _PLAN_TRACKING_RELATIVE_PATH
    from datetime import datetime

    plan_path = work_dir / _PLAN_TRACKING_RELATIVE_PATH
    plan_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict
    if plan_path.exists() and plan_path.is_file():
        try:
            loaded = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            loaded = None
        payload = loaded if isinstance(loaded, dict) else {}
    else:
        payload = {}

    previous_goal_raw = payload.get("goal", "")
    previous_goal = previous_goal_raw.strip() if isinstance(previous_goal_raw, str) else ""

    payload["goal"] = goal
    if previous_goal and previous_goal != goal:
        payload["key_achievement"] = ""
        payload["current_stage"] = "goal_reset_required"
        payload["tasks"] = []
    else:
        payload.setdefault("key_achievement", "")
        payload.setdefault("current_stage", "planowanie")
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            payload["tasks"] = []
    payload["updated_at"] = datetime.utcnow().isoformat() + "Z"

    plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan_path


def collect_capabilities(
    *,
    permission_manager: PermissionManager,
    autonomous_mode: bool,
    check_network: bool = False,
) -> dict:
    """Build a capabilities dict (extracted from ``run_cli`` closure)."""
    fswebcam = shutil.which("fswebcam")
    ffmpeg = shutil.which("ffmpeg")
    arecord = shutil.which("arecord")
    camera_devices = sorted(str(path) for path in Path("/dev").glob("video*"))

    audio_devices: list[str] = []
    audio_probe_status = "not_checked"
    if arecord is not None:
        if permission_manager.request_process_exec("Sprawdzenie urządzeń audio wymaga wykonania procesu systemowego."):
            try:
                completed = subprocess.run(
                    [arecord, "-l"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if completed.returncode == 0:
                    audio_probe_status = "ok"
                    for line in completed.stdout.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("card "):
                            audio_devices.append(stripped)
                else:
                    audio_probe_status = "failed"
            except Exception:
                audio_probe_status = "failed"
        else:
            audio_probe_status = "permission_denied:process.exec"

    network_status = "not_checked"
    if check_network:
        if permission_manager.request_internet("Weryfikacja łączności sieciowej wymaga dostępu do internetu."):
            try:
                request = Request(
                    url="https://www.google.com",
                    headers={"User-Agent": "amiagi/0.1"},
                    method="HEAD",
                )
                with urlopen(request, timeout=10) as response:
                    network_status = f"ok:{getattr(response, 'status', 'unknown')}"
            except Exception as error:
                network_status = f"failed:{error}"
        else:
            network_status = "permission_denied:network.internet"

    tool_readiness = {
        "read_file": True,
        "list_dir": True,
        "run_shell": True,
        "run_python": True,
        "check_python_syntax": True,
        "fetch_web": True,
        "search_web": True,
        "write_file": True,
        "append_file": True,
        "capture_camera_frame": bool(camera_devices) and bool(fswebcam or ffmpeg),
        "record_microphone_clip": bool(arecord),
        "check_capabilities": True,
    }

    return {
        "ok": True,
        "tool": "check_capabilities",
        "autonomous_mode": autonomous_mode,
        "camera_devices": camera_devices,
        "audio_devices": audio_devices,
        "audio_probe_status": audio_probe_status,
        "network_status": network_status,
        "binaries": {
            "fswebcam": bool(fswebcam),
            "ffmpeg": bool(ffmpeg),
            "arecord": bool(arecord),
            "curl": bool(shutil.which("curl")),
        },
        "tool_readiness": tool_readiness,
    }
