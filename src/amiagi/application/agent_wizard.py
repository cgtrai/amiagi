"""AgentWizardService — orchestrates agent creation from natural-language need.

Flow:
1. Sponsor describes need (text)
2. Planner model generates AgentBlueprint (via structured conversation)
3. Sponsor reviews/edits
4. AgentFactory creates agent
5. AgentTestRunner validates
6. Blueprint persisted to YAML
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from amiagi.application.agent_factory import AgentFactory
from amiagi.application.agent_test_runner import AgentTestRunner, ValidationReport
from amiagi.application.model_client_protocol import ChatCompletionClient
from amiagi.application.persona_generator import PersonaGenerator
from amiagi.application.skill_recommender import SkillRecommender
from amiagi.application.tool_recommender import ToolRecommender
from amiagi.domain.agent import AgentDescriptor, AgentRole
from amiagi.domain.blueprint import AgentBlueprint, TestScenario
from amiagi.infrastructure.agent_runtime import AgentRuntime


class SensitivePermissionError(Exception):
    """Raised when a blueprint requests sensitive permissions without Sponsor confirmation.

    Attributes:
        blueprint: The blueprint that triggered the error.
        sensitive_perms: List of human-readable strings describing the sensitive accesses.
    """

    def __init__(self, blueprint: AgentBlueprint, sensitive_perms: list[str]) -> None:
        self.blueprint = blueprint
        self.sensitive_perms = sensitive_perms
        msg = (
            f"Blueprint '{blueprint.name}' requests sensitive permissions "
            f"that require Sponsor confirmation: {', '.join(sensitive_perms)}"
        )
        super().__init__(msg)


_BLUEPRINT_PROMPT = """\
You are an expert AI agent architect.  Based on the user's description of
what they need, generate a complete agent blueprint.

User need: {need}

