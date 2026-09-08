-- 989 — Cash Pickup nets out the bill-pay cash that is collected on the bill-pay screen.
--
-- OWNER DIRECTIVE 2026-09-08:
--   "on the cash pick up it shows the full amount but it should only show the store cash amount to
--    be picked up, as the epay amount is being declared and picked up on a different menu — this is
--    duplicating the total cash."
--   and, asked which figure to net by:
--   "it should be the total cash minus the epay cash, NOT as declared by the employee but as
--    CALCULATED BY THE POS."
--
-- The closing form's cash field is "Total cash in store including Bill Payments" (owner 2026-09-02),
-- so `t_cash` is the whole drawer and the bill-pay share sits inside it. Cash Pickup collected the
-- drawer while the bill-pay page separately offered that share for collection — the same physical
-- dollars on two screens.
--
-- RULE TWO: this is a per-org SWITCH, not a code branch. Default FALSE, so every tenant behaves
-- exactly as before until switched on; the code reads it adaptively, so a database that has not run
-- this migration is also unchanged.
--
-- ⚠ MONEY-TOUCHING — SEEDING THIS TRUE CHANGES WHAT A DM IS TOLD TO COLLECT.
--    Measured live for August 2026 on the house org: Cash Pickup currently offers $209,583.23
--    (= sum of t_cash). The POS sales leg reports bill-pay CASH of roughly $91,232 for Aug 1-7
--    alone, so the netted figure will be materially lower. Nothing is netted for a store-day the POS
--    has no figure for — that envelope is shown un-netted and says so, rather than being reduced by
--    a fabricated zero.
--    Review the numbers on the screen with netting ON for one day before leaving it on.
--
-- REVERT:
--   UPDATE commcalc.cash_pickup_config SET pickup_nets_pos_billpay_cash = FALSE
--    WHERE org_id = '00000000-0000-0000-0000-000000000001';
--   -- (the column itself is additive and safe to leave in place;
--   --  ALTER TABLE commcalc.cash_pickup_config DROP COLUMN IF EXISTS pickup_nets_pos_billpay_cash;)

ALTER TABLE commcalc.cash_pickup_config
  ADD COLUMN IF NOT EXISTS pickup_nets_pos_billpay_cash BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN commcalc.cash_pickup_config.pickup_nets_pos_billpay_cash IS
  'When true, Cash Pickup subtracts the POS-calculated bill-pay CASH for the store-day from each '
  'envelope, because that cash is collected on the bill-pay pickup screen. The amount always comes '
  'from the POS (sales-transaction bill-pay cash, else the processor figure), never from the rep''s '
  'declared epay_on_cash; the declaration only splits the POS amount between the reps who worked. '
  'No POS figure for a store-day means nothing is netted and the envelope says so. Owner directive '
  '2026-09-08; see index 23m.';

-- ── SWITCH IT ON — UNCOMMENT ONLY AFTER REVIEWING ONE DAY ON SCREEN ──────────────────────────────
-- This is deliberately left commented: it changes the cash a DM is told to collect, which is the
-- owner's call to make with the numbers in front of them, not a side effect of running a migration.
--
-- INSERT INTO commcalc.cash_pickup_config (org_id, pickup_nets_pos_billpay_cash)
-- VALUES ('00000000-0000-0000-0000-000000000001', TRUE)
-- ON CONFLICT (org_id) DO UPDATE SET pickup_nets_pos_billpay_cash = EXCLUDED.pickup_nets_pos_billpay_cash;
