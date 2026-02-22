from __future__ import annotations


def extract_dialogue_without_code(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    in_fenced_code = False
    output: list[str] = []

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            in_fenced_code = not in_fenced_code
            continue

        if in_fenced_code:
            continue

        output.append(line)

    cleaned = "\n".join(output)
    compact_lines = [line.rstrip() for line in cleaned.splitlines()]
    while compact_lines and not compact_lines[-1]:
        compact_lines.pop()
    return "\n".join(compact_lines).strip()
