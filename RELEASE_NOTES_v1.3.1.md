# Release Notes — v1.3.1

**Release date:** 2026-03-10  
**Codename:** Supervisor Operations  
**Previous release:** v1.3.0 (UAT Readiness)

## Highlights

- **Supervisor promoted to the default operator surface** with a communication-first layout instead of a passive status dashboard
- **Session runtime metrics fixed in web mode** so token usage, session cost, and energy cost now accumulate correctly
- **Startup takeover added for web mode** so a stale local GUI process is terminated before rebinding the configured port
- **Stream continuity hardened** with replayable recent history, reconnect feedback, and normalized routing metadata
- **Operator workflow tooling extended** with project-scoped skills, runtime skill refresh, and task dossier guidance

## What's New Since v1.3.0

### 1. Supervisor and operator UX

- Reworked `/supervisor` into the main operator workspace and changed `/` to redirect there by default
- Simplified the topbar to emphasize live operational metrics: GPU RAM used, GPU utilization, session tokens, and session cost
- Replaced noisy counters and duplicated queue widgets with a cleaner command-focused layout
- Added embedded communication screens for active agents, including Kastor and Router-oriented visibility where applicable
- Localized and standardized agent card actions, statuses, tooltips, and empty states
- Removed sidebar group labels and cleaned shared navigation chrome for a denser operator view
- Renamed the brand presentation in the web shell to `AmIAGI`

### 2. Runtime cost and usage accounting

- Wired runtime model-usage callbacks from both Ollama and OpenAI-compatible clients into the budget layer
- Fixed the OpenAI usage callback path so budget updates no longer depend on an optional usage tracker
- Included energy cost in session-level cost presentation for web surfaces
- Centralized runtime metric aggregation in `runtime_metrics.py`
- Updated budget configuration endpoints so runtime token and energy pricing changes affect active web state

### 3. Web stream continuity and observability

- Added `stream.config` handshake payloads for active session context and retention metadata
- Added `stream.history` replay on connect and reconnect via `since_id`
- Normalized stream payloads with semantic fields such as `message_type`, `from`, `to`, `status`, and `thread_owners`
- Added reconnect UI feedback and bounded local retention hints for embedded communication screens
- Ensured Kastor-facing, supervisor-facing, and targeted agent events route to the correct visible screen

### 4. Web startup resilience

- Added PID-file based stale web process cleanup before starting Uvicorn
- Used Linux `/proc` port and process inspection to terminate only matching local Amiagi web processes
- Cleaned PID ownership on shutdown so repeated local starts do not leave stale runtime markers behind

### 5. Skills and executor guidance

- Added a file-based project skill repository under `skills/` for project-scoped runtime instructions
- Added runtime skill refresh integration so admin changes immediately affect selection and recommendation
- Added task dossier generation to provide executor-facing skill and tool recommendations for current sponsor work
- Added admin web routes and UI for managing project skills

### 6. Runtime hygiene and tool surface

- Extended cold start so persistent runtime artifacts and working directories are actually reset, not just selected logs
- Added protected-system-tools write denial in permission enforcement
- Added `analyze_workspace` as a supported system tool for workspace inventory reporting in text and JSON formats
- Fixed model-config persistence to respect the configured settings path in web routes

## Key Files Updated

- `src/amiagi/interfaces/web/templates/supervisor.html`
- `src/amiagi/interfaces/web/static/js/supervisor.js`
- `src/amiagi/interfaces/web/static/js/components/live-stream.js`
- `src/amiagi/interfaces/web/routes/system_routes.py`
- `src/amiagi/interfaces/web/runtime_metrics.py`
- `src/amiagi/interfaces/web/run.py`
- `src/amiagi/interfaces/web/web_adapter.py`
- `src/amiagi/interfaces/web/stream_contract.py`
- `src/amiagi/interfaces/web/skills/project_skill_repository.py`
- `src/amiagi/interfaces/web/skills/runtime_skill_provider.py`
- `src/amiagi/application/task_dossier_builder.py`
- `src/amiagi/main.py`
- `WEB_INTERFACE.md`

## Validation

Validated in this release-prep cycle:

- `tests/test_web_app_health.py -q` → **13 passed**
- `tests/test_supervisor_web_ui.py tests/test_notifications_drawer_extended.py tests/test_faza11_responsive.py -q` → **38 passed**

Previously validated during the same fix stream:

- focused cost/UI/budget pack → **33 passed**

## Breaking Changes

None intended.

## Upgrade Notes

- Web operators should now treat `/supervisor` as the primary entrypoint instead of `/dashboard`
- Session cost displayed in web mode now includes energy cost in addition to token/model usage
- Repeated local web starts will actively reclaim the configured port from a stale Amiagi GUI instance
- Project-specific skills can now be stored on disk and refreshed into runtime selection without a full restart

## Documentation

- [README.md](README.md)
- [WEB_INTERFACE.md](WEB_INTERFACE.md)
- [RELEASE_NOTES_UNRELEASED.md](RELEASE_NOTES_UNRELEASED.md)
- [devdocs/UAT_SCENARIOS_v1.3.1.md](devdocs/UAT_SCENARIOS_v1.3.1.md)