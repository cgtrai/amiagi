# amiagi v1.0.1

Patch release: documentation alignment, naming consistency, and format migrations.

## Changes

### Naming consistency (P1)
- Renamed `ollama_client` field → `model_client` across `ChatService`, `SupervisorService`, `AgentFactory`, `main.py`, and all related test files to reflect backend-agnostic architecture.

### Blueprint persistence — JSON → YAML (R1)
- `AgentWizard` now saves and loads blueprints as `.yaml` files instead of `.json`.
- `list_blueprints()` scans for `*.yaml` in the blueprints directory.

### Router → TaskQueue bridge (R2)
- New `RouterTaskBridge` class in `application/router_task_bridge.py` — automatically decomposes sponsor messages into prioritized tasks via `TaskQueue`.
- 7 new tests in `test_router_task_bridge.py`.

### Workflow format migration — JSON → YAML (R3)
- Converted `code_review.json`, `research.json`, `feature.json` → `.yaml` in `data/workflows/`.
- Updated `textual_cli.py` glob patterns from `*.json` to `*.yaml`.

### Team template renaming (R4)
- `backend_api.yaml` → `team_backend.yaml`
- `fullstack.yaml` → `team_fullstack.yaml`
- Consistent `team_` prefix across all templates in `config/team_templates/`.

### Budget dashboard panel (R5)
- New `/api/budget` endpoint in `DashboardServer`.
- New 💰 Costs panel in `index.html` with live cost tracking per agent.

### Dashboard CSS extraction
- Moved all inline `<style>` from `index.html` to external `dashboard.css`.
- Served via `/static/dashboard.css` route.
- Replaced inline `style=` attributes with CSS classes.

### Documentation
- Added missing README badges: Python version, License, Tests count, Version, Platform.
- Updated test count from 815 → 1045 across all documentation files.
- Fixed stale JSON references → YAML in README.md, README.pl.md, RELEASE_NOTES_v1.0.0.md.
- Added new modules (`router_task_bridge.py`, `wizard_conversation.py`, `trace_viewer.py`) to project structure in README.
- Added `sdk/` directory to project structure.
- Created `RELEASE_NOTES_v1.0.1.md`.

## New modules

| Layer | Module | Description |
|-------|--------|-------------|
| application | `router_task_bridge.py` | RouterTaskBridge — sponsor → task decomposition |

## New test files

`test_router_task_bridge.py` (7 tests).

## Validation

- Full test suite: **1045 passed**, 2 warnings.
- 85 test files, 84 source modules.

## Compatibility

- Python: 3.10+
- OS: Linux
