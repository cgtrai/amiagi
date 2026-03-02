# amiagi — Web Interface Documentation

amiagi provides two HTTP-based interfaces that run in background threads alongside the TUI.
Both are launched from the Textual interface and require no external dependencies (no npm, no build step).

---

## 1. Monitoring Dashboard (Phase 4)

A real-time browser-based monitoring panel for observing agents, tasks, metrics, and events.

### How to start

1. **Launch amiagi in Textual mode:**
   ```bash
   source .venv/bin/activate
   python -m amiagi.main --ui textual
   ```

2. **Start the dashboard from the Sponsor input panel:**
   ```
   /dashboard start
   ```
   Default port: **8080**. Custom port: `/dashboard start 9090`.

3. **Open in any browser:**
   ```
   http://localhost:8080
   ```

4. **Stop the dashboard:**
   ```
   /dashboard stop
   ```

5. **Check status:**
   ```
   /dashboard status
   ```

### Dashboard panels

The dashboard is a single-page application (vanilla HTML/CSS/JS, zero external dependencies) with four panels:

| Panel | Content |
|-------|---------|
| **Agents** | Name, role, model, current state — color-coded badges (idle/working/paused/error/terminated) |
| **Tasks** | Title, priority, status, assigned agent — Kanban-style overview of the task queue |
| **Metrics** | Aggregated metrics (count, avg, min, max) — token usage, task duration, success/error rate |
| **Event Log** | Last 50 events from JSONL session logs — timestamp, source, event type |

A status bar at the bottom shows agent count, task breakdown, and last refresh time.

### Auto-refresh and live events

- The dashboard **auto-refreshes every 5 seconds** via polling.
- **Server-Sent Events (SSE)** provide real-time push updates — when the server emits events, the UI updates immediately.

### Dashboard API endpoints

The dashboard server exposes a JSON API (CORS enabled, no auth required):

| Endpoint | Method | Response |
|----------|--------|----------|
| `/api/agents` | GET | All registered agents with state, role, model |
| `/api/tasks` | GET | All tasks with priority, status, assignment |
| `/api/metrics` | GET | Aggregated metrics summary |
| `/api/alerts` | GET | Recent alerts from AlertManager |
| `/api/replay` | GET | Last 200 session events from JSONL logs |
| `/api/events` | GET | SSE stream — live event push |
| `/api/status` | GET | Server status, agent count, task breakdown |

### Static files

The dashboard HTML is served from `src/amiagi/interfaces/dashboard_static/index.html`.  
No build step, no npm — plain HTML/CSS/JS.

### Configuration

| Environment variable | Default | Description |
|----------------------|---------|-------------|
| `AMIAGI_DASHBOARD_PORT` | `8080` | Default port for the monitoring dashboard |

---

## 2. REST API (Phase 10)

A programmable HTTP API for external integrations, CI/CD pipelines, and SDK clients.
Provides bearer-token authentication and pluggable route handlers.

### How to start

1. **Launch amiagi in Textual mode** (same as above).

2. **Start the REST API server:**
   ```
   /api start
   ```
   Default port: **8090**. Configurable via `AMIAGI_REST_API_PORT`.

3. **Check status:**
   ```
   /api status
   ```

4. **Stop the server:**
   ```
   /api stop
   ```

### Authentication

When `AMIAGI_REST_API_TOKEN` is set, every request must include:

```
Authorization: Bearer <your-token>
```

When the token is empty (default), the API is open (no auth required).

### Route system

The REST server uses a pluggable route system. Routes are registered via `RESTServer.add_route()`:

```python
server.add_route("GET", "/agents", handler_fn)
server.add_route("POST", "/tasks", handler_fn)
```

Each handler receives a request context dict and returns `(status_code, response_dict)`.

### Example usage with curl

```bash
# Health check (no auth)
curl http://localhost:8090/health

# With auth token
curl -H "Authorization: Bearer my-secret-token" http://localhost:8090/agents
```

### Configuration

| Environment variable | Default | Description |
|----------------------|---------|-------------|
| `AMIAGI_REST_API_PORT` | `8090` | Port for the REST API server |
| `AMIAGI_REST_API_TOKEN` | _(empty)_ | Bearer token for authentication (empty = no auth) |

