#!/usr/bin/env python3
"""One-time migration script: replace hardcoded Polish strings with _() calls.

Run from project root:
    python scripts/migrate_i18n.py

Creates backup (.bak) of each modified file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IFACE = ROOT / "src" / "amiagi" / "interfaces"
SRC = ROOT / "src" / "amiagi"

# ── helpers ─────────────────────────────────────────────────────────

def _add_import(text: str, import_line: str = "from amiagi.i18n import _") -> str:
    """Insert `from amiagi.i18n import _` right after the last existing import block."""
    if import_line in text:
        return text
    # Find the last "from ... import ..." or "import ..." line
    lines = text.split("\n")
    last_import_idx = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("from ") or stripped.startswith("import "):
            last_import_idx = i
        # Skip continuation lines inside import blocks
        if stripped.startswith(")"):
            last_import_idx = i
    # Insert after last import
    lines.insert(last_import_idx + 1, import_line)
    return "\n".join(lines)


def _do_replacements(text: str, replacements: list[tuple[str, str]]) -> tuple[str, int]:
    """Apply a list of (old, new) exact-string replacements. Returns (new_text, count)."""
    count = 0
    for old, new in replacements:
        if old in text:
            text = text.replace(old, new, 1)  # replace first occurrence only (safer)
            count += 1
    return text, count


def _do_all_replacements(text: str, replacements: list[tuple[str, str]]) -> tuple[str, int]:
    """Apply replacements, replacing ALL occurrences."""
    count = 0
    for old, new in replacements:
        n = text.count(old)
        if n > 0:
            text = text.replace(old, new)
            count += n
    return text, count


# ── TEXTUAL_CLI REPLACEMENTS ───────────────────────────────────────

TEXTUAL_HELP_REPLACEMENTS = [
    # Help command descriptions — second element of tuples
    ('("/help", "pokaż dostępne komendy")', '("/help", _("help.cmd.help"))'),
    ('("/cls", "wyczyść ekran główny (panel użytkownika)")', '("/cls", _("help.cmd.cls"))'),
    ('("/cls all", "wyczyść wszystkie panele")', '("/cls all", _("help.cmd.cls_all"))'),
    ('("/models current", "pokaż aktualnie aktywny model dla Polluksa")', '("/models current", _("help.cmd.models_current"))'),
    ('("/models show", "pokaż modele dostępne w Ollama (1..x)")', '("/models show", _("help.cmd.models_show"))'),
    ('("/models chose <nr>", "wybierz model dla Polluksa po numerze z /models show")', '("/models chose <nr>", _("help.cmd.models_chose"))'),
    ('("/kastor-model show", "pokaż aktualny model Kastora")', '("/kastor-model show", _("help.cmd.kastor_model_show"))'),
    ('("/kastor-model chose <nr>", "zmień model Kastora na wybrany z listy")', '("/kastor-model chose <nr>", _("help.cmd.kastor_model_chose"))'),
    ('("/permissions", "pokaż aktualny tryb zgód")', '("/permissions", _("help.cmd.permissions"))'),
    ('("/permissions all", "włącz globalną zgodę na zasoby")', '("/permissions all", _("help.cmd.permissions_all"))'),
    ('("/permissions ask", "wyłącz globalną zgodę")', '("/permissions ask", _("help.cmd.permissions_ask"))'),
    ('("/permissions reset", "wyczyść zapamiętane zgody per zasób")', '("/permissions reset", _("help.cmd.permissions_reset"))'),
    ('("/queue-status", "pokaż stan kolejki modeli i decyzji polityki VRAM")', '("/queue-status", _("help.cmd.queue_status"))'),
    ('("/capabilities [--network]", "pokaż gotowość narzędzi i backendów")', '("/capabilities [--network]", _("help.cmd.capabilities"))'),
    ('("/show-system-context [tekst]", "pokaż kontekst systemowy przekazywany do modelu")', '("/show-system-context [tekst]", _("help.cmd.show_system_context"))'),
    ('("/goal-status", "pokaż cel główny i etap z notes/main_plan.json")', '("/goal-status", _("help.cmd.goal_status"))'),
    ('("/goal", "alias: pokaż cel główny i etap")', '("/goal", _("help.cmd.goal"))'),
    ('("/router-status", "pokaż status aktorów i okna IDLE")', '("/router-status", _("help.cmd.router_status"))'),
    ('("/idle-until <ISO8601|off>", "ustaw/wyczyść planowane IDLE watchdoga")', '("/idle-until <ISO8601|off>", _("help.cmd.idle_until"))'),
    ('("/history [n]", "pokaż ostatnie wiadomości (domyślnie 10)")', '("/history [n]", _("help.cmd.history"))'),
    ('("/remember <tekst>", "zapisz notatkę do pamięci")', '("/remember <tekst>", _("help.cmd.remember"))'),
    ('("/memories [zapytanie]", "przeszukaj pamięć")', '("/memories [zapytanie]", _("help.cmd.memories"))'),
    ('("/import-dialog [plik]", "zapisz dialog (bez kodu) jako kontekst pamięci")', '("/import-dialog [plik]", _("help.cmd.import_dialog"))'),
    ('("/create-python <plik> <opis>", "wygeneruj i zapisz skrypt Python przez model")', '("/create-python <plik> <opis>", _("help.cmd.create_python"))'),
    ('("/run-python <plik> [arg ...]", "uruchom skrypt Python z argumentami")', '("/run-python <plik> [arg ...]", _("help.cmd.run_python"))'),
    ('("/run-shell <polecenie>", "uruchom polecenie shell z polityką whitelist")', '("/run-shell <polecenie>", _("help.cmd.run_shell"))'),
    ('("/api-usage", "pokaż szczegółowe zużycie tokenów i koszty API")', '("/api-usage", _("help.cmd.api_usage"))'),
    ('("/api-key verify", "zweryfikuj ponownie klucz API")', '("/api-key verify", _("help.cmd.api_key_verify"))'),
    ('("/skills", "lista załadowanych skills per rola")', '("/skills", _("help.cmd.skills"))'),
    ('("/skills reload", "przeładuj pliki skills z dysku")', '("/skills reload", _("help.cmd.skills_reload"))'),
    ('("/agents list", "lista agentów, stan, model, rola")', '("/agents list", _("help.cmd.agents_list"))'),
    ('("/agents info <id|name>", "szczegóły agenta")', '("/agents info <id|name>", _("help.cmd.agents_info"))'),
    ('("/agents pause <id>", "wstrzymaj agenta")', '("/agents pause <id>", _("help.cmd.agents_pause"))'),
    ('("/agents resume <id>", "wznów agenta")', '("/agents resume <id>", _("help.cmd.agents_resume"))'),
    ('("/agents terminate <id>", "zakończ agenta")', '("/agents terminate <id>", _("help.cmd.agents_terminate"))'),
    ('("/agent-wizard create <opis>", "utwórz agenta na podstawie opisu")', '("/agent-wizard create <opis>", _("help.cmd.agent_wizard_create"))'),
    ('("/agent-wizard blueprints", "lista zapisanych blueprintów")', '("/agent-wizard blueprints", _("help.cmd.agent_wizard_blueprints"))'),
    ('("/agent-wizard load <nazwa>", "załaduj agenta z blueprintu")', '("/agent-wizard load <nazwa>", _("help.cmd.agent_wizard_load"))'),
    ('("/tasks list", "lista zadań w kolejce")', '("/tasks list", _("help.cmd.tasks_list"))'),
    ('("/tasks add <opis>", "dodaj nowe zadanie")', '("/tasks add <opis>", _("help.cmd.tasks_add"))'),
    ('("/tasks info <id>", "szczegóły zadania")', '("/tasks info <id>", _("help.cmd.tasks_info"))'),
    ('("/tasks cancel <id>", "anuluj zadanie")', '("/tasks cancel <id>", _("help.cmd.tasks_cancel"))'),
    ('("/tasks stats", "statystyki kolejki zadań")', '("/tasks stats", _("help.cmd.tasks_stats"))'),
    ('("/dashboard start [--port N]", "uruchom web dashboard (domyślnie :8080)")', '("/dashboard start [--port N]", _("help.cmd.dashboard_start"))'),
    ('("/dashboard stop", "zatrzymaj web dashboard")', '("/dashboard stop", _("help.cmd.dashboard_stop"))'),
    ('("/dashboard status", "pokaż status web dashboard")', '("/dashboard status", _("help.cmd.dashboard_status"))'),
    ('("/knowledge store <tekst>", "dodaj wpis do bazy wiedzy")', '("/knowledge store <tekst>", _("help.cmd.knowledge_store"))'),
    ('("/knowledge query <pytanie>", "przeszukaj bazę wiedzy")', '("/knowledge query <pytanie>", _("help.cmd.knowledge_query"))'),
    ('("/knowledge count", "liczba wpisów w bazie wiedzy")', '("/knowledge count", _("help.cmd.knowledge_count"))'),
    ('("/workspace list", "lista plików we współdzielonym workspace")', '("/workspace list", _("help.cmd.workspace_list"))'),
    ('("/workspace read <plik>", "odczytaj plik z workspace")', '("/workspace read <plik>", _("help.cmd.workspace_read"))'),
    ('("/workspace write <plik> <treść>", "zapisz plik do workspace")', '("/workspace write <plik> <treść>", _("help.cmd.workspace_write"))'),
    ('("/workflow list", "lista dostępnych szablonów workflow")', '("/workflow list", _("help.cmd.workflow_list"))'),
    ('("/workflow run <nazwa>", "uruchom workflow z szablonu")', '("/workflow run <nazwa>", _("help.cmd.workflow_run"))'),
    ('("/workflow status", "status aktywnego workflow")', '("/workflow status", _("help.cmd.workflow_status"))'),
    ('("/workflow pause", "wstrzymaj aktywny workflow")', '("/workflow pause", _("help.cmd.workflow_pause"))'),
    ('("/workflow resume", "wznów workflow")', '("/workflow resume", _("help.cmd.workflow_resume"))'),
    ('("/audit query [agent]", "przeszukaj łańcuch audytu")', '("/audit query [agent]", _("help.cmd.audit_query"))'),
    ('("/audit last [n]", "ostatnie wpisy audytu")', '("/audit last [n]", _("help.cmd.audit_last"))'),
    ('("/sandbox list", "lista sandboxów agentów")', '("/sandbox list", _("help.cmd.sandbox_list"))'),
    ('("/sandbox create <agent>", "utwórz sandbox dla agenta")', '("/sandbox create <agent>", _("help.cmd.sandbox_create"))'),
    ('("/sandbox destroy <agent>", "usuń sandbox agenta")', '("/sandbox destroy", _("help.cmd.sandbox_destroy"))'),
    ('("/budget status", "pokaż budżety agentów")', '("/budget status", _("help.cmd.budget_status"))'),
    ('("/budget set <agent> <limit>", "ustaw budżet agenta (USD)")', '("/budget set <agent> <limit>", _("help.cmd.budget_set"))'),
    ('("/budget reset <agent>", "resetuj wydatki agenta")', '("/budget reset <agent>", _("help.cmd.budget_reset"))'),
    ('("/quota status", "pokaż politykę quotas per rola")', '("/quota status", _("help.cmd.quota_status"))'),
    ('("/quota set <rola> <tokens> <cost> <req/h>", "ustaw quota dla roli")', '("/quota set <rola> <tokens> <cost> <req/h>", _("help.cmd.quota_set"))'),
    ('("/eval run <agent> [--benchmark X]", "uruchom ewaluację agenta")', '("/eval run <agent> [--benchmark X]", _("help.cmd.eval_run"))'),
    ('("/eval compare <agent_a> <agent_b>", "porównanie A/B dwóch agentów")', '("/eval compare <agent_a> <agent_b>", _("help.cmd.eval_compare"))'),
    ('("/eval history [agent]", "historia wyników ewaluacji")', '("/eval history [agent]", _("help.cmd.eval_history"))'),
    ('("/eval baselines", "lista zapisanych baselines")', '("/eval baselines", _("help.cmd.eval_baselines"))'),
    ('("/feedback summary", "podsumowanie opinii o agentach")', '("/feedback summary", _("help.cmd.feedback_summary"))'),
    ('("/feedback up <agent> [komentarz]", "pozytywna ocena agenta")', '("/feedback up <agent> [komentarz]", _("help.cmd.feedback_up"))'),
    ('("/feedback down <agent> [komentarz]", "negatywna ocena agenta")', '("/feedback down <agent> [komentarz]", _("help.cmd.feedback_down"))'),
    ('("/api start", "uruchom REST API (domyślnie :8090)")', '("/api start", _("help.cmd.api_start"))'),
    ('("/api stop", "zatrzymaj REST API")', '("/api stop", _("help.cmd.api_stop"))'),
    ('("/api status", "status REST API")', '("/api status", _("help.cmd.api_status"))'),
    ('("/plugins list", "lista załadowanych pluginów")', '("/plugins list", _("help.cmd.plugins_list"))'),
    ('("/plugins load", "załaduj wszystkie pluginy")', '("/plugins load", _("help.cmd.plugins_load"))'),
    ('("/plugins install <path>", "zainstaluj plugin ze ścieżki")', '("/plugins install <path>", _("help.cmd.plugins_install"))'),
    ('("/team list", "lista zarejestrowanych zespołów")', '("/team list", _("help.cmd.team_list"))'),
    ('("/team templates", "dostępne szablony zespołów")', '("/team templates", _("help.cmd.team_templates"))'),
    ('("/team create <szablon>", "utwórz zespół z szablonu")', '("/team create <szablon>", _("help.cmd.team_create"))'),
    ('("/team compose <cel>", "skomponuj zespół na podstawie celu")', '("/team compose <cel>", _("help.cmd.team_compose"))'),
    ('("/team status <id>", "org chart i status zespołu")', '("/team status <id>", _("help.cmd.team_status"))'),
    ('("/team scale <id> up|down", "skaluj zespół w górę/w dół")', '("/team scale <id> up|down", _("help.cmd.team_scale"))'),
    ('("/bye", "zapisz podsumowanie sesji i zakończ")', '("/bye", _("help.cmd.bye"))'),
    ('("/quit", "zakończ tryb textual")', '("/quit", _("help.cmd.quit"))'),
    ('("/exit", "zakończ tryb textual")', '("/exit", _("help.cmd.exit"))'),
]

TEXTUAL_MISC_REPLACEMENTS = [
    # _build_textual_help_text header
    ('lines = ["Komendy (textual):"]', 'lines = [_("help.header.textual")]'),
    
    # Import guard
    ('"Tryb textual wymaga biblioteki \'textual\'. Zainstaluj zależności runtime."',
     '_("error.textual_import")'),
    
    # Clipboard
    ('"Brak treści do skopiowania."', '_("clipboard.empty")'),
    ('"Brak narzędzia wl-copy (Wayland)."', '_("clipboard.no_wlcopy")'),
    ('"Przekroczono limit czasu kopiowania przez wl-copy."', '_("clipboard.timeout_wlcopy")'),
    ('"Schowek systemowy (Wayland / wl-copy)."', '_("clipboard.ok_wayland")'),
    ('"Nie udało się skopiować przez wl-copy."', '_("clipboard.fail_wlcopy")'),
    ('"Przekroczono limit czasu kopiowania przez xclip."', '_("clipboard.timeout_xclip")'),
    ('"Schowek systemowy (X11 / xclip)."', '_("clipboard.ok_xclip")'),
    ('"Przekroczono limit czasu kopiowania przez xsel."', '_("clipboard.timeout_xsel")'),
    ('"Schowek systemowy (X11 / xsel)."', '_("clipboard.ok_xsel")'),
    ('"Brak narzędzia do schowka X11 (zainstaluj xclip albo xsel)."', '_("clipboard.no_x11_tool")'),
    ('"Nie wykryto środowiska schowka (WAYLAND_DISPLAY/DISPLAY)."', '_("clipboard.no_display")'),
    
    # Bindings
    ('"Kopiuj zaznaczenie"', '_("binding.copy_selection")'),
    ('"Wyjście"', '_("binding.quit")'),
    
    # Permissions
    ('"--- PERMISSIONS ---"', '_("permissions.header")'),
    ('"Włączono globalną zgodę na zasoby."', '_("permissions.global_on")'),
    ('"Włączono tryb pytań o zgodę per zasób."', '_("permissions.ask_on")'),
    ('"W trybie textual zgoda interakcyjna nie jest wyświetlana; użyj /permissions all, aby wysyłać zapytania do modelu."',
     '_("permissions.ask_textual_hint")'),
    ('"Wyczyszczono zapamiętane zgody per zasób."', '_("permissions.reset_done")'),
    ('"Brak zapamiętanych zgód do wyczyszczenia."', '_("permissions.reset_empty")'),
    ('"Użycie: /permissions [status|all|ask|reset]"', '_("permissions.usage")'),
]

# Will be applied to all files processing textual_cli.py
TEXTUAL_WIDGET_REPLACEMENTS = [
    # Widget titles
    ('"Użytkownik ↔ Polluks"', '_("widget.user_model_title")'),
    ('"Status modelu: READY · możesz pisać"', '_("widget.busy_ready")'),
    ('"Status modelu: BUSY · trwa wykonywanie kroku"', '_("widget.busy_working")'),
    ('"Wpisz polecenie i Enter (/quit aby wyjść)"', '_("widget.input_placeholder")'),
    ('"Router"', '_("widget.router_title")'),
    ('"Kastor → Router"', '_("widget.supervisor_title")'),
    ('"Polluks → Kastor"', '_("widget.executor_title")'),
]

TEXTUAL_CLIPBOARD_NOTIFY = [
    # These use f-strings with {details}, need special handling
    ('f"Skopiowano do schowka ({details})."', '_("clipboard.copied_notify", details=details)'),
    ('f"Skopiowano przez tryb terminalowy (OSC52). Szczegóły środowiska: {details}"',
     '_("clipboard.osc52_notify", details=details)'),
    ('"Brak zaznaczonej treści do skopiowania. Kliknij w okno logu i zaznacz tekst."',
     '_("clipboard.no_selection")'),
]

TEXTUAL_MODELS_REPLACEMENTS = [
    # /models
    ('"Użycie: /models show | /models chose <nr>"', '_("models.usage")'),
    ('"--- AKTYWNE MODELE ---"', '_("models.active_header")'),
    ('"(nieaktywny)"', '_("models.kastor_inactive")'),
    ('"(nie ustawiony)"', '_("models.not_set")'),
    ('"Brak modeli dostępnych."', '_("models.none_available")'),
    ('"--- MODELE POLLUKSA ---"', '_("models.header_polluks")'),
    ('"Modele lokalne (Ollama):"', '_("models.local_header")'),
    ('"Modele zewnętrzne (API):"', '_("models.api_header")'),
    ('"  [aktywny]"', '_("models.active_marker")'),
    ('"Użycie: /models chose <nr>"', '_("models.chose_usage")'),
    ('"Nieprawidłowy numer modelu. Użyj wartości całkowitej, np. /models chose 1"',
     '_("models.invalid_number")'),
    ('"Brak klucza API (OPENAI_API_KEY)."', '_("models.no_api_key")'),
    # /kastor-model
    ('"Kastor jest nieaktywny w tej sesji."', '_("kastor.inactive")'),
    ('"Użycie: /kastor-model chose <nr>"', '_("kastor.usage")'),
    ('"Podaj numer modelu."', '_("kastor.give_number")'),
    ('"--- MODELE DLA KASTORA ---"', '_("kastor.models_header")'),
]

TEXTUAL_ROUTER_REPLACEMENTS = [
    ('"--- ROUTER STATUS ---"', '_("router_status.header")'),
    ('"Aktorzy:"', '_("router.actors_header")'),
    ('"brak"', '_("router.idle_until_none")'),
    ('"Ostatnie zdarzenie:"', '_("router.last_event_label")'),
    ('"Uruchomienie sesji"', '_("router.session_start_event")'),
]

TEXTUAL_IDENTITY_REPLACEMENTS = [
    ('"Jestem Polluks, modelem wykonawczym frameworka amiagi."',
     '_("identity.reply")'),
    ('" Czy chcesz, żebym kontynuował plan, przerwał go, czy przygotował nowe zadanie?"',
     '_("identity.followup_question")'),
]

TEXTUAL_CLS_REPLACEMENTS = [
    ('"Wyczyszczono ekran główny."', '_("cls.main_done")'),
    ('"Wyczyszczono wszystkie panele."', '_("cls.all_done")'),
]

TEXTUAL_QUEUE_REPLACEMENTS = [
    ('"Polityka kolejki modeli jest wyłączona."', '_("queue.disabled")'),
    ('"--- MODEL QUEUE STATUS ---"', '_("queue.header")'),
    ('"vram: brak aktywnego doradcy VRAM"', '_("queue.no_vram_advisor")'),
]

TEXTUAL_CAPABILITIES_REPLACEMENTS = [
    ('"--- CAPABILITIES ---"', '_("capabilities.header")'),
]

TEXTUAL_SYSTEM_CONTEXT_REPLACEMENTS = [
    ('"--- SYSTEM CONTEXT ---"', '_("system_context.header")'),
]

TEXTUAL_GOAL_REPLACEMENTS = [
    ('"--- GOAL STATUS ---"', '_("goal.header")'),
]

TEXTUAL_IDLE_REPLACEMENTS = [
    ('"Wyczyszczono zaplanowane okno IDLE."', '_("idle_until.cleared")'),
]

TEXTUAL_IMPORT_DIALOG_REPLACEMENTS = [
    ('"Zapisano treść dialogu (bez kodu) do pamięci."', '_("import_dialog.done")'),
]

TEXTUAL_CREATE_PYTHON_REPLACEMENTS = [
    ('"Użycie: /create-python <plik> <opis>"', '_("create_python.usage")'),
]

TEXTUAL_RUN_PYTHON_REPLACEMENTS = [
    ('"Użycie: /run-python <plik> [arg ...]"', '_("run_python.usage")'),
]

TEXTUAL_RUN_SHELL_REPLACEMENTS = [
    ('"Użycie: /run-shell <polecenie>"', '_("run_shell.usage")'),
]

TEXTUAL_HISTORY_REPLACEMENTS = [
    ('"Brak historii."', '_("history.empty")'),
]

TEXTUAL_REMEMBER_REPLACEMENTS = [
    ('"Użycie: /remember <tekst>"', '_("remember.usage")'),
    ('"Zapisano notatkę."', '_("remember.saved")'),
]

TEXTUAL_MEMORIES_REPLACEMENTS = [
    ('"Brak wyników."', '_("memories.empty")'),
]

TEXTUAL_BYE_REPLACEMENTS = [
    ('"Zapisano podsumowanie sesji do kontynuacji po restarcie."', '_("bye.saved")'),
    ('"--- START POINT ---"', '_("bye.start_point")'),
    ('"Do zobaczenia."', '_("bye.farewell")'),
]

TEXTUAL_API_USAGE_REPLACEMENTS = [
    ('"Brak danych o zużyciu API w tej sesji."', '_("api_usage.empty")'),
    ('"--- API USAGE ---"', '_("api_usage.header")'),
]

TEXTUAL_API_KEY_REPLACEMENTS = [
    ('"Brak klucza API (OPENAI_API_KEY nie ustawiony)."', '_("api_key.missing")'),
]

TEXTUAL_SKILLS_REPLACEMENTS = [
    ('"SkillsLoader nie jest skonfigurowany."', '_("skills.no_loader")'),
    ('"Brak załadowanych skills. Sprawdź katalog skills/."', '_("skills.empty")'),
    ('"--- ZAŁADOWANE SKILLS ---"', '_("skills.header")'),
]

TEXTUAL_AGENTS_REPLACEMENTS = [
    ('"Rejestr agentów nie jest aktywny w tej sesji."', '_("agents.no_registry")'),
    ('"Brak zarejestrowanych agentów."', '_("agents.empty")'),
    ('"--- AGENCI ---"', '_("agents.header")'),
    ('"(brak modelu)"', '_("agents.no_model")'),
    ('"Użycie: /agents info <id|nazwa>"', '_("agents.info_usage")'),
    ('"--- AGENT INFO ---"', '_("agents.info_header")'),
    ('"Użycie: /agents pause <id>"', '_("agents.pause_usage")'),
    ('"Użycie: /agents resume <id>"', '_("agents.resume_usage")'),
    ('"Użycie: /agents terminate <id>"', '_("agents.terminate_usage")'),
    ('"Użycie: /agents list | /agents info <id> | /agents pause <id> | /agents resume <id> | /agents terminate <id>"',
     '_("agents.usage_full")'),
    ('"(brak)"',  '_("agents.info.no_skills")'),
]

TEXTUAL_WIZARD_REPLACEMENTS = [
    ('"Fabryka agentów nie jest aktywna w tej sesji."', '_("wizard.no_factory")'),
    ('"Użycie: /agent-wizard create <opis potrzeby>"', '_("wizard.create_usage")'),
    ('"--- AGENT WIZARD ---"', '_("wizard.created_header")'),
    ('"Brak zapisanych blueprintów."', '_("wizard.no_blueprints")'),
    ('"--- BLUEPRINTY ---"', '_("wizard.blueprints_header")'),
    ('"Użycie: /agent-wizard load <nazwa>"', '_("wizard.load_usage")'),
    ('"--- BLUEPRINT ---"', '_("wizard.blueprint_header")'),
    ('"Użycie: /agent-wizard create <opis> | /agent-wizard blueprints | /agent-wizard load <nazwa>"',
     '_("wizard.usage_full")'),
]

TEXTUAL_TASKS_REPLACEMENTS = [
    ('"Kolejka zadań nie jest aktywna w tej sesji."', '_("tasks.no_queue")'),
    ('"Brak zadań w kolejce."', '_("tasks.empty")'),
    ('"--- ZADANIA ---"', '_("tasks.header")'),
    ('"Użycie: /tasks add <tytuł zadania>"', '_("tasks.add_usage")'),
    ('"Użycie: /tasks info <id>"', '_("tasks.info_usage")'),
    ('"--- ZADANIE ---"', '_("tasks.info_header")'),
    ('"(nieprzypisany)"', '_("tasks.info_not_assigned")'),
    ('"Użycie: /tasks cancel <id>"', '_("tasks.cancel_usage")'),
    ('"--- STATYSTYKI ZADAŃ ---"', '_("tasks.stats_header")'),
    ('"Użycie: /tasks list | /tasks add <tytuł> | /tasks info <id> | /tasks cancel <id> | /tasks stats"',
     '_("tasks.usage_full")'),
]

TEXTUAL_DASHBOARD_REPLACEMENTS = [
    ('"Nieprawidłowy numer portu."', '_("dashboard.invalid_port")'),
    ('"Dashboard nie jest uruchomiony."', '_("dashboard.not_running")'),
    ('"Dashboard zatrzymany."', '_("dashboard.stopped")'),
    ('"Dashboard: NIEAKTYWNY"', '_("dashboard.inactive")'),
    ('"Użycie: /dashboard start [port] | /dashboard stop | /dashboard status"',
     '_("dashboard.usage")'),
    ('"Zatrzymanie: /dashboard stop"', '_("dashboard.stop_hint")'),
    ('"Przeglądarka otwarta."', '_("dashboard.browser_opened")'),
]

TEXTUAL_KNOWLEDGE_REPLACEMENTS = [
    ('"Baza wiedzy nie jest aktywna w tej sesji."', '_("knowledge.inactive")'),
    ('"Brak wyników."', '_("knowledge.no_results")'),
    ('"Użycie: /knowledge status | /knowledge search <zapytanie> | /knowledge add <tekst>"',
     '_("knowledge.usage")'),
]

TEXTUAL_WORKSPACE_REPLACEMENTS = [
    ('"Wspólny workspace nie jest aktywny."', '_("workspace.inactive")'),
    ('"Workspace jest pusty."', '_("workspace.empty")'),
    ('"Brak zmian w historii workspace."', '_("workspace.changes_empty")'),
    ('"Użycie: /workspace list | /workspace read <ścieżka> | /workspace log"',
     '_("workspace.usage")'),
]

TEXTUAL_AUDIT_REPLACEMENTS = [
    ('"Łańcuch audytu nie jest aktywny."', '_("audit.inactive")'),
    ('"Brak wpisów audytu."', '_("audit.empty")'),
    ('"Użycie: /audit list | /audit agent <agent_id>"', '_("audit.usage")'),
]

TEXTUAL_SANDBOX_REPLACEMENTS = [
    ('"Menadżer sandbox nie jest aktywny."', '_("sandbox.inactive")'),
    ('"Brak aktywnych sandboxów."', '_("sandbox.empty")'),
    ('"--- Sandboxy ---"', '_("sandbox.header")'),
    ('"Użycie: /sandbox list | /sandbox create <agent_id> | /sandbox destroy <agent_id>"',
     '_("sandbox.usage")'),
]

TEXTUAL_WORKFLOW_REPLACEMENTS = [
    ('"Silnik workflow nie jest aktywny."', '_("workflow.inactive")'),
    ('"Brak uruchomionych workflow."', '_("workflow.empty")'),
    ('"--- Workflow ---"', '_("workflow.header")'),
    ('"Brak szablonów workflow."', '_("workflow.no_templates")'),
    ('"--- Szablony workflow ---"', '_("workflow.templates_header")'),
]

TEXTUAL_BUDGET_REPLACEMENTS = [
    ('"BudgetManager nie jest aktywny."', '_("budget.inactive")'),
    ('"Brak danych o budżetach agentów."', '_("budget.no_data")'),
    ('"--- BUDGET STATUS ---"', '_("budget.header")'),
    ('"Brak budżetów agentów."', '_("budget.no_agents")'),
]

TEXTUAL_QUOTA_REPLACEMENTS = [
    ('"QuotaPolicy nie jest aktywna."', '_("quota.inactive")'),
    ('"Brak zdefiniowanych quotas."', '_("quota.empty")'),
    ('"--- QUOTA POLICY ---"', '_("quota.header")'),
]

TEXTUAL_EVAL_REPLACEMENTS = [
    ('"EvalRunner nie jest aktywny."', '_("eval.inactive")'),
    ('"ABTestRunner nie jest aktywny."', '_("eval.compare_inactive")'),
    ('"Użycie: /eval compare <agent_a> <agent_b>"', '_("eval.compare_usage")'),
    ('"Brak historii ewaluacji."', '_("eval.history_empty")'),
    ('"--- EVAL HISTORY ---"', '_("eval.history_header")'),
    ('"RegressionDetector nie jest aktywny."', '_("eval.regression_inactive")'),
    ('"Brak zapisanych baselines."', '_("eval.baselines_empty")'),
    ('"--- EVAL BASELINES ---"', '_("eval.baselines_header")'),
]

TEXTUAL_FEEDBACK_REPLACEMENTS = [
    ('"HumanFeedbackCollector nie jest aktywny."', '_("feedback.inactive")'),
    ('"Brak zebranych opinii."', '_("feedback.empty")'),
    ('"--- FEEDBACK SUMMARY ---"', '_("feedback.header")'),
]

TEXTUAL_API_REST_REPLACEMENTS = [
    ('"RESTServer nie jest aktywny."', '_("api.inactive")'),
    ('"--- API STATUS ---"', '_("api.status_header")'),
    ('"REST API zatrzymane."', '_("api.stopped")'),
    ('"Użycie: /api status | /api start | /api stop"', '_("api.usage")'),
]

TEXTUAL_PLUGINS_REPLACEMENTS = [
    ('"PluginLoader nie jest aktywny."', '_("plugins.inactive")'),
    ('"Brak załadowanych pluginów."', '_("plugins.empty")'),
    ('"--- PLUGINS ---"', '_("plugins.header")'),
]

TEXTUAL_TEAM_REPLACEMENTS = [
    ('"TeamDashboard nie jest aktywny."', '_("team.dashboard_inactive")'),
    ('"Brak zarejestrowanych zespołów."', '_("team.empty")'),
    ('"--- TEAMS ---"', '_("team.header")'),
    ('"TeamComposer nie jest aktywny."', '_("team.composer_inactive")'),
    ('"Brak dostępnych szablonów zespołów."', '_("team.no_templates")'),
    ('"--- TEAM TEMPLATES ---"', '_("team.templates_header")'),
    ('"--- TEAM COMPOSED ---"', '_("team.composed_header")'),
    ('"DynamicScaler nie jest aktywny."', '_("team.scaler_inactive")'),
    ('"Użycie: /team scale <id> up|down"', '_("team.scale_usage")'),
]

TEXTUAL_MOUNT_REPLACEMENTS = [
    ('"Oczekiwanie na odpowiedź modelu wykonawczego."', '_("mount.executor_waiting")'),
    ('"Kastor jest nieaktywny w tej sesji (brak supervisor_service)."',
     '_("mount.kastor_inactive")'),
    ('"Oczekiwanie na wpisy Kastora."', '_("mount.kastor_waiting")'),
    ('"Kastor jest nieaktywny; panel pokazuje tylko komunikaty techniczne."',
     '_("mount.kastor_inactive_panel")'),
    ('"Watchdog Kastora został ponownie aktywowany po nowej wiadomości użytkownika."',
     '_("mount.watchdog_reactivated")'),
]

TEXTUAL_USER_TURN_REPLACEMENTS = [
    ('"Wznawiam plan i kontynuuję pracę."', '_("user_turn.plan_continue")'),
    ('"Plan został przerwany. Podaj nowe zadanie, abym utworzył nowy plan."',
     '_("user_turn.plan_stopped")'),
    ('"Utworzyłem nowy plan. Możesz wpisać kolejne polecenie, a rozpocznę realizację."',
     '_("user_turn.new_plan_created")'),
]

TEXTUAL_TOOL_FLOW_REPLACEMENTS = [
    ('"Ostrzeżenie runtime: osiągnięto limit iteracji resolve_tool_calls, pozostał nierozwiązany krok narzędziowy. Użyj krótkiego polecenia wtrącającego (np. \'kontynuuj\'), aby wznowić cykl."',
     '_("tool_flow.iteration_cap")'),
]

TEXTUAL_WATCHDOG_REPLACEMENTS = [
    ('"Watchdog Kastora osiągnął limit prób reaktywacji; wstrzymuję auto-reaktywację do kolejnej wiadomości użytkownika."',
     '_("watchdog.capped")'),
    ('"Watchdog Kastora wstrzymany po błędzie nadzorcy; oczekuję nowej wiadomości użytkownika."',
     '_("watchdog.error_suspended")'),
    ('"[Kastor] Błąd konsultacji — pomijam."', '_("watchdog.consult_error")'),
]

TEXTUAL_FORMAT_REPLACEMENTS = [
    ('"Wykonałem krok operacyjny i kontynuuję pracę."', '_("format.empty_answer")'),
    ('"Wykonałem krok operacyjny i kontynuuję realizację zadania."',
     '_("format.tool_step_generic")'),
]

TEXTUAL_COORDINATOR_REPLACEMENTS = [
    ('"[Koordynator] Treść do Sponsora zawierała wyłącznie tool_call/JSON — przekierowano do executor_log."',
     '_("coordinator.tool_redirected")'),
]

# ── f-string replacements (need regex or targeted replace) ─────────

TEXTUAL_FSTRING_REPLACEMENTS = [
    # Example: f"Nieprawidłowy numer. Zakres: 1..{max_index}."
    # These are trickier — we convert them to _("key", var=val) form.
    # We'll handle these with regex-based replacement.
]


# ── Aggregated replacements per file ───────────────────────────────

ALL_TEXTUAL_REPLACEMENTS = (
    TEXTUAL_HELP_REPLACEMENTS +
    TEXTUAL_MISC_REPLACEMENTS +
    TEXTUAL_WIDGET_REPLACEMENTS +
    TEXTUAL_CLIPBOARD_NOTIFY +
    TEXTUAL_MODELS_REPLACEMENTS +
    TEXTUAL_ROUTER_REPLACEMENTS +
    TEXTUAL_IDENTITY_REPLACEMENTS +
    TEXTUAL_CLS_REPLACEMENTS +
    TEXTUAL_QUEUE_REPLACEMENTS +
    TEXTUAL_CAPABILITIES_REPLACEMENTS +
    TEXTUAL_SYSTEM_CONTEXT_REPLACEMENTS +
    TEXTUAL_GOAL_REPLACEMENTS +
    TEXTUAL_IDLE_REPLACEMENTS +
    TEXTUAL_IMPORT_DIALOG_REPLACEMENTS +
    TEXTUAL_CREATE_PYTHON_REPLACEMENTS +
    TEXTUAL_RUN_PYTHON_REPLACEMENTS +
    TEXTUAL_RUN_SHELL_REPLACEMENTS +
    TEXTUAL_HISTORY_REPLACEMENTS +
    TEXTUAL_REMEMBER_REPLACEMENTS +
    TEXTUAL_MEMORIES_REPLACEMENTS +
    TEXTUAL_BYE_REPLACEMENTS +
    TEXTUAL_API_USAGE_REPLACEMENTS +
    TEXTUAL_API_KEY_REPLACEMENTS +
    TEXTUAL_SKILLS_REPLACEMENTS +
    TEXTUAL_AGENTS_REPLACEMENTS +
    TEXTUAL_WIZARD_REPLACEMENTS +
    TEXTUAL_TASKS_REPLACEMENTS +
    TEXTUAL_DASHBOARD_REPLACEMENTS +
    TEXTUAL_KNOWLEDGE_REPLACEMENTS +
    TEXTUAL_WORKSPACE_REPLACEMENTS +
    TEXTUAL_AUDIT_REPLACEMENTS +
    TEXTUAL_SANDBOX_REPLACEMENTS +
    TEXTUAL_WORKFLOW_REPLACEMENTS +
    TEXTUAL_BUDGET_REPLACEMENTS +
    TEXTUAL_QUOTA_REPLACEMENTS +
    TEXTUAL_EVAL_REPLACEMENTS +
    TEXTUAL_FEEDBACK_REPLACEMENTS +
    TEXTUAL_API_REST_REPLACEMENTS +
    TEXTUAL_PLUGINS_REPLACEMENTS +
    TEXTUAL_TEAM_REPLACEMENTS +
    TEXTUAL_MOUNT_REPLACEMENTS +
    TEXTUAL_USER_TURN_REPLACEMENTS +
    TEXTUAL_TOOL_FLOW_REPLACEMENTS +
    TEXTUAL_WATCHDOG_REPLACEMENTS +
    TEXTUAL_FORMAT_REPLACEMENTS +
    TEXTUAL_COORDINATOR_REPLACEMENTS +
    []
)


# ── CLI.PY REPLACEMENTS ───────────────────────────────────────────

CLI_HELP_REPLACEMENTS = [
    # These are the help tuples in cli.py's _CLI_HELP_COMMANDS
    ('("/help", "pokaż dostępne komendy")', '("/help", _("cli.help.cmd.help"))'),
    ('("/cls", "wyczyść ekran główny terminala")', '("/cls", _("cli.help.cmd.cls"))'),
    ('("/cls all", "wyczyść ekran i historię przewijania terminala")', '("/cls all", _("cli.help.cmd.cls_all"))'),
    ('("/models current", "pokaż aktualnie aktywny model wykonawczy")', '("/models current", _("cli.help.cmd.models_current"))'),
    ('("/models show", "pokaż modele dostępne w Ollama (1..x)")', '("/models show", _("cli.help.cmd.models_show"))'),
    ('("/models chose <nr>", "wybierz model wykonawczy po numerze z /models show")', '("/models chose <nr>", _("cli.help.cmd.models_chose"))'),
    ('("/permissions", "pokaż aktualny tryb zgód na zasoby")', '("/permissions", _("cli.help.cmd.permissions"))'),
    ('("/permissions all", "włącz globalną zgodę na zasoby")', '("/permissions all", _("cli.help.cmd.permissions_all"))'),
    ('("/permissions ask", "wyłącz globalną zgodę (pytaj per zasób)")', '("/permissions ask", _("cli.help.cmd.permissions_ask"))'),
    ('("/permissions reset", "wyczyść zapamiętane zgody per zasób")', '("/permissions reset", _("cli.help.cmd.permissions_reset"))'),
    ('("/show-system-context [tekst]", "pokaż kontekst systemowy przekazywany do modelu")', '("/show-system-context [tekst]", _("cli.help.cmd.show_system_context"))'),
    ('("/goal-status", "pokaż cel główny i etap z notes/main_plan.json")', '("/goal-status", _("cli.help.cmd.goal_status"))'),
    ('("/goal", "alias: pokaż cel główny i etap z notes/main_plan.json")', '("/goal", _("cli.help.cmd.goal"))'),
    ('("/queue-status", "pokaż stan kolejki modeli i decyzji polityki VRAM")', '("/queue-status", _("cli.help.cmd.queue_status"))'),
    ('("/capabilities [--network]", "pokaż gotowość narzędzi i backendów")', '("/capabilities [--network]", _("cli.help.cmd.capabilities"))'),
    ('("/history [n]", "pokaż ostatnie wiadomości (domyślnie 10)")', '("/history [n]", _("cli.help.cmd.history"))'),
    ('("/remember <tekst>", "zapisz notatkę do pamięci")', '("/remember <tekst>", _("cli.help.cmd.remember"))'),
    ('("/memories [zapytanie]", "przeszukaj pamięć")', '("/memories [zapytanie]", _("cli.help.cmd.memories"))'),
    ('("/import-dialog [plik]", "zapisz dialog (bez kodu) jako kontekst pamięci")', '("/import-dialog [plik]", _("cli.help.cmd.import_dialog"))'),
    ('("/create-python <plik> <opis>", "wygeneruj i zapisz skrypt Python przez model")', '("/create-python <plik> <opis>", _("cli.help.cmd.create_python"))'),
    ('("/run-python <plik> [arg ...]", "uruchom skrypt Python z argumentami")', '("/run-python <plik> [arg ...]", _("cli.help.cmd.run_python"))'),
    ('("/run-shell <polecenie>", "uruchom polecenie shell z polityką whitelist")', '("/run-shell <polecenie>", _("cli.help.cmd.run_shell"))'),
    ('("/bye", "zapisz podsumowanie sesji i zakończ")', '("/bye", _("cli.help.cmd.bye"))'),
    ('("/exit", "zakończ bez podsumowania")', '("/exit", _("cli.help.cmd.exit"))'),
]

CLI_MISC_REPLACEMENTS = [
    # CLI-specific strings
    ('lines = ["Komendy (CLI):"]', 'lines = [_("cli.help.header")]'),
    ('"Użycie: /models show | /models chose <nr>"', '_("cli.models_usage")'),
    ('"Brak modeli dostępnych w Ollama."', '_("cli.models_empty")'),
    ('"--- MODELE OLLAMA ---"', '_("cli.models_header")'),
    ('"Użycie: /models chose <nr>"', '_("cli.models_chose_usage")'),
    ('"Nieprawidłowy numer modelu. Użyj wartości całkowitej, np. /models chose 1"',
     '_("cli.models_invalid_number")'),
    ('"--- PERMISSIONS ---"', '_("cli.permissions.header")'),
    ('"Włączono globalną zgodę na zasoby."', '_("cli.permissions.global_on")'),
    ('"Włączono tryb pytań o zgodę per zasób."', '_("cli.permissions.ask_on")'),
    ('"Wyczyszczono zapamiętane zgody per zasób."', '_("cli.permissions.reset_done")'),
    ('"Brak zapamiętanych zgód do wyczyszczenia."', '_("cli.permissions.reset_empty")'),
    ('"Użycie: /permissions [status|all|ask|reset]"', '_("cli.permissions.usage")'),
    ('"Zamknięto sesję."', '_("cli.session.closed")'),
    ('"Zapisano podsumowanie sesji do kontynuacji po restarcie."', '_("cli.session.bye_saved")'),
    ('"Do zobaczenia."', '_("cli.session.bye_farewell")'),
    ('"Polityka kolejki modeli jest wyłączona."', '_("cli.queue.disabled")'),
    ('"--- MODEL QUEUE STATUS ---"', '_("cli.queue.header")'),
    ('"vram: brak aktywnego doradcy VRAM"', '_("cli.queue.no_vram")'),
    ('"- brak"', '_("cli.queue.no_decisions")'),
    ('"--- CAPABILITIES ---"', '_("cli.capabilities.header")'),
    ('"--- SYSTEM CONTEXT ---"', '_("cli.system_context.header")'),
    ('"--- GOAL STATUS ---"', '_("cli.goal_status.header")'),
    ('"Zapisano treść dialogu (bez kodu) do pamięci."', '_("cli.import.done")'),
    ('"Użycie: /create-python <plik> <opis>"', '_("cli.create_python.usage")'),
    ('"Użycie: /run-python <plik> [arg ...]"', '_("cli.run_python.usage")'),
    ('"Użycie: /run-shell <polecenie>"', '_("cli.run_shell.usage")'),
    ('"Brak historii."', '_("cli.history.empty")'),
    ('"Użycie: /remember <tekst>"', '_("cli.remember.usage")'),
    ('"Zapisano notatkę."', '_("cli.remember.done")'),
    ('"Brak wyników."', '_("cli.memories.empty")'),
]


# ── MAIN.PY REPLACEMENTS ──────────────────────────────────────────

MAIN_REPLACEMENTS = [
    ('"amiagi CLI"', '_("main.arg.description")'),
    ('"Wyczyść historię konwersacji i uruchom z kontekstem startowym."',
     '_("main.arg.cold_start")'),
    ('"Włącz tryb autonomiczny dla bieżącego uruchomienia."',
     '_("main.arg.auto")'),
    ('"Wyłącz kontrolę VRAM i kolejki modeli po stronie runtime."',
     '_("main.arg.vram_off")'),
    ('"Ścieżka do pliku instrukcji startowych (markdown)."',
     '_("main.arg.startup_dialogue")'),
    ('"Tryb interfejsu: klasyczny CLI lub Textual (podział ekranu)."',
     '_("main.arg.ui")'),
]


def migrate_textual_cli():
    path = IFACE / "textual_cli.py"
    text = path.read_text(encoding="utf-8")
    backup = path.with_suffix(".py.bak")
    backup.write_text(text, encoding="utf-8")
    
    text, count = _do_all_replacements(text, ALL_TEXTUAL_REPLACEMENTS)
    text = _add_import(text)
    
    path.write_text(text, encoding="utf-8")
    print(f"textual_cli.py: {count} replacements applied (backup: {backup.name})")
    return count


def migrate_cli():
    path = IFACE / "cli.py"
    text = path.read_text(encoding="utf-8")
    backup = path.with_suffix(".py.bak")
    backup.write_text(text, encoding="utf-8")
    
    all_cli = CLI_HELP_REPLACEMENTS + CLI_MISC_REPLACEMENTS
    text, count = _do_all_replacements(text, all_cli)
    text = _add_import(text)
    
    path.write_text(text, encoding="utf-8")
    print(f"cli.py: {count} replacements applied (backup: {backup.name})")
    return count


def migrate_main():
    path = SRC / "main.py"
    text = path.read_text(encoding="utf-8")
    backup = path.with_suffix(".py.bak")
    backup.write_text(text, encoding="utf-8")
    
    text, count = _do_all_replacements(text, MAIN_REPLACEMENTS)
    text = _add_import(text)
    
    path.write_text(text, encoding="utf-8")
    print(f"main.py: {count} replacements applied (backup: {backup.name})")
    return count


def main():
    total = 0
    total += migrate_textual_cli()
    total += migrate_cli()
    total += migrate_main()
    print(f"\nTotal: {total} replacements across 3 files.")
    print("Review changes, then delete .bak files when satisfied.")


if __name__ == "__main__":
    main()
