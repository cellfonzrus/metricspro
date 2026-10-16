-- 1087_pl_ma_month_spiff_source_by_rung.sql
-- WHICH FEED STATES WHICH MONTH-OF-LIFE RUNG — per org, as config.
--
-- Owner report 2026-10-16, verbatim:
--   "something is wrong on teh m1 commmission it cannot be that low, jsut a rule of thumb , if the
--    store does 100 activation and an mrc of 50, teh first month payout is 25, so the month shoudl
--    have 2500 plus any spiff etc, recheck"
-- and his DECISION on the card that followed the measurement (2026-10-16 22:33 UTC), option A,
-- "Earned first month": report the first month when EARNED, while the trailing months stay on cash.
--
-- WHY. `pl_ma_month_spiff_source` (mig 314) answers "which feed is this org's month-spiff money"
-- with ONE answer for ALL rungs. It was built to stop one payment booking twice, on the assumption
-- that both feeds carry the same money at the same time. MEASURED (index §COMMCALC-17), they do not:
--   • the commission SHEET states the FIRST month at activation and almost nothing else —
--     September 2026: 410 of 482 rows carry a non-zero spiff_m1, 26 carry any of spiff_m2..m6;
--   • the daily CASH feed states the trailing months promptly and the first month RETROACTIVELY —
--     that family's row count collapses 582 -> 467 -> 139 -> 60 over Jun->Sep while activations only
--     halve, and cumulatively its first month is 60.8% of the earned first month and NEVER above it:
--     the same money with a lag, not a second payment.
-- So a single-feed switch drops a whole rung whichever way it is set. September, on the org that
-- reported it: the sheet says the first month was $7,899.24 (48.3% of MRC — the owner's rule of
-- thumb exactly) and the books showed $1,237.99, because that is the other feed.
--
-- WHAT THIS MIGRATION DOES: adds ONE column, a per-rung map. Nothing else. RULE TWO — no carrier,
-- tenant, product or month count is named in code; the behaviour is this config row with the house
-- answer as the default. The reader is `account/ma_store_pnl.load_config`, and the ONE home that
-- answers "which feed states this rung" is `ma_store_pnl.month_spiff_source_for_rung`; both booking
-- functions ask it through `books_month_spiff_from`, so exactly one feed books a given rung and the
-- other feed's rows for that rung book NOTHING. A caller that compares the switch to a feed name
-- itself fails the build (`backend/harness_ma_income_one_home_guard.py` CHECK 2d).
--
-- THE MAP. jsonb, `{rung key: feed}`. A rung key is the vocabulary commcalc.commission_legs already
-- owns: a ladder key ('1','2',…'12','13'…), a bucket name ('m1' / 'trailing' / 'unsplit'), or
-- 'default'. A feed is 'commission_sheet' or 'daily_tx'. Resolution is most-specific-first: the
-- exact rung, then its bucket, then 'default', then the mig-314 scalar. An unknown key or a feed
-- name that is a typo is DROPPED and that rung keeps the scalar — a typo can never silently restate
-- a P&L line.
--
-- MONEY IMPACT. The DEFAULT is '{}' — every rung reads the mig-314 scalar, which is exactly what has
-- always happened, so applying this file moves NO money for any org and reproduces today's single
-- value byte-identically (pinned by name: backend/harness_month_leg_resolution.py §H6-H9).
-- The owner's decision is the second statement below, for ONE org, and it moves money on that org's
-- P&L: the carrier-commission line's FIRST-MONTH rung stops reading the cash that has arrived and
-- starts reading the sheet's spiff_m1, while every trailing rung is untouched. MEASURED through the
-- real booking functions on that org's live feeds (read-only, 2026-10-16):
--     June 2026       26,999.54 -> 29,229.41   (+2,229.87)
--     July 2026       28,830.94 -> 41,158.45  (+12,327.51)
--     August 2026     98,656.37 -> 115,878.31 (+17,221.94)   first month 6,049.96 -> 23,271.90
--     September 2026 101,270.98 -> 118,077.91 (+16,806.93)   first month 5,666.97 -> 22,473.90
--     October 2026    38,855.99 -> 45,168.90   (+6,312.91)
-- August's first month becomes $23,271.90 — the figure the Gross Profit tile has shown all along.
-- No retroactive first-month cash row books alongside it (the gate is per rung, wholesale: of 2,661
-- first-month cash rows, ZERO carry an order number that appears as an activation order on any of
-- the 5,495 sheet rows, so the feeds cannot be netted row to row — harness §G16-G17).
--
-- PERIODS WITH NO SHEET UPLOAD, STATED NOT HIDDEN. On that org the commission sheet starts in
-- June 2026: `raw_ma_commission` has ZERO rows for February-May 2026. Under this decision those
-- months' first-month rung is UNREPORTED, which is not the same fact as $0.00 — the P&L read-out
-- names it (`ma_store_pnl.rung_source_gaps`, §COMMCALC-16) and there is deliberately NO fallback to
-- the other feed, because that would double-count the rung across periods. Recompute Jun 2026
-- onward; recomputing Feb-May 2026 would read their first month as 0.00.
--
-- Statements are per-period SNAPSHOTS (commcalc.account_statements). Applying this file rewrites no
-- stored row; a period's statement changes only when that period is RECOMPUTED, deliberately.
--
-- Idempotent, additive, no data rewrite. Coexists with every other pl_* column on this table in any
-- order (different columns, same table).

ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS pl_ma_month_spiff_source_by_rung jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN commcalc.commission_org_config.pl_ma_month_spiff_source_by_rung IS
  'mig 1087 (owner decision 2026-10-16, index §COMMCALC-17). PER-RUNG sourcing of the MA month-spiff '
  'money: {rung key: feed}, where a rung key is a ladder key (''1''…''13''…), a bucket name '
  '(''m1'' / ''trailing'' / ''unsplit'') or ''default'', and a feed is ''commission_sheet'' (the '
  'sheet''s spiff columns, EARNED at activation) or ''daily_tx'' (the cash rows, RECEIVED in the '
  'month paid). Resolution: exact rung, then bucket, then ''default'', then the mig-314 scalar '
  'pl_ma_month_spiff_source. An unknown key or an unknown feed is dropped and that rung keeps the '
  'scalar. ''{}'' (the default) = every rung on the scalar, byte-identical to mig 314. Exactly one '
  'feed books a given rung; the other feed''s rows for it book nothing, which is what keeps the '
  'retroactive first-month cash from booking a second time. Read by '
  'account/ma_store_pnl.load_config; resolved by ma_store_pnl.month_spiff_source_for_rung.';

