# Release Notes — v1.1.0

**Release date:** 2026-03-05  
**Codename:** Web Interface  
**Plan alias:** v1.0.4-web — this release implements the full scope of
`amiagi-my-work/plan_v1.0.4_web.md` plus stabilisation fixes and four
additional proposals (P11–P14).  The version was bumped to 1.1.0
to reflect the significance of the Web Interface addition.

## Highlights

- **Full Web GUI** — Browser-based dashboard for managing multi-agent AI workflows
- **Liquid Glass Design** — Modern CSS design system with 71 tokens and 70 component classes
- **Real-time Updates** — WebSocket-based event streaming with JWT authentication
- **PostgreSQL Backend** — 6 auto-running migrations, asyncpg connection pool
- **OAuth2 Authentication** — GitHub and Google providers, JWT HS256 sessions
- **RBAC** — Role-based access control with admin panel and audit trail
- **16 Route Modules** — 80+ HTTP endpoints covering all functionality
- **Cron Scheduler** — Recurring task execution via cron expressions
- **Per-Task Cost Tracking** — MODEL_PRICING dictionary with dashboard cost breakdown
- **Agent Memory Browser** — List, filter, edit and delete cross-agent memory items
- **Health Diagnostics** — `/health/detailed` endpoint with disk, DB pool, agent counts, Ollama status

## New Features

### Foundation (Fazy 0–3)
- ASGI application factory with Starlette
- asyncpg database pool with auto-migration system
- OAuth2 login (GitHub, Google) with JWT session management
- RBAC system: roles, permissions, user-role assignments
- Admin panel with audit log (filter, CSV export)

### UI & Design (Fazy 4, 9, 11)
- Liquid Glass CSS design system (tokens.css, components.css)
- Responsive layout: 3 breakpoints (mobile <768, tablet 768–1023, desktop ≥1024)
- Hamburger menu for mobile, sticky bottom input bar
- Dashboard with agent cards, task board, event ticker, metric cards
- Web Components: agent-card, chat-stream, task-board, event-ticker, metric-card

### Multi-Agent Workspace (Fazy 5–8)
- Per-user workspace directories with file upload/download
- SHA-256 content-addressable file storage (50 MB limit)
- WebSocket event hub with heartbeat (30s/90s)
- Per-agent WebSocket streams with JWT auth
- Multi-user workspace isolation

### Intelligence (Fazy 10, 12)
- Skill and trait management (CRUD, assignments, usage logging)
- Multi-level skill selector (keyword, tool, pinned, budget, priority matching)
- Shared prompt library with {placeholder} parameter templates
- Full-text search via PostgreSQL tsvector + GIN index
- Code snippet library with agent/task context
- Command palette (Ctrl+Shift+P) with 13 commands
- Keyboard shortcuts (Ctrl+K search, Ctrl+Enter send, etc.)

### Monitoring & Integrations (Faza 13)
- Agent performance dashboard (avg/p50/p95 duration, success rate, cost)
- In-app notification center (bell icon, badge count, mark read)
- Session replay: record and browse EventBus events per session
- Self-service API keys (SHA-256 hashed, scope picker, expiry)
- Webhook management (HMAC-SHA256 signatures, test button)

### Templates & i18n (Faza 14)
- Task template system with YAML workflow definitions
- 4 builtin templates: Code Review, Documentation, Bug Investigation, Refactoring
- Template wizard: parameters → preview → execute
- Import/export YAML
- Multi-language support: Polish (default) + English
- Language detection: cookie → Accept-Language header → fallback
- 80+ translation keys per locale

### Proposals P11–P14 (post-plan additions)

#### P11 — Cron Jobs / Recurring Tasks
- `CronScheduler` with cron-expression parser and 60 s tick loop
- Migration `006_cron_jobs.sql` (table `dbo.cron_jobs`)
- CRUD routes: `GET/POST/PUT/DELETE /api/cron`
- Dashboard "Scheduled" tab in sections
- Wired in `app.py` on_startup / on_shutdown

#### P12 — Per-Task Cost Tracking
- `MODEL_PRICING` dictionary and `estimate_cost()` in `budget_manager.py`
- `GET /api/budget/tasks` — per-task cost details
- "Costs" panel with per-task breakdown in dashboard

#### P13 — Agent Memory Browser
- `GET /api/memory` — list / filter (agent_id, task_id, limit)
- `DELETE /api/memory/{index}`, `DELETE /api/memory` (clear all)
- `PUT /api/memory/{index}` — edit key_findings / tags
- Dashboard "Memory" tab with table, filters and inline edit

#### P14 — Health Diagnostics
- `GET /health/detailed` — RAM, CPU, disk, DB pool status, Ollama reachability, uptime
- `app.state._startup_time` recorded at startup
- "System Health" panel with 5 metric cards in dashboard (auto-refresh 30 s)

## Database Migrations

| # | File | Tables |
|---|------|--------|
| 001 | rbac_and_sessions.sql | users, roles, permissions, user_roles, sessions, audit_log |
| 002 | skills.sql | skills, agent_traits, agent_skill_assignments, skill_usage_log |
| 003 | productivity.sql | prompts, search_index, snippets |
| 004 | monitoring.sql | agent_performance, notifications, notification_preferences, session_events, api_keys, webhooks |
| 005 | task_templates.sql | task_templates (with 4 builtin inserts) |
| 006 | cron_jobs.sql | cron_jobs (scheduled recurring tasks) |

## Test Summary

- **1902 tests** — all passing, 0 warnings
- Phase-specific test files: test_faza6 through test_faza14
- P11–P14 test files: test_cron_scheduler, test_cost_tracking, test_memory_routes, test_health_detailed
- Comprehensive unit and integration test coverage
- Pyright 1.1.408 — 0 type errors across entire codebase

## Dependencies Added

```
starlette>=0.52.1
uvicorn>=0.41.0
asyncpg>=0.31.0
PyJWT>=2.11.0
httpx>=0.28.1
Jinja2>=3.1.6
itsdangerous>=2.2.0
python-multipart>=0.0.22
PyYAML>=6.0
```

## Breaking Changes

None — the web interface is an additive feature (`--ui web`).

## Known Limitations

- Web Push notifications require HTTPS in production
- OAuth2 requires configured provider credentials
- PostgreSQL 13+ required for `gen_random_uuid()`

## Build & Tooling

- Added `[tool.pyright]` to `pyproject.toml` (`extraPaths = ["src"]`, `typeCheckingMode = "standard"`, `pythonVersion = "3.10"`)
- Cleaned up redundant string-literal annotation in `chat_service.py`