Return a single JSON object (no markdown fences, no explanation):
{{
  "name": "agent name (short, lowercase, underscores)",
  "role": "executor",
  "team_function": "concise description of the agent's job",
  "persona_prompt": "3-5 sentence persona",
  "required_skills": ["skill_name_1"],
  "required_tools": ["tool_name_1"],
  "suggested_model": "",
  "suggested_backend": "ollama",
  "communication_style": "balanced",
  "test_scenarios": [
    {{
      "name": "basic_test",
      "prompt": "a test prompt for the agent",
      "expected_keywords": ["keyword1"],
      "expected_tool_calls": [],
      "max_turns": 1
    }}
  ]
}}
"""


@dataclass
class WizardState:
    """Tracks the multi-turn wizard conversation."""
    need: str = ""
    blueprint: AgentBlueprint | None = None
    runtime: AgentRuntime | None = None
    validation: ValidationReport | None = None
    phase: str = "idle"  # idle | gathering | review | confirmed | validated | error
    conversation: list[dict[str, str]] = field(default_factory=list)


class AgentWizardService:
    """Orchestrates the full agent creation wizard.

    Usage (programmatic)::

        wizard = AgentWizardService(planner_client=..., factory=..., ...)
        blueprint = wizard.generate_blueprint("I need a code reviewer for Python")
        # ... sponsor edits blueprint ...
        runtime = wizard.create_agent(blueprint)
        report = wizard.validate_agent(runtime, blueprint)
    """

    def __init__(
        self,
        *,
        planner_client: ChatCompletionClient | None = None,
        factory: AgentFactory,
        persona_generator: PersonaGenerator | None = None,
        skill_recommender: SkillRecommender | None = None,
        tool_recommender: ToolRecommender | None = None,
        test_runner: AgentTestRunner | None = None,
        blueprints_dir: Path = Path("./data/agents/blueprints"),
    ) -> None:
        self._planner = planner_client
        self._factory = factory
        self._persona_gen = persona_generator or PersonaGenerator(planner_client)
        self._skill_rec = skill_recommender
        self._tool_rec = tool_recommender
        self._test_runner = test_runner or AgentTestRunner()
        self._blueprints_dir = blueprints_dir
        self._blueprints_dir.mkdir(parents=True, exist_ok=True)

    # ---- step 0: analyze request ----

    def analyze_request(self, need: str) -> dict[str, Any]:
        """Pre-analyze a user need before generating a full blueprint.

        Returns a dict with ``complexity``, ``suggested_role``,
        ``suggested_tools``, and ``summary``.
        """
        words = need.lower().split()
        complexity = "simple" if len(words) < 20 else ("complex" if len(words) > 50 else "moderate")
        role = "executor"
        if any(w in need.lower() for w in ("review", "audit", "check")):
            role = "specialist"
        elif any(w in need.lower() for w in ("plan", "architect", "design", "supervise")):
            role = "supervisor"

        tools: list[str] = ["read_file", "list_dir"]
        if any(w in need.lower() for w in ("code", "python", "program", "script")):
            tools.extend(["run_python", "check_python_syntax", "write_file"])
        if any(w in need.lower() for w in ("web", "search", "research", "http")):
            tools.extend(["fetch_web", "search_web"])
        if any(w in need.lower() for w in ("shell", "command", "terminal", "deploy")):
            tools.append("run_shell")

        return {
            "complexity": complexity,
            "suggested_role": role,
            "suggested_tools": tools,
            "word_count": len(words),
            "summary": need[:200],
        }

    # ---- step 1: generate blueprint ----

    def generate_blueprint(self, need: str) -> AgentBlueprint:
        """Generate an :class:`AgentBlueprint` from a natural-language need.

        Uses the planner LLM when available; falls back to heuristic generation.
        """
        if self._planner is not None:
            return self._generate_with_llm(need)
        return self._generate_heuristic(need)

    # ---- step 2: create agent from blueprint ----

    @staticmethod
    def check_sensitive_permissions(blueprint: AgentBlueprint) -> list[str]:
        """Return human-readable list of sensitive permissions the blueprint requests.

        Empty list means no confirmation is needed.
        """
        sensitive: list[str] = []
        perms = blueprint.initial_permissions or {}
        if perms.get("shell_access"):
            sensitive.append("shell_access (uruchamianie poleceń systemowych)")
        if perms.get("network_access"):
            sensitive.append("network_access (dostęp do sieci)")
        # Broad path permissions
        allowed = perms.get("allowed_paths", [])
        if any(p in ("*", "/", "~") for p in allowed):
            sensitive.append(f"unrestricted_paths ({', '.join(allowed)})")
        return sensitive

    def create_agent(
        self,
        blueprint: AgentBlueprint,
        *,
        client: ChatCompletionClient | None = None,
        sponsor_confirmed: bool = False,
    ) -> AgentRuntime:
        """Instantiate an agent from *blueprint* and register it.

        If the blueprint requests sensitive permissions (shell_access,
        network_access, unrestricted paths) and *sponsor_confirmed* is
        ``False``, raises :class:`SensitivePermissionError` so the caller
        (e.g. TUI) can present a confirmation dialog.
        """
        sensitive = self.check_sensitive_permissions(blueprint)
        if sensitive and not sponsor_confirmed:
            raise SensitivePermissionError(blueprint, sensitive)
        role_map = {
            "executor": AgentRole.EXECUTOR,
            "supervisor": AgentRole.SUPERVISOR,
            "specialist": AgentRole.SPECIALIST,
        }
        descriptor = AgentDescriptor(
            agent_id=AgentFactory.generate_id(),
            name=blueprint.name,
            role=role_map.get(blueprint.role, AgentRole.EXECUTOR),
            persona_prompt=blueprint.persona_prompt,
            model_backend=blueprint.suggested_backend,
            model_name=blueprint.suggested_model,
            skills=list(blueprint.required_skills),
            tools=list(blueprint.required_tools),
            metadata={
                "origin": "wizard",
                "team_function": blueprint.team_function,
                "initial_permissions": blueprint.initial_permissions,
            },
        )
        runtime = self._factory.create_agent(descriptor, client=client)

        # Apply initial_permissions to PermissionEnforcer if available
        if blueprint.initial_permissions and hasattr(self._factory, '_permission_enforcer'):
            enforcer = getattr(self._factory, '_permission_enforcer', None)
            if enforcer is not None:
                from amiagi.domain.permission_policy import AgentPermissionPolicy
                perms = blueprint.initial_permissions
                policy = AgentPermissionPolicy(
                    allowed_tools=perms.get("allowed_tools", []),
                    denied_tools=perms.get("denied_tools", []),
                    allowed_paths=perms.get("allowed_paths", []),
                    read_only_paths=perms.get("read_only_paths", []),
                    network_access=perms.get("network_access", False),
                    shell_access=perms.get("shell_access", False),
                    max_file_size_bytes=perms.get("max_file_size_bytes", 10 * 1024 * 1024),
                )
                enforcer.set_policy(descriptor.agent_id, policy)

        return runtime

    # ---- step 3: validate ----

    def validate_agent(
        self,
        runtime: AgentRuntime,
        blueprint: AgentBlueprint,
    ) -> ValidationReport:
        """Run test scenarios from *blueprint* against *runtime*."""
        return self._test_runner.validate(runtime, blueprint.test_scenarios)

    # ---- persistence ----

    def save_blueprint(self, blueprint: AgentBlueprint) -> Path:
        """Persist *blueprint* as YAML to the blueprints directory."""
        path = self._blueprints_dir / f"{blueprint.name}.yaml"
        path.write_text(
            yaml.dump(blueprint.to_dict(), default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return path

    def load_blueprint(self, name: str) -> AgentBlueprint | None:
        """Load a previously-saved blueprint by name (YAML preferred, JSON fallback)."""
        yaml_path = self._blueprints_dir / f"{name}.yaml"
        json_path = self._blueprints_dir / f"{name}.json"
        if yaml_path.exists():
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        elif json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
        else:
            return None
        return AgentBlueprint.from_dict(data)

    def list_blueprints(self) -> list[str]:
        """Return names of all saved blueprints."""
        names: set[str] = set()
        for ext in ("*.yaml", "*.json"):
            for p in self._blueprints_dir.glob(ext):
                names.add(p.stem)
        return sorted(names)

    # ---- internals ----

    def _generate_with_llm(self, need: str) -> AgentBlueprint:
        assert self._planner is not None
        prompt = _BLUEPRINT_PROMPT.format(need=need)
        raw = self._planner.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are an agent architect. Return ONLY valid JSON.",
        )
        data = self._parse_json(raw)
        if data is None:
            return self._generate_heuristic(need)

        blueprint = AgentBlueprint.from_dict(data)

        # Enrich with skill & tool recommendations
        if self._skill_rec is not None:
            rec = self._skill_rec.recommend(
                team_function=blueprint.team_function,
                capabilities=need,
                role=blueprint.role,
            )
            for skill in rec.get("existing", []):
                if skill not in blueprint.required_skills:
                    blueprint.required_skills.append(skill)

        if self._tool_rec is not None:
            rec = self._tool_rec.recommend(
                team_function=blueprint.team_function,
                capabilities=need,
            )
            for tool in rec.get("recommended_tools", []):
                if tool not in blueprint.required_tools:
                    blueprint.required_tools.append(tool)

        # Enrich persona if thin
        if len(blueprint.persona_prompt) < 50:
            blueprint.persona_prompt = self._persona_gen.generate(
                name=blueprint.name,
                role=blueprint.role,
                team_function=blueprint.team_function,
                communication_style=blueprint.communication_style,
                use_llm=True,
            )

        return blueprint

    def _generate_heuristic(self, need: str) -> AgentBlueprint:
        """Template-based blueprint generation when no LLM is available."""
        words = need.lower().split()
        name = "_".join(words[:3]) if words else "new_agent"
        name = re.sub(r"[^a-z0-9_]", "", name)

        team_function = need[:120]
        persona = self._persona_gen.generate(
            name=name,
            role="executor",
            team_function=team_function,
        )

        tools: list[str] = ["read_file", "list_dir"]
        if any(w in need.lower() for w in ("code", "python", "program")):
            tools.extend(["run_python", "check_python_syntax", "write_file"])
        if any(w in need.lower() for w in ("web", "search", "research")):
            tools.extend(["fetch_web", "search_web"])

        return AgentBlueprint(
            name=name,
            role="executor",
            team_function=team_function,
            persona_prompt=persona,
            required_tools=tools,
            suggested_backend="ollama",
            communication_style="balanced",
            test_scenarios=[
                TestScenario(
                    name="basic_response",
                    prompt=f"Can you briefly describe your capabilities as a {team_function}?",
                    expected_keywords=[],
                    max_turns=1,
                ),
            ],
        )

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        """Extract a JSON object from possibly fenced LLM output."""
        cleaned = text.strip()
        if "```" in cleaned:
            parts = cleaned.split("```")
            for part in parts:
                stripped = part.strip()
                if stripped.startswith("json"):
                    stripped = stripped[4:].strip()
                if stripped.startswith("{"):
                    cleaned = stripped
                    break

        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            # Try to find the first { ... } block
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except (json.JSONDecodeError, ValueError):
                    pass
        return None
