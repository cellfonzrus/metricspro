-- 312_ma_payment_rules_and_discrepancy_attribution.sql
-- mod-commission · band 200–299 spill → 312 (follows 311). Additive + idempotent + safe to re-run.
--
-- WHAT IT IS FOR (owner spec 2026-09-01, "B2B ↔ MA TX / MA Commission reconciliation", Phase C).
-- "MA TX can also be used to verify the sales ingested via the B2B … If an activation has been rung
-- out in B2B but not paid in MA Commission / MA TX then it should fall into the discrepancy report
-- for that month, and full analysis to be attributed to why it did not get paid as per the business
-- rules — if business rules are not present then they should be uploaded; if the activation is still
-- not paid but no business rules exist it should still appear in the report without a reason."
--
-- Two pieces:
--   a. commcalc.ma_payment_rule — the owner's UPLOADABLE "business rules" store. A rule EXPLAINS why
--      an activation is legitimately unpaid (e.g. BYOD SIM kits carry no MA payout). First match by
--      priority wins; NO match = the activation still reports, with the honest reason
--      'no business rule configured' (the ma_product_class 'unmapped' idiom from mig 254 — loud,
--      never silently guessed). Engine: backend/app/modules/commcalc/ma_recon.py.
--   b. commcalc.discrepancy_results — canonical DDL + the MA-attribution columns. This table
--      PRE-DATES the migration series (created by console; written by discrepancy_engine.py since),
--      so the CREATE below is guarded (IF NOT EXISTS = a no-op on prod) purely so a fresh
--      environment gets the same shape, and the new columns are ADD COLUMN IF NOT EXISTS so the
--      engine's adaptive writer can degrade to the narrower insert on an unmigrated database.
--
-- CONFIG, NEVER CODE (RULE TWO): the "why it did not get paid" knowledge is rows in ma_payment_rule
-- (per org, carrier-scoped or org-wide), never a branch in code. Match fields/ops are constrained to
-- the audited set below.
--
-- 💰 MONEY POSTURE: running this migration changes NO behaviour on its own. No payout is computed or
-- mutated; the recon engine only WRITES report rows (source='ma') and never touches the Boost
-- engine's rows (scoped delete). Evidence is presence-based (which source has/lacks the activation).
--
-- REVERT:
--   DROP TABLE IF EXISTS commcalc.ma_payment_rule;
--   ALTER TABLE commcalc.discrepancy_results
--     DROP COLUMN IF EXISTS rule_id, DROP COLUMN IF EXISTS rule_key, DROP COLUMN IF EXISTS rule_reason,
--     DROP COLUMN IF EXISTS evidence, DROP COLUMN IF EXISTS source, DROP COLUMN IF EXISTS order_number;
--   (Do NOT drop commcalc.discrepancy_results itself — it pre-dates this migration and holds the
--    Boost engine's rows.)

-- ── a. ma_payment_rule — the uploadable business-rule store ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.ma_payment_rule (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,
  carrier_id       UUID,                                -- NULL = org-wide rule
  rule_key         TEXT NOT NULL,                       -- short slug, the upsert key with org_id
  description      TEXT NOT NULL,                       -- the human "why it does not get paid" text shown in the report
  match_field      TEXT NOT NULL
                   CHECK (match_field IN ('product_desc','department','category','contract_type','sku','plan')),
  match_op         TEXT NOT NULL DEFAULT 'contains'
                   CHECK (match_op IN ('contains','equals','prefix','regex')),
  match_value      TEXT NOT NULL,
  expected_outcome TEXT NOT NULL DEFAULT 'not_paid'
                   CHECK (expected_outcome IN ('not_paid','paid_late','partial')),
  priority         INT NOT NULL DEFAULT 100,            -- ascending; first match wins
  is_active        BOOLEAN NOT NULL DEFAULT true,
  effective_from   DATE,                                -- NULL = no floor
  effective_to     DATE,                                -- NULL = no ceiling
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, rule_key)
);
CREATE INDEX IF NOT EXISTS ma_payment_rule_org_active
  ON commcalc.ma_payment_rule (org_id, is_active, priority);

COMMENT ON TABLE commcalc.ma_payment_rule IS
  'The owner''s uploadable "business rules" store for the B2B ↔ MA discrepancy report (mig 312, Phase C). '
  'A rule EXPLAINS why an activation rung out in B2B is legitimately unpaid in MA Commission / MA TX '
  '(e.g. BYOD SIM kits carry no MA payout). Matched against the SOLD line (raw_sales/daily_sales_feed '
  'columns) in ascending priority — FIRST match wins; matching is case/trim-insensitive. NO match = the '
  'unpaid activation still lands in the report with the literal reason ''no business rule configured'' '
  '(the mig-254 ''unmapped'' idiom — a loud reserved outcome, never a silent guess). Resolved in '
  'backend/app/modules/commcalc/ma_recon.py:match_rules; org rows first, house-org rows inherit after.';
COMMENT ON COLUMN commcalc.ma_payment_rule.carrier_id IS
  'NULL = org-wide rule; set = only sales under that carrier (reserved for per-carrier splits; the '
  'engine today applies org rows to the org''s plan-mode recon).';
COMMENT ON COLUMN commcalc.ma_payment_rule.match_field IS
  'Which SOLD-line column the rule tests — a raw_sales/daily_sales_feed column: product_desc, '
  'department, category, contract_type, sku or plan. A field absent on a row simply does not match.';
