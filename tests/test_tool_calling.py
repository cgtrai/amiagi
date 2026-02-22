from __future__ import annotations

from amiagi.application.tool_calling import parse_tool_call, parse_tool_calls


def test_parse_fenced_tool_call() -> None:
    text = """```tool_call
{"tool":"read_file","args":{"path":"/tmp/a.txt"},"intent":"odczyt"}
```"""
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "read_file"
    assert call.args["path"] == "/tmp/a.txt"


def test_parse_invalid_tool_call_returns_none() -> None:
    assert parse_tool_call("niepoprawne") is None


def test_parse_fenced_json_tool_call_schema() -> None:
    text = """```json
{"tool_call":{"name":"list_dir","arguments":{"path":"."}},"intent":"lista"}
```"""
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "list_dir"
    assert call.args["path"] == "."
    assert call.intent == "lista"


def test_parse_plain_json_tool_call_schema_without_intent() -> None:
    text = '{"tool_call":{"name":"fetch_web","arguments":{"url":"https://example.com"}}}'
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "fetch_web"
    assert call.args["url"] == "https://example.com"
    assert call.intent == ""


def test_parse_batched_tool_calls_array() -> None:
    text = '{"tool_calls":[{"name":"list_dir","args":{"path":"."},"intent":"scan"},{"name":"read_file","args":{"path":"README.md","max_chars":1000},"intent":"inspect"}]}'
    calls = parse_tool_calls(text)

    assert len(calls) == 2
    assert calls[0].tool == "list_dir"
    assert calls[1].tool == "read_file"


def test_parse_tool_calls_from_fenced_python_block() -> None:
    text = """```python
{"tool_calls":[{"name":"run_command","args":{"command":"ls -la"},"intent":"check"}]}
```"""
    calls = parse_tool_calls(text)

    assert len(calls) == 1
    assert calls[0].tool == "run_command"
    assert calls[0].args["command"] == "ls -la"


def test_parse_tool_call_returns_first_from_batched_payload() -> None:
    text = '{"tool_calls":[{"name":"list_dir","args":{"path":"."}}]}'
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "list_dir"


def test_parse_tool_call_string_with_root_arguments() -> None:
    text = '{"tool_call":"list_dir","arguments":{"path":"."}}'
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "list_dir"
    assert call.args["path"] == "."


def test_parse_legacy_tool_call_read_file_syntax() -> None:
    text = "[TOOL_CALL](`read_file`, `/tmp/demo.txt`)"
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "read_file"
    assert call.args["path"] == "/tmp/demo.txt"


def test_parse_legacy_tool_call_fetch_web_with_named_arg() -> None:
    text = "[TOOL_CALL](`fetch_web`, `https://example.com`, `max_chars`=5000)"
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "fetch_web"
    assert call.args["url"] == "https://example.com"
    assert call.args["max_chars"] == 5000


def test_parse_tool_call_with_root_level_path_argument() -> None:
    text = '{"tool":"list_dir","path":"/home/chestr/Documents/projekty/amiagi/amiagi-my-work"}'
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "list_dir"
    assert call.args["path"] == "/home/chestr/Documents/projekty/amiagi/amiagi-my-work"


def test_parse_tool_call_callable_string_with_positional_argument() -> None:
    text = '{"tool_call": "list_dir(\'/home\')"}'
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "list_dir"
    assert call.args["path"] == "/home"


def test_parse_tool_call_callable_string_with_arguments_payload() -> None:
    text = '{"tool_call": "read_file()", "arguments": {"path": "wprowadzenie.txt", "max_chars": 12000}}'
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "read_file"
    assert call.args["path"] == "wprowadzenie.txt"
    assert call.args["max_chars"] == 12000


def test_parse_colon_style_tool_call_list_dir() -> None:
    text = 'tool_call: list_dir(path="amiagi-my-work")'
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "list_dir"
    assert call.args["path"] == "amiagi-my-work"


def test_parse_colon_style_tool_call_fetch_web_multiline() -> None:
    text = '''tool_call: fetch_web(
    url="https://capterra.com/business-software",
    max_chars=12000
)'''
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "fetch_web"
    assert call.args["url"] == "https://capterra.com/business-software"
    assert call.args["max_chars"] == 12000


def test_parse_colon_style_tool_call_write_file_with_triple_quoted_content() -> None:
    text = (
        "tool_call: write_file(\n"
        "    path=\"badanie_rynku.py\",\n"
        "    content='''import asyncio\\nprint(\"ok\")\\n''',\n"
        "    overwrite=true\n"
        ")"
    )
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "write_file"
    assert call.args["path"] == "badanie_rynku.py"
    assert "import asyncio" in call.args["content"]
    assert call.args["overwrite"] is True


def test_parse_python_style_tool_call_invocation_in_code_block() -> None:
    text = """```python
tool_call(list_dir, {\"path\": \"\"})
```"""
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "list_dir"
    assert call.args["path"] == ""


def test_parse_python_direct_write_file_invocation_with_lowercase_true() -> None:
    text = """```python
write_file(
    path="hello_world.py",
    content='print("Hello World")',
    overwrite=true
)
```"""
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "write_file"
    assert call.args["path"] == "hello_world.py"
    assert call.args["content"] == 'print("Hello World")'
    assert call.args["overwrite"] is True


def test_parse_yaml_fenced_tool_call_format() -> None:
    text = """```yaml
---
tool_call:
  tool: read_file
  args: {"path":"/amiagi-my-work/test.txt","max_chars":12000}
```
"""
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "read_file"
    assert call.args["path"] == "/amiagi-my-work/test.txt"
    assert call.args["max_chars"] == 12000


def test_parse_yaml_fenced_nested_tool_call_with_multiline_args() -> None:
    text = '''```yaml
tool_call:
    name: write_file
    args:
        path: scripts/hello.py
        content: |
            print("Hello from YAML")
            print("Second line")
        overwrite: true
```
'''
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "write_file"
    assert call.args["path"] == "scripts/hello.py"
    assert call.args["content"] == 'print("Hello from YAML")\nprint("Second line")'
    assert call.args["overwrite"] is True


def test_parse_loose_fenced_tool_call_marker_on_new_line() -> None:
    text = '''```
tool_call
{"tool":"write_file","args":{"path":"hello_world.py","content":"print(1)","overwrite":true},"intent":"save"}
```'''
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "write_file"
    assert call.args["path"] == "hello_world.py"
    assert call.args["overwrite"] is True


def test_parse_python_direct_write_file_with_triple_quoted_content() -> None:
    text = '''```python
write_file(
    path="sum_evens.py",
    content="""# Calculate sum of even numbers between 1 and 100
def sum_evens():
    total = 0
    for i in range(1, 101):
        if i % 2 == 0:
            total += i
    return total
""",
    overwrite=True
)
```
'''
    call = parse_tool_call(text)

    assert call is not None
    assert call.tool == "write_file"
    assert call.args["path"] == "sum_evens.py"
    assert "def sum_evens" in call.args["content"]
    assert call.args["overwrite"] is True


def test_parse_python_direct_write_file_with_variable_path_returns_none() -> None:
    text = '''```python
cmd = ["a.py", "b.py"]
write_file(path=cmd[1], content="print(1)", overwrite=true)
```
'''
    call = parse_tool_call(text)

    assert call is None


def test_parse_python_direct_write_file_with_variable_positional_path_returns_none() -> None:
    text = '''```python
dest_path = "hello.py"
write_file(dest_path, content="print(1)", overwrite=true)
```
'''
    call = parse_tool_call(text)

    assert call is None
