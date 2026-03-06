# Release Notes — v1.2.0

**Release date:** 2026-03-06  
**Codename:** Web Management Console  
**Previous release:** v1.1.0 (Web Interface foundation)

## Highlights

- **All 5 UI sprints complete** — 11 new screens, 19 Web Components, 100+ API endpoints
- **Liquid Glass v2 design system** — 71 tokens, 70+ component classes, 3-tier glass, shimmer/specular effects
- **2543 tests** passing (up from 1902 in v1.1.0) — 641 new tests
- **12 database migrations** — full schema for all management features
- **490+ i18n keys** — complete Polish + English coverage
- **29 management tools** — every system capability accessible from the web UI

## What's New Since v1.1.0

### Sprint 1 — Layout & Design System v2

- `tokens.css` v2 — 3-tier glass (dense/card/pill), chromatic dispersion, shimmer + specular keyframes
- `layout.css` — CSS Grid: Command Rail (64px) | Main Viewport | Detail Drawer (400px)
- `components.css` — 70+ utility classes: command-rail, status-bar, detail-drawer, live-stream, agent-control-card
- `partials/command_rail.html` — icon rail with tooltips, active state, badge overlay
- `partials/status_bar.html` — live status: model, budget, tasks, inbox, tokens, uptime
- `partials/detail_drawer.html` — slide-in panel, overlay on mobile
- `base.html` refactored — sidebar replaced with command rail + status bar + drawer slot
- Responsive layout: 320px, 768px, 1024px, 1440px breakpoints
- All existing templates migrated to new layout

### Sprint 2 — Supervisor + Inbox + Agent Controls

- `supervisor.html` — Mission Control: live-stream, active agents grid, current task, input
- `<live-stream>` Web Component — auto-scroll, coloring, filtering, WebSocket feed
- `inbox.html` — Operator inbox: tabs (pending/approved/rejected/expired), action cards
- `<inbox-badge>` Web Component — pulsing badge in rail + status bar
- `<approval-card>` Web Component — context, approve/reject/reply/delegate buttons
- `InboxService` — aggregator for WorkflowEngine GATE + AskHumanTool
- Agent inline controls: pause/resume/terminate/spawn in agent-card + drawer
- DB migration 008: `inbox_items` table

### Sprint 3 — Model Hub + Cost Center + Vault

- Model Hub redesign — dedicated view: model list, status, VRAM usage, pull, benchmark tab
- `model_hub.html` + `model_hub.css` + `model_hub.js` — full model management UI
- API: `POST /api/models/pull`, `GET /api/models/vram`, `POST /api/models/benchmark`
- Cost Center redesign — budget visualization: 3-tier bars, history chart, quotas config
- `budget.html` + `budget.css` + `budget.js` — full budget management UI
- API: `GET /api/budget/history`, `PUT /api/budget/quotas`, `POST /api/budget/reset`
- `vault.html` — Secret Vault: masked list, add/edit/rotate/delete, assignments, access log
- `<secret-field>` Web Component — mask/reveal toggle + clipboard copy
- `SecretVault` → Fernet encryption upgrade + DB persistence
- API: full CRUD `/api/vault/*` + rotate + access-log + assignments
- `AuditChain` integration — vault operations logged
- DB migration 009: `vault_secrets`, `vault_access_log`, `model_assignments`, `budget_snapshots`

### Sprint 4 — Workflow Studio + Memory + Evaluations + Knowledge

- Workflow Studio — DAG visualization, run status, definition browser
- `<workflow-dag>` Web Component — SVG DAG with node status, GATE highlight
- Memory Browser — per-agent facts, shared memory, search, export
- `evaluations.html` — 5-tab: dashboard, history, A/B tests, baselines, suites
- `<eval-chart>` + `<ab-comparison>` Web Components
- `EvalRunner`/`ABTestRunner` → DB persistence + `run_async()` + background workers
- `knowledge.html` — 5-tab: bases overview, sources, explore, pipeline, stats
- `<knowledge-search>` + `<index-progress>` Web Components
- `KnowledgeManager` + `DocumentIngester` + `ChunkingStrategy` (3 strategies)
- `EvalRepository` + `KnowledgeRepository` — full DB CRUD
- DB migration 010: `eval_runs`, `eval_run_scenarios`, `ab_campaigns`, `knowledge_bases`, `knowledge_sources`, `shared_memory`

### Sprint 5 — Health + Settings + Sandboxes + Polish

