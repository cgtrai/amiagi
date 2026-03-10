from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amiagi.interfaces.web.skills.runtime_skill_provider import RuntimeSkillProvider


@pytest.mark.asyncio
async def test_runtime_skill_provider_selects_traits_and_skills_by_role_and_prompt() -> None:
    repo = SimpleNamespace(
        list_skills=AsyncMock(
            return_value=[
                SimpleNamespace(
                    id="skill-plan",
                    name="planning-discipline",
                    display_name="Planning Discipline",
                    content="Create a staged plan with checklist and completion criteria.",
                    trigger_keywords=["plan", "etap", "checklista"],
                    compatible_tools=[],
                    compatible_roles=["polluks"],
                    token_cost=120,
                    priority=80,
                ),
                SimpleNamespace(
                    id="skill-web",
                    name="web-research",
                    display_name="Web Research",
                    content="Search the web, compare sources, and keep evidence.",
                    trigger_keywords=["internet", "oferty", "raport"],
                    compatible_tools=["search_web"],
                    compatible_roles=["polluks"],
                    token_cost=120,
                    priority=70,
                ),
            ]
        ),
        list_traits=AsyncMock(
            return_value=[
                SimpleNamespace(
                    id="trait-kastor",
                    agent_role="kastor",
                    trait_type="protocol",
                    name="stage-supervision",
                    content="Approve the plan, then dispatch exactly one next unfinished stage.",
                    token_cost=90,
                    priority=100,
                ),
                SimpleNamespace(
                    id="trait-polluks",
                    agent_role="polluks",
                    trait_type="protocol",
                    name="execution-discipline",
                    content="Verify previous stage, execute current one, save results, report back.",
                    token_cost=90,
                    priority=100,
                ),
            ]
        ),
        get_pinned_skills=AsyncMock(side_effect=lambda role: ["skill-plan"] if role == "polluks" else []),
    )

    provider = RuntimeSkillProvider(token_budget=500, trait_budget=150)
    await provider.refresh(repo)

    polluks_selected = provider.select("polluks", "Przygotuj plan etapów raportu ofert z internetu", ["search_web"])
    kastor_selected = provider.select("kastor", "Sprawdź etap planowania i zatwierdź następny krok", None)

    assert any(item["name"] == "trait:execution-discipline" for item in polluks_selected)
    assert any(item["name"] == "skill:planning-discipline" for item in polluks_selected)
    assert any(item["name"] == "skill:web-research" for item in polluks_selected)
    assert any(item["name"] == "trait:stage-supervision" for item in kastor_selected)
    assert not any(item["name"] == "skill:web-research" for item in kastor_selected)


@pytest.mark.asyncio
async def test_runtime_skill_provider_refresh_updates_snapshot() -> None:
    repo = SimpleNamespace(
        list_skills=AsyncMock(return_value=[]),
        list_traits=AsyncMock(return_value=[]),
        get_pinned_skills=AsyncMock(return_value=[]),
    )

    provider = RuntimeSkillProvider()
    await provider.refresh(repo)

    assert provider.refreshed_at


@pytest.mark.asyncio
async def test_runtime_skill_provider_merges_db_and_project_file_skills() -> None:
    db_repo = SimpleNamespace(
        list_skills=AsyncMock(
            return_value=[
                SimpleNamespace(
                    id="db-1",
                    name="global-web-research",
                    display_name="Global Web Research",
                    content="Global research skill",
                    trigger_keywords=["internet"],
                    compatible_tools=["search_web"],
                    compatible_roles=["polluks"],
                    token_cost=80,
                    priority=70,
                )
            ]
        ),
        list_traits=AsyncMock(return_value=[]),
        get_pinned_skills=AsyncMock(return_value=[]),
    )
    project_repo = SimpleNamespace(
        list_skills=lambda role=None: [
            SimpleNamespace(
                role="polluks",
                name="local-xlsx-export",
                display_name="Local XLSX Export",
                content="Use local spreadsheet flow",
                trigger_keywords=["xlsx", "raport"],
                compatible_tools=["run_python"],
                compatible_roles=["polluks"],
                priority=60,
            )
        ]
    )

    provider = RuntimeSkillProvider(token_budget=500, trait_budget=100)
    await provider.refresh(db_repo, project_repo)

    selected = provider.select("polluks", "Przygotuj raport z internetu do xlsx", None)
    recommended = provider.recommend("polluks", "Przygotuj raport z internetu do xlsx", None)

    assert any(item["name"] == "skill:global-web-research" and item["source"] == "db" for item in selected)
    assert any(item["name"] == "skill:local-xlsx-export" and item["source"] == "file" for item in selected)
    assert any(item["name"] == "global-web-research" and item["source"] == "db" for item in recommended)
    assert any(item["name"] == "local-xlsx-export" and item["source"] == "file" for item in recommended)