from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _norm(text: str) -> str:
    return str(text or "").strip().lower()


@dataclass
class TaskDossierBuilder:
    runtime_skill_provider: Any

    def classify(self, text: str) -> str:
        normalized = _norm(text)
        if any(token in normalized for token in ("cena", "ofert", "price", "xlsx", "excel", "raport cen")):
            return "price_report"
        if any(token in normalized for token in ("internet", "www", "web", "strona", "research", "wyszuk")):
            return "web_research"
        if any(token in normalized for token in ("pdf", "dokument", "markdown", "konwers", "extract")):
            return "document_processing"
        if any(token in normalized for token in ("python", "kod", "repo", "test", "refactor", "bug", "program")):
            return "coding"
        if any(token in normalized for token in ("analiza", "analysis", "porówn", "compare", "zestaw")):
            return "analysis"
        return "general"

    def build(self, *, sponsor_task: str, current_prompt: str) -> dict[str, Any]:
        merged = "\n\n".join(part for part in (sponsor_task, current_prompt) if str(part or "").strip())
        task_class = self.classify(merged)
        recommendations = self.runtime_skill_provider.recommend(
            "polluks",
            merged,
            None,
        )
        required_tools = sorted({tool for item in recommendations for tool in item.get("compatible_tools", [])})
        environment_gaps: list[str] = []

        recommendation_names = [str(item.get("name", "")) for item in recommendations if str(item.get("name", ""))]
        normalized_names = [name.lower() for name in recommendation_names]
        normalized_text = _norm(merged)

        if task_class == "price_report" and not any(token in normalized_text for token in ("csv", "xlsx", "excel")):
            pass
        elif task_class == "price_report" and not any("xlsx" in name or "spreadsheet" in name or "excel" in name for name in normalized_names):
            environment_gaps.append("xlsx_export_capability_unverified")

        if task_class == "web_research" and "search_web" not in required_tools and "fetch_web" not in required_tools:
            environment_gaps.append("web_research_toolchain_not_explicitly_covered")

        return {
            "task_class": task_class,
            "recommended_executor_skills": recommendation_names,
            "required_tools": required_tools,
            "environment_gaps": environment_gaps,
        }