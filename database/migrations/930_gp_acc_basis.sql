-- 930_gp_acc_basis.sql
-- Owner directive 2026-09-02 (verbatim): "Acc Gp should show the price at which the accessories were
-- sold not the Gross profit as they are not entered correct, so Acc Gp renamed to Acc Sales."
--
-- Per-org BASIS of the GP report's accessory column (RULE TWO — config, never code):
--   'sales' — Σ ext_price of accessory sale lines (sell price; the basis the carrier portal's own
--             'Acc. Sales' column reconciled to within 1%). HOUSE DEFAULT: a NULL/absent value
--             resolves to 'sales' in the reader (commcalc/router._accessory_config_uncached).
--   'gp'    — the legacy Σ gp, opt-back for a tenant whose POS accessory costs are trustworthy.
-- Consumed by gp_report.calc_gp_report(acc_basis=…); the report payload carries acc_basis +
-- acc_label ('Acc Sales'/'Acc GP') so display surfaces never hardcode the label.
--
-- Idempotent, additive. No backfill: NULL means "house default" by design, so the default can be
-- governed in one place (the reader) rather than frozen into every row.

ALTER TABLE commcalc.accessory_config
    ADD COLUMN IF NOT EXISTS gp_acc_basis text;

COMMENT ON COLUMN commcalc.accessory_config.gp_acc_basis IS
    'GP-report accessory column basis: ''sales'' (Σ ext_price — house default, applied on NULL) or ''gp'' (legacy Σ gp). Mig 930, owner 2026-09-02.';

-- REVERT: ALTER TABLE commcalc.accessory_config DROP COLUMN IF EXISTS gp_acc_basis;