-- THE OWNER'S DECISION, for the one org that reported it (2026-10-16 22:33 UTC, option A "Earned
-- first month"). Per-org CONFIG ROW, not per-tenant code: every other org, house default included,
-- keeps '{}' and does not move. The trailing rungs are left to the scalar deliberately — naming them
-- here would freeze them against a later change of that scalar.
UPDATE commcalc.commission_org_config
   SET pl_ma_month_spiff_source_by_rung = '{"m1": "commission_sheet"}'::jsonb
 WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';

-- CONFIRMING SELECT — the column exists, the house default is empty for everyone else, and exactly
-- one org states its first month on the sheet.
SELECT org_id,
       pl_ma_month_spiff_source                        AS scalar_source,
       pl_ma_month_spiff_source_by_rung                AS by_rung,
       pl_ma_month_spiff_source_by_rung -> 'm1'        AS first_month_feed
  FROM commcalc.commission_org_config
 ORDER BY (pl_ma_month_spiff_source_by_rung = '{}'::jsonb), org_id;

-- REVERT:
--   -- the decision only (keeps the mechanism, returns that org's books to the mig-314 scalar for
--   -- every rung — the first month then reads the cash feed again):
--   UPDATE commcalc.commission_org_config
--      SET pl_ma_month_spiff_source_by_rung = '{}'::jsonb
--    WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c';
--   -- the column as well (every org then reads the scalar, which is this file's own default):
--   ALTER TABLE commcalc.commission_org_config
--     DROP COLUMN IF EXISTS pl_ma_month_spiff_source_by_rung;
-- Neither revert rewrites a money row, and no period's stored statement changes until that period is
-- recomputed.
