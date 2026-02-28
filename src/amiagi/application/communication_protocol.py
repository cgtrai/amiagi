"""Communication protocol — loader, parser, and prompt builder.

Loads ``config/communication_rules.json`` and exposes helpers used by
chat_service, supervisor_service and the textual router.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AddressedBlock:
    """A single addressed segment parsed from a model response."""
    sender: str
    target: str
    content: str


@dataclass(frozen=True)
class CommunicationRules:
    """Parsed representation of config/communication_rules.json."""
    protocol_version: str = "1.1"
    greeting_text: str = ""
    polluks_rules: list[str] = field(default_factory=list)
    polluks_examples: list[str] = field(default_factory=list)
    polluks_peers: dict[str, str] = field(default_factory=dict)
    kastor_rules: list[str] = field(default_factory=list)
    kastor_examples: list[str] = field(default_factory=list)
    kastor_peers: dict[str, str] = field(default_factory=dict)
    missing_header_threshold: int = 2
    reminder_template: str = ""
    max_reminders_per_session: int = 5
    sponsor_readability_check: bool = True
    consultation_enabled: bool = True
    consultation_max_rounds: int = 1
    history_turns_for_kastor: int = 5
    panel_mapping: dict[str, str | list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Regex for parsing [Sender -> Target] headers
# ---------------------------------------------------------------------------

_VALID_ACTORS = {"Polluks", "Kastor", "Koordynator", "Sponsor"}
_VALID_TARGETS = _VALID_ACTORS | {"all"}

_ADDRESSED_HEADER_RE = re.compile(
    r"\[(?P<sender>Polluks|Kastor|Koordynator|Sponsor)"
    r"\s*->\s*"
    r"(?P<target>Polluks|Kastor|Koordynator|Sponsor|all)\]"
)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_RULES_PATH = Path("config/communication_rules.json")


def load_communication_rules(path: Path | None = None) -> CommunicationRules:
    """Load communication rules from JSON.  Returns defaults on failure."""
    resolved = path or _DEFAULT_RULES_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        return CommunicationRules()

    if not isinstance(raw, dict):
        return CommunicationRules()

    greeting = raw.get("greeting", {})
    actors = raw.get("actors", {})
    polluks = actors.get("Polluks", {})
    kastor = actors.get("Kastor", {})
    routing = raw.get("routing_rules", {})
    reminders = raw.get("reminders", {})
    consultation = raw.get("consultation", {})
    history = raw.get("history_context", {})

    return CommunicationRules(
        protocol_version=str(raw.get("protocol_version", "1.1")),
        greeting_text=str(greeting.get("text", "")),
        polluks_rules=list(polluks.get("communication_rules", [])),
        polluks_examples=list(polluks.get("addressing_examples", [])),
        polluks_peers=dict(polluks.get("peers_context", {})),
        kastor_rules=list(kastor.get("communication_rules", [])),
        kastor_examples=list(kastor.get("addressing_examples", [])),
        kastor_peers=dict(kastor.get("peers_context", {})),
        missing_header_threshold=int(reminders.get("threshold_turns", 2)),
        reminder_template=str(reminders.get("kastor_reminder_template", "")),
        max_reminders_per_session=int(reminders.get("max_reminders_per_session", 5)),
        sponsor_readability_check=bool(routing.get("sponsor_readability_check", True)),
        consultation_enabled=bool(consultation.get("enabled", True)),
        consultation_max_rounds=int(consultation.get("max_rounds_per_cycle", 1)),
        history_turns_for_kastor=int(history.get("turns_for_kastor", 5)),
        panel_mapping=dict(routing.get("panel_mapping", {})),
    )


# ---------------------------------------------------------------------------
# Prompt builders (to be injected into system prompts)
# ---------------------------------------------------------------------------

def build_polluks_communication_prompt(rules: CommunicationRules) -> str:
    """Build the KOMUNIKACJA section for Polluks' system prompt."""
    lines = [
        "PROTOKÓŁ KOMUNIKACJI (OBOWIĄZKOWY):",
        f"[Koordynator -> Polluks] {rules.greeting_text[:600]}" if rules.greeting_text else "",
        "",
        "Twoje zasady komunikacji:",
    ]
    for idx, rule in enumerate(rules.polluks_rules, 1):
        lines.append(f"  {idx}. {rule}")

    if rules.polluks_peers:
        lines.append("")
        lines.append("Kontekst rozmówców:")
        for peer, desc in rules.polluks_peers.items():
            lines.append(f"  - {peer}: {desc}")

    if rules.polluks_examples:
        lines.append("")
        lines.append("Przykłady poprawnego adresowania:")
        for example in rules.polluks_examples:
            lines.append(f"  {example}")

    return "\n".join(line for line in lines if line is not None)


