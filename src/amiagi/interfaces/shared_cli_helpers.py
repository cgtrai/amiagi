"""Shared helper functions used by both *cli.py* and *textual_cli.py*.

Extracted from ``cli.py`` during the v1.0.3 Strangler-Fig cleanup so that
``textual_cli.py`` no longer needs a direct import from the plain-CLI adapter.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import is_dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import json
import random
import re

from typing import Any, cast

from amiagi.application.chat_service import ChatService
from amiagi import __version__
from amiagi.i18n import _

# ---------------------------------------------------------------------------
# Landing page: ASCII art logo + MOTD
# ---------------------------------------------------------------------------

_AMIAGI_LOGO = r"""
               _              ___    _    ____ ___ 
              / \   _ __ ___ |_ _|  / \  / ___|_ _|
             / _ \ | '_ ` _ \ | |  / _ \| |  _ | | 
            / ___ \| | | | | || | / ___ \ |_| || | 
           /_/   \_\_| |_| |_|___/_/   \_\____|___|
"""

_MOTD_COUNT = 8

_PLAN_TRACKING_RELATIVE_PATH = "notes/main_plan.json"

# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def _build_landing_banner(*, mode: str) -> str:
    """Return the startup banner string for *mode* ('textual' or 'cli')."""
    pool = [_(f"motd.{i}") for i in range(_MOTD_COUNT)]
    motd = random.choice(pool)  # noqa: S311
    lines = [
        _AMIAGI_LOGO.rstrip(),
        _("banner.mode_line", version=__version__, mode=mode),
        "",
        _("banner.motd_prefix", motd=motd),
        "",
        _("banner.help_hint"),
        " ",
        " ",
    ]
    return "\n".join(lines)


def _build_operator_command_catalog() -> list[dict[str, str]]:
    """Return the shared operator command catalog for CLI/TUI/Web surfaces."""
    return [
        {"command": "/help", "description": _("cli.help.cmd.help"), "category": "session", "web_support": "run", "web_note": "Supported in WWW"},
        {"command": "/cls", "description": _("cli.help.cmd.cls"), "category": "session", "web_support": "unsupported", "web_note": "Terminal-only before UAT"},
        {"command": "/cls all", "description": _("cli.help.cmd.cls_all"), "category": "session", "web_support": "unsupported", "web_note": "Terminal-only before UAT"},
        {"command": "/models current", "description": _("cli.help.cmd.models_current"), "category": "models", "web_support": "run", "web_note": "Supported in WWW"},
        {"command": "/models show", "description": _("cli.help.cmd.models_show"), "category": "models", "web_support": "run", "web_note": "Supported in WWW"},
        {"command": "/models chose <nr>", "description": _("cli.help.cmd.models_chose"), "category": "models", "web_support": "insert", "web_note": "Fill the model number before running"},
        {"command": "/permissions", "description": _("cli.help.cmd.permissions"), "category": "permissions", "web_support": "run", "web_note": "Supported in WWW"},
        {"command": "/permissions all", "description": _("cli.help.cmd.permissions_all"), "category": "permissions", "web_support": "run", "web_note": "Supported in WWW"},
        {"command": "/permissions ask", "description": _("cli.help.cmd.permissions_ask"), "category": "permissions", "web_support": "run", "web_note": "Supported in WWW"},
        {"command": "/permissions reset", "description": _("cli.help.cmd.permissions_reset"), "category": "permissions", "web_support": "run", "web_note": "Supported in WWW"},
        {"command": "/show-system-context [tekst]", "description": _("cli.help.cmd.show_system_context"), "category": "context", "web_support": "insert", "web_note": "Add optional sample text before running"},
        {"command": "/goal-status", "description": _("cli.help.cmd.goal_status"), "category": "planning", "web_support": "run", "web_note": "Supported in WWW"},
        {"command": "/goal", "description": _("cli.help.cmd.goal"), "category": "planning", "web_support": "run", "web_note": "Supported in WWW"},
        {"command": "/queue-status", "description": _("cli.help.cmd.queue_status"), "category": "runtime", "web_support": "run", "web_note": "Supported in WWW"},
        {"command": "/capabilities [--network]", "description": _("cli.help.cmd.capabilities"), "category": "runtime", "web_support": "unsupported", "web_note": "Permission-interactive in CLI/TUI only before UAT"},
        {"command": "/history [n]", "description": _("cli.help.cmd.history"), "category": "memory", "web_support": "insert", "web_note": "Optional limit can be added before running"},
        {"command": "/remember <tekst>", "description": _("cli.help.cmd.remember"), "category": "memory", "web_support": "insert", "web_note": "Fill note text before running"},
        {"command": "/memories [zapytanie]", "description": _("cli.help.cmd.memories"), "category": "memory", "web_support": "insert", "web_note": "Optional query can be added before running"},
        {"command": "/import-dialog [plik]", "description": _("cli.help.cmd.import_dialog"), "category": "tools", "web_support": "unsupported", "web_note": "Disk-permission flow not supported in WWW before UAT"},
        {"command": "/create-python <plik> <opis>", "description": _("cli.help.cmd.create_python"), "category": "tools", "web_support": "unsupported", "web_note": "Generation/write flow remains CLI/TUI-only before UAT"},
        {"command": "/run-python <plik> [arg ...]", "description": _("cli.help.cmd.run_python"), "category": "tools", "web_support": "unsupported", "web_note": "Process-exec flow remains CLI/TUI-only before UAT"},
        {"command": "/run-shell <polecenie>", "description": _("cli.help.cmd.run_shell"), "category": "tools", "web_support": "unsupported", "web_note": "Shell execution remains CLI/TUI-only before UAT"},
        {"command": "/lang <code>", "description": _("cli.help.cmd.lang"), "category": "session", "web_support": "unsupported", "web_note": "Web locale is request-driven before UAT"},
        {"command": "/bye", "description": _("cli.help.cmd.bye"), "category": "session", "web_support": "unsupported", "web_note": "Session-closing flow is CLI/TUI-only before UAT"},
        {"command": "/exit", "description": _("cli.help.cmd.exit"), "category": "session", "web_support": "unsupported", "web_note": "Terminal-only before UAT"},
    ]


def _web_command_support(raw_command: str) -> str:
    lowered = str(raw_command or "").strip().lower()
    if lowered in {"/cls", "/cls all", "/bye", "/exit"} or lowered.startswith("/lang"):
        return "unsupported"
    if lowered.startswith("/capabilities") or lowered.startswith("/import-dialog") or lowered.startswith("/create-python") or lowered.startswith("/run-python") or lowered.startswith("/run-shell"):
        return "unsupported"
    if lowered.startswith("/models chose") or lowered.startswith("/show-system-context") or lowered.startswith("/history") or lowered.startswith("/remember") or lowered.startswith("/memories"):
        return "run"
    if lowered == "/help" or lowered.startswith("/models") or lowered.startswith("/permissions") or lowered in {"/goal-status", "/goal", "/queue-status"}:
        return "run"
    return "unsupported"


def _fetch_ollama_models(chat_service: ChatService) -> tuple[list[str], str | None]:
    client = getattr(chat_service, "ollama_client", None)
    list_models = getattr(client, "list_models", None)
    if not callable(list_models):
        return [], "Aktywny klient modelu nie obsługuje listowania modeli."

    try:
        raw_models = list_models()
    except Exception as error:
        return [], str(error)

    if isinstance(raw_models, Iterable) and not isinstance(raw_models, (str, bytes, dict)):
        iterable_models = raw_models
    else:
        iterable_models = []

    names: list[str] = []
    seen: set[str] = set()
    for item in iterable_models:
        name = str(item).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names, None


def _set_executor_model(chat_service: ChatService, model_name: str) -> tuple[bool, str]:
    client = getattr(chat_service, "ollama_client", None)
    if client is None:
        return False, "Brak aktywnego klienta modelu."

    current = str(getattr(client, "model", "") or "").strip()
    if current == model_name:
        return True, current

    if is_dataclass(client) and not isinstance(client, type):
        try:
            chat_service.ollama_client = replace(cast(Any, client), model=model_name)
            return True, current
        except Exception:
            pass

    try:
        setattr(client, "model", model_name)
    except Exception as error:
        return False, str(error)
    return True, current


def _select_executor_model_by_index(chat_service: ChatService, one_based_index: int) -> tuple[bool, str, list[str]]:
    models, error = _fetch_ollama_models(chat_service)
    if error is not None:
        return False, f"Nie udało się pobrać listy modeli: {error}", []
    if not models:
        return False, _("cli.models_empty"), []
    if one_based_index < 1 or one_based_index > len(models):
        return False, f"Nieprawidłowy numer modelu: {one_based_index}. Dostępny zakres: 1..{len(models)}.", models

    selected_model = models[one_based_index - 1]
    ok, error_message = _set_executor_model(chat_service, selected_model)
    if not ok:
        return False, f"Nie udało się przełączyć modelu: {error_message}", models
    return True, selected_model, models


def _network_resource_for_model(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return "network.local"
    return "network.internet"


def _read_plan_tracking_snapshot(work_dir: Path) -> dict:
    plan_path = work_dir / _PLAN_TRACKING_RELATIVE_PATH
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


def _repair_plan_tracking_file(work_dir: Path) -> dict:
    plan_path = work_dir / _PLAN_TRACKING_RELATIVE_PATH
    if not plan_path.exists() or not plan_path.is_file():
        return {"repaired": False, "reason": "missing_file", "path": str(plan_path)}

    raw = plan_path.read_text(encoding="utf-8", errors="replace")
    goal_match = re.search(r'"goal"\s*:\s*"([^"]*)"', raw)
    stage_match = re.search(r'"current_stage"\s*:\s*"([^"]*)"', raw)
    achievement_match = re.search(r'"key_achievement"\s*:\s*"([^"]*)"', raw)

    repaired_payload = {
        "goal": goal_match.group(1).strip() if goal_match else "",
        "key_achievement": achievement_match.group(1).strip() if achievement_match else "",
        "current_stage": stage_match.group(1).strip() if stage_match else "",
        "tasks": [],
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "repair_note": "main_plan_auto_repaired_from_malformed_json",
    }

    backup_path = plan_path.with_suffix(plan_path.suffix + ".broken")
    try:
        backup_path.write_text(raw, encoding="utf-8")
    except Exception:
        backup_path = None

    plan_path.write_text(
        json.dumps(repaired_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "repaired": True,
        "path": str(plan_path),
        "backup_path": str(backup_path) if backup_path is not None else "",
        "goal": repaired_payload["goal"],
        "current_stage": repaired_payload["current_stage"],
    }
