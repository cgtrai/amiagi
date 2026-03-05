# amiagi v1.1.0 — Web Interface

## What's New

Full browser-based Web GUI for managing multi-agent AI workflows.

### Highlights

- **Dashboard** — Real-time agent monitoring with WebSocket streaming
- **Liquid Glass** design system — 71 CSS tokens, 70 component classes, 3 responsive breakpoints
- **OAuth2 Authentication** — GitHub & Google, JWT sessions, RBAC
- **Skill System** — Multi-level skill matching with keyword, tool, priority and budget trimming
- **Prompt Library** — Shared prompts with {placeholder} templates and full-text search
- **Performance Dashboard** — Agent metrics with model comparison (avg/p50/p95)
- **Notification Center** — In-app bell with badge count
- **API Key Self-Service** — SHA-256 hashed keys with scope picker
- **Webhook Management** — HMAC-SHA256 signed payloads with test button
- **Task Templates** — YAML workflow definitions with wizard GUI and 4 builtin templates
- **i18n** — Polish + English, cookie/Accept-Language detection
- **Session Replay** — Browse and analyze past session events
- **Command Palette** — Ctrl+Shift+P with 13 commands, global search (Ctrl+K)
- **Cron Scheduler** — Recurring task execution via cron expressions with dashboard UI
- **Per-Task Cost Tracking** — MODEL_PRICING + dashboard cost breakdown panel
- **Agent Memory Browser** — List, filter, edit and delete cross-agent memory items
- **Health Diagnostics** — `/health/detailed` with disk, DB pool, agent counts, auto-refresh

### Stats

- 1902 tests passing (0 warnings)
- 6 database migrations (auto-run on startup)
- 16 route modules, 80+ endpoints
- 80+ i18n translation keys per locale
- Pyright 0 errors (standard mode)

### Install

```bash
pip install -e ".[web]"
python main.py --ui web
```

### Requirements

- Python 3.10+
- PostgreSQL 13+
- Ollama (for LLM inference)

Full documentation: [WEB_INTERFACE.md](WEB_INTERFACE.md)
