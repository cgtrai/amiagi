-- ================================================================
-- Migration 004: Performance, Notifications, Sessions, API Keys, Webhooks
-- Faza 13 — Monitoring, analysis & integrations
-- ================================================================

SET search_path TO dbo;

-- ── Agent Performance ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dbo.agent_performance (
    id          BIGSERIAL PRIMARY KEY,
    agent_role  VARCHAR(100) NOT NULL,
    model       VARCHAR(200),
    task_type   VARCHAR(100),
    duration_ms INT,
    success     BOOLEAN DEFAULT true,
    tokens_in   INT DEFAULT 0,
    tokens_out  INT DEFAULT 0,
    cost_usd    NUMERIC(10,6) DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_perf_agent ON dbo.agent_performance(agent_role);
CREATE INDEX IF NOT EXISTS idx_perf_created ON dbo.agent_performance(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_perf_model ON dbo.agent_performance(model);

-- ── Notifications ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dbo.notifications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL,
    type        VARCHAR(50) NOT NULL,   -- 'task.done', 'agent.error', 'budget.exceeded'
    title       VARCHAR(255) NOT NULL,
    body        TEXT DEFAULT '',
    is_read     BOOLEAN DEFAULT false,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notif_user ON dbo.notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_notif_created ON dbo.notifications(created_at DESC);

CREATE TABLE IF NOT EXISTS dbo.notification_preferences (
    user_id     UUID NOT NULL,
    event_type  VARCHAR(50) NOT NULL,
    channel     VARCHAR(20) NOT NULL DEFAULT 'in_app',  -- 'in_app', 'web_push', 'webhook'
    is_enabled  BOOLEAN DEFAULT true,
    PRIMARY KEY (user_id, event_type, channel)
);

-- ── Session Events (for replay) ───────────────────────────────

CREATE TABLE IF NOT EXISTS dbo.session_events (
    id          BIGSERIAL PRIMARY KEY,
    session_id  VARCHAR(255) NOT NULL,
    event_type  VARCHAR(100) NOT NULL,
    agent_id    VARCHAR(100),
    payload     JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sess_events_session ON dbo.session_events(session_id, created_at);

-- ── API Keys ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dbo.api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL,
    name        VARCHAR(255) NOT NULL,
    key_hash    VARCHAR(128) NOT NULL,    -- SHA-256
    scopes      TEXT[] DEFAULT '{}',
    expires_at  TIMESTAMPTZ,
    is_active   BOOLEAN DEFAULT true,
    last_used_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_apikey_user ON dbo.api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_apikey_hash ON dbo.api_keys(key_hash) WHERE is_active = true;

-- ── Webhooks ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dbo.webhooks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL,
    url         VARCHAR(2048) NOT NULL,
    events      TEXT[] DEFAULT '{}',
    secret      VARCHAR(255),
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhooks_user ON dbo.webhooks(user_id);
