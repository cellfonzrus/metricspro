-- 260_commission_plan_pay_gate.sql   (mod-commission, band 200-299)
--
-- THE PAY GATE — per-tenant control over WHICH matched lines pay, HOW MANY TIMES, and ON WHAT BASIS.
--
-- ⚠️ MONEY-TOUCHING PACKAGE. READ THIS HEADER BEFORE RUNNING.
--
-- WHY (owner report, luxelink July 2026, transaction 3207 on 2026-07-12)
-- ─────────────────────────────────────────────────────────────────────
-- ONE financed sale paid EIGHT times. Every line of the receipt — the rate plan, the activation fee,
-- the handset, a case, an access charge, a protection plan, a screen protector and a wallet load —
-- was bucketed `edge` and each paid $25.00 ($/unit). The rep collected ~$200 for one sale.
--
-- Two facts combined, and neither is wrong on its own:
--   (a) the `edge` rule was correctly re-keyed on 2026-07-27 to the sale's TENDER (owner: "edge is
--       only of the tender method is tw finnacing"). `tender_type` is a TRANSACTION-level attribute —
--       the POS stamps it on every line of the receipt — so the rule matches all eight lines; and
--   (b) `payout_kind='flat_per_unit'` has always meant "flat per matching LINE". There was no dedup
--       by device or by transaction anywhere in commission_engine.preview().
--
-- OWNER RULING 2026-08-01, verbatim:
--   "one trans id but paying out multiple times for the edge sale, one imie ca be paid only once for
--    the edge sale" and "any accessory or rate plan wil not paid for the edge sale".
--
-- WHAT THIS MIGRATION ADDS — three nullable columns, no table, no seed, no dollar amount.
-- ────────────────────────────────────────────────────────────────────────────────────
--   commcalc.commission_org_config.plan_pay_gate  JSONB   per-tenant switches (all four concerns)
--   commcalc.commission_rule.unit_basis           TEXT    per-rule override, NULL = auto
--
-- Shape of plan_pay_gate (every key optional; anything absent takes the code default):
--   {"unit_basis": {"enabled": true,
--                   "auto_txn_level_fields": ["tender_type"],
--                   "default_basis": "per_device",
--                   "unit_serial_kinds": ["imei"],
--                   "exclude_accessory_units": true,
--                   "no_unit_fallback": "once_per_transaction"},
--    "exclusions": {"enabled": true},
--    "accessory_basis_guard": {"enabled": false, "fallback_basis": "ext_price",
--                              "assumed_margin_pct": null, "clamp_negative": true,
--                              "trigger_flags": ["cost_equals_price","cost_negative",
--                                                "cost_zero","gp_negative"]}}
--
-- 💰 WHAT MOVES ON DEPLOY, STATED PLAINLY
-- ────────────────────────────────────────
-- The CODE DEFAULT *IS* THE OWNER'S RULE, because a default that left the overpayment running would
-- not be a fix. With this migration UNAPPLIED, a `flat_per_unit` rule that matches on `tender_type`
-- pays ONCE PER DEVICE instead of once per line, from the moment the code deploys and the next
-- Calculate runs. Nothing else changes:
--   • a rule keyed on ANY OTHER field is untouched (proved over a 300-seed fuzz);
--   • a `pct_gp` / `pct_mrc` / `pct_price_over_cost` rule is NEVER deduped, on any field — collapsing
--     a %-of-basis rule would delete real dollars rather than stop double-paying them;
--   • a tenant with no commission plans (Boost/house) is byte-identical;
--   • `accessory_basis_guard` is OFF for everybody until a tenant switches it on.
-- Run `GET /commcalc/commission-plans/unit-dedup-impact/{period}` FIRST: it runs the real engine twice
-- in memory and returns the per-rep before / after / delta, writing nothing.
--
-- A TENANT CAN DECLINE IT ENTIRELY: set plan_pay_gate = '{"unit_basis":{"enabled":false}}' (or empty
-- auto_txn_level_fields, or set the rule's own unit_basis='per_line') and the previous behaviour is
-- restored exactly.
--
-- UNTIL THIS RUNS: plan_pay_gate.load_gate_config() swallows the missing column and returns the code
-- defaults; commission_rule.unit_basis reads as absent (= auto). Every page renders. Running this
-- migration changes NO behaviour by itself — it only makes the behaviour tenant-editable (RULE TWO).
--
-- ADDITIVE + IDEMPOTENT + safe to re-run. No new table, so no RLS/GRANT clause is required (both
-- target tables carry their own posture). No GRANT, no CREATE POLICY, no anon/authenticated (§5).

DO $$
BEGIN
  IF to_regclass('commcalc.commission_org_config') IS NULL THEN
    RAISE NOTICE 'commcalc.commission_org_config missing — run migration 201 first; 260 partial.';
  ELSE
    ALTER TABLE commcalc.commission_org_config
      ADD COLUMN IF NOT EXISTS plan_pay_gate JSONB;
  END IF;

  IF to_regclass('commcalc.commission_rule') IS NULL THEN
    RAISE NOTICE 'commcalc.commission_rule missing — run migration 059 first; 260 partial.';
  ELSE
    ALTER TABLE commcalc.commission_rule
      ADD COLUMN IF NOT EXISTS unit_basis TEXT;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'commission_rule_unit_basis_chk') THEN
      ALTER TABLE commcalc.commission_rule
        ADD CONSTRAINT commission_rule_unit_basis_chk
        CHECK (unit_basis IS NULL OR unit_basis IN ('per_line', 'per_device', 'per_transaction'));
    END IF;
  END IF;
END $$;

COMMENT ON COLUMN commcalc.commission_org_config.plan_pay_gate IS
  'Per-tenant PAY GATE switches (mig 260): unit_basis (one payment per device/transaction for a rule '
  'keyed on a transaction-level field), exclusions (the payout-exclusion map, mig 261), and '
  'accessory_basis_guard (pay %% of PRICE when GP is not a believable basis; OFF by default). NULL = '
  'the code defaults, which implement the owner ruling of 2026-08-01. MONEY-TOUCHING: takes effect on '
  'the next POST /commcalc/calculate.';

COMMENT ON COLUMN commcalc.commission_rule.unit_basis IS
  'How often THIS rule pays within one transaction (mig 260). NULL = auto: a flat_per_unit rule keyed '
  'on a transaction-level field (default: tender_type) pays once per DEVICE, everything else pays per '
  'LINE as before. ''per_line'' | ''per_device'' | ''per_transaction''. Ignored for %%-of-basis payout '
  'kinds — collapsing those would delete dollars, not stop double-paying them.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 260 complete — commission_org_config.plan_pay_gate + commission_rule.unit_basis. '
       'Both NULL: the tenant inherits the code defaults (the owner ruling of 2026-08-01). Nothing '
       'is seeded and no dollar amount exists anywhere in this file.' AS status;
