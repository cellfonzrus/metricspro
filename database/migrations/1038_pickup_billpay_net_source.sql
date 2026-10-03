-- 1038_pickup_billpay_net_source.sql — WHICH bill-pay figure Cash Pickup nets by.
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────────
-- OWNER BUG REPORT 2026-10-02 (B-2612, 2026-09-03):
--   "cash pick up 258 short but 258 is epay cash which appears on the next report for epay pick up,
--    this cash pick up report should not show anything short since 15 is declared as store cash and
--    258 as epay and the total is 273 — cash pick up should only show the cash from sales and a
--    column for total cash"
-- Confirmed in live data: that store-day's drawer is $273.00, the rep declared $258.00 of it as
-- bill-pay cash, the DM collected $15.00, and the pickup row stored $273.00 as the amount to
-- collect — so the variance reads exactly −$258.00, the bill-pay cash the bill-pay screen collects
-- separately. The same physical dollars, offered on two screens.
--
-- ── WHAT THIS IS NOT ───────────────────────────────────────────────────────────────────────────────
-- It is NOT a new mechanism. Migration 989 built this netting to the owner's directive of
-- 2026-09-08, which said the figure to net by is the one "CALCULATED BY THE POS", not the
-- declaration. That switch (`pickup_nets_pos_billpay_cash`) has been FALSE for every org since it
-- shipped, which is why the overlap is still on screen. This migration adds the one thing that
-- directive did not cover: the 2026-10-02 report describes the DECLARED figures, so WHICH figure to
-- net by is now a per-org choice instead of a second code path (RULE TWO — behaviour is config, never
-- a branch on a tenant).
--
-- ── ONE FACT, ONE HOME ─────────────────────────────────────────────────────────────────────────────
-- The vocabulary is `closing/billpay_netting.NET_SOURCES`, and `backend/harness_billpay_netting.py`
-- fails the build if this CHECK and that tuple ever disagree. The constraint exists so the DATABASE
-- cannot hold a word the netting has no behaviour for; the tuple remains the home.
--
-- DEFAULT 'pos' — byte-identical to what mig 989 shipped, so this migration changes NO number for
-- any org on its own. It only makes the other source available to choose.
--
-- ⚠ NOT money-moving BY ITSELF. Choosing a source changes nothing while
--    `pickup_nets_pos_billpay_cash` is FALSE (as it is for every org today). Switching THAT on is
--    the money-touching step — it changes what a DM is told to collect — and stays the owner's call
--    with the numbers in front of them. Both statements are left commented out below for that reason.
-- Additive + idempotent. Run in the Supabase SQL editor.

ALTER TABLE commcalc.cash_pickup_config
  ADD COLUMN IF NOT EXISTS pickup_billpay_net_source TEXT NOT NULL DEFAULT 'pos';

DO $$
BEGIN
  BEGIN
    EXECUTE $q$ALTER TABLE commcalc.cash_pickup_config
                 ADD CONSTRAINT cash_pickup_net_source_known
                 CHECK (pickup_billpay_net_source IN ('pos', 'declared'))$q$;
  EXCEPTION WHEN duplicate_object THEN NULL; END;
END $$;

COMMENT ON COLUMN commcalc.cash_pickup_config.pickup_billpay_net_source IS
  'WHICH bill-pay cash figure Cash Pickup nets out of each envelope when '
  'pickup_nets_pos_billpay_cash is true. ''pos'' (default, owner directive 2026-09-08) = the '
  'POS-calculated bill-pay cash for the store-day; an envelope on a store-day the POS has no figure '
  'for is left un-netted and says so. ''declared'' (owner report 2026-10-02) = each rep''s own '
  'declared bill-pay cash out of their own envelope, capped at that envelope''s cash; it resolves '
  'every store-day, and where a POS figure also exists the screen reports whether the POS backs the '
  'declaration. See closing/billpay_netting.NET_SOURCES and index 23m.';

-- ── CHOOSE THE SOURCE, THEN SWITCH THE NETTING ON — UNCOMMENT AFTER REVIEWING ONE DAY ─────────────
-- Deliberately left commented: together these change the cash a DM is told to collect.
--
-- UPDATE commcalc.cash_pickup_config SET pickup_billpay_net_source = 'declared'
--  WHERE org_id = '00000000-0000-0000-0000-000000000001';
--
-- INSERT INTO commcalc.cash_pickup_config (org_id, pickup_nets_pos_billpay_cash)
-- VALUES ('00000000-0000-0000-0000-000000000001', TRUE)
-- ON CONFLICT (org_id) DO UPDATE SET pickup_nets_pos_billpay_cash = EXCLUDED.pickup_nets_pos_billpay_cash;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1038 complete — cash_pickup_config.pickup_billpay_net_source (pos | declared)' AS status;

-- REVERT:
--   ALTER TABLE commcalc.cash_pickup_config DROP CONSTRAINT IF EXISTS cash_pickup_net_source_known;
--   ALTER TABLE commcalc.cash_pickup_config DROP COLUMN IF EXISTS pickup_billpay_net_source;
--   (The reader degrades to 'pos' when the column is absent — `closing/router.billpay_net_source`
--    never raises — so dropping it restores exactly the mig-989 behaviour rather than erroring.)
