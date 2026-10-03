-- 1039_cash_pickup_amount_basis.sql — a stored cash pickup records WHICH CASH its amount was.
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────────
-- `commcalc.cash_pickup.amount` is a SNAPSHOT of what the pickup screen offered the DM, and the row
-- carries no statement of which cash that was. While the bill-pay netting is off it is the whole
-- declared drawer; the day an org turns the netting on (mig 989) or changes its source (mig 1038)
-- every historical row silently changes meaning, and the variance beside it — `actual_picked_amount`
-- minus `amount` — is then read against a basis nobody recorded.
--
-- This is the same defect class migration 1036 just closed for a stored envelope count, found in the
-- report that prompted it (owner 2026-10-02, B-2612 / 2026-09-03). Fixing it for one table and not
-- its sibling question is the patchwork the house rules forbid.
--
-- ── ONE FACT, ONE HOME ─────────────────────────────────────────────────────────────────────────────
-- The vocabulary is `closing/billpay_netting.PICKUP_BASES`, and `backend/harness_billpay_netting.py`
-- fails the build if this CHECK and that tuple ever disagree. The column is WRITTEN by
-- `closing/router.pickup_amount_basis` (from the config in force, server-side — never from anything
-- the client asserts) and READ by `closing/router.pickup_basis_label`, one writer and one reader.
--
-- ── ABSENCE IS NEVER A GUESS ───────────────────────────────────────────────────────────────────────
-- NULLABLE, and deliberately NOT backfilled. Every row written before this column recorded no basis.
-- It is true that the netting has been off for every org since mig 989 shipped, so those rows are in
-- fact the whole drawer — but a backfill would turn "nobody recorded this" into "the system recorded
-- 'off'", and the two are not the same statement. The READER resolves a NULL instead, in one place:
-- it reports the historical default together with `recorded: false`, so the screen can say "total
-- cash (not recorded — collected before the basis was stored)" and never claim otherwise.
--
-- NOT money-moving: no stored amount, actual, variance or disposition is read or written here. Every
-- existing row keeps every value it has. Additive + idempotent. Run in the Supabase SQL editor.

ALTER TABLE commcalc.cash_pickup
  ADD COLUMN IF NOT EXISTS amount_basis TEXT;

-- The four states the netting has behaviour for (billpay_netting.PICKUP_BASES). NULL stays legal: it
-- is how an un-recorded basis is spelled, and the reader words it.
DO $$
BEGIN
  BEGIN
    EXECUTE $q$ALTER TABLE commcalc.cash_pickup
                 ADD CONSTRAINT cash_pickup_amount_basis_known
                 CHECK (amount_basis IS NULL
                        OR amount_basis IN ('off', 'none', 'declared', 'pos'))$q$;
  EXCEPTION WHEN duplicate_object THEN NULL; END;
END $$;

-- The pickup list filters by store-day already; this one only has to answer "which basis" within an
-- org, for reading a span of history back after a source change.
CREATE INDEX IF NOT EXISTS cash_pickup_amount_basis ON commcalc.cash_pickup (org_id, amount_basis);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1039 complete — commcalc.cash_pickup.amount_basis (which cash the amount was)' AS status;

-- REVERT:
--   DROP INDEX IF EXISTS commcalc.cash_pickup_amount_basis;
--   ALTER TABLE commcalc.cash_pickup DROP CONSTRAINT IF EXISTS cash_pickup_amount_basis_known;
--   ALTER TABLE commcalc.cash_pickup DROP COLUMN IF EXISTS amount_basis;
--   (Nothing else depends on the column: the pickup writer retries WITHOUT it on a schema that lacks
--    it, and `pickup_basis_label` treats a missing key exactly as it treats a NULL — the historical
--    default with recorded=false. No amount is affected either way.)
