from __future__ import annotations

from pathlib import Path

from amiagi.interfaces.cli import (
    _build_plan_tracking_corrective_prompt,
    _build_no_action_corrective_prompt,
    _build_pseudo_tool_corrective_prompt,
    _build_python_code_corrective_prompt,
    _build_unparsed_tool_call_corrective_prompt,
    _has_supported_tool_call,
    _has_unknown_tool_calls,
    _canonicalize_tool_calls,
    _is_non_action_placeholder,
    _looks_like_unparsed_tool_call,
    _resolve_tool_path,
    _build_unknown_tools_corrective_prompt,
    _is_path_within_work_dir,
    _detect_preferred_microphone_device,
    _build_microphone_profiles,
)
from amiagi.application.tool_calling import ToolCall


def test_pseudo_tool_corrective_prompt_contains_checklist_and_allowed_tools() -> None:
    prompt = _build_pseudo_tool_corrective_prompt()

    assert "pseudo-kod" in prompt
    assert "1) Zapisz plik: write_file" in prompt
    assert "3) Sprawdź składnię: check_python_syntax" in prompt
    assert "4) Dopiero po poprawnej składni uruchom: run_python" in prompt
    assert "Dozwolone narzędzia:" in prompt


def test_unknown_tools_corrective_prompt_contains_same_python_checklist() -> None:
    prompt = _build_unknown_tools_corrective_prompt(["foo", "bar", "foo"])

    assert "nieobsługiwanych narzędzi: bar, foo" in prompt
    assert "Działaj proaktywnie" in prompt
    assert "notes/tool_design_plan.json" in prompt
    assert "state/tool_registry.json" in prompt
    assert "Dostępne narzędzia bazowe:" in prompt
    assert "1) Zapisz plik: write_file" in prompt
    assert "3) Sprawdź składnię: check_python_syntax" in prompt
    assert "Teraz zwróć WYŁĄCZNIE jeden poprawny blok tool_call jako pierwszy krok" in prompt


def test_no_action_corrective_prompt_contains_context_and_checklist() -> None:
    prompt = _build_no_action_corrective_prompt(
        "uruchom eksperyment", "/tmp/wprowadzenie.md"
    )

    assert "Polecenie użytkownika: uruchom eksperyment" in prompt
    assert "Preferuj: read_file('/tmp/wprowadzenie.md')" in prompt
    assert "Dozwolone narzędzia:" in prompt
    assert "1) Zapisz plik: write_file" in prompt
    assert "5) Oceń wynik wykonania" in prompt


def test_plan_tracking_corrective_prompt_requires_notes_plan_file() -> None:
    prompt = _build_plan_tracking_corrective_prompt("kontynuuj eksperyment")

    assert "notes/main_plan.json" in prompt
    assert "goal" in prompt
    assert "key_achievement" in prompt
    assert "status" in prompt


def test_resolve_tool_path_strips_leading_work_dir_segment() -> None:
    work_dir = Path("/home/chestr/Documents/projekty/amiagi/amiagi-my-work")

    resolved = _resolve_tool_path("amiagi-my-work/test_script.py", work_dir)

    assert resolved == work_dir / "test_script.py"


def test_resolve_tool_path_keeps_other_relative_paths() -> None:
    work_dir = Path("/home/chestr/Documents/projekty/amiagi/amiagi-my-work")

    resolved = _resolve_tool_path("subdir/test_script.py", work_dir)

    assert resolved == work_dir / "subdir/test_script.py"


def test_resolve_tool_path_strips_leading_work_dir_alias_with_underscore() -> None:
    work_dir = Path("/home/chestr/Documents/projekty/amiagi/amiagi-my-work")

    resolved = _resolve_tool_path("amiagi_my_work/sum_of_squares.py", work_dir)

    assert resolved == work_dir / "sum_of_squares.py"


def test_resolve_tool_path_collapses_duplicated_absolute_work_dir_alias_segments() -> None:
    work_dir = Path("/home/chestr/Documents/projekty/amiagi/amiagi-my-work")

    resolved = _resolve_tool_path(
        "/home/chestr/Documents/projekty/amiagi/amiagi-my-work/amiagi_my_work/sum_of_squares.py",
        work_dir,
    )

    assert resolved == Path("/home/chestr/Documents/projekty/amiagi/amiagi-my-work/sum_of_squares.py")