- Health Dashboard — system health cards (Ollama, DB, GPU, Agents, Disk), auto-refresh, VRAM bars, connection grid
- `health.html` + `health.css` + `health.js` — full health monitoring UI
- API: `GET /health/vram`, `/health/connections`, `/health/detailed`
- Settings redesign — 12 tabs: General, Models, Costs, Cron, Integrations, Memory, Prompts, Templates, Execution, Security, System, Advanced
- `admin/sandboxes.html` + `sandboxes.css` + `sandboxes.js` — sandbox management UI
- `<shell-policy-editor>` Web Component — visual + JSON mode, toast integration
- `SandboxMonitor` — resource tracking, cleanup hooks, alerts on limits
- API: 10 sandbox/shell-policy endpoints (CRUD + cleanup + executions)
- `AskHumanTool` + `ReviewRequestTool` — agent tools for Human-in-the-Loop inbox
- `HumanInteractionBridge` — wires tools to `InboxService`
- DB migration 012: `shell_executions`, `sandbox_metadata`
- Global Search spotlight refinement — per-type icons, recent searches (localStorage), filter tabs
- Toast notifications — 4 types (success/error/warning/info), used across all new pages
- 65+ new i18n keys for settings, health, sandboxes
- Responsive audit — 480px breakpoint added to all new CSS files
- Performance audit — total CSS+JS < 600KB, no WebSocket leaks, no interval leaks
- `SECURITY.md` rewritten — Vault Encryption, Sandbox Isolation, Shell Policy, HITL sections
- `WEB_INTERFACE.md` rewritten — ~290 lines, full architecture + routes + 19 WC table

### Bug Fixes

- `event.target` implicit global in `switchSettings()` — replaced with explicit `btn` parameter
- Empty `.health-panel--wide` CSS ruleset — added `grid-column: 1 / -1`
- Deprecated `-webkit-overflow-scrolling` — replaced with `@supports (scrollbar-width: none)` progressive enhancement
- Missing `.settings-muted` CSS class — added definition
- Missing `for=` attributes on `<label>` elements in settings — 3 accessibility fixes
- CSS `-webkit-backdrop-filter` missing in Sprint 4 CSS files — added
- `kb` possibly unbound in `reindex_base` — fixed scoping
- Background workers missing after DB persistence refactor — restored with `asyncio.create_task`
- HTML inline `style="display:none"` — replaced with semantic `hidden` attribute

## Database Migrations

| # | File | Tables |
|---|------|--------|
| 001 | rbac_and_sessions.sql | users, roles, permissions, user_roles, sessions, audit_log |
| 002 | skills.sql | skills, agent_traits, agent_skill_assignments, skill_usage_log |
| 003 | productivity.sql | prompts, search_index, snippets |
| 004 | monitoring.sql | agent_performance, notifications, notification_preferences, session_events, api_keys, webhooks |
| 005 | task_templates.sql | task_templates (with 4 builtin inserts) |
| 006 | cron_jobs.sql | cron_jobs |
| 007 | workspace_tables.sql | workspace tables |
| 008 | inbox_items.sql | inbox_items |
| 009 | vault_models.sql | vault_secrets, vault_access_log, model_assignments, budget_snapshots |
| 010 | eval_knowledge.sql | eval_runs, eval_run_scenarios, ab_campaigns, knowledge_bases, knowledge_sources, shared_memory |
| 011 | (reserved) | — |
| 012 | shell_executions.sql | shell_executions, sandbox_metadata |

## Web Components (19 total)

| Component | Sprint | Description |
|-----------|--------|-------------|
| `<agent-card>` | S1 | Agent status card with inline controls |
| `<chat-stream>` | S1 | Chat message stream |
| `<task-board>` | S1 | Kanban-style task board |
| `<event-ticker>` | S1 | Real-time event ticker |
| `<metric-card>` | S1 | Dashboard metric card |
| `<live-stream>` | S2 | Auto-scrolling WebSocket log |
| `<inbox-badge>` | S2 | Pulsing notification badge |
| `<approval-card>` | S2 | HITL approval/reject card |
| `<secret-field>` | S3 | Mask/reveal + copy secret field |
| `<workflow-dag>` | S4 | SVG DAG visualization |
| `<eval-chart>` | S4 | Evaluation results chart |
| `<ab-comparison>` | S4 | A/B test comparison view |
| `<knowledge-search>` | S4 | Knowledge base search |
| `<index-progress>` | S4 | Document indexing progress |
| `<shell-policy-editor>` | S5 | Shell allowlist visual editor |
| `<global-search>` | S5 | Ctrl+K spotlight search |
| `<toast-container>` | S5 | Toast notification system |
| `<command-palette>` | S1 | Ctrl+Shift+P command palette |
| `<detail-drawer>` | S1 | Slide-in detail panel |

## Test Summary

- **2543 tests** — all passing, 0 errors, 0 warnings
- 641 new tests since v1.1.0
- Key new test files:
  - `test_sprint5_health_settings_sandboxes.py` (108 tests)
  - `test_sprint_p2.py` (Sprint 3 tests)
  - `test_model_hub_routes.py`, `test_vault_routes.py`
  - `test_web_app_health.py`, `test_health_detailed.py`
  - `test_sandbox_manager.py`

## Breaking Changes

None — all changes are additive. The `--ui web` flag continues to work as before.

## Known Limitations

- Web Push notifications require HTTPS in production
- OAuth2 requires configured provider credentials
- PostgreSQL 13+ required for `gen_random_uuid()`
- SQLite fallback available for development (no asyncpg needed)
