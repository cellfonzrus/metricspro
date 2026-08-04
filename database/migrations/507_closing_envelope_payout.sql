-- 507_closing_envelope_payout.sql — mod-retail-ops band 500-599.
--
-- Envelope Expense Management (EEP), Feature 2 — OWNER DIRECTIVE 2026-08-04. See the cross-module
-- spec: /workspaces/commcalc/docs/specs/envelope-expense-payout.md (binding contract; do not drift).
-- Depends on migration 506 (commcalc.closing_expense) landing first, but is independently additive —
-- either can run before the other without breaking anything.
--
-- commcalc.envelope_withdrawal — one row per "cash taken out of a specific envelope" event, recorded
--   by the DM execution page (GET /closing/payout-due -> GET /closing/envelope-plan -> DM confirms
--   per envelope -> this table). purpose classifies WHY the cash left the envelope; expense_id links
--   back to the commcalc.closing_expense row it paid (payroll/commission/expense-kind), NULL for a
--   commission/salary payout recorded straight against the sibling ledgers (commission_payout_ledger /
--   storeops.salary_advance_ledger — those two tables are NOT this migration's band; mod-commission /
--   mod-people own them under their own migration numbers per the spec's table-ownership list).
--
-- commcalc.envelope_payout_config — per-org (store_code NULL) or per-store OVERRIDE of what the
--   envelope may fund + on what cadence. Lazy-read default (no row -> take_commission/take_salary/
--   take_expenses all default to what the spec calls out, cadence 'weekly') exactly mirrors every
--   other closing config table's "no row = sane coded default" doctrine.
--
-- MONEY DOCTRINE: this table records CASH MOVEMENTS against ALREADY-COMPUTED numbers (commission
-- accrual balance, salary-owed balance, approved expense lines) — it never computes or changes a
-- payout number itself. See docs/specs/envelope-expense-payout.md "Money doctrine".
--
-- SAFE: additive + idempotent. DEGRADES GRACEFULLY: every read/write in closing/envelope.py is
-- try/except-guarded; until this runs, GET /closing/envelope-plan and /closing/payout-due read an
-- empty withdrawal history (never fail) and the DM execution page shows "run migration 507" instead
-- of a 500.
--
-- RLS POSTURE (AGENT_CONTRACT §5): RLS enabled, ZERO policies, GRANT ALL to service_role only.

CREATE TABLE IF NOT EXISTS commcalc.envelope_withdrawal (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  store_code      TEXT,
  close_date      DATE NOT NULL,             -- the close_date of the ENVELOPE the cash was taken from
  closing_row_id  UUID,                       -- the specific daily_closing row (envelope) drawn down
  amount          NUMERIC NOT NULL,
  purpose         TEXT NOT NULL DEFAULT 'other',  -- 'commission_payout' | 'salary_payout' | 'expense' | 'other'
  expense_id      UUID REFERENCES commcalc.closing_expense(id),
  employee_id     TEXT,                       -- who the cash was paid to, when purpose is a payout
  employee_name   TEXT,
  payout_ref      TEXT,                       -- e.g. the commission_payout_ledger / salary_advance_ledger row id
  remaining_after NUMERIC,                     -- envelope's remaining cash after this withdrawal (DM-confirmed)
  taken_by        TEXT,
  taken_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  notes           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS envelope_withdrawal_org_date_idx
  ON commcalc.envelope_withdrawal (org_id, close_date, store_code);
CREATE INDEX IF NOT EXISTS envelope_withdrawal_row_idx
  ON commcalc.envelope_withdrawal (closing_row_id);
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'envelope_withdrawal_purpose_chk') THEN
    ALTER TABLE commcalc.envelope_withdrawal
      ADD CONSTRAINT envelope_withdrawal_purpose_chk
      CHECK (purpose IN ('commission_payout','salary_payout','expense','other'));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS commcalc.envelope_payout_config (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  store_code          TEXT,                    -- NULL = org default; a real code = per-store override
  take_commission     BOOLEAN NOT NULL DEFAULT true,
  take_salary         BOOLEAN NOT NULL DEFAULT true,
  take_expenses       BOOLEAN NOT NULL DEFAULT true,
  commission_cadence  TEXT NOT NULL DEFAULT 'weekly',   -- 'daily' | 'weekly' | 'biweekly' | 'monthly'
  commission_anchor   INT,                      -- weekday 0-6 (weekly) or day-of-month (monthly)
  commission_anchor_date DATE,                  -- biweekly reference date
  salary_cadence      TEXT NOT NULL DEFAULT 'weekly',
  salary_anchor       INT,
  salary_anchor_date  DATE,
  updated_by          TEXT,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS envelope_payout_config_org_store_uq
  ON commcalc.envelope_payout_config (org_id, COALESCE(store_code, ''));
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'envelope_payout_config_commission_cadence_chk') THEN
    ALTER TABLE commcalc.envelope_payout_config
      ADD CONSTRAINT envelope_payout_config_commission_cadence_chk
      CHECK (commission_cadence IN ('daily','weekly','biweekly','monthly'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'envelope_payout_config_salary_cadence_chk') THEN
    ALTER TABLE commcalc.envelope_payout_config
      ADD CONSTRAINT envelope_payout_config_salary_cadence_chk
      CHECK (salary_cadence IN ('daily','weekly','biweekly','monthly'));
  END IF;
END $$;

ALTER TABLE commcalc.envelope_withdrawal    ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.envelope_payout_config ENABLE ROW LEVEL SECURITY;
GRANT ALL ON commcalc.envelope_withdrawal    TO service_role;
GRANT ALL ON commcalc.envelope_payout_config TO service_role;

NOTIFY pgrst, 'reload schema';
SELECT '507 complete — commcalc.envelope_withdrawal + commcalc.envelope_payout_config ready' AS status;
