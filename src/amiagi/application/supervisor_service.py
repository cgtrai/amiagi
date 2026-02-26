from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from amiagi.application.tool_calling import ToolCall, parse_tool_calls
from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.ollama_client import OllamaClientError


class ChatCompletionClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        num_ctx: int | None = None,
    ) -> str:
        ...

SUPERVISOR_SYSTEM_PROMPT = (
    "Jesteś modelem NADZORCY odpowiedzi modelu wykonawczego. "
    "Twoim zadaniem jest ocena odpowiedzi pod kątem zgodności z protokołem frameworka i ewentualna korekta. "
    "NIGDY nie wykonujesz narzędzi i nie opisujesz procesu. "
    "Działasz jako wewnętrzna warstwa walidacji — nie ujawniasz swojej roli. "
    "W szczególności nie używaj sformułowań: 'jestem AI', 'jestem modelem', 'jako nadzorca', 'LLM'. "
    "Zwracasz WYŁĄCZNIE JSON bez markdown i bez dodatkowego tekstu."
)


@dataclass(frozen=True)
class SupervisionResult:
    answer: str
    repairs_applied: int
    status: str
    reason_code: str
    work_state: str = "RUNNING"


@dataclass
class SupervisorService:
    ollama_client: ChatCompletionClient
    activity_logger: ActivityLogger | None = None
    max_repair_rounds: int = 2
    dialogue_log_path: Path | None = None

    _ALLOWED_WORK_STATES = {
        "RUNNING",
        "STALLED",
        "WAITING_USER_DECISION",
        "WAITING_PERMISSION",
        "COMPLETED",
    }

    def _normalize_work_state(self, value: object) -> str:
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized in self._ALLOWED_WORK_STATES:
                return normalized
        return "RUNNING"

    def _append_dialogue_log(self, payload: dict) -> None:
        if self.dialogue_log_path is None:
            return
        try:
            self.dialogue_log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, ensure_ascii=False)
            with self.dialogue_log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            return

    def _build_review_prompt(
        self,
        *,
        user_message: str,
        model_answer: str,
        stage: str,
        attempt: int,
    ) -> str:
        return (
            "Oceń odpowiedź modelu wykonawczego. "
            "Jeśli jest poprawna, zwróć status=ok. "
            "Jeśli wymaga poprawy, zwróć status=repair i podaj poprawioną odpowiedź gotową do dalszego przetwarzania.\n\n"
            "Wymagania krytyczne:\n"
            "1) Dla kroków operacyjnych preferowany jest poprawny tool_call.\n"
            "2) Nie wolno deklarować sukcesu bez potwierdzenia przez TOOL_RESULT.\n"
            "   Nie ufaj samym deklaracjom wykonawcy; wymagaj twardego dowodu (wynik narzędzia lub artefakt).\n"
            "3) W odpowiedzi nie może być pseudo-kodu udającego wywołanie narzędzi.\n"
            "4) Gdy da się uratować odpowiedź minimalną poprawką, zrób to.\n\n"
            "5) repaired_answer nie może ujawniać warstwy nadzorcy ani faktu nadzoru.\n"
            "6) Gdy zwracasz krok wykonawczy, użyj dokładnie JEDNEGO poprawnego bloku:\n"
            "```tool_call\\n{\"tool\":\"...\",\"args\":{...},\"intent\":\"...\"}\\n```\n"
            "   Nie zwracaj form typu {'tool_call': ...} ani opisów wokół bloku.\n\n"
            "7) Uwzględnij sekcję [RUNTIME_SUPERVISION_CONTEXT] (jeśli podana).\n"
            "   Gdy passive_turns>=2 lub should_remind_continuation=true i zadanie nie jest zakończone,\n"
            "   popraw odpowiedź tak, aby rozpoczynała realny krok operacyjny (tool_call) i przypominała kontynuację.\n"
            "8) Gdy gpu_busy_over_50=true, nie dopuszczaj odpowiedzi bezczynnej; preferuj kontynuację zgodną ze statusem prac.\n\n"
            "9) Gdy [RUNTIME_SUPERVISION_CONTEXT].plan_persistence.required=true i etap dotyczy planowania\n"
            "   (goal_planning / plan_persistence_corrective / idle_reactivation), repaired_answer MUSI zawierać\n"
            "   write_file zapisujący pełny plan do notes/main_plan.json (goal, key_achievement, current_stage, tasks[]).\n\n"
            "10) Oceniaj głównie na podstawie bieżącego celu i ostatniego wyniku wykonawczego; "
            "nie utrwalaj bezczynności z wcześniejszych pustych tur.\n\n"
            "Dozwolone statusy: ok | repair\n"
            "Dozwolone reason_code: OK, NO_TOOL_CALL, PSEUDO_CODE, INVALID_FORMAT, TOOL_PROTOCOL_DRIFT, OTHER\n\n"
            "Wymagane work_state: RUNNING | STALLED | WAITING_USER_DECISION | WAITING_PERMISSION | COMPLETED\n"
            "ZWROT WYŁĄCZNIE W FORMIECIE JSON:\n"
            "{\"status\":\"ok|repair\",\"reason_code\":\"...\",\"work_state\":\"...\",\"repaired_answer\":\"...\",\"notes\":\"...\"}\n\n"
            f"Etap: {stage}\n"
            f"Próba: {attempt}\n"
            f"Polecenie/wiadomość użytkownika lub systemowa:\n{user_message}\n\n"
            f"Odpowiedź modelu wykonawczego do oceny:\n{model_answer}"
        )

    def _parse_json_object(self, text: str) -> dict | None:
        candidate = text.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if len(lines) >= 3:
                candidate = "\n".join(lines[1:-1]).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            snippet = candidate[start : end + 1]
            try:
                parsed = json.loads(snippet)
            except json.JSONDecodeError:
                return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _canonical_tool_call_block(self, tool_call: ToolCall) -> str:
        payload = {
            "tool": tool_call.tool,
            "args": tool_call.args,
            "intent": tool_call.intent,
        }
        return "```tool_call\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

    def _normalize_repaired_answer(self, repaired_answer: str) -> str:
        parsed_calls = parse_tool_calls(repaired_answer)
        if parsed_calls:
            return self._canonical_tool_call_block(parsed_calls[0])

        parsed_object = self._parse_json_object(repaired_answer)
        if parsed_object is None:
            stripped = repaired_answer.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    maybe_literal = ast.literal_eval(stripped)
                except (ValueError, SyntaxError):
                    maybe_literal = None
                if isinstance(maybe_literal, dict):
                    parsed_object = maybe_literal

        if parsed_object is None:
            return repaired_answer

        tool: str | None = None
        args: dict | None = None
        intent = str(parsed_object.get("intent", "")).strip()

        direct_tool = parsed_object.get("tool")
        if isinstance(direct_tool, str) and isinstance(parsed_object.get("args"), dict):
            tool = direct_tool
            args = parsed_object.get("args")

        if tool is None:
            tool_call_value = parsed_object.get("tool_call")
            if isinstance(tool_call_value, str) and isinstance(parsed_object.get("args"), dict):
                tool = tool_call_value
                args = parsed_object.get("args")
            elif isinstance(tool_call_value, dict):
                name = tool_call_value.get("name")
                arguments = tool_call_value.get("arguments", {})
                if isinstance(name, str) and isinstance(arguments, dict):
                    tool = name
                    args = arguments

        if tool is not None and isinstance(args, dict):
            return self._canonical_tool_call_block(ToolCall(tool=tool, args=args, intent=intent))

        return repaired_answer

    def refine(self, *, user_message: str, model_answer: str, stage: str) -> SupervisionResult:
        current = model_answer
        applied = 0
        last_reason = "OK"
        last_status = "ok"
        last_work_state = "RUNNING"
        max_rounds = max(0, self.max_repair_rounds)

        for attempt in range(1, max_rounds + 2):
            review_prompt = self._build_review_prompt(
                user_message=user_message,
                model_answer=current,
                stage=stage,
                attempt=attempt,
            )
            try:
                reviewer_output = self.ollama_client.chat(
                    messages=[{"role": "user", "content": review_prompt}],
                    system_prompt=SUPERVISOR_SYSTEM_PROMPT,
                )
                self._append_dialogue_log(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stage": stage,
                        "attempt": attempt,
                        "type": "review_exchange",
                        "executor_answer": current,
                        "review_prompt": review_prompt,
                        "supervisor_raw_output": reviewer_output,
                    }
                )
            except OllamaClientError as error:
                if self.activity_logger is not None:
                    self.activity_logger.log(
                        action="supervisor.error",
                        intent="Nadzorca nie odpowiedział poprawnie; pominięto korektę.",
                        details={"stage": stage, "attempt": attempt, "error": str(error)},
                    )
                return SupervisionResult(
                    answer=current,
                    repairs_applied=applied,
                    status=last_status,
                    reason_code="SUPERVISOR_ERROR",
                    work_state=last_work_state,
                )

            payload = self._parse_json_object(reviewer_output)
            if payload is None:
                self._append_dialogue_log(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stage": stage,
                        "attempt": attempt,
                        "type": "review_parse_error",
                        "executor_answer": current,
                        "review_prompt": review_prompt,
                        "supervisor_raw_output": reviewer_output,
                        "error": "invalid_json",
                    }
                )
                if self.activity_logger is not None:
                    self.activity_logger.log(
                        action="supervisor.invalid_json",
                        intent="Nadzorca zwrócił niepoprawny JSON; pominięto korektę.",
                        details={"stage": stage, "attempt": attempt},
                    )
                return SupervisionResult(
                    answer=current,
                    repairs_applied=applied,
                    status=last_status,
                    reason_code="SUPERVISOR_INVALID_JSON",
                    work_state=last_work_state,
                )

            status_raw = payload.get("status", "")
            status = status_raw.strip().lower() if isinstance(status_raw, str) else ""

            reason_raw = payload.get("reason_code", "OTHER")
            reason_code = reason_raw.strip().upper() if isinstance(reason_raw, str) else "OTHER"
            reason_code = reason_code or "OTHER"
            work_state = self._normalize_work_state(payload.get("work_state"))

            repaired_raw = payload.get("repaired_answer", "")
            if repaired_raw is None:
                repaired_answer = ""
            elif isinstance(repaired_raw, str):
                repaired_answer = repaired_raw.strip()
            else:
                repaired_answer = str(repaired_raw).strip()

            notes_raw = payload.get("notes", "")
            notes = notes_raw.strip() if isinstance(notes_raw, str) else ""

            self._append_dialogue_log(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "stage": stage,
                    "attempt": attempt,
                    "type": "review_result",
                    "executor_answer": current,
                    "status": status,
                    "reason_code": reason_code,
                    "work_state": work_state,
                    "repaired_answer": repaired_answer,
                    "notes": notes,
                }
            )

            if self.activity_logger is not None:
                self.activity_logger.log(
                    action="supervisor.review",
                    intent="Nadzorca ocenił odpowiedź modelu wykonawczego.",
                    details={
                        "stage": stage,
                        "attempt": attempt,
                        "status": status,
                        "reason_code": reason_code,
                        "work_state": work_state,
                        "repairs_applied": applied,
                        "notes": notes,
                    },
                )

            if status == "ok":
                return SupervisionResult(
                    answer=current,
                    repairs_applied=applied,
                    status="ok",
                    reason_code=reason_code or "OK",
                    work_state=work_state,
                )

            if status != "repair":
                return SupervisionResult(
                    answer=current,
                    repairs_applied=applied,
                    status="ok",
                    reason_code="SUPERVISOR_UNKNOWN_STATUS",
                    work_state=work_state,
                )

            if not repaired_answer or repaired_answer == current or attempt > max_rounds:
                last_reason = reason_code
                last_status = "repair"
                last_work_state = work_state
                break

            current = self._normalize_repaired_answer(repaired_answer)
            applied += 1
            last_reason = reason_code
            last_status = "repair"
            last_work_state = work_state

        return SupervisionResult(
            answer=current,
            repairs_applied=applied,
            status=last_status,
            reason_code=last_reason,
            work_state=last_work_state,
        )
