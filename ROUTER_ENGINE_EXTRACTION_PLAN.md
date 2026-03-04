# RouterEngine Extraction Plan

> Structured report identifying every method and state variable in `textual_cli.py`
> and `cli.py` that performs **orchestration logic** (not presentation) and should
> move into a new `RouterEngine` class.

---

## A. State Variables Classification

### A1. PURE ORCHESTRATION — move to RouterEngine

| Variable | textual_cli.py init line | cli.py line | Type / Default | Purpose |
|---|---|---|---|---|
| `_passive_turns` | L690 | L876 (`passive_turns`) | `int / 0` | Counts turns without tool progress |
| `_last_user_message` | L691 | L877 (`last_user_message`) | `str / ""` | Last user input for continuation/watchdog |
| `_last_model_answer` | L692 | — (inline) | `str / ""` | Last model output for followup |
| `_last_progress_monotonic` | L693 | L874 (`last_tool_activity_monotonic`) | `float / time.monotonic()` | Timestamp of last operational progress |
| `_watchdog_attempts` | L694 | L879 (`idle_reactivation_attempts`) | `int / 0` | Watchdog/reactivation attempt counter |
| `_watchdog_capped_notified` | L695 | L880 (`idle_reactivation_capped_notified`) | `bool / False` | Whether cap message was shown |
| `_watchdog_suspended_until_user_input` | L698 | — (implicit via cap) | `bool / False` | Suspends auto-watchdog |
| `_watchdog_idle_threshold_seconds` | L700-703 | — (hardcoded constant) | `float / adaptive` | Idle seconds before watchdog fires |
| `_router_cycle_in_progress` | L704 | — (no threading in CLI) | `bool / False` | Mutex preventing concurrent router cycles |
| `_supervisor_outbox` | L706 | L884 (`supervisor_outbox`) | `list[dict] / []` | Kastor→Polluks message queue |
| `_actor_states` | L707-712 | — (no equivalent) | `dict[str,str]` | router/creator/supervisor/terminal state machine |
| `_idle_until_epoch` | L713 | — (no equivalent) | `float\|None / None` | Scheduled IDLE window end time |
| `_idle_until_source` | L714 | — | `str / ""` | Source of IDLE hint |
| `_plan_pause_active` | L716 | — (no equivalent) | `bool / False` | Whether plan is paused |
| `_plan_pause_started_monotonic` | L717 | — | `float / 0.0` | When pause started |
| `_plan_pause_reason` | L718 | — | `str / ""` | Reason for pause |
| `_pending_user_decision` | L719 | — | `bool / False` | Model asked user a question |
| `_pending_decision_identity_query` | L720 | — | `bool / False` | Pending decision is identity query |
| `_unaddressed_turns` | L721 | — | `int / 0` | Turns missing comm-protocol headers |
| `_reminder_count` | L722 | — | `int / 0` | Header-addressing reminders sent |
| `_consultation_rounds_this_cycle` | L723 | — | `int / 0` | Kastor consultation counter per cycle |
| `_user_message_queue` | L725 | — | `deque[str] / maxlen=20` | Queued messages when router busy |
| `_last_watchdog_cap_autonudge_monotonic` | L696 | — | `float / 0.0` | Timer for autonudge after cap |
| `code_path_failure_streak` | — | L881 | `int / 0` | Consecutive code-path failures |
| `user_turns_without_plan_update` | — | L882 | `int / 0` | Turns since plan was last updated |
| `pending_goal_candidate` | — | L883 | `str\|None / None` | Goal detected but not yet persisted |
| `last_work_state` | — | L878 | `str / "RUNNING"` | Last supervisor-reported work_state |

### A2. SHARED SERVICE REFS — injected into RouterEngine

