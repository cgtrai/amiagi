-- ============================================================
-- Migration 007 (PostgreSQL): Admin bootstrap & login tracking
-- ============================================================

-- ── Admin setup tokens (CLI → Web first-run flow) ────────────
CREATE TABLE IF NOT EXISTS dbo.admin_setup_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT NOT NULL,
    token_hash  TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    is_blocked  BOOLEAN NOT NULL DEFAULT FALSE,
    is_used     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_setup_tokens_email
    ON dbo.admin_setup_tokens(email, is_used, is_blocked);

-- ── Login attempt tracking ───────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.login_attempts (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT NOT NULL,
    ip_address  TEXT,
    success     BOOLEAN NOT NULL DEFAULT FALSE,
    reason      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_email
    ON dbo.login_attempts(email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip
    ON dbo.login_attempts(ip_address, created_at DESC);
