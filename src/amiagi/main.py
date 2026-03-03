from __future__ import annotations

import argparse
from dataclasses import is_dataclass, replace
from pathlib import Path

from amiagi.application.agent_factory import AgentFactory
from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.alert_manager import AlertManager
from amiagi.application.audit_chain import AuditChain
from amiagi.application.chat_service import ChatService
from amiagi.application.context_compressor import ContextCompressor
from amiagi.application.context_window_manager import ContextWindowManager
from amiagi.application.cross_agent_memory import CrossAgentMemory
from amiagi.application.discussion_sync import extract_dialogue_without_code
from amiagi.application.model_queue_policy import ModelQueuePolicy
from amiagi.application.permission_enforcer import PermissionEnforcer
from amiagi.application.skills_loader import SkillsLoader
from amiagi.application.supervisor_service import SupervisorService
from amiagi.application.task_queue import TaskQueue
from amiagi.application.work_assigner import WorkAssigner
from amiagi.application.workflow_engine import WorkflowEngine
from amiagi.config import Settings
from amiagi.domain.agent import AgentRole
from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.knowledge_base import KnowledgeBase
from amiagi.infrastructure.lifecycle_logger import LifecycleLogger
from amiagi.infrastructure.memory_repository import MemoryRepository
from amiagi.infrastructure.metrics_collector import MetricsCollector
from amiagi.infrastructure.model_io_logger import ModelIOLogger
from amiagi.infrastructure.ollama_client import OllamaClient
from amiagi.infrastructure.sandbox_manager import SandboxManager
from amiagi.infrastructure.secret_vault import SecretVault
from amiagi.infrastructure.session_replay import SessionReplay
from amiagi.infrastructure.shared_workspace import SharedWorkspace
from amiagi.infrastructure.vram_advisor import VramAdvisor
from amiagi.infrastructure.session_model_config import SessionModelConfig
from amiagi.infrastructure.workflow_checkpoint import WorkflowCheckpoint
# Phase 8
from amiagi.application.budget_manager import BudgetManager
from amiagi.domain.quota_policy import QuotaPolicy
from amiagi.infrastructure.energy_cost_tracker import EnergyCostTracker
from amiagi.infrastructure.gpu_power_monitor import GpuPowerMonitor
from amiagi.infrastructure.rate_limiter import RateLimiter
from amiagi.infrastructure.vram_scheduler import VRAMScheduler
# Phase 9
from amiagi.domain.eval_rubric import EvalRubric
from amiagi.application.eval_runner import EvalRunner
from amiagi.application.regression_detector import RegressionDetector
from amiagi.infrastructure.benchmark_suite import BenchmarkSuite
from amiagi.interfaces.human_feedback import HumanFeedbackCollector
# Phase 10
from amiagi.application.plugin_loader import PluginLoader
from amiagi.infrastructure.ci_adapter import CIAdapter
from amiagi.infrastructure.rest_server import RESTServer
from amiagi.infrastructure.webhook_dispatcher import WebhookDispatcher
# Phase 11
from amiagi.application.dynamic_scaler import DynamicScaler
from amiagi.application.skill_catalog import SkillCatalog
from amiagi.application.team_composer import TeamComposer
from amiagi.interfaces.team_dashboard import TeamDashboard
from amiagi.interfaces.cli import run_cli
from amiagi.interfaces.textual_cli import run_textual_cli
from amiagi.i18n import _, set_language, available_languages


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=_("main.arg.description"))
    parser.add_argument(
        "-cs",
        "--cold_start",
        action="store_true",
        help=_("main.arg.cold_start"),
    )
    parser.add_argument(
        "-auto",
        "--auto",
        action="store_true",
        help=_("main.arg.auto"),
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
        help=_("main.arg.startup_dialogue"),
    )
    parser.add_argument(
        "--ui",
        choices=("cli", "textual"),
        default="cli",
        help=_("main.arg.ui"),
    )
    parser.add_argument(
        "--lang",
        choices=available_languages(),
        default=None,
        help=_("main.arg.lang"),
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
    if args.lang:
        set_language(args.lang)
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
        # Clear persisted model assignment and input history
        SessionModelConfig.clear(settings.model_config_path)
        if settings.input_history_path.exists():
            settings.input_history_path.unlink(missing_ok=True)
        activity_logger.log(
            action="startup.cold_start",
            intent="Wyczyszczenie historii, logów JSONL, konfiguracji modeli i historii poleceń.",
            details={
                "db_path": str(settings.db_path),
                "executor_model_io_log_path": str(executor_log_path),
                "supervisor_model_io_log_path": str(supervisor_log_path),
                "activity_log_path": str(settings.activity_log_path),
                "model_config_path": str(settings.model_config_path),
                "input_history_path": str(settings.input_history_path),
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
        model="",  # Placeholder — the UI wizard will set the actual model.
        io_logger=io_logger,
        activity_logger=activity_logger,
        request_timeout_seconds=settings.ollama_request_timeout_seconds,
        max_retries=settings.ollama_max_retries,
        retry_backoff_seconds=settings.ollama_retry_backoff_seconds,
        client_role="executor",
        queue_policy=queue_policy,
        vram_advisor=vram_advisor,
    )

    # Model auto-select removed — the startup wizard in the UI handles
    # interactive model selection for both Polluks (executor) and Kastor
    # (supervisor).  See _run_model_selection_wizard() in textual_cli.py.

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
                model_client=supervisor_client,
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

    skills_loader = SkillsLoader(skills_dir=settings.skills_dir)

    chat_service = ChatService(
        memory_repository=repository,
        model_client=ollama,
        max_context_memories=settings.max_context_memories,
        activity_logger=activity_logger,
        vram_advisor=vram_advisor,
        work_dir=settings.work_dir,
        supervisor_service=supervisor_service,
        skills_loader=skills_loader,
        energy_tracker=EnergyCostTracker(gpu_monitor=GpuPowerMonitor()),
    )

    # --- Populate supervisor's sponsor_task from startup dialogue ---
    if supervisor_service is not None:
        sponsor_task = chat_service.extract_sponsor_task()
        if sponsor_task:
            supervisor_service.sponsor_task = sponsor_task
            activity_logger.log(
                action="supervisor.sponsor_task_loaded",
                intent="Załadowano zadanie Sponsora do kontekstu nadzorcy.",
                details={"chars": len(sponsor_task)},
            )

    # ------------------------------------------------------------------
    # Agent Registry, Factory & Lifecycle (Phase 1)
    # ------------------------------------------------------------------
    lifecycle_logger = LifecycleLogger(settings.agent_lifecycle_log_path)
    agent_registry = AgentRegistry(lifecycle_logger=lifecycle_logger)
    agent_factory = AgentFactory(
        registry=agent_registry,
        memory_repository=repository,
        activity_logger=activity_logger,
        lifecycle_logger=lifecycle_logger,
        skills_loader=skills_loader,
        work_dir=settings.work_dir,
    )

    # Wrap existing Polluks (executor) and Kastor (supervisor) as registered agents
    polluks_runtime = agent_factory.create_from_existing(
        agent_id="polluks",
        name="Polluks",
        role=AgentRole.EXECUTOR,
        chat_service=chat_service,
        supervisor_service=supervisor_service,
        model_backend="ollama",
        model_name=getattr(ollama, "model", ""),
        metadata={"origin": "bootstrap", "persona": "executor"},
    )
    if supervisor_service is not None:
        agent_factory.create_from_existing(
            agent_id="kastor",
            name="Kastor",
            role=AgentRole.SUPERVISOR,
            chat_service=None,
            supervisor_service=supervisor_service,
            model_backend="ollama",
            model_name=settings.supervisor_model,
            metadata={"origin": "bootstrap", "persona": "supervisor"},
        )

    # ------------------------------------------------------------------
    # Task Queue & Work Distribution (Phase 3)
    # ------------------------------------------------------------------
    task_queue = TaskQueue()
    work_assigner = WorkAssigner(registry=agent_registry, task_queue=task_queue)

    # ------------------------------------------------------------------
    # Observability & Dashboard (Phase 4)
    # ------------------------------------------------------------------
    metrics_collector = MetricsCollector(db_path=settings.metrics_db_path)
    alert_manager = AlertManager()
    session_replay = SessionReplay(log_dir=settings.activity_log_path.parent)

    # ------------------------------------------------------------------
    # Shared Context & Knowledge (Phase 5)
    # ------------------------------------------------------------------
    shared_workspace = SharedWorkspace(root=settings.shared_workspace_dir)
    knowledge_base = KnowledgeBase(db_path=settings.knowledge_base_path)
    cross_memory = CrossAgentMemory(persist_path=settings.cross_memory_path)
    context_compressor = ContextCompressor()
    context_window_mgr = ContextWindowManager(
        max_tokens=settings.context_window_max_tokens,
        compressor=context_compressor,
        cross_memory=cross_memory,
    )

    # ------------------------------------------------------------------
    # Security & Sandboxing (Phase 7)
    # ------------------------------------------------------------------
    permission_enforcer = PermissionEnforcer()
    sandbox_manager = SandboxManager(root=settings.sandbox_dir)
    secret_vault = SecretVault(vault_path=settings.vault_path)
    audit_chain = AuditChain(log_path=settings.audit_log_path)

    # ------------------------------------------------------------------
    # Workflow Engine (Phase 6)
    # ------------------------------------------------------------------
    workflow_engine = WorkflowEngine()
    workflow_checkpoint = WorkflowCheckpoint(
        checkpoint_dir=settings.workflow_checkpoint_dir,
    )

    # ------------------------------------------------------------------
    # Resource & Cost Governance (Phase 8)
    # ------------------------------------------------------------------
    budget_manager = BudgetManager()
    quota_policy = QuotaPolicy()
    if settings.quota_policy_path.exists():
        try:
            quota_policy = QuotaPolicy.load_json(settings.quota_policy_path)
        except Exception:  # noqa: BLE001
            pass
    rate_limiter = RateLimiter()
    vram_scheduler = VRAMScheduler()

    # ------------------------------------------------------------------
    # Evaluation & Quality (Phase 9)
    # ------------------------------------------------------------------
    eval_runner = EvalRunner(rubric=EvalRubric(name="default"))
    benchmark_suite = BenchmarkSuite(benchmarks_dir=settings.benchmarks_dir)
    regression_detector = RegressionDetector(baselines_dir=settings.baselines_dir)
    human_feedback = HumanFeedbackCollector(settings.feedback_path)

    # ------------------------------------------------------------------
    # External Integration & API (Phase 10)
    # ------------------------------------------------------------------
    rest_server = RESTServer(
        port=settings.rest_api_port,
        bearer_token=settings.rest_api_token,
    )
    webhook_dispatcher = WebhookDispatcher()
    plugin_loader = PluginLoader(plugins_dir=settings.plugins_dir)
    ci_adapter = CIAdapter()

    # Wire domain routes so /api start exposes real endpoints
    rest_server.wire_domain_routes(
        agent_registry=agent_registry,
        task_queue=task_queue,
        workflow_engine=workflow_engine,
        metrics_collector=metrics_collector,
        budget_manager=budget_manager,
    )

    # ------------------------------------------------------------------
    # Team Composition (Phase 11)
    # ------------------------------------------------------------------
    team_composer = TeamComposer(
        templates_dir=str(settings.teams_dir) if settings.teams_dir.is_dir() else None,
    )
    skill_catalog = SkillCatalog()
    dynamic_scaler = DynamicScaler()
    team_dashboard = TeamDashboard()

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
                activity_logger=activity_logger,
                settings=settings,
                agent_registry=agent_registry,
                agent_factory=agent_factory,
                task_queue=task_queue,
                work_assigner=work_assigner,
                metrics_collector=metrics_collector,
                alert_manager=alert_manager,
                session_replay=session_replay,
                shared_workspace=shared_workspace,
                knowledge_base=knowledge_base,
                cross_memory=cross_memory,
                context_window_manager=context_window_mgr,
                permission_enforcer=permission_enforcer,
                sandbox_manager=sandbox_manager,
                secret_vault=secret_vault,
                audit_chain=audit_chain,
                workflow_engine=workflow_engine,
                workflow_checkpoint=workflow_checkpoint,
                # Phase 8
                budget_manager=budget_manager,
                quota_policy=quota_policy,
                rate_limiter=rate_limiter,
                vram_scheduler=vram_scheduler,
                # Phase 9
                eval_runner=eval_runner,
                benchmark_suite=benchmark_suite,
                regression_detector=regression_detector,
                human_feedback=human_feedback,
                # Phase 10
                rest_server=rest_server,
                webhook_dispatcher=webhook_dispatcher,
                plugin_loader=plugin_loader,
                ci_adapter=ci_adapter,
                # Phase 11
                team_composer=team_composer,
                skill_catalog=skill_catalog,
                dynamic_scaler=dynamic_scaler,
                team_dashboard=team_dashboard,
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
