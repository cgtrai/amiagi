from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict[str, Any]
    intent: str


_FENCED_TOOL_CALL = re.compile(r"```tool_call\s*(?P<payload>\{.*?\})\s*```", re.DOTALL)
_FENCED_TOOL_CALL_LOOSE = re.compile(
    r"```\s*tool_call\s*(?P<payload>\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)
_FENCED_JSON = re.compile(r"```json\s*(?P<payload>\{.*?\})\s*```", re.DOTALL)
_FENCED_ANY = re.compile(r"```[a-zA-Z0-9_+-]*\s*(?P<payload>\{.*?\})\s*```", re.DOTALL)
_FENCED_YAML = re.compile(r"```ya?ml\s*(?P<payload>.*?)```", re.DOTALL | re.IGNORECASE)
_LEGACY_TOOL_CALL = re.compile(r"\[TOOL_CALL\]\((?P<body>.*?)\)", re.IGNORECASE | re.DOTALL)
_TOOL_CALLABLE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\((?P<raw_args>.*)\)$")
_COLON_TOOL_CALL_HEADER = re.compile(
    r"tool_call\s*:\s*(?P<tool>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|$)",
    re.IGNORECASE,
)
_DIRECT_TOOL_NAMES = {
    "read_file",
    "list_dir",
    "run_shell",
    "run_command",
    "run_python",
    "check_python_syntax",
    "fetch_web",
    "search_web",
    "capture_camera_frame",
    "record_microphone_clip",
    "check_capabilities",
    "write_file",
    "append_file",
}
_UNRESOLVED_AST_VALUE = object()


def parse_tool_calls(text: str) -> list[ToolCall]:
    payloads = _extract_payloads(text)
    calls: list[ToolCall] = []
    for payload_text in payloads:
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        calls.extend(_tool_calls_from_payload(payload))

    calls.extend(_parse_legacy_tool_call_syntax(text))
    calls.extend(_parse_colon_tool_call_syntax(text))
    calls.extend(_parse_yaml_tool_call_syntax(text))
    calls.extend(_parse_python_tool_call_invocations(text))
    calls.extend(_parse_python_direct_tool_invocations(text))

    deduplicated: list[ToolCall] = []
    seen: set[tuple[str, str, str]] = set()
    for call in calls:
        signature = (
            call.tool,
            json.dumps(call.args, sort_keys=True, ensure_ascii=False),
            call.intent,
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduplicated.append(call)
    return deduplicated


def _parse_colon_tool_call_syntax(text: str) -> list[ToolCall]:
    matches = list(_COLON_TOOL_CALL_HEADER.finditer(text))
    if not matches:
        return []

    parsed: list[ToolCall] = []
    for index, match in enumerate(matches):
        tool = match.group("tool").strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end]
        args = _extract_colon_style_args(tool, chunk)
        parsed.append(ToolCall(tool=tool, args=args, intent="colon_tool_call_syntax"))
    return parsed


def _parse_python_tool_call_invocations(text: str) -> list[ToolCall]:
    invocations = _extract_callable_invocations(text, {"tool_call"})
    parsed: list[ToolCall] = []

    for invocation in invocations:
        try:
            tree = ast.parse(invocation, mode="eval")
        except SyntaxError:
            continue

        call = tree.body
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Name) or call.func.id != "tool_call":
            continue
        if not call.args:
            continue

        tool_name = _extract_tool_name_from_ast(call.args[0])
        if tool_name is None:
            continue

        args: dict[str, Any] = {}
        if len(call.args) > 1:
            maybe_args = _extract_dict_from_ast(call.args[1])
            if maybe_args is not None:
                args.update(maybe_args)

        intent = ""
        for keyword in call.keywords:
            if keyword.arg == "intent":
                try:
                    raw_intent = ast.literal_eval(keyword.value)
                except (ValueError, SyntaxError):
                    raw_intent = ""
                intent = str(raw_intent)
            elif keyword.arg == "args":
                maybe_args = _extract_dict_from_ast(keyword.value)
                if maybe_args is not None:
                    args.update(maybe_args)

        normalized_tool, normalized_args = _normalize_tool_and_args(tool_name, args)
        parsed.append(
            ToolCall(
                tool=normalized_tool.strip(),
                args=normalized_args,
                intent=intent.strip() or "python_tool_call_syntax",
            )
        )

    return parsed


