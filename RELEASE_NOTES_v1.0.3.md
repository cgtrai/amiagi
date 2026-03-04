# amiagi v1.0.3

Release focused on architectural extraction of orchestration logic into a shared
`RouterEngine` + `EventBus` core, eliminating duplication between the Textual TUI
and synchronous CLI adapters.

## Highlights

### RouterEngine — shared orchestration core
- `RouterEngine` (3 705 LOC) in `src/amiagi/application/router_engine.py`:
  single-source-of-truth for tool execution, tool-call resolution, supervision
  watchdog, auto-resume, plan tracking, and collaboration signals.
- `EventBus` (137 LOC) in `src/amiagi/application/event_bus.py`:
  typed pub/sub with 5 event types (`LogEvent`, `ActorStateEvent`,
  `CycleFinishedEvent`, `SupervisorMessageEvent`, `CollaborationEvent`).
- Public property API on RouterEngine (`last_progress_monotonic`,
  `supervisor_outbox_size`, `comm_rules`, `pending_user_decision`) — adapters
  no longer touch engine internals.
- Textual TUI (`textual_cli.py`) delegates all orchestration methods to the
  engine and subscribes to events for UI updates.
- Synchronous CLI (`cli.py`) delegates `execute_tool_call` to the engine,
  subscribes to `LogEvent` for runtime notices (e.g. microphone recording status).

### Shared CLI helpers module
- Extracted 7 helper functions and 3 constants shared between `cli.py` and
  `textual_cli.py` into `src/amiagi/interfaces/shared_cli_helpers.py` (211 LOC):
  `_build_landing_banner`, `_fetch_ollama_models`, `_set_executor_model`,
  `_select_executor_model_by_index`, `_network_resource_for_model`,
  `_read_plan_tracking_snapshot`, `_repair_plan_tracking_file`.
- `textual_cli.py` no longer imports from `cli.py` — both adapters import
  from the shared module.

### execute_tool_call deduplication
- Removed ~540 LOC duplicated `execute_tool_call` closure from `cli.py`;
  replaced with 3-line delegation to `RouterEngine.execute_tool_call()`.
- Ported `record_microphone_clip` and `capture_camera_frame` handlers to the
  engine (previously CLI-only).
- Added engine-level guards: empty-JSON write prevention, `run_python`
  path-outside-work-dir check, auto-overwrite for `notes/main_plan.json`.

### Watchdog, auto-resume, supervision dialogue (Faza 3)
- Extracted `watchdog_tick`, `auto_resume_tick`, `poll_supervision_dialogue`
  from Textual adapter into RouterEngine.
- Textual timers now call thin engine wrappers.
- Added 17 new engine-level tests for these methods.

### Dead code & wrapper cleanup
- Removed ~120 LOC of orphaned helper functions from `cli.py`
  (`_detect_preferred_microphone_device`, `_build_microphone_profiles`,
  `_default_artifact_path`, `_path_candidate_from_argument`,
  `_is_main_plan_tracking_path`, `_task_has_required_fields`,
  `resolve_tool_path` closure, `emit_runtime_notice` closure,
  `_format_user_facing_answer`, `parse_tool_calls` import,
  `_is_path_within_work_dir` / `_parse_search_results_from_html` /
  `_resolve_tool_path` re-exports).
- Removed 3 unused thin wrappers from `textual_cli.py`
  (`_record_collaboration_signal`, `_set_plan_paused`,
  `_auto_resume_paused_plan_if_needed`).
- Consolidated `_comm_rules` — adapters now use `RouterEngine.comm_rules`
  property; eliminated duplicate `load_communication_rules()` call in
  `textual_cli.py`.

## Architecture summary

```
┌──────────────┐    EventBus     ┌──────────────┐
│ Textual TUI  │◄───(events)────►│ RouterEngine  │
│  (adapter)   │    ──(calls)──► │ (3 705 LOC)   │
└──────┬───────┘                 └──────┬───────┘
       │                                │
       └──── shared_cli_helpers ────────┘
             (211 LOC)                  │
┌──────────────┐    EventBus            │
│  CLI (sync)  │◄───(LogEvent)──────────┘
│  (adapter)   │    ──(calls)──►
└──────────────┘
```

## Migration approach

Strangler Fig pattern — incremental extraction with thin delegation wrappers.
Each phase was verified by the full test suite before proceeding.

### Completed phases
| Phase | Scope |
|-------|-------|
| 0 | EventBus + RouterEngine skeleton |
| 1 | execute_tool_call + resolve_tool_calls extraction (Textual) |
| 2 | _process_user_turn + helpers + CycleFinishedEvent (Textual) |
| 3 | watchdog_tick + auto_resume_tick + poll_supervision_dialogue |
| 4 | CLI execute_tool_call delegation + camera/microphone porting + dead code removal |
| 5 | Shared helpers extraction + public property API + dead code / wrapper cleanup |

## File size comparison (before → after)

| File | v1.0.2 LOC | v1.0.3 LOC | Δ |
|------|-----------|-----------|---|
| `cli.py` | 3 067 | 284 | −2 783 |
| `textual_cli.py` | 5 312 | 1 176 | −4 136 |
| `router_engine.py` | (new) | 3 705 | +3 705 |
| `event_bus.py` | (new) | 137 | +137 |
| `shared_cli_helpers.py` | (new) | 211 | +211 |
| **Net** | **8 379** | **5 513** | **−2 866** |

Net reduction of ~2 866 LOC: the engine consolidates logic from two adapters.
Adapters are now thin UI shells (~284 and ~1 176 LOC).

## Compatibility

- Python: 3.10+
- OS: Linux

## Validation

- Full test suite: **1 177 passed** (up from 1 086 in v1.0.2).
- 89 engine-level tests, 15 EventBus tests.
- All 34 CLI runtime flow tests and 21 CLI corrective prompts tests pass.

## Safety

No permission policy expansion or shell allowlist relaxation.
Use only in isolated/sandboxed environments as described in `SECURITY.md`.
