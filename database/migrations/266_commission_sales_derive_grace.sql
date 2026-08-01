-- 266_commission_sales_derive_grace.sql  (mod-commission, band 200-299)
--
-- MONTH-BOUNDARY SALES-DERIVATION GRACE WINDOW — per-tenant config for how long after a month rolls
-- over the automatic feed→raw_sales derivation keeps re-deriving the month that just closed.
--
-- WHY (owner-verified on production data, tenant luxelink, 2026-08-01)
--   The hourly derivation picked its period from the wall clock and nothing else
--   (router._ftp_current_period() = datetime.now().strftime('%B %Y')), so from 00:00 on the 1st it only
--   ever derived the NEW month. The B2B daily email feed, meanwhile, carried on FINALIZING the old one:
--   that night the July daily_sales_feed row-count climbed 283 -> 313 -> 317 across the 00:09-04:05
--   sweeps while every derivation run processed only {"August 2026"}. The result, measured by owner SQL
--   the same morning:
--       July commcalc.daily_sales_feed : 3,787 distinct trans_ids
--       July commcalc.raw_sales        : 3,744 distinct trans_ids
--       => 45 transactions in the feed and NOT in the authoritative monthly basis
--   raw_sales is what a CLOSED month is paid from, so those 45 are missing from every July report and
--   would be UNPAID in a July recompute. This recurs at every month boundary, for every tenant on the
--   feed.
--
-- WHAT THIS COLUMN DOES
--   Holds ONE jsonb object per tenant. NULL / missing row = the code default, which is documented in
--   backend/app/modules/commcalc/sales_derive.py and is:
--       {"enabled": true, "days": 3, "retain": null}
--     enabled — grace re-derive on/off for this tenant. false (or days = 0) restores the pre-fix
--               behaviour exactly: current month only, forever.
--     days    — how many days into the new month the prior month is still re-derived (3 = the 1st, 2nd
--               and 3rd). Clamped to 0..15 in code: a "grace window" longer than half a month is not a
--               grace window, it is a standing re-derive of a month people are already being paid from.
--     retain  — the shrink guard for GRACE runs only (null = the normal 0.85). A tenant that hand-uploads
--               the authoritative 78-column monthly file for a closed month can set 1.0, which refuses
--               any grace run that would leave the month with fewer lines than it found. Clamped to
--               0.85..1.0 so a typo can never WEAKEN the guard.
--
-- THIS MIGRATION MOVES NO MONEY AND NO DATA. It adds one nullable column. Deriving sales does not
-- recompute anything — a closed month's pay still only changes when a human runs Calculate.
--
-- UNTIL THIS RUNS: sales_derive.load() catches the missing column and returns the code default, so the
-- month-boundary fix is already working; running this migration only makes the window TUNABLE per
-- tenant (RULE TWO) and makes the editor on /commcalc/sales-derive able to save.
--
-- ADDITIVE + IDEMPOTENT. Safe to re-run. No GRANT, no CREATE POLICY, no anon/authenticated (contract §5).

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS sales_derive_grace JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.sales_derive_grace IS
  'Month-boundary grace window for the automatic feed->raw_sales derivation. NULL = code default '
  '{"enabled":true,"days":3,"retain":null}. days = days into the new month the PRIOR month is still '
  're-derived (0..15; 0 or enabled=false restores current-month-only). retain = shrink guard for grace '
  'runs only (null = 0.85, clamped 0.85..1.0). Deriving never recomputes pay.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 266 complete — commission_org_config.sales_derive_grace (per-tenant month-boundary '
       're-derive window; nullable, code-defaulted, moves no money and no data)' AS status;
