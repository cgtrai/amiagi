from __future__ import annotations

import argparse
from dataclasses import is_dataclass, replace
from pathlib import Path

from amiagi.application.chat_service import ChatService
from amiagi.application.discussion_sync import extract_dialogue_without_code
from amiagi.application.model_queue_policy import ModelQueuePolicy
from amiagi.application.supervisor_service import SupervisorService
from amiagi.config import Settings
from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.memory_repository import MemoryRepository
from amiagi.infrastructure.model_io_logger import ModelIOLogger
from amiagi.infrastructure.ollama_client import OllamaClient
from amiagi.infrastructure.vram_advisor import VramAdvisor
from amiagi.interfaces.cli import run_cli
from amiagi.interfaces.textual_cli import run_textual_cli


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="amiagi CLI")
    parser.add_argument(
        "-cs",
        "--cold_start",
        action="store_true",
        help="Wyczyść historię konwersacji i uruchom z kontekstem startowym.",
    )
    parser.add_argument(
        "-auto",
        "--auto",
        action="store_true",
        help="Włącz tryb autonomiczny dla bieżącego uruchomienia.",
    )
    parser.add_argument(
        "-vram-off",
        "--vram-off",
        action="store_true",
        help="Wyłącz kontrolę VRAM i kolejki modeli po stronie runtime (zarządzanie pamięcią pozostaw Ollama).",
    )
    parser.add_argument(
        "--startup_dialogue_path",
        default="wprowadzenie.md",
        help="Ścieżka do pliku instrukcji startowych (markdown).",
    )
    parser.add_argument(
        "--ui",
        choices=("cli", "textual"),
        default="cli",
        help="Tryb interfejsu: klasyczny CLI lub Textual (podział ekranu).",
    )
    return parser.parse_args(argv)


def _seed_startup_context(
    *,
    repository: MemoryRepository,
    activity_logger: ActivityLogger,
    startup_dialogue_path: Path,
    force: bool,
) -> None:
    if not startup_dialogue_path.exists():
        activity_logger.log(
            action="startup.seed.skip",
            intent="Pominięto seed kontekstu, brak pliku dialogu startowego.",
            details={"path": str(startup_dialogue_path)},
        )
        return

    if not force:
        existing = repository.latest_memory(kind="session_summary", source="startup_seed")
        if existing is not None:
            return

    raw_dialogue = startup_dialogue_path.read_text(encoding="utf-8")
    cleaned_dialogue = extract_dialogue_without_code(raw_dialogue)
    startup_summary = cleaned_dialogue.strip() or raw_dialogue.strip()

    repository.replace_memory(
        kind="discussion_context",
        source="imported_dialogue",
        content=cleaned_dialogue,
    )
    repository.replace_memory(
        kind="session_summary",
        source="startup_seed",
        content=startup_summary,
    )
    activity_logger.log(
        action="startup.seed.done",
        intent="Zapisano instrukcje startowe i punkt startowy dla modelu.",
        details={"path": str(startup_dialogue_path), "summary_chars": len(startup_summary)},
    )