| Variable | textual_cli.py init line | Purpose |
|---|---|---|
| `_chat_service` | L619 | Core model interaction service |
| `_permission_manager` | L621 | Runtime permission checks |
| `_activity_logger` | L626 | Structured activity logging |
| `_script_executor` | L687 | Python / shell execution |
| `_work_dir` | L688-689 | Working directory Path |
| `_shell_policy` | L727-729 | Loaded shell allowlist |
| `_comm_rules` | L724 | Communication protocol rules |
| `_supervisor_dialogue_log_path` | L620 | Path to supervision dialogue JSONL |
| `_dialogue_log_offset` | L623 | Read offset for dialogue polling |
| `_router_mailbox_log_path` | L624 | Path to router mailbox JSONL |
| `_permission_enforcer` | L649 (Phase 7) | Per-agent permission enforcement |
| `_audit_chain` | L652 (Phase 7) | Audit trail recording |

### A3. PRESENTATION ONLY — remain in TUI/CLI

| Variable | textual_cli.py init line | Purpose |
|---|---|---|
| `_input_history` | L679-681 | Terminal readline-style input history |
| `_wizard_phase` | L672 | Wizard UI state (0/1/2/3) |
| `_wizard_models` | L673 | Model list for wizard picker |
| `_wizard_kastor_models` | L674 | Kastor model list for wizard |
| `_wizard_polluks_choice` | L675 | Selected Polluks model |
| `_model_configured` | L671 | Whether model setup is done |
| `_usage_tracker` | L670 | Token usage UI tracker |
| `_wizard_service` | L669 | Wizard UI service |
| `_supervisor_notice_shown` | L689 | One-time Kastor-inactive notice |
| `_dashboard_server` | L640 | Dashboard web server (Phase 4) |
| `_settings` | L627 | UI settings |

---

## B. Methods Classification

### B1. PURE ORCHESTRATION — move directly to RouterEngine

| Method | textual_cli.py line | cli.py equivalent (line) | Signature | Purpose |
|---|---|---|---|---|
| `_is_conversational_interrupt` | L838 | — | `(self, text: str) -> bool` | Detects interrupt markers in user text |
| `_extract_pause_decision` | L846 | — | `(self, text: str) -> str\|None` | Parses "continue"/"stop" from user |
| `_is_identity_query` | L856 | — | `(self, text: str) -> bool` | Detects identity questions |
| `_model_response_awaits_user` | L862 | — (inline checks) | `(self, answer: str) -> bool` | Detects model questions to user |
| `_is_premature_plan_completion` | L910 | — (inline) | `(self, answer: str) -> bool` | Checks if plan wrongly marked done |
| `_runtime_supported_tool_names` | L1065 | L891 (`_runtime_supported_tools`) | `(self) -> set[str]` | Returns set of supported tool names |
| `_answer_has_supported_tool_call` | L1068 | L894 (`_has_supported_tool_call_runtime`) | `(self, answer: str) -> bool` | Checks answer for supported tool_calls |
| `_drain_supervisor_outbox_context` | L1304 | L1246 | `(self) -> str` | Builds text context from outbox, clears it |
| `_ask_executor_with_router_mailbox` | L1325 | L1267 (`ask_executor_with_router_mailbox`) | `(self, message: str) -> str` | Wraps `chat_service.ask()` with mailbox context |
| `_merge_supervisor_notes` | L1093 | — | `(self, base_note: str, supervisor_note: str) -> str` | Combines base note with supervisor output |
| `_apply_idle_hint_from_answer` | L1352 | — (inline) | `(self, answer: str, source: str) -> None` | Parses IDLE hints, sets `_idle_until_epoch` |
| `_plan_requires_update` | L4360 | — (inline checks) | `(self) -> bool` | Validates plan JSON structure completeness |
| `_has_actionable_plan` | L4397 | — (inline) | `(self) -> bool` | Checks for in-progress tasks in plan |
| `_resolve_model_path` | L4347 | — (inline path resolution) | `(self, raw_path: str) -> Path` | Resolves model-provided paths |
| — | — | L1054 (`_supervision_context`) | `(stage: str) -> dict` | Builds supervision context dict (cli-only) |
| — | — | L1071 (`ensure_plan_persisted`) | `(user_msg, answer) -> str` | Forces plan persistence if missing |
| — | — | L1114 (`ensure_plan_progress_updated`) | `(user_msg, answer) -> str` | Forces plan progress update |
| — | — | L2271 (`enforce_actionable_autonomy`) | `(user_msg, answer) -> str` | Forces tool_call on autonomy triggers |

