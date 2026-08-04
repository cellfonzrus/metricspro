-- 267_commission_daily_accrual_payout_ledger.sql  (mod-commission, band 200-299)
--
-- DAILY COMMISSION ACCRUAL + ENVELOPE PAYOUT LEDGER (EEP — owner directive 2026-08-04).
-- Spec: docs/specs/envelope-expense-payout.md (Feature 2, commission side).
--
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- MONEY DOCTRINE — READ THIS BEFORE CHANGING ANYTHING HERE
-- ════════════════════════════════════════════════════════════════════════════════════════════════
--   * `daily_commission_accrual` holds a PROBABLE / EXPECTED number. It is the same doctrine as the
--     M2-M6 "expected" column: it is NEVER summed into anyone's pay, it never feeds
--     commcalc.rep_commissions, and no payout engine reads it. It exists so a store manager can see
--     "roughly what this rep has earned so far" and so cash advanced out of the daily envelope has
--     something honest to be measured against.
--   * `commission_payout_ledger` records CASH ADVANCES — money physically handed to an employee out
--     of a day's envelope. Recording an advance is a CASH MOVEMENT, not a payroll event and not a
--     P&L expense (the P&L already carries rep commission from rep_commissions and wages from
--     clock-in; see the spec's money doctrine + mod-finance's double-count guard).
--   * NOTHING in this feature mutates rep_commissions, commission plans, payout schedules, tiers or
--     any number a human is actually paid. The accrual is derived FROM the pay logic; it never
--     writes back to it.
--
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- TABLE 1 — commcalc.daily_commission_accrual
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- One row per (org, work_date, employee, store). Written by POST /commcalc/payout/accrual/run (and by
-- the daily sweep that calls it), which is IDEMPOTENT: re-running a date recomputes that date's rows
-- from the day's sale lines and UPSERTs them, so a replay can only ever restate the same day, never
-- accumulate.
--
--   base_amount  — the day's OWN sale-derived commission, computed UN-TIERED. Plan-mode tenants: the
--                  commission_engine's base + tiered rule payouts at multiplier 1.0 plus the set-up-fee
--                  pay item. Boost tenants: the calculator's `subtotal` (the 8 sale-derived components)
--                  before the KPI tier multiplier. A single DAY cannot know a MONTHLY tier attainment,
--                  so speculating one would make the accrual wrong in both directions; instead the whole
--                  tier effect is recognized once, later, as ->
--   tier_amount  — the MONTHLY TRUE-UP for a prior month, recognized exactly ONCE per (employee,
--                  source month) on a tenant-configurable recognition date, and only after that month's
--                  commission run actually exists (rep_commissions rows). It is
--                  `final month total - sum(base_amount accrued in that month)`, so after recognition
--                  the accrual stream converges on what the rep genuinely earned. It CAN be negative
--                  (a month that finished below the un-tiered accrual); that is a true-up, not a
--                  clawback, and nothing is deducted from anyone.
--   total_amount — base_amount + tier_amount. Stored (not generated) so a future accrual-kind can set
--                  it directly; the writer always keeps it consistent.
--   components   — jsonb explaining the number in plain language (per-bucket breakdown, the source
--                  table the day was read from, which pieces are deliberately deferred to the monthly
--                  true-up). It is SHOWN TO REPS AND DMs, so it must always explain, never just assert.
--
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- TABLE 2 — commcalc.commission_payout_ledger
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- Append-only record of cash paid to an employee against their accrued commission. `withdrawal_ref`
-- points back at the retail-ops `envelope_withdrawal` row the cash physically came out of (nullable:
-- an advance can also be recorded without an envelope). There is deliberately NO netting and NO
-- clawback: where advances exceed accruals the system FLAGS it for review (ledger Q14 default) and
-- leaves the correction to a human.
--
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- TABLE 3 (column) — commcalc.commission_org_config.accrual_config
-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- RULE TWO: nothing about this is hard-coded. One jsonb per tenant; NULL = the code default in
-- backend/app/modules/commcalc/payout_accrual.py:
--     {"enabled": true,
--      "tier_basis": "none",                  -- "none" = accrue un-tiered (default) | "as_computed"
--      "tier_recognition": {"mode": "on_run_available", "day_of_month": null, "lookback_months": 3},
--      "auto_run": {"enabled": true, "days_back": 1, "min_interval_minutes": 50}}
--   tier_recognition.mode
--      "on_run_available" (default) — recognize the prior month's true-up as soon as that month's
--                                     commission run exists (earliest = the 1st of the next month).
--      "day_of_month"               — recognize on `day_of_month` of the FOLLOWING month (clamped to
--                                     month end), and still only once the run exists.
--   auto_run.days_back — how many days back of accrual the daily sweep re-runs (1 = yesterday+today),
--                        clamped 0..7 in code so a typo can never turn the sweep into a month rewrite.
--   auto_run.min_interval_minutes — throttles the SWEEP only (0 = no throttle; a hand-pressed
--                        POST /payout/accrual/run is never throttled). The accrual rides the hourly
--                        promote sweep; this stops a burst of promote calls from re-driving the pay
--                        engine for every tenant several times inside one hour for no new information.
--
-- ADDITIVE + IDEMPOTENT. Safe to re-run. RLS enabled with ZERO policies; no GRANT, no CREATE POLICY,
-- no anon/authenticated (contract §5) — all access is the backend service role.
-- UNTIL THIS RUNS: every endpoint degrades gracefully (`ready:false`, empty lists, a plain-language
-- note) and the daily sweep no-ops. Nothing else in commcalc changes.

