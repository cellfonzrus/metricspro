-- 1077_store_prospect_eval.sql
-- ────────────────────────────────────────────────────────────
-- EVALUATE A STORE WE DO NOT OWN YET, WITHOUT CREATING IT (owner directive 2026-10-12,
-- index §COMMCALC-03)
--
-- Owner: "need to make a module for evaluating the store sales for the store we need to take over,
-- but without creating the actual stores but upload the ma comm, max tx and handset prdering data,
-- import the expenses from our average expenses, keep the rent as what the actual rent is for the
-- new store and calculate the p&l also need to fillter out the family plans with more than 2 lines
-- from these reports for each month to assess if fraud is going on and cjhecking which phones are
-- sold on those, create a sub report in that menu to take out those phones from profitability or
-- leave them. thisis platform wide but situation at hand is for total, nothing hardocded and
-- everything should be clear with analysis"
--
-- WHY A PROSPECT CANNOT BE A STORE ROW. The cheap implementation is to create the store, upload its
-- feeds into the live raw_* tables and read the P&L. That is exactly what the owner forbade, and for
-- a structural reason: a commcalc.store_mapping row is reachable by EVERY live surface — the
-- consolidated P&L, the GP report, the expense matrix, the commission engine, payroll, the
-- dashboards and the targets. A store we are only CONSIDERING would move the company's real books
-- and real payouts, and would keep moving them after we walked away from the deal. There is also no
-- staging database to rehearse it in.
--
-- So a prospect is a row in store_prospect, and its uploaded rows land in prospect_feed_row, which
-- NO live reader reads. The evaluation is computed on demand and stored nowhere a statement can
-- find it; nothing in this feature writes a raw_* table, a store row or an account_statements
-- snapshot.
--
-- ONE LANDING TABLE, NOT THREE. The obvious shape — prospect_ma_commission, prospect_ma_daily_tx,
-- prospect_ma_fulfillment — is three sibling derivations of the three live tables, which is the
-- duplicate defect the house index rules forbid: three schemas to keep in step with three live ones
-- forever. prospect_feed_row holds ONE row per uploaded line as JSONB in the live table's own
-- COLUMN SHAPE, so the existing booking one-homes (account/ma_store_pnl.ma_commission_bookings,
-- ma_tx_bookings, account/device_cogs.ma_unit_price_from_rows / ma_sold_cost_from_rows) read a
-- prospect row and a live row identically, with no translation layer to drift. A new prospect feed
-- is a registry entry in commcalc/prospect_eval.FEED_REGISTRY, never a migration.
--
-- RULE TWO. The five knobs below are per-org config columns with house defaults resolved in code
-- (commcalc/prospect_eval.resolve_config), seeded NULL so an org that declares nothing behaves
-- exactly as the house defaults say. No carrier, tenant or product name appears anywhere here.
--
-- Additive and idempotent. Nothing existing changes; no existing row is read differently. Applying
-- this changes no figure on any live report — the feature has no live reader at all.
--
-- MONEY: this migration moves no money. It creates two empty tables and five NULL config columns.
--
-- REVERT:
--   DROP TABLE IF EXISTS commcalc.prospect_feed_row;
--   DROP TABLE IF EXISTS commcalc.store_prospect;
--   ALTER TABLE commcalc.commission_org_config
--     DROP COLUMN IF EXISTS prospect_account_key_columns,
--     DROP COLUMN IF EXISTS prospect_family_line_threshold,
--     DROP COLUMN IF EXISTS prospect_expense_basis_months,
--     DROP COLUMN IF EXISTS prospect_rent_expense_names,
--     DROP COLUMN IF EXISTS prospect_expense_average_basis;
-- ────────────────────────────────────────────────────────────

