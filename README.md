# amiagi

[![CI](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml/badge.svg)](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Non-Commercial](https://img.shields.io/badge/license-non--commercial-orange.svg)](LICENSE)
[![Tests: 1177](https://img.shields.io/badge/tests-1177%20passed-brightgreen.svg)](tests/)
[![Version: 1.0.3](https://img.shields.io/badge/version-1.0.3-blueviolet.svg)](pyproject.toml)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey.svg)]()

A local, CLI-first framework for orchestrating autonomous LLM agent teams in controlled environments.

`amiagi` is a full-featured agent orchestration platform: dynamic agent registry, task queuing, workflow engine, budget governance, evaluation framework, REST API, web dashboard, and team composition — all with per-agent security isolation, JSONL audit logs, and multi-backend support (Ollama, OpenAI, OpenRouter, Azure, vLLM).

Current version: **v1.0.3** — all 11 roadmap phases complete, **1177 tests**.

v1.0.3 introduces a shared `RouterEngine` + `EventBus` orchestration core — both the Textual TUI and synchronous CLI are now thin adapters delegating all routing, tool execution, watchdog, and supervision logic to a single engine.

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
- **Web dashboard** — real-time browser UI with agents, tasks, metrics, events (see [WEB_INTERFACE.md](WEB_INTERFACE.md))

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
- **Permission enforcer** — middleware that checks policy before every tool call
- **Sandbox manager** — isolated working directory per agent
- **Secret vault** — per-agent credential store with cross-agent isolation
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

amiagi provides two HTTP-based interfaces. For full details see [WEB_INTERFACE.md](WEB_INTERFACE.md).

### Monitoring Dashboard (Phase 4)

A single-page browser application (vanilla JS, zero dependencies) with four panels: Agents, Tasks, Metrics, and Event Log. Auto-refreshes every 5 seconds with SSE live-push support.

```
/dashboard start [port]   # default 8080, then open http://localhost:8080
/dashboard stop
```

### REST API (Phase 10)

Programmatic HTTP API with bearer-token auth for external integrations, CI/CD, SDK clients.

```
/api start                # starts on port 8090 (AMIAGI_REST_API_PORT)
/api stop
```

See [WEB_INTERFACE.md](WEB_INTERFACE.md) for endpoints, configuration, and SDK usage.

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
    dashboard_static/         # HTML/CSS/JS for web dashboard
  sdk/                # AmiagiClient SDK package
tests/                # pytest suite (1177 tests)
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

### Recommended for GitHub users (auto-create virtual environment)

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

If you use the local `.venv`, activate it first:

```bash
source .venv/bin/activate
```

If you use Conda, activate your environment first:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate <your_env_name>
```

Preferred CLI command:

```bash
amiagi
```

Backward-compatible command:

```bash
amiagi
```

Alternative module launch:

```bash
python -m main
```

Useful runtime modes:

```bash
python -m main --cold_start
python -m main --auto
python -m main --cold_start --auto
```

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
Latest release notes: [RELEASE_NOTES_v1.0.3.md](RELEASE_NOTES_v1.0.3.md).
Previous releases: [v1.0.2](RELEASE_NOTES_v1.0.2.md) · [v1.0.1](RELEASE_NOTES_v1.0.1.md) · [v1.0.0](RELEASE_NOTES_v1.0.0.md).
Roadmap: [ROADMAP_v1.0.md](ROADMAP_v1.0.md).

## Polish Documentation

Polish documentation is available in [README.pl.md](README.pl.md).
