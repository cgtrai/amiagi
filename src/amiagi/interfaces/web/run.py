"""Web interface entry point — ``run_web()`` function.

Called from ``main.py`` when ``--ui web`` is passed.  Wires up the
RouterEngine, EventBus, WebAdapter, and starts the Uvicorn ASGI server.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from amiagi.application.agent_factory import AgentFactory
    from amiagi.application.agent_registry import AgentRegistry
    from amiagi.application.alert_manager import AlertManager
    from amiagi.application.audit_chain import AuditChain
    from amiagi.application.budget_manager import BudgetManager
    from amiagi.application.chat_service import ChatService
    from amiagi.application.context_window_manager import ContextWindowManager
    from amiagi.application.cross_agent_memory import CrossAgentMemory
    from amiagi.application.dynamic_scaler import DynamicScaler
    from amiagi.application.permission_enforcer import PermissionEnforcer
    from amiagi.application.skill_catalog import SkillCatalog
    from amiagi.application.task_queue import TaskQueue
    from amiagi.application.team_composer import TeamComposer
    from amiagi.application.work_assigner import WorkAssigner
    from amiagi.application.workflow_engine import WorkflowEngine
    from amiagi.config import Settings
    from amiagi.domain.quota_policy import QuotaPolicy
    from amiagi.infrastructure.activity_logger import ActivityLogger
    from amiagi.infrastructure.knowledge_base import KnowledgeBase
    from amiagi.infrastructure.metrics_collector import MetricsCollector
    from amiagi.infrastructure.rate_limiter import RateLimiter
    from amiagi.infrastructure.rest_server import RESTServer
    from amiagi.infrastructure.sandbox_manager import SandboxManager
    from amiagi.infrastructure.secret_vault import SecretVault
    from amiagi.infrastructure.session_replay import SessionReplay
    from amiagi.infrastructure.shared_workspace import SharedWorkspace
    from amiagi.infrastructure.vram_scheduler import VRAMScheduler
    from amiagi.infrastructure.webhook_dispatcher import WebhookDispatcher
    from amiagi.infrastructure.workflow_checkpoint import WorkflowCheckpoint
    from amiagi.interfaces.team_dashboard import TeamDashboard

logger = logging.getLogger(__name__)


def run_web(
    *,
    settings: "Settings",
    chat_service: "ChatService",
    activity_logger: "ActivityLogger | None" = None,
    agent_registry: "AgentRegistry | None" = None,
    agent_factory: "AgentFactory | None" = None,
    task_queue: "TaskQueue | None" = None,
    work_assigner: "WorkAssigner | None" = None,
    metrics_collector: "MetricsCollector | None" = None,
    alert_manager: "AlertManager | None" = None,
    session_replay: "SessionReplay | None" = None,
    shared_workspace: "SharedWorkspace | None" = None,
    knowledge_base: "KnowledgeBase | None" = None,
    cross_memory: "CrossAgentMemory | None" = None,
    context_window_manager: "ContextWindowManager | None" = None,
    permission_enforcer: "PermissionEnforcer | None" = None,
    sandbox_manager: "SandboxManager | None" = None,
    secret_vault: "SecretVault | None" = None,
    audit_chain: "AuditChain | None" = None,
    workflow_engine: "WorkflowEngine | None" = None,
    workflow_checkpoint: "WorkflowCheckpoint | None" = None,
    budget_manager: "BudgetManager | None" = None,
    quota_policy: "QuotaPolicy | None" = None,
    rate_limiter: "RateLimiter | None" = None,
    vram_scheduler: "VRAMScheduler | None" = None,
    rest_server: "RESTServer | None" = None,
    webhook_dispatcher: "WebhookDispatcher | None" = None,
    team_composer: "TeamComposer | None" = None,
    skill_catalog: "SkillCatalog | None" = None,
    team_dashboard: "TeamDashboard | None" = None,
    eval_runner: Any = None,
    benchmark_suite: Any = None,
    regression_detector: Any = None,
) -> None:
    """Wire up and start the web interface (blocking)."""
    import uvicorn

    from amiagi.application.event_bus import EventBus
    from amiagi.application.router_engine import RouterEngine
    from amiagi.application.shell_policy import default_shell_policy, load_shell_policy
    from amiagi.infrastructure.script_executor import ScriptExecutor
    from amiagi.interfaces.permission_manager import PermissionManager
    from amiagi.interfaces.web.app import create_app
    from amiagi.interfaces.web.web_adapter import WebAdapter

    # -- EventBus -----------------------------------------------------------
    event_bus = EventBus()

    # -- Script executor & shell policy -------------------------------------
    script_executor = ScriptExecutor()
    try:
        shell_policy = load_shell_policy(settings.shell_policy_path)
    except Exception:
        shell_policy = default_shell_policy()

    # -- RouterEngine -------------------------------------------------------
    router_engine = RouterEngine(
        chat_service=chat_service,
        permission_manager=PermissionManager(),
        script_executor=script_executor,
        work_dir=settings.work_dir,
        shell_policy_path=settings.shell_policy_path,
        event_bus=event_bus,
        activity_logger=activity_logger,
        settings=settings,
        autonomous_mode=settings.autonomous_mode,
        router_mailbox_log_path=settings.router_mailbox_log_path,
        supervisor_dialogue_log_path=settings.supervisor_dialogue_log_path,
        permission_enforcer=permission_enforcer,
        audit_chain=audit_chain,
    )

    # -- WebAdapter ---------------------------------------------------------
    web_adapter = WebAdapter(
        event_bus=event_bus,
        router_engine=router_engine,
    )

    # -- Starlette app ------------------------------------------------------
    app = create_app(
        settings=settings,
        web_adapter=web_adapter,
        agent_registry=agent_registry,
        agent_factory=agent_factory,
        task_queue=task_queue,
        work_assigner=work_assigner,
        metrics_collector=metrics_collector,
        alert_manager=alert_manager,
        session_replay=session_replay,
        shared_workspace=shared_workspace,
        knowledge_base=knowledge_base,
        budget_manager=budget_manager,
        cross_memory=cross_memory,
        team_composer=team_composer,
        skill_catalog=skill_catalog,
        team_dashboard=team_dashboard,
        workflow_engine=workflow_engine,
        secret_vault=secret_vault,
        audit_chain=audit_chain,
        eval_runner=eval_runner,
        benchmark_suite=benchmark_suite,
        regression_detector=regression_detector,
    )

    # -- Start Uvicorn ------------------------------------------------------
    port = settings.dashboard_port
    url = f"http://localhost:{port}"
    logger.info("Starting amiagi web GUI on %s", url)
    print(f"\n  🌐 amiagi Web GUI: {url}\n")

    # Auto-open the browser after a short delay (non-blocking).
    import threading
    import webbrowser

    def _open_browser() -> None:
        import time
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
