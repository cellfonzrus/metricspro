-- 1009_commission_bucket_registry.sql — the canonical ledger's BUCKETS become per-org CONFIG ROWS.
--
-- OWNER DIRECTIVE 2026-09-20: "need to add the following buckets: Chargebacks, Vendor fee, Misc
-- charges", then — shown a fixed list of eight — "again this is not to be hardcoded, the user should
-- be able to define the buckets and also assign the bucket to a bigger category on the P&L as the
-- others are linked as per the index."
--
-- WHAT THIS IS. One row per bucket per org: its key (what `commcalc.commission_ledger.category`
-- holds per line), its label, its KIND (earned | deduction — which decides the sign a line books
-- with), its order on every screen, whether it is active, the neutral HINT WORDS the onboarding
-- intake matches a statement's labels against to pre-place them (a guess the person confirms),
-- and its link to the P&L chart (`pl_line_key` — a key of account/coa.PL_SPEC, the SAME chart every
-- other booking names; a tenant picks the line from that chart, never a parallel mapping).
--
-- The house org (00000000-0000-0000-0000-000000000001) seeds today's five buckets plus the three
-- the owner asked for. A tenant INHERITS the house rows and OVERRIDES per key with its own row
-- (add / rename / reorder / deactivate / change the hint words or the P&L line) — the mig-207
-- `report_pull_map` pattern (RULE TWO: config, never code).
--
-- WHAT IS DELIBERATELY NOT HERE. No new amount column on commcalc.commission_ledger. The five
-- column-backed buckets (commission / spiff / equipment_rebate / residual_monthly / autopay_residual,
-- mig 071) keep their columns and every existing reader of them is byte-identical; a line booked
-- to ANY bucket carries its signed amount in `payout_total` and its bucket key in `category`, which
-- is how a tenant-defined bucket needs no schema change. The three column-less house buckets and
-- every tenant-defined one are read by (category, payout_total) — commission_ledger.summarize,
-- onboarding_intake.bucket_totals and /commission-ledger/by-rep all do.
--
-- THE SIGN RULE (proven DB-free by backend/harness_commission_ledger_sign.py §I). Under the amount
-- column's declared convention (mig 1006/1008), a line's CANONICAL amount is raw × payout_sign.
--   · an EARNED bucket books +|amt| for a line pointing the earned way and −|amt| for a netted
--     reversal (unchanged since #246);
--   · a DEDUCTION bucket books the canonical SIGNED amount whichever way the line points — a
--     −1,500 deactivation books −1,500, a fee written as a positive charge under `payout_negative`
--     books −|fee|, a fee REFUND books positive (it reduces the deduction). Never abs().
-- So Σ over every active bucket (earned +, deductions signed) = the statement's own total,
-- whatever buckets the tenant chose. `payout_total` is the NET contribution of a line to that
-- total, which is what it has always summed to.
--
-- THE P&L LINK. `pl_line_key` names a line of account/coa.PL_SPEC (validated in code against the
-- chart; a typo cannot invent a P&L line). Booking ledger buckets INTO the P&L is the finance
-- module's job (account/coa.build_inputs) and is NOT wired by this migration — the mapping is
-- recorded here so that when finance books the ledger, each bucket rolls up to the line its tenant
-- chose. House defaults below are the obvious lines and are an owner/finance decision to change:
-- a config edit, never code.
--
-- ADDITIVE, IDEMPOTENT, NO-OP FOR EVERY EXISTING ROW. Nothing is recomputed: no ledger row changes
-- category, no rule changes target. Written and NOT applied: until it runs the code reads the
-- built-in house defaults for DISPLAY only, and a commit or import that would book a line to a
-- bucket with no column of its own is REFUSED naming this file — never silently re-bucketed.
--
-- REVERT: DROP TABLE IF EXISTS commcalc.commission_bucket;
--         NOTIFY pgrst, 'reload schema';
--         (Only after re-filing any ledger row whose category is a column-less bucket key; the code
--          then falls back to the five column-backed buckets for display and refuses new bookings.)

CREATE TABLE IF NOT EXISTS commcalc.commission_bucket (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  key           TEXT NOT NULL,                       -- what commission_ledger.category holds; [a-z0-9_]
  label         TEXT NOT NULL,
  kind          TEXT NOT NULL DEFAULT 'earned',      -- earned | deduction (decides the sign a line books)
  sort_order    INT  NOT NULL DEFAULT 100,
  is_active     BOOLEAN NOT NULL DEFAULT true,
  hint_words    TEXT[] NOT NULL DEFAULT '{}',        -- neutral words the intake matches labels against
  pl_line_key   TEXT,                                -- a key of account/coa.PL_SPEC (validated in code)
  is_builtin    BOOLEAN NOT NULL DEFAULT false,      -- the five column-backed keys (mig 071): key immutable
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, key),
  CONSTRAINT commission_bucket_kind_ck CHECK (kind IN ('earned', 'deduction')),
  CONSTRAINT commission_bucket_key_ck  CHECK (key ~ '^[a-z][a-z0-9_]{0,63}$')
);
CREATE INDEX IF NOT EXISTS commission_bucket_org ON commcalc.commission_bucket (org_id, sort_order);

