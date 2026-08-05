-- 509_closing_deposit_recon.sql — mod-retail-ops band 500-599.
--
-- Cash Deposit Reconciliation — OWNER DIRECTIVE 2026-08-05. Cross-checks cash actually collected
-- (Daily Closing + POS X-Report) against cash actually DEPOSITED (commcalc.bank_deposit, mig 107/502),
-- net of tenant-configurable adjustments (cash expenses / bill-payment cash / other), split across
-- tenant-defined deposit CATEGORIES. Reuses the EXISTING bank_deposit table + _bank_deposit_declared /
-- envelope.approved_expense_totals math (backend/app/modules/closing/deposit_recon.py) — this
-- migration adds config/ledger tables only, no new money-computation duplicated here.
--
-- FOUR pieces:
--   (1) commcalc.closing_deposit_category — org-scoped, tenant-configurable deposit/reconciliation
--       buckets. `basis` picks which already-computed cash figure the category reconciles against:
--       'bill_payment_cash' | 'store_cash' | 'total_cash' (the SAME 3 values _bank_deposit_declared
--       already supports) | 'manual' (a tenant-added bucket with no auto-computed expected figure —
--       deposits still recorded/reported, expected stays 0 until a future generalized cash-split
--       exists — see the retail-ops handoff park record for this package). Lazy-seeded on first GET
--       (same pattern as closing_expense_category, mig 506) with 2 presets: "Bill Payment Cash
--       Deposit" (basis=bill_payment_cash) and "Store Cash Deposit" (basis=store_cash) — the owner's
--       exact named defaults.
--   (2) commcalc.closing_deposit_adjustment_type — org-scoped, tenant-configurable "other adjustment"
--       reasons (e.g. "Safe Change Fund", "Bank Fee"). NO forced presets — open admin-add list, since
--       "cash expenses" and "bill payments in cash" are already covered by existing infra
--       (closing_expense / epay_on_cash) and don't need a row here.
--   (3) commcalc.closing_deposit_adjustment — the ledger of manual "other" adjustment $ amounts per
--       (store, day), tagged to an adjustment_type. Only nets into "expected deposit" when the
--       report's include_other_adjustments toggle is on (default OFF — see closing_deposit_config
--       columns below).
--   (4) commcalc.bank_deposit (EXISTING table, mig 107/502) gains columns for: which deposit CATEGORY
--       a deposit belongs to, the append-only short-deposit flow (short_reason / is_supplemental /
--       parent_deposit_id — a supplemental deposit is always a NEW row, NEVER an update of the
--       original), and an audit trail of who recorded it + what expected/adjustment-toggle state was
--       in effect at record time (mirrors the existing declared_amount/match_target frozen-at-insert
--       columns from mig 502 — same auditability doctrine, new columns).
--   commcalc.closing_deposit_config (mig 502) gains the 3 default include/exclude toggles for
--       adjustments — ALL DEFAULT FALSE (excluded), per the owner's explicit "excluded by default"
--       requirement; the report itself can override per-run via query params without touching config.
--
-- SEEDING TRAP AVOIDED (per dispatch's own memory note): the 2 preset categories are LAZY-SEEDED at
-- read time (backend, upsert-on-first-GET against a real NOT-NULL `id` primary key), never an
-- `ON CONFLICT` migration-time seed keyed on a nullable column — there is no migration-time INSERT of
-- per-tenant rows here at all, sidestepping the double-seed class entirely (same reasoning as mig 506).
--
-- SAFE: additive + idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS everywhere). DEGRADES
-- GRACEFULLY: every reader in deposit_recon.py / router.py wraps these tables/columns in try/except —
-- pre-migration, GET /closing/deposit-categories returns the coded 2-preset default (unsaved), the
-- deposit-recon report returns "not available (run migration 509?)" instead of a 500, and
-- POST /closing/bank-deposit keeps saving with the pre-509 column set exactly as it does today when
-- mig 502 hasn't run (existing degrade pattern, extended one more step).
--
-- RLS POSTURE (AGENT_CONTRACT §5, locked down 2026-07-28): RLS enabled, ZERO policies, GRANT ALL to
-- service_role only. No anon/authenticated grant.

