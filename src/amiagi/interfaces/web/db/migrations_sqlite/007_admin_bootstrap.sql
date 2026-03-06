-- ============================================================
-- Migration 007 (SQLite): Admin bootstrap & login tracking
-- ============================================================

-- ── Admin setup tokens (CLI → Web first-run flow) ────────────
CREATE TABLE IF NOT EXISTS admin_setup_tokens (
    id           TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    email        TEXT NOT NULL,
    token_hash   TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    is_blocked   INTEGER NOT NULL DEFAULT 0,
    is_used      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_setup_tokens_email
    ON admin_setup_tokens(email, is_used, is_blocked);

-- ── Login attempt tracking ───────────────────────────────────
CREATE TABLE IF NOT EXISTS login_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    ip_address  TEXT,
    success     INTEGER NOT NULL DEFAULT 0,
    reason      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_email
    ON login_attempts(email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip
    ON login_attempts(ip_address, created_at DESC);
