-- 258_commission_expected_earned_promote.sql   (mod-commission, band 200-299)
--
-- EXPECTED vs EARNED for the multi-month installment, and the permission-gated manual promote.
--
-- OWNER DIRECTIVE 2026-08-01 (verbatim): "as a modification to the commision payout for the second
-- third and upto 6 months, let the system calculate the the expected commission as a separate column
-- but not use that to pay out, if the company gets paid the employee commission auto fills from there,
-- there should be an option to move the expected commisison to the earned column is the system
-- malfnctions or the report is not updated on time, this will done as an edit function gated per
-- permission."
--
-- ── WHAT THIS ADDS ───────────────────────────────────────────────────────────────────────────────
--   1. commcalc.installment_promote            NEW — one row per manually promoted chain-month.
--   2. commcalc.commission_org_config.expected_commission_config JSONB  — the window + posture.
--   3. commcalc.sale_installment_ledger        + expected_amount / promote_id / promoted_by /
--                                                promoted_at  (audit columns on the existing ledger).
--
-- ── MONEY POSTURE ────────────────────────────────────────────────────────────────────────────────
-- EXPECTED PAYS NOBODY. `expected_amount` is the amount a month WOULD pay — the pre-gate figure the
-- engine already computes and currently discards when the gate is unmet. It is written to the ledger
-- as a column and summed into NOTHING: not by_rep, not totals.amount, not rep_commissions, not the
-- P&L. "calculate the expected commission as a separate column but not use that to pay out."
--
-- EARNED IS UNCHANGED. "if the company gets paid the employee commission auto fills from there" is the
-- EXISTING paid gate (raw_mi / raw_ma_commission). No gate logic is forked, re-implemented or altered.
--
-- THE PROMOTE IS THE ONLY NEW MONEY PATH, and this migration creates it EMPTY. A promote row is
-- written only by POST /commcalc/expected-commission/promote, which is permission-gated and records
-- who/when/why. With zero rows in installment_promote, every tenant's pay is byte-identical.
--
-- ── WHY ITS OWN TABLE: IT MUST SURVIVE RECOMPUTE ────────────────────────────────────────────────
-- sale_installment_engine._persist DELETES the pay period's ledger rows and re-inserts them on every
-- recalculation. A promote stored on the ledger would therefore be erased by the next Run Calculation
-- and the employee would silently stop being paid. Promotes live in a SEPARATE org-scoped table that
-- the calc never deletes, and are re-applied during compute_sale_installments — the same separation
-- that keeps a manually-assigned chargeback alive (router._run_calculation deletes chargeback_items
-- with .neq('source','chargeback_review')).
--
-- ── AND IT IS NEVER PAID AT A STALE NUMBER ──────────────────────────────────────────────────────
-- expected_at_promote records the figure that was approved. If a later recompute expects a DIFFERENT
-- amount, the default posture (`on_expected_change='hold_and_warn'`) does NOT pay it and raises a loud
-- promote_expected_changed warning naming both figures. A tenant may choose 'pay_current_and_warn',
-- which pays TODAY's figure — never the stored one. No configuration pays the stale number.
--
-- ── UNTIL THIS RUNS ─────────────────────────────────────────────────────────────────────────────
-- Everything degrades: expected_commission.load_config() returns the code defaults (window 2..6),
-- load_promotes() returns [] (so no promote exists and nothing can be paid by one), and the ledger
-- write ADAPTS — sale_installment_engine._persist tries the four new columns once, falls back to the
-- pre-258 column list for the rest of the run, and REPORTS which it used. That fallback matters: the
-- persist path deletes before it inserts, so a hard failure would have emptied the period.
-- The page renders read-only with ready:false and names this file; the promote endpoint returns a
-- clear 400, never a 500.
--
-- ADDITIVE + IDEMPOTENT + RLS-ZERO-POLICY: safe to re-run; RLS on, NO policies, NO anon/authenticated
-- grants (contract §5 — all access is via the backend service role).

