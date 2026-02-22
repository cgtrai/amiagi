from __future__ import annotations

from amiagi.application.discussion_sync import extract_dialogue_without_code


def build_startup_summary(raw_markdown_dialogue: str) -> str:
    dialogue = extract_dialogue_without_code(raw_markdown_dialogue)
    user_turns = [
        line.strip()
        for line in dialogue.splitlines()
        if line.strip().startswith(">>>")
    ]
    highlights = user_turns[:5]

    lines = [
        "PUNKT STARTOWY SESJI:",
        "- Użytkownik uruchomił eksperyment lokalnego agenta na Ubuntu + Ollama.",
        "- Wymagania obejmują: pamięć trwałą, zgody zasobowe, logowanie działań, bezpieczeństwo uruchamiania.",
        "- Zbudowano kompletną aplikację CLI realizującą te wymagania.",
        "- Model ma być świadomy aktualnych możliwości frameworka i może go rozbudowywać po uzyskaniu zgody użytkownika.",
        "- Program jest gotowy operacyjnie: model może kontynuować eksplorację z zachowaniem kontroli i bezpieczeństwa.",
    ]

    if highlights:
        lines.append("- Kluczowe wypowiedzi użytkownika z rozmowy początkowej:")
        for turn in highlights:
            lines.append(f"  - {turn}")

    lines.append(
        "- Konkluzja: napisaliśmy ten program, a teraz model może zacząć poznawać świat "
        "w ramach dostępnych i zatwierdzonych zasobów."
    )
    return "\n".join(lines)