COMMENT ON COLUMN commcalc.ma_payment_rule.match_op IS
  '''contains'' (default) | ''equals'' | ''prefix'' | ''regex''. All case/trim-insensitive; a regex '
  'that fails to compile is SKIPPED (never crashes the recon) — fix the rule, the row reports ''no '
  'business rule configured'' meanwhile.';
COMMENT ON COLUMN commcalc.ma_payment_rule.expected_outcome IS
  'What the rule says SHOULD happen: ''not_paid'' (never pays — report status ''info''), ''paid_late'' '
  '(pays in a later statement — report status ''lagged''), ''partial'' (pays a reduced amount — '
  'status ''info''). The row is attributed either way; the outcome only steers which report tab.';
COMMENT ON COLUMN commcalc.ma_payment_rule.effective_from IS
  'Rule applies only to sales whose trans_date is >= effective_from (NULL = no floor) and <= '
  'effective_to (NULL = no ceiling) — so a rule can describe a promo window without ever being deleted.';

-- ── b. discrepancy_results — canonical DDL (console-created on prod; guarded no-op there) ────────
-- Column list mirrors what discrepancy_engine.py has always written (org_id … status, notes, id) so a
-- FRESH environment materialises the table the code expects. On prod this CREATE is a no-op.
CREATE TABLE IF NOT EXISTS commcalc.discrepancy_results (
  id                  BIGSERIAL PRIMARY KEY,
  org_id              UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  period              TEXT NOT NULL,
  imei                TEXT,
  mdn                 TEXT,
  store               TEXT,
  rep_username        TEXT,
  activation_date     DATE,
  activation_type     TEXT,
  device_model        TEXT,
  customer_plan       TEXT,
  commissionable_mrc  NUMERIC,
  bounty_month        INT,
  comp_type           TEXT,
  expected_amount     NUMERIC,
  received_amount     NUMERIC,
  gap                 NUMERIC,
  status              TEXT,
  notes               TEXT,
  created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS discrepancy_results_org_period
  ON commcalc.discrepancy_results (org_id, period);

-- MA-attribution columns (Phase C). Written ADAPTIVELY by ma_recon.persist_results — a backend
-- deployed after this migration degrades to the narrower insert when the columns are absent, and a
-- backend deployed before it simply never writes them. NULL on every Boost-engine row (absence is
-- honest, never guessed).
ALTER TABLE commcalc.discrepancy_results
  ADD COLUMN IF NOT EXISTS rule_id      UUID,
  ADD COLUMN IF NOT EXISTS rule_key     TEXT,
  ADD COLUMN IF NOT EXISTS rule_reason  TEXT,
  ADD COLUMN IF NOT EXISTS evidence     JSONB,
  ADD COLUMN IF NOT EXISTS source       TEXT,
  ADD COLUMN IF NOT EXISTS order_number TEXT;

COMMENT ON TABLE commcalc.discrepancy_results IS
  'The discrepancy report''s row store. Two writers, partitioned by `source`: the legacy Boost engine '
  '(discrepancy_engine.run_discrepancy; source ''boost''/NULL on pre-312 rows) and the B2B ↔ MA recon '
  '(ma_recon.run_ma_discrepancy; source ''ma'', comp_type ''MA_ACTIVATION''). Each writer delete-then-'
  'inserts ONLY its own (org_id, period, source) slice — they never touch each other''s rows. Table '
  'pre-dates the migration series (console-created); mig 312 authors the canonical DDL + attribution.';
COMMENT ON COLUMN commcalc.discrepancy_results.rule_id IS
  'MA recon (mig 312): the commcalc.ma_payment_rule row that explained this unpaid activation — NULL '
  'when no rule matched (status ''open'', notes ''no business rule configured'') or on Boost rows.';
COMMENT ON COLUMN commcalc.discrepancy_results.rule_key IS
  'MA recon: matched rule''s slug, denormalised so the report survives a later rule edit/delete.';
COMMENT ON COLUMN commcalc.discrepancy_results.rule_reason IS
  'MA recon: the matched rule''s description — the human "why it did not get paid" shown in the report.';
COMMENT ON COLUMN commcalc.discrepancy_results.evidence IS
  'MA recon: EVIDENCE-FIRST provenance — which source had / lacked the activation, by name: '
  '{b2b:{trans_id,trans_date,source_table}, ma_commission:{matched,activation_orders,imei}, '
  'ma_tx:{matched,order_number,month_net,activation_order_seen}}. Never a guessed reason.';
COMMENT ON COLUMN commcalc.discrepancy_results.source IS
  'Which engine wrote the row: ''boost'' (discrepancy_engine) | ''ma'' (ma_recon). NULL = a pre-312 '
  'Boost row. Each engine''s persist deletes only its own source slice.';
COMMENT ON COLUMN commcalc.discrepancy_results.order_number IS
  'MA recon: the raw_ma_daily_tx.order_number the two-hop join (serial → raw_ma_commission.'
  'activation_order → order_number) resolved for this activation. IDENTIFIER, never money. NULL = '
  'the MA TX hop did not resolve.';

-- ── RLS (open_all, matching every commcalc table today) ──────────────────────────────────────────
ALTER TABLE commcalc.ma_payment_rule ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='ma_payment_rule' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.ma_payment_rule FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;
ALTER TABLE commcalc.discrepancy_results ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='discrepancy_results' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.discrepancy_results FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 312 complete — ma_payment_rule created; discrepancy_results canonical DDL + MA attribution columns' AS status;
