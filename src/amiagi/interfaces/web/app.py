"""Starlette ASGI application factory for the amiagi web interface."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.websockets import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from amiagi.config import Settings
    from amiagi.interfaces.web.web_adapter import WebAdapter

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"


# ------------------------------------------------------------------
# WebSocket endpoint for events
# ------------------------------------------------------------------

async def _ws_events(websocket: WebSocket) -> None:
    """Global event stream WebSocket (``/ws/events``).

    Requires a valid JWT token passed as ``?token=<jwt>`` query param.
    Unauthenticated connections are closed with code 4001.
    """
    # --- JWT auth ---
    # Prefer query-param token; fall back to HttpOnly session cookie
    # (the cookie is httponly so JS cannot read it, but the browser
    # sends it along with the WebSocket handshake request).
    token = websocket.query_params.get("token") or ""
    if not token:
        token = websocket.cookies.get("amiagi_session", "")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    session_mgr = getattr(websocket.app.state, "session_manager", None)
    if session_mgr is None:
        await websocket.close(code=4001, reason="Auth unavailable")
        return

    session = await session_mgr.validate_session(token)
    if session is None:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    hub = websocket.app.state.event_hub
    await hub.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle client pings
            try:
                import json as _json
                msg = _json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(_json.dumps({"type": "pong"}))
            except (ValueError, TypeError):
                pass
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(websocket)


# ------------------------------------------------------------------
# Application factory
# ------------------------------------------------------------------

def create_app(
    *,
    settings: "Settings",
    web_adapter: "WebAdapter",
    **extra: Any,
) -> Starlette:
    """Build and return a fully configured Starlette ASGI application.

    Parameters
    ----------
    settings:
        Application settings (database, ports, etc.).
    web_adapter:
        Bridge between EventBus/RouterEngine and the WebSocket hub.
    **extra:
        Additional services to store on ``app.state`` (e.g. agent_registry).
    """
    from amiagi.interfaces.web.db.pool import close_pool, create_pool, run_migrations
    from amiagi.interfaces.web.routes.admin_routes import admin_routes
    from amiagi.interfaces.web.routes.agent_config_routes import agent_config_routes
    from amiagi.interfaces.web.routes.api_routes import api_routes
    from amiagi.interfaces.web.routes.auth_routes import auth_routes
    from amiagi.interfaces.web.routes.dashboard_routes import dashboard_routes
    from amiagi.interfaces.web.routes.health_routes import health_routes
    from amiagi.interfaces.web.routes.model_routes import model_routes
    from amiagi.interfaces.web.routes.skill_admin_routes import skill_admin_routes
    from amiagi.interfaces.web.routes.team_routes import team_routes
    from amiagi.interfaces.web.routes.workspace_routes import workspace_routes
    from amiagi.interfaces.web.routes.prompt_routes import prompt_routes
    from amiagi.interfaces.web.routes.search_routes import search_routes
    from amiagi.interfaces.web.routes.snippet_routes import snippet_routes
    from amiagi.interfaces.web.routes.monitoring_routes import monitoring_routes
    from amiagi.interfaces.web.routes.template_routes import template_routes
    from amiagi.interfaces.web.routes.i18n_routes import i18n_routes
    from amiagi.interfaces.web.routes.memory_routes import memory_routes
    from amiagi.interfaces.web.routes.cron_routes import cron_routes
    from amiagi.interfaces.web.routes.inbox_routes import inbox_routes
    from amiagi.interfaces.web.routes.system_routes import system_routes
    from amiagi.interfaces.web.routes.model_hub_routes import model_hub_routes
    from amiagi.interfaces.web.routes.budget_routes import budget_routes
    from amiagi.interfaces.web.routes.vault_routes import vault_routes
    from amiagi.interfaces.web.routes.workflow_routes import workflow_routes
    from amiagi.interfaces.web.routes.eval_routes import eval_routes
    from amiagi.interfaces.web.routes.knowledge_routes import knowledge_routes
    from amiagi.interfaces.web.routes.sandbox_routes import sandbox_routes
    from amiagi.interfaces.web.ws.agent_stream import ws_agent_stream
    from amiagi.interfaces.web.ws.event_hub import EventHub

    # -- Routes -----------------------------------------------------------
    routes: list[Route | Mount | WebSocketRoute] = [
        *health_routes,
        *auth_routes,
        *admin_routes,
        *skill_admin_routes,
        *api_routes,
        *team_routes,
        *model_routes,
        *agent_config_routes,
        *dashboard_routes,
        *workspace_routes,
        *prompt_routes,
        *search_routes,
        *snippet_routes,
        *monitoring_routes,
        *template_routes,
        *i18n_routes,
        *memory_routes,
        *cron_routes,
        *inbox_routes,
        *system_routes,
        *model_hub_routes,
        *budget_routes,
        *vault_routes,
        *workflow_routes,
        *eval_routes,
        *knowledge_routes,
        *sandbox_routes,
        WebSocketRoute("/ws/events", _ws_events),
        WebSocketRoute("/ws/agent/{agent_id}", ws_agent_stream),
    ]

    # Mount static files if directory exists
    if _STATIC_DIR.exists():
        routes.append(
            Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static"),
        )

    # -- Startup / shutdown hooks -----------------------------------------

    async def on_startup() -> None:
        # 0. Record startup time for uptime tracking
        app.state._startup_time = time.time()

        # 1. Database pool (PostgreSQL or SQLite fallback)
        pool = await create_pool(settings)
        app.state.db_pool = pool
        await run_migrations(pool, schema=settings.db_schema)

        # 2. Session manager
        from amiagi.interfaces.web.auth.session import SessionManager

        secret_key = settings.oauth_client_secret or "amiagi-dev-secret-change-me"
        session_mgr = SessionManager(secret_key=secret_key, pool=pool)
        app.state.session_manager = session_mgr

        # 3. RBAC repository
        from amiagi.interfaces.web.rbac.repository import RbacRepository

        app.state.rbac_repo = RbacRepository(pool)

        # 3b. Binary store for file management
        from amiagi.interfaces.web.files.binary_store import BinaryStore

        workspace_base = getattr(settings, "workspace_base_dir", None) or "data/workspaces"
        app.state.binary_store = BinaryStore(pool, workspace_base)

        # 3c. Activity logger (audit trail)
        from amiagi.interfaces.web.audit.activity_logger import WebActivityLogger

        app.state.activity_logger = WebActivityLogger(pool)

        # 3d. Workspace manager (per-user directories)
        from amiagi.interfaces.web.audit.workspace_manager import WorkspaceManager

        app.state.workspace_manager = WorkspaceManager(workspace_base)

        # 3e. Skill repository and selector
        from amiagi.interfaces.web.skills.skill_repository import SkillRepository
        from amiagi.interfaces.web.skills.skill_selector import SkillSelector

        app.state.skill_repository = SkillRepository(pool)
        app.state.skill_selector = SkillSelector(pool)

        # 3f. Productivity: prompts, search, snippets
        from amiagi.interfaces.web.productivity.prompt_repository import PromptRepository
        from amiagi.interfaces.web.productivity.search_service import SearchService
        from amiagi.interfaces.web.productivity.snippet_repository import SnippetRepository

        app.state.prompt_repository = PromptRepository(pool)
        app.state.search_service = SearchService(pool)
        app.state.snippet_repository = SnippetRepository(pool)

        # 3g. Monitoring: performance, notifications, sessions, API keys, webhooks
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceTracker
        from amiagi.interfaces.web.monitoring.notification_service import NotificationService
        from amiagi.interfaces.web.monitoring.session_recorder import SessionRecorder
        from amiagi.interfaces.web.monitoring.api_key_manager import ApiKeyManager
        from amiagi.interfaces.web.monitoring.webhook_manager import WebhookManager

        app.state.performance_tracker = PerformanceTracker(pool)
        app.state.notification_service = NotificationService(pool)
        app.state.session_recorder = SessionRecorder(pool)
        app.state.api_key_manager = ApiKeyManager(pool)
        app.state.webhook_manager = WebhookManager(pool)

        # 3g-ii. Inbox (Human-in-the-Loop)
        from amiagi.interfaces.web.monitoring.inbox_service import InboxService

        app.state.inbox_service = InboxService(pool)

        # 3g-iii. Wire WorkflowEngine → InboxService (gate → inbox item)
        _wf_engine = extra.get("workflow_engine")
        if _wf_engine is not None:
            app.state.workflow_engine = _wf_engine
            _inbox_svc: InboxService = app.state.inbox_service
            _wf_loop = asyncio.get_running_loop()

            def _gate_to_inbox(node, run) -> None:
                """on_gate_waiting callback: create inbox item for gate."""
                try:
                    asyncio.run_coroutine_threadsafe(
                        _inbox_svc.create(
                            item_type="gate_approval",
                            title=f"Gate: {node.node_id}",
                            body=f"Workflow '{run.workflow.name}' is waiting "
                                 f"for approval at gate '{node.node_id}'.",
                            source_type="workflow",
                            source_id=run.run_id,
                            node_id=node.node_id,
                            priority=5,
                            metadata={"workflow_name": run.workflow.name},
                        ),
                        _wf_loop,
                    )
                    # Broadcast so inbox UI updates in real-time
                    _hub = getattr(app.state, "event_hub", None)
                    if _hub is not None:
                        _hub.broadcast("inbox.new", {
                            "node_id": node.node_id,
                            "run_id": run.run_id,
                        })
                except Exception:
                    logger.debug("Failed to create inbox item for gate %s", node.node_id, exc_info=True)

            _wf_engine._on_gate_waiting = _gate_to_inbox
            logger.info("WorkflowEngine.on_gate_waiting wired to InboxService")

        # 3h. Task templates
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository

        app.state.template_repository = TaskTemplateRepository(pool)

        # 3i. Eval + Knowledge repositories (DB-backed persistence)
        from amiagi.interfaces.web.db.eval_repository import EvalRepository
        from amiagi.interfaces.web.db.knowledge_repository import KnowledgeRepository

        app.state.eval_repo = EvalRepository(pool)
        app.state.knowledge_repo = KnowledgeRepository(pool)

        # 3. EventHub (WebSocket broadcast)
        hub = EventHub()
        app.state.event_hub = hub
        hub.start_heartbeat()

        # 3i. Cron scheduler
        from amiagi.interfaces.web.scheduling.cron_scheduler import CronScheduler

        cron = CronScheduler(pool, schema=settings.db_schema)
        await cron.load_jobs()
        cron.start()
        app.state.cron_scheduler = cron

        # 4. Wire adapter ↔ hub
        loop = asyncio.get_running_loop()
        web_adapter.set_event_hub(hub)
        web_adapter.set_loop(loop)
        web_adapter.start()

        # 4a. Wire HumanInteractionBridge (AskHumanTool + ReviewRequestTool)
        from amiagi.application.human_tools import HumanInteractionBridge

        _human_bridge = HumanInteractionBridge(
            inbox_service=app.state.inbox_service,
            event_hub=hub,
            loop=loop,
        )
        app.state.human_bridge = _human_bridge

        # Inject into RouterEngine if available
        _router_engine = extra.get("router_engine")
        if _router_engine is not None:
            _router_engine._human_bridge = _human_bridge
            logger.info("HumanInteractionBridge wired to RouterEngine")

        # 4a-ii. SandboxMonitor (resource tracking + execution logging)
        _sandbox_mgr = extra.get("sandbox_manager")
        if _sandbox_mgr is not None:
            from amiagi.interfaces.web.monitoring.sandbox_monitor import SandboxMonitor

            _sandbox_monitor = SandboxMonitor(
                _sandbox_mgr, pool, scan_interval=300,  # scan every 5 min
            )
            _sandbox_monitor.start(loop)
            app.state.sandbox_monitor = _sandbox_monitor
            app.state.sandbox_manager = _sandbox_mgr
            logger.info("SandboxMonitor started (scan interval: 300s)")

        # 4b. Wire PerformanceTracker to EventBus CycleFinishedEvent
        from amiagi.interfaces.web.monitoring.performance_tracker import PerformanceTracker as _PT  # noqa: F811
        _perf_tracker: _PT = app.state.performance_tracker
        _event_bus = web_adapter._event_bus

        def _on_cycle_record_perf(event: Any) -> None:
            """Auto-record a performance entry when a cycle finishes."""
            try:
                asyncio.run_coroutine_threadsafe(
                    _perf_tracker.record(
                        agent_role="router",
                        task_type="cycle",
                        success="błęd" not in getattr(event, "event", "").lower(),
                    ),
                    loop,
                )
            except Exception:
                logger.debug("Failed to auto-record performance on CycleFinished", exc_info=True)

        from amiagi.application.event_bus import CycleFinishedEvent as _CFE
        _event_bus.on(_CFE, _on_cycle_record_perf)
        app.state._perf_cycle_handler = _on_cycle_record_perf  # prevent GC

        # 4c. Wire SessionEventBuffer — auto-flush session events every 5s
        from amiagi.interfaces.web.monitoring.session_recorder import SessionEventBuffer
        from amiagi.application.event_bus import (
            LogEvent as _LE,
            ActorStateEvent as _ASE,
            ErrorEvent as _EE,
        )
        _session_buf = SessionEventBuffer(app.state.session_recorder, flush_interval=5.0)
        _session_buf.start(loop)
        app.state._session_event_buffer = _session_buf

        for _evt_cls in (_LE, _ASE, _CFE, _EE):
            _event_bus.on(_evt_cls, _session_buf.on_event)

        # 5. Jinja2 templates with i18n context injection
        from amiagi.interfaces.web.i18n_web import make_translator

        _base_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

        class _I18nTemplates:
            """Thin wrapper that injects ``_()`` and ``lang`` into every
            TemplateResponse context so Jinja2 templates can use
            ``{{ _("key") }}`` for translations.

            Also populates status-bar variables from live services so that
            the initial HTML render shows real data (not just defaults).
            """

            def __init__(self, base: Jinja2Templates) -> None:
                self._base = base

            @property
            def env(self):  # type: ignore[override]
                return self._base.env

            def _status_bar_context(self, request) -> dict:
                """Collect live status-bar variables from app.state services."""
                ctx: dict = {}
                state = request.app.state

                # ── Model name & config ──
                try:
                    settings = getattr(state, "settings", None)
                    model_cfg_path = Path("data/model_config.json")
                    if model_cfg_path.exists():
                        import json as _json
                        with open(model_cfg_path) as f:
                            mcfg = _json.load(f)
                        ctx["model_name"] = mcfg.get("polluks_model") or mcfg.get("kastor_model") or "—"
                    else:
                        ctx["model_name"] = "—"
                except Exception:
                    ctx["model_name"] = "—"

                # ── Budget (session-level) ──
                budget_mgr = getattr(state, "budget_manager", None)
                if budget_mgr is not None:
                    try:
                        sb = budget_mgr.session_budget
                        ctx["budget_pct"] = round(sb.utilization_pct, 1)
                        ctx["budget_used"] = f"{sb.spent_usd:.2f}"
                        lim = sb.limit_usd
                        ctx["budget_limit"] = f"{lim:.2f}" if lim > 0 else "∞"
                        ctx["token_count"] = sb.tokens_used
                    except Exception:
                        pass

                # ── Active tasks ──
                task_queue = getattr(state, "task_queue", None)
                if task_queue is not None:
                    try:
                        ctx["active_tasks"] = task_queue.pending_count()
                    except Exception:
                        ctx["active_tasks"] = 0

                # ── Inbox pending (notifications) ──
                notif_svc = getattr(state, "notification_service", None)
                if notif_svc is not None:
                    try:
                        import asyncio as _aio
                        loop = _aio.get_event_loop()
                        if loop.is_running():
                            ctx["inbox_pending"] = "…"
                        else:
                            ctx["inbox_pending"] = 0
                    except Exception:
                        ctx["inbox_pending"] = 0
                else:
                    ctx["inbox_pending"] = 0

                # ── Uptime ──
                startup_ts = getattr(state, "_startup_time", None)
                if startup_ts is not None:
                    elapsed = int(time.time() - startup_ts)
                    if elapsed < 60:
                        ctx["uptime"] = f"{elapsed}s"
                    elif elapsed < 3600:
                        ctx["uptime"] = f"{elapsed // 60}m"
                    else:
                        h, m = divmod(elapsed // 60, 60)
                        ctx["uptime"] = f"{h}h {m}m"
                else:
                    ctx["uptime"] = "0m"

                return ctx

            def TemplateResponse(self, request, name, context=None, **kwargs):
                ctx = dict(context) if context else {}
                translator, lang = make_translator(request)
                ctx.setdefault("_", translator)
                ctx.setdefault("lang", lang)
                # Inject status-bar data (can be overridden by route-level ctx)
                for k, v in self._status_bar_context(request).items():
                    ctx.setdefault(k, v)
                return self._base.TemplateResponse(request, name, ctx, **kwargs)

        templates = _I18nTemplates(_base_templates)
        app.state.templates = templates

        # 6. Store references on app state
        app.state.web_adapter = web_adapter
        app.state.settings = settings
        for key, value in extra.items():
            setattr(app.state, key, value)

        # 7. Wire SecretVault → database persistence (if vault + pool available)
        _vault = getattr(app.state, "secret_vault", None)
        if _vault is not None and pool is not None:
            _vault.attach_db(pool)
            # Migrate any existing file-based secrets into DB, then sync back
            try:
                migrated = await _vault.migrate_file_to_db()
                if migrated:
                    logger.info("Vault: migrated %d file-based secrets → DB", migrated)
                await _vault.sync_from_db()
            except Exception:
                logger.warning("Vault: DB sync failed — falling back to file-based", exc_info=True)

        logger.info(
            "amiagi web server started — port %s, schema %s",
            settings.dashboard_port,
            settings.db_schema,
        )

    async def on_shutdown() -> None:
        web_adapter.stop()
        # Final flush of session event buffer
        _buf = getattr(app.state, "_session_event_buffer", None)
        if _buf is not None:
            await _buf.stop()
        hub = getattr(app.state, "event_hub", None)
        if hub is not None:
            hub.stop_heartbeat()
        cron = getattr(app.state, "cron_scheduler", None)
        if cron is not None:
            cron.stop()
        pool = getattr(app.state, "db_pool", None)
        if pool is not None:
            await close_pool(pool)
        logger.info("amiagi web server stopped.")

    # -- Build Starlette app ----------------------------------------------
    from starlette.middleware import Middleware
    from amiagi.interfaces.web.auth.middleware import AuthMiddleware

    app = Starlette(
        routes=routes,
        middleware=[Middleware(AuthMiddleware)],
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
        debug=False,
    )

    return app
