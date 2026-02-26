from __future__ import annotations

import shlex
import re
import shutil
import select
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse
import json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from amiagi.application.framework_directives import FrameworkDirective, parse_framework_directive
from amiagi.application.tool_calling import ToolCall, parse_tool_calls
from amiagi.application.shell_policy import (
    default_shell_policy,
    load_shell_policy,
    parse_and_validate_shell_command,
)
from amiagi.application.discussion_sync import extract_dialogue_without_code
from amiagi.application.chat_service import ChatService
from amiagi.infrastructure.script_executor import ScriptExecutor
from amiagi.interfaces.permission_manager import PermissionManager


_HELP_COMMANDS: list[tuple[str, str]] = [
        ("/help", "pokaż pomoc"),
        ("/show-system-context [tekst]", "pokaż kontekst systemowy przekazywany do modelu"),
    ("/goal-status", "pokaż cel główny i etap z notes/main_plan.json"),
    ("/goal", "alias: pokaż cel główny i etap z notes/main_plan.json"),
        ("/queue-status", "pokaż stan kolejki modeli i decyzji polityki VRAM"),
        ("/capabilities [--network]", "pokaż gotowość narzędzi i backendów"),
        ("/history [n]", "pokaż ostatnie wiadomości (domyślnie 10)"),
        ("/remember <tekst>", "zapisz notatkę do pamięci"),
        ("/memories [zapytanie]", "przeszukaj pamięć"),
        ("/import-dialog [plik]", "zapisz dialog (bez kodu) jako kontekst pamięci"),
        ("/create-python <plik> <opis>", "wygeneruj i zapisz skrypt Python przez model"),
        ("/run-python <plik> [arg ...]", "uruchom skrypt Python z argumentami"),
        ("/run-shell <polecenie>", "uruchom polecenie shell z polityką whitelist"),
        ("/bye", "zapisz podsumowanie sesji i zakończ"),
        ("/exit", "zakończ bez podsumowania"),
]


def _build_help_text() -> str:
        command_width = max(len(command) for command, _ in _HELP_COMMANDS)
        lines = ["Komendy:"]
        for command, description in _HELP_COMMANDS:
                lines.append(f"  {command.ljust(command_width)}  - {description}")
        return "\n".join(lines)


HELP_TEXT = _build_help_text()