### B2. MIXED ORCHESTRATION + UI — must be split

| Method | textual_cli.py line | cli.py equivalent (line) | What moves to RouterEngine | What stays in UI |
|---|---|---|---|---|
| `_redirect_premature_completion` | L948 | — | Supervisor refinement call, answer rewrite | `_append_log`, `_set_actor_state` calls |
| `_set_plan_paused` | L1075 | — | State mutation (`_plan_pause_*` flags) | `_append_plan_event`, `_append_log`, `_set_actor_state` |
| `_auto_resume_paused_plan_if_needed` | L1104 | — | Pause timer check, decision logic | Thread spawning, `_set_actor_state` |
| `_auto_resume_background` | L1145 | — | `supervisor.refine()`, answer construction | `_append_log`, `_resolve_tool_calls` → `_set_actor_state` |
| `_finalize_router_cycle` | L1193 | — | `_router_cycle_in_progress = False`, counter updates | `_set_actor_state`, `_consultation_rounds` reset |
| `_refresh_router_runtime_state` | L1204 | — | Idle-seconds check, state inference | `_set_actor_state` |
| `_enqueue_supervisor_message` | L1231 | L1218 | Outbox append + dedup, mailbox JSONL write | `_append_log` routing to panels |
| `_record_collaboration_signal` | L1056 | — | `_log_activity` call | — (mostly logging) |
| `_process_user_turn` | L3756 | L2499+ (main loop body) | **THE CORE**: supervisor refinement, interrupt detection, premature completion check, tool dispatch, communication routing, plan pause/resume | `_append_log`, `_set_actor_state`, panel routing |
| `_dispatch_user_turn` | L3731 | — (synchronous in CLI) | Queue handling, busy check | Thread spawning |
| `_drain_user_queue` | L3744 | — | Queue drain logic | Thread spawning |
| `_run_supervisor_idle_watchdog` | L4057 | L2175 (`run_idle_reactivation_cycle`) | Idle detection, threshold check, cap logic | `_append_log`, `_set_actor_state`, thread dispatch |
| `_watchdog_background_work` | L4136 | — (inline in `run_idle_reactivation_cycle`) | Supervisor prompt, `refine()`, progress enforcement | `_append_log`, panel updates |
| `_poll_supervision_dialogue` | L4230 | — (no equivalent) | JSONL parsing, payload extraction | Panel routing via `_append_log` |
| `_enforce_supervised_progress` | L4418 | — (partial: `ensure_plan_persisted` + `ensure_plan_progress_updated`) | Iterative supervisor refinement loop, fallback tool_call generation | `_set_actor_state`, `_enqueue_supervisor_message` (which itself is mixed) |
| `_execute_tool_call` | L4545 | L1349 (`execute_tool_call`) | ALL tool dispatch logic (read_file, write_file, run_shell, etc.) | `_ensure_resource` (permission UI) |
| `_resolve_tool_calls` | L4970 | L1892 (`resolve_tool_calls`) | Tool parse→execute→followup loop, unknown tool correction, loop detection | `_append_log`, `_set_actor_state` |
| — | — | L1283 (`apply_supervisor`) | `supervisor.refine()` wrapper | `console.print` |

### B3. PRESENTATION ONLY — remain in TUI/CLI

| Method (textual_cli.py) | Line | Purpose |
|---|---|---|
| `compose` | (CSS/widget layout) | Textual widget tree |
| `on_mount` | L3580 | CSS setup, watchdog timer, wizard launch |
| `on_input_submitted` | L3616 | Input widget handler, wizard routing |
| `_append_log` | L1362 | Routes text to UI log panels |
| `_set_actor_state` | L815 | Updates `_actor_states` dict + logs event |
| `_log_activity` | L731 | Writes to activity JSONL |
| `_ensure_resource` | L1452 | Permission prompt UI |
| `_sanitize_block_for_sponsor` | L1376 | Strips tool_call blocks before showing to sponsor panel |
| `_format_supervision_lane_label` | L4052 | Format label string for panel routing |
| `_append_plan_event` | L1022 | Writes plan event to JSONL |
| All `/cmd` handlers | L1460-3600 | Command dispatch (status, help, team, eval, etc.) |

