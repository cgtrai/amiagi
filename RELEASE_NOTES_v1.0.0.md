# amiagi v1.0.0

Major milestone release: all 11 roadmap phases complete.  
Transition from "collection of agents" to "managed team framework".

## Highlights

### Phase 8 — Resource & Cost Governance
- **BudgetManager**: per-agent cost tracking with threshold callbacks (80% warning, 100% block).
- **QuotaPolicy**: per-role configurable limits (daily tokens, daily cost USD, requests/hour) via JSON.
- **RateLimiter**: token-bucket rate limiting per backend with exponential backoff.
- **VRAMScheduler**: priority-based GPU/VRAM scheduling with idle-agent eviction.
- TUI commands: `/budget status|set|reset`, `/quota`.

### Phase 9 — Evaluation & Quality Framework
- **EvalRubric**: weighted criteria scoring (normalized 0–100).
- **EvalRunner**: pluggable scorer (keyword + LLM-as-judge), full eval history.
- **BenchmarkSuite**: category-based benchmark loading from `benchmarks/` directory.
- **ABTestRunner**: side-by-side comparison of two agent configurations.
- **RegressionDetector**: JSON baseline comparison with configurable threshold.
- **HumanFeedbackCollector**: thumbs up/down + comment persistence to JSONL.
- TUI commands: `/eval history|baselines`, `/feedback summary|up|down`.

### Phase 10 — External Integration & API
- **RESTServer**: HTTP API with bearer-token auth and pluggable route handlers.
- **WebhookDispatcher**: event-filtered webhooks with retry/backoff and delivery history.
- **PluginLoader**: dynamic plugin discovery via `entry_points` and directory scan.
- **CIAdapter**: GitHub Actions helpers (PR review, benchmark, test orchestration).
- **AmiagiClient (SDK)**: Python SDK for programmatic control over REST API.
- TUI commands: `/api status|start|stop`, `/plugins list|load`.

### Phase 11 — Persona & Team Composition
- **TeamDefinition**: structured team model with member descriptors and JSON persistence.
- **TeamComposer**: heuristic + template-based team recommendation and assembly.
- **SkillCatalog**: searchable skill registry with tool/model matching.
- **DynamicScaler**: load-monitoring scaler with cooldown-based scale-up/down decisions.
- **TeamDashboard**: org chart, per-team metrics, and summary views.
- 3 predefined team templates: `team_backend.json`, `team_research.json`, `team_fullstack.json`.
- TUI commands: `/team list|templates|create|status`.

### Integration
- `config.py` extended with 8 new settings: `quota_policy_path`, `feedback_path`, `benchmarks_dir`, `baselines_dir`, `rest_api_port`, `rest_api_token`, `plugins_dir`, `teams_dir`.
- `main.py` bootstraps all Phase 8–11 services.
- `textual_cli.py` dispatches 7 new TUI command families via dedicated handlers.

## New modules (20)

| Layer | Module | Phase |
|-------|--------|-------|
| domain | `quota_policy.py` | 8 |
| domain | `eval_rubric.py` | 9 |
| domain | `team_definition.py` | 11 |
| application | `budget_manager.py` | 8 |
| application | `eval_runner.py` | 9 |
| application | `ab_test_runner.py` | 9 |
| application | `regression_detector.py` | 9 |
| application | `plugin_loader.py` | 10 |
| application | `team_composer.py` | 11 |
| application | `skill_catalog.py` | 11 |
| application | `dynamic_scaler.py` | 11 |
| infrastructure | `rate_limiter.py` | 8 |
| infrastructure | `vram_scheduler.py` | 8 |
| infrastructure | `benchmark_suite.py` | 9 |
| infrastructure | `rest_server.py` | 10 |
| infrastructure | `webhook_dispatcher.py` | 10 |
| infrastructure | `ci_adapter.py` | 10 |
| infrastructure | `sdk_client.py` | 10 |
| interfaces | `human_feedback.py` | 9 |
| interfaces | `team_dashboard.py` | 11 |

## New test files (20)

`test_quota_policy.py`, `test_budget_manager.py`, `test_rate_limiter.py`, `test_vram_scheduler.py`,
`test_eval_rubric.py`, `test_eval_runner.py`, `test_benchmark_suite.py`, `test_ab_test_runner.py`,
`test_regression_detector.py`, `test_human_feedback.py`,
`test_rest_server.py`, `test_webhook_dispatcher.py`, `test_plugin_loader.py`, `test_ci_adapter.py`, `test_sdk_client.py`,
`test_team_definition.py`, `test_team_composer.py`, `test_skill_catalog.py`, `test_dynamic_scaler.py`, `test_team_dashboard.py`.

## Compatibility

- Python: 3.10+
- OS: Linux

## Validation

- Full test suite: **815 passed** (203 new tests from Phases 8–11).
- 0 Pylance errors.

## Safety

No permission policy expansion and no shell allowlist relaxation were introduced in this release.

Use only in isolated/sandboxed environments as described in `SECURITY.md`.
