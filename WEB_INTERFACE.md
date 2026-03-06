# amiagi Web Interface

> Full documentation for the amiagi Web GUI — version 1.1.0

## Overview

The amiagi web interface provides a browser-based dashboard for managing
multi-agent AI workflows. It is built on **Starlette** (ASGI), uses
**PostgreSQL** (with SQLite fallback) for persistence, and communicates in
real time via WebSockets. The interface implements 29 management tools
across 11+ screens.

## Quick Start

```bash
# Install with web dependencies
pip install -e ".[web]"

# Start the web server
python main.py --ui web
```

## Architecture

```
src/amiagi/interfaces/web/
├── app.py                    # ASGI application factory (~535 lines)
├── i18n_web.py               # Web-specific i18n (PL + EN, 490+ keys)
├── auth/                     # OAuth2 + JWT sessions + RBAC
├── rbac/                     # Role-Based Access Control
├── db/                       # Database layer + migrations (001-012)
├── ws/                       # WebSocket layer (events + per-agent)
├── files/                    # File management (SHA-256 dedup, 50 MB)
├── audit/                    # Audit trail + workspace manager
├── skills/                   # Skill management + selector
├── productivity/             # Prompts, search, snippets
├── monitoring/               # Performance, notifications, sessions,
│                             #   keys, webhooks, sandbox monitor
├── task_templates/           # YAML workflow templates
├── routes/                   # 18 route modules
│   ├── health_routes.py      # /health, /health/detailed, VRAM, connections
│   ├── sandbox_routes.py     # Sandbox CRUD + shell policy + exec log
│   ├── dashboard_routes.py   # Page routes (17 pages)
│   ├── search_routes.py      # Full-text search (tsvector)
│   └── ...                   # agents, tasks, teams, models, budget, etc.
├── static/
│   ├── css/                  # Liquid Glass v2 design system
│   │   ├── tokens.css        # 71 design tokens
│   │   ├── components.css    # 70+ component classes
│   │   ├── layout.css        # Command Rail + Viewport + Drawer
│   │   ├── responsive.css    # 3 breakpoints (mobile/tablet/desktop)
│   │   ├── health.css        # Health Dashboard styles
│   │   ├── sandboxes.css     # Sandboxes Admin styles
│   │   ├── settings.css      # Settings page styles (12 tabs)
│   │   └── ...               # Per-page CSS files
│   └── js/
│       ├── dashboard.js      # Main dashboard logic
│       ├── health.js         # Health auto-refresh (10s)
│       ├── sandboxes.js      # Sandbox CRUD + exec log
│       ├── keybindings.js    # Ctrl+K, Ctrl+Shift+P, etc.
│       └── components/       # 19 Web Components (Shadow DOM)
│           ├── global-search.js      # Spotlight search + filters
│           ├── shell-policy-editor.js # Visual shell policy editor
│           ├── live-stream.js        # WebSocket streaming
│           ├── chat-stream.js        # Agent chat interface
│           ├── approval-card.js      # Inbox approval card
│           ├── inbox-badge.js        # Inbox count badge
│           └── ...
└── templates/                # Jinja2 HTML templates
    ├── base.html             # Layout shell
    ├── dashboard.html        # Main dashboard
    ├── health.html           # Health Dashboard
    ├── settings.html         # Settings (12 tabs)
    ├── admin/
    │   ├── sandboxes.html    # Sandbox management
    │   └── users.html        # User management
    └── partials/
        ├── command_rail.html # Navigation rail
        ├── status_bar.html   # Bottom status bar
        └── toast.html        # Toast notification system
```

## Routes

### Health & System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (JSON) |
| GET | `/health/detailed` | Detailed system metrics |
| GET | `/api/health/vram` | GPU VRAM usage + loaded models |
| GET | `/api/health/connections` | DB pool, WebSocket, rate limiter stats |
| GET | `/readiness` | Readiness probe |

### Dashboard Pages
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Main dashboard |
| GET | `/health-dashboard` | Health Dashboard UI |
| GET | `/settings` | Settings (12 tabs) |
| GET | `/admin/sandboxes` | Sandbox management (admin) |
| GET | `/admin/users` | User management (admin) |
| GET | `/metrics` | Metrics & analytics |
| GET | `/sessions` | Session history |
| GET | `/supervisor` | Supervisor panel |
| GET | `/inbox` | Operator inbox |
| GET | `/model-hub` | Model management |
| GET | `/budget` | Budget tracking |
| GET | `/vault` | Secret vault (admin) |
| GET | `/workflows` | Workflow designer |
| GET | `/memory` | Memory browser |
| GET | `/evaluations` | Evaluation lab |
| GET | `/knowledge` | Knowledge base |

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| GET | `/login` | Login page |
| GET | `/login/{provider}` | OAuth2 start |
| GET | `/auth/callback/{provider}` | OAuth2 callback |
| POST | `/logout` | Logout |

### REST API — Agents & Tasks
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agents` | List agents |
| GET | `/api/agents/{id}` | Agent detail |
| GET | `/api/tasks` | Task queue |
| GET | `/api/metrics` | System metrics |
| GET | `/api/budget` | Budget summary |

### REST API — Teams & Models
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/teams` | List teams |
| GET | `/api/models` | List Ollama models |
| GET/PUT | `/api/model-config` | Session model config |
| POST | `/api/agents/{id}/model` | Assign model |