ALTER TABLE commcalc.commission_bucket ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='commission_bucket' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.commission_bucket FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
GRANT ALL ON commcalc.commission_bucket TO service_role;

COMMENT ON TABLE commcalc.commission_bucket IS
  'The canonical commission ledger''s buckets, per org (house org = defaults, tenant row overrides per key). key = commission_ledger.category; kind decides the sign a line books (earned: +|amt| earned / -|amt| netted reversal; deduction: the canonical signed amount, never abs()). hint_words = the neutral vocabulary the onboarding intake pre-places labels with (a guess the person confirms). pl_line_key = the account/coa.PL_SPEC line this bucket rolls up to on the P&L (recorded here; booked by finance). is_builtin = one of the five column-backed keys of mig 071, whose key cannot change.';

-- ── HOUSE DEFAULTS — the five buckets every tenant has today (column-backed, mig 071) and the three
--    the owner asked for. Neutral English hint words only: no carrier, tenant or product name.
--    ON CONFLICT DO NOTHING so a re-run never overwrites an edited house row.
INSERT INTO commcalc.commission_bucket (org_id, key, label, kind, sort_order, is_active, hint_words, pl_line_key, is_builtin) VALUES
  ('00000000-0000-0000-0000-000000000001', 'commission',       'Commission',                    'earned',    10, true,
     ARRAY['commission','activation','upgrade','price plan','new account','add-a-line','add a line','new line'], 'carrier_comm', true),
  ('00000000-0000-0000-0000-000000000001', 'spiff',            'Spiff',                         'earned',    20, true,
     ARRAY['spiff','spf','incentive','bonus','bounty'], 'carrier_comm', true),
  ('00000000-0000-0000-0000-000000000001', 'equipment_rebate', 'Equipment rebate',              'earned',    30, true,
     ARRAY['rebate','subsidy','promo','trade-in','trade in','trade'], 'device_rebate', true),
  ('00000000-0000-0000-0000-000000000001', 'residual_monthly', 'Residual / monthly incentives', 'earned',    40, true,
     ARRAY['residual','monthly incentive'], 'mi_income', true),
  ('00000000-0000-0000-0000-000000000001', 'autopay_residual', 'Auto Pay residual',             'earned',    50, true,
     ARRAY['autopay residual','auto pay residual','auto-pay residual','autopay','auto pay','auto-pay'], 'mi_income', true),
  ('00000000-0000-0000-0000-000000000001', 'chargebacks',      'Chargebacks',                   'deduction', 60, true,
     ARRAY['chargeback','charge back','charge-back','deactivation','deactivate','deact','clawback','claw back','claw-back','reversal','reversed'], 'chargebacks', false),
  ('00000000-0000-0000-0000-000000000001', 'vendor_fee',       'Vendor fee',                    'deduction', 70, true,
     ARRAY['service fee','vendor fee','fee'], 'vip_fees', false),
  ('00000000-0000-0000-0000-000000000001', 'misc_charges',     'Misc charges',                  'deduction', 80, true,
     ARRAY['adjustment','adjust','misc','miscellaneous','charitable','contribution','other charge'], 'store_opex', false)
ON CONFLICT (org_id, key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 1009 complete — commcalc.commission_bucket ('
       || (SELECT count(*) FROM commcalc.commission_bucket WHERE org_id = '00000000-0000-0000-0000-000000000001')
       || ' house bucket(s); no ledger row and no rule changed)' AS status;