def build_kastor_communication_prompt(rules: CommunicationRules) -> str:
    """Build the KOMUNIKACJA section for Kastor's system prompt."""
    lines = [
        "PROTOKÓŁ KOMUNIKACJI (OBOWIĄZKOWY):",
        f"[Koordynator -> Kastor] {rules.greeting_text[:600]}" if rules.greeting_text else "",
        "",
        "Twoje zasady komunikacji:",
    ]
    for idx, rule in enumerate(rules.kastor_rules, 1):
        lines.append(f"  {idx}. {rule}")

    if rules.kastor_peers:
        lines.append("")
        lines.append("Kontekst rozmówców:")
        for peer, desc in rules.kastor_peers.items():
            lines.append(f"  - {peer}: {desc}")

    if rules.kastor_examples:
        lines.append("")
        lines.append("Przykłady poprawnego adresowania:")
        for example in rules.kastor_examples:
            lines.append(f"  {example}")

    return "\n".join(line for line in lines if line is not None)


# ---------------------------------------------------------------------------
# Address block parser (used by the router)
# ---------------------------------------------------------------------------

def parse_addressed_blocks(text: str) -> list[AddressedBlock]:
    """Split a model response into addressed blocks.

    Each block starts with ``[Sender -> Target]``.  Content before the first
    header is returned as a block with sender/target = "" (unaddressed).
    Tool-call fenced blocks are left intact inside the content.
    """
    matches = list(_ADDRESSED_HEADER_RE.finditer(text))
    if not matches:
        return [AddressedBlock(sender="", target="", content=text.strip())] if text.strip() else []

    blocks: list[AddressedBlock] = []

    # Content before first header → unaddressed block
    prefix = text[: matches[0].start()].strip()
    if prefix:
        blocks.append(AddressedBlock(sender="", target="", content=prefix))

    for i, match in enumerate(matches):
        sender = match.group("sender")
        target = match.group("target")
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            blocks.append(AddressedBlock(sender=sender, target=target, content=content))

    return blocks


def has_valid_address_header(text: str) -> bool:
    """Return True if the text contains at least one valid [X -> Y] header."""
    return bool(_ADDRESSED_HEADER_RE.search(text))


# ---------------------------------------------------------------------------
# Sponsor readability check
# ---------------------------------------------------------------------------

_SPONSOR_UNFRIENDLY_PATTERNS = re.compile(
    r'```|"tool":|"args":|"tool_call"|"status":\s*"ok"|"reason_code"'
)


def is_sponsor_readable(content: str) -> bool:
    """Return True if the content looks readable for a non-technical Sponsor."""
    if not content.strip():
        return True
    brace_count = content.count("{") + content.count("}")
    if brace_count > 4:
        return False
    if _SPONSOR_UNFRIENDLY_PATTERNS.search(content):
        return False
    return True


# ---------------------------------------------------------------------------
# Panel routing helper
# ---------------------------------------------------------------------------

_DEFAULT_PANEL_MAPPING: dict[str, str | list[str]] = {
    "Sponsor": "user_model_log",
    "Kastor": "supervisor_log",
    "Polluks": "executor_log",
    "Koordynator": "executor_log",
    "all": ["user_model_log", "supervisor_log", "executor_log"],
}


def panels_for_target(target: str, mapping: dict[str, str | list[str]] | None = None) -> list[str]:
    """Return list of panel IDs for the given target actor."""
    m = mapping or _DEFAULT_PANEL_MAPPING
    result = m.get(target, "executor_log")
    if isinstance(result, list):
        return list(result)
    return [result]


# ---------------------------------------------------------------------------
# History formatter (for Kastor review context)
# ---------------------------------------------------------------------------

def format_conversation_excerpt(
    messages: Sequence,
    limit: int = 5,
) -> str:
    """Format recent messages with actor headers for Kastor review context.

    ``messages`` should be a sequence of objects with ``.role``, ``.content``
    and ``.actor`` attributes (``Message`` dataclass).
    """
    recent = list(messages)[-limit:] if len(messages) > limit else list(messages)
    if not recent:
        return ""

    lines = ["[CONVERSATION_HISTORY]"]
    for msg in recent:
        actor = getattr(msg, "actor", "") or ""
        role = msg.role
        header = f"[{actor}]" if actor else f"[{role}]"
        # truncate long messages
        content = msg.content[:400] + "..." if len(msg.content) > 400 else msg.content
        lines.append(f"{header} {content}")
    lines.append("[/CONVERSATION_HISTORY]")
    return "\n".join(lines)
