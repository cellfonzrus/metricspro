-- 263_commission_setup_fee_pay.sql   (mod-commission, band 200-299)
--
-- DEVICE SET-UP FEE / ACTIVATION FEE — per-carrier economics + the employee pay item.
--
-- ⚠️ MONEY-TOUCHING CONFIG. THIS MIGRATION MOVES $0 BY ITSELF and cannot move money by itself.
--
-- OWNER DIRECTIVE 2026-08-01, verbatim:
--   "Exceutive MTD sould also have the device set fee collected by the stores as a column , it is
--    already calculated towars commision in boost but there is no commisison being paid out on the
--    luxelink side, also the device set up fee is the same as activation fee on luxelink , an option
--    should be there in commission payout if this has to be a part of commission and what % is used to
--    pay out comp, for example , the boost payd 100% of the device set up fee collected to the dealer
--    and the employee get 10%, but total collects actiuvation fee and payd the dealer 50% of the
--    activation fee collected but the employee is npot being paid anythting right now, need ot build
--    this into the system which can be confgured by the user and the company - if criclet delaer uses
--    metrics pro they should be able to design based on their payouts"
--
-- WHAT IT ADDS — ONE nullable JSONB column. No table, no seed, no percentage.
--   commcalc.commission_org_config.setup_fee_pay  JSONB
--
-- Shape (every key optional; anything absent takes the code default):
--   {"default":    {"include_in_commission": false,
--                   "employee_pct_of_collected": null,
--                   "dealer_share_pct": null,
--                   "match_mode": "legacy_case_sensitive",
--                   "counts_toward_accessory_target": true},
--    "by_carrier": {"<commcalc.carrier.id>": { …same shape, overrides the default… }}}
--
-- ── THE DEFAULTS PAY NOBODY, DELIBERATELY ────────────────────────────────────────────────────────
-- include_in_commission = false and employee_pct_of_collected = NULL. NULL means "no human has stated
-- it" — the engine pays $0 AND RAISES A NAMED WARNING; it never guesses a rate and never substitutes a
-- zero. An explicit 0 is a DECISION and is honoured silently. So:
--   • every plan-driven tenant (luxelink/Total) pays exactly $0 from this item on merge, which matches
--     the owner's own description of today ("the employee is npot being paid anythting right now");
--   • BOOST IS UNTOUCHED. Boost's set-up-fee pay has always come from calculator.py using
--     payout_config.setup_fee_rate (default 10%), and it still does. This column does not feed it.
--
-- ── WHAT THIS MIGRATION DOES *NOT* CHANGE, AND WHY THAT MATTERS ──────────────────────────────────
-- RECOGNITION IS NOT FORKED. Which sale lines ARE the fee still comes from the EXISTING per-tenant
-- list `commcalc.accessory_config.setup_fee_keywords` (migration 217) — the same list the Sales
-- Report, Executive MTD and the accessory-target basis already read. This package's only change there
-- is that calculator.py now READS that list instead of carrying the literal 'Device Setup Charge' in
-- its own source, so a tenant editing the list finally moves their PAY as well as their REPORTS.
--
-- `match_mode` exists because the two historic matchers disagree on CASE: the pay path was
-- case-SENSITIVE, the report path lower-cases both sides. The default `legacy_case_sensitive`
-- reproduces the pay path EXACTLY (byte-identical by construction). Before switching a tenant to
-- `case_insensitive`, run GET /commcalc/setup-fee/recognition-divergence/{period}: it lists every line
-- the two matchers disagree about, with its dollars. An empty list means the switch moves $0.
--
-- ── UNTIL THIS RUNS ──────────────────────────────────────────────────────────────────────────────
-- setup_fee_pay.load_pay_config() swallows the missing column and returns the code defaults — i.e.
-- the fee pays no employee anywhere and the two new Executive-MTD columns render as em-dashes. Every
-- page works. Running the migration only makes the numbers enterable (RULE TWO).
--
-- ── THE OWNER'S NUMBERS ARE SEEDS FOR A HUMAN, NOT SQL ───────────────────────────────────────────
-- Boost = dealer 100% / employee 10%; Total = dealer 50% / employee 0%. They are surfaced in the admin
-- card as a labelled reference and are NOT written by this file. Nothing here pre-fills a percentage,
-- because a percentage is what decides somebody's pay.
--
-- ADDITIVE + IDEMPOTENT + safe to re-run. No new table, so no RLS/GRANT clause is required
-- (commission_org_config carries its own posture). No GRANT, no CREATE POLICY, no anon/authenticated
-- (AGENT_CONTRACT §5).

DO $$
BEGIN
  IF to_regclass('commcalc.commission_org_config') IS NULL THEN
    RAISE NOTICE 'commcalc.commission_org_config missing — run migration 201 first; 263 skipped.';
    RETURN;
  END IF;

  ALTER TABLE commcalc.commission_org_config
    ADD COLUMN IF NOT EXISTS setup_fee_pay JSONB;
END $$;

COMMENT ON COLUMN commcalc.commission_org_config.setup_fee_pay IS
  'Per-tenant / per-carrier DEVICE SET-UP FEE (a.k.a. activation fee) economics, mig 263. NULL = the '
  'code defaults = include_in_commission false and employee_pct_of_collected NULL, i.e. the fee pays '
  'no employee anywhere. Shape: {"default":{…},"by_carrier":{"<carrier id>":{…}}} with keys '
  'include_in_commission, employee_pct_of_collected (fraction; NULL = not stated, pays $0 AND warns; '
  'an explicit 0 is a decision), dealer_share_pct (carrier economics, informational — no employee '
  'payout reads it), match_mode (legacy_case_sensitive | case_insensitive), '
  'counts_toward_accessory_target. WHICH LINES are the fee comes from accessory_config.'
  'setup_fee_keywords (mig 217), NOT from here. Boost pay still comes from payout_config.'
  'setup_fee_rate and is unaffected by this column.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 263 complete — commission_org_config.setup_fee_pay (NULL). No percentage exists '
       'anywhere in this file; no employee pay changed. The fee starts paying only after a human sets '
       'include_in_commission and TYPES a percentage, and then only on the next Calculate.' AS status;
