-- ============================================================
-- Migration 001: Initial schema for amiagi Web GUI
-- Schema: dbo
-- ============================================================

CREATE SCHEMA IF NOT EXISTS dbo;
SET search_path TO dbo, public;

-- ── Users ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         VARCHAR(255) UNIQUE NOT NULL,
    display_name  VARCHAR(255) NOT NULL,
    avatar_url    TEXT,
    provider      VARCHAR(50) NOT NULL DEFAULT 'google',
    provider_sub  VARCHAR(255),
    is_active     BOOLEAN NOT NULL DEFAULT true,
    is_blocked    BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Roles ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.roles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    is_system   BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Permissions ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.permissions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codename    VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    category    VARCHAR(50)
);

-- ── Role ↔ Permission (M:N) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.role_permissions (
    role_id       UUID REFERENCES dbo.roles(id) ON DELETE CASCADE,
    permission_id UUID REFERENCES dbo.permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- ── User ↔ Role (M:N) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.user_roles (
    user_id UUID REFERENCES dbo.users(id) ON DELETE CASCADE,
    role_id UUID REFERENCES dbo.roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- ── Sessions ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES dbo.users(id) ON DELETE CASCADE,
    token_hash  VARCHAR(64) NOT NULL,
    ip_address  INET,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked     BOOLEAN NOT NULL DEFAULT false
);

-- ── User Activity Log ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.user_activity_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID REFERENCES dbo.users(id),
    session_id  UUID REFERENCES dbo.sessions(id),
    action      VARCHAR(100) NOT NULL,
    detail      JSONB,
    ip_address  INET,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activity_user
    ON dbo.user_activity_log(user_id, created_at DESC);

-- ── User Workspaces ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dbo.user_workspaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES dbo.users(id) ON DELETE CASCADE,
    name        VARCHAR(255) NOT NULL,
    disk_path   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, name)
);

-- ── Binary Assets (file metadata) ───────────────────────────
CREATE TABLE IF NOT EXISTS dbo.binary_assets (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID REFERENCES dbo.user_workspaces(id) ON DELETE CASCADE,
    filename     VARCHAR(255) NOT NULL,
    mime_type    VARCHAR(100),
    size_bytes   BIGINT NOT NULL,
    disk_path    TEXT NOT NULL,
    sha256_hash  VARCHAR(64),
    uploaded_by  UUID REFERENCES dbo.users(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- Seed: Default roles
-- ============================================================
INSERT INTO dbo.roles (name, description, is_system) VALUES
    ('admin',    'Pełen dostęp do systemu',              true),
    ('operator', 'Zarządzanie agentami i zadaniami',     false),
    ('viewer',   'Podgląd — tylko odczyt',               false)
ON CONFLICT (name) DO NOTHING;

-- ============================================================
-- Seed: Default permissions
-- ============================================================
INSERT INTO dbo.permissions (codename, description, category) VALUES
    ('dashboard.view',     'Podgląd dashboardu',                'dashboard'),
    ('agents.view',        'Podgląd listy agentów',             'agents'),
    ('agents.manage',      'Tworzenie/edycja/usuwanie agentów', 'agents'),
    ('agents.chat',        'Interakcja z agentami (prompt)',     'agents'),
    ('tasks.view',         'Podgląd zadań',                     'tasks'),
    ('tasks.manage',       'Tworzenie/edycja/anulowanie zadań', 'tasks'),
    ('files.upload',       'Upload plików',                     'files'),
    ('files.download',     'Pobieranie plików/archiwów',        'files'),
    ('files.manage',       'Dodawanie/usuwanie binarnych',      'files'),
    ('workspace.view',     'Przeglądanie workspace',            'workspace'),
    ('workspace.edit',     'Edycja plików w workspace',         'workspace'),
    ('admin.users',        'Zarządzanie użytkownikami',         'admin'),
    ('admin.roles',        'Zarządzanie rolami',                'admin'),
    ('admin.audit',        'Przeglądanie logów audytowych',     'admin'),
    ('admin.settings',     'Zmiana ustawień systemowych',       'admin'),
    ('models.view',        'Podgląd konfiguracji modeli',       'models'),
    ('models.manage',      'Zmiana modeli agentów',             'models')
ON CONFLICT (codename) DO NOTHING;

-- ============================================================
-- Seed: admin → all permissions
-- ============================================================
INSERT INTO dbo.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM dbo.roles r CROSS JOIN dbo.permissions p
WHERE r.name = 'admin'
ON CONFLICT DO NOTHING;

-- ============================================================
-- Seed: operator → agents.*, tasks.*, files.*, workspace.*, dashboard.*, models.view
-- ============================================================
INSERT INTO dbo.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM dbo.roles r, dbo.permissions p
WHERE r.name = 'operator'
  AND p.codename IN (
    'dashboard.view', 'agents.view', 'agents.manage', 'agents.chat',
    'tasks.view', 'tasks.manage', 'files.upload', 'files.download',
    'workspace.view', 'workspace.edit', 'models.view'
  )
ON CONFLICT DO NOTHING;

-- ============================================================
-- Seed: viewer → *.view
-- ============================================================
INSERT INTO dbo.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM dbo.roles r, dbo.permissions p
WHERE r.name = 'viewer'
  AND p.codename LIKE '%.view'
ON CONFLICT DO NOTHING;
