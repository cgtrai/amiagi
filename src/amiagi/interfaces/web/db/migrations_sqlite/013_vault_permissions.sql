-- ============================================================
-- Migration 013 (SQLite): Add vault permissions
-- ============================================================

-- ── Vault permissions ────────────────────────────────────────
INSERT OR IGNORE INTO permissions (codename, description, category) VALUES
    ('vault.admin',  'Zarządzanie sejfem (dodawanie, rotacja, usuwanie sekretów)', 'vault');
INSERT OR IGNORE INTO permissions (codename, description, category) VALUES
    ('vault.view',   'Podgląd listy sekretów (bez wartości)',                      'vault');

-- ── admin → vault.admin + vault.view ─────────────────────────
INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r CROSS JOIN permissions p
WHERE r.name = 'admin'
  AND p.codename IN ('vault.admin', 'vault.view');

-- ── operator → vault.view ────────────────────────────────────
INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.name = 'operator'
  AND p.codename = 'vault.view';
