"""Model-selection wizard methods for the Textual TUI adapter (extracted mixin).

Part of the v1.0.3 Strangler Fig migration — Faza 5.2 dead code / LOC reduction.
Extracts ~450 LOC of wizard methods from textual_cli.py into a reusable mixin.
"""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast
from collections.abc import Awaitable

from amiagi.infrastructure.openai_client import (
    OpenAIClient,
    SUPPORTED_OPENAI_MODELS,
    mask_api_key,
)
from amiagi.infrastructure.session_model_config import SessionModelConfig
from amiagi.interfaces.shared_cli_helpers import (
    _fetch_ollama_models,
    _set_executor_model,
)
from amiagi.i18n import _

if TYPE_CHECKING:
    from amiagi.application.agent_registry import AgentRegistry
    from amiagi.application.chat_service import ChatService
    from amiagi.application.router_engine import RouterEngine
    from amiagi.config import Settings
    from amiagi.infrastructure.activity_logger import ActivityLogger
    from amiagi.infrastructure.usage_tracker import UsageTracker


class TextualWizardMixin:
    """Mixin providing model-selection wizard for ``_AmiagiTextualApp``.

    All methods access services and state via ``self``, which at runtime
    is the full ``_AmiagiTextualApp`` instance.
    """

    # -- Type stubs so Pylance can resolve attributes from the host class --
    if TYPE_CHECKING:
        _chat_service: ChatService
        _router_engine: RouterEngine
        _settings: Settings | None
        _activity_logger: ActivityLogger | None
        _usage_tracker: UsageTracker
        _agent_registry: AgentRegistry | None
        _model_config_path: Path
        _work_dir: Path
        _wizard_phase: int
        _wizard_models: list[tuple[str, str]]
        _wizard_kastor_models: list[tuple[str, str]]
        _wizard_polluks_choice: tuple[str, str]
        _model_configured: bool
        _main_thread_id: int
        _last_background_worker: threading.Thread | None

        # Methods from _AmiagiTextualApp
        def _append_log(self, widget_id: str, message: str) -> None: ...
        def _run_on_ui_thread(self, callback: Callable[[], Any]) -> None: ...
        def _log_activity(self, *, action: str, intent: str, details: dict[str, Any] | None = None) -> None: ...

        # Methods inherited from App (stubs match Textual signatures)
        def query_one(self, selector: str | type, expect_type: type | None = None) -> Any: ...
        def set_interval(self, interval: float, callback: Callable[[], Awaitable[Any]] | Callable[[], Any] | None = None, *, name: str | None = None, repeat: int = 0, pause: bool = False) -> Any: ...

    # ------------------------------------------------------------------
    # Model list helpers
    # ------------------------------------------------------------------

    def _build_wizard_model_list(self) -> list[tuple[str, str]]:
        """Return a combined list of (model_name, source) for the wizard."""
        entries: list[tuple[str, str]] = []
        # Ollama local models
        models, error = _fetch_ollama_models(self._chat_service)
        if error is None and models:
            for name in models:
                entries.append((name, "ollama"))
        # OpenAI API models
        for name in SUPPORTED_OPENAI_MODELS:
            entries.append((name, "openai"))
        return entries

    def _format_wizard_model_list(
        self, models: list[tuple[str, str]], *, default_name: str = ""
    ) -> str:
        lines: list[str] = []
        ollama_models = [(n, s) for n, s in models if s == "ollama"]
        api_models = [(n, s) for n, s in models if s != "ollama"]
        idx = 1
        if ollama_models:
            lines.append(_("wizard.model_list_local"))
            for name, _size in ollama_models:
                marker = _("wizard.default_marker") if name == default_name else ""
                lines.append(f"    {idx}. {name}{marker}")
                idx += 1
        if api_models:
            lines.append(_("wizard.model_list_api"))
            for name, source in api_models:
                marker = _("wizard.default_marker") if name == default_name else ""
                lines.append(f"    {idx}. ☁ {name}  [{source.upper()}]{marker}")
                idx += 1
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Wizard lifecycle
    # ------------------------------------------------------------------

    def _start_model_selection_wizard(self) -> None:
        """Begin the interactive model selection wizard on mount.

        If a saved model config exists, auto-restore it and skip the wizard.
        Heavy HTTP work (model listing, API pings) is dispatched to a
        background thread so the Textual event loop is never blocked.
        """
        self._append_log(
            "user_model_log",
            "⏳ Ładowanie konfiguracji modeli…",
        )
        worker = threading.Thread(
            target=self._wizard_startup_background,
            daemon=True,
            name="amiagi-wizard-startup",
        )
        self._last_background_worker = worker
        worker.start()

    def _wizard_startup_background(self) -> None:
        """Background thread: fetch models & auto-restore or prepare interactive wizard."""
        try:
            saved = SessionModelConfig.load(self._model_config_path)
            if saved and saved.polluks_model:
                available = self._build_wizard_model_list()
                available_names = {n for n, _s in available}
                polluks_ok = saved.polluks_model in available_names or saved.polluks_source != "ollama"
                kastor_ok = (
                    not saved.kastor_model
                    or saved.kastor_model in available_names
                    or saved.kastor_source != "ollama"
                )
                if polluks_ok:
                    self._wizard_polluks_choice = (saved.polluks_model, saved.polluks_source)
                    self._wizard_kastor_models = available
                    self._wizard_models = available
                    self._wizard_finalize(saved.kastor_model if kastor_ok else "", saved.kastor_source if kastor_ok else "ollama")
                    self._run_on_ui_thread(lambda: self._append_log(
                        "user_model_log",
                        _("wizard.restored"),
                    ))
                    self._on_wizard_ready()
                    return

            models = self._build_wizard_model_list()
            if not models:
                def _no_models():
                    self._append_log(
                        "user_model_log",
                        _("wizard.no_models"),
                    )
                    self._model_configured = True
                self._run_on_ui_thread(_no_models)
                self._on_wizard_ready()
                return

            # Interactive wizard — show prompt on UI thread
            self._wizard_models = models
            def _show_interactive():
                self._wizard_phase = 1
                self._wizard_show_polluks_prompt()
            self._run_on_ui_thread(_show_interactive)
        except Exception as exc:
            def _show_error():
                self._append_log(
                    "user_model_log",
                    f"Błąd inicjalizacji wizarda modeli: {exc}",
                )
                self._model_configured = True
            self._run_on_ui_thread(_show_error)
            self._on_wizard_ready()

    def _on_wizard_ready(self) -> None:
        """Called after wizard completes (from background thread).

        If autonomous_mode is active, auto-dispatch an initial 'kontynuuj' turn.
        """
        if getattr(self, "_autonomous_mode", False) and self._model_configured:
            def _auto_kickoff():
                if not self._router_engine.router_cycle_in_progress:
                    self._router_engine.submit_user_turn("kontynuuj")
            self._run_on_ui_thread(_auto_kickoff)

    # ------------------------------------------------------------------
    # Wizard prompt display
    # ------------------------------------------------------------------

    def _wizard_show_polluks_prompt(self) -> None:
        """Display (or re-display) the Polluks model selection prompt."""
        header = _("wizard.polluks_header")
        footer = "╰───────────────────────────────────────────────────────────╯ \n"
        body = self._format_wizard_model_list(self._wizard_models)
        b1 = _("wizard.polluks_body1")
        b2 = _("wizard.polluks_body2")
        b3 = _("wizard.polluks_body3")
        b4 = _("wizard.polluks_body4")
        hint = _("wizard.polluks_hint")
        self._append_log(
            "user_model_log",
            f"\n{header}\n"
            f"{b1}\n"
            f"{b2}\n\n"
            f"{b3}\n\n"
            f"{body}\n\n"
            f"{b4}\n"
            f"{hint}\n"
            f"{footer}",
        )

    def _wizard_show_kastor_prompt(self, default_name: str = "") -> None:
        """Display (or re-display) the Kastor model selection prompt."""
        header = _("wizard.kastor_header")
        footer = "╰───────────────────────────────────────────────────────────╯ \n"
        body = self._format_wizard_model_list(
            self._wizard_kastor_models, default_name=default_name
        )
        kb1 = _("wizard.kastor_body1")
        kb2 = _("wizard.kastor_body2")
        kb3 = _("wizard.kastor_body3")
        hint = _("wizard.polluks_hint")
        self._append_log(
            "user_model_log",
            f"\n{header}\n"
            f"{kb1}\n\n"
            f"{kb2}\n\n"
            f"{body}\n\n"
            f"{kb3}\n"
            f"{hint}\n"
            f"{footer}",
        )

    def _wizard_redisplay_prompt(self) -> None:
        """Re-show the current wizard step after a / command."""
        if self._wizard_phase == 1:
            self._wizard_show_polluks_prompt()
        elif self._wizard_phase == 2:
            default_kastor = self._wizard_get_default_kastor()
            self._wizard_show_kastor_prompt(default_kastor)

    def _wizard_get_default_kastor(self) -> str:
        """Return the default Kastor model name for the wizard prompt."""
        default_kastor = ""
        settings = self._settings
        if settings is not None:
            default_kastor = settings.supervisor_model or ""
        if not default_kastor and self._wizard_kastor_models:
            default_kastor = self._wizard_kastor_models[0][0]
        return default_kastor

    # ------------------------------------------------------------------
    # Wizard input handling
    # ------------------------------------------------------------------

    def _wizard_handle_input(self, text: str) -> bool:
        """Process wizard-phase input. Return True if consumed."""
        if self._wizard_phase == 0:
            return False

        if self._wizard_phase == 1:
            return self._wizard_handle_polluks_choice(text)
        if self._wizard_phase == 2:
            return self._wizard_handle_kastor_choice(text)
        return False

    def _wizard_handle_polluks_choice(self, text: str) -> bool:
        """Phase 1: user picks the executor (Polluks) model."""
        try:
            idx = int(text.strip())
        except ValueError:
            total = len(self._wizard_models)
            self._append_log(
                "user_model_log",
                _("wizard.polluks_expect_number", total=total),
            )
            return True

        if idx < 1 or idx > len(self._wizard_models):
            self._append_log(
                "user_model_log",
                _("wizard.invalid_range", max=len(self._wizard_models)),
            )
            return True

        name, source = self._wizard_models[idx - 1]
        self._wizard_polluks_choice = (name, source)
        self._append_log("user_model_log", f"  → Polluks: {name} ({source})")

        # Move to phase 2: Kastor model
        self._wizard_phase = 2
        self._wizard_kastor_models = self._build_wizard_model_list()
        default_kastor = self._wizard_get_default_kastor()
        self._wizard_show_kastor_prompt(default_kastor)
        return True

    def _wizard_handle_kastor_choice(self, text: str) -> bool:
        """Phase 2: user picks the supervisor (Kastor) model or presses Enter for default."""
        stripped = text.strip()

        # Default selection (empty input)
        if stripped == "":
            kastor_name = ""
            kastor_source = "ollama"
            settings = self._settings
            if settings is not None and settings.supervisor_model:
                kastor_name = settings.supervisor_model
                kastor_source = "ollama"
            elif self._wizard_kastor_models:
                kastor_name = self._wizard_kastor_models[0][0]
                kastor_source = self._wizard_kastor_models[0][1]
        else:
            try:
                idx = int(stripped)
            except ValueError:
                total = len(self._wizard_kastor_models)
                self._append_log(
                    "user_model_log",
                    _("wizard.kastor_expect_number", total=total),
                )
                return True
            if idx < 1 or idx > len(self._wizard_kastor_models):
                self._append_log(
                    "user_model_log",
                    _("wizard.invalid_range", max=len(self._wizard_kastor_models)),
                )
                return True
            kastor_name, kastor_source = self._wizard_kastor_models[idx - 1]

        self._append_log("user_model_log", f"  → Kastor: {kastor_name} ({kastor_source})")
        self._wizard_finalize(kastor_name, kastor_source)
        return True

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def _wizard_finalize(self, kastor_name: str, kastor_source: str) -> None:
        """Apply wizard selections and unblock the UI."""
        polluks_name, polluks_source = self._wizard_polluks_choice
        errors: list[str] = []

        # --- Apply Polluks model ---
        if polluks_source == "ollama":
            ok, _prev = _set_executor_model(self._chat_service, polluks_name)
            if not ok:
                errors.append(_("wizard.finalize_polluks_fail", name=polluks_name))
        else:
            # OpenAI model for Polluks
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                settings = self._settings
                if settings is not None:
                    api_key = settings.openai_api_key
            if not api_key:
                errors.append(
                    _("wizard.finalize_no_api_key")
                )
                # Fallback to first local model
                local = [n for n, s in self._wizard_models if s == "ollama"]
                if local:
                    _set_executor_model(self._chat_service, local[0])
                    polluks_name = local[0]
                    polluks_source = "ollama"
            else:
                base_url = "https://api.openai.com/v1"
                timeout = 120
                settings = self._settings
                if settings is not None:
                    base_url = settings.openai_base_url or base_url
                    timeout = settings.openai_request_timeout_seconds or timeout

                openai_client = OpenAIClient(
                    api_key=api_key,
                    model=polluks_name,
                    base_url=base_url,
                    io_logger=getattr(self._chat_service.ollama_client, "io_logger", None),
                    activity_logger=self._activity_logger,
                    client_role="executor",
                    request_timeout_seconds=timeout,
                    usage_tracker=self._usage_tracker,
                )
                # Validate API key
                try:
                    reachable = openai_client.ping()
                except Exception:
                    reachable = False

                if reachable:
                    self._chat_service.ollama_client = openai_client
                    self._append_log(
                        "user_model_log",
                        _("wizard.finalize_api_verified", masked=mask_api_key(api_key)),
                    )
                else:
                    errors.append(
                        _("wizard.finalize_api_fail", masked=mask_api_key(api_key))
                    )
                    local = [n for n, s in self._wizard_models if s == "ollama"]
                    if local:
                        _set_executor_model(self._chat_service, local[0])
                        polluks_name = local[0]
                        polluks_source = "ollama"

        # --- Apply Kastor model ---
        supervisor = self._chat_service.supervisor_service
        if supervisor is not None and kastor_name:
            if kastor_source == "ollama":
                try:
                    supervisor.ollama_client = replace(
                        cast(Any, supervisor.ollama_client), model=kastor_name
                    )
                except Exception:
                    try:
                        supervisor.ollama_client.model = kastor_name  # type: ignore[attr-defined]
                    except Exception:
                        errors.append(_("wizard.finalize_kastor_fail", name=kastor_name))
            else:
                # OpenAI for Kastor
                api_key = os.environ.get("OPENAI_API_KEY", "")
                settings = self._settings
                if not api_key and settings is not None:
                    api_key = settings.openai_api_key
                if api_key:
                    base_url = "https://api.openai.com/v1"
                    timeout = 120
                    if settings is not None:
                        base_url = settings.openai_base_url or base_url
                        timeout = settings.openai_request_timeout_seconds or timeout

                    kastor_openai = OpenAIClient(
                        api_key=api_key,
                        model=kastor_name,
                        base_url=base_url,
                        io_logger=getattr(supervisor.ollama_client, "io_logger", None),
                        activity_logger=self._activity_logger,
                        client_role="supervisor",
                        request_timeout_seconds=timeout,
                        usage_tracker=self._usage_tracker,
                    )
                    supervisor.ollama_client = cast(Any, kastor_openai)
                else:
                    errors.append(
                        _("wizard.finalize_kastor_no_key")
                    )

        # --- Sync AgentDescriptor in registry ---
        self._sync_agent_model("polluks", polluks_name, polluks_source)
        if kastor_name:
            self._sync_agent_model("kastor", kastor_name, kastor_source)

        # --- Show errors ---
        for msg in errors:
            self._append_log("user_model_log", msg)

        # --- Configuration summary ---
        polluks_label = polluks_name
        if polluks_source != "ollama":
            polluks_label = f"☁ {polluks_name} [{polluks_source.upper()}]"
        kastor_label = kastor_name or _("wizard.finalize_kastor_disabled")
        if kastor_source != "ollama" and kastor_name:
            kastor_label = f"☁ {kastor_name} [{kastor_source.upper()}]"

        summary_hdr = _("wizard.finalize_summary_header")
        ready_msg = _("wizard.finalize_ready")
        summary = (
            f"\n{summary_hdr}\n"
            f"  Polluks: {polluks_label}\n"
            f"  Kastor:  {kastor_label}\n"
            "╰──────────────────────────────────────────────────────────╯\n"
            f"\n{ready_msg}"
        )
        self._append_log("user_model_log", summary)

        # --- Activate API usage bar if API model ---
        if polluks_source != "ollama" or (kastor_source != "ollama" and kastor_name):
            self._show_api_usage_bar()

        # --- Persist model assignment for next session ---
        SessionModelConfig(
            polluks_model=polluks_name,
            polluks_source=polluks_source,
            kastor_model=kastor_name,
            kastor_source=kastor_source,
        ).save(self._model_config_path)

        # --- Unblock ---
        self._wizard_phase = 0
        self._model_configured = True
        self._log_activity(
            action="wizard.completed",
            intent="Użytkownik wybrał modele w wizardzie startowym.",
            details={
                "polluks_model": polluks_name,
                "polluks_source": polluks_source,
                "kastor_model": kastor_name,
                "kastor_source": kastor_source,
            },
        )

        # If called from the interactive wizard on UI thread, trigger auto-kickoff
        if threading.get_ident() == self._main_thread_id:
            self._on_wizard_ready()

    # ------------------------------------------------------------------
    # Model sync & persistence helpers
    # ------------------------------------------------------------------

    def _sync_agent_model(
        self, agent_id: str, model_name: str, source: str = "ollama"
    ) -> None:
        """Update the agent descriptor in the registry so the dashboard shows the correct model."""
        if self._agent_registry is None:
            return
        try:
            self._agent_registry.update_model(
                agent_id, model_name=model_name, model_backend=source
            )
        except KeyError:
            pass  # agent not registered — nothing to sync

    def _persist_model_config(self) -> None:
        """Snapshot current model assignments and save to disk."""
        polluks_model = str(getattr(self._chat_service.ollama_client, "model", ""))
        polluks_api = getattr(self._chat_service.ollama_client, "_is_api_client", False)
        polluks_source = "openai" if polluks_api else "ollama"

        kastor_model = ""
        kastor_source = "ollama"
        supervisor = self._chat_service.supervisor_service
        if supervisor is not None:
            kastor_model = str(getattr(supervisor.ollama_client, "model", ""))
            kastor_api = getattr(supervisor.ollama_client, "_is_api_client", False)
            kastor_source = "openai" if kastor_api else "ollama"

        SessionModelConfig(
            polluks_model=polluks_model,
            polluks_source=polluks_source,
            kastor_model=kastor_model,
            kastor_source=kastor_source,
        ).save(self._model_config_path)

    # ------------------------------------------------------------------
    # API usage bar (kept here because it's tightly coupled to model state)
    # ------------------------------------------------------------------

    def _show_api_usage_bar(self) -> None:
        """Make the API usage status bar visible and start refresh timer."""
        try:
            from textual.widgets import Static
            bar = self.query_one("#api_usage_bar", Static)
            bar.styles.display = "block"
            self.set_interval(2.0, self._refresh_api_usage_bar)
        except Exception:
            pass

    def _refresh_api_usage_bar(self) -> None:
        """Update the API usage status bar with current token/cost info."""
        try:
            from textual.widgets import Static
            bar = self.query_one("#api_usage_bar", Static)
        except Exception:
            return
        line = self._usage_tracker.format_status_line()
        if line:
            bar.update(line)
