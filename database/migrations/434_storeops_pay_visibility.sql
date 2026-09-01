-- 434_storeops_pay_visibility.sql — mod-people band 400-499.
--
-- OWNER SPEC (Payroll & Workforce charter rule 4, 2026-09-01): pay-per-hour, gross pay and salary
-- are HIDDEN BY DEFAULT from every level below market manager; market manager and above see them;
-- WHICH roles see pay is a per-org CONFIG (nothing hardcoded); those below can see/adjust hours only
-- per granted permission. Enforced SERVER-SIDE (backend/app/modules/storeops/pay_visibility.py strips
-- the money keys from the payload before serialization, so a gated column can never leak through an
-- Excel/PDF export — RULE FOUR).
--
-- RULE TWO (config, never code): the whole policy is two tenant columns on storeops.tenants — the
-- SAME per-org settings home migrations 085 (pay-period cycle), 418 (lunch deduction), 421
-- (attendance thresholds) and 433 (lateness alerts) already use. No new table, no RLS/grant change
-- (storeops.tenants keeps its service-role-only posture).
--
--   pay_visibility     'manager_up'   (DEFAULT — the owner rule: market manager and above see pay)
--                      'permissioned' (only roles holding the `employee_pay_rates` data grant —
--                                      rbac.ts DATA_GRANTS key, granted per role on /admin/roles)
--                      'all'          (legacy open — every caller who can open the page sees pay)
--   pay_visible_roles  explicit allow-list of role names for 'manager_up'; NULL = the built-in
--                      default {'admin','master_admin','market_manager','market'}, which IS
--                      "market manager and above": any role ABOVE market manager is company-wide
--                      (permissions.scope='all') and passes on its scope without needing a row here.
--
-- ADAPTIVE + SAFE BY DEFAULT: the backend reads these columns adaptively (a pre-434 database
-- resolves to 'manager_up' — the restrictive owner default, never open), so deploying code before
-- or after this migration is safe in either order. Additive + idempotent (ADD COLUMN IF NOT EXISTS).
--
-- REVERT: ALTER TABLE storeops.tenants DROP COLUMN IF EXISTS pay_visibility,
--                                      DROP COLUMN IF EXISTS pay_visible_roles;
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS pay_visibility TEXT NOT NULL DEFAULT 'manager_up'
    CHECK (pay_visibility IN ('all', 'manager_up', 'permissioned')),
  ADD COLUMN IF NOT EXISTS pay_visible_roles TEXT[];

COMMENT ON COLUMN storeops.tenants.pay_visibility IS
  'Who sees pay-per-hour / gross pay / salary on payroll & workforce surfaces (owner rule, enforced server-side in pay_visibility.py): ''manager_up'' (DEFAULT) = market manager and above; ''permissioned'' = only roles holding the employee_pay_rates data grant; ''all'' = legacy open.';
COMMENT ON COLUMN storeops.tenants.pay_visible_roles IS
  'Explicit role-name allow-list for pay_visibility=''manager_up''. NULL = built-in default {admin,master_admin,market_manager,market} ("market manager and above" — company-wide roles above MM pass on scope=''all'' regardless). Set it when the tenant names its management roles differently.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 434 complete — storeops.tenants pay-visibility RBAC config (pay_visibility=manager_up default, pay_visible_roles=NULL -> market manager and above). Server gate: backend/app/modules/storeops/pay_visibility.py.' AS status;