-- ── 1. THE PROSPECT ───────────────────────────────────────────────────────────────────────────────
-- Deliberately NOT commcalc.store_mapping and deliberately carrying no store_code: a prospect has
-- no identity any live reader could resolve, which is the safety property. `actual_rent_monthly` is
-- the owner's one explicit substitution — the only cost that is a fact about THIS address and is
-- known before the deal closes.
CREATE TABLE IF NOT EXISTS commcalc.store_prospect (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 UUID NOT NULL,
  name                   TEXT NOT NULL,
  address                TEXT,
  carrier_id             UUID,
  -- The prospect's own rent. NULL means "not entered yet" and is reported as such; the averaged
  -- rent then stands and is LABELLED as an average, never silently presented as the real figure.
  actual_rent_monthly    NUMERIC,
  -- Per-prospect overrides of the org config. NULL ⇒ inherit the org row, which in turn falls back
  -- to the house defaults in prospect_eval.resolve_config.
  expense_basis_months   INT,
  family_line_threshold  INT,
  exclusion_mode         TEXT,
  status                 TEXT NOT NULL DEFAULT 'evaluating',
  notes                  TEXT,
  -- NAMED WITH THE `_auth_id` SUFFIX DELIBERATELY. The house reserves the bare `created_by` /
  -- `uploaded_by` for TEXT actor columns, and harness_actor_uid_lock.py resolves a UUID actor
  -- column BY NAME across the whole repo: declaring the bare name UUID here made five
  -- pre-existing TEXT writers in core / helpdesk / marketing / pos read as non-UUID literals
  -- into a UUID column. A UUID actor column carries the suffix (platform_notice
  -- .created_by_auth_id, restore_drill.recorded_by_auth_id); this one holds `_caller_uid`'s
  -- auth uid, or NULL when nobody signed in resolves.
  created_by_auth_id     UUID,
  updated_at             TIMESTAMPTZ DEFAULT NOW(),
  created_at             TIMESTAMPTZ DEFAULT NOW(),
  -- One prospect per name per org. A second row for the same name would be two evaluations of one
  -- deal, drifting apart exactly as two P&L paths would.
  UNIQUE (org_id, name)
);

CREATE INDEX IF NOT EXISTS store_prospect_org ON commcalc.store_prospect (org_id, status);

-- The status vocabulary. 'evaluating' while the numbers are being worked, then the decision, so a
-- prospect that was passed on stays on the record with its figures instead of being deleted.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'store_prospect_status_chk'
      AND conrelid = 'commcalc.store_prospect'::regclass
  ) THEN
    ALTER TABLE commcalc.store_prospect
      ADD CONSTRAINT store_prospect_status_chk
      CHECK (status IN ('evaluating', 'acquired', 'passed', 'archived'));
  END IF;
END $$;

-- The exclusion vocabulary, mirroring prospect_eval.EXCLUSION_MODES. This only picks which figure
-- the screen OPENS on — both are always computed, so neither number is ever hidden behind it.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'store_prospect_exclusion_chk'
      AND conrelid = 'commcalc.store_prospect'::regclass
  ) THEN
    ALTER TABLE commcalc.store_prospect
      ADD CONSTRAINT store_prospect_exclusion_chk
      CHECK (exclusion_mode IS NULL OR exclusion_mode IN ('keep', 'exclude'));
  END IF;
END $$;

-- Rent is a cost, never a negative, and a zero is a real answer (rent-free family premises) that
-- must be distinguishable from NULL "not entered".
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'store_prospect_rent_chk'
      AND conrelid = 'commcalc.store_prospect'::regclass
  ) THEN
    ALTER TABLE commcalc.store_prospect
      ADD CONSTRAINT store_prospect_rent_chk
      CHECK (actual_rent_monthly IS NULL OR actual_rent_monthly >= 0);
  END IF;
END $$;

-- ── 2. THE ONE PROSPECT LANDING TABLE ─────────────────────────────────────────────────────────────
-- `feed_key` names a commcalc/prospect_eval.FEED_REGISTRY entry; `row` carries the parsed line in
-- the LIVE table's column shape, which is what lets the live booking one-homes read it unchanged.
-- `upload_batch` is what a re-upload replaces: a prospect month is replaced per (prospect, feed,
-- period), the same slice-scoped posture the live ingest uses, so an identical re-upload replaces
-- its own slice only and can never double-count.
CREATE TABLE IF NOT EXISTS commcalc.prospect_feed_row (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  prospect_id  UUID NOT NULL REFERENCES commcalc.store_prospect (id) ON DELETE CASCADE,
  feed_key     TEXT NOT NULL,
  period       TEXT,
  period_month INT,
  period_year  INT,
  row_index    INT,
  row          JSONB NOT NULL DEFAULT '{}'::jsonb,
  upload_batch UUID,
  -- `_auth_id` for the same reason as store_prospect.created_by_auth_id above.
  uploaded_by_auth_id  UUID,
  uploaded_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS prospect_feed_row_slice
  ON commcalc.prospect_feed_row (org_id, prospect_id, feed_key, period);
CREATE INDEX IF NOT EXISTS prospect_feed_row_batch
  ON commcalc.prospect_feed_row (org_id, upload_batch);

-- ── 3. RLS, mirroring the posture of every other commcalc table ───────────────────────────────────
-- Org scoping is enforced in the application layer (every sensitive query is org-scoped and CI
-- checks it), so the policy is permissive here rather than a second, divergent gate.
ALTER TABLE commcalc.store_prospect ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'commcalc' AND tablename = 'store_prospect' AND policyname = 'open_all'
  ) THEN
    CREATE POLICY open_all ON commcalc.store_prospect FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