def _parse_python_direct_tool_invocations(text: str) -> list[ToolCall]:
    parsed_from_ast = _parse_python_calls_via_ast(text, _DIRECT_TOOL_NAMES)
    if parsed_from_ast:
        return parsed_from_ast

    invocations = _extract_callable_invocations(text, _DIRECT_TOOL_NAMES)
    parsed: list[ToolCall] = []

    for invocation in invocations:
        try:
            tree = ast.parse(invocation, mode="eval")
        except SyntaxError:
            continue

        call = tree.body
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Name):
            continue
        tool_name = call.func.id.strip()
        if tool_name not in _DIRECT_TOOL_NAMES:
            continue

        args: dict[str, Any] = {}
        positional_values = [
            value
            for value in (_ast_to_value(node) for node in call.args)
            if value is not _UNRESOLVED_AST_VALUE
        ]
        _apply_positional_args(tool_name, positional_values, args)

        for keyword in call.keywords:
            if keyword.arg is None:
                continue
            parsed_value = _ast_to_value(keyword.value)
            if parsed_value is _UNRESOLVED_AST_VALUE:
                continue
            args[keyword.arg] = parsed_value

        normalized_tool, normalized_args = _normalize_tool_and_args(tool_name, args)
        if not _has_required_args_for_tool(normalized_tool, normalized_args):
            continue
        parsed.append(
            ToolCall(
                tool=normalized_tool.strip(),
                args=normalized_args,
                intent="python_direct_tool_syntax",
            )
        )

    return parsed


def _parse_yaml_tool_call_syntax(text: str) -> list[ToolCall]:
    parsed: list[ToolCall] = []
    for match in _FENCED_YAML.finditer(text):
        payload = match.group("payload")
        wrapped = _parse_wrapped_yaml_tool_call(payload)
        if wrapped is not None:
            parsed.append(wrapped)
            continue

        tool_match = re.search(r"^\s*tool\s*:\s*(?P<tool>[A-Za-z_][A-Za-z0-9_]*)\s*$", payload, re.MULTILINE)
        if tool_match is None:
            continue

        args_match = re.search(r"^\s*args\s*:\s*(?P<args>\{.*\})\s*$", payload, re.MULTILINE)
        if args_match is None:
            continue

        tool = tool_match.group("tool").strip()
        raw_args = args_match.group("args").strip()
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            continue
        if not isinstance(args, dict):
            continue

        intent_match = re.search(r"^\s*intent\s*:\s*(?P<intent>.+?)\s*$", payload, re.MULTILINE)
        intent = intent_match.group("intent").strip().strip('"\'') if intent_match else "yaml_tool_call_syntax"
        parsed.append(ToolCall(tool=tool, args=args, intent=intent))

    return parsed


def _parse_wrapped_yaml_tool_call(payload: str) -> ToolCall | None:
    lines = payload.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped != "tool_call:":
            continue

        tool_call_indent = _line_indent(line)
        fields, _ = _parse_simple_yaml_mapping(lines, index + 1, tool_call_indent)
        if not isinstance(fields, dict):
            return None

        tool_raw = fields.get("tool", fields.get("name"))
        if not isinstance(tool_raw, str) or not tool_raw.strip():
            return None

        args_raw = fields.get("args", {})
        if isinstance(args_raw, str):
            stripped_args = args_raw.strip()
            if stripped_args.startswith("{") and stripped_args.endswith("}"):
                try:
                    parsed_args = json.loads(stripped_args)
                except json.JSONDecodeError:
                    return None
                args_raw = parsed_args
        if not isinstance(args_raw, dict):
            return None

        intent_raw = fields.get("intent", "yaml_tool_call_syntax")
        intent = str(intent_raw).strip() if isinstance(intent_raw, str) else "yaml_tool_call_syntax"
        return ToolCall(tool=tool_raw.strip(), args=args_raw, intent=intent or "yaml_tool_call_syntax")
    return None


