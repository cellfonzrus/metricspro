-- 506_closing_expense_category.sql — mod-retail-ops band 500-599.
--
-- Envelope Expense Management (EEP), Feature 1 — OWNER DIRECTIVE 2026-08-04. See the cross-module
-- spec: /workspaces/commcalc/docs/specs/envelope-expense-payout.md (binding contract; do not drift).
--
-- Two tables:
--   commcalc.closing_expense_category — org-scoped, tenant-configurable expense categories. `kind`
--     drives behaviour app-side: 'payroll' | 'commission' | 'expense'. Lazy-seeded on first GET when
--     empty (backend/app/modules/closing/expense_config.py), same pattern as closing_tender_def
--     (mig 111) / closing_count_field_def (mig 501) / commcalc.item_category_config — 5 presets:
--     Salary(payroll), Commission(commission), Petty Expenses / Office Expenses / Supplies (expense).
--   commcalc.closing_expense — the categorized line items entered from the daily closing flow (rep
--     submit + DM approve). closing_row_id is NULLABLE (a line may be tied to one rep's own closing
--     row, or entered by a manager against just a store+date with no single rep). category_kind/
--     category_name are SNAPSHOTTED at insert time (a later rename/kind-change of the category must
--     never retroactively change how an already-posted line item behaved or displayed).
--
-- MONEY DOCTRINE (spec's "Architecture" section, binding): closing_expense rows do NOT themselves
-- mutate any payout number. payroll/commission-kind lines record advances (via mod-people/
-- mod-commission ledgers), never P&L; only 'expense'-kind APPROVED lines roll up to the P&L (Store
-- Expenses system-line, source_key = 'closing_expense:<category-id>') — see router.py's rollup poster.
--
-- SAFE: additive + idempotent (IF NOT EXISTS everywhere). DEGRADES GRACEFULLY: every reader in
-- expense_config.py / router.py wraps these two tables in try/except and returns the coded-default
-- category list / an empty expense list until this migration runs — nothing else breaks.
--
-- RLS POSTURE (AGENT_CONTRACT §5, locked down 2026-07-28): RLS enabled, ZERO policies, GRANT ALL to
-- service_role only. No anon/authenticated grant.

CREATE TABLE IF NOT EXISTS commcalc.closing_expense_category (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'expense',   -- 'payroll' | 'commission' | 'expense'
  is_preset   BOOLEAN NOT NULL DEFAULT false,     -- one of the 5 seeded defaults (still renameable)
  is_active   BOOLEAN NOT NULL DEFAULT true,
  sort_order  INT NOT NULL DEFAULT 100,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS closing_expense_category_org_idx
  ON commcalc.closing_expense_category (org_id, is_active);
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'closing_expense_category_kind_chk') THEN
    ALTER TABLE commcalc.closing_expense_category
      ADD CONSTRAINT closing_expense_category_kind_chk CHECK (kind IN ('payroll','commission','expense'));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS commcalc.closing_expense (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,
  store_code       TEXT,
  close_date       DATE NOT NULL,
  closing_row_id   UUID,                         -- nullable FK-by-convention to commcalc.daily_closing.id
  category_id      UUID REFERENCES commcalc.closing_expense_category(id),
  category_kind    TEXT NOT NULL,                 -- snapshot of category.kind at insert time
  category_name    TEXT NOT NULL,                 -- snapshot of category.name at insert time
  amount           NUMERIC NOT NULL DEFAULT 0,
  employee_id      TEXT,                          -- storeops.employees.id; required for payroll/commission kinds
  employee_name    TEXT,                          -- display snapshot
  description      TEXT,
  status           TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'approved' | 'rejected'
  approved_by      TEXT,
  approved_at      TIMESTAMPTZ,
  paid             BOOLEAN NOT NULL DEFAULT false,    -- marked true once the DM execution page pays it out
  paid_at          TIMESTAMPTZ,
  withdrawal_id    UUID,                              -- the envelope_withdrawal (mig 507) that paid it, if any
  pl_pushed_at     TIMESTAMPTZ,                        -- last time an 'expense'-kind line was rolled into the P&L
  created_by       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS closing_expense_org_date_idx
  ON commcalc.closing_expense (org_id, close_date, store_code);
CREATE INDEX IF NOT EXISTS closing_expense_row_idx
  ON commcalc.closing_expense (closing_row_id);
CREATE INDEX IF NOT EXISTS closing_expense_status_idx
  ON commcalc.closing_expense (org_id, status);
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'closing_expense_kind_chk') THEN
    ALTER TABLE commcalc.closing_expense
      ADD CONSTRAINT closing_expense_kind_chk CHECK (category_kind IN ('payroll','commission','expense'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'closing_expense_status_chk') THEN
    ALTER TABLE commcalc.closing_expense
      ADD CONSTRAINT closing_expense_status_chk CHECK (status IN ('pending','approved','rejected'));
  END IF;
END $$;

ALTER TABLE commcalc.closing_expense_category ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.closing_expense          ENABLE ROW LEVEL SECURITY;
-- RLS posture: backend service role only (AGENT_CONTRACT §5). No policies, no anon/authenticated grants.
GRANT ALL ON commcalc.closing_expense_category TO service_role;
GRANT ALL ON commcalc.closing_expense          TO service_role;

NOTIFY pgrst, 'reload schema';
SELECT '506 complete — commcalc.closing_expense_category + commcalc.closing_expense ready' AS status;
