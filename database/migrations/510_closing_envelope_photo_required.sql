-- 510_closing_envelope_photo_required.sql — mod-retail-ops band 500-599.
--
-- BUG FIX (owner-reported 2026-08-07): reps were submitting a daily closing with cash declared but the
-- envelope photo silently never reached the server (root cause + client fix in ClosingSubmitForm.tsx —
-- see the retail-ops handoff). This migration adds the TENANT-CONFIGURABLE hard gate that lets a tenant
-- require an envelope photo whenever cash > 0 is declared on a closing. OFF by default
-- (require_photo_if_cash = false) so an un-opted tenant's close flow stays BYTE-IDENTICAL to today —
-- RULE TWO (SAP-configurable, never hard-coded). Surfaced on the existing Envelope Config page
-- (/closing/envelope-config), org default + optional per-store override, same shape as
-- take_commission/take_salary/take_expenses.
--
-- Depends on migration 507 (commcalc.envelope_payout_config) — guarded below so this is a safe no-op
-- if it happens to run before 507 (belt-and-suspenders, same pattern as mig 508). SAFE: additive +
-- idempotent, degrades gracefully — until this runs, GET /closing/envelope-config reads the coded
-- default (require_photo_if_cash=false, byte-identical to today's unconditional accept), and
-- PUT /closing/envelope-config saves every OTHER field unaffected (this column is independently
-- try/except-guarded in closing/router.py's put_envelope_config). POST /closing/row's new photo-
-- required gate reads the same coded default (false) and never blocks until this is run AND a tenant
-- opts in.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'commcalc' AND table_name = 'envelope_payout_config'
  ) THEN
    ALTER TABLE commcalc.envelope_payout_config
      ADD COLUMN IF NOT EXISTS require_photo_if_cash BOOLEAN NOT NULL DEFAULT false;
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT '510 complete — commcalc.envelope_payout_config.require_photo_if_cash ready (no-op if 507 has not run yet)' AS status;
