-- Migration 005 (SQLite): Task Templates

CREATE TABLE IF NOT EXISTS task_templates (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    yaml_content TEXT NOT NULL,
    tags        TEXT DEFAULT '[]',           -- JSON array
    author_id   TEXT,
    is_public   INTEGER DEFAULT 0,
    use_count   INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_templates_public ON task_templates(is_public) WHERE is_public = 1;
CREATE INDEX IF NOT EXISTS idx_templates_author ON task_templates(author_id);

-- ── Builtin templates ───────────────────────────────────────

INSERT INTO task_templates (name, description, yaml_content, tags, is_public, use_count)
VALUES
(
    'Code Review Pipeline',
    'Przegląd kodu z security audit',
    'name: "Code Review Pipeline"
description: "Przegląd kodu z security audit"
steps:
  - agent: executor
    prompt: "Review {target_file} for code quality"
  - agent: executor
    prompt: "Run security audit on {target_file}"
  - agent: supervisor
    prompt: "Summarize review findings"
parameters:
  - name: target_file
    type: string
    description: "Ścieżka do pliku"',
    '["review", "security", "code"]',
    1, 0
),
(
    'Documentation Sprint',
    'Generowanie dokumentacji dla modułu',
    'name: "Documentation Sprint"
description: "Generowanie dokumentacji"
steps:
  - agent: executor
    prompt: "Analyze {module_path} and list public API"
  - agent: executor
    prompt: "Generate docstrings for {module_path}"
  - agent: supervisor
    prompt: "Compile documentation in Markdown format"
parameters:
  - name: module_path
    type: string
    description: "Ścieżka do modułu"',
    '["docs", "documentation"]',
    1, 0
),
(
    'Bug Investigation',
    'Systematyczne badanie błędu',
    'name: "Bug Investigation"
description: "Systematyczne badanie błędu"
steps:
  - agent: executor
    prompt: "Reproduce the bug described: {bug_description}"
  - agent: executor
    prompt: "Identify root cause of the bug"
  - agent: executor
    prompt: "Propose a fix with test"
  - agent: supervisor
    prompt: "Review the fix and assess risk"
parameters:
  - name: bug_description
    type: string
    description: "Opis błędu"',
    '["bug", "debug", "investigation"]',
    1, 0
),
(
    'Refactoring Plan',
    'Planowanie i wykonanie refactoringu',
    'name: "Refactoring Plan"
description: "Planowanie i wykonanie refactoringu"
steps:
  - agent: executor
    prompt: "Analyze {target_module} for code smells"
  - agent: executor
    prompt: "Create refactoring plan for {target_module}"
  - agent: executor
    prompt: "Implement refactoring step by step"
  - agent: supervisor
    prompt: "Verify refactoring preserves behavior"
parameters:
  - name: target_module
    type: string
    description: "Moduł do refactoringu"',
    '["refactor", "cleanup", "improvement"]',
    1, 0
)
ON CONFLICT DO NOTHING;
