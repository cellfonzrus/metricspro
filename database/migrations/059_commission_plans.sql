-- 059_commission_plans.sql — CONFIGURABLE commission PLAN engine (user-built, assigned per scope).
--
-- WHY: commission payout is hard-coded today (premium/byod/upgrade flat spiffs by contract_type, acima
-- by tender_type, accessories by department, custom_spiffs by category, KPI tiering). That's one company's
-- plan baked into calculator.py. A SaaS tenant — or even one tenant with two markets — needs DIFFERENT pay
-- structures that THEY define: any line item on the sales transaction report can optionally qualify for
-- commission, on rules they create, assigned to whichever employees / stores / markets need them.
--
-- This migration is the DATA MODEL for that. The compute (commcalc.commission_engine) is READ-ONLY /
-- PREVIEW — it never touches rep_commissions or the live POST /calculate path. Wiring a plan into the live
-- calc is an explicit later step the user approves. Running this migration ALONE changes nothing.
--
-- Additive + idempotent. RLS open_all (matches every commcalc table today).

-- ── plan header ──────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.commission_plan (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  name             TEXT NOT NULL,
  carrier_id       UUID,                       -- commcalc.carrier.id this plan applies to; NULL = any
  base_tier_metric TEXT,                       -- which qualifying-unit count drives the tier multiplier:
                                               -- 'activations' | 'upgrades' | 'boxes' | 'none' (NULL/none = no tiering)
  is_active        BOOLEAN NOT NULL DEFAULT true,
  notes            TEXT,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, name)
);

-- ── rules: each rule MATCHES sale lines + defines how they PAY ────────────────────────────────────
-- A line "matches" when match_field <match_op> match_value. If qualifies=true, matching lines count
-- toward the plan's tier metric AND pay per payout_kind. payout_kind:
--   flat_per_unit         amount × (# matching qualifying lines)
--   pct_mrc               pct × the matched subscriber's MRC (raw_mi, joined by mdn/subscriber)
--   pct_gp                pct × raw_sales.gp on the matched line
--   pct_price_over_cost   pct × max(0, ext_price − raw_catalog cost for product_id) on the matched line
--   flat                  amount once (a flat bonus if ANY line matches)
CREATE TABLE IF NOT EXISTS commcalc.commission_rule (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  plan_id     UUID NOT NULL REFERENCES commcalc.commission_plan(id) ON DELETE CASCADE,
  label       TEXT,
  match_field TEXT NOT NULL DEFAULT 'any',     -- contract_type|tender_type|department|category|product_desc|sku|trans_type|any
  match_op    TEXT NOT NULL DEFAULT 'equals',  -- equals | contains | in  (in = comma-separated, case-insensitive)
  match_value TEXT,
  qualifies   BOOLEAN NOT NULL DEFAULT true,   -- does a match qualify for commission (and count toward tier)
  payout_kind TEXT NOT NULL DEFAULT 'flat_per_unit',
  amount      NUMERIC NOT NULL DEFAULT 0,      -- for flat_per_unit / flat
  pct         NUMERIC NOT NULL DEFAULT 0,      -- for pct_mrc / pct_gp / pct_price_over_cost (0.10 = 10%)
  tiered      BOOLEAN NOT NULL DEFAULT false,  -- does this rule's payout scale by the plan's tier multiplier
  sort        INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS commission_rule_plan ON commcalc.commission_rule (org_id, plan_id, sort);

-- ── tiers: highest min_count ≤ the rep's qualifying-unit count wins → its multiplier ──────────────
CREATE TABLE IF NOT EXISTS commcalc.commission_tier (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  plan_id     UUID NOT NULL REFERENCES commcalc.commission_plan(id) ON DELETE CASCADE,
  metric      TEXT,                            -- 'activations'|'upgrades'|'boxes' — which count this tier reads
  min_count   INT NOT NULL DEFAULT 0,          -- applies when the rep's metric count ≥ this
  multiplier  NUMERIC NOT NULL DEFAULT 1,      -- payout × this for tiered rules (1.0 = full)
  sort        INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS commission_tier_plan ON commcalc.commission_tier (org_id, plan_id, min_count);

-- ── assignments: which plan applies to whom. Precedence employee > store > market > default ───────
CREATE TABLE IF NOT EXISTS commcalc.commission_plan_assignment (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  plan_id     UUID NOT NULL REFERENCES commcalc.commission_plan(id) ON DELETE CASCADE,
  scope       TEXT NOT NULL DEFAULT 'default', -- 'employee' | 'store' | 'market' | 'default'
  scope_value TEXT,                            -- employee rep-name (epay_salesperson) / store / market; NULL for default
  priority    INT NOT NULL DEFAULT 0,          -- tie-breaker within a scope (higher wins)
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS commission_plan_assignment_plan  ON commcalc.commission_plan_assignment (org_id, plan_id);
CREATE INDEX IF NOT EXISTS commission_plan_assignment_scope ON commcalc.commission_plan_assignment (org_id, scope, scope_value);

-- RLS open_all (per-tenant RLS is the later backstop; matches 057/058 and every commcalc table today)
ALTER TABLE commcalc.commission_plan            ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.commission_rule            ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.commission_tier            ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.commission_plan_assignment ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='commission_plan' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.commission_plan FOR ALL USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='commission_rule' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.commission_rule FOR ALL USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='commission_tier' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.commission_tier FOR ALL USING (true) WITH CHECK (true); END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc' AND tablename='commission_plan_assignment' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.commission_plan_assignment FOR ALL USING (true) WITH CHECK (true); END IF;
END $$;