-- ── 1) the promote table — the audit trail IS the feature ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.installment_promote (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  -- identity: the SAME four fields as sale_installment_ledger's own UNIQUE key, so a promote can never
  -- point at two rows or drift from the row it approved.
  pay_period          TEXT NOT NULL,
  trans_id            TEXT NOT NULL,
  mdn                 TEXT NOT NULL DEFAULT '',
  month_index         INT  NOT NULL,
  -- context, for the audit list (never used for matching)
  sale_period         TEXT,
  serial_1            TEXT,
  schedule_id         UUID,
  plan_id             UUID,
  epay_salesperson    TEXT,
  store               TEXT,
  -- the figure that was APPROVED. If a recompute disagrees, the promote holds (or pays the CURRENT
  -- figure) and shouts — it is never paid at this stored number.
  expected_at_promote NUMERIC,
  reason              TEXT NOT NULL,
  status              TEXT NOT NULL DEFAULT 'active',   -- active | revoked (revoked rows are KEPT)
  promoted_by         TEXT,
  promoted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_by          TEXT,
  revoked_at          TIMESTAMPTZ,
  revoke_reason       TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, pay_period, trans_id, mdn, month_index)
);
CREATE INDEX IF NOT EXISTS installment_promote_period ON commcalc.installment_promote (org_id, pay_period);
CREATE INDEX IF NOT EXISTS installment_promote_status ON commcalc.installment_promote (org_id, status);
CREATE INDEX IF NOT EXISTS installment_promote_line   ON commcalc.installment_promote (org_id, trans_id, mdn);

ALTER TABLE commcalc.installment_promote ENABLE ROW LEVEL SECURITY;

-- ── 2) the per-tenant window + posture ─────────────────────────────────────────────────────────────
-- The owner's "second third and upto 6 months" is the DEFAULT, not a constant (contract RULE TWO).
ALTER TABLE commcalc.commission_org_config
  ADD COLUMN IF NOT EXISTS expected_commission_config JSONB;

COMMENT ON COLUMN commcalc.commission_org_config.expected_commission_config IS
  'Expected-vs-earned config for the sale-triggered installment (mig 258). NULL = code defaults '
  '{"enabled":true,"from_month":2,"to_month":6,"on_expected_change":"hold_and_warn",'
  '"promote_allow_unidentified":false}. from_month/to_month are the months that carry an EXPECTED '
  'column and may be manually promoted (the owner''s "second third and upto 6 months"). '
  'on_expected_change decides what happens when a recompute expects a different amount than the one '
  'approved: hold_and_warn (do not pay, shout, ask for re-approval) or pay_current_and_warn (pay '
  'TODAY''s figure, never the stored one). EXPECTED itself is never summed into any payout.';

-- ── 3) audit columns on the existing ledger ────────────────────────────────────────────────────────
-- expected_amount is REPORTING ONLY. amount stays the single source of what was paid.
ALTER TABLE commcalc.sale_installment_ledger
  ADD COLUMN IF NOT EXISTS expected_amount NUMERIC,
  ADD COLUMN IF NOT EXISTS promote_id      UUID,
  ADD COLUMN IF NOT EXISTS promoted_by     TEXT,
  ADD COLUMN IF NOT EXISTS promoted_at     TIMESTAMPTZ;

COMMENT ON COLUMN commcalc.sale_installment_ledger.expected_amount IS
  'What this installment month WOULD pay — the pre-gate amount (mig 258). REPORTING ONLY: never '
  'summed into by_rep, totals.amount, rep_commissions or the P&L. `amount` remains the only record '
  'of what was actually paid.';

-- NOTHING IS SEEDED. A promote is a money decision made by a named person for a named reason; there
-- is no such thing as a default one.

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 258 complete — installment_promote (EMPTY), expected_commission_config (NULL) and '
       'four reporting/audit columns on sale_installment_ledger. No payout changes: expected is never '
       'summed, and with zero promote rows every tenant pays exactly what it paid before.' AS status;
