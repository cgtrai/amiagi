"""CLI adapter – thin client (Faza 4.2 / 5.2, Strangler Fig v1.0.3).

The main I/O loop delegates slash commands to ``cli_commands.dispatch_cli_command``
and everything else to ``RouterEngine._process_cli_user_turn``.
"""
from __future__ import annotations

import select
import sys
from pathlib import Path

from amiagi.application.chat_service import ChatService
from amiagi.application.event_bus import EventBus, LogEvent
from amiagi.application.router_engine import RouterEngine
from amiagi.application.shell_policy import default_shell_policy, load_shell_policy
from amiagi.infrastructure.script_executor import ScriptExecutor
from amiagi.interfaces.cli_commands import (
    CliContext,
    collect_capabilities as _collect_capabilities,
    dispatch_cli_command,
)
from amiagi.interfaces.permission_manager import PermissionManager
from amiagi.interfaces.shared_cli_helpers import (
    _build_landing_banner,
    _fetch_ollama_models,
    _set_executor_model,
)
from amiagi.i18n import _


# ── Help text ────────────────────────────────────────────────────────

def _build_cli_help_commands() -> list[tuple[str, str]]:
    return [
        ("/help", _("cli.help.cmd.help")),
        ("/cls", _("cli.help.cmd.cls")),
        ("/cls all", _("cli.help.cmd.cls_all")),
        ("/models current", _("cli.help.cmd.models_current")),
        ("/models show", _("cli.help.cmd.models_show")),
        ("/models chose <nr>", _("cli.help.cmd.models_chose")),
        ("/permissions", _("cli.help.cmd.permissions")),
        ("/permissions all", _("cli.help.cmd.permissions_all")),
        ("/permissions ask", _("cli.help.cmd.permissions_ask")),
        ("/permissions reset", _("cli.help.cmd.permissions_reset")),
        ("/show-system-context [tekst]", _("cli.help.cmd.show_system_context")),
        ("/goal-status", _("cli.help.cmd.goal_status")),
        ("/goal", _("cli.help.cmd.goal")),
        ("/queue-status", _("cli.help.cmd.queue_status")),
        ("/capabilities [--network]", _("cli.help.cmd.capabilities")),
        ("/history [n]", _("cli.help.cmd.history")),
        ("/remember <tekst>", _("cli.help.cmd.remember")),
        ("/memories [zapytanie]", _("cli.help.cmd.memories")),
        ("/import-dialog [plik]", _("cli.help.cmd.import_dialog")),
        ("/create-python <plik> <opis>", _("cli.help.cmd.create_python")),
        ("/run-python <plik> [arg ...]", _("cli.help.cmd.run_python")),
        ("/run-shell <polecenie>", _("cli.help.cmd.run_shell")),
        ("/lang <code>", _("cli.help.cmd.lang")),
        ("/bye", _("cli.help.cmd.bye")),
        ("/exit", _("cli.help.cmd.exit")),
    ]


_HELP_COMMANDS: list[tuple[str, str]] = _build_cli_help_commands()


def _build_help_text() -> str:
    command_width = max(len(command) for command, _desc in _HELP_COMMANDS)
    lines = [_("cli.help.header")]
    for command, description in _HELP_COMMANDS:
        lines.append(f"  {command.ljust(command_width)}  - {description}")
    return "\n".join(lines)


def _rebuild_cli_help() -> None:
    """Rebuild help commands and text after a language switch."""
    global _HELP_COMMANDS, HELP_TEXT
    _HELP_COMMANDS = _build_cli_help_commands()
    HELP_TEXT = _build_help_text()


HELP_TEXT = _build_help_text()


# ── Setup helpers ────────────────────────────────────────────────────

_IDLE_REACTIVATION_SECONDS = 30.0


def _ensure_default_executor_model(
    chat_service: ChatService,
) -> tuple[str | None, list[str], str | None]:
    models, error = _fetch_ollama_models(chat_service)
    if error is not None or not models:
        return None, models, error

    default_model = models[0]
    ok, switch_error = _set_executor_model(chat_service, default_model)
    if not ok:
        return None, models, switch_error
    return default_model, models, None


# ── Main entry point ─────────────────────────────────────────────────

