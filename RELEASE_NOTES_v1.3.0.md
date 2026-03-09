# Release Notes — v1.3.0

**Release date:** 2026-03-09  
**Codename:** UAT Readiness  
**Previous release:** v1.2.0 (Web Management Console)

## Highlights

- **2868 tests** collected in the current suite, with critical pre-UAT regression gates passing
- **Plan 02 closed** — operator-facing parity, runtime semantics, and workflow contract fixes completed
- **UAT-ready web console** — major operational screens now provide explicit feedback and safer action flows
- **Ollama-first local model catalog restored** — local models come from `ollama list`, not hardcoded cloud defaults
- **Commercial model registry clarified** — default empty, user-defined only, with configurable OpenAI / Anthropic / Google providers
- **Release hygiene improved** — restrictive default permissions restored for release state

## What's New Since v1.2.0

### 1. Operator readiness and dashboard parity

- Completed the remaining repair plan work required to treat the web console as an operator-grade surface
- Closed gaps between Mission Control / Supervisor workflows and the rest of the management views
- Normalized success/error messaging so critical actions no longer rely on weak browser-native dialogs
- Improved action semantics in Teams, Settings, Model Hub, Agents, Evaluations, and Knowledge flows

### 2. Runtime semantics and backend/frontend contract fixes

- Hardened Teams, Evaluations, and Knowledge behavior around real runtime state rather than optimistic placeholders
- Fixed workflow-related UI/backend contract mismatches so the operator sees reliable results and explicit failures
- Preserved text-first, administrative interaction patterns across the console

### 3. Model management corrected

- Removed hardcoded default commercial models that incorrectly replaced local Ollama inventory
- Local model inventory now prefers shell output from `ollama list`
- Runtime fallback remains available when CLI access is not possible
- Commercial model list now reflects only explicit user configuration
- Added configurable Google provider support alongside OpenAI and Anthropic in the commercial model UI

### 4. Release and operational safety

- Restored release-safe default for permissions configuration (`allow_all: false`)
- Updated project documentation to reflect the current UAT-ready state
- Prepared an internal UAT scenario pack for functional validation

## Key Files Updated

- `src/amiagi/interfaces/web/routes/model_routes.py`
- `src/amiagi/interfaces/web/routes/model_hub_routes.py`
- `src/amiagi/interfaces/web/templates/model_hub.html`
- `src/amiagi/interfaces/web/static/js/model_hub.js`
- `tests/test_faza9_dashboard.py`
- `tests/test_model_hub_ui.py`
- `README.md`
- `README.pl.md`
- `WEB_INTERFACE.md`
- `config/permissions.json`

## Test Summary

### Final readiness gate

- Critical operator regression pack: **193 passed**

### Model regression verification

- Focused model/UI regression pack: **58 passed**

### Suite size

- Current collection size: **2868 tests collected**

## Breaking Changes

None intended.

## Upgrade Notes

- Verify local model visibility through Ollama runtime availability on the target machine
- Reconfigure commercial providers only through explicit user-supplied provider/model settings
- Revalidate permission policy if a non-default deployment intentionally uses broader execution allowances

## Documentation

- [README.md](README.md)
- [README.pl.md](README.pl.md)
- [WEB_INTERFACE.md](WEB_INTERFACE.md)
