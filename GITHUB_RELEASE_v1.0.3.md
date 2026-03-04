# amiagi v1.0.3

**Architectural extraction release** — shared `RouterEngine` + `EventBus` orchestration core replaces duplicated logic in both UI adapters.

## ✨ Highlights

### RouterEngine — shared orchestration core
The monolithic orchestration logic previously duplicated across `textual_cli.py` (5 312 LOC) and `cli.py` (3 126 LOC) has been extracted into a single `RouterEngine` class using the **Strangler Fig** migration pattern.

Both adapters are now **thin UI shells**:
- `textual_cli.py`: 5 312 → **1 176 LOC** (−78%)
- `cli.py`: 3 126 → **284 LOC** (−91%)

### EventBus — typed pub/sub
New `EventBus` with 5 typed events (`LogEvent`, `ActorStateEvent`, `CycleFinishedEvent`, `SupervisorMessageEvent`, `ErrorEvent`) decouples the engine from presentation. Any future adapter (Web GUI, API) simply subscribes to events.

### What moved to the engine
- Tool execution and resolution (`execute_tool_call`, `resolve_tool_calls`)
- Full router cycle (`process_user_turn`, `dispatch_user_turn`, `drain_user_queue`)
- Supervisor watchdog with idle detection and nudge logic
- Auto-resume for paused plans
- Plan tracking, fingerprinting, and premature-completion detection
- Collaboration signals and communication rules enforcement
- Idle reactivation cycle
- Pending user decision handling

### Shared CLI helpers
7 helper functions and 3 constants extracted to `shared_cli_helpers.py` (211 LOC) — both adapters import from the shared module instead of from each other.

## 📊 Architecture

```
┌──────────────┐    EventBus     ┌──────────────┐
│ Textual TUI  │◄───(events)────►│ RouterEngine  │
│  1 176 LOC   │    ──(calls)──► │  3 705 LOC    │
└──────────────┘                 └──────┬───────┘
       │                                │
       └──── shared_cli_helpers ────────┘
             (211 LOC)                  │
┌──────────────┐    EventBus            │
│  CLI (sync)  │◄───(LogEvent)──────────┘
│   284 LOC    │    ──(calls)──►
└──────────────┘
```

## 🆕 New modules

| Layer | Module | LOC | Description |
|-------|--------|-----|-------------|
| application | `router_engine.py` | 3 705 | Shared orchestration core |
| application | `event_bus.py` | 137 | Typed pub/sub for adapter decoupling |
| interfaces | `shared_cli_helpers.py` | 211 | Helpers shared between CLI and TUI |

## 🧪 Validation

- **1 177 tests passed** (up from 1 069 in v1.0.2)
- 91 new RouterEngine tests
- 15 new EventBus tests
- 89 test files, 101 source modules
- Zero regressions — full backward compatibility

## 📦 File size comparison

| File | v1.0.2 LOC | v1.0.3 LOC | Δ |
|------|-----------|-----------|---|
| `cli.py` | 3 067 | 284 | −2 783 |
| `textual_cli.py` | 5 312 | 1 176 | −4 136 |
| `router_engine.py` | (new) | 3 705 | +3 705 |
| `event_bus.py` | (new) | 137 | +137 |
| `shared_cli_helpers.py` | (new) | 211 | +211 |
| **Net** | **8 379** | **5 513** | **−2 866** |

## 🔒 Safety

- No permission policy expansion or shell allowlist relaxation
- Use only in isolated/sandboxed environments as described in `SECURITY.md`

## 📋 Compatibility

- Python: 3.10+
- OS: Linux
- No new runtime dependencies

## ⬆️ Upgrade

No breaking changes. All CLI commands and runtime behavior remain identical. The internal architecture change is transparent to end users.

```bash
git pull
pip install -e .
```