---

## C. Duplicated Orchestration in cli.py

The following orchestration functions are defined as **closures inside `run_cli()`** (starting L862) and
duplicate logic that exists as methods in `_AmiagiTextualApp`:

| cli.py closure | Line | Textual equivalent | Line | Notes |
|---|---|---|---|---|
| `_runtime_supported_tools()` | L891 | `_runtime_supported_tool_names()` | L1065 | Identical logic: base set + registered tools |
| `_has_supported_tool_call_runtime()` | L894 | `_answer_has_supported_tool_call()` | L1068 | Identical: parse + check against runtime set |
| `_has_unknown_tool_calls_runtime()` | L901 | — | — | CLI-only: returns True if any call not in runtime set |
| `_supervision_context()` | L1054 | inline in `_process_user_turn` | L3756+ | Builds dict with passive_turns, should_remind, etc. |
| `_enqueue_supervisor_message()` | L1218 | `_enqueue_supervisor_message()` | L1231 | Near-identical: append + dedup + cap at 10 |
| `_drain_supervisor_outbox_context()` | L1246 | `_drain_supervisor_outbox_context()` | L1304 | Near-identical: build text + clear list |
| `ask_executor_with_router_mailbox()` | L1267 | `_ask_executor_with_router_mailbox()` | L1325 | Identical: drain → prepend → chat_service.ask() |
| `apply_supervisor()` | L1283 | inline in `_process_user_turn` | L3756+ | Wraps `supervisor.refine()` + enqueue |
| `ensure_plan_persisted()` | L1071 | `_enforce_supervised_progress()` (partial) | L4418 | Forces plan init if missing — no textual equivalent as standalone |
| `ensure_plan_progress_updated()` | L1114 | `_enforce_supervised_progress()` (partial) | L4418 | Forces plan progress — merged in textual |
| `execute_tool_call()` | L1349 | `_execute_tool_call()` | L4545 | **~540 lines each**, nearly identical tool dispatch |
| `resolve_tool_calls()` | L1892 | `_resolve_tool_calls()` | L4970 | **~250 lines each**, same loop: parse→execute→followup→supervisor |
| `run_idle_reactivation_cycle()` | L2175 | `_run_supervisor_idle_watchdog()` + `_watchdog_background_work()` | L4057 + L4136 | Same concept, textual splits into dispatch+background |
| `enforce_actionable_autonomy()` | L2271 | `_enforce_supervised_progress()` | L4418 | CLI version is more targeted (forces tool_call on autonomy) |

**Total duplicated orchestration: ~14 functions, ~2000+ lines across both files.**

---

## D. Tool Resolution Logic Comparison

### D1. textual_cli.py `_resolve_tool_calls` (L4970, ~250 lines)

```
while iteration < max_steps (15):
  1. parse_tool_calls(current)
  2. For each call: _execute_tool_call(call)
  3. Track unknown tools
  4. Loop detection: 3 consecutive identical signatures → break
  5. Unknown tool handling:
     a. Per-tool correction counter (max 2)
     b. If exhausted → force tool-creation workflow (write plan)
     c. Else → supervisor corrective prompt with available tools list
  6. Known tools: build [TOOL_RESULT] → _ask_executor_with_router_mailbox()
  7. Supervisor review of follow-up answer (reject if unsupported tools)
  8. Continue loop if new tool_calls in answer
```

### D2. cli.py `resolve_tool_calls` (L1892, ~280 lines)

```
while iteration < max_steps (15):
  1. parse_tool_calls(current)
  2. For each call: execute_tool_call(call)
  3. Track unknown tools
  4. Loop detection: 3 consecutive identical signatures → break
  5. Unknown tool handling:
     a. Per-tool correction counter (max 2)
     b. If exhausted → force tool-creation workflow (write plan)
     c. Else → supervisor corrective prompt with available tools list
  6. Known tools: build [TOOL_RESULT] → ask_executor_with_router_mailbox()
  7. Supervisor review (with unsupported-tool rejection filter)
  8. Continue loop if new tool_calls
```

