-- 504_ops_chargebacks.sql — Ops-accountability chargebacks (retail-ops domain events + tables).
--
-- OWNER DIRECTIVE 2026-07-22: a store's EFFECTIVE closer for a day is the static store_closer
-- assignee only if they actually worked that store that day, otherwise the last person to leave.
-- A missed daily closing flags the effective closer + creates a management-definable PENDING
-- chargeback, decided (post/waive) at payroll. A missed DM verification creates a pending
-- chargeback against the DM's COMMISSION, surfaced on the DM Verify page.
--
-- OWNER FOLLOW-UP (same day, v2 — schema amended before anything shipped, nothing to migrate FROM):
-- cascade deduction. A commissionable person's chargeback deducts from COMMISSION FIRST; any
-- uncovered remainder either falls to PAYROLL or is FORWARDED to the next commission cycle, per
-- ops_chargeback_policy.overflow. mod-commission owns the settlement engine that creates the
-- overflow CHILD row(s) (parent_id set, covered_amount stamped on the PARENT once settled); this
-- module only ever creates PARENT rows (parent_id NULL, covered_amount NULL).
--
-- This is the SHARED data contract other agents build against in parallel (mod-people: punch
-- notices, payroll decide UI, employee dashboard; mod-commission: commission settlement/deduction
-- of posted DM-verify chargebacks) — the columns below are frozen by that contract; do not
-- rename/retype without updating docs/handoffs/retail-ops.md's cross-agent note.
--
-- SAFE: additive + idempotent (CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS). No seeded
-- amounts — every reason starts disabled/zero until an admin sets it on the Ops Chargeback Amounts
-- config page. Nothing breaks until this runs: every read/write of these two tables in the closing
-- module is try/except-guarded and degrades to an honest empty/no-op state (see
-- backend/app/modules/closing/ops_chargebacks.py).

CREATE TABLE IF NOT EXISTS commcalc.ops_chargeback (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,
  employee_id    TEXT,
  employee_name  TEXT,
  store_code     TEXT,
  reason         TEXT NOT NULL,            -- 'missed_closing' | 'missed_dm_verify' (extensible)
  incident_date  DATE NOT NULL,
  amount         NUMERIC NOT NULL,         -- snapshot of the configured amount at creation
  status         TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'posted' | 'waived'
  applied_to     TEXT NOT NULL,            -- 'payroll' | 'commission'
  posted_ref     TEXT,
  decided_by     TEXT, decided_at TIMESTAMPTZ,
  notes          TEXT,
  parent_id      UUID REFERENCES commcalc.ops_chargeback(id),  -- set on a settlement-created
                                             -- overflow CHILD row; NULL on every row this module
                                             -- creates (the "original incident" / parent row).
  covered_amount NUMERIC,                   -- how much of a PARENT's amount commission actually
                                             -- absorbed; stamped by the commission settlement,
                                             -- NULL until settled. Never set by this module.
  created_at     TIMESTAMPTZ DEFAULT NOW()
);
-- Defensive for a table already created by an earlier draft of this migration in some environment.
ALTER TABLE commcalc.ops_chargeback ADD COLUMN IF NOT EXISTS parent_id      UUID REFERENCES commcalc.ops_chargeback(id);
ALTER TABLE commcalc.ops_chargeback ADD COLUMN IF NOT EXISTS covered_amount NUMERIC;

CREATE INDEX IF NOT EXISTS ops_chargeback_org_idx ON commcalc.ops_chargeback (org_id, status, applied_to);

-- Two PARTIAL unique indexes (not one plain UNIQUE) because a settlement-created child row shares
-- its parent's (org_id, employee_id, store_code, reason, incident_date) tuple by construction —
-- a single unconditional UNIQUE on that tuple would make the FIRST child insert collide.
--   • Parent rows (parent_id IS NULL): the original per-incident idempotency key this module's
--     detection sweeps upsert against (unchanged from v1).
--   • Child rows (parent_id IS NOT NULL): the settlement's OWN idempotency key — one child per
--     (parent, applied_to leg, commission period) — owned/used by mod-commission's engine.
CREATE UNIQUE INDEX IF NOT EXISTS ops_chargeback_parent_uq
  ON commcalc.ops_chargeback (org_id, employee_id, store_code, reason, incident_date)
  WHERE parent_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ops_chargeback_child_uq
  ON commcalc.ops_chargeback (org_id, parent_id, applied_to, posted_ref)
  WHERE parent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS commcalc.ops_chargeback_policy (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL, reason TEXT NOT NULL,
  label      TEXT,                            -- management-assigned display message for the reason
                                                -- (editable even for a reason this module never
                                                -- hard-coded — "assign manual error messages if we
                                                -- have a log of errors").
  amount     NUMERIC NOT NULL DEFAULT 0, enabled BOOLEAN NOT NULL DEFAULT false,
  overflow   TEXT NOT NULL DEFAULT 'payroll',  -- 'payroll' | 'next_cycle' — where an uncovered
                                                -- COMMISSION remainder goes after cascade deduction.
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, reason)
);
ALTER TABLE commcalc.ops_chargeback_policy ADD COLUMN IF NOT EXISTS label    TEXT;
ALTER TABLE commcalc.ops_chargeback_policy ADD COLUMN IF NOT EXISTS overflow TEXT NOT NULL DEFAULT 'payroll';

-- RLS open_all to match sibling tables (089_cash_management.sql).
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.ops_chargeback','commcalc.ops_chargeback_policy'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 504 complete — ops chargebacks (missed_closing / missed_dm_verify, cascade-ready)' AS status;