def _parse_simple_yaml_mapping(
    lines: list[str],
    start_index: int,
    parent_indent: int,
) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    index = start_index
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue

        indent = _line_indent(raw)
        if indent <= parent_indent:
            break

        key, separator, raw_value = stripped.partition(":")
        if separator != ":":
            index += 1
            continue

        key = key.strip()
        value_text = raw_value.strip()
        if not key:
            index += 1
            continue

        if value_text == "|":
            literal_raw_lines: list[str] = []
            index += 1
            while index < len(lines):
                literal_raw = lines[index]
                literal_stripped = literal_raw.strip()
                literal_indent = _line_indent(literal_raw)
                if literal_stripped and literal_indent <= indent:
                    break
                if not literal_stripped:
                    literal_raw_lines.append("")
                else:
                    literal_raw_lines.append(literal_raw)
                index += 1
            normalized_literal = _normalize_yaml_literal_lines(literal_raw_lines)
            mapping[key] = "\n".join(normalized_literal).rstrip("\n")
            continue

        if value_text == "":
            nested, next_index = _parse_simple_yaml_mapping(lines, index + 1, indent)
            mapping[key] = nested
            index = next_index
            continue

        mapping[key] = _parse_yaml_scalar(value_text)
        index += 1

    return mapping, index


def _parse_yaml_scalar(value_text: str) -> Any:
    lowered = value_text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d+", value_text):
        try:
            return int(value_text)
        except ValueError:
            return value_text
    if re.fullmatch(r"-?\d+\.\d+", value_text):
        try:
            return float(value_text)
        except ValueError:
            return value_text

    stripped = value_text.strip()
    if (stripped.startswith('"') and stripped.endswith('"')) or (
        stripped.startswith("'") and stripped.endswith("'")
    ):
        return stripped[1:-1]
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
    return stripped


def _line_indent(value: str) -> int:
    return len(value) - len(value.lstrip(" "))


def _normalize_yaml_literal_lines(raw_lines: list[str]) -> list[str]:
    non_empty = [line for line in raw_lines if line.strip()]
    if not non_empty:
        return [""] * len(raw_lines)

    min_indent = min(_line_indent(line) for line in non_empty)
    normalized: list[str] = []
    for line in raw_lines:
        if not line.strip():
            normalized.append("")
            continue
        normalized.append(line[min_indent:])
    return normalized


def _extract_tool_name_from_ast(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _extract_dict_from_ast(node: ast.AST) -> dict[str, Any] | None:
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    if isinstance(value, dict):
        return value
    return None


def _parse_python_calls_via_ast(text: str, allowed_tools: set[str]) -> list[ToolCall]:
    parsed: list[ToolCall] = []
    segments = _extract_python_segments(text)
    for segment in segments:
        try:
            tree = ast.parse(segment, mode="exec")
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name):
                continue
            tool = node.func.id.strip()
            if tool not in allowed_tools:
                continue

            args: dict[str, Any] = {}
            positional_values = [
                value
                for value in (_ast_to_value(item) for item in node.args)
                if value is not _UNRESOLVED_AST_VALUE
            ]
            _apply_positional_args(tool, positional_values, args)

            for keyword in node.keywords:
                if keyword.arg is None:
                    continue
                parsed_value = _ast_to_value(keyword.value)
                if parsed_value is _UNRESOLVED_AST_VALUE:
                    continue
                args[keyword.arg] = parsed_value

            normalized_tool, normalized_args = _normalize_tool_and_args(tool, args)
            if not _has_required_args_for_tool(normalized_tool, normalized_args):
                continue
            parsed.append(
                ToolCall(
                    tool=normalized_tool.strip(),
                    args=normalized_args,
                    intent="python_direct_tool_syntax",
                )
            )
    return parsed


def _extract_python_segments(text: str) -> list[str]:
    segments: list[str] = []
    fenced_python = re.compile(r"```(?:python|py)\s*(?P<code>[\s\S]*?)```", re.IGNORECASE)
    for match in fenced_python.finditer(text):
        code = match.group("code").strip()
        if code:
            segments.append(code)
    stripped = text.strip()
    if stripped:
        segments.append(stripped)
    return segments


def _ast_to_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Name):
        if node.id == "true":
            return True
        if node.id == "false":
            return False
        if node.id == "none":
            return None
        return _UNRESOLVED_AST_VALUE
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return _UNRESOLVED_AST_VALUE


