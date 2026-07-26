-- 505_closing_stale_alert_config.sql — mod-retail-ops SETTINGS AUDIT (OWNER DIRECTIVE 2026-07-26).
--
-- WHAT: one tenant-configurable knob backing the new "stores selling but not submitting daily
-- closings" admin-attention check (backend/app/modules/closing/attention_providers.py,
-- _p_closing_stale_stores). N = how many days a store may have B2B sales activity with NO
-- daily_closing submission before it's flagged in the login attention popup. 0 disables the check
-- for a tenant that intentionally doesn't use Daily Closing for every store.
--
-- WHY storeops.tenants (not a new commcalc table): this module already writes its other closing-gate
-- settings onto this exact row (closing_deadline / closing_gate_enabled / cash_alert_after_days /
-- closing_mode — mig 033/089), so a 5th sibling column keeps GET/PUT /closing/cash-config a single
-- read/write instead of a second table for one integer.
--
-- SAFETY: additive + idempotent (IF NOT EXISTS, safe to re-run). DEFAULT 3 means an existing tenant's
-- behaviour the moment this runs is "flag after 3 days" — the SAME default the Python fallback in
-- both get_cash_config() and the attention provider already uses when this column/migration doesn't
-- exist yet, so running this migration changes NOTHING observable; it only makes the value durable
-- and editable instead of a hardcoded fallback.
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS closing_stale_alert_days INT NOT NULL DEFAULT 3;

COMMENT ON COLUMN storeops.tenants.closing_stale_alert_days IS
  'Admin-attention: flag a store as "selling but not closing" once it has gone this many days with '
  'B2B sales activity but no daily_closing submission. 0 disables the check for this tenant. '
  'Edited at /closing/cash-config; read/written by backend/app/modules/closing/router.py '
  '(get_cash_config/put_cash_config) and attention_providers.py.';

SELECT '505 complete — storeops.tenants.closing_stale_alert_days (default 3, 0 = disabled)' AS status;
