-- ============================================================
-- Migration 010 (SQLite): Sprint P3 - Workflow, Memory, Params, Knowledge
-- ============================================================

CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    metrics_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS eval_run_scenarios (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    scenario_name TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    details_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(run_id) REFERENCES eval_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ab_campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    variant_a_id TEXT NOT NULL,
    variant_b_id TEXT NOT NULL,
    results_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    embedding_model TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_sources (
    id TEXT PRIMARY KEY,
    base_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    indexed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shared_memory (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    key_findings TEXT NOT NULL,
    tags TEXT,
    metadata TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_shared_memory_agent ON shared_memory(agent_id);
CREATE INDEX IF NOT EXISTS idx_shared_memory_task ON shared_memory(task_id);
