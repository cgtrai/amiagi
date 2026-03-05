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
    import asyncpg

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
    token = websocket.query_params.get("token")
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

        # 1. Database pool
        pool: asyncpg.Pool = await create_pool(settings)
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

        # 3h. Task templates
        from amiagi.interfaces.web.task_templates.template_repository import TaskTemplateRepository

        app.state.template_repository = TaskTemplateRepository(pool)

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

        # 5. Jinja2 templates
        templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
        app.state.templates = templates

        # 6. Store references on app state
        app.state.web_adapter = web_adapter
        app.state.settings = settings
        for key, value in extra.items():
            setattr(app.state, key, value)

        # 7. Wire AuthMiddleware now that SessionManager exists
        from amiagi.interfaces.web.auth.middleware import AuthMiddleware
        app.add_middleware(AuthMiddleware, session_manager=session_mgr)

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
    app = Starlette(
        routes=routes,
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
        debug=False,
    )

    return app