_ALLOWED_TOOLS_TEXT = (
    "read_file, list_dir, run_shell, run_python, check_python_syntax, fetch_web, search_web, capture_camera_frame, record_microphone_clip, check_capabilities, write_file, append_file"
)
_SUPPORTED_TOOL_NAMES = {
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

_IDLE_REACTIVATION_SECONDS = 30.0
_REACTIVATION_ALLOWED_STATES = {"RUNNING", "STALLED"}


_MAX_CODE_PATH_FAILURE_STREAK = 2
_PLAN_TRACKING_RELATIVE_PATH = "notes/main_plan.json"
_MAX_USER_TURNS_WITHOUT_PLAN_UPDATE = 2


def _build_pseudo_tool_corrective_prompt() -> str:
    return (
        "Twoja poprzednia odpowiedź zawiera pseudo-kod użycia narzędzi frameworka, "
        "ale nie uruchamia realnej operacji. "
        "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call jako następny krok wykonawczy. "
        "Bez opisu i bez kodu Python.\n"
        f"{_PYTHON_WORKFLOW_CHECKLIST}\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}."
    )


def _build_unknown_tools_corrective_prompt(unknown_tools: list[str]) -> str:
    return (
        "Twoje poprzednie wywołanie użyło nieobsługiwanych narzędzi: "
        + ", ".join(sorted(set(unknown_tools)))
        + ".\n"
        f"Dostępne narzędzia to wyłącznie: {_ALLOWED_TOOLS_TEXT}.\n"
        f"{_PYTHON_WORKFLOW_CHECKLIST}\n"
        "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call z tej listy."
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


def _build_unparsed_tool_call_corrective_prompt() -> str:
    return (
        "Twoja poprzednia odpowiedź wygląda jak próba wywołania narzędzia, "
        "ale nie jest w poprawnym formacie wykonywalnym przez framework. "
        "Teraz zwróć WYŁĄCZNIE jeden blok w formacie:\n"
        "```tool_call\\n{\"tool\":\"...\",\"args\":{...},\"intent\":\"...\"}\\n```\n"
        "Bez YAML, bez dodatkowego opisu i bez pseudo-kodu.\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}."
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


def _build_code_path_abort_prompt(last_user_message: str) -> str:
    return (
        "Wykryto powtarzające się błędy składni skryptu Python i brak postępu w tym wątku. "
        "Porzuć teraz wątek generowania kodu i wróć do głównego celu zadania. "
        "Wykonaj WYŁĄCZNIE jeden następny krok operacyjny przez tool_call, "
        "preferując narzędzia badawcze (np. list_dir/read_file/search_web/fetch_web) zamiast write_file/check_python_syntax.\n\n"
        f"Ostatnia wiadomość użytkownika: {last_user_message}\n"
        f"Dozwolone narzędzia: {_ALLOWED_TOOLS_TEXT}."
    )


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


def _upsert_main_plan_goal(work_dir: Path, goal: str) -> Path:
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


def _network_resource_for_model(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return "network.local"
    return "network.internet"


def _is_non_action_placeholder(answer: str) -> bool:
    normalized = answer.strip().strip("`").strip().lower()
    return normalized in {"", "none", "null", "n/a", "brak"}


def _looks_like_unparsed_tool_call(answer: str) -> bool:
    lower = answer.lower()
    if "tool_call" not in lower:
        return False
    yaml_like = "tool_call:" in lower and ("name:" in lower or "args:" in lower)
    fenced_yaml = "```yaml" in lower or "```yml" in lower
    return yaml_like or fenced_yaml


def _canonical_tool_name_for_validation(name: str) -> str:
    aliases = {
        "run_command": "run_shell",
        "execute_command": "run_shell",
    }
    return aliases.get(name, name)


def _has_supported_tool_call(answer: str) -> bool:
    calls = parse_tool_calls(answer)
    if not calls:
        return False
    return any(_canonical_tool_name_for_validation(call.tool) in _SUPPORTED_TOOL_NAMES for call in calls)


def _has_unknown_tool_calls(answer: str) -> bool:
    calls = parse_tool_calls(answer)
    if not calls:
        return False
    return any(_canonical_tool_name_for_validation(call.tool) not in _SUPPORTED_TOOL_NAMES for call in calls)


def _has_only_supported_tool_calls(answer: str) -> bool:
    calls = parse_tool_calls(answer)
    if not calls:
        return False
    return all(_canonical_tool_name_for_validation(call.tool) in _SUPPORTED_TOOL_NAMES for call in calls)


def _canonicalize_tool_calls(calls: list[ToolCall]) -> str:
    blocks: list[str] = []
    for call in calls:
        payload = {
            "tool": call.tool,
            "args": call.args,
            "intent": call.intent,
        }
        blocks.append("```tool_call\n" + json.dumps(payload, ensure_ascii=False) + "\n```")
    return "\n".join(blocks)


def _resolve_tool_path(raw_path: str, work_dir: Path) -> Path:
    def _alias_set() -> set[str]:
        canonical = work_dir.name
        return {
            canonical,
            canonical.replace("-", "_"),
            canonical.replace("_", "-"),
        }

    def _collapse_duplicate_alias_segments(path: Path, aliases: set[str]) -> Path:
        if not path.parts:
            return path

        anchor = path.anchor
        parts = list(path.parts)
        start_index = 1 if anchor else 0
        normalized_parts: list[str] = []
        previous_is_alias = False

        for part in parts[start_index:]:
            current_is_alias = part in aliases
            if current_is_alias and previous_is_alias:
                continue
            normalized_parts.append(part)
            previous_is_alias = current_is_alias

        if anchor:
            return Path(anchor, *normalized_parts)
        return Path(*normalized_parts) if normalized_parts else Path(".")

    cleaned = raw_path.strip()
    if not cleaned:
        return work_dir

    candidate = Path(cleaned)
    aliases = _alias_set()

    if candidate.is_absolute():
        return _collapse_duplicate_alias_segments(candidate, aliases)

    parts = candidate.parts
    if parts and parts[0] in aliases:
        candidate = Path(*parts[1:]) if len(parts) > 1 else Path(".")

    resolved = work_dir / candidate
    return _collapse_duplicate_alias_segments(resolved, aliases)


def _is_path_within_work_dir(path: Path, work_dir: Path) -> bool:
    try:
        normalized_path = path.resolve(strict=False)
        normalized_work_dir = work_dir.resolve(strict=False)
    except Exception:
        return False
    return normalized_work_dir == normalized_path or normalized_work_dir in normalized_path.parents


def _is_main_plan_tracking_path(path: Path, work_dir: Path) -> bool:
    try:
        normalized_path = path.resolve(strict=False)
        normalized_plan_path = (work_dir / _PLAN_TRACKING_RELATIVE_PATH).resolve(strict=False)
    except Exception:
        return False
    return normalized_path == normalized_plan_path


def _default_artifact_path(work_dir: Path, stem: str, suffix: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return work_dir / "artifacts" / f"{stem}_{timestamp}{suffix}"


def _path_candidate_from_argument(value: str, work_dir: Path) -> Path | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.startswith("-"):
        return None

    looks_like_path = (
        "/" in cleaned
        or "\\" in cleaned
        or cleaned.startswith(".")
        or cleaned.startswith("~")
        or cleaned.startswith("/")
        or bool(re.search(r"\.[A-Za-z0-9]{1,8}$", cleaned))
    )
    if not looks_like_path:
        return None

    return _resolve_tool_path(cleaned, work_dir)


def _detect_preferred_microphone_device() -> str | None:
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
    pattern = re.compile(r"^card\s+(?P<card>\d+):\s*(?P<name>[^\[]+)\[.*?\],\s*device\s+(?P<device>\d+):")
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


def _build_microphone_profiles(requested_rate: int, requested_channels: int) -> list[tuple[int, int]]:
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


def _read_main_plan_payload(work_dir: Path) -> dict | None:
    plan_path = work_dir / _PLAN_TRACKING_RELATIVE_PATH
    if not plan_path.exists() or not plan_path.is_file():
        return None
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _task_has_required_fields(task: object) -> bool:
    if not isinstance(task, dict):
        return False
    required = ("id", "title", "status")
    for field in required:
        value = task.get(field)
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def _plan_persistence_snapshot(work_dir: Path) -> dict:
    payload = _read_main_plan_payload(work_dir)
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

    valid_header = isinstance(goal, str) and bool(goal.strip()) and isinstance(current_stage, str) and bool(current_stage.strip())
    has_tasks = isinstance(tasks, list) and len(tasks) > 0
    return {
        "exists": True,
        "valid": bool(valid_header and isinstance(tasks, list)),
        "has_tasks": bool(has_tasks),
        "required": not bool(has_tasks),
    }


def _main_plan_fingerprint(work_dir: Path) -> str:
    payload = _read_main_plan_payload(work_dir)
    if payload is None:
        return ""
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        return ""


def _has_actionable_main_plan(snapshot: dict) -> bool:
    if not bool(snapshot.get("exists")):
        return False
    if bool(snapshot.get("parse_error")):
        return False
    tasks_total = snapshot.get("tasks_total", 0)
    tasks_done = snapshot.get("tasks_done", 0)
    if not isinstance(tasks_total, int) or not isinstance(tasks_done, int):
        return False
    return tasks_total > 0 and tasks_done < tasks_total


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


def _parse_search_results_from_html(html: str, engine: str, max_results: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    if engine == "duckduckgo":
        pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            re.IGNORECASE,
        )
        for match in pattern.finditer(html):
            href = match.group("href").strip()
            title = re.sub(r"<.*?>", "", match.group("title")).strip()
            if not href:
                continue
            results.append({"title": title or href, "url": href})
            if len(results) >= max_results:
                break
        return results

    pattern = re.compile(r'<a\s+href="/url\?q=(?P<url>[^"&]+)[^"]*"[^>]*>(?P<title>.*?)</a>', re.IGNORECASE)
    for match in pattern.finditer(html):
        href = unquote(match.group("url").strip())
        if not href.startswith("http"):
            continue
        title = re.sub(r"<.*?>", "", match.group("title")).strip()
        results.append({"title": title or href, "url": href})
        if len(results) >= max_results:
            break
    return results


def run_cli(
    chat_service: ChatService,
    shell_policy_path: Path,
    autonomous_mode: bool = False,
    max_idle_autoreactivations: int = 2,
) -> None:
    permission_manager = PermissionManager()
    if autonomous_mode:
        permission_manager.allow_all = True
    script_executor = ScriptExecutor()
    max_idle_autoreactivations = max(0, int(max_idle_autoreactivations))
    last_tool_activity_monotonic = time.monotonic()
    last_idle_reactivation_monotonic = time.monotonic()
    passive_turns = 0
    last_user_message = ""
    last_work_state = "RUNNING"
    idle_reactivation_attempts = 0
    idle_reactivation_capped_notified = False
    code_path_failure_streak = 0
    user_turns_without_plan_update = 0
    pending_goal_candidate: str | None = None
    work_dir = chat_service.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = Path.cwd()
    last_referenced_file: Path | None = None

    file_ref_pattern = re.compile(r"(?P<path>[\w./-]+\.[A-Za-z0-9]+)")
    read_this_file_pattern = re.compile(
        r"(przeczytaj|odczytaj).*(tego\s+pliku|ten\s+plik)",
        re.IGNORECASE,
    )
    autonomy_pattern = re.compile(
        r"\b(kontynuuj|działaj|dzialaj|ty\s+decyduj|sam\s+decyduj|decyduj|nie\s+zatrzymuj\s+się|nie\s+zatrzymuj\s*sie|rozpocznij\s+.*eksperyment)\b",
        re.IGNORECASE,
    )
    action_pattern = re.compile(
        r"\b(przeczytaj|odczytaj|analizuj|przeanalizuj|rozpocznij|wykonaj|zapisz)\b",
        re.IGNORECASE,
    )
    pseudo_tool_usage_pattern = re.compile(
        r"\b(read_file|list_dir|run_shell|run_command|run_python|check_python_syntax|fetch_web|write_file|append_file)\s*\(",
        re.IGNORECASE,
    )
    python_code_block_pattern = re.compile(
        r"```(?:python|py)?\s*[\s\S]*?(?:def\s+\w+\(|class\s+\w+\(|import\s+\w+|from\s+\w+\s+import|print\()",
        re.IGNORECASE,
    )

    def resolve_tool_path(raw_path: str) -> Path:
        return _resolve_tool_path(raw_path, work_dir)

    def resolve_workspace_path(path: Path) -> Path:
        if path.is_absolute():
            return path
        return workspace_root / path

    def collect_capabilities(check_network: bool = False) -> dict:
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

    def _canonical_tool_name(name: str) -> str:
        aliases = {
            "run_command": "run_shell",
            "execute_command": "run_shell",
        }
        return aliases.get(name, name)

    def _gpu_utilization_percent() -> int | None:
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
        if not values:
            return None
        return max(values)

    def _supervision_context(stage: str) -> dict:
        gpu_util = _gpu_utilization_percent()
        idle_seconds = int(max(0.0, time.monotonic() - last_tool_activity_monotonic))
        plan_snapshot = _read_plan_tracking_snapshot(work_dir)
        plan_persistence = _plan_persistence_snapshot(work_dir)
        return {
            "stage": stage,
            "passive_turns": passive_turns,
            "idle_seconds_since_tool_activity": idle_seconds,
            "work_state": last_work_state,
            "main_plan_tracking": plan_snapshot,
            "plan_persistence": plan_persistence,
            "gpu_utilization_percent": gpu_util,
            "gpu_busy_over_50": (gpu_util is not None and gpu_util > 50),
            "should_remind_continuation": passive_turns >= 2,
        }

    def ensure_plan_persisted(user_message: str, answer: str) -> str:
        current_answer = answer
        snapshot = _plan_persistence_snapshot(work_dir)
        if not snapshot.get("exists"):
            return current_answer
        if snapshot.get("has_tasks"):
            return current_answer

        for attempt in range(1, 3):
            corrective_prompt = _build_plan_persistence_corrective_prompt(user_message)
            log_action(
                "plan.persistence.enforce.start",
                "Wymuszono trwały zapis planu głównego po wykryciu braku planu lub zadań.",
                {
                    "attempt": attempt,
                    "required": snapshot.get("required", True),
                    "exists": snapshot.get("exists", False),
                },
            )
            corrected = chat_service.ask(corrective_prompt)
            corrected = apply_supervisor(corrective_prompt, corrected, stage="plan_persistence_corrective")
            current_answer = resolve_tool_calls(corrected)

            snapshot = _plan_persistence_snapshot(work_dir)
            if snapshot.get("has_tasks"):
                log_action(
                    "plan.persistence.enforce.done",
                    "Zapis planu głównego został potwierdzony po wymuszeniu korekty.",
                    {"attempt": attempt},
                )
                return current_answer

        log_action(
            "plan.persistence.enforce.failed",
            "Nie udało się potwierdzić trwałego zapisu planu po korektach.",
            {
                "exists": snapshot.get("exists", False),
                "valid": snapshot.get("valid", False),
                "has_tasks": snapshot.get("has_tasks", False),
            },
        )
        return current_answer

    def ensure_plan_progress_updated(user_message: str, answer: str) -> str:
        current_answer = answer
        baseline = _main_plan_fingerprint(work_dir)
        if not baseline:
            return current_answer

        for attempt in range(1, 3):
            corrective_prompt = _build_plan_progress_update_prompt(user_message)
            log_action(
                "plan.progress.enforce.start",
                "Wymuszono aktualizację postępu planu po wykryciu braku zmian przez kolejne tury.",
                {"attempt": attempt},
            )
            corrected = chat_service.ask(corrective_prompt)
            corrected = apply_supervisor(corrective_prompt, corrected, stage="plan_progress_corrective")
            current_answer = resolve_tool_calls(corrected)

            current_fingerprint = _main_plan_fingerprint(work_dir)
            if current_fingerprint and current_fingerprint != baseline:
                log_action(
                    "plan.progress.enforce.done",
                    "Potwierdzono aktualizację planu po wymuszeniu korekty postępu.",
                    {"attempt": attempt},
                )
                return current_answer

        log_action(
            "plan.progress.enforce.failed",
            "Nie udało się potwierdzić aktualizacji planu po korektach postępu.",
            {},
        )
        return current_answer

    def _short_text(value: str, max_chars: int = 1800) -> dict:
        if len(value) <= max_chars:
            return {"text": value, "truncated": False, "total_chars": len(value)}
        return {
            "text": value[:max_chars],
            "truncated": True,
            "total_chars": len(value),
        }

    def _compact_tool_result_for_model(result: dict) -> dict:
        compact = dict(result)
        for key in ("content", "stdout", "stderr"):
            value = compact.get(key)
            if isinstance(value, str):
                shortened = _short_text(value)
                compact[key] = shortened["text"]
                if shortened["truncated"]:
                    compact[f"{key}_truncated"] = True
                    compact[f"{key}_total_chars"] = shortened["total_chars"]
        return compact

    def _compact_tool_results_payload(results: list[dict]) -> str:
        payload = {
            "results": [
                {
                    "tool": item.get("tool"),
                    "intent": item.get("intent", ""),
                    "result": _compact_tool_result_for_model(item.get("result", {})),
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
            slim_result = {
                "ok": result.get("ok"),
                "tool": result.get("tool", item.get("tool")),
                "error": result.get("error"),
                "path": result.get("path"),
                "url": result.get("url"),
                "exit_code": result.get("exit_code"),
                "content_truncated": result.get("content_truncated", False),
                "stdout_truncated": result.get("stdout_truncated", False),
                "stderr_truncated": result.get("stderr_truncated", False),
            }
            slim_results.append({"tool": item.get("tool"), "intent": item.get("intent", ""), "result": slim_result})
        return json.dumps({"results": slim_results, "compact": True}, ensure_ascii=False)

    def log_action(action: str, intent: str, details: dict | None = None) -> None:
        if chat_service.activity_logger is not None:
            chat_service.activity_logger.log(action=action, intent=intent, details=details)

    def emit_runtime_notice(action: str, message: str, details: dict | None = None) -> None:
        print(f"\nSystem> {message}")
        log_action(action, message, details)

    def apply_supervisor(user_message: str, model_answer: str, stage: str) -> str:
        nonlocal last_work_state
        supervisor = chat_service.supervisor_service
        if supervisor is None:
            return model_answer

        user_message_with_context = (
            f"{user_message}\n\n"
            "[RUNTIME_SUPERVISION_CONTEXT]\n"
            + json.dumps(_supervision_context(stage), ensure_ascii=False)
        )
        result = supervisor.refine(
            user_message=user_message_with_context,
            model_answer=model_answer,
            stage=stage,
        )
        last_work_state = result.work_state
        if result.repairs_applied > 0:
            log_action(
                "supervisor.repair.applied",
                "Zastosowano poprawkę odpowiedzi modelu wykonawczego przez nadzorcę.",
                {
                    "stage": stage,
                    "repairs_applied": result.repairs_applied,
                    "reason_code": result.reason_code,
                    "work_state": result.work_state,
                },
            )
            if _has_unknown_tool_calls(result.answer):
                log_action(
                    "supervisor.repair.rejected",
                    "Odrzucono poprawkę nadzorcy zawierającą nieobsługiwane narzędzie.",
                    {
                        "stage": stage,
                        "reason_code": result.reason_code,
                        "had_supported_in_original": _has_supported_tool_call(model_answer),
                    },
                )
                return model_answer
        else:
            log_action(
                "supervisor.review.done",
                "Nadzorca ocenił odpowiedź bez konieczności poprawek.",
                {
                    "stage": stage,
                    "reason_code": result.reason_code,
                    "work_state": result.work_state,
                },
            )
        return result.answer

    def execute_tool_call(tool_call: ToolCall) -> dict:
        tool = _canonical_tool_name(tool_call.tool)
        args = tool_call.args

        if tool == "read_file":
            path = resolve_tool_path(str(args.get("path", "")))
            max_chars = int(args.get("max_chars", 12000))
            if not permission_manager.request_disk_read("Tool read_file wymaga odczytu pliku z dysku."):
                return {"ok": False, "error": "permission_denied:disk.read"}
            if not path.exists() and "amiagi" in str(path):
                repaired = Path(str(path).replace("amiagi", "amiagi"))
                if repaired.exists() and repaired.is_file():
                    path = repaired
            if not path.exists() or not path.is_file():
                return {"ok": False, "error": "file_not_found", "path": str(path)}
            try:
                content = path.read_text(encoding="utf-8")
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(path)}
            truncated = len(content) > max_chars
            return {
                "ok": True,
                "tool": "read_file",
                "path": str(path),
                "content": content[:max_chars],
                "truncated": truncated,
                "total_chars": len(content),
            }

        if tool == "list_dir":
            path = resolve_tool_path(str(args.get("path", "")))
            if not permission_manager.request_disk_read("Tool list_dir wymaga odczytu katalogu."):
                return {"ok": False, "error": "permission_denied:disk.read"}
            if not path.exists() or not path.is_dir():
                return {"ok": False, "error": "dir_not_found", "path": str(path)}
            items = sorted(child.name for child in path.iterdir())
            return {"ok": True, "tool": "list_dir", "path": str(path), "items": items}

        if tool == "run_shell":
            command_text = str(args.get("command", "")).strip()
            if not command_text:
                return {"ok": False, "error": "missing_command"}
            _, validation_error = parse_and_validate_shell_command(command_text, shell_policy)
            if validation_error is not None:
                return {"ok": False, "error": f"policy_rejected:{validation_error}"}
            if not permission_manager.request_process_exec("Tool run_shell wymaga wykonania procesu."):
                return {"ok": False, "error": "permission_denied:process.exec"}
            result = script_executor.execute_shell(command_text)
            return {
                "ok": True,
                "tool": "run_shell",
                "command": result.command,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        if tool == "run_python":
            path = resolve_tool_path(str(args.get("path", "")))
            run_args = args.get("args", [])
            if not isinstance(run_args, list):
                return {"ok": False, "error": "args_must_be_list"}
            if not _is_path_within_work_dir(path, work_dir):
                return {
                    "ok": False,
                    "error": "path_outside_work_dir",
                    "path": str(path),
                    "work_dir": str(work_dir),
                }

            blocked_path_args: list[str] = []
            for item in run_args:
                raw_value = str(item)
                candidate = _path_candidate_from_argument(raw_value, work_dir)
                if candidate is not None and not _is_path_within_work_dir(candidate, work_dir):
                    blocked_path_args.append(raw_value)
            if blocked_path_args:
                return {
                    "ok": False,
                    "error": "path_outside_work_dir_in_args",
                    "args": blocked_path_args,
                    "work_dir": str(work_dir),
                }

            if not permission_manager.request_disk_read("Tool run_python wymaga odczytu skryptu."):
                return {"ok": False, "error": "permission_denied:disk.read"}
            if not permission_manager.request_process_exec("Tool run_python wymaga wykonania procesu."):
                return {"ok": False, "error": "permission_denied:process.exec"}
            if not path.exists() or not path.is_file():
                return {"ok": False, "error": "file_not_found", "path": str(path)}
            result = script_executor.execute_python(path, [str(item) for item in run_args])
            return {
                "ok": True,
                "tool": "run_python",
                "command": result.command,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        if tool == "check_python_syntax":
            path = resolve_tool_path(str(args.get("path", "")))
            if not permission_manager.request_disk_read(
                "Tool check_python_syntax wymaga odczytu skryptu.",
            ):
                return {"ok": False, "error": "permission_denied:disk.read"}
            if not path.exists() or not path.is_file():
                return {"ok": False, "error": "file_not_found", "path": str(path)}

            try:
                source = path.read_text(encoding="utf-8")
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(path)}

            try:
                compile(source, str(path), "exec")
            except SyntaxError as error:
                return {
                    "ok": False,
                    "tool": "check_python_syntax",
                    "path": str(path),
                    "syntax_ok": False,
                    "error": "syntax_error",
                    "message": str(error),
                    "line": error.lineno,
                    "offset": error.offset,
                    "text": error.text,
                }
            except Exception as error:
                return {
                    "ok": False,
                    "tool": "check_python_syntax",
                    "path": str(path),
                    "syntax_ok": False,
                    "error": str(error),
                }

            return {
                "ok": True,
                "tool": "check_python_syntax",
                "path": str(path),
                "syntax_ok": True,
            }

        if tool == "fetch_web":
            url = str(args.get("url", "")).strip()
            max_chars = int(args.get("max_chars", 12000))
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return {"ok": False, "error": "invalid_url_scheme"}
            if not permission_manager.request_internet("Tool fetch_web wymaga dostępu do internetu."):
                return {"ok": False, "error": "permission_denied:network.internet"}
            try:
                request = Request(
                    url=url,
                    headers={"User-Agent": "amiagi/0.1"},
                    method="GET",
                )
                with urlopen(request, timeout=20) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    content = response.read().decode(charset, errors="replace")
            except (HTTPError, URLError, TimeoutError) as error:
                return {"ok": False, "error": str(error), "url": url}

            truncated = len(content) > max_chars
            return {
                "ok": True,
                "tool": "fetch_web",
                "url": url,
                "content": content[:max_chars],
                "truncated": truncated,
                "total_chars": len(content),
            }

        if tool == "search_web":
            query = str(args.get("query", "")).strip()
            engine = str(args.get("engine", "duckduckgo")).strip().lower() or "duckduckgo"
            max_results_raw = int(args.get("max_results", 5))
            max_results = max(1, min(10, max_results_raw))

            if not query:
                return {"ok": False, "error": "missing_query"}
            if engine not in {"duckduckgo", "google"}:
                return {"ok": False, "error": "unsupported_engine", "engine": engine}
            if not permission_manager.request_internet("Tool search_web wymaga dostępu do internetu."):
                return {"ok": False, "error": "permission_denied:network.internet"}

            if engine == "duckduckgo":
                search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
            else:
                search_url = f"https://www.google.com/search?q={quote_plus(query)}"

            try:
                request = Request(
                    url=search_url,
                    headers={"User-Agent": "amiagi/0.1"},
                    method="GET",
                )
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
                "search_url": search_url,
                "results_count": len(results),
            }

        if tool == "capture_camera_frame":
            output_arg = str(args.get("output_path", "")).strip()
            device = str(args.get("device", "/dev/video0")).strip() or "/dev/video0"
            output_path = resolve_tool_path(output_arg) if output_arg else _default_artifact_path(work_dir, "camera", ".jpg")

            if not _is_path_within_work_dir(output_path, work_dir):
                return {
                    "ok": False,
                    "error": "path_outside_work_dir",
                    "path": str(output_path),
                    "work_dir": str(work_dir),
                }
            if not permission_manager.request_camera_access("Tool capture_camera_frame wymaga dostępu do kamery."):
                return {"ok": False, "error": "permission_denied:camera"}
            if not permission_manager.request_disk_write("Tool capture_camera_frame wymaga zapisu pliku obrazu."):
                return {"ok": False, "error": "permission_denied:disk.write"}
            if not permission_manager.request_process_exec("Tool capture_camera_frame wymaga wykonania procesu systemowego."):
                return {"ok": False, "error": "permission_denied:process.exec"}
            if not Path(device).exists():
                return {"ok": False, "error": "camera_device_not_found", "device": device}

            v4l2_ctl = shutil.which("v4l2-ctl")
            if v4l2_ctl is None:
                return {
                    "ok": False,
                    "error": "camera_init_tool_missing",
                    "details": "Zainstaluj v4l-utils (v4l2-ctl).",
                }

            init_command = [v4l2_ctl, "-d", device, "--set-ctrl=auto_exposure=1"]
            init_completed = subprocess.run(
                init_command,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if init_completed.returncode != 0:
                return {
                    "ok": False,
                    "error": "camera_init_failed",
                    "device": device,
                    "exit_code": init_completed.returncode,
                    "stdout": init_completed.stdout,
                    "stderr": init_completed.stderr,
                }

            output_path.parent.mkdir(parents=True, exist_ok=True)
            fswebcam = shutil.which("fswebcam")
            ffmpeg = shutil.which("ffmpeg")
            if fswebcam:
                command = [fswebcam, "-q", "-d", device, str(output_path)]
            elif ffmpeg:
                command = [ffmpeg, "-y", "-f", "video4linux2", "-i", device, "-frames:v", "1", str(output_path)]
            else:
                return {
                    "ok": False,
                    "error": "camera_backend_missing",
                    "details": "Zainstaluj fswebcam lub ffmpeg.",
                }

            completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
            if completed.returncode != 0 or not output_path.exists():
                return {
                    "ok": False,
                    "error": "camera_capture_failed",
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            return {
                "ok": True,
                "tool": "capture_camera_frame",
                "path": str(output_path),
                "device": device,
                "size_bytes": output_path.stat().st_size,
            }

        if tool == "record_microphone_clip":
            output_arg = str(args.get("output_path", "")).strip()
            output_path = resolve_tool_path(output_arg) if output_arg else _default_artifact_path(work_dir, "microphone", ".wav")
            duration_seconds = max(1, min(60, int(args.get("duration_seconds", 5))))
            sample_rate_hz = max(8000, min(48000, int(args.get("sample_rate_hz", 16000))))
            channels = max(1, min(2, int(args.get("channels", 1))))
            explicit_device = str(args.get("device", "")).strip()

            if not _is_path_within_work_dir(output_path, work_dir):
                return {
                    "ok": False,
                    "error": "path_outside_work_dir",
                    "path": str(output_path),
                    "work_dir": str(work_dir),
                }
            if not permission_manager.request_microphone_access("Tool record_microphone_clip wymaga dostępu do mikrofonu."):
                return {"ok": False, "error": "permission_denied:microphone"}
            if not permission_manager.request_disk_write("Tool record_microphone_clip wymaga zapisu pliku audio."):
                return {"ok": False, "error": "permission_denied:disk.write"}
            if not permission_manager.request_process_exec("Tool record_microphone_clip wymaga wykonania procesu systemowego."):
                return {"ok": False, "error": "permission_denied:process.exec"}

            arecord = shutil.which("arecord")
            if arecord is None:
                return {
                    "ok": False,
                    "error": "microphone_backend_missing",
                    "details": "Zainstaluj pakiet ALSA (arecord).",
                }

            preferred_device = explicit_device or _detect_preferred_microphone_device()
            profiles = _build_microphone_profiles(sample_rate_hz, channels)

            emit_runtime_notice(
                "microphone.recording.prepare",
                "[MIC] Przygotowanie nagrywania mikrofonu.",
                {
                    "output_path": str(output_path),
                    "duration_seconds": duration_seconds,
                    "device": preferred_device or "default",
                    "profiles_count": len(profiles),
                },
            )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            last_completed: subprocess.CompletedProcess[str] | None = None
            used_profile: tuple[int, int] | None = None
            used_device = preferred_device or "default"
            for index, (profile_rate, profile_channels) in enumerate(profiles, start=1):
                emit_runtime_notice(
                    "microphone.recording.active",
                    "[MIC] Nagrywanie aktywne.",
                    {
                        "attempt": index,
                        "duration_seconds": duration_seconds,
                        "device": used_device,
                        "sample_rate_hz": profile_rate,
                        "channels": profile_channels,
                        "output_path": str(output_path),
                    },
                )
                command = [
                    arecord,
                    "-q",
                    "-d",
                    str(duration_seconds),
                    "-f",
                    "S16_LE",
                    "-r",
                    str(profile_rate),
                    "-c",
                    str(profile_channels),
                ]
                if preferred_device:
                    command.extend(["-D", preferred_device])
                command.append(str(output_path))

                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=duration_seconds + 10,
                )
                last_completed = completed
                if completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                    used_profile = (profile_rate, profile_channels)
                    break

            if used_profile is None:
                emit_runtime_notice(
                    "microphone.recording.failed",
                    "[MIC] Nagrywanie nieudane.",
                    {
                        "device": used_device,
                        "attempted_profiles": [
                            {"sample_rate_hz": rate, "channels": ch}
                            for rate, ch in profiles
                        ],
                        "exit_code": last_completed.returncode if last_completed is not None else -1,
                    },
                )
                return {
                    "ok": False,
                    "error": "microphone_record_failed",
                    "exit_code": last_completed.returncode if last_completed is not None else -1,
                    "stdout": last_completed.stdout if last_completed is not None else "",
                    "stderr": last_completed.stderr if last_completed is not None else "",
                    "device": used_device,
                    "attempted_profiles": [
                        {"sample_rate_hz": rate, "channels": ch}
                        for rate, ch in profiles
                    ],
                }
            emit_runtime_notice(
                "microphone.recording.done",
                "[MIC] Nagrywanie zakończone.",
                {
                    "device": used_device,
                    "sample_rate_hz": used_profile[0],
                    "channels": used_profile[1],
                    "size_bytes": output_path.stat().st_size,
                    "path": str(output_path),
                },
            )
            return {
                "ok": True,
                "tool": "record_microphone_clip",
                "path": str(output_path),
                "duration_seconds": duration_seconds,
                "sample_rate_hz": used_profile[0],
                "channels": used_profile[1],
                "device": used_device,
                "size_bytes": output_path.stat().st_size,
            }

        if tool == "check_capabilities":
            check_network = bool(args.get("check_network", False))
            return collect_capabilities(check_network=check_network)

        if tool == "write_file":
            path = resolve_tool_path(str(args.get("path", "")))
            raw_content = args.get("content")
            if raw_content is None and "data" in args:
                raw_content = args.get("data")

            if isinstance(raw_content, str):
                content = raw_content
            elif raw_content is None:
                content = ""
            else:
                content = json.dumps(raw_content, ensure_ascii=False, indent=2)
            explicit_overwrite = bool(args.get("overwrite", False))
            overwrite = explicit_overwrite or _is_main_plan_tracking_path(path, work_dir)
            if not _is_path_within_work_dir(path, work_dir):
                return {
                    "ok": False,
                    "error": "path_outside_work_dir",
                    "path": str(path),
                    "work_dir": str(work_dir),
                }
            if not permission_manager.request_disk_write("Tool write_file wymaga zapisu pliku."):
                return {"ok": False, "error": "permission_denied:disk.write"}
            if path.suffix.lower() in {".json", ".jsonl"} and not content.strip():
                return {
                    "ok": False,
                    "error": "empty_content_not_allowed_for_json",
                    "path": str(path),
                }
            if path.exists() and not overwrite:
                return {"ok": False, "error": "file_exists_overwrite_required", "path": str(path)}
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(path)}
            return {"ok": True, "tool": "write_file", "path": str(path), "chars": len(content)}

        if tool == "append_file":
            path = resolve_tool_path(str(args.get("path", "")))
            content = str(args.get("content", ""))
            if not _is_path_within_work_dir(path, work_dir):
                return {
                    "ok": False,
                    "error": "path_outside_work_dir",
                    "path": str(path),
                    "work_dir": str(work_dir),
                }
            if not permission_manager.request_disk_write("Tool append_file wymaga zapisu pliku."):
                return {"ok": False, "error": "permission_denied:disk.write"}
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as file:
                    file.write(content)
            except Exception as error:
                return {"ok": False, "error": str(error), "path": str(path)}
            return {"ok": True, "tool": "append_file", "path": str(path), "chars": len(content)}

        return {
            "ok": False,
            "error": f"unknown_tool:{tool}",
            "allowed_tools": [
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
            ],
        }

    def resolve_tool_calls(
        initial_answer: str,
        max_steps: int = 3,
        allow_safe_fallback: bool = True,
    ) -> str:
        nonlocal last_tool_activity_monotonic
        nonlocal idle_reactivation_attempts
        nonlocal idle_reactivation_capped_notified
        nonlocal code_path_failure_streak

        def _log_rejected_pseudo_call(reason: str, answer: str) -> None:
            preview = _short_text(answer, max_chars=400)
            details = {
                "reason": reason,
                "answer_preview": preview["text"],
                "answer_preview_truncated": preview["truncated"],
                "answer_total_chars": preview["total_chars"],
            }
            log_action(
                "tool_call.pseudo_rejected",
                "Odrzucono pseudo-tool_call i uruchomiono ścieżkę korekty/fallback.",
                details,
            )

        def _run_safe_tool_fallback() -> str:
            fallback_call = ToolCall(
                tool="list_dir",
                args={"path": "."},
                intent="fallback_start",
            )
            log_action(
                "tool_call.fallback.request",
                "Uruchomiono bezpieczny krok awaryjny po nieudanej normalizacji tool_call.",
                {"tool": fallback_call.tool, "intent": fallback_call.intent},
            )
            tool_result = execute_tool_call(fallback_call)
            last_tool_activity_monotonic = time.monotonic()
            log_action(
                "tool_call.fallback.result",
                "Zakończono bezpieczny krok awaryjny narzędzia.",
                {"tool": fallback_call.tool, "ok": bool(tool_result.get("ok"))},
            )
            if not bool(tool_result.get("ok")):
                return _TOOL_CALL_RESOLUTION_FAILED_MESSAGE

            followup = (
                "[TOOL_RESULT]\n"
                + _compact_tool_results_payload(
                    [
                        {
                            "tool": fallback_call.tool,
                            "intent": fallback_call.intent,
                            "result": tool_result,
                        }
                    ]
                )
                + "\nNa podstawie tego wyniku odpowiedz użytkownikowi."
            )
            post_fallback_answer = chat_service.ask(followup)
            post_fallback_calls = parse_tool_calls(post_fallback_answer)
            if post_fallback_calls and not _has_unknown_tool_calls(post_fallback_answer):
                return resolve_tool_calls(
                    _canonicalize_tool_calls(post_fallback_calls),
                    max_steps=1,
                    allow_safe_fallback=False,
                )
            return post_fallback_answer

        current = initial_answer
        for _ in range(max_steps):
            tool_calls = parse_tool_calls(current)
            if tool_calls:
                current = apply_supervisor("[TOOL_FLOW]", current, stage="tool_flow")
                tool_calls = parse_tool_calls(current)
            if not tool_calls:
                if _looks_like_unparsed_tool_call(current):
                    _log_rejected_pseudo_call("unparsed_tool_call", current)
                    corrective_prompt = _build_unparsed_tool_call_corrective_prompt()
                    corrected = chat_service.ask(corrective_prompt)
                    corrected = apply_supervisor(
                        corrective_prompt,
                        corrected,
                        stage="unparsed_tool_call_corrective",
                    )
                    corrected_calls = parse_tool_calls(corrected)
                    if corrected_calls and not _has_unknown_tool_calls(corrected):
                        current = corrected
                        continue
                    return corrected
                if _is_non_action_placeholder(current):
                    corrective_prompt = _build_no_action_corrective_prompt(
                        "kontynuuj", str(work_dir / "wprowadzenie.md")
                    )
                    corrected = chat_service.ask(corrective_prompt)
                    corrected = apply_supervisor(corrective_prompt, corrected, stage="empty_answer_corrective")
                    corrected_calls = parse_tool_calls(corrected)
                    if corrected_calls and not _has_unknown_tool_calls(corrected):
                        current = corrected
                        continue
                    current = corrected
                    continue
                if python_code_block_pattern.search(current):
                    _log_rejected_pseudo_call("python_code_block", current)
                    corrective_prompt = _build_python_code_corrective_prompt()
                    corrected = chat_service.ask(corrective_prompt)
                    corrected = apply_supervisor(corrective_prompt, corrected, stage="python_code_corrective")
                    corrected_calls = parse_tool_calls(corrected)
                    if corrected_calls and not _has_unknown_tool_calls(corrected):
                        current = corrected
                        continue
                    current = corrected
                    continue
                if pseudo_tool_usage_pattern.search(current):
                    _log_rejected_pseudo_call("pseudo_tool_usage", current)
                    corrective_prompt = _build_pseudo_tool_corrective_prompt()
                    corrected = chat_service.ask(corrective_prompt)
                    corrected = apply_supervisor(corrective_prompt, corrected, stage="pseudo_tool_corrective")
                    corrected_calls = parse_tool_calls(corrected)
                    if corrected_calls and not _has_unknown_tool_calls(corrected):
                        current = corrected
                        continue
                    current = corrected
                    continue
                if _looks_like_unparsed_tool_call(current):
                    if not allow_safe_fallback:
                        return current
                    return _run_safe_tool_fallback()
                if python_code_block_pattern.search(current) or pseudo_tool_usage_pattern.search(current):
                    if not allow_safe_fallback:
                        return current
                    return _run_safe_tool_fallback()
                return current

            current = _canonicalize_tool_calls(tool_calls)

            aggregated_results: list[dict] = []
            unknown_tools: list[str] = []
            for tool_call in tool_calls:
                log_action(
                    "tool_call.request",
                    "Model zgłosił żądanie użycia narzędzia frameworka.",
                    {"tool": tool_call.tool, "intent": tool_call.intent},
                )
                tool_result = execute_tool_call(tool_call)
                last_tool_activity_monotonic = time.monotonic()
                idle_reactivation_attempts = 0
                idle_reactivation_capped_notified = False
                error = tool_result.get("error")
                if isinstance(error, str) and error.startswith("unknown_tool:"):
                    unknown_tools.append(error.removeprefix("unknown_tool:"))
                log_action(
                    "tool_call.result",
                    "Framework wykonał narzędzie i zwrócił wynik do modelu.",
                    {"tool": tool_call.tool, "ok": bool(tool_result.get("ok"))},
                )
                aggregated_results.append(
                    {
                        "tool": _canonical_tool_name(tool_call.tool),
                        "intent": tool_call.intent,
                        "result": tool_result,
                    }
                )

            if unknown_tools:
                followup = _build_unknown_tools_corrective_prompt(unknown_tools)
            else:
                syntax_failures = [
                    item for item in aggregated_results
                    if item.get("tool") == "check_python_syntax" and not bool(item.get("result", {}).get("ok"))
                ]
                if syntax_failures:
                    code_path_failure_streak += len(syntax_failures)
                else:
                    had_successful_tool = any(bool(item.get("result", {}).get("ok")) for item in aggregated_results)
                    if had_successful_tool:
                        code_path_failure_streak = 0

                if code_path_failure_streak >= _MAX_CODE_PATH_FAILURE_STREAK:
                    log_action(
                        "tool_flow.code_path.aborted",
                        "Przerwano wątek generowania kodu po powtarzających się błędach składni.",
                        {
                            "streak": code_path_failure_streak,
                            "threshold": _MAX_CODE_PATH_FAILURE_STREAK,
                        },
                    )
                    abort_prompt = _build_code_path_abort_prompt(last_user_message or "kontynuuj")
                    redirected = chat_service.ask(abort_prompt)
                    redirected = apply_supervisor(
                        abort_prompt,
                        redirected,
                        stage="code_path_abort_corrective",
                    )
                    redirected_calls = parse_tool_calls(redirected)
                    code_path_failure_streak = 0
                    if redirected_calls and not _has_unknown_tool_calls(redirected):
                        current = _canonicalize_tool_calls(redirected_calls)
                        continue
                    return redirected

                successful_writes = [
                    item for item in aggregated_results
                    if item.get("tool") == "write_file" and bool(item.get("result", {}).get("ok"))
                ]
                post_write_instruction = ""
                if successful_writes:
                    instructions: list[str] = []
                    for item in successful_writes:
                        result = item.get("result", {})
                        path = str(result.get("path", "")).strip()
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

                followup = (
                    "[TOOL_RESULT]\n"
                    + _compact_tool_results_payload(aggregated_results)
                    + "\nNa podstawie tego wyniku odpowiedz użytkownikowi."
                    + post_write_instruction
                    + "\nINSTRUKCJA SPÓJNOŚCI GŁÓWNEGO WĄTKU: "
                    + f"utrzymuj i aktualizuj plan w pliku '{_PLAN_TRACKING_RELATIVE_PATH}'. "
                    + "Po zakończeniu etapu zaktualizuj current_stage oraz statusy zadań."
                )
            current = chat_service.ask(followup)

        if parse_tool_calls(current):
            if not allow_safe_fallback:
                return current
            return _run_safe_tool_fallback()
        if _looks_like_unparsed_tool_call(current):
            if not allow_safe_fallback:
                return current
            return _run_safe_tool_fallback()
        if python_code_block_pattern.search(current) or pseudo_tool_usage_pattern.search(current):
            if not allow_safe_fallback:
                return current
            return _run_safe_tool_fallback()
        return current

    def read_user_input_with_idle(prompt: str, timeout_seconds: float) -> str:
        if not sys.stdin.isatty():
            return input(prompt).strip()

        print(prompt, end="", flush=True)
        while True:
            try:
                readable, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
            except Exception:
                return input("\n" + prompt).strip()
            if readable:
                line = sys.stdin.readline()
                if line == "":
                    raise EOFError
                return line.strip()
            run_idle_reactivation_cycle()

    def run_idle_reactivation_cycle() -> None:
        nonlocal passive_turns
        nonlocal last_idle_reactivation_monotonic
        nonlocal idle_reactivation_attempts
        nonlocal idle_reactivation_capped_notified

        now = time.monotonic()
        plan_snapshot = _read_plan_tracking_snapshot(work_dir)
        actionable_plan = _has_actionable_main_plan(plan_snapshot)
        if passive_turns <= 0 and not actionable_plan:
            return
        if last_work_state not in _REACTIVATION_ALLOWED_STATES:
            return
        if now - last_tool_activity_monotonic < _IDLE_REACTIVATION_SECONDS:
            return
        if now - last_idle_reactivation_monotonic < _IDLE_REACTIVATION_SECONDS:
            return
        if idle_reactivation_attempts >= max_idle_autoreactivations:
            if not idle_reactivation_capped_notified:
                print(
                    "\nModel> Wstrzymuję kolejne autowzbudzenia po "
                    f"{max_idle_autoreactivations} próbach. "
                    "Oczekuję decyzji użytkownika lub nowej aktywności narzędziowej."
                )
                log_action(
                    "idle.reactivation.capped",
                    "Wstrzymano kolejne auto-reaktywacje po osiągnięciu limitu prób.",
                    {
                        "max_attempts": max_idle_autoreactivations,
                        "work_state": last_work_state,
                    },
                )
                idle_reactivation_capped_notified = True
            last_idle_reactivation_monotonic = now
            return

        network_resource = _network_resource_for_model(chat_service.ollama_client.base_url)
        network_reason = (
            "Połączenie z lokalnym API modelu wymaga dostępu do sieci lokalnej."
            if network_resource == "network.local"
            else "Połączenie z modelem wymaga dostępu do internetu/sieci zewnętrznej."
        )
        if network_resource == "network.local":
            if not permission_manager.request_local_network(network_reason):
                log_action(
                    "idle.reactivation.denied",
                    "Pominięto auto-reaktywację po bezczynności z powodu odmowy zasobu sieciowego.",
                )
                last_idle_reactivation_monotonic = now
                return
        else:
            if not permission_manager.request_internet(network_reason):
                log_action(
                    "idle.reactivation.denied",
                    "Pominięto auto-reaktywację po bezczynności z powodu odmowy zasobu sieciowego.",
                )
                last_idle_reactivation_monotonic = now
                return

        idle_prompt = _build_idle_reactivation_prompt(last_user_message or "kontynuuj")
        idle_reactivation_attempts += 1
        idle_reactivation_capped_notified = False
        log_action(
            "idle.reactivation.start",
            "Uruchomiono auto-reaktywację po przekroczeniu dopuszczalnego okresu bezczynności.",
            {
                "idle_threshold_seconds": _IDLE_REACTIVATION_SECONDS,
                "passive_turns": passive_turns,
                "attempt": idle_reactivation_attempts,
                "max_attempts": max_idle_autoreactivations,
                "triggered_by_actionable_plan": actionable_plan,
                "tasks_total": plan_snapshot.get("tasks_total", 0),
                "tasks_done": plan_snapshot.get("tasks_done", 0),
            },
        )

        answer = chat_service.ask(idle_prompt)
        answer = apply_supervisor(idle_prompt, answer, stage="idle_reactivation")
        answer = enforce_actionable_autonomy(last_user_message or "kontynuuj", answer)
        if _has_supported_tool_call(answer):
            passive_turns = 0
        else:
            passive_turns += 1
        answer = resolve_tool_calls(answer)
        answer = ensure_plan_persisted(last_user_message or "kontynuuj", answer)
        print(f"\nModel> {answer}")
        log_action(
            "idle.reactivation.done",
            "Zakończono cykl auto-reaktywacji po bezczynności.",
            {"answer_chars": len(answer)},
        )
        last_idle_reactivation_monotonic = time.monotonic()

    def enforce_actionable_autonomy(user_message: str, model_answer: str) -> str:
        should_enforce = bool(autonomy_pattern.search(user_message) or action_pattern.search(user_message))
        if not should_enforce:
            return model_answer
        if _has_only_supported_tool_calls(model_answer):
            return model_answer

        plan_snapshot = _read_plan_tracking_snapshot(work_dir)
        if not bool(plan_snapshot.get("exists")):
            corrective_prompt = _build_plan_tracking_corrective_prompt(user_message)
            forced_answer = chat_service.ask(corrective_prompt)
            forced_answer = apply_supervisor(corrective_prompt, forced_answer, stage="plan_tracking_init")
            if _has_only_supported_tool_calls(forced_answer):
                return forced_answer

        intro_candidates = [workspace_root / "wprowadzenie.md", work_dir.parent / "wprowadzenie.md"]
        intro_path = next((path for path in intro_candidates if path.exists()), None)
        intro_hint = str(intro_path.resolve()) if intro_path is not None else "wprowadzenie.md"

        forced_answer = model_answer
        for _ in range(2):
            corrective_prompt = _build_no_action_corrective_prompt(user_message, intro_hint)
            forced_answer = chat_service.ask(corrective_prompt)
            forced_answer = apply_supervisor(corrective_prompt, forced_answer, stage="no_action_corrective")
            if _has_only_supported_tool_calls(forced_answer):
                return forced_answer
        return forced_answer

    try:
        shell_policy = load_shell_policy(shell_policy_path)
    except Exception as error:
        shell_policy = default_shell_policy()
        print(
            "Uwaga: nie udało się wczytać polityki shell "
            f"z {shell_policy_path}: {error}. Używam polityki domyślnej."
        )

    print("amiagi CLI")
    print("Inicjalizacja kontekstu modelu...")
    log_action(
        "session.start",
        "Rozpoczęcie sesji CLI i przygotowanie kontekstu ciągłości.",
        {
            "shell_policy_path": str(shell_policy_path),
            "work_dir": str(work_dir),
            "autonomous_mode": autonomous_mode,
        },
    )

    try:
        readiness = chat_service.bootstrap_runtime_readiness()
        print("\n--- MODEL READINESS ---")
        print(readiness)
        print("\nModel gotowy. Wpisz /help, aby zobaczyć komendy.")
        log_action(
            "session.readiness",
            "Model potwierdził gotowość po automatycznym bootstrapie.",
            {"chars": len(readiness)},
        )
    except Exception as error:
        print(f"Błąd bootstrapu modelu: {error}")
        print("Przechodzę do trybu interaktywnego bez potwierdzenia gotowości.")
        log_action(
            "session.readiness.error",
            "Nie udało się uzyskać komunikatu gotowości podczas bootstrapu.",
            {"error": str(error)},
        )
        print("Wpisz /help, aby zobaczyć komendy.")

    while True:
        try:
            raw = read_user_input_with_idle("\nTy> ", _IDLE_REACTIVATION_SECONDS)
        except (EOFError, KeyboardInterrupt):
            print("\nZamknięto sesję.")
            log_action("session.interrupt", "Zakończenie sesji przez przerwanie wejścia.")
            break

        if not raw:
            continue

        if pending_goal_candidate is not None:
            if _is_goal_confirmation_message(raw):
                confirmed_goal = pending_goal_candidate
                pending_goal_candidate = None
                plan_path = _upsert_main_plan_goal(work_dir, confirmed_goal)
                print(f"Zarejestrowano główny cel: {confirmed_goal}")
                log_action(
                    "goal.confirmed",
                    "Użytkownik potwierdził główny cel, zapisano plan główny w notatkach.",
                    {"goal": confirmed_goal, "plan_path": str(plan_path)},
                )

                planning_prompt = (
                    "Użytkownik potwierdził główny cel pracy. "
                    "Najpierw zaplanuj realizację krok po kroku i rozpocznij wykonanie przez realny tool_call.\n\n"
                    f"Główny cel: {confirmed_goal}\n"
                    f"Plan bazowy znajduje się w: {_PLAN_TRACKING_RELATIVE_PATH}. "
                    "Aktualizuj current_stage i statusy po każdym potwierdzonym etapie."
                )

                last_user_message = confirmed_goal
                network_resource = _network_resource_for_model(chat_service.ollama_client.base_url)
                network_reason = (
                    "Połączenie z lokalnym API modelu wymaga dostępu do sieci lokalnej."
                    if network_resource == "network.local"
                    else "Połączenie z modelem wymaga dostępu do internetu/sieci zewnętrznej."
                )
                if network_resource == "network.local":
                    if not permission_manager.request_local_network(network_reason):
                        log_action("goal.plan.denied", "Odmowa dostępu do sieci lokalnej podczas planowania celu.")
                        continue
                else:
                    if not permission_manager.request_internet(network_reason):
                        log_action("goal.plan.denied", "Odmowa dostępu do sieci zewnętrznej podczas planowania celu.")
                        continue

                answer = chat_service.ask(planning_prompt)
                answer = apply_supervisor(planning_prompt, answer, stage="goal_planning")
                answer = enforce_actionable_autonomy(confirmed_goal, answer)
                if _has_supported_tool_call(answer):
                    passive_turns = 0
                else:
                    passive_turns += 1
                answer = resolve_tool_calls(answer)
                answer = ensure_plan_persisted(confirmed_goal, answer)
                user_turns_without_plan_update = 0
                print(f"\nModel> {answer}")
                log_action(
                    "goal.plan.done",
                    "Uruchomiono planowanie i realizację po potwierdzeniu głównego celu.",
                    {"goal": confirmed_goal, "answer_chars": len(answer)},
                )
                continue

            if _is_goal_rejection_message(raw):
                pending_goal_candidate = None
                print("Anulowano kandydat celu. Podaj nowy cel frazą typu: 'Twoim celem jest ...'.")
                log_action("goal.rejected", "Użytkownik odrzucił parafrazę celu.")
                continue

            replacement_candidate = _extract_goal_candidate_from_message(raw)
            if replacement_candidate is not None:
                pending_goal_candidate = replacement_candidate
                print(
                    "Czy dobrze rozumiem, że moim głównym celem jest: "
                    f"{pending_goal_candidate}? Odpowiedz 'tak' lub 'nie'."
                )
                log_action(
                    "goal.candidate.updated",
                    "Zaktualizowano kandydata celu na podstawie doprecyzowania użytkownika.",
                    {"goal_candidate": pending_goal_candidate},
                )
                continue

            print("Oczekuję potwierdzenia celu: odpowiedz 'tak' lub 'nie'.")
            continue

        detected_goal_candidate = _extract_goal_candidate_from_message(raw)
        if detected_goal_candidate is not None:
            pending_goal_candidate = detected_goal_candidate
            print(
                "Czy dobrze rozumiem, że moim głównym celem jest: "
                f"{pending_goal_candidate}? Odpowiedz 'tak' lub 'nie'."
            )
            log_action(
                "goal.candidate.detected",
                "Wykryto kandydat głównego celu i poproszono o potwierdzenie parafrazy.",
                {"goal_candidate": pending_goal_candidate},
            )
            continue

        if raw == "/bye":
            log_action(
                "session.bye.request",
                "Zakończenie sesji z podsumowaniem i zapisem punktu startowego.",
            )
            network_resource = _network_resource_for_model(
                chat_service.ollama_client.base_url
            )
            network_reason = (
                "Podsumowanie sesji wymaga wywołania lokalnego modelu."
                if network_resource == "network.local"
                else "Podsumowanie sesji wymaga dostępu do modelu przez sieć zewnętrzną."
            )
            if network_resource == "network.local":
                granted = permission_manager.request_local_network(network_reason)
            else:
                granted = permission_manager.request_internet(network_reason)

            if not granted:
                log_action(
                    "session.bye.denied",
                    "Użytkownik odmówił zasobu do utworzenia podsumowania sesji.",
                )
                continue
            summary = chat_service.summarize_session_for_restart()
            print("Zapisano podsumowanie sesji do kontynuacji po restarcie.")
            print("\n--- START POINT ---")
            print(summary)
            print("\nDo zobaczenia.")
            log_action(
                "session.bye.done",
                "Sesja zakończona po zapisaniu podsumowania startowego.",
                {"summary_chars": len(summary)},
            )
            break

        if raw == "/exit":
            print("Do zobaczenia.")
            log_action("session.exit", "Zakończenie sesji bez tworzenia podsumowania.")
            break

        if raw == "/help":
            print(HELP_TEXT)
            log_action("help.show", "Wyświetlenie listy dostępnych komend.")
            continue

        if raw == "/queue-status":
            policy = chat_service.ollama_client.queue_policy
            vram_advisor = chat_service.ollama_client.vram_advisor
            if policy is None:
                print("Polityka kolejki modeli jest wyłączona.")
                log_action("queue.status", "Wyświetlenie statusu kolejki modeli (wyłączona).")
                continue

            snapshot = policy.snapshot()
            print("\n--- MODEL QUEUE STATUS ---")
            print(f"queue_length: {snapshot.get('queue_length', 0)}")
            print(f"queue: {snapshot.get('queue', [])}")
            print(f"queue_max_wait_seconds: {snapshot.get('queue_max_wait_seconds')}")
            print(f"supervisor_min_free_vram_mb: {snapshot.get('supervisor_min_free_vram_mb')}")

            if vram_advisor is not None:
                profile = vram_advisor.detect()
                print(
                    "vram: "
                    f"free_mb={profile.free_mb}, total_mb={profile.total_mb}, "
                    f"suggested_num_ctx={profile.suggested_num_ctx}"
                )
            else:
                print("vram: brak aktywnego doradcy VRAM")

            recent = snapshot.get("recent_decisions", [])
            print("recent_decisions:")
            if isinstance(recent, list) and recent:
                for item in recent[-10:]:
                    print(f"- {item}")
            else:
                print("- brak")

            log_action(
                "queue.status",
                "Wyświetlenie statusu kolejki modeli i ostatnich decyzji polityki.",
                {
                    "queue_length": snapshot.get("queue_length", 0),
                    "recent_decisions": len(recent) if isinstance(recent, list) else 0,
                },
            )
            continue

        if raw.startswith("/capabilities"):
            check_network = "--network" in raw.split()
            capabilities = collect_capabilities(check_network=check_network)
            print("\n--- CAPABILITIES ---")
            print(json.dumps(capabilities, ensure_ascii=False, indent=2))
            log_action(
                "capabilities.show",
                "Wyświetlenie gotowości narzędzi i backendów runtime.",
                {"check_network": check_network},
            )
            continue

        if raw.startswith("/show-system-context"):
            parts = raw.split(maxsplit=1)
            sample_message = parts[1].strip() if len(parts) == 2 else "kontekst diagnostyczny"
            prompt = chat_service.build_system_prompt(sample_message)
            print("\n--- SYSTEM CONTEXT ---")
            print(prompt)
            log_action(
                "context.show",
                "Wyświetlenie kontekstu systemowego przekazywanego do modelu.",
                {"sample_message": sample_message},
            )
            continue

        if raw in {"/goal-status", "/goal"}:
            snapshot = _read_plan_tracking_snapshot(work_dir)
            repair_info: dict | None = None
            if snapshot.get("parse_error"):
                repair_info = _repair_plan_tracking_file(work_dir)
                snapshot = _read_plan_tracking_snapshot(work_dir)
            print("\n--- GOAL STATUS ---")
            print(f"path: {snapshot.get('path')}")
            print(f"exists: {snapshot.get('exists')}")
            print(f"goal: {snapshot.get('goal', '')}")
            print(f"current_stage: {snapshot.get('current_stage', '')}")
            print(
                "tasks: "
                f"{snapshot.get('tasks_done', 0)}/{snapshot.get('tasks_total', 0)} zakończonych"
            )
            if snapshot.get("parse_error"):
                print("parse_error: true")
            if repair_info and repair_info.get("repaired"):
                print("auto_repair: true")
                if repair_info.get("backup_path"):
                    print(f"backup_path: {repair_info.get('backup_path')}")
            log_action(
                "goal.status",
                "Wyświetlono status głównego celu i etapu realizacji.",
                {
                    "exists": snapshot.get("exists"),
                    "goal": snapshot.get("goal", ""),
                    "current_stage": snapshot.get("current_stage", ""),
                    "tasks_total": snapshot.get("tasks_total", 0),
                    "tasks_done": snapshot.get("tasks_done", 0),
                    "auto_repaired": bool(repair_info and repair_info.get("repaired")),
                },
            )
            continue

        for match in file_ref_pattern.finditer(raw):
            token = match.group("path")
            candidate = resolve_workspace_path(Path(token))
            if candidate.exists() and candidate.is_file():
                last_referenced_file = candidate

        directive = parse_framework_directive(raw)
        if directive is None and read_this_file_pattern.search(raw) and last_referenced_file is not None:
            directive = FrameworkDirective(action="read_file", path=last_referenced_file)

        if directive is not None:
            resolved_directive_path = resolve_workspace_path(directive.path)
            if directive.action == "read_file":
                log_action(
                    "framework.read_file.request",
                    "Wykonanie dyrektywy frameworka: odczyt zawartości pliku.",
                    {"path": str(resolved_directive_path)},
                )
                if not permission_manager.request_disk_read(
                    "Odczyt zawartości pliku wymaga dostępu do dysku.",
                ):
                    log_action(
                        "framework.read_file.denied",
                        "Użytkownik odmówił odczytu pliku.",
                        {"path": str(directive.path)},
                    )
                    continue

                if not resolved_directive_path.exists() or not resolved_directive_path.is_file():
                    print(f"Nie znaleziono pliku: {resolved_directive_path}")
                    log_action(
                        "framework.read_file.missing",
                        "Wskazany plik nie istnieje lub nie jest plikiem regularnym.",
                        {"path": str(resolved_directive_path)},
                    )
                    continue

                try:
                    content = resolved_directive_path.read_text(encoding="utf-8")
                except Exception as error:
                    print(f"Błąd odczytu pliku: {error}")
                    log_action(
                        "framework.read_file.error",
                        "Błąd podczas odczytu pliku.",
                        {"path": str(resolved_directive_path), "error": str(error)},
                    )
                    continue

                max_chars = 12000
                if len(content) > max_chars:
                    content_to_show = content[:max_chars] + "\n\n[TRUNCATED]"
                else:
                    content_to_show = content
                print("\n--- FILE CONTENT ---")
                print(content_to_show)
                log_action(
                    "framework.read_file.done",
                    "Zwrócono użytkownikowi zawartość pliku.",
                    {
                        "path": str(resolved_directive_path),
                        "chars": len(content),
                        "truncated": len(content) > max_chars,
                    },
                )
                continue

        if raw.startswith("/import-dialog"):
            log_action("import_dialog.request", "Import treści dyskusji bez kodu do pamięci.")
            if not permission_manager.request_disk_read(
                "Import dialogu wymaga odczytu pliku z dysku.",
            ):
                log_action("import_dialog.denied", "Odmowa odczytu pliku przez użytkownika.")
                continue

            parts = raw.split(maxsplit=1)
            path = Path(parts[1].strip()) if len(parts) == 2 else Path("początkowe_konsultacje.md")
            if not path.exists():
                print(f"Nie znaleziono pliku: {path}")
                log_action("import_dialog.missing", "Nie znaleziono wskazanego pliku.", {"path": str(path)})
                continue

            text = path.read_text(encoding="utf-8")
            discussion = extract_dialogue_without_code(text)
            chat_service.save_discussion_context(discussion)
            print("Zapisano treść dialogu (bez kodu) do pamięci.")
            log_action("import_dialog.done", "Zapisano kontekst dyskusji do pamięci.", {"path": str(path)})
            continue

        if raw.startswith("/create-python"):
            log_action("create_python.request", "Generowanie i zapis kodu Python.")
            parts = raw.split(maxsplit=2)
            if len(parts) < 3:
                print("Użycie: /create-python <plik> <opis>")
                log_action("create_python.invalid", "Niepoprawne użycie komendy create-python.")
                continue

            output_path = Path(parts[1].strip())
            description = parts[2].strip()

            network_resource = _network_resource_for_model(
                chat_service.ollama_client.base_url
            )
            network_reason = (
                "Połączenie z lokalnym API modelu wymaga dostępu do sieci lokalnej."
                if network_resource == "network.local"
                else "Połączenie z modelem wymaga dostępu do internetu/sieci zewnętrznej."
            )
            if network_resource == "network.local":
                if not permission_manager.request_local_network(network_reason):
                    log_action("create_python.denied", "Odmowa dostępu do sieci lokalnej.")
                    continue
            else:
                if not permission_manager.request_internet(network_reason):
                    log_action("create_python.denied", "Odmowa dostępu do sieci zewnętrznej.")
                    continue

            if not permission_manager.request_disk_write(
                "Zapis wygenerowanego skryptu wymaga zapisu na dysku.",
            ):
                log_action("create_python.denied", "Odmowa zapisu pliku skryptu.")
                continue

            output_path.parent.mkdir(parents=True, exist_ok=True)
            code = chat_service.generate_python_code(description)
            output_path.write_text(code + "\n", encoding="utf-8")
            print(f"Zapisano skrypt: {output_path}")
            log_action(
                "create_python.done",
                "Wygenerowano i zapisano skrypt Python.",
                {"path": str(output_path), "chars": len(code)},
            )
            continue

        if raw.startswith("/run-python"):
            log_action("run_python.request", "Uruchomienie skryptu Python.")
            parts = shlex.split(raw)
            if len(parts) < 2:
                print("Użycie: /run-python <plik> [arg ...]")
                log_action("run_python.invalid", "Niepoprawne użycie komendy run-python.")
                continue

            script_path = Path(parts[1])
            script_args = parts[2:]

            if not permission_manager.request_disk_read(
                "Uruchomienie skryptu wymaga odczytu pliku z dysku.",
            ):
                log_action("run_python.denied", "Odmowa odczytu pliku skryptu.")
                continue
            if not permission_manager.request_process_exec(
                "Uruchomienie skryptu wymaga wykonania procesu systemowego.",
            ):
                log_action("run_python.denied", "Odmowa wykonania procesu systemowego.")
                continue

            if not script_path.exists():
                print(f"Nie znaleziono skryptu: {script_path}")
                log_action("run_python.missing", "Nie znaleziono wskazanego skryptu.", {"path": str(script_path)})
                continue

            try:
                result = script_executor.execute_python(script_path, script_args)
            except Exception as error:
                print(f"Błąd uruchomienia: {error}")
                log_action("run_python.error", "Błąd podczas uruchomienia skryptu Python.", {"error": str(error)})
                continue

            print(f"Polecenie: {' '.join(result.command)}")
            print(f"Kod wyjścia: {result.exit_code}")
            if result.stdout.strip():
                print("\n--- STDOUT ---")
                print(result.stdout)
            if result.stderr.strip():
                print("\n--- STDERR ---")
                print(result.stderr)
            log_action(
                "run_python.done",
                "Zakończono wykonanie skryptu Python.",
                {"path": str(script_path), "exit_code": result.exit_code},
            )
            continue

        if raw.startswith("/run-shell"):
            log_action("run_shell.request", "Uruchomienie polecenia shell z polityką whitelist.")
            parts = raw.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                print("Użycie: /run-shell <polecenie>")
                log_action("run_shell.invalid", "Niepoprawne użycie komendy run-shell.")
                continue

            command_text = parts[1].strip()
            _, validation_error = parse_and_validate_shell_command(
                command_text,
                shell_policy,
            )
            if validation_error is not None:
                print(f"Odrzucono polecenie: {validation_error}")
                log_action("run_shell.rejected", "Odrzucono polecenie shell przez politykę.", {"error": validation_error})
                continue

            if not permission_manager.request_process_exec(
                "Uruchomienie polecenia shell wymaga wykonania procesu systemowego.",
            ):
                log_action("run_shell.denied", "Odmowa wykonania procesu shell.")
                continue

            try:
                result = script_executor.execute_shell(command_text)
            except Exception as error:
                print(f"Błąd uruchomienia: {error}")
                log_action("run_shell.error", "Błąd wykonania polecenia shell.", {"error": str(error)})
                continue

            print(f"Polecenie: {' '.join(result.command)}")
            print(f"Kod wyjścia: {result.exit_code}")
            if result.stdout.strip():
                print("\n--- STDOUT ---")
                print(result.stdout)
            if result.stderr.strip():
                print("\n--- STDERR ---")
                print(result.stderr)
            log_action(
                "run_shell.done",
                "Zakończono wykonanie polecenia shell.",
                {"command": command_text, "exit_code": result.exit_code},
            )
            continue

        if raw.startswith("/history"):
            log_action("history.show", "Odczyt historii wiadomości z pamięci.")
            parts = raw.split(maxsplit=1)
            limit = 10
            if len(parts) == 2 and parts[1].isdigit():
                limit = max(1, min(200, int(parts[1])))
            messages = chat_service.memory_repository.recent_messages(limit=limit)
            if not messages:
                print("Brak historii.")
                continue
            for message in messages:
                print(
                    f"[{message.created_at.isoformat(timespec='seconds')}] "
                    f"{message.role}: {message.content}"
                )
            continue

        if raw.startswith("/remember"):
            log_action("remember.request", "Zapis notatki użytkownika.")
            parts = raw.split(maxsplit=1)
            if len(parts) < 2:
                print("Użycie: /remember <tekst>")
                log_action("remember.invalid", "Niepoprawne użycie komendy remember.")
                continue
            chat_service.remember(parts[1].strip())
            print("Zapisano notatkę.")
            continue

        if raw.startswith("/memories"):
            log_action("memories.search", "Przegląd zawartości pamięci.")
            parts = raw.split(maxsplit=1)
            query = parts[1].strip() if len(parts) == 2 else None
            records = chat_service.memory_repository.search_memories(query=query, limit=20)
            if not records:
                print("Brak wyników.")
                continue
            for record in records:
                print(
                    f"[{record.created_at.isoformat(timespec='seconds')}] "
                    f"{record.kind}/{record.source}: {record.content}"
                )
            continue

        try:
            log_action("chat.message", "Przetwarzanie standardowej wiadomości użytkownika.")
            last_user_message = raw
            idle_reactivation_attempts = 0
            idle_reactivation_capped_notified = False

            network_resource = _network_resource_for_model(
                chat_service.ollama_client.base_url
            )
            network_reason = (
                "Połączenie z lokalnym API modelu wymaga dostępu do sieci lokalnej."
                if network_resource == "network.local"
                else "Połączenie z modelem wymaga dostępu do internetu/sieci zewnętrznej."
            )
            if network_resource == "network.local":
                if not permission_manager.request_local_network(network_reason):
                    log_action("chat.denied", "Odmowa dostępu do sieci lokalnej.")
                    continue
            else:
                if not permission_manager.request_internet(network_reason):
                    log_action("chat.denied", "Odmowa dostępu do sieci zewnętrznej.")
                    continue
            plan_fingerprint_before = _main_plan_fingerprint(work_dir)

            answer = chat_service.ask(raw)
            answer = apply_supervisor(raw, answer, stage="user_turn")
            answer = enforce_actionable_autonomy(raw, answer)
            if _has_supported_tool_call(answer):
                passive_turns = 0
            else:
                passive_turns += 1
            answer = resolve_tool_calls(answer)
            answer = ensure_plan_persisted(raw, answer)

            plan_fingerprint_after = _main_plan_fingerprint(work_dir)
            if plan_fingerprint_after and plan_fingerprint_after != plan_fingerprint_before:
                user_turns_without_plan_update = 0
            else:
                snapshot = _read_plan_tracking_snapshot(work_dir)
                if _has_actionable_main_plan(snapshot) and last_work_state in _REACTIVATION_ALLOWED_STATES:
                    user_turns_without_plan_update += 1
                else:
                    user_turns_without_plan_update = 0

            if user_turns_without_plan_update >= _MAX_USER_TURNS_WITHOUT_PLAN_UPDATE:
                log_action(
                    "plan.progress.stale.detected",
                    "Wykryto brak aktualizacji planu przez kolejne tury; uruchomiono twardą korektę.",
                    {
                        "turns_without_update": user_turns_without_plan_update,
                        "threshold": _MAX_USER_TURNS_WITHOUT_PLAN_UPDATE,
                    },
                )
                answer = ensure_plan_progress_updated(raw, answer)
                refreshed_fingerprint = _main_plan_fingerprint(work_dir)
                if refreshed_fingerprint and refreshed_fingerprint != plan_fingerprint_after:
                    user_turns_without_plan_update = 0

            print(f"\nModel> {answer}")
            log_action("chat.response", "Zwrócono odpowiedź modelu użytkownikowi.", {"chars": len(answer)})
        except Exception as error:
            print(f"Błąd: {error}")
            log_action("chat.error", "Błąd podczas obsługi wiadomości użytkownika.", {"error": str(error)})