def _extract_callable_invocations(text: str, names: set[str]) -> list[str]:
    snippets: list[str] = []
    for name in sorted(names, key=len, reverse=True):
        start = 0
        while True:
            idx = text.find(name, start)
            if idx == -1:
                break

            if idx > 0 and (text[idx - 1].isalnum() or text[idx - 1] == "_"):
                start = idx + len(name)
                continue

            pos = idx + len(name)
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if pos >= len(text) or text[pos] != "(":
                start = idx + len(name)
                continue

            depth = 0
            in_string = False
            string_delim = ""
            escaped = False
            end_pos = None

            for cursor in range(pos, len(text)):
                ch = text[cursor]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == string_delim:
                        in_string = False
                    continue

                if ch in {"'", '"'}:
                    in_string = True
                    string_delim = ch
                    continue
                if ch == "(":
                    depth += 1
                    continue
                if ch == ")":
                    depth -= 1
                    if depth == 0:
                        end_pos = cursor + 1
                        break

            if end_pos is None:
                start = idx + len(name)
                continue

            snippets.append(text[idx:end_pos])
            start = end_pos

    return snippets


def _extract_colon_style_args(tool: str, chunk: str) -> dict[str, Any]:
    args: dict[str, Any] = {}

    def extract_string_arg(name: str) -> str | None:
        patterns = [
            rf"{name}\s*=\s*'''(?P<value>.*?)'''",
            rf'{name}\s*=\s*"""(?P<value>.*?)"""',
            rf"{name}\s*=\s*'(?P<value>[^']*)'",
            rf'{name}\s*=\s*"(?P<value>[^"]*)"',
        ]
        for pattern in patterns:
            found = re.search(pattern, chunk, re.DOTALL)
            if found:
                return found.group("value")
        return None

    def extract_int_arg(name: str) -> int | None:
        found = re.search(rf"{name}\s*=\s*(?P<value>-?\d+)", chunk)
        if not found:
            return None
        try:
            return int(found.group("value"))
        except ValueError:
            return None

    def extract_bool_arg(name: str) -> bool | None:
        found = re.search(rf"{name}\s*=\s*(?P<value>true|false)", chunk, re.IGNORECASE)
        if not found:
            return None
        return found.group("value").lower() == "true"

    if tool in {"read_file", "list_dir", "write_file", "append_file", "run_python"}:
        path = extract_string_arg("path")
        if path is not None:
            args["path"] = path

    if tool == "fetch_web":
        url = extract_string_arg("url")
        if url is not None:
            args["url"] = url

    if tool in {"run_shell", "run_command"}:
        command = extract_string_arg("command")
        if command is not None:
            args["command"] = command

    if tool == "write_file":
        content = extract_string_arg("content")
        if content is not None:
            args["content"] = content
        overwrite = extract_bool_arg("overwrite")
        if overwrite is not None:
            args["overwrite"] = overwrite

    max_chars = extract_int_arg("max_chars")
    if max_chars is not None:
        args["max_chars"] = max_chars

    return args


def _parse_legacy_tool_call_syntax(text: str) -> list[ToolCall]:
    parsed: list[ToolCall] = []
    for match in _LEGACY_TOOL_CALL.finditer(text):
        body = match.group("body").strip()
        if not body:
            continue

        raw_parts = [part.strip() for part in body.split(",") if part.strip()]
        if not raw_parts:
            continue

        tool = _strip_wrappers(raw_parts[0]).strip()
        if not tool:
            continue

        args: dict[str, Any] = {}
        positional = []
        for token in raw_parts[1:]:
            if "=" in token:
                key, value = token.split("=", 1)
                key_clean = _strip_wrappers(key).strip()
                value_clean = _coerce_value(_strip_wrappers(value).strip())
                if key_clean:
                    args[key_clean] = value_clean
            else:
                positional.append(_coerce_value(_strip_wrappers(token).strip()))

        _apply_positional_args(tool, positional, args)
        parsed.append(ToolCall(tool=tool, args=args, intent="legacy_tool_call_syntax"))
    return parsed


def _apply_positional_args(tool: str, positional: list[Any], args: dict[str, Any]) -> None:
    if not positional:
        return

    if tool in {"read_file", "list_dir", "write_file", "append_file", "run_python"}:
        path = positional[0]
        if isinstance(path, str) and path.strip():
            args.setdefault("path", path)
    elif tool in {"run_shell", "run_command"}:
        command = positional[0]
        if isinstance(command, str) and command.strip():
            args.setdefault("command", command)
    elif tool == "fetch_web":
        url = positional[0]
        if isinstance(url, str) and url.strip():
            args.setdefault("url", url)


