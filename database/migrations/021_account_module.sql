-- 021_account_module.sql — Account Module (#8 engine · #9 P&L/Balance Sheet · #10 recon).
-- Run this in the Supabase SQL editor (Claude cannot run SQL).
--
-- Adds the multi-company accounting layer:
--   • companies / store_companies  — legal entities + which store belongs to which company
--   • journal_entries              — MANUAL P&L/Balance-Sheet lines (cash, fixtures, owner
--                                    capital, opening retained earnings, taxes, one-offs)
--   • account_statements           — the persisted P&L / Balance-Sheet SNAPSHOTS the pages read
--   • vip_credit_memos             — scraped VIP "Weekly Incentive Credit" memos for the #10 recon
-- ...and BACKFILLS the raw_mi columns the #5b MI/ATU mapper (epay_sweep.map_mi_row) writes but
-- that no prior migration ever created — without these, the next manual MI upload OR the epay
-- sweep would fail on insert (PostgREST rejects unknown columns). mi_activation_date also gives
-- the #10 reconciliation its per-subscriber date for missed-days bucketing.
--
-- All tables here are frontend-readable report/config tables (blanket open_all, same as
-- 008/014) — the backend writes via service_role; the browser reads + (companies/journal) writes.

-- ── raw_mi: add the columns the shared MI/ATU mapper writes (idempotent backfill) ───────────
-- Date-ish columns are TEXT: the mapper reads the report with dtype=str and stores the raw
-- string slice (str(v)[:10]), which is not guaranteed ISO — keep it lossless and parse on read.
ALTER TABLE commcalc.raw_mi
  ADD COLUMN IF NOT EXISTS subscriber_id              TEXT,
  ADD COLUMN IF NOT EXISTS device_serial              TEXT,
  ADD COLUMN IF NOT EXISTS mi_activation_date         TEXT,
  ADD COLUMN IF NOT EXISTS mi_deactivation_date       TEXT,
  ADD COLUMN IF NOT EXISTS residual_transfer_in_date  TEXT,
  ADD COLUMN IF NOT EXISTS residual_transfer_out_date TEXT,
  ADD COLUMN IF NOT EXISTS customer_plan              TEXT,
  ADD COLUMN IF NOT EXISTS base_mrc                   NUMERIC,
  ADD COLUMN IF NOT EXISTS commissionable_mrc         NUMERIC,
  ADD COLUMN IF NOT EXISTS rep_username               TEXT,
  ADD COLUMN IF NOT EXISTS door_type                  TEXT,
  ADD COLUMN IF NOT EXISTS report_month               TEXT;

-- ── companies (legal entities) ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.companies (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  name        TEXT NOT NULL,
  legal_name  TEXT,
  ein         TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS companies_org_name ON commcalc.companies(org_id, name);

-- store_address (the canonical store key used by asset_ledger/rep_commissions/raw_sales) → company
CREATE TABLE IF NOT EXISTS commcalc.store_companies (
  org_id        UUID NOT NULL,
  store_address TEXT NOT NULL,
  company_id    UUID,
  updated_at    TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (org_id, store_address)
);

-- ── manual journal entries (the MANUAL chart-of-accounts lines) ──────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.journal_entries (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  company_id    UUID,                 -- null = applies to the consolidated entity
  store_address TEXT,                 -- optional store scope
  period        TEXT NOT NULL,        -- "June 2026"
  period_month  INT,
  period_year   INT,
  entry_date    DATE,
  statement     TEXT NOT NULL,        -- 'pl' | 'balance_sheet'
  account_type  TEXT NOT NULL,        -- revenue|cogs|opex|other|asset|liability|equity
  account_line  TEXT NOT NULL,        -- the P&L / BS line label (matches the chart of accounts)
  amount        NUMERIC NOT NULL DEFAULT 0,
  memo          TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS journal_entries_scope ON commcalc.journal_entries(org_id, period);

-- ── persisted statement snapshots (pages read these; the engine writes them) ─────────────────
CREATE TABLE IF NOT EXISTS commcalc.account_statements (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  period         TEXT NOT NULL,
  statement_type TEXT NOT NULL,       -- 'pl' | 'balance_sheet'
  scope_key      TEXT NOT NULL,       -- 'consolidated' | 'company:<id>' | 'store:<address>'
  scope_label    TEXT,                -- human label for the scope
  payload        JSONB,               -- the assembled statement (sections + lines + totals)
  narrative      TEXT,                -- plain-English narrative (Claude; '' if key unset)
  model          TEXT,                -- model id that produced it (or 'deterministic')
  crosscheck_ok  BOOLEAN DEFAULT TRUE,-- balance-sheet identity / subtotal cross-check passed
  computed_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS account_statements_uniq
  ON commcalc.account_statements(org_id, period, statement_type, scope_key);

-- ── VIP credit memos (Weekly Incentive Credit) for the #10 recon ─────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.vip_credit_memos (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  credit_memo_id     BIGINT,           -- portal Id
  credit_memo_number TEXT,
  memo               TEXT,             -- "Weekly Incentive Credit - <date range> <year>"
  company_name       TEXT,             -- multi-line; line 2 = store address
  store_address      TEXT,             -- resolved via store_mapping
  grand_total        NUMERIC,
  amount_linked      NUMERIC,
  balance            NUMERIC,
  status             TEXT,
  order_status       TEXT,
  is_xfinity         BOOLEAN DEFAULT FALSE,   -- excluded from the MI/ATU recon
  created_on         DATE,
  memo_start         DATE,             -- parsed start of the memo's date range
  memo_end           DATE,             -- parsed end of the memo's date range
  period             TEXT, period_month INT, period_year INT,
  swept_at           TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS vip_credit_memos_uniq   ON commcalc.vip_credit_memos(org_id, credit_memo_id);
CREATE INDEX IF NOT EXISTS vip_credit_memos_period ON commcalc.vip_credit_memos(org_id, period);
CREATE INDEX IF NOT EXISTS vip_credit_memos_store  ON commcalc.vip_credit_memos(org_id, store_address);

-- ── RLS + grants (blanket open_all, same as sibling commcalc report/config tables) ───────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'commcalc.companies', 'commcalc.store_companies', 'commcalc.journal_entries',
    'commcalc.account_statements', 'commcalc.vip_credit_memos'
  ] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA commcalc TO anon, authenticated;
GRANT ALL ON commcalc.companies          TO anon, authenticated;
GRANT ALL ON commcalc.store_companies    TO anon, authenticated;
GRANT ALL ON commcalc.journal_entries    TO anon, authenticated;
GRANT ALL ON commcalc.account_statements TO anon, authenticated;
GRANT ALL ON commcalc.vip_credit_memos   TO anon, authenticated;

-- seed a Default Company so unmapped stores roll up somewhere
INSERT INTO commcalc.companies (org_id, name, legal_name)
VALUES ('00000000-0000-0000-0000-000000000001', 'Default Company', 'Default Company')
ON CONFLICT (org_id, name) DO NOTHING;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 021 complete — Account Module (companies, journal, statements, credit memos) + raw_mi backfill' as status;