---

## 3. Python SDK (Phase 10)

The `AmiagiClient` provides a high-level Python SDK for interacting with the REST API programmatically.

### Usage

```python
from amiagi.infrastructure.sdk_client import AmiagiClient

client = AmiagiClient(
    base_url="http://localhost:8090",
    token="my-secret-token",  # optional
)

# Health check
client.ping()

# Agent management
agents = client.list_agents()
client.create_agent({"name": "reviewer", "role": "specialist"})

# Task management
tasks = client.list_tasks()
client.create_task({"title": "Review PR #42", "priority": "HIGH"})

# Workflow
client.run_workflow("code_review")

# Metrics
metrics = client.get_metrics()
```

### Error handling

The SDK raises `SDKError` for non-2xx responses:

```python
from amiagi.infrastructure.sdk_client import AmiagiClient, SDKError

try:
    client.create_agent({"name": "test"})
except SDKError as e:
    print(f"HTTP {e.status_code}: {e.detail}")
```

---

## 4. Webhook Dispatcher (Phase 10)

Sends HTTP POST notifications to registered URLs when specific events occur.

### Registration

```python
from amiagi.infrastructure.webhook_dispatcher import WebhookDispatcher, WebhookTarget

dispatcher = WebhookDispatcher()
dispatcher.register(WebhookTarget(
    url="https://example.com/hook",
    events=["task_done", "agent_error", "budget_alert"],
))
```

### Features

- **Event filtering** — each target receives only subscribed event types
- **Retry with backoff** — configurable retry count with exponential delay (1s, 2s, 4s, ...)
- **Delivery history** — track success/failure per delivery for debugging
- **Async dispatch** — non-blocking delivery in background threads

---

## 5. CI Adapter (Phase 10)

GitHub Actions integration for running amiagi agent workflows in CI/CD pipelines.

### Capabilities

- `review_pr()` — automated PR code review
- `run_benchmark(suite_name)` — benchmark suite execution with results
- `run_tests()` — test orchestration
- Git helpers: `current_branch()`, `diff_stat()`, `changed_files()`

---

## Complete configuration reference

All web-interface related environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AMIAGI_DASHBOARD_PORT` | `8080` | Monitoring dashboard HTTP port |
| `AMIAGI_REST_API_PORT` | `8090` | REST API HTTP port |
| `AMIAGI_REST_API_TOKEN` | _(empty)_ | Bearer token for REST API auth |
| `AMIAGI_PLUGINS_DIR` | `./plugins` | Directory for plugin discovery |
| `AMIAGI_QUOTA_POLICY_PATH` | `./data/quota_policy.json` | Path to quota policy config |
| `AMIAGI_FEEDBACK_PATH` | `./data/human_feedback.jsonl` | Human feedback JSONL file |
| `AMIAGI_BENCHMARKS_DIR` | `./data/benchmarks` | Benchmark scenarios directory |
| `AMIAGI_BASELINES_DIR` | `./data/eval_baselines` | Evaluation baselines directory |
| `AMIAGI_TEAMS_DIR` | `./data/teams` | Team template JSON directory |

---

## TUI command summary (web-related)

| Command | Description |
|---------|-------------|
| `/dashboard start [port]` | Start monitoring dashboard (default: 8080) |
| `/dashboard stop` | Stop monitoring dashboard |
| `/dashboard status` | Check dashboard status |
| `/api start` | Start REST API server |
| `/api stop` | Stop REST API server |
| `/api status` | Check REST API status |
| `/plugins list` | List loaded plugins |
| `/plugins load <name>` | Load a plugin |
| `/budget status` | Per-agent cost summary |
| `/budget set <agent> <limit>` | Set agent budget limit |
| `/quota` | Show quota policy |
| `/eval history` | Evaluation run history |
| `/eval baselines` | Baseline scores |
| `/feedback summary` | Human feedback stats |
| `/team list` | Active teams |
| `/team templates` | Available team templates |
| `/team create <template>` | Create team from template |
| `/team status <id>` | Team details |
