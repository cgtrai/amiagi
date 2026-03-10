from __future__ import annotations

from amiagi.application.task_dossier_builder import TaskDossierBuilder


class _RuntimeProvider:
    def recommend(self, agent_role: str, prompt: str, available_tools):
        assert agent_role == "polluks"
        assert "raport" in prompt.lower()
        return [
            {
                "name": "web-research",
                "display_name": "Web Research",
                "compatible_tools": ["search_web", "fetch_web"],
                "compatible_roles": ["polluks"],
                "priority": 70,
                "match_reason": "keyword",
                "source": "db",
            },
            {
                "name": "xlsx-export-local",
                "display_name": "XLSX Export Local",
                "compatible_tools": ["run_python"],
                "compatible_roles": ["polluks"],
                "priority": 55,
                "match_reason": "role",
                "source": "file",
            },
        ]


def test_task_dossier_builder_returns_read_only_executor_guidance() -> None:
    builder = TaskDossierBuilder(runtime_skill_provider=_RuntimeProvider())

    dossier = builder.build(
        sponsor_task="Przygotuj raport cenowy ofert z internetu w xlsx.",
        current_prompt="Zaplanuj etapy i przygotuj raport.",
    )

    assert dossier["task_class"] == "price_report"
    assert dossier["recommended_executor_skills"] == ["web-research", "xlsx-export-local"]
    assert sorted(dossier["required_tools"]) == ["fetch_web", "run_python", "search_web"]
    assert dossier["environment_gaps"] == []