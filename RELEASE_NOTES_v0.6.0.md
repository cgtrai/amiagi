# amiagi v0.6.0

Release implementing Phases 1–4 of the v1.0 Roadmap: Agent Registry & Lifecycle, AgentWizard, Task Queue & Work Distribution, and Observability & Dashboard.

## Highlights

### Faza 1 — Agent Registry & Lifecycle

- **`AgentDescriptor`** (domain): dataclass with validated state transitions (`IDLE → WORKING → IDLE`, `IDLE ↔ PAUSED`, `WORKING → ERROR → IDLE`, `TERMINATED` is final). Roles: `EXECUTOR`, `SUPERVISOR`, `SPECIALIST`.
- **`AgentRegistry`** (application): thread-safe registry — `register()`, `unregister()`, `get()`, `list_all()`, `list_by_role()`, `list_by_state()`, `update_state()`. Emits lifecycle events via LifecycleLogger.
- **`AgentRuntime`** (infrastructure): wraps `ChatService` + `AgentDescriptor`. Methods: `ask()`, `pause()`, `resume()`, `terminate()`, `spawn()`. Lifecycle hook lists: `on_spawn`, `on_pause`, `on_resume`, `on_terminate`, `on_error`.
- **`AgentFactory`** (application): `create_agent(descriptor, client)` builds full runtime + registers. `create_from_existing(agent_id, name, role, chat_service)` wraps legacy services (Polluks, Kastor).
- **`LifecycleLogger`** (infrastructure): JSONL event logger — `log(agent_id, event, details)`.
- **Migration**: Polluks and Kastor bootstrapped as agents in registry at startup.
- **TUI commands**: `/agents list`, `/agents info <id|name>`, `/agents pause <id>`, `/agents resume <id>`, `/agents terminate <id>`.

### Faza 2 — AgentWizard

- **`AgentBlueprint`** (domain): serializable dataclass with `to_dict()`/`from_dict()`. Includes `TestScenario` for validation.
- **`AgentWizardService`** (application): `generate_blueprint(need)` (LLM with heuristic fallback), `create_agent()`, `validate_agent()`, `save_blueprint()`/`load_blueprint()`/`list_blueprints()`. Enriches blueprints with skill and tool recommendations.
- **`PersonaGenerator`** (application): template + LLM persona generation.
- **`SkillRecommender`** (application): keyword heuristic + LLM skill recommendation from existing catalog.
- **`ToolRecommender`** (application): 14 built-in tools catalog, keyword + LLM recommendation.
- **`AgentTestRunner`** (application): scenario-based validation with keyword matching (50% threshold).
- **TUI commands**: `/agent-wizard create <description>`, `/agent-wizard blueprints`, `/agent-wizard load <name>`.

### Faza 3 — Task Queue & Work Distribution

- **`Task`** (domain): priority enum (`CRITICAL` > `HIGH` > `NORMAL` > `LOW`), status transitions (`PENDING → ASSIGNED → IN_PROGRESS → DONE/FAILED/CANCELLED`), dependency tracking.
- **`TaskQueue`** (application): thread-safe priority queue with dependency resolution — `enqueue()`, `dequeue_next(agent_skills)`, `get_ready_tasks()`, `mark_done()`, `mark_failed()`, `cancel()`, `stats()`.
- **`TaskDecomposer`** (application): LLM-based task → subtask decomposition with trivial fallback.
- **`WorkAssigner`** (application): skill-matching assignment of ready tasks to idle agents. First-idle-wins with single-use-per-tick guarantee.
- **`TaskScheduler`** (infrastructure): background thread scheduler with deadline escalation (CRITICAL within 5 min).
- **TUI commands**: `/tasks list`, `/tasks add <title>`, `/tasks info <id>`, `/tasks cancel <id>`, `/tasks stats`.

### Faza 4 — Observability & Dashboard