**Key differences:**
- Textual version uses `_set_actor_state()` calls throughout (UI coupling)
- Textual version uses `_append_log()` for warnings (UI coupling)
- Textual version uses `_log_activity()` for structured logging
- CLI version uses `console.print()` for all output
- **Core algorithm is identical** — both have the same loop structure, same max_steps (15), same unknown-tool max corrections (2), same loop detection (3 consecutive identical)

### D3. `_execute_tool_call` / `execute_tool_call` comparison

Both dispatch the same set of tools:
`read_file`, `list_dir`, `write_file`, `append_file`, `check_python_syntax`, `run_python`, `run_shell`, `fetch_web`, `download_file`, `convert_pdf_to_markdown`, `search_web`, `check_capabilities`, plus custom tools via `tool_registry.json`.

**Key differences:**
- Textual adds Phase 7 `_permission_enforcer` checks (agent_id-based) — L4547-4575
- Textual uses `_ensure_resource()` for permission prompts
- CLI uses `permission_manager.ensure()` calls inline
- Textual uses `_resolve_model_path()` which handles `amiagi-main/` prefix
- CLI uses `_resolve_tool_path()` helper function (module-level)
- **Tool dispatch logic is 95% identical** — ideal for extraction

---

## E. Supervisor Integration

### E1. Supervisor calls in textual_cli.py

| Call site | Method | Line | Stage parameter | Purpose |
|---|---|---|---|---|
| Initial review | `_process_user_turn` | L3756+ | `"textual_review"` | First supervisor review of executor answer |
| Progress guard | `_enforce_supervised_progress` | L4418 | `"textual_progress_guard"` | Forces tool_call when passive |
| Premature redirect | `_redirect_premature_completion` | L948 | `"redirect_premature_completion"` | Redirects false plan-done claims |
| Tool flow review | `_resolve_tool_calls` | L4970 | `"tool_flow"` | Reviews answer after TOOL_RESULT |
| Unknown tool fix | `_resolve_tool_calls` | L4970 | `"textual_unknown_tool_corrective"` | Corrects unsupported tool names |
| Watchdog nudge | `_watchdog_background_work` | L4136 | `"textual_watchdog_nudge"` | Reactive supervisor after idle |
| Consultation | `_process_user_turn` | L3756+ | `"consultation"` | [Polluks → Kastor] addressed blocks |
| Auto-resume | `_auto_resume_background` | L1145 | `"textual_auto_resume"` | Resume after plan pause timeout |

### E2. Supervisor calls in cli.py

| Call site | Function | Line | Stage parameter | Purpose |
|---|---|---|---|---|
| Main review | `apply_supervisor` | L1283 | `"cli_review"` | First supervisor review |
| Plan persist | `ensure_plan_persisted` | L1071 | `"cli_plan_persistence_guard"` | Forces plan file init |
| Plan progress | `ensure_plan_progress_updated` | L1114 | `"cli_plan_progress_guard"` | Forces plan progress update |
| Tool flow | `resolve_tool_calls` | L1892 | `"tool_flow"` | Reviews answer after TOOL_RESULT |
| Unknown tool fix | `resolve_tool_calls` | L1892 | `"cli_unknown_tool_corrective"` | Corrects unsupported tool |
| Idle reactivation | `run_idle_reactivation_cycle` | L2175 | `"idle_reactivation"` | Watchdog nudge |
| Actionable autonomy | `enforce_actionable_autonomy` | L2271 | `"cli_autonomy_guard"` | Force tool_call on autonomy |

### E3. Supervisor message flow (shared)

Both interfaces follow the same pattern:
1. Call `supervisor.refine(user_message, model_answer, stage, conversation_excerpt?)`
2. Get `SupervisionResult(answer, repairs_applied, status, reason_code, work_state, notes)`
3. Call `_enqueue_supervisor_message(stage, reason_code, notes, answer)` to add to outbox
4. The outbox is drained via `_drain_supervisor_outbox_context()` into the next `chat_service.ask()` call

**This entire flow can be encapsulated in RouterEngine.**

---

## F. Plan Tracking

### F1. textual_cli.py plan methods

