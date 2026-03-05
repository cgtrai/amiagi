-- Migration 002: Skills, Agent Traits, and Skill Assignment tables.
-- Part of amiagi 1.1.0 — Faza 10 (DB-driven Skill & Trait Management).

SET search_path TO dbo, public;

-- ── Skills ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.skills (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) UNIQUE NOT NULL,
    display_name    VARCHAR(255) NOT NULL,
    category        VARCHAR(50) NOT NULL DEFAULT 'general',
    description     TEXT,
    content         TEXT NOT NULL,
    trigger_keywords TEXT[] NOT NULL DEFAULT '{}',
    compatible_tools TEXT[] NOT NULL DEFAULT '{}',
    compatible_roles TEXT[] NOT NULL DEFAULT '{}',
    token_cost      INTEGER NOT NULL DEFAULT 0,
    priority        INTEGER NOT NULL DEFAULT 50,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skills_category ON dbo.skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_keywords ON dbo.skills USING GIN(trigger_keywords);
CREATE INDEX IF NOT EXISTS idx_skills_roles ON dbo.skills USING GIN(compatible_roles);

-- ── Agent Traits ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.agent_traits (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trait_type      VARCHAR(20) NOT NULL CHECK (trait_type IN ('persona', 'knowledge', 'protocol', 'skill_override')),
    agent_role      VARCHAR(50) NOT NULL,
    name            VARCHAR(100) NOT NULL,
    content         TEXT NOT NULL,
    token_cost      INTEGER NOT NULL DEFAULT 0,
    priority        INTEGER NOT NULL DEFAULT 50,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(trait_type, agent_role, name)
);

CREATE INDEX IF NOT EXISTS idx_traits_role ON dbo.agent_traits(agent_role, trait_type);

-- ── Agent ↔ Skill Assignments ────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.agent_skill_assignments (
    agent_role      VARCHAR(50) NOT NULL,
    skill_id        UUID REFERENCES dbo.skills(id) ON DELETE CASCADE,
    is_pinned       BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (agent_role, skill_id)
);

-- ── Skill Usage Log ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.skill_usage_log (
    id              BIGSERIAL PRIMARY KEY,
    skill_id        UUID REFERENCES dbo.skills(id),
    agent_role      VARCHAR(50) NOT NULL,
    task_summary    TEXT,
    was_useful      BOOLEAN,
    tokens_used     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skill_usage_date ON dbo.skill_usage_log(created_at DESC);
