-- 017_employee_widget_overrides.sql — per-EMPLOYEE Employee-Dashboard widget overrides (#1b).
-- Role-level widget gating already lives in storeops.roles.permissions.employee_widgets.
-- This adds a per-person override layer: a partial JSONB map {widget_key: bool} on the
-- app_user. Effective visibility = role default, then this override applied on top
-- (only keys present here override; absent keys inherit the role). NULL/{} = pure inherit.
ALTER TABLE storeops.app_users
  ADD COLUMN IF NOT EXISTS widget_overrides JSONB;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 017 complete — storeops.app_users.widget_overrides ready' AS status;
