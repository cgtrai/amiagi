"""Phase 2.7 — WizardConversation — interactive multi-turn agent creation.

Implements a stateful conversation flow where the Sponsor describes a need,
the wizard asks clarifying questions, and iteratively refines the blueprint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from amiagi.application.model_client_protocol import ChatCompletionClient
from amiagi.domain.blueprint import AgentBlueprint


class ConversationPhase:
    GATHERING = "gathering"
    REFINING = "refining"
    REVIEW = "review"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


_SYSTEM_PROMPT = """\
You are an expert AI agent wizard. You help the Sponsor create a new agent
by asking pointed questions about the agent's purpose, tools, and persona.

Follow this conversation flow:
1. Ask what task the agent should perform.
2. Ask about preferred communication style (formal/casual/balanced).
3. Ask which tools the agent needs (offer suggestions based on the task).
4. Ask if the agent needs special permissions (shell, network, etc.).
5. Summarise the blueprint and ask for confirmation.

At each step, wait for the user's answer before proceeding.
When you have enough info, produce the final blueprint as JSON wrapped in
```blueprint ... ``` fences.
"""


@dataclass
class WizardConversation:
    """Multi-turn interactive wizard that builds an AgentBlueprint.

    Usage::

        conv = WizardConversation(planner_client=my_llm)
        reply1 = conv.start("I need a code reviewer")   # wizard asks questions
        reply2 = conv.reply("Python backend, formal style")  # refines
        ...
        if conv.is_confirmed:
            bp = conv.blueprint
    """

    planner_client: ChatCompletionClient | None = None
    phase: str = ConversationPhase.GATHERING
    messages: list[dict[str, str]] = field(default_factory=list)
    blueprint: AgentBlueprint | None = None
    turn_count: int = 0
    max_turns: int = 10
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_confirmed(self) -> bool:
        return self.phase == ConversationPhase.CONFIRMED

    @property
    def is_cancelled(self) -> bool:
        return self.phase == ConversationPhase.CANCELLED

    @property
    def is_active(self) -> bool:
        return self.phase in (ConversationPhase.GATHERING, ConversationPhase.REFINING, ConversationPhase.REVIEW)

    def start(self, initial_need: str) -> str:
        """Begin the wizard conversation with the Sponsor's initial need.

        Returns the wizard's first reply (a question or prompt).
        """
        self.phase = ConversationPhase.GATHERING
        self.messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"I need: {initial_need}"},
        ]
        self.turn_count = 1
        reply = self._get_reply()
        return reply

    def reply(self, user_message: str) -> str:
        """Process the Sponsor's answer and return the wizard's next reply."""
        if not self.is_active:
            return "Wizard conversation is not active."

        # Handle confirmation shortcuts
        lower = user_message.strip().lower()
        if lower in ("yes", "tak", "confirm", "ok", "potwierdź"):
            if self.blueprint is not None:
                self.phase = ConversationPhase.CONFIRMED
                return "Blueprint confirmed! Agent ready to create."

        if lower in ("cancel", "anuluj", "no", "nie"):
            self.phase = ConversationPhase.CANCELLED
            return "Wizard cancelled."

        self.messages.append({"role": "user", "content": user_message})
        self.turn_count += 1

        if self.turn_count >= self.max_turns:
            # Force finalization
            self.messages.append({
                "role": "user",
                "content": "Please finalize the blueprint now with what you know.",
            })

        reply = self._get_reply()

        # Check if the wizard produced a blueprint
        bp = self._extract_blueprint(reply)
        if bp is not None:
            self.blueprint = bp
            self.phase = ConversationPhase.REVIEW

        return reply

    def confirm(self) -> bool:
        """Explicitly confirm the current blueprint."""
        if self.blueprint is not None:
            self.phase = ConversationPhase.CONFIRMED
            return True
        return False

    def cancel(self) -> None:
        self.phase = ConversationPhase.CANCELLED

    # ---- internal ----

    def _get_reply(self) -> str:
        """Get the wizard's reply using the planner LLM or a fallback."""
        if self.planner_client is not None:
            try:
                reply = self.planner_client.chat(
                    messages=self.messages,
                    system_prompt=_SYSTEM_PROMPT,
                )
                self.messages.append({"role": "assistant", "content": reply})
                return reply
            except Exception:
                pass

        # Heuristic fallback (no LLM)
        return self._heuristic_reply()

    def _heuristic_reply(self) -> str:
        """Template-based replies when no LLM is available."""
        if self.turn_count == 1:
            reply = (
                "Rozumiem. Chcę zadać kilka pytań, żeby dobrze skonfigurować agenta:\n\n"
                "1. Jaki styl komunikacji preferujesz? (formalny / casualowy / zbalansowany)\n"
                "2. Czy agent potrzebuje dostępu do shell/terminala?\n"
                "3. Czy agent potrzebuje dostępu do internetu?"
            )
        elif self.turn_count == 2:
            reply = (
                "Dzięki! Jeszcze jedno pytanie:\n"
                "Jakie narzędzia agent powinien mieć? Dostępne:\n"
                "- read_file, write_file, list_dir\n"
                "- run_python, check_python_syntax\n"
                "- run_shell\n"
                "- fetch_web, search_web\n\n"
                "Wymień potrzebne lub napisz 'wszystkie'."
            )
        else:
            # Build a basic blueprint from conversation
            need = ""
            for msg in self.messages:
                if msg["role"] == "user" and msg["content"].startswith("I need:"):
                    need = msg["content"].removeprefix("I need:").strip()
                    break

            bp = AgentBlueprint(
                name=need.split()[0].lower() if need.split() else "new_agent",
                role="executor",
                team_function=need[:120],
                persona_prompt=f"Agent stworzony przez wizard na podstawie: {need}",
                required_tools=["read_file", "list_dir", "write_file"],
                communication_style="balanced",
            )
            bp_json = json.dumps(bp.to_dict(), indent=2, ensure_ascii=False)
            reply = (
                f"Oto proponowany blueprint:\n\n```blueprint\n{bp_json}\n```\n\n"
                "Czy potwierdzasz? (tak/nie)"
            )
            self.blueprint = bp
            self.phase = ConversationPhase.REVIEW

        self.messages.append({"role": "assistant", "content": reply})
        return reply

    @staticmethod
    def _extract_blueprint(text: str) -> AgentBlueprint | None:
        """Extract a blueprint JSON from ```blueprint ... ``` fences."""
        import re
        match = re.search(r"```blueprint\s*\n?(.*?)```", text, re.DOTALL)
        if match is None:
            match = re.search(r"```json\s*\n?(.*?)```", text, re.DOTALL)
        if match is None:
            return None
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
            return AgentBlueprint.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize conversation state."""
        return {
            "phase": self.phase,
            "turn_count": self.turn_count,
            "messages": self.messages,
            "blueprint": self.blueprint.to_dict() if self.blueprint else None,
            "metadata": self.metadata,
        }
