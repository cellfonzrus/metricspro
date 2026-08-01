-- 265_commission_ma_class_money_wiring.sql
-- MONEY WIRING for the MA product classes — the step 254 deliberately did NOT do.
--
-- WHY (owner go-ahead in chat 2026-08-01, AFTER confirming the classes on /commcalc/ma-product-class:
-- "go ahead and fix, it updated the classes"). Migration 254 built the classification
-- (commcalc.ma_product_class_map: EXACT product_name -> class, with a proposed/confirmed lifecycle) and
-- wired it into NOTHING. This migration adds the two CONFIG tables that let a tenant wire it into
-- exactly two consumers, each behind its own mode flag:
--
--   consumer 'ledger'          commission_ledger.classify() gains match_op='product_class', so a tenant
--                              writes ONE rule per canonical bucket instead of keyword-guessing. Lines
--                              re-bucket on the NEXT ledger refresh/import — nothing changes retroactively.
--   consumer 'carrier_income'  whatif._ma_carrier_income selects its RESIDUAL and AIRTIME legs by the
--                              line's CONFIRMED class instead of order_type, so device sales, wallet
--                              funding and memos stop landing in "airtime margin".
--
-- THIS MIGRATION MOVES $0 ON ITS OWN, AND SO DOES DEPLOYING THE CODE. Both consumers are seeded
-- 'legacy' = today's behaviour to the cent, and absent config reads 'legacy' too, so a tenant that never
-- runs this file is unaffected. The money only moves when a human picks 'class' on
-- /commcalc/ma-class-wiring, after reading the delta panels on that page. Reverting is the same
-- dropdown — no deploy, no SQL, no recompute.
--
-- ONLY CONFIRMED MAPPINGS CLASSIFY MONEY. The reader (ma_class_wiring.confirmed_index) keeps
-- ma_product_class_map rows with status='confirmed' and nothing else — a proposal, including the four
-- the 254 seed flagged 'AMBIGUOUS — please verify', classifies NOTHING and is surfaced instead.
--
-- NOT A CONSUMER, EVER: calculator.py / commission_engine.py / rep_commissions. Rep pay derives from POS
-- sales x Commission Plans, never from the MA daily file.
-- NOT WIRED YET: the P&L / GP leg (account/coa.py, account/residual_subs.py). billpayment and
-- device_sale are revenue WITH A COST, fee is an expense, adjustment_memo is a correction — that leg is
-- sequenced BEHIND the device-cost-recognition policy decision and belongs to mod-finance.
--
-- ADDITIVE + IDEMPOTENT + RLS-ZERO-POLICY: safe to re-run; RLS on, NO policies, NO anon/authenticated
-- grants (contract §5 — all access is via the backend service role). Degrades gracefully: until this
-- runs, every read falls back to the code defaults ('legacy' for both consumers) and the two write
-- endpoints return a 400 naming this file, never a 500.

-- ── 1) the mode flag, one row per (tenant, consumer) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.ma_class_wiring_config (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  consumer      TEXT NOT NULL,                          -- 'ledger' | 'carrier_income'
  mode          TEXT NOT NULL DEFAULT 'legacy',         -- 'legacy' (today) | 'class'
  source_report TEXT NOT NULL DEFAULT 'ma_daily_tx',
  note          TEXT,
  updated_by    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, consumer)
);
CREATE INDEX IF NOT EXISTS ma_class_wiring_config_org ON commcalc.ma_class_wiring_config (org_id);

-- ── 2) which carrier-income leg each class feeds (consumer 2 only) ─────────────────────────────────
-- 'residual' -> the RESIDUAL tile (residual_mi_atu, via the configured residual amount column)
-- 'airtime'  -> the airtime-margin heading (merchant_discount)
-- 'excluded' -> NOT carrier income; it leaves the total and is reported with its dollars.
-- A class with no row here is EXCLUDED (fail-closed: absent never means income).
CREATE TABLE IF NOT EXISTS commcalc.ma_class_income_leg (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  product_class TEXT NOT NULL,
  income_leg    TEXT NOT NULL DEFAULT 'excluded',
  updated_by    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, product_class)
);
CREATE INDEX IF NOT EXISTS ma_class_income_leg_org ON commcalc.ma_class_income_leg (org_id);

-- ── 3) RLS: enabled, ZERO policies, ZERO grants (contract §5) ──────────────────────────────────────
ALTER TABLE commcalc.ma_class_wiring_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.ma_class_income_leg    ENABLE ROW LEVEL SECURITY;

-- ── 4) seed the HOUSE org — LEGACY, i.e. nothing changes ───────────────────────────────────────────
-- Every other tenant inherits the code default, which is the same 'legacy'. Seeding explicitly is only
-- so the admin page shows a row to flip; it is NOT how the behaviour is decided.
INSERT INTO commcalc.ma_class_wiring_config (org_id, consumer, mode, source_report, note)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'ledger',         'legacy', 'ma_daily_tx',
   'seeded by 265 — legacy = the keyword rules, today''s behaviour'),
  ('00000000-0000-0000-0000-000000000001', 'carrier_income', 'legacy', 'ma_daily_tx',
   'seeded by 265 — legacy = order-type selection, today''s behaviour')
ON CONFLICT (org_id, consumer) DO NOTHING;

-- ── 5) seed the HOUSE class -> leg map (INERT until carrier_income mode = 'class') ──────────────────
-- This is the honest mapping in the design of record: residual and billpayment are the two legs and
-- everything else leaves the income total. It matches ma_class_wiring.DEFAULT_INCOME_LEGS exactly.
INSERT INTO commcalc.ma_class_income_leg (org_id, product_class, income_leg)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'residual',        'residual'),
  ('00000000-0000-0000-0000-000000000001', 'billpayment',     'airtime'),
  ('00000000-0000-0000-0000-000000000001', 'commission',      'excluded'),
  ('00000000-0000-0000-0000-000000000001', 'spiff',           'excluded'),
  ('00000000-0000-0000-0000-000000000001', 'device_sale',     'excluded'),
  ('00000000-0000-0000-0000-000000000001', 'protection',      'excluded'),
  ('00000000-0000-0000-0000-000000000001', 'financing',       'excluded'),
  ('00000000-0000-0000-0000-000000000001', 'subsidy',         'excluded'),
  ('00000000-0000-0000-0000-000000000001', 'fee',             'excluded'),
  ('00000000-0000-0000-0000-000000000001', 'wallet',          'excluded'),
  ('00000000-0000-0000-0000-000000000001', 'sim_kit',         'excluded'),
  ('00000000-0000-0000-0000-000000000001', 'adjustment_memo', 'excluded')
ON CONFLICT (org_id, product_class) DO NOTHING;

COMMENT ON TABLE commcalc.ma_class_wiring_config IS
  'Per-tenant, per-consumer mode for the MA product-class money wiring (mig 265). legacy = pre-2026-08-01 behaviour; class = read commcalc.ma_product_class_map (CONFIRMED rows only). Default and seed are both legacy.';
COMMENT ON TABLE commcalc.ma_class_income_leg IS
  'Per-tenant map of MA product class -> What-If carrier-income leg (residual / airtime / excluded). Only consulted while ma_class_wiring_config.consumer=carrier_income has mode=class. A class with no row is excluded.';
