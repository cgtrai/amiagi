-- ============================================================
-- Migration 001 (SQLite): Initial schema for amiagi Web GUI
-- ============================================================

-- ── Users ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    email         TEXT UNIQUE NOT NULL,
    display_name  TEXT NOT NULL,
    avatar_url    TEXT,
    provider      TEXT NOT NULL DEFAULT 'google',
    provider_sub  TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    is_blocked    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Roles ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS roles (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    is_system   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Permissions ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS permissions (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    codename    TEXT UNIQUE NOT NULL,
    description TEXT,
    category    TEXT
);

-- ── Role ↔ Permission (M:N) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id       TEXT REFERENCES roles(id) ON DELETE CASCADE,
    permission_id TEXT REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- ── User ↔ Role (M:N) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    role_id TEXT REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- ── Sessions ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id     TEXT REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,
    ip_address  TEXT,
    user_agent  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);

-- ── User Activity Log ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_activity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT REFERENCES users(id),
    session_id  TEXT REFERENCES sessions(id),
    action      TEXT NOT NULL,
    detail      TEXT,       -- JSON
    ip_address  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activity_user
    ON user_activity_log(user_id, created_at DESC);

-- ── User Workspaces ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_workspaces (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    user_id     TEXT REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    disk_path   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, name)
);

-- ── Binary Assets (file metadata) ───────────────────────────
CREATE TABLE IF NOT EXISTS binary_assets (
    id           TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    workspace_id TEXT REFERENCES user_workspaces(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    mime_type    TEXT,
    size_bytes   INTEGER NOT NULL,
    disk_path    TEXT NOT NULL,
    sha256_hash  TEXT,
    uploaded_by  TEXT REFERENCES users(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Seed: Default roles
-- ============================================================
INSERT INTO roles (name, description, is_system) VALUES
    ('admin',    'Pełen dostęp do systemu',              1),
    ('operator', 'Zarządzanie agentami i zadaniami',     0),
    ('viewer',   'Podgląd — tylko odczyt',               0)
ON CONFLICT (name) DO NOTHING;

-- ============================================================
-- Seed: Default permissions
-- ============================================================
INSERT INTO permissions (codename, description, category) VALUES
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
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r CROSS JOIN permissions p
WHERE r.name = 'admin'
ON CONFLICT DO NOTHING;

-- ============================================================
-- Seed: operator → agents.*, tasks.*, files.*, workspace.*, dashboard.*, models.view
-- ============================================================
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
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
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.name = 'viewer'
  AND p.codename LIKE '%.view'
ON CONFLICT DO NOTHING;
