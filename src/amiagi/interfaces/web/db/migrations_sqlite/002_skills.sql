-- Migration 002 (SQLite): Skills, Agent Traits, Skill Assignment tables.

-- ── Skills ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skills (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name            TEXT UNIQUE NOT NULL,
    display_name    TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'general',
    description     TEXT,
    content         TEXT NOT NULL,
    trigger_keywords TEXT NOT NULL DEFAULT '[]',   -- JSON array
    compatible_tools TEXT NOT NULL DEFAULT '[]',   -- JSON array
    compatible_roles TEXT NOT NULL DEFAULT '[]',   -- JSON array
    token_cost      INTEGER NOT NULL DEFAULT 0,
    priority        INTEGER NOT NULL DEFAULT 50,
    is_active       INTEGER NOT NULL DEFAULT 1,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category);

-- ── Agent Traits ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_traits (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    trait_type      TEXT NOT NULL CHECK (trait_type IN ('persona', 'knowledge', 'protocol', 'skill_override')),
    agent_role      TEXT NOT NULL,
    name            TEXT NOT NULL,
    content         TEXT NOT NULL,
    token_cost      INTEGER NOT NULL DEFAULT 0,
    priority        INTEGER NOT NULL DEFAULT 50,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(trait_type, agent_role, name)
);

CREATE INDEX IF NOT EXISTS idx_traits_role ON agent_traits(agent_role, trait_type);

-- ── Agent ↔ Skill Assignments ────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_skill_assignments (
    agent_role      TEXT NOT NULL,
    skill_id        TEXT REFERENCES skills(id) ON DELETE CASCADE,
    is_pinned       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_role, skill_id)
);

-- ── Skill Usage Log ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skill_usage_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id        TEXT REFERENCES skills(id),
    agent_role      TEXT NOT NULL,
    task_summary    TEXT,
    was_useful      INTEGER,
    tokens_used     INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_skill_usage_date ON skill_usage_log(created_at DESC);