def _resolve_startup_dialogue_path(raw_path: str, work_dir: Path) -> Path:
    direct_path = Path(raw_path)
    if direct_path.exists() or direct_path.is_absolute():
        return direct_path

    work_dir_candidate = work_dir / raw_path
    if work_dir_candidate.exists():
        return work_dir_candidate

    return direct_path


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = Settings.from_env()
    if args.auto:
        if is_dataclass(settings):
            settings = replace(settings, autonomous_mode=True)
        else:
            settings.autonomous_mode = True
    settings.work_dir.mkdir(parents=True, exist_ok=True)

    repository = MemoryRepository(settings.db_path)
    executor_log_path = settings.executor_model_io_log_path
    supervisor_log_path = settings.supervisor_model_io_log_path
    io_logger = ModelIOLogger(executor_log_path, model_role="executor")
    activity_logger = ActivityLogger(settings.activity_log_path)

    if args.cold_start:
        repository.clear_all()
        log_paths_to_clear = {
            executor_log_path,
            supervisor_log_path,
            settings.model_io_log_path,
            settings.supervisor_dialogue_log_path,
        }
        for path in log_paths_to_clear:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        settings.activity_log_path.parent.mkdir(parents=True, exist_ok=True)
        settings.activity_log_path.write_text("", encoding="utf-8")
        activity_logger.log(
            action="startup.cold_start",
            intent="Wyczyszczenie historii i logów JSONL oraz uruchomienie od kontekstu początkowego.",
            details={
                "db_path": str(settings.db_path),
                "executor_model_io_log_path": str(executor_log_path),
                "supervisor_model_io_log_path": str(supervisor_log_path),
                "activity_log_path": str(settings.activity_log_path),
            },
        )

    startup_dialogue_path = _resolve_startup_dialogue_path(
        raw_path=args.startup_dialogue_path,
        work_dir=settings.work_dir,
    )

    _seed_startup_context(
        repository=repository,
        activity_logger=activity_logger,
        startup_dialogue_path=startup_dialogue_path,
        force=args.cold_start,
    )

    runtime_vram_control_enabled = not args.vram_off
    queue_policy = (
        ModelQueuePolicy(
            supervisor_min_free_vram_mb=settings.supervisor_min_free_vram_mb,
            queue_max_wait_seconds=settings.model_queue_max_wait_seconds,
        )
        if runtime_vram_control_enabled
        else None
    )
    vram_advisor = VramAdvisor()

    ollama = OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        io_logger=io_logger,
        activity_logger=activity_logger,
        request_timeout_seconds=settings.ollama_request_timeout_seconds,
        max_retries=settings.ollama_max_retries,
        retry_backoff_seconds=settings.ollama_retry_backoff_seconds,
        client_role="executor",
        queue_policy=queue_policy,
        vram_advisor=vram_advisor,
    )

    default_executor_models: list[str] = []
    list_models = getattr(ollama, "list_models", None)
    if callable(list_models):
        try:
            default_executor_models = list_models()
        except Exception as error:
            activity_logger.log(
                action="models.default.error",
                intent="Nie udało się pobrać listy modeli z Ollama podczas ustawiania modelu domyślnego.",
                details={"error": str(error)},
            )

    if default_executor_models:
        first_model = default_executor_models[0]
        if first_model != getattr(ollama, "model", ""):
            ollama = replace(ollama, model=first_model)
            activity_logger.log(
                action="models.default.selected",
                intent="Ustawiono domyślny model wykonawczy na pierwszy model zwrócony przez Ollama.",
                details={"model": first_model, "models_count": len(default_executor_models)},
            )

    supervisor_service: SupervisorService | None = None
    if settings.supervisor_enabled:
        supervisor_io_logger = ModelIOLogger(supervisor_log_path, model_role="supervisor")
        supervisor_client = OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.supervisor_model,
            io_logger=supervisor_io_logger,
            activity_logger=activity_logger,
            request_timeout_seconds=settings.supervisor_request_timeout_seconds,
            max_retries=0,
            retry_backoff_seconds=0.0,
            client_role="supervisor",
            queue_policy=queue_policy,
            vram_advisor=vram_advisor,
        )
        if supervisor_client.ping():
            supervisor_service = SupervisorService(
                ollama_client=supervisor_client,
                activity_logger=activity_logger,
                max_repair_rounds=settings.supervisor_max_repair_rounds,
                dialogue_log_path=settings.supervisor_dialogue_log_path,
            )
            activity_logger.log(
                action="supervisor.enabled",
                intent="Uruchomiono model nadzorcy odpowiedzi modelu wykonawczego.",
                details={
                    "model": settings.supervisor_model,
                    "log_path": str(supervisor_log_path),
                    "max_repair_rounds": settings.supervisor_max_repair_rounds,
                },
            )
        else:
            print(
                "Uwaga: nie można połączyć z modelem nadzorcy. "
                "Uruchamiam bez warstwy nadzorcy."
            )
            activity_logger.log(
                action="supervisor.disabled",
                intent="Nie udało się uruchomić modelu nadzorcy; użyto trybu bez nadzorcy.",
                details={"model": settings.supervisor_model},
            )

    chat_service = ChatService(
        memory_repository=repository,
        ollama_client=ollama,
        max_context_memories=settings.max_context_memories,
        activity_logger=activity_logger,
        vram_advisor=vram_advisor,
        work_dir=settings.work_dir,
        supervisor_service=supervisor_service,
    )
    max_idle_autoreactivations = getattr(settings, "max_idle_autoreactivations", 2)
    router_mailbox_log_path = getattr(settings, "router_mailbox_log_path", Path("./logs/router_mailbox.jsonl"))

    print(f"Model: {getattr(ollama, 'model', settings.ollama_model)}")
    print(
        "Ollama timeout/retry: "
        f"{settings.ollama_request_timeout_seconds}s, retries={settings.ollama_max_retries}, "
        f"backoff={settings.ollama_retry_backoff_seconds}s"
    )
    print(f"Baza pamięci: {settings.db_path}")
    print(f"Log I/O modelu (wykonawca): {executor_log_path}")
    print(f"Log I/O modelu (nadzorca): {supervisor_log_path}")
    print(f"Log dialogu nadzoru: {settings.supervisor_dialogue_log_path}")
    print(f"Log czynności: {settings.activity_log_path}")
    print(f"Log skrzynki routera: {router_mailbox_log_path}")
    print(f"Polityka shell: {settings.shell_policy_path}")
    print(f"Katalog roboczy modelu: {settings.work_dir}")
    print(f"Tryb autonomiczny: {'ON' if settings.autonomous_mode else 'OFF'}")
    print(f"Interfejs: {args.ui}")
    print(f"Kontrola VRAM runtime: {'OFF' if args.vram_off else 'ON'}")
    print(f"Limit autowzbudzeń IDLE: {max_idle_autoreactivations}")
    if runtime_vram_control_enabled:
        print(
            "Polityka kolejki modeli: "
            f"max_wait={settings.model_queue_max_wait_seconds}s, "
            f"supervisor_min_free_vram={settings.supervisor_min_free_vram_mb}MB"
        )
    else:
        print("Polityka kolejki modeli: WYŁĄCZONA (-vram-off)")
    latest_summary = repository.latest_memory(kind="session_summary", source=None)
    if latest_summary is not None:
        print(f"Wczytano punkt startowy: source={latest_summary.source}")
    if not ollama.ping():
        print(
            "Uwaga: nie można połączyć z Ollama. "
            "Uruchom `ollama serve` i upewnij się, że model jest dostępny."
        )
    try:
        if args.ui == "textual":
            run_textual_cli(
                chat_service=chat_service,
                supervisor_dialogue_log_path=settings.supervisor_dialogue_log_path,
                shell_policy_path=settings.shell_policy_path,
                router_mailbox_log_path=router_mailbox_log_path,
            )
        else:
            run_cli(
                chat_service,
                shell_policy_path=settings.shell_policy_path,
                autonomous_mode=settings.autonomous_mode,
                max_idle_autoreactivations=max_idle_autoreactivations,
                router_mailbox_log_path=router_mailbox_log_path,
            )
    except KeyboardInterrupt:
        print("\nZamknięto sesję (Ctrl+C).")
        activity_logger.log(
            action="session.interrupt",
            intent="Zakończenie sesji przez Ctrl+C poza pętlą wejścia CLI.",
        )


if __name__ == "__main__":
    main()