- **`MetricsCollector`** (infrastructure): in-memory ring buffer (10 000 points) + SQLite flush. `record()`, `query()`, `summary()` (count/sum/min/max/avg). Auto-flush every 500 data points.
- **`AlertManager`** (application): configurable alert rules with cooldown, severity levels (`INFO`, `WARNING`, `CRITICAL`), listener callbacks. Background evaluation loop.
- **`SessionReplay`** (infrastructure): loads 6 default JSONL log files, time-filtered replay with source filtering and limit.
- **`DashboardServer`** (infrastructure): HTTP + SSE server — endpoints: `/api/agents`, `/api/tasks`, `/api/metrics`, `/api/alerts`, `/api/events` (SSE), `/api/replay`, `/api/status`.
- **Dashboard UI** (`index.html`): vanilla JS single-page app, 4 panels (Agents, Tasks, Metrics, Alerts), dark theme, auto-refresh + SSE live events.
- **TUI commands**: `/dashboard start [port]`, `/dashboard stop`, `/dashboard status`.

### Integration

- **`main.py`**: full bootstrap sequence — LifecycleLogger → AgentRegistry → AgentFactory → Polluks/Kastor registration → TaskQueue → WorkAssigner → MetricsCollector → AlertManager → SessionReplay.
- **`textual_cli.py`**: 4 new command handlers (`/agents`, `/agent-wizard`, `/tasks`, `/dashboard`) with full sub-command parsing and table-formatted output.
- **`config.py`**: 4 new settings — `agent_lifecycle_log_path`, `blueprints_dir`, `metrics_db_path`, `dashboard_port`.

## New files (21 modules + 1 static asset)

| Layer | File | Purpose |
|-------|------|---------|
| domain | `agent.py` | AgentState, AgentRole, AgentDescriptor |
| domain | `blueprint.py` | AgentBlueprint, TestScenario |
| domain | `task.py` | TaskPriority, TaskStatus, Task |
| application | `agent_registry.py` | Thread-safe agent CRUD + lifecycle events |
| application | `agent_factory.py` | Agent creation + legacy migration |
| application | `agent_wizard.py` | End-to-end wizard flow |
| application | `persona_generator.py` | Persona prompt generation |
| application | `skill_recommender.py` | Skill catalog recommendation |
| application | `tool_recommender.py` | Tool recommendation from 14 built-ins |
| application | `agent_test_runner.py` | Scenario validation |
| application | `task_queue.py` | Priority queue with DAG dependencies |
| application | `task_decomposer.py` | LLM task decomposition |
| application | `work_assigner.py` | Task-to-agent matching |
| application | `alert_manager.py` | Rule-based alerting |
| infrastructure | `lifecycle_logger.py` | JSONL lifecycle event logger |
| infrastructure | `agent_runtime.py` | Runtime wrapper with hooks |
| infrastructure | `metrics_collector.py` | Ring buffer + SQLite metrics |
| infrastructure | `session_replay.py` | JSONL session replay |
| infrastructure | `dashboard_server.py` | HTTP + SSE server |
| infrastructure | `task_scheduler.py` | Background task scheduler |
| interfaces | `dashboard_static/index.html` | Web dashboard SPA |

## New tests (12 test files, 132 tests)

| File | Tests | Coverage |
|------|-------|----------|
| `test_agent_domain.py` | 16 | AgentState, AgentRole, AgentDescriptor transitions |
| `test_agent_registry.py` | 14 | CRUD, state, lifecycle events, concurrency (50 threads) |
| `test_agent_runtime.py` | 13 | Properties, ask(), lifecycle, hooks |
| `test_agent_factory.py` | 5 | create_agent, create_from_existing, generate_id |
| `test_lifecycle_logger.py` | 5 | JSONL output, structure, parent dirs |
| `test_agent_wizard.py` | 10 | Blueprint serialization, wizard heuristic, persistence |
| `test_task_queue.py` | 27 | Task domain, queue CRUD, ready tasks, concurrency |
| `test_work_assigner.py` | 6 | Assignment, skill matching, backpressure |
| `test_metrics_collector.py` | 10 | Record, query, flush, summary, ring buffer |
| `test_alert_manager.py` | 9 | Rules, cooldown, listeners, start/stop |
| `test_session_replay.py` | 9 | JSONL loading, merging, filtering |
| `test_dashboard_server.py` | 8 | HTTP endpoints, SSE, start/stop |

## Compatibility

- Python: 3.10+
- OS: Linux

## Validation

- Full test suite: **464 passed** (328 original + 136 new).
- 0 Pylance errors across all files.
- All existing functionality preserved — Polluks and Kastor work as before, now registered as agents.

## Safety

No permission policy expansion and no shell allowlist relaxation were introduced in this release.

Use only in isolated/sandboxed environments as described in `SECURITY.md`.