-- ── 1. daily accrual ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.daily_commission_accrual (
  id             BIGSERIAL PRIMARY KEY,
  org_id         UUID NOT NULL,
  work_date      DATE NOT NULL,
  employee_key   TEXT NOT NULL,
  store_code     TEXT NOT NULL DEFAULT '',
  employee_name  TEXT,
  base_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
  tier_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
  total_amount   NUMERIC(12,2) NOT NULL DEFAULT 0,
  components     JSONB,
  computed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The upsert key. store_code defaults to '' (never NULL) precisely so this unique index can never be
-- defeated by a NULL: in Postgres NULLs are distinct, so a nullable store_code would let one rep
-- accumulate unlimited duplicate rows for the same day and silently multiply the accrual.
CREATE UNIQUE INDEX IF NOT EXISTS daily_commission_accrual_key_idx
  ON commcalc.daily_commission_accrual (org_id, work_date, employee_key, store_code);

CREATE INDEX IF NOT EXISTS daily_commission_accrual_org_idx
  ON commcalc.daily_commission_accrual (org_id);
CREATE INDEX IF NOT EXISTS daily_commission_accrual_org_date_idx
  ON commcalc.daily_commission_accrual (org_id, work_date);
CREATE INDEX IF NOT EXISTS daily_commission_accrual_org_emp_idx
  ON commcalc.daily_commission_accrual (org_id, employee_key);

ALTER TABLE commcalc.daily_commission_accrual ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE commcalc.daily_commission_accrual IS
  'PROBABLE (expected) daily commission per employee per store. Never pay: nothing sums this into '
  'rep_commissions and no payout engine reads it. base_amount = that day''s un-tiered sale-derived '
  'commission; tier_amount = the prior month''s true-up, recognized once on a tenant-configurable date '
  'after that month''s commission run exists. Upserted idempotently by POST /commcalc/payout/accrual/run.';
COMMENT ON COLUMN commcalc.daily_commission_accrual.components IS
  'Plain-language breakdown shown to reps/DMs: per-rule (plan mode) or per-component (Boost mode) '
  'amounts, the sales table the day was read from, and what is deliberately deferred to the monthly '
  'true-up (KPI tier, ePay-derived trade-in spiff).';

-- ── 2. payout ledger ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.commission_payout_ledger (
  id              BIGSERIAL PRIMARY KEY,
  org_id          UUID NOT NULL,
  employee_key    TEXT NOT NULL,
  employee_name   TEXT,
  amount          NUMERIC(12,2) NOT NULL,
  paid_date       DATE NOT NULL,
  method          TEXT NOT NULL DEFAULT 'envelope_cash',
  store_code      TEXT,
  withdrawal_ref  TEXT,
  note            TEXT,
  recorded_by     TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS commission_payout_ledger_org_idx
  ON commcalc.commission_payout_ledger (org_id);
CREATE INDEX IF NOT EXISTS commission_payout_ledger_org_emp_idx
  ON commcalc.commission_payout_ledger (org_id, employee_key);
CREATE INDEX IF NOT EXISTS commission_payout_ledger_org_date_idx
  ON commcalc.commission_payout_ledger (org_id, paid_date);
-- One envelope withdrawal produces at most one commission ledger row: makes the DM execution page's
-- retry (or a double-click) a no-op instead of paying the rep twice on paper. Partial so the many
-- rows with no withdrawal_ref (manual advances) are unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS commission_payout_ledger_withdrawal_idx
  ON commcalc.commission_payout_ledger (org_id, withdrawal_ref)
  WHERE withdrawal_ref IS NOT NULL;

ALTER TABLE commcalc.commission_payout_ledger ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE commcalc.commission_payout_ledger IS
  'CASH ADVANCES paid to an employee against accrued commission (usually out of a daily closing '
  'envelope). A cash movement, not a payroll event and not a P&L expense — the P&L already carries rep '
  'commission from rep_commissions. Append-only; no netting and no clawback (over-advances are FLAGGED '
  'for human review). withdrawal_ref points at the retail-ops envelope_withdrawal row.';

-- ── 3. per-tenant accrual config (RULE TWO) ─────────────────────────────────────────────────────
ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS accrual_config JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.accrual_config IS
  'Daily commission accrual settings. NULL = code default '
  '{"enabled":true,"tier_basis":"none","tier_recognition":{"mode":"on_run_available",'
  '"day_of_month":null,"lookback_months":3},"auto_run":{"enabled":true,"days_back":1,'
  '"min_interval_minutes":50}}. '
  'tier_recognition.mode: on_run_available | day_of_month (of the FOLLOWING month, clamped to month '
  'end; still gated on the prior month''s commission run existing). Accruals are expected numbers — '
  'changing this never changes what anyone is paid.';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 267 complete — commcalc.daily_commission_accrual + commcalc.commission_payout_ledger '
       '+ commission_org_config.accrual_config (EXPECTED accruals + cash-advance ledger; writes no pay, '
       'reads no pay engine backwards, mutates no rep_commissions)' AS status;