def _has_required_args_for_tool(tool: str, args: dict[str, Any]) -> bool:
    if tool in {"read_file", "list_dir", "run_python"}:
        path = args.get("path")
        return isinstance(path, str) and bool(path.strip())

    if tool in {"write_file", "append_file"}:
        path = args.get("path")
        content = args.get("content", args.get("data"))
        return isinstance(path, str) and bool(path.strip()) and content is not None

    if tool in {"run_shell", "run_command"}:
        command = args.get("command")
        return isinstance(command, str) and bool(command.strip())

    if tool == "fetch_web":
        url = args.get("url")
        return isinstance(url, str) and bool(url.strip())

    return True


def _strip_wrappers(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("`") and cleaned.endswith("`") and len(cleaned) >= 2:
        return cleaned[1:-1]
    if cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) >= 2:
        return cleaned[1:-1]
    if cleaned.startswith("'") and cleaned.endswith("'") and len(cleaned) >= 2:
        return cleaned[1:-1]
    return cleaned


def _coerce_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError:
            return value
    return value


def parse_tool_call(text: str) -> ToolCall | None:
    calls = parse_tool_calls(text)
    return calls[0] if calls else None


def _extract_payloads(text: str) -> list[str]:
    payloads: list[str] = []
    for pattern in (_FENCED_TOOL_CALL, _FENCED_TOOL_CALL_LOOSE, _FENCED_JSON):
        for match in pattern.finditer(text):
            payload = match.group("payload")
            if payload not in payloads:
                payloads.append(payload)

    for candidate in _FENCED_ANY.finditer(text):
        payload = candidate.group("payload")
        if payload not in payloads:
            payloads.append(payload)

    payloads.extend(_extract_json_objects(text))

    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        payloads.append(stripped)
    return payloads


def _extract_json_objects(text: str) -> list[str]:
    decoder = json.JSONDecoder()
    found: list[str] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            _, offset = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        found.append(text[index : index + offset])
    return found


def _tool_calls_from_payload(payload: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []

    single = _single_tool_call_from_payload(payload)
    if single is not None:
        calls.append(single)

    tool_calls = payload.get("tool_calls")
    if isinstance(tool_calls, list):
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("tool")
            args = item.get("args", item.get("arguments", {}))
            intent = item.get("intent", "")
            if isinstance(name, str) and isinstance(args, dict):
                normalized_tool, normalized_args = _normalize_tool_and_args(name, args)
                calls.append(
                    ToolCall(tool=normalized_tool.strip(), args=normalized_args, intent=str(intent).strip())
                )

    return calls


def _single_tool_call_from_payload(payload: dict[str, Any]) -> ToolCall | None:
    tool = payload.get("tool")
    args = payload.get("args", payload.get("arguments", {}))
    intent = payload.get("intent", "")

    if not isinstance(tool, str) or not tool.strip():
        tool_call = payload.get("tool_call")
        if isinstance(tool_call, dict):
            name = tool_call.get("name")
            arguments = tool_call.get("arguments", {})
            if isinstance(name, str):
                tool = name
                args = arguments if isinstance(arguments, dict) else {}
                if not intent:
                    intent = payload.get("intent", "")
        elif isinstance(tool_call, str):
            tool = tool_call
            if not isinstance(args, dict):
                args = {}

    if not isinstance(tool, str) or not tool.strip():
        return None
    if not isinstance(args, dict):
        return None
    tool, args = _normalize_tool_and_args(tool, args)
    if not args:
        args = _infer_root_args(payload)
    if not isinstance(intent, str):
        intent = ""

    return ToolCall(tool=tool.strip(), args=args, intent=intent.strip())


def _normalize_tool_and_args(tool: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    stripped_tool = tool.strip()
    match = _TOOL_CALLABLE.match(stripped_tool)
    if match is None:
        return stripped_tool, args

    name = match.group("name").strip()
    raw_args = match.group("raw_args").strip()
    if not raw_args:
        return name, args

    positional_tokens = [token.strip() for token in raw_args.split(",") if token.strip()]
    positional_values = [_coerce_value(_strip_wrappers(token)) for token in positional_tokens]

    merged_args = dict(args)
    _apply_positional_args(name, positional_values, merged_args)
    return name, merged_args


def _infer_root_args(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "path",
        "max_chars",
        "url",
        "query",
        "engine",
        "max_results",
        "output_path",
        "duration_seconds",
        "sample_rate_hz",
        "channels",
        "device",
        "check_network",
        "command",
        "content",
        "overwrite",
        "args",
        "cwd",
    }
    return {
        key: value
        for key, value in payload.items()
        if key in allowed and key not in {"tool", "tool_call", "tool_calls", "intent"}
    }