### REST API — Sandboxes & Shell
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sandboxes` | List all sandboxes |
| POST | `/api/sandboxes` | Create sandbox |
| GET | `/api/sandboxes/{agent_id}` | Sandbox detail |
| DELETE | `/api/sandboxes/{agent_id}` | Destroy sandbox |
| POST | `/api/sandboxes/{agent_id}/reset` | Reset sandbox |
| POST | `/api/sandboxes/cleanup` | Cleanup all temp files |
| GET | `/api/shell-policy` | Read shell allowlist |
| PUT | `/api/shell-policy` | Update shell allowlist (admin) |
| GET | `/api/shell-executions` | Execution audit log |

### REST API — Skills
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/admin/skills` | Skill CRUD |
| GET/PUT/DELETE | `/admin/skills/{id}` | Skill detail |
| POST | `/admin/skills/{id}/traits` | Trait management |
| POST/DELETE | `/admin/skills/{id}/assign/{agent}` | Assignment |

### REST API — Productivity
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/prompts` | Prompt library |
| GET | `/api/search` | Full-text search (tsvector) |
| GET/POST | `/snippets` | Snippet library |

### REST API — Monitoring & Integrations
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/performance` | Performance records |
| GET | `/api/performance/summary` | Aggregated stats |
| GET | `/api/notifications` | Notification list + count |
| PUT | `/api/notifications/{id}/read` | Mark read |
| GET/POST | `/settings/api-keys` | API key management |
| GET/POST | `/settings/webhooks` | Webhook management |
| POST | `/settings/webhooks/{id}/test` | Test webhook |

### REST API — Templates
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/templates` | Template CRUD |
| POST | `/templates/{id}/execute` | Execute workflow |
| GET | `/templates/{id}/export` | Export YAML |

### Internationalization
| Method | Path | Description |
|--------|------|-------------|
| GET | `/lang/{lang}` | Switch language (cookie) |
| GET | `/api/lang` | Current language |

### WebSocket
| Path | Description |
|------|-------------|
| `/ws/events` | Global event stream (JWT auth) |
| `/ws/agent/{agent_id}` | Per-agent stream |

## Authentication

- **OAuth2**: GitHub and Google providers
- **JWT HS256**: Session tokens with configurable expiry
- **Middleware**: AuthMiddleware validates session on every request
- Public paths: `/health`, `/readiness`, `/login`, `/static/`

## Database

- **PostgreSQL** via asyncpg (with SQLite fallback)
- **Schema**: `dbo`
- **Migrations**: Auto-run on startup (001–012)
- Key tables: `agents`, `tasks`, `sessions`, `prompts`, `vault_entries`,
  `search_index`, `shell_executions`, `sandbox_metadata`

## Design System — Liquid Glass v2

Three-tier glass hierarchy:

| Tier | Usage | Example |
|------|-------|---------|
| Dense | Navigation, overlays, status bar | Command Rail, Toast |
| Card | Content panels, sections | Dashboard cards, Settings tabs |
| Pill | Interactive elements | Badges, metric values, tags |

Files:
- `tokens.css`: 71 design tokens (colors, spacing, typography, glass effects)
- `components.css`: 70+ component classes
- `responsive.css`: 3 breakpoints — mobile (<768px), tablet (768–1023px), desktop (≥1024px)
- Per-page CSS: health.css, sandboxes.css, settings.css, etc.

## Web Components (19)

| Component | Description |
|-----------|-------------|
| `<global-search>` | Spotlight search with type icons, filters, recent searches |
| `<shell-policy-editor>` | Visual shell policy editor + raw JSON mode |
| `<live-stream>` | WebSocket-backed streaming display |
| `<chat-stream>` | Agent chat interface |
| `<approval-card>` | Inbox approval/rejection card |
| `<inbox-badge>` | Unread inbox count badge |
| `<session-timeline>` | Session event timeline |
| `<secret-field>` | Masked secret value field |
| `<dag-viewer>` | Workflow DAG visualization |
| `<eval-chart>` | Evaluation metric chart |
| `<ab-comparison>` | A/B test comparison panel |
| `<kb-search>` | Knowledge base search |
| `<index-progress>` | Indexing progress indicator |
| `<cloud-model-card>` | Cloud model info card |
| ... | 5 additional utility components |

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+K | Global search (spotlight) |
| Ctrl+Shift+P | Command palette |
| Ctrl+Enter | Send prompt |
| Esc | Close overlay |
| Ctrl+1–9 | Switch agent tabs |
| ? | Show help overlay |

## Internationalization

- Languages: `pl` (default), `en`
- Detection: cookie → Accept-Language → fallback pl
- 490+ translation keys per locale
- All UI labels, navigation, and system messages are translatable

## Security

- CSRF via itsdangerous cookie signing
- API keys: SHA-256 hashed, shown once at creation
- Webhooks: HMAC-SHA256 signatures
- File uploads: 50 MB limit, SHA-256 dedup
- RBAC: role-based permissions on admin routes
- Vault encryption: AES-256-GCM at rest
- Sandbox isolation: per-agent file system scope
- Shell policy: allowlist enforcement with audit logging
- See [SECURITY.md](SECURITY.md) for full details
