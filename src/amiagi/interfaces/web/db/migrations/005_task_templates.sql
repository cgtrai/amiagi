-- ================================================================
-- Migration 005: Task Templates
-- Faza 14 — Task templates & localization
-- ================================================================

SET search_path TO dbo;

CREATE TABLE IF NOT EXISTS dbo.task_templates (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    yaml_content TEXT NOT NULL,
    tags        TEXT[] DEFAULT '{}',
    author_id   UUID,
    is_public   BOOLEAN DEFAULT false,
    use_count   INT DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_templates_public ON dbo.task_templates(is_public) WHERE is_public = true;
CREATE INDEX IF NOT EXISTS idx_templates_tags ON dbo.task_templates USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_templates_author ON dbo.task_templates(author_id);

-- ── Builtin templates (≥ 4) ─────────────────────────────────

INSERT INTO dbo.task_templates (name, description, yaml_content, tags, is_public, use_count)
VALUES
(
    'Code Review Pipeline',
    'Przegląd kodu z security audit',
    E'name: "Code Review Pipeline"\ndescription: "Przegląd kodu z security audit"\nsteps:\n  - agent: executor\n    prompt: "Review {target_file} for code quality"\n  - agent: executor\n    prompt: "Run security audit on {target_file}"\n  - agent: supervisor\n    prompt: "Summarize review findings"\nparameters:\n  - name: target_file\n    type: string\n    description: "Ścieżka do pliku"',
    ARRAY['review', 'security', 'code'],
    true, 0
),
(
    'Documentation Sprint',
    'Generowanie dokumentacji dla modułu',
    E'name: "Documentation Sprint"\ndescription: "Generowanie dokumentacji"\nsteps:\n  - agent: executor\n    prompt: "Analyze {module_path} and list public API"\n  - agent: executor\n    prompt: "Generate docstrings for {module_path}"\n  - agent: supervisor\n    prompt: "Compile documentation in Markdown format"\nparameters:\n  - name: module_path\n    type: string\n    description: "Ścieżka do modułu"',
    ARRAY['docs', 'documentation'],
    true, 0
),
(
    'Bug Investigation',
    'Systematyczne badanie błędu',
    E'name: "Bug Investigation"\ndescription: "Systematyczne badanie błędu"\nsteps:\n  - agent: executor\n    prompt: "Reproduce the bug described: {bug_description}"\n  - agent: executor\n    prompt: "Identify root cause of the bug"\n  - agent: executor\n    prompt: "Propose a fix with test"\n  - agent: supervisor\n    prompt: "Review the fix and assess risk"\nparameters:\n  - name: bug_description\n    type: string\n    description: "Opis błędu"',
    ARRAY['bug', 'debug', 'investigation'],
    true, 0
),
(
    'Refactoring Plan',
    'Planowanie i wykonanie refactoringu',
    E'name: "Refactoring Plan"\ndescription: "Planowanie i wykonanie refactoringu"\nsteps:\n  - agent: executor\n    prompt: "Analyze {target_module} for code smells"\n  - agent: executor\n    prompt: "Create refactoring plan for {target_module}"\n  - agent: executor\n    prompt: "Implement refactoring step by step"\n  - agent: supervisor\n    prompt: "Verify refactoring preserves behavior"\nparameters:\n  - name: target_module\n    type: string\n    description: "Moduł do refactoringu"',
    ARRAY['refactor', 'cleanup', 'improvement'],
    true, 0
)
ON CONFLICT DO NOTHING;