def run_cli(
    chat_service: ChatService,
    shell_policy_path: Path,
    autonomous_mode: bool = False,
    max_idle_autoreactivations: int = 2,
    router_mailbox_log_path: Path | None = None,
) -> None:
    permission_manager = PermissionManager()
    if autonomous_mode:
        permission_manager.allow_all = True
    script_executor = ScriptExecutor()
    work_dir = chat_service.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = Path.cwd()
    mailbox_log_path = (
        router_mailbox_log_path
        if router_mailbox_log_path is not None
        else Path("./logs/router_mailbox.jsonl")
    )

    # --- RouterEngine: shared orchestration core (Faza 4 — Strangler Fig) ---
    _event_bus = EventBus()
    _router_engine = RouterEngine(
        chat_service=chat_service,
        permission_manager=permission_manager,
        script_executor=script_executor,
        work_dir=work_dir,
        shell_policy_path=shell_policy_path,
        event_bus=_event_bus,
        autonomous_mode=autonomous_mode,
        router_mailbox_log_path=mailbox_log_path,
        supervisor_dialogue_log_path=Path("./logs/supervision_dialogue.jsonl"),
    )
    _router_engine._background_enabled = False  # CLI is synchronous
    _router_engine._max_idle_autoreactivations = max(0, int(max_idle_autoreactivations))

    def _on_engine_log(event: LogEvent) -> None:
        if event.panel == "system":
            print(f"\nSystem> {event.message}")
        elif event.panel == "model":
            print(f"\nModel> {event.message}")

    _event_bus.on(LogEvent, _on_engine_log)

    # --- Log helper ---
    def log_action(action: str, intent: str, details: dict | None = None) -> None:
        if chat_service.activity_logger is not None:
            chat_service.activity_logger.log(action=action, intent=intent, details=details)

    # --- Idle-aware input ---
    def read_user_input_with_idle(prompt: str, timeout_seconds: float) -> str:
        if not sys.stdin.isatty():
            return input(prompt).strip()

        print(prompt, end="", flush=True)
        while True:
            try:
                readable, _w, _x = select.select([sys.stdin], [], [], timeout_seconds)
            except Exception:
                return input("\n" + prompt).strip()
            if readable:
                line = sys.stdin.readline()
                if line == "":
                    raise EOFError
                return line.strip()
            _router_engine.run_idle_reactivation_cycle()

    # --- Shell policy ---
    try:
        shell_policy = load_shell_policy(shell_policy_path)
    except Exception as error:
        shell_policy = default_shell_policy()
        print(
            "Uwaga: nie udało się wczytać polityki shell "
            f"z {shell_policy_path}: {error}. Używam polityki domyślnej."
        )

    # --- Landing page ---
    print(_build_landing_banner(mode="cli"))

    log_action(
        "session.start",
        "Rozpoczęcie sesji CLI i przygotowanie kontekstu ciągłości.",
        {
            "shell_policy_path": str(shell_policy_path),
            "work_dir": str(work_dir),
            "autonomous_mode": autonomous_mode,
        },
    )

    selected_default_model, discovered_models, default_model_error = (
        _ensure_default_executor_model(chat_service)
    )
    if selected_default_model is not None:
        log_action(
            "models.default.selected",
            "Ustawiono domyślny model wykonawczy na pierwszy model z listy Ollama.",
            {
                "selected_model": selected_default_model,
                "models_count": len(discovered_models),
            },
        )
    else:
        fallback_model = str(getattr(chat_service.ollama_client, "model", ""))
        if default_model_error:
            log_action(
                "models.default.fallback",
                "Pozostawiono model fallback z powodu błędu listowania modeli.",
                {"error": default_model_error, "fallback_model": fallback_model},
            )

    try:
        readiness = chat_service.bootstrap_runtime_readiness()
        log_action(
            "session.readiness",
            "Model potwierdził gotowość po automatycznym bootstrapie.",
            {"chars": len(readiness)},
        )
    except Exception as error:
        log_action(
            "session.readiness.error",
            "Nie udało się uzyskać komunikatu gotowości podczas bootstrapu.",
            {"error": str(error)},
        )

    # --- Build CliContext for command dispatcher ---
    ctx = CliContext(
        chat_service=chat_service,
        permission_manager=permission_manager,
        script_executor=script_executor,
        work_dir=work_dir,
        workspace_root=workspace_root,
        shell_policy=shell_policy,
        autonomous_mode=autonomous_mode,
        log_action=log_action,
        collect_capabilities=lambda check_network=False: _collect_capabilities(
            permission_manager=permission_manager,
            autonomous_mode=autonomous_mode,
            check_network=check_network,
        ),
    )

    # ── Main I/O loop ────────────────────────────────────────────────
    while True:
        try:
            raw = read_user_input_with_idle("\nTy> ", _IDLE_REACTIVATION_SECONDS)
        except (EOFError, KeyboardInterrupt):
            print("\nZamknięto sesję.")
            log_action("session.interrupt", "Zakończenie sesji przez przerwanie wejścia.")
            break

        if not raw or not raw.strip():
            continue
        raw = raw.strip()

        # Dispatch slash commands to cli_commands module
        result = dispatch_cli_command(
            raw,
            ctx,
            router_engine=_router_engine,
            help_text=HELP_TEXT,
        )
        if result.handled:
            # Rebuild help text if /lang was invoked
            if raw.lower().startswith("/lang"):
                _rebuild_cli_help()
            if result.should_exit:
                break
            continue

        # Fall through to RouterEngine for regular messages
        try:
            _router_engine._process_cli_user_turn(raw)
        except Exception as error:
            print(f"Błąd: {error}")
            log_action(
                "chat.error",
                "Błąd podczas obsługi wiadomości użytkownika.",
                {"error": str(error)},
            )