| Method | Line | Purpose |
|---|---|---|
| `_plan_requires_update()` | L4360 | Validates plan JSON schema (goal, current_stage, tasks with statuses) |
| `_has_actionable_plan()` | L4397 | Returns True if any task has status "rozpoczęta" or "w trakcie realizacji" |
| `_is_premature_plan_completion()` | L910 | Detects false "done" claims while tasks remain open |
| `_redirect_premature_completion()` | L948 | Uses supervisor to redirect model back to work |
| `_set_plan_paused()` | L1075 | Pause/resume state machine with reason tracking |
| `_auto_resume_paused_plan_if_needed()` | L1104 | Auto-resume after 90s pause |
| `_auto_resume_background()` | L1145 | Background thread for auto-resume |
| `_append_plan_event()` | L1022 | Writes plan events to JSONL (used by pause/resume) |
| `_enforce_supervised_progress()` | L4418 | Iterative supervisor loop forcing plan init or tool_call |

### F2. cli.py plan functions

| Function | Line | Purpose |
|---|---|---|
| `ensure_plan_persisted()` | L1071 | Forces plan init via supervisor if file missing/invalid |
| `ensure_plan_progress_updated()` | L1114 | Forces plan progress update via supervisor |
| `_has_actionable_plan()` (inline) | ~L2200 | Same plan file checks (inline, not extracted) |
| `_plan_requires_update()` (inline) | ~L2190 | Same validation (inline) |

### F3. Extraction recommendation

All plan validation and enforcement should move to RouterEngine:
- `plan_requires_update() -> bool`
- `has_actionable_plan() -> bool`
- `is_premature_plan_completion(answer) -> bool`
- `redirect_premature_completion(user_msg, answer) -> str | None`
- `enforce_supervised_progress(user_msg, answer, max_attempts) -> str`
- `set_plan_paused(paused, reason, source) -> PlanPauseEvent`
- `auto_resume_if_needed(now) -> bool`

The UI layer receives `PlanPauseEvent` / `PlanResumeEvent` callbacks for display.

---

## G. Permission Flow

### G1. textual_cli.py permission layers

1. **`_ensure_resource(resource, reason) -> bool`** (L1452):
   Runtime permission gate — calls `_permission_manager.ensure(resource, reason)`.
   Used by every tool in `_execute_tool_call` before I/O operations.
   Resources: `disk.read`, `disk.write`, `process.exec`, `network.internet`.

2. **Phase 7 `_permission_enforcer` checks** (L4547-4575 in `_execute_tool_call`):
   Per-agent permission enforcement via `PermissionEnforcer.check_tool(agent_id, tool)`.
   Path-level checks via `check_path(agent_id, path, write=bool)`.
   Denial logged to `AuditChain.record_action()`.

3. **Shell policy validation** (inside `run_shell` tool handler):
   `parse_and_validate_shell_command(command, self._shell_policy)` — allowlist enforcement.

### G2. cli.py permission layers

1. **`permission_manager.ensure(resource, reason)`** — same as textual, called inline in each tool handler.
2. **No Phase 7 enforcer** — CLI lacks per-agent permission checks.
3. **Shell policy** — same `parse_and_validate_shell_command()` call.

### G3. Extraction recommendation

RouterEngine should expose:
```python
class RouterEngine:
    def check_permission(self, resource: str, reason: str) -> bool:
        """Delegates to permission_manager — UI can override for interactive prompts."""
        ...

    def check_agent_permission(self, agent_id: str, tool: str, path: str | None, write: bool) -> PermissionResult:
        """Phase 7 per-agent enforcement."""
        ...
```

The UI layer provides a `PermissionCallback` protocol that RouterEngine calls when interactive
approval is needed. This cleanly separates the permission *policy* (RouterEngine) from the
permission *prompt* (UI).

---

## H. Recommended RouterEngine Interface

Based on the analysis above, here is the recommended public interface:

