"""ToolRecommender — suggests tools from the built-in + registered set."""

from __future__ import annotations

from amiagi.application.model_client_protocol import ChatCompletionClient

# Built-in tool names (from textual_cli._SUPPORTED_TEXTUAL_TOOLS)
BUILTIN_TOOLS: frozenset[str] = frozenset({
    "read_file", "list_dir", "run_shell", "run_python",
    "check_python_syntax", "fetch_web", "search_web", "download_file",
    "convert_pdf_to_markdown", "capture_camera_frame",
    "record_microphone_clip", "check_capabilities",
    "write_file", "append_file",
})

_RECOMMEND_PROMPT = """\
Given the agent's function and capabilities, recommend which tools
should be enabled. Available tools: {available}

Agent function: {team_function}
Capabilities: {capabilities}

Return a JSON object:
{{
  "recommended_tools": ["tool_1", "tool_2"],
  "reasoning": "brief explanation"
}}
"""


class ToolRecommender:
    """Recommends tools for a new agent based on its function."""

    def __init__(
        self,
        registered_tools: set[str] | None = None,
        client: ChatCompletionClient | None = None,
    ) -> None:
        self._registered_tools = registered_tools or set()
        self._client = client

    @property
    def all_available(self) -> set[str]:
        return set(BUILTIN_TOOLS) | self._registered_tools

    def recommend(
        self,
        *,
        team_function: str,
        capabilities: str = "",
    ) -> dict[str, list[str] | str]:
        """Return ``{"recommended_tools": [...], "reasoning": "..."}``."""
        if self._client is not None:
            return self._recommend_with_llm(
                team_function=team_function,
                capabilities=capabilities,
            )
        return self._recommend_by_keyword(
            team_function=team_function,
            capabilities=capabilities,
        )

    # ---- internals ----

    def _recommend_by_keyword(
        self,
        *,
        team_function: str,
        capabilities: str,
    ) -> dict[str, list | str]:
        """Heuristic tool matching."""
        query = (team_function + " " + capabilities).lower()
        recommended: list[str] = []

        # Always include read_file and list_dir
        recommended.extend(["read_file", "list_dir"])

        if any(w in query for w in ("code", "python", "program", "develop", "backend")):
            recommended.extend(["run_python", "check_python_syntax", "write_file"])
        if any(w in query for w in ("shell", "command", "deploy", "devops", "system")):
            recommended.append("run_shell")
        if any(w in query for w in ("web", "research", "search", "browse", "fetch")):
            recommended.extend(["fetch_web", "search_web", "download_file"])
        if any(w in query for w in ("review", "write", "edit", "file")):
            recommended.extend(["write_file", "append_file"])
        if "pdf" in query:
            recommended.append("convert_pdf_to_markdown")

        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for t in recommended:
            if t not in seen and t in self.all_available:
                seen.add(t)
                unique.append(t)

        return {"recommended_tools": unique, "reasoning": "keyword heuristic"}

    def _recommend_with_llm(
        self,
        *,
        team_function: str,
        capabilities: str,
    ) -> dict[str, list[str] | str]:
        assert self._client is not None
        import json as _json

        prompt = _RECOMMEND_PROMPT.format(
            team_function=team_function,
            capabilities=capabilities or "(general)",
            available=", ".join(sorted(self.all_available)),
        )
        try:
            raw = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a tool recommendation expert. Return ONLY valid JSON.",
            )
            text = raw.strip()
            if "```" in text:
                parts = text.split("```")
                for part in parts:
                    stripped = part.strip()
                    if stripped.startswith("json"):
                        stripped = stripped[4:].strip()
                    if stripped.startswith("{"):
                        text = stripped
                        break
            data = _json.loads(text)
            tools = [t for t in data.get("recommended_tools", []) if t in self.all_available]
            return {
                "recommended_tools": tools,
                "reasoning": data.get("reasoning", ""),
            }
        except Exception:
            return self._recommend_by_keyword(
                team_function=team_function,
                capabilities=capabilities,
            )
