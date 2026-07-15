-- 205_commission_expense_apply_config.sql — the per-tenant PROTECTED-expense set for the Store-Expenses
--   "apply to other months" command (mod-commission, band 200-299).
--
-- WHY (owner request 2026-07-15): expenses were entered in July and need to be back-applied to earlier
-- months for ALL expenses EXCEPT commission and salary. RULE TWO: the excluded set must be CONFIG, not a
-- magic list hard-coded in the handler, so a tenant can adjust what's protected. This table holds the
-- per-org expense-name TOKENS (case-insensitive substring match on expense_name) that are NEVER copied
-- across months. Seeded default {commission, salary}. The endpoint falls back to that same code default
-- when no rows exist, so protection holds even before this migration runs (feature degrades gracefully).
--
-- Consumed by GET/PUT /commcalc/expenses/apply-config + POST /commcalc/expenses/apply-to-months.
-- ADDITIVE + IDEMPOTENT (safe to re-run). NON-money: expenses feed GP/P&L, not commission payouts.

CREATE TABLE IF NOT EXISTS commcalc.expense_apply_config (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL,
  token      TEXT NOT NULL,               -- case-insensitive substring matched against expense_name
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS expense_apply_config_org_idx
  ON commcalc.expense_apply_config (org_id);

CREATE UNIQUE INDEX IF NOT EXISTS expense_apply_config_org_token_uq
  ON commcalc.expense_apply_config (org_id, lower(token));

DO $$
DECLARE t text := 'commcalc.expense_apply_config';
BEGIN
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
  EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
  EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
  EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
END $$;

-- Seed the default protected tokens {commission, salary}. Idempotent (ON CONFLICT DO NOTHING) so it never
-- overwrites a tenant's edited set. Every tenant inherits the SAME code default until it saves its own
-- rows, so seeding one org is sufficient for correct protection everywhere; back-fill the tenant list too.
CREATE OR REPLACE FUNCTION commcalc.seed_expense_apply_config(p_org uuid)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
  -- 'salaries' alongside 'salary' — the match is a plain substring and the real category names are the
  -- PLURAL "Employee Salaries" / "Owner / Mgmt Salaries", which 'salary' alone would not catch.
  INSERT INTO commcalc.expense_apply_config (org_id, token)
  SELECT p_org, tok FROM (VALUES ('commission'), ('salary'), ('salaries')) AS v(tok)
  ON CONFLICT (org_id, lower(token)) DO NOTHING;
END;
$fn$;
GRANT EXECUTE ON FUNCTION commcalc.seed_expense_apply_config(uuid) TO anon, authenticated, service_role;

DO $seed$
DECLARE t record;
BEGIN
  PERFORM commcalc.seed_expense_apply_config('00000000-0000-0000-0000-000000000001');
  BEGIN
    FOR t IN SELECT org_id FROM storeops.tenants LOOP
      PERFORM commcalc.seed_expense_apply_config(t.org_id);
    END LOOP;
  EXCEPTION WHEN undefined_table THEN
    NULL;  -- storeops.tenants absent in a bare env → house seed above still applied
  END;
END $seed$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 205 complete — expense_apply_config (apply-to-months protected set)' AS status;