def test_python_code_corrective_prompt_enforces_write_file_flow() -> None:
    prompt = _build_python_code_corrective_prompt()

    assert "zawiera kod źródłowy" in prompt
    assert "zapisze ten kod do pliku przez write_file" in prompt
    assert "Dozwolone narzędzia:" in prompt
    assert "3) Sprawdź składnię: check_python_syntax" in prompt


def test_unparsed_tool_call_corrective_prompt_enforces_canonical_block() -> None:
    prompt = _build_unparsed_tool_call_corrective_prompt()

    assert "nie jest w poprawnym formacie wykonywalnym" in prompt
    assert "Teraz zwróć WYŁĄCZNIE jeden blok" in prompt
    assert "Bez YAML" in prompt
    assert "Dozwolone narzędzia:" in prompt


def test_is_non_action_placeholder_detects_none_and_null() -> None:
    assert _is_non_action_placeholder("None") is True
    assert _is_non_action_placeholder(" null ") is True
    assert _is_non_action_placeholder("````none````") is True
    assert _is_non_action_placeholder("zwykła odpowiedź") is False


def test_looks_like_unparsed_tool_call_detects_yaml_form() -> None:
    answer = '''```yaml
tool_call:
    name: write_file
    args:
        path: scripts/hello.py
        content: |
            print("ok")
        overwrite: true
```'''

    assert _looks_like_unparsed_tool_call(answer) is True
    assert _looks_like_unparsed_tool_call("To jest normalna odpowiedź tekstowa.") is False


def test_has_supported_tool_call_detects_python_style_invocation() -> None:
    answer = """```python
list_dir('.')
```"""

    assert _has_supported_tool_call(answer) is True
    assert _has_unknown_tool_calls(answer) is False


def test_has_unknown_tool_calls_detects_supervisor_drift_tool_name() -> None:
    answer = '```tool_call\n{"tool":"filesystem","args":{"path":"."},"intent":"scan"}\n```'

    assert _has_unknown_tool_calls(answer) is True
    assert _has_supported_tool_call(answer) is False


def test_has_supported_tool_call_recognizes_search_web() -> None:
    answer = '```tool_call\n{"tool":"search_web","args":{"query":"python"},"intent":"search"}\n```'

    assert _has_unknown_tool_calls(answer) is False
    assert _has_supported_tool_call(answer) is True


def test_has_supported_tool_call_recognizes_check_capabilities() -> None:
    answer = '```tool_call\n{"tool":"check_capabilities","args":{"check_network":false},"intent":"diag"}\n```'

    assert _has_unknown_tool_calls(answer) is False
    assert _has_supported_tool_call(answer) is True


def test_canonicalize_tool_calls_generates_tool_call_blocks() -> None:
    calls = [
        ToolCall(tool="list_dir", args={"path": "."}, intent="scan"),
        ToolCall(tool="read_file", args={"path": "wprowadzenie.md", "max_chars": 12000}, intent="inspect"),
    ]

    normalized = _canonicalize_tool_calls(calls)

    assert normalized.count("```tool_call") == 2
    assert '"tool": "list_dir"' in normalized
    assert '"tool": "read_file"' in normalized


def test_is_path_within_work_dir_accepts_nested_path() -> None:
    work_dir = Path("/tmp/amiagi-my-work")
    assert _is_path_within_work_dir(work_dir / "notes" / "a.md", work_dir) is True


def test_is_path_within_work_dir_rejects_path_outside() -> None:
    work_dir = Path("/tmp/amiagi-my-work")
    assert _is_path_within_work_dir(Path("/tmp/other/a.md"), work_dir) is False


def test_build_microphone_profiles_prefers_requested_then_fallbacks() -> None:
    profiles = _build_microphone_profiles(16000, 1)

    assert profiles[0] == (16000, 1)
    assert (48000, 2) in profiles
    assert (44100, 2) in profiles


def test_detect_preferred_microphone_device_prefers_webcam(monkeypatch) -> None:
    class DummyCompleted:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = (
                "card 0: PCH [HDA Intel PCH], device 0: ALC1220 Analog [ALC1220 Analog]\n"
                "card 2: C920 [HD Pro Webcam C920], device 0: USB Audio [USB Audio]\n"
            )

    monkeypatch.setattr("amiagi.interfaces.cli.shutil.which", lambda name: "/usr/bin/arecord" if name == "arecord" else None)
    monkeypatch.setattr(
        "amiagi.interfaces.cli.subprocess.run",
        lambda *args, **kwargs: DummyCompleted(),
    )

    device = _detect_preferred_microphone_device()
    assert device == "hw:2,0"
