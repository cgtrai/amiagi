# amiagi Web Interface

> Full documentation for the amiagi Web GUI — version 1.1.0

## Overview

The amiagi web interface provides a browser-based dashboard for managing
multi-agent AI workflows. It is built on **Starlette** (ASGI), uses
**PostgreSQL** for persistence, and communicates in real time via WebSockets.

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
├── app.py                    # ASGI application factory
├── i18n_web.py               # Web-specific i18n (Faza 14)
├── auth/                     # OAuth2 + JWT sessions + RBAC
├── rbac/                     # Role-Based Access Control
├── db/                       # Database layer + migrations (001-005)
├── ws/                       # WebSocket layer
├── files/                    # File management (SHA-256 dedup, 50 MB)
├── audit/                    # Audit trail + workspace manager
├── skills/                   # Skill management + selector
├── productivity/             # Prompts, search, snippets
├── monitoring/               # Performance, notifications, sessions, keys, webhooks
├── task_templates/           # YAML workflow templates
├── routes/                   # 16 route modules
├── static/                   # CSS (Liquid Glass) & JS (dashb, keybindings)
└── templates/                # Jinja2 HTML templates
```

## Routes

### Health & System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/readiness` | Readiness probe |

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| GET | `/login` | Login page |
| GET | `/login/{provider}` | OAuth2 start |
| GET | `/auth/callback/{provider}` | OAuth2 callback |
| POST | `/logout` | Logout |

### REST API
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agents` | List agents |
| GET | `/api/agents/{id}` | Agent detail |
| GET | `/api/tasks` | Task queue |
| GET | `/api/metrics` | System metrics |
| GET | `/api/budget` | Budget summary |

### Teams & Models
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/teams` | List teams |
| GET | `/api/models` | List Ollama models |
| GET/PUT | `/api/model-config` | Session model config |
| POST | `/api/agents/{id}/model` | Assign model |

### Skills Administration
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/admin/skills` | Skill CRUD |
| GET/PUT/DELETE | `/admin/skills/{id}` | Skill detail |
| POST | `/admin/skills/{id}/traits` | Trait management |
| POST/DELETE | `/admin/skills/{id}/assign/{agent}` | Assignment |

### Productivity
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/prompts` | Prompt library |
| GET | `/api/search` | Full-text search (tsvector) |
| GET/POST | `/snippets` | Snippet library |

### Monitoring & Integrations
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/performance` | Performance records |
| GET | `/api/performance/summary` | Aggregated stats |
| GET | `/api/notifications` | Notification list + count |
| PUT | `/api/notifications/{id}/read` | Mark read |
| GET/POST | `/settings/api-keys` | API key management |
| GET/POST | `/settings/webhooks` | Webhook management |
| POST | `/settings/webhooks/{id}/test` | Test webhook |

### Task Templates
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

- **PostgreSQL** via asyncpg
- **Schema**: `dbo`
- **Migrations**: Auto-run on startup (001–005)

## Design System

**Liquid Glass** theme:
- `tokens.css`: 71 design tokens
- `components.css`: 70 component classes
- `responsive.css`: 3 breakpoints (mobile/tablet/desktop)

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+K | Global search |
| Ctrl+Shift+P | Command palette |
| Ctrl+Enter | Send prompt |
| Esc | Close overlay |

## Internationalization

- Languages: `pl` (default), `en`
- Detection: cookie → Accept-Language → fallback pl
- 80+ translation keys per locale

## Security

- CSRF via itsdangerous cookie signing
- API keys: SHA-256 hashed, shown once at creation
- Webhooks: HMAC-SHA256 signatures
- File uploads: 50 MB limit, SHA-256 dedup
- RBAC: role-based permissions on admin routes
