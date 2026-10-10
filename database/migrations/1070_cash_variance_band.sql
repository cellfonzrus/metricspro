-- 1070_cash_variance_band.sql — "WE IGNORE UPTO $5 OF VARIANCE" IS A TENANT'S ANSWER, NOT A CONSTANT
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim 2026-10-10: "the data will never match so we ignore upto $5 of variance, the
-- system should then be smart to find the trends of a particular store / employee".
--
-- RULE TWO (config, never code) says $5 cannot be a number in a Python file. It is this tenant's
-- current tolerance for counting noise, and the next tenant — or this one next quarter — will want a
-- different one. So the band is a per-org config value with a house default, and the report reads it.
--
-- THE CLASS, NOT THE INSTANCE. The instance is "the new report needs $5". The class is that the
-- dollar band below which a cash difference is noise was a LITERAL in every reader that needed one:
--   deposit_recon.status_for(..., tolerance=1.0)        envelope_report.count_fields(..., 0.0)
--   deposit_accountability.pos_next_to(..., 1.0)        deposit_accountability.dm_shortage_rows(0.0)
--   billpay_pickup.billpay_pos_mismatch(..., 1.0)       external_credit_recon.recon_row(..., 0.0)
-- Six readers, six literals, and the owner's $5 would have been a seventh.
--
-- WHAT THIS MIGRATION DOES NOT DO, STATED PLAINLY: it does not change any of those six. Each is a
-- DEFAULT ARGUMENT of a pure function whose callers pass their own agreed band — $1 for rounding on a
-- bank slip, $0 for a counted envelope that must tie to the cent — and widening them all to $5 would
-- SILENTLY STOP SURFACING variances those screens report today. That is a money-visibility change
-- nobody asked for. They are excused here on the record, not overlooked: the one home now exists
-- (`closing/cash_variance.band`), the new reader dereferences it, and migrating the other six is a
-- separate, reviewable change per screen.
--
-- ── WHAT SHIPS (additive, nullable, no backfill, no default) ────────────────────────────────────
--   commcalc.cash_pickup_config.cash_variance_band      NUMERIC  — dollars; NULL = house default 5.00
--   commcalc.cash_pickup_config.cash_variance_min_days  INTEGER  — out-of-band days that make a
--                                                                  trend; NULL = house default 3
--
-- `cash_pickup_config` is the EXISTING per-org config for this very chain (mig 942 / 949 / 989 /
-- 1038 / 1039 live on it: who is notified, whether an actual count relieves cash, which bill-pay
-- netting source). A new config table for two numbers would be the sibling the index rules forbid.
--
-- NULL IS "NO OPINION", NEVER ZERO. `closing/cash_variance.band()` resolves NULL, blank, negative
-- and unparseable to the house default on purpose: an operator clearing the cell must not silently
-- turn every rounding difference into a shortage against a named employee.
--
-- ADAPTIVE BY CONSTRUCTION (the mig-201 precedent every writer in this module follows): the report
-- SELECTs `*` from `cash_pickup_config` and resolves whatever it finds, so on an un-migrated database
-- it simply reads the house $5 and $3 days. Nothing breaks before this is run, and nothing needs
-- re-running after.
--
-- 💰 MOVES NO MONEY. Two nullable config columns. Books nothing, pays nothing, alters no amount, no
-- existing column and no constraint; no P&L, GP, payroll, commission or payout path reads them. The
-- report they steer is read-only and writes nothing.
--
-- Additive + idempotent. RLS unchanged (columns on an existing table). Run in the Supabase SQL editor.

ALTER TABLE commcalc.cash_pickup_config
  ADD COLUMN IF NOT EXISTS cash_variance_band NUMERIC;
ALTER TABLE commcalc.cash_pickup_config
  ADD COLUMN IF NOT EXISTS cash_variance_min_days INTEGER;

COMMENT ON COLUMN commcalc.cash_pickup_config.cash_variance_band IS
  'Dollars below which a cash difference is counting noise rather than a variance, for the Cash '
  'Variance report (index 72). Owner 2026-10-10: "we ignore upto $5 of variance". NULL = the house '
  'default 5.00 — never 0, which would make every rounding difference a shortage against a named '
  'employee. Read ONLY through closing/cash_variance.band(); no other reader may spell a band.';
COMMENT ON COLUMN commcalc.cash_pickup_config.cash_variance_min_days IS
  'How many out-of-band days in the range make a TREND worth highlighting rather than a bad day. '
  'NULL = the house default 3, which deliberately matches flag_registry CASH_REPEAT_VARIANCE '
  'min_days: the same question already had an answer on the findings side. Read only through '
  'closing/cash_variance.min_days().';

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1070 complete — cash_variance_band / cash_variance_min_days on cash_pickup_config'
       AS status;

-- REVERT:
--   ALTER TABLE commcalc.cash_pickup_config DROP COLUMN IF EXISTS cash_variance_band;
--   ALTER TABLE commcalc.cash_pickup_config DROP COLUMN IF EXISTS cash_variance_min_days;
--   (Safe at any time. The report SELECTs * and resolves an absent value to the house default, so a
--    revert returns every org to $5 and 3 days and moves no stored figure — these columns are read
--    by one read-only report and by nothing that books, pays or settles anything.)
