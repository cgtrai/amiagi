-- Migration 004 (SQLite): Performance, Notifications, Sessions, API Keys, Webhooks

-- ── Agent Performance ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_performance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_role  TEXT NOT NULL,
    model       TEXT,
    task_type   TEXT,
    duration_ms INTEGER,
    success     INTEGER DEFAULT 1,
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    cost_usd    REAL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_perf_agent ON agent_performance(agent_role);
CREATE INDEX IF NOT EXISTS idx_perf_created ON agent_performance(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_perf_model ON agent_performance(model);

-- ── Notifications ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id     TEXT NOT NULL,
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT DEFAULT '',
    is_read     INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at DESC);

CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id     TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT 'in_app',
    is_enabled  INTEGER DEFAULT 1,
    PRIMARY KEY (user_id, event_type, channel)
);

-- ── Session Events (for replay) ───────────────────────────────
CREATE TABLE IF NOT EXISTS session_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    agent_id    TEXT,
    payload     TEXT DEFAULT '{}',       -- JSON
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sess_events_session ON session_events(session_id, created_at);

-- ── API Keys ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    key_hash     TEXT NOT NULL,
    scopes       TEXT DEFAULT '[]',      -- JSON array
    expires_at   TEXT,
    is_active    INTEGER DEFAULT 1,
    last_used_at TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_apikey_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_apikey_hash ON api_keys(key_hash) WHERE is_active = 1;

-- ── Webhooks ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS webhooks (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id     TEXT NOT NULL,
    url         TEXT NOT NULL,
    events      TEXT DEFAULT '[]',       -- JSON array
    secret      TEXT,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_webhooks_user ON webhooks(user_id);