```python
class RouterEngine:
    """Orchestration engine extracted from textual_cli.py and cli.py."""

    # --- Constructor (all former __init__ state from Section A1 + A2) ---
    def __init__(
        self,
        chat_service: ChatService,
        work_dir: Path,
        shell_policy: ShellPolicy,
        comm_rules: CommunicationRules,
        permission_manager: PermissionManager,
        activity_logger: ActivityLogger | None = None,
        permission_enforcer: PermissionEnforcer | None = None,
        audit_chain: AuditChain | None = None,
        supervisor_dialogue_log_path: Path | None = None,
        router_mailbox_log_path: Path | None = None,
    ): ...

    # --- Core orchestration (Section B1) ---
    def process_user_turn(self, text: str) -> RouterResult: ...
    def resolve_tool_calls(self, answer: str, max_steps: int = 15) -> str: ...
    def execute_tool_call(self, tool_call: ToolCall, agent_id: str = "") -> dict: ...
    def ask_executor_with_mailbox(self, message: str) -> str: ...

    # --- Supervisor integration (Section E) ---
    def enqueue_supervisor_message(self, stage: str, reason_code: str, notes: str, answer: str) -> None: ...
    def drain_supervisor_outbox(self) -> str: ...
    def enforce_supervised_progress(self, user_msg: str, answer: str, max_attempts: int = 3) -> str: ...

    # --- Plan tracking (Section F) ---
    def plan_requires_update(self) -> bool: ...
    def has_actionable_plan(self) -> bool: ...
    def is_premature_plan_completion(self, answer: str) -> bool: ...
    def redirect_premature_completion(self, user_msg: str, answer: str) -> str | None: ...
    def set_plan_paused(self, paused: bool, reason: str, source: str) -> None: ...
    def auto_resume_if_needed(self) -> bool: ...

    # --- Watchdog / idle (Section B2) ---
    def run_idle_watchdog(self) -> WatchdogResult | None: ...
    def poll_supervision_dialogue(self) -> list[SupervisionEvent]: ...

    # --- Detection helpers (Section B1) ---
    def is_conversational_interrupt(self, text: str) -> bool: ...
    def extract_pause_decision(self, text: str) -> str | None: ...
    def is_identity_query(self, text: str) -> bool: ...
    def model_response_awaits_user(self, answer: str) -> bool: ...
    def runtime_supported_tool_names(self) -> set[str]: ...
    def answer_has_supported_tool_call(self, answer: str) -> bool: ...

    # --- Permission (Section G) ---
    def check_permission(self, resource: str, reason: str) -> bool: ...
    def check_agent_permission(self, agent_id: str, tool: str, path: str | None = None, write: bool = False) -> PermissionResult: ...

    # --- Callbacks for UI layer ---
    on_log: Callable[[str, str], None]           # (panel_id, message)
    on_actor_state: Callable[[str, str, str], None]  # (actor, state, event)
    on_plan_event: Callable[[str, dict], None]    # (event_type, payload)
```

### RouterResult dataclass
```python
@dataclasses.dataclass
class RouterResult:
    answer: str                    # Final answer text
    display_answer: str            # User-facing formatted answer
    routed_to_user_panel: bool     # Whether comm-protocol routed to user
    tool_calls_resolved: bool      # Whether tool_calls were executed
    plan_paused: bool              # Whether plan was paused during this turn
    pending_user_decision: bool    # Whether model is asking user a question
```

---

## I. Migration Priority

| Priority | What | Lines saved | Risk |
|---|---|---|---|
| **P0** | `_execute_tool_call` / `execute_tool_call` | ~1100 (540×2) | Low — pure logic |
| **P0** | `_resolve_tool_calls` / `resolve_tool_calls` | ~530 (250+280) | Low — same algorithm |
| **P1** | Supervisor outbox (`enqueue`, `drain`, `ask_with_mailbox`) | ~200 | Low — pure data flow |
| **P1** | Plan tracking (`plan_requires_update`, `has_actionable_plan`, etc.) | ~300 | Low — pure validation |
| **P2** | `_process_user_turn` / main-loop body | ~400 | **High** — deeply coupled to UI |
| **P2** | Watchdog (`_run_supervisor_idle_watchdog` + background work) | ~250 | Medium — threading differences |
| **P3** | `_poll_supervision_dialogue` | ~120 | Medium — panel routing is UI |
| **P3** | Detection helpers (interrupt, pause, identity, etc.) | ~80 | Low — pure functions |

**Estimated total deduplication: ~2,500–3,000 lines** across both interfaces.
