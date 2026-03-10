# AmIAGI

[![CI](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml/badge.svg)](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Non-Commercial](https://img.shields.io/badge/license-non--commercial-orange.svg)](LICENSE)
[![Tests: 2868](https://img.shields.io/badge/tests-2868-brightgreen.svg)](tests/)
[![Release: 1.3.0](https://img.shields.io/badge/release-1.3.0-blueviolet.svg)](pyproject.toml)
[![Web UI: Operator Console](https://img.shields.io/badge/web%20ui-operator%20console-6f42c1.svg)](WEB_INTERFACE.md)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey.svg)]()

A local-first multi-agent orchestration platform for people asking a serious question: **Am I AGI?**

`AmIAGI` (package/repo: `amiagi`) is a full operating environment for autonomous AI teams: dynamic agent registry, task queueing, workflow execution, budget governance, evaluations, knowledge workflows, REST API, and a polished browser console for real operator control. It combines per-agent isolation, JSONL auditability, model governance, and multi-backend support across Ollama and OpenAI-compatible providers.

Current version: **v1.3.0** — UAT-ready operator console after Plan 02 closure, **2868 tests**.

v1.3.0 is the release where the browser experience stops being a sidecar and becomes the product's command layer: Mission Control, live event streams, inbox approvals, Model Hub, evaluations, knowledge, memory, budget, vault, settings, sessions, metrics, sandboxes, and admin tooling now form a cohesive web management console instead of a thin monitoring add-on.

Key release docs: [RELEASE_NOTES_v1.3.0.md](RELEASE_NOTES_v1.3.0.md), [GITHUB_RELEASE_v1.3.0.md](GITHUB_RELEASE_v1.3.0.md), [RELEASE_NOTES_v1.3.1.md](RELEASE_NOTES_v1.3.1.md), [GITHUB_RELEASE_v1.3.1.md](GITHUB_RELEASE_v1.3.1.md).

## Why v1.3.0 matters

- **AmIAGI finally looks like the system it already is** — not just a CLI experiment, but a controllable runtime for serious multi-agent work
- **Operator-grade Web Management Console** — full browser control surface for Supervisor, Agents, Teams, Tasks, Models, Evaluations, Knowledge, Memory, Budget, Vault, Settings, Sessions, Metrics, Inbox, Sandboxes, and Admin views
- **Real-time operational visibility** — live updates over WebSockets, health monitoring, event streams, status bars, and explicit runtime feedback for critical actions
- **Model governance that matches reality** — Ollama-first local model inventory, user-defined commercial registry, and configurable provider support for OpenAI, Anthropic, and Google
- **Safer management workflows** — permission-aware actions, vault-backed secrets, audit trail, shell policy controls, and clearer success/failure messaging
- **UAT-ready release** — final repair-plan closure, refreshed docs, and validated regression gates

## Web Management Console

The web interface is now one of AmIAGI's defining features, not a side panel.

This is the layer that turns an agent runtime into an operator product: you can supervise work, inspect state, intervene safely, manage models, review evaluations, browse knowledge, control budgets, and administer the system from one place.

- **Mission Control / Supervisor** — monitor active agents, current tasks, live logs, and operator interventions
- **Inbox and approvals** — handle Human-in-the-Loop requests with explicit approve/reject/reply flows
- **Model Hub** — inspect local Ollama models, assign models to roles, and manage commercial provider definitions
- **Knowledge + Evaluations** — run evaluation flows, inspect baselines, manage knowledge bases, sources, and indexing progress
- **Operations surface** — health, metrics, sessions, budget, vault, files, memory, cron, settings, sandboxes, and admin management in one browser UI

See [WEB_INTERFACE.md](WEB_INTERFACE.md) for architecture, routes, and startup details.

The v1.3.1 follow-up focuses on Supervisor operations: the browser now defaults to `/supervisor`, session runtime cost metrics in web mode are live, and repeated local web starts automatically reclaim a stale Amiagi GUI process when necessary.

## Safety Disclaimer (Read First)

This project can execute model-generated code and shell commands. Treat it as **high risk**.

- Use with the **highest caution**.
- Run only inside an **isolated virtual machine** (or equivalent sandboxed environment).
- Do not connect it to production systems, sensitive data, or privileged credentials.
- You are fully responsible for runtime isolation, network policy, and access control.

See [SECURITY.md](SECURITY.md) for mandatory safety recommendations.

## License and Usage Scope

This repository is released for:

- **Non-commercial use only**
- **Scientific and research use only**

Commercial usage is not permitted.

See [LICENSE](LICENSE) for full terms.

## Key Capabilities

### Model backends

- **Local LLM integration** with Ollama (any GGUF model)
- **External API support** via `OpenAIClient` — OpenAI, OpenRouter, Azure, vLLM, or any OpenAI-compatible endpoint
- **Per-role model assignment** — Polluks (executor) and Kastor (supervisor) can independently use local or API models
- **Interactive model selection wizard** at startup with session restore
- **Session persistence** — model-to-role assignments saved between sessions (`SessionModelConfig`)
- **Token usage tracking** for API models with real-time cost display (`UsageTracker`)

### Skills

- **Dynamic skill loading** — Markdown skill files from `skills/<role>/*.md` injected into system prompt
- **API-model conditional** — skills loaded only for large-context API models; local models skip to avoid context overflow
- **Per-role skill directories** — separate `skills/polluks/` and `skills/kastor/` directories

### Architecture & runtime

- Layered architecture (`domain`, `application`, `infrastructure`, `interfaces`)
- **`RouterEngine`** — shared orchestration core (routing, tool execution, watchdog, supervision, plan tracking) with `EventBus` for adapter communication
- **`EventBus`** — typed pub/sub with 5 event types (`LogEvent`, `ActorStateEvent`, `CycleFinishedEvent`, `SupervisorMessageEvent`, `ErrorEvent`)
- `ChatCompletionClient` protocol — structural interface all backends must satisfy
- Persistent memory in SQLite
- Full JSONL audit logs for:
  - model input/output/errors,
  - activity events and intents,
  - supervisor ↔ executor dialogue
- Permission-gated resource access (`disk.*`, `network.*`, `process.exec`)
- Controlled shell policy via allowlist
- Dynamic runtime behavior based on available VRAM
- Multi-actor communication protocol with addressed-block routing, unaddressed-turn reminders, and consultation rounds
- Deep tool-call resolution flow with iteration cap protection (`resolve_tool_calls`, max 15 steps)
- Tool name alias resolution (`file_read→read_file`, `dir_list→list_dir`) with per-tool correction tracking
- Adaptive supervisor watchdog with attempt caps/cooldown and plan-aware reactivation checks

### Agent management (Phase 1–2)

- **Dynamic agent registry** — register, unregister, lifecycle state tracking (IDLE/WORKING/PAUSED/ERROR/TERMINATED)
- **Agent factory** — programmatic agent creation from descriptors
- **Agent wizard** — natural language description → full agent blueprint (persona, skills, tools, test scenarios)
- **Lifecycle logging** — every state change logged to `logs/agent_lifecycle.jsonl`

### Task queue & work distribution (Phase 3)

- **Priority task queue** — CRITICAL/HIGH/NORMAL/LOW with dependency resolution
- **Task decomposer** — LLM-powered split of complex tasks into subtasks with DAG dependencies
- **Work assigner** — skill-based matching of tasks to idle agents with backpressure
- **Task scheduler** — periodic ready-task dispatch with deadline escalation

### Observability & dashboard (Phase 4)

- **Metrics collector** — in-memory ringbuffer for token usage, task duration, success/error rates
- **Alert manager** — configurable rules with severity-based alerting
- **Session replay** — event-based session reconstruction from JSONL logs
- **Web Management Console** — operator-facing browser UI spanning Supervisor, Inbox, Tasks, Teams, Models, Evaluations, Knowledge, Memory, Budget, Vault, Sessions, Metrics, Settings, and Admin views (see [WEB_INTERFACE.md](WEB_INTERFACE.md))
- **Health dashboard** — system health cards, VRAM monitoring, connection status, auto-refresh, and operational readiness visibility
- **Sandbox management** — per-agent sandbox admin with shell policy editor and explicit maintenance feedback

### Shared context & memory (Phase 5)

- **Shared workspace** — per-project workspace with file authorship tracking
- **Knowledge base** — searchable document store with TF-IDF matching
- **Context compressor** — LLM-powered conversation summarization for context window management
- **Cross-agent memory** — automatic key-findings sharing between agents

### Workflow engine (Phase 6)

- **DAG workflow definitions** — YAML-defined directed acyclic graphs with conditional branching
- **Workflow checkpoints** — serialized workflow state for crash recovery
- Predefined templates: `code_review.yaml`, `research.yaml`, `feature.yaml`

### Security & isolation (Phase 7)

- **Per-agent permission policies** — allowed tools, paths, network/shell access per agent
- **Protected system tools** — built-in runtime tools live in `src/amiagi/system_tools/` and are blocked from agent-side modification
- **Permission enforcer** — middleware that checks policy before every tool call
- **Sandbox manager** — isolated working directory per agent
- **Secret vault** — per-agent credential store with Fernet encryption, DB persistence, and access audit log
- **Audit chain** — full responsibility chain: who ordered, approved, and executed each action

### Resource & cost governance (Phase 8)

- **Budget manager** — per-agent cost tracking with 80%/100% threshold callbacks
- **Quota policy** — per-role configurable daily token/cost/request limits (JSON)
- **Rate limiter** — token-bucket rate limiting per backend with exponential backoff
- **VRAM scheduler** — priority-based GPU slot scheduling with idle-agent eviction

### Evaluation & quality (Phase 9)

- **Eval rubric** — weighted criteria scoring (normalized 0–100)
- **Eval runner** — pluggable scorer (keyword + LLM-as-judge) with full eval history
- **Benchmark suite** — category-based benchmark loading from `benchmarks/` directory
- **A/B test runner** — side-by-side comparison of two agent configurations
- **Regression detector** — JSON baseline comparison with configurable threshold
- **Human feedback collector** — thumbs up/down + comment persistence (JSONL)

### External integration & API (Phase 10)

- **REST API server** — HTTP API with bearer-token auth and pluggable routes (see [WEB_INTERFACE.md](WEB_INTERFACE.md))
- **Webhook dispatcher** — event-filtered webhooks with retry/backoff and delivery history
- **Plugin loader** — dynamic plugin discovery via `entry_points` and directory scanning
- **CI adapter** — GitHub Actions helpers (PR review, benchmark, test orchestration)
- **SDK client** — Python SDK for programmatic control over REST API

### Team composition (Phase 11)

- **Team definition** — structured team model with member descriptors and YAML persistence
- **Team composer** — heuristic + template-based team recommendation and assembly
- **Skill catalog** — searchable skill registry with tool/model matching
- **Dynamic scaler** — load-monitoring scaler with cooldown-based scale-up/down decisions
- **Team dashboard** — org chart, per-team metrics, and summary views
- **Router → TaskQueue bridge** — sponsor messages automatically decomposed into tasks
- Predefined templates: `team_backend.yaml`, `team_research.yaml`, `team_fullstack.yaml`, `data_pipeline.yaml`

### User experience

- **Readline-style input history** (up/down arrows) with persistent file-backed storage
- **Sponsor panel sanitization** — raw `tool_call` JSON is filtered from user-facing panel; technical details preserved in executor logs
- Runtime model switching from UI/CLI (`/models show`, `/models chose <nr>`, `/models current`)
- Kas tor model management (`/kastor-model show`, `/kastor-model chose <nr>`)
- API usage monitoring (`/api-usage`) and key verification (`/api-key verify`)
- Explicit multi-actor runtime visibility (Router, Polluks, Kastor, Terminal) in Textual status panel
- Directional supervision lanes in logs (`POLLUKS→KASTOR`, `KASTOR→ROUTER`) for clearer handoff tracing
- Interrupt-safe conversational mode in Textual (identity-aware reply + user decision follow-up)
- ASCII art landing page with randomized MOTD on startup (both CLI and Textual)
- Context-aware `/help` — shows only commands relevant to the active interface mode
- User message queue with position feedback when router cycle is busy

## Runtime Commands (CLI and Textual)

Model management commands:

- `/cls` — clears the main terminal screen
- `/cls all` — clears terminal screen and scrollback history
- `/models current` — shows both Polluks and Kastor with their assigned models and sources
- `/models show` — lists all available models (local Ollama + external API) with index numbers
- `/models chose <nr>` — switches Polluks (executor) model by index from `/models show`
- `/kastor-model show` — displays current Kastor (supervisor) model and source
- `/kastor-model chose <nr>` — switches Kastor model by index from the model list

API and usage commands:

- `/api-usage` — shows detailed API token usage, cost breakdown, and request count
- `/api-key verify` — re-verifies the OpenAI API key with masked output

Operational and diagnostics commands:

- `/queue-status` — shows model queue status and VRAM policy decision context
- `/capabilities [--network]` — checks tool/backend readiness (optionally includes network reachability)
- `/show-system-context [text]` — displays current system prompt/context used for model call
- `/goal-status` (alias: `/goal`) — shows goal/stage snapshot from `notes/main_plan.json`

Textual-focused actor/runtime commands:

- `/router-status` — shows actor states and runtime routing status
- `/idle-until <ISO8601|off>` — schedules/clears watchdog idle window

Notes:

- On startup, an interactive wizard guides model selection for both Polluks and Kastor.
- Previous model configuration is auto-restored if all models are still available.
- User-facing model output is sanitized: raw `tool_call`/JSON payloads are filtered from the Sponsor panel and preserved in technical logs.
- Input history (up/down arrows) persists across sessions.

Agent management commands (v0.6+):

- `/agents list` — table of all agents (id, name, role, state, model)
- `/agents info <id|name>` — detailed info for a single agent
- `/agents pause <id>` / `/agents resume <id>` / `/agents terminate <id>` — lifecycle control
- `/agent-wizard create <description>` — generate a new agent from natural language description
- `/agent-wizard blueprints` — list saved agent blueprints
- `/agent-wizard load <name>` — load a previously saved blueprint

Task management commands (v0.6+):

- `/tasks list` — all tasks with priority, status, agent assignment
- `/tasks add <title>` — create a new task
- `/tasks info <id>` — task details (partial id match)
- `/tasks cancel <id>` — cancel a pending/assigned task
- `/tasks stats` — pending / in-progress / done / failed counts

Dashboard commands (v0.6+):

- `/dashboard start [port]` — start the web monitoring dashboard (default port: 8080)
- `/dashboard stop` — stop the dashboard server
- `/dashboard status` — check whether the dashboard is running

Shared context & memory commands (v0.9+):

- `/knowledge search <query>` — search the knowledge base
- `/knowledge store <text>` — store a document in the knowledge base
- `/workspace list` — list files in the shared workspace
- `/workspace read <path>` — read a file from the shared workspace

Security & audit commands (v0.9+):

- `/audit show [limit]` — show recent audit chain entries
- `/sandbox status` — show sandbox isolation status per agent

Workflow commands (v0.9+):

- `/workflow run <name>` — execute a workflow (e.g. `code_review`, `research`, `feature`)
- `/workflow status` — show active workflow state
- `/workflow list` — list available workflow templates
- `/workflow pause` — pause the active workflow

Budget & quota commands (v1.0+):

- `/budget status` — show per-agent cost tracking summary
- `/budget set <agent> <limit>` — set cost limit for an agent
- `/budget reset <agent>` — reset budget counters for an agent
- `/quota` — show per-role quota policy

Evaluation & feedback commands (v1.0+):

- `/eval history` — show evaluation run history
- `/eval baselines` — list baseline scores
- `/feedback summary` — show human feedback statistics
- `/feedback up <comment>` — record positive feedback
- `/feedback down <comment>` — record negative feedback

REST API commands (v1.0+):

- `/api status` — show REST API server status
- `/api start` — start the REST API server
- `/api stop` — stop the REST API server

Plugin commands (v1.0+):

- `/plugins list` — list loaded plugins
- `/plugins load <name>` — load a plugin by name

Team commands (v1.0+):

- `/team list` — list active teams
- `/team templates` — list available team templates
- `/team create <template>` — create a team from a template
- `/team status <id>` — show team details and member status

## Web Interfaces

amiagi now exposes three HTTP-facing surfaces:

### Web Management Console (v1.3.0)

The main browser experience for operators. It delivers an integrated console for supervision, models, tasks, teams, evaluations, knowledge, vault, budget, settings, sessions, metrics, and admin flows.

```bash
pip install -e ".[web]"
amiagi --ui web
```

Open `http://localhost:8080` after startup. The web stack is built on Starlette, uses persistent storage, and supports real-time updates. Full documentation: [WEB_INTERFACE.md](WEB_INTERFACE.md).

### REST API

Programmatic HTTP API with bearer-token auth for external integrations, CI/CD, SDK clients, and automation.

```
/api start                # starts on port 8090 (AMIAGI_REST_API_PORT)
/api stop
```

### Legacy Monitoring Dashboard

The lightweight monitoring dashboard remains available for simple browser-based monitoring flows.

```
/dashboard start [port]   # default 8080, then open http://localhost:8080
/dashboard stop
```

## Current Runtime Behavior (Polluks/Kastor/Router)

- **Per-role backends**: Polluks and Kastor can use different models from different providers (Ollama, OpenAI, OpenRouter, etc.).
- **Skills injection**: when an API model is active, role-specific skills from `skills/<role>/` are injected into the system prompt.
- Textual interruptions are now decision-driven: after interrupt handling, runtime explicitly asks whether to continue, stop, or start a new task.
- Identity queries in interrupt mode are handled deterministically (Polluks identity response), avoiding accidental tool-flow drift.
- Auto-resume is blocked while identity decision is still pending, preventing unwanted continuation.
- Idle watchdog reactivation checks include actionable-plan context, not only passive turn counters.
- If tool-call resolution reaches iteration cap with unresolved calls, runtime emits explicit warning and marks router as stalled for visibility.
- Multi-actor communication protocol enforces addressed blocks (`[Sender -> Receiver]`), with automatic reminders for unaddressed turns and configurable consultation rounds.
- Supervisor `[Kastor -> Sponsor]` messages are routed to the user's main panel with sanitized content (no raw `tool_call` JSON).
- Unknown tool names are resolved via alias map; after max correction attempts, runtime forces a tool-creation plan.

## Project Structure

```text
src/amiagi/
  domain/             # domain models, enums, dataclasses
    agent_descriptor.py       # AgentDescriptor, AgentState, AgentRole
    task.py                   # Task, TaskStatus, TaskPriority
    quota_policy.py           # QuotaPolicy, RoleQuota
    eval_rubric.py            # EvalRubric, Criterion, EvalResult
    team_definition.py        # TeamDefinition, AgentDescriptor
    blueprint.py              # AgentBlueprint, TestScenario
    workflow_definition.py    # WorkflowDefinition, WorkflowNode
    permission_policy.py      # AgentPermissionPolicy
  application/        # use-cases, orchestration, protocols
    router_engine.py          # RouterEngine (shared orchestration core)
    event_bus.py              # EventBus (typed pub/sub for adapters)
    chat_service.py           # ChatService (main LLM conversation loop)
    tool_calling.py           # tool dispatch + alias resolution
    tool_registry.py          # ToolRegistry (dynamic tool registration)
    model_queue_policy.py     # VRAM-aware model queue policy
    budget_manager.py         # BudgetManager (per-agent cost tracking)
    eval_runner.py            # EvalRunner (rubric-based evaluation)
    ab_test_runner.py         # ABTestRunner (A/B agent comparison)
    regression_detector.py    # RegressionDetector (baseline comparison)
    plugin_loader.py          # PluginLoader (entry_points + dir scan)
    team_composer.py          # TeamComposer (heuristic recommendation)
    skill_catalog.py          # SkillCatalog (searchable skill registry)
    dynamic_scaler.py         # DynamicScaler (load-based scaling)
    agent_registry.py         # AgentRegistry (thread-safe CRUD)
    agent_factory.py          # AgentFactory (create agents from descriptors)
    router_task_bridge.py     # RouterTaskBridge (sponsor → tasks)
    wizard_conversation.py    # WizardConversation (multi-turn blueprint creation)
    task_queue.py             # TaskQueue (priority + dependency)
    work_assigner.py          # WorkAssigner (skill-based matching)
    workflow_engine.py        # WorkflowEngine (DAG interpreter)
    permission_enforcer.py    # PermissionEnforcer (middleware)
    audit_chain.py            # AuditChain (responsibility chain)
  infrastructure/     # IO, storage, runtime integrations
    ollama_client.py          # OllamaClient (local Ollama API)
    openai_client.py          # OpenAIClient (OpenAI-compatible API)
    usage_tracker.py          # UsageTracker + UsageSnapshot
    rate_limiter.py           # RateLimiter (token bucket)
    vram_scheduler.py         # VRAMScheduler (priority GPU scheduling)
    benchmark_suite.py        # BenchmarkSuite (category benchmarks)
    rest_server.py            # RESTServer (HTTP API, bearer auth)
    webhook_dispatcher.py     # WebhookDispatcher (event webhooks)
    ci_adapter.py             # CIAdapter (GitHub Actions integration)
    sdk_client.py             # AmiagiClient (Python SDK)
    dashboard_server.py       # DashboardServer (web monitoring + /api/budget)
    trace_viewer.py           # TraceViewer (request chain visualization)
    sandbox_manager.py        # SandboxManager (per-agent isolation)
    secret_vault.py           # SecretVault (per-agent credentials)
  interfaces/         # CLI and user interaction layer
    textual_cli.py            # Textual TUI adapter (thin, delegates to RouterEngine)
    cli.py                    # Synchronous CLI adapter (thin, delegates to RouterEngine)
    shared_cli_helpers.py     # Shared helpers for both CLI adapters
    human_feedback.py         # HumanFeedbackCollector (JSONL)
    team_dashboard.py         # TeamDashboard (org chart + metrics)
    dashboard_static/         # assets for the legacy monitoring dashboard
  sdk/                # AmiagiClient SDK package
tests/                # pytest suite (2868 tests)
config/               # shell allowlist policy
skills/               # per-role Markdown skill files
data/                 # local persistent DB, history, model config
  teams/              # team definition files
  benchmarks/         # benchmark scenario files
logs/                 # JSONL runtime and model logs
```

## Requirements

- Linux environment
- Python 3.10+
- Local Ollama server (`http://127.0.0.1:11434`)
- NVIDIA GPU with **minimum 24 GB VRAM**

## Install Ollama and Models

Install Ollama (Linux):

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Start local Ollama server:

```bash
ollama serve
```

Pull the models used by this project:

```bash
ollama pull hf.co/TeichAI/Qwen3-14B-Claude-4.5-Opus-High-Reasoning-Distill-GGUF:Q4_K_M
ollama pull cogito:14b
```

Recommended `.env` model settings:

```env
OLLAMA_MODEL=hf.co/TeichAI/Qwen3-14B-Claude-4.5-Opus-High-Reasoning-Distill-GGUF:Q4_K_M
AMIAGI_SUPERVISOR_MODEL=cogito:14b
```

## Installation

### Recommended: one-command install

```bash
bash install.sh
```

The installer checks prerequisites (Python 3.10+, GPU, Ollama), creates a
virtualenv, installs all dependencies, configures `.env`, and optionally
pulls the Ollama models.

### Minimal (venv only, no checks)

```bash
bash scripts/setup_venv.sh
source .venv/bin/activate
```

### Optional (Conda environment with your own name)

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda create -n <your_env_name> python=3.10 -y
conda activate <your_env_name>
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

For development and tests:

```bash
pip install -r requirements-dev.txt
```

### Alternative (new local virtual environment)

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## Configure Environment

Copy `.env.example` to `.env` and adjust values if needed.

```bash
cp .env.example .env
```

### External API models (optional)

To use OpenAI-compatible API models, set these in your `.env`:

```env
OPENAI_API_KEY=sk-your-api-key-here
OPENAI_BASE_URL=https://api.openai.com/v1    # or OpenRouter, Azure, etc.
OPENAI_REQUEST_TIMEOUT_SECONDS=120
```

The model selection wizard at startup will offer both local Ollama and external API models.

### Skills directory (optional)

Custom skills can be added as Markdown files in `skills/<role>/`:

```env
AMIAGI_SKILLS_DIR=./skills
```

Skills are loaded only for API models with large context windows.

## Run

Activate your environment first:

```bash
source .venv/bin/activate    # virtualenv
# or
conda activate <your_env_name>  # conda
```

### Quick reference

| Command | Description |
|---------|-------------|
| `amiagi` | Standard launch — interactive model wizard, then Textual TUI |
| `amiagi --auto` | Autonomous mode — agent works without waiting for user confirmation |
| `amiagi --cold_start` | Full reset — removes `amiagi-my-work` contents and runtime data before launch |
| `amiagi --cold_start --auto` | Full reset + autonomous — best for starting a brand new project |
| `amiagi --ui textual` | Textual TUI (default) — multi-panel interface with actor status |
| `amiagi --ui cli` | Classic synchronous CLI — simple stdin/stdout loop |
| `amiagi --ui web` | Web Management Console — operator-grade browser UI on `http://localhost:8080` |
| `amiagi --lang en` | English interface |
| `amiagi --lang pl` | Polish interface (default) |
| `amiagi --vram-off` | Disable VRAM monitoring — let Ollama manage GPU memory |

### Usage scenarios

**Web Management Console — browser-based operator surface:**
```bash
pip install -e ".[web]"   # first time only — installs Starlette, asyncpg, etc.
amiagi --ui web
```
Opens the full Web Management Console at `http://localhost:8080`.  
Recommended setup: **PostgreSQL 13+** for full persistence, with SQLite fallback
available for development, plus **Ollama** for local LLM inference.  
See [WEB_INTERFACE.md](WEB_INTERFACE.md) for full documentation covering routes,
RBAC, OAuth2, migrations, runtime behavior, and the operator-facing feature set.

**First launch — getting started:**
```bash
amiagi
```
The interactive wizard guides you through model selection for both roles
(Polluks — executor, Kastor — supervisor). Your choices are saved for
future sessions.

**Starting a new project (full reset):**
```bash
amiagi --cold_start
```
Removes runtime state from previous work, including:
- contents of `amiagi-my-work/`
- SQLite memory and auxiliary databases
- JSONL logs (model I/O, activity, supervision dialogue, audit, mailbox)
- saved model configuration and input history
- shared workspace, sandboxes, workflow checkpoints, cross-agent memory

Use this when switching to a completely different project or when you need to guarantee that no prior workspace artifact influences the next run.

**Autonomous mode — let the agent work independently:**
```bash
amiagi --auto
```
The agent executes tools and follows its plan without asking for user
confirmation at each step. The supervisor (Kastor) still monitors quality.
Ideal for longer tasks like code generation or research.

**Fresh project + autonomous (most common for new tasks):**
```bash
amiagi --cold_start --auto
```
Combines both: full runtime reset + agent runs independently. The recommended
way to start a brand new coding or research task.

**English interface:**
```bash
amiagi --lang en
```
All UI strings, help, and status messages switch to English.
Alternatively set `AMIAGI_LANG=en` in your `.env`.

**Classic CLI instead of Textual TUI:**
```bash
amiagi --ui cli
```
Simple synchronous terminal — useful for SSH sessions, low-bandwidth
connections, or scripting. All commands work identically.

**Low VRAM / shared GPU:**
```bash
amiagi --vram-off
```
Disables runtime VRAM checks and model queue scheduling. Ollama manages
GPU memory on its own. Use when running on a shared machine or with
limited GPU.

**Custom startup context:**
```bash
amiagi --startup_dialogue_path ./my-project/context.md
```
Provides a Markdown file with project context that seeds the agent's
initial memory. Defaults to `wprowadzenie.md` in the work directory.

**Combining everything:**
```bash
amiagi --cold_start --auto --lang en --ui textual --vram-off
```
Full reset, autonomous, English, Textual TUI, no VRAM control.

### Environment variables (.env)

Key variables that affect runtime behavior:

```env
# Model configuration
OLLAMA_MODEL=hf.co/TeichAI/...          # Executor (Polluks) model
AMIAGI_SUPERVISOR_MODEL=cogito:14b      # Supervisor (Kastor) model
AMIAGI_SUPERVISOR_ENABLED=true          # Enable/disable supervisor

# Autonomous behavior
AMIAGI_AUTONOMOUS_MODE=true             # Same as --auto flag
AMIAGI_MAX_IDLE_AUTOREACTIVATIONS=2     # Max idle reactivation cycles

# Language
AMIAGI_LANG=en                          # Same as --lang flag

# Paths
AMIAGI_WORK_DIR=./amiagi-my-work        # Agent's working directory
AMIAGI_DB_PATH=./data/amiagi.db         # SQLite memory database
AMIAGI_SHELL_POLICY_PATH=./config/shell_allowlist.json
```

See `.env.example` for the full list with defaults.

## Tests

```bash
pytest
```

## Continuous Integration

GitHub Actions workflow runs the full test suite on every push and pull request.

- Workflow file: `.github/workflows/ci.yml`
- Python versions: 3.10, 3.11, 3.12

## Notes on Naming

The package code namespace remains `amiagi` for compatibility with existing imports. The distribution and project identity are now `amiagi`.

## Contributing

Contribution guidelines are available in [CONTRIBUTING.md](CONTRIBUTING.md).

## Release Process

Pre-release checklist is available in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
Current unreleased changes: [RELEASE_NOTES_UNRELEASED.md](RELEASE_NOTES_UNRELEASED.md).
Latest release notes: [RELEASE_NOTES_v1.3.0.md](RELEASE_NOTES_v1.3.0.md).
Previous releases: [v1.1.0](RELEASE_NOTES_v1.1.0.md) · [v1.0.3](RELEASE_NOTES_v1.0.3.md) · [v1.0.2](RELEASE_NOTES_v1.0.2.md) · [v1.0.1](RELEASE_NOTES_v1.0.1.md) · [v1.0.0](RELEASE_NOTES_v1.0.0.md).
Roadmap: [ROADMAP_v1.0.md](ROADMAP_v1.0.md).

## Polish Documentation

Polish documentation is available in [README.pl.md](README.pl.md).
