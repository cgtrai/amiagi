"""Tests for TeamComposer LLM path and smart_recommend."""

from __future__ import annotations

import json

from amiagi.application.team_composer import TeamComposer, CompositionAdvice


class FakePlanner:
    """Simulates an LLM planner returning team composition JSON."""

    def __init__(self, response: str = "") -> None:
        self._response = response

    def chat(self, messages: list[dict], system_prompt: str = "", **kw) -> str:
        return self._response


def test_recommend_heuristic_fallback() -> None:
    composer = TeamComposer()
    advice = composer.recommend("Backend API z bazą danych i testami")
    assert isinstance(advice, CompositionAdvice)
    assert "backend_developer" in advice.recommended_roles
    assert advice.team_size > 0


def test_recommend_with_llm_no_planner_falls_back() -> None:
    composer = TeamComposer(planner_client=None)
    advice = composer.recommend_with_llm("Build a web application")
    assert isinstance(advice, CompositionAdvice)
    assert advice.team_size > 0


def test_recommend_with_llm_valid_response() -> None:
    response = json.dumps({
        "roles": ["backend_developer", "frontend_developer", "tester"],
        "reasoning": "Full stack team needed",
        "confidence": 0.85,
    })
    planner = FakePlanner(response)
    composer = TeamComposer(planner_client=planner)
    advice = composer.recommend_with_llm("Build full stack web app")
    assert "backend_developer" in advice.recommended_roles
    assert "frontend_developer" in advice.recommended_roles
    assert advice.confidence == 0.85
    assert advice.metadata.get("source") == "llm"


def test_recommend_with_llm_json_in_fences() -> None:
    response = '```json\n{"roles": ["researcher", "data_analyst"], "reasoning": "Research project", "confidence": 0.7}\n```'
    planner = FakePlanner(response)
    composer = TeamComposer(planner_client=planner)
    advice = composer.recommend_with_llm("Research project about AI")
    assert "researcher" in advice.recommended_roles


def test_recommend_with_llm_invalid_json_falls_back() -> None:
    planner = FakePlanner("This is not JSON at all")
    composer = TeamComposer(planner_client=planner)
    advice = composer.recommend_with_llm("Build API backend and tests")
    # Should fall back to heuristic
    assert isinstance(advice, CompositionAdvice)
    assert advice.team_size > 0


def test_recommend_with_llm_empty_roles_falls_back() -> None:
    response = json.dumps({"roles": [], "reasoning": "empty", "confidence": 0.0})
    planner = FakePlanner(response)
    composer = TeamComposer(planner_client=planner)
    advice = composer.recommend_with_llm("Backend API")
    # Empty roles → falls back to heuristic
    assert advice.team_size > 0


def test_smart_recommend_delegates_to_llm() -> None:
    response = json.dumps({
        "roles": ["architect", "backend_developer"],
        "reasoning": "Complex system",
        "confidence": 0.9,
    })
    planner = FakePlanner(response)
    composer = TeamComposer(planner_client=planner)
    advice = composer.smart_recommend("Complex distributed system")
    assert "architect" in advice.recommended_roles


def test_smart_recommend_without_planner() -> None:
    composer = TeamComposer()
    advice = composer.smart_recommend("Frontend UI with React")
    assert isinstance(advice, CompositionAdvice)
    assert "frontend_developer" in advice.recommended_roles


def test_recommend_with_llm_exception_in_planner() -> None:
    class BrokenPlanner:
        def chat(self, **kw):
            raise RuntimeError("LLM unavailable")

    composer = TeamComposer(planner_client=BrokenPlanner())
    advice = composer.recommend_with_llm("Test project")
    # Should catch exception and fall back
    assert isinstance(advice, CompositionAdvice)


def test_history_tracks_llm_recommendations() -> None:
    response = json.dumps({
        "roles": ["tester"],
        "reasoning": "QA",
        "confidence": 0.6,
    })
    planner = FakePlanner(response)
    composer = TeamComposer(planner_client=planner)
    composer.recommend_with_llm("QA automation")
    assert len(composer.history()) == 1