ALTER TABLE commcalc.prospect_feed_row ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'commcalc' AND tablename = 'prospect_feed_row' AND policyname = 'open_all'
  ) THEN
    CREATE POLICY open_all ON commcalc.prospect_feed_row FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- ── 4. THE DECLARED RULES (RULE TWO) ──────────────────────────────────────────────────────────────
-- All five seeded NULL. prospect_eval.resolve_config supplies the house default for every column it
-- does not find, so this module behaves identically before and after this migration is applied —
-- and an org that declares nothing is on the measured house defaults, not on a silent guess.
ALTER TABLE commcalc.commission_org_config
  -- WHICH COLUMN CARRIES "which subscriber account is this line on", as an ordered ladder read per
  -- ROW (first populated column wins). Measured on the live platform (LuxeLink August 2026,
  -- raw_ma_commission, 1,248 rows): `ban` non-null on 27 rows, `bin` non-null on all 1,248 with
  -- 1,031 distinct values and at most 5 lines on one value, `activation_order` 1,248 distinct (that
  -- is the LINE, not the account). Every one of the 163 multi-line `bin` values resolved to ONE
  -- store on ONE day with consecutive activation orders, all activation_type='Add'. House default
  -- ['ban','bin'].
  ADD COLUMN IF NOT EXISTS prospect_account_key_columns  TEXT[],
  -- MORE THAN how many lines on one account in one month is worth a human look. The owner said
  -- "more than 2", so the threshold is EXCLUSIVE. House default 2.
  ADD COLUMN IF NOT EXISTS prospect_family_line_threshold INT,
  -- How many COMPLETE months the expense average is taken over. House default 3.
  ADD COLUMN IF NOT EXISTS prospect_expense_basis_months  INT,
  -- WHICH expense line is the rent the prospect's own rent replaces. House default ['rent / lease']
  -- — the label the live house expense matrix actually carries on all 28 stores every month since
  -- March 2026. Matched case-insensitively.
  ADD COLUMN IF NOT EXISTS prospect_rent_expense_names    TEXT[],
  -- WHICH denominator the per-store average uses: 'all_stores' (what a TYPICAL store costs) or
  -- 'stores_with_the_line' (what the line costs a store that HAS it). They differ a lot on a sparse
  -- line — live house September 2026, 'Garbage / Waste' is on 6 of 28 stores: $21.43 vs $100.00 —
  -- so BOTH are always reported per line and this only picks which one books. House default
  -- 'all_stores'.
  ADD COLUMN IF NOT EXISTS prospect_expense_average_basis TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'commission_org_config_prospect_basis_chk'
      AND conrelid = 'commcalc.commission_org_config'::regclass
  ) THEN
    ALTER TABLE commcalc.commission_org_config
      ADD CONSTRAINT commission_org_config_prospect_basis_chk
      CHECK (prospect_expense_average_basis IS NULL
             OR prospect_expense_average_basis IN ('all_stores', 'stores_with_the_line'));
  END IF;
END $$;

COMMENT ON TABLE commcalc.store_prospect IS
  'A store being EVALUATED for takeover. Deliberately not a commcalc.store_mapping row and carrying '
  'no store_code, so no live surface (P&L, GP, commission engine, payroll, targets) can reach it. '
  'Index §COMMCALC-03.';
COMMENT ON COLUMN commcalc.store_prospect.actual_rent_monthly IS
  'The prospect''s own rent — the one expense line NOT taken from our stores'' average. NULL = not '
  'entered, which is reported; the averaged rent then stands and is labelled as an average.';
COMMENT ON TABLE commcalc.prospect_feed_row IS
  'THE one landing table for every prospect feed (one row per uploaded line, `row` JSONB in the LIVE '
  'table''s own column shape so account/ma_store_pnl and account/device_cogs read it unchanged). '
  'Three sibling tables would be three derivations of three live ones. Index §COMMCALC-03.';
COMMENT ON COLUMN commcalc.prospect_feed_row.feed_key IS
  'A key of commcalc/prospect_eval.FEED_REGISTRY. A new prospect feed is a registry entry, not a '
  'migration.';

SELECT '1077_store_prospect_eval complete' AS status;
