-- ============================================================
-- Migration 010 (PostgreSQL): Sprint P3 - Workflow, Memory, Params, Knowledge
-- ============================================================

-- ── Eval Runs ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.eval_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
    metrics_json    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Eval Run Scenarios ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.eval_run_scenarios (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES dbo.eval_runs(id) ON DELETE CASCADE,
    scenario_name   VARCHAR(255) NOT NULL,
    passed          BOOLEAN NOT NULL,
    details_json    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── A/B Campaigns ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.ab_campaigns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
    variant_a_id    TEXT NOT NULL,
    variant_b_id    TEXT NOT NULL,
    results_json    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Knowledge Bases ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.knowledge_bases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL UNIQUE,
    description     TEXT,
    embedding_model VARCHAR(100),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Knowledge Sources ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.knowledge_sources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    base_id         UUID NOT NULL REFERENCES dbo.knowledge_bases(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
    indexed_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Shared Memory ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.shared_memory (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    key_findings    TEXT NOT NULL,
    tags            JSONB,
    metadata        JSONB,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shared_memory_agent ON dbo.shared_memory(agent_id);
CREATE INDEX IF NOT EXISTS idx_shared_memory_task ON dbo.shared_memory(task_id);
