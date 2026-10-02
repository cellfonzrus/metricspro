-- 1036_envelope_count_basis.sql — a stored envelope count records WHICH CASH it counted
-- (owner 2026-10-02, the item #348 reported open rather than fixed).
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- The Management Envelope Receipt lets management pick which cash a count is of — the whole
-- drawer, the store's own cash, or the bill-payment cash inside it (§47.1). Until #348 the save
-- handler ignored that pick and scored every count against the whole drawer; #348 fixed the
-- scoring, and the count is now measured on the basis the counter was looking at.
--
-- What #348 could NOT fix without a migration: `commcalc.envelope_count` has no column saying
-- which basis a stored count used. Each row stays self-consistent — its own `expected_amount` is
-- the snapshot it was scored against, so no variance is wrong and no chargeback is wrong — but the
-- report cannot LABEL a stored count, and two rows reading "short $230" may be answering two
-- different questions. This column makes the row say which.
--
-- ── ONE FACT, ONE HOME ─────────────────────────────────────────────────────────────────────────
-- The basis vocabulary is NOT restated here as a list this file maintains. The three keys are
-- `closing/envelope_report.ENVELOPE_BASES`, themselves the bases of
-- `closing/deposit_recon.cash_for_basis`, and `backend/harness_envelope_receipt_basis.py` fails
-- the build if this CHECK and that tuple ever disagree. The constraint exists so the DATABASE
-- cannot hold a word the math has no formula for; the tuple remains the home.
--
-- ── ABSENCE IS NEVER A GUESS ───────────────────────────────────────────────────────────────────
-- NULLABLE, and deliberately NOT backfilled. A row written before this migration recorded no
-- basis, and a backfill would turn "nobody recorded this" into "somebody recorded total_cash" —
-- the house rule that absence is never zero. The READER resolves a NULL instead, in one place
-- (`envelope_report.counted_basis`): it reports the historical default basis together with
-- `recorded: false`, because the handler of that era had no basis to use and scored against the
-- whole drawer by construction. So the screen can say "total cash (not recorded — counted before
-- the basis was stored)" and never claim a person chose it.
--
-- NOT money-moving: no stored amount, variance, status or chargeback is read or written here.
-- Every existing row keeps every value it has. Additive + idempotent. Run in the Supabase SQL editor.

ALTER TABLE commcalc.envelope_count
  ADD COLUMN IF NOT EXISTS basis TEXT;

-- The three bases the cash math has a formula for (envelope_report.ENVELOPE_BASES). NULL stays
-- legal: it is how an un-recorded basis is spelled, and the reader words it.
DO $$
BEGIN
  BEGIN
    EXECUTE $q$ALTER TABLE commcalc.envelope_count
                 ADD CONSTRAINT envelope_count_basis_known
                 CHECK (basis IS NULL OR basis IN ('total_cash', 'store_cash', 'bill_payment_cash'))$q$;
  EXCEPTION WHEN duplicate_object THEN NULL; END;
END $$;

-- The receipt filters and groups by basis over a date range; the day index already leads on
-- (org_id, close_date), so this one only has to answer "which basis" within an org.
CREATE INDEX IF NOT EXISTS envelope_count_basis ON commcalc.envelope_count (org_id, basis);

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1036 complete — commcalc.envelope_count.basis (which cash a stored count counted)' AS status;

-- REVERT:
--   DROP INDEX IF EXISTS commcalc.envelope_count_basis;
--   ALTER TABLE commcalc.envelope_count DROP CONSTRAINT IF EXISTS envelope_count_basis_known;
--   ALTER TABLE commcalc.envelope_count DROP COLUMN IF EXISTS basis;
--   (Nothing else depends on the column: `counted_basis` treats a missing key exactly as it treats
--    a NULL — the historical default with recorded=false — so the receipt degrades to the words it
--    used before this migration rather than erroring. No amount is affected either way.)
