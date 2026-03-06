-- ============================================================
-- Migration 013 (PostgreSQL): Add vault permissions
-- ============================================================

-- ── Vault permissions ────────────────────────────────────────
INSERT INTO dbo.permissions (codename, description, category) VALUES
    ('vault.admin',  'Zarządzanie sejfem (dodawanie, rotacja, usuwanie sekretów)', 'vault'),
    ('vault.view',   'Podgląd listy sekretów (bez wartości)',                      'vault')
ON CONFLICT (codename) DO NOTHING;

-- ── admin → vault.admin + vault.view ─────────────────────────
INSERT INTO dbo.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM dbo.roles r CROSS JOIN dbo.permissions p
WHERE r.name = 'admin'
  AND p.codename IN ('vault.admin', 'vault.view')
ON CONFLICT DO NOTHING;

-- ── operator → vault.view ────────────────────────────────────
INSERT INTO dbo.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM dbo.roles r, dbo.permissions p
WHERE r.name = 'operator'
  AND p.codename = 'vault.view'
ON CONFLICT DO NOTHING;