CREATE TABLE IF NOT EXISTS commcalc.closing_deposit_category (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  name        TEXT NOT NULL,
  basis       TEXT NOT NULL DEFAULT 'manual',   -- 'bill_payment_cash' | 'store_cash' | 'total_cash' | 'manual'
  is_preset   BOOLEAN NOT NULL DEFAULT false,
  is_active   BOOLEAN NOT NULL DEFAULT true,
  sort_order  INT NOT NULL DEFAULT 100,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS closing_deposit_category_org_idx
  ON commcalc.closing_deposit_category (org_id, is_active);
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'closing_deposit_category_basis_chk') THEN
    ALTER TABLE commcalc.closing_deposit_category
      ADD CONSTRAINT closing_deposit_category_basis_chk
      CHECK (basis IN ('bill_payment_cash','store_cash','total_cash','manual'));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS commcalc.closing_deposit_adjustment_type (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  name        TEXT NOT NULL,
  is_active   BOOLEAN NOT NULL DEFAULT true,
  sort_order  INT NOT NULL DEFAULT 100,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS closing_deposit_adjustment_type_org_idx
  ON commcalc.closing_deposit_adjustment_type (org_id, is_active);

CREATE TABLE IF NOT EXISTS commcalc.closing_deposit_adjustment (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  store_code          TEXT,
  close_date          DATE NOT NULL,
  adjustment_type_id  UUID REFERENCES commcalc.closing_deposit_adjustment_type(id),
  adjustment_type_name TEXT,                    -- snapshot at insert time
  category_id         UUID REFERENCES commcalc.closing_deposit_category(id),  -- which bucket this reduces; NULL = total_cash/general
  amount              NUMERIC NOT NULL DEFAULT 0,
  description         TEXT,
  created_by          TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS closing_deposit_adjustment_org_date_idx
  ON commcalc.closing_deposit_adjustment (org_id, close_date, store_code);

-- ── EXISTING commcalc.bank_deposit (mig 107/502) — additive columns for category + short-deposit flow.
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS category_id           UUID;
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS category_name         TEXT;   -- snapshot at insert
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS short_reason          TEXT;
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS is_supplemental       BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS parent_deposit_id     UUID REFERENCES commcalc.bank_deposit(id);
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS will_deposit_more     BOOLEAN;
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS expected_amount_recon NUMERIC;  -- expected deposit for THIS category/day, per the recon toggles in effect at record time (distinct from the pre-existing `declared_amount`, which is the OCR match basis — never overwritten)
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS include_expenses      BOOLEAN;  -- adjustment toggles frozen at record time (audit)
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS include_bill_payments BOOLEAN;
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS include_other_adj     BOOLEAN;
ALTER TABLE commcalc.bank_deposit ADD COLUMN IF NOT EXISTS recorded_by           TEXT;   -- the logged-in app user who recorded THIS row (audit; distinct from employee_name = whose cash it is)
CREATE INDEX IF NOT EXISTS bank_deposit_category_idx
  ON commcalc.bank_deposit (org_id, close_date, store_code, category_id);
CREATE INDEX IF NOT EXISTS bank_deposit_parent_idx
  ON commcalc.bank_deposit (parent_deposit_id);

-- ── EXISTING commcalc.closing_deposit_config (mig 502) — default include/exclude toggles for the
-- expected-deposit adjustment calc. ALL DEFAULT FALSE (excluded) per the owner's explicit directive;
-- the deposit-recon REPORT can override these per-run via query params without touching this config.
ALTER TABLE commcalc.closing_deposit_config ADD COLUMN IF NOT EXISTS include_expenses_default      BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE commcalc.closing_deposit_config ADD COLUMN IF NOT EXISTS include_bill_payments_default  BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE commcalc.closing_deposit_config ADD COLUMN IF NOT EXISTS include_other_adj_default       BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE commcalc.closing_deposit_category         ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.closing_deposit_adjustment_type   ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.closing_deposit_adjustment        ENABLE ROW LEVEL SECURITY;
-- RLS posture: backend service role only (AGENT_CONTRACT §5). No policies, no anon/authenticated grants.
GRANT ALL ON commcalc.closing_deposit_category         TO service_role;
GRANT ALL ON commcalc.closing_deposit_adjustment_type   TO service_role;
GRANT ALL ON commcalc.closing_deposit_adjustment        TO service_role;

NOTIFY pgrst, 'reload schema';
SELECT '509 complete — commcalc.closing_deposit_category / _adjustment_type / _adjustment ready; '
       'bank_deposit + closing_deposit_config extended for Cash Deposit Reconciliation' AS status;
