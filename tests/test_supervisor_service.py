from __future__ import annotations

import json
from dataclasses import dataclass

from amiagi.application.supervisor_service import SupervisorService


@dataclass
class FakeSupervisorClient:
    responses: list[str]

    def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        num_ctx: int | None = None,
    ) -> str:
        _ = messages
        _ = system_prompt
        _ = num_ctx
        if not self.responses:
            return '{"status":"ok","reason_code":"OK","repaired_answer":"","notes":""}'
        return self.responses.pop(0)


def test_supervisor_service_applies_repair_and_then_accepts() -> None:
    client = FakeSupervisorClient(
        responses=[
            '{"status":"repair","reason_code":"NO_TOOL_CALL","repaired_answer":"```tool_call\\n{\\"tool\\":\\"list_dir\\",\\"args\\":{\\"path\\":\\".\\"},\\"intent\\":\\"start\\"}\\n```","notes":""}',
            '{"status":"ok","reason_code":"OK","repaired_answer":"","notes":""}',
        ]
    )
    service = SupervisorService(ollama_client=client, max_repair_rounds=2)

    result = service.refine(
        user_message="działaj",
        model_answer="Zaraz zacznę pracę.",
        stage="user_turn",
    )

    assert result.repairs_applied == 1
    assert result.status == "ok"
    assert "tool_call" in result.answer


def test_supervisor_service_ignores_invalid_json() -> None:
    client = FakeSupervisorClient(responses=["to nie jest json"])
    service = SupervisorService(ollama_client=client, max_repair_rounds=2)

    original = "Niepoprawna odpowiedź wykonawcy"
    result = service.refine(
        user_message="kontynuuj",
        model_answer=original,
        stage="tool_flow",
    )

    assert result.answer == original
    assert result.reason_code == "SUPERVISOR_INVALID_JSON"


def test_supervisor_service_accepts_protocol_client() -> None:
    client = FakeSupervisorClient(
        responses=['{"status":"ok","reason_code":"OK","repaired_answer":"","notes":""}']
    )
    service = SupervisorService(ollama_client=client, max_repair_rounds=1)

    result = service.refine(
        user_message="test",
        model_answer="odpowiedź",
        stage="user_turn",
    )

    assert result.status == "ok"


def test_supervisor_service_normalizes_dict_style_repair() -> None:
    client = FakeSupervisorClient(
        responses=[
            '{"status":"repair","reason_code":"INVALID_FORMAT","repaired_answer":"{\'tool_call\': \'write_file\', \'args\': {\'path\': \'main.py\', \'content\': \'print(123)\'}, \'intent\': \'utwórz plik\'}","notes":""}',
            '{"status":"ok","reason_code":"OK","repaired_answer":"","notes":""}',
        ]
    )
    service = SupervisorService(ollama_client=client, max_repair_rounds=2)

    result = service.refine(
        user_message="kontynuuj",
        model_answer="{'tool_call': 'write_file'}",
        stage="tool_flow",
    )

    assert result.status == "ok"
    assert result.repairs_applied == 1
    assert result.answer.startswith("```tool_call")
    assert '"tool": "write_file"' in result.answer


def test_supervisor_service_handles_null_repaired_answer_without_none_text() -> None:
    client = FakeSupervisorClient(
        responses=['{"status":"repair","reason_code":"NO_TOOL_CALL","repaired_answer":null,"notes":""}']
    )
    service = SupervisorService(ollama_client=client, max_repair_rounds=1)

    original = "Brak poprawnego kroku wykonawczego"
    result = service.refine(
        user_message="kontynuuj",
        model_answer=original,
        stage="tool_flow",
    )

    assert result.answer == original
    assert result.answer != "None"


def test_supervisor_service_writes_dialogue_log(tmp_path) -> None:
    client = FakeSupervisorClient(
        responses=['{"status":"ok","reason_code":"OK","repaired_answer":"","notes":""}']
    )
    dialogue_log = tmp_path / "supervision_dialogue.jsonl"
    service = SupervisorService(
        ollama_client=client,
        max_repair_rounds=1,
        dialogue_log_path=dialogue_log,
    )

    _ = service.refine(
        user_message="test",
        model_answer="odpowiedź",
        stage="user_turn",
    )

    assert dialogue_log.exists()
    lines = [line for line in dialogue_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 2
    payloads = [json.loads(line) for line in lines]
    types = {item.get("type") for item in payloads}
    assert "review_exchange" in types
    assert "review_result" in types


def test_supervisor_review_prompt_demands_hard_evidence_not_declarations() -> None:
    client = FakeSupervisorClient(
        responses=['{"status":"ok","reason_code":"OK","repaired_answer":"","notes":""}']
    )
    service = SupervisorService(ollama_client=client, max_repair_rounds=1)

    prompt = service._build_review_prompt(
        user_message="kontynuuj",
        model_answer="Zrobione, wszystko działa.",
        stage="user_turn",
        attempt=1,
    )

    assert "Nie ufaj samym deklaracjom wykonawcy" in prompt
    assert "wymagaj twardego dowodu" in prompt
    assert "wsparcie decyzyjne" in prompt


def test_supervisor_service_generates_fallback_coaching_notes_when_missing() -> None:
    client = FakeSupervisorClient(
        responses=[
            '{"status":"ok","reason_code":"NO_TOOL_CALL","work_state":"RUNNING","repaired_answer":"","notes":""}'
        ]
    )
    service = SupervisorService(ollama_client=client, max_repair_rounds=1)

    result = service.refine(
        user_message="kontynuuj",
        model_answer="Zaraz coś zrobię.",
        stage="user_turn",
    )

    assert result.status == "ok"
    assert result.notes
    assert "krok" in result.notes.lower() or "narzęd" in result.notes.lower()
