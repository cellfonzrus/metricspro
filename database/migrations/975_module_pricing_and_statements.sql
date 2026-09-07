-- 975_module_pricing_and_statements.sql — price per (plan x module) + the itemized tenant statement
--
-- OWNER DIRECTIVE 2026-09-05 (sanjot@): "the billing engine should list all the modules and an option
-- to assign price against them, a drop down menu to assign what kind of plan could belong to like
-- free, starter, premium etc so those items can be checked off from the multi user and assigned a
-- price right next to it" + "have an itemized statement for the tenant for a clear visibility
-- including their monthly fee".
--
-- ══ DUPLICATE CHECK — THREE OF THE FOUR PIECES ALREADY EXISTED ═════════════════════════════════
-- Searched the index for plan / package / price / module before writing. What is REUSED untouched:
--   · `core.entitlements.MODULE_CATALOG` / `core.module_catalog` (mig 700) is THE module registry.
--     The pricing grid is DERIVED from it, so a module added to the platform appears automatically
--     with an explicit UNPRICED cell. Nothing here hand-writes a module list — `main.py`'s /health
--     already shipped that bug once (a hardcoded literal that "CONFIDENTLY MISREPRESENTS the
--     deployment" by omitting eleven modules), and here the same bug would mean a module silently
--     billing nothing.
--   · `storeops.pricing_package` (mig 908) IS the plan/tier table. "free / starter / premium" are
--     ROWS the operator edits, NOT a code enum (RULE TWO) — a new tier needs no deploy. NO parallel
--     plan table is created here.
--   · `storeops.tenants.package_key` (mig 908) already assigns a tenant to a plan. Reused as-is.
--   · mig 908's posture is PRESERVED: nothing is public by default and "a price the operator did not
--     type must never reach the public internet". This migration adds NO public surface at all — the
--     anonymous GET /billing/public-pricing keeps serving only its published display fields and
--     never the internal `notes`.
--   · `core.ai_call_audit` / `core.token_rates` / `core.ai_margin_config` supply the AI lines, so AI
--     spend appears as line items on the SAME statement rather than in a second system.
-- Only two things were genuinely missing: a PRICE PER (plan, module), and the statement record.
--
-- ══ "NOTHING IS FOR FREE" vs AN UNPRICED MODULE — THEY ARE DIFFERENT ════════════════════════════
-- Three states are kept distinct all the way onto the invoice, because collapsing them is how a
-- module bills nothing forever without anyone noticing:
--     included   the plan's monthly fee covers it — an explicit operator decision, $0 on the line
--                and LABELLED as included.
--     priced     a unit price the operator typed. $0.00 is legitimate IF the operator typed 0.
--     UNPRICED   nobody set a price. The line shows the usage, says "not yet priced", is EXCLUDED
--                from the total, and the whole statement is flagged incomplete and unsendable.
-- A quiet $0.00 would under-charge silently and permanently. Same honesty rule already applied to
-- grey "not monitored" control-box lamps and to unmetered AI call sites.
--
-- ══ MONEY CORRECTNESS ═════════════════════════════════════════════════════════════════════════
-- 1. NO RETROACTIVE CHANGE. `module_price` rows are EFFECTIVE-DATED and resolved for the period
--    being billed (`statement.price_for` — the same tenant/plan-over-default, newest-effective-date
--    shape as `fix_pipeline.rate_for` and `ai_usage.margin_for`; ONE resolution idea used three
--    times, not three implementations). Changing a price = INSERT a row with a new effective_date.
--    But `storeops.pricing_package.price` (the monthly fee) has NO effective_date and can be edited
--    in place, so a CLOSED statement is additionally FROZEN into `core.billing_statement` and read
--    back verbatim. `harness_module_billing.py` §F proves it: close, then change BOTH a module price
--    and the plan's monthly fee, re-read — byte-identical.
-- 2. ROUNDING — deliberately DIFFERENT from mig 973's AI rule, for a stated reason. AI usage
--    produces ONE figure, so it sums at full precision and rounds once. A STATEMENT is a document a
--    human checks with a calculator, so each LINE is quantised once to cents (ROUND_HALF_UP) and the
--    total is the SUM OF THE QUANTISED LINES — never a separately rounded grand total, which is how
--    an invoice ends up off by a cent from its own lines. Inside a line, calls x unit_price is
--    computed at full Decimal precision first: 100,000 calls x $0.000015 is $1.50, whereas rounding
--    per call first gives $0.00 — a 100% billing error, proven in the harness.
--
-- SAFE: additive + idempotent. Re-runnable.
-- MONEY-TOUCHING: YES. Per house convention every seed that would assign a REAL price or plan to a
--   REAL tenant is left COMMENTED OUT below for owner approval. What ships is structure only: no
--   module price and no tenant plan assignment is created, so applying this migration cannot start
--   charging anybody anything. Every module simply reads UNPRICED until the owner prices it.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5). Every pricing
--   endpoint is `_require_super_admin`, fail-closed, server-side.

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. PRICE PER (PLAN x MODULE) — the operator grid's storage
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- One row = "on plan <plan_key>, module <module_key> is charged like this, from <effective_date>".
-- `plan_key` references storeops.pricing_package.key (NOT enforced by FK: pricing_package lives in
-- another schema and mig 908 lets the operator delete a package; an orphaned price row must not
-- block that, it simply stops resolving). The special plan key 'default' applies to any tenant whose
-- plan has no row of its own — the "checked off and priced" default column on the grid.
CREATE TABLE IF NOT EXISTS core.module_price (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_key       TEXT NOT NULL,                  -- 'free' | 'starter' | 'premium' | … | 'default'
  module_key     TEXT NOT NULL,                  -- an entitlement module key (core.module_catalog)
  -- HOW it is charged. 'unpriced' is never STORED — the ABSENCE of a row is what unpriced means, so
  -- there is exactly one representation of "nobody has priced this" and it cannot drift.
  mode           TEXT NOT NULL DEFAULT 'per_call'
                 CHECK (mode IN ('per_call','flat','included')),
  unit_price     NUMERIC CHECK (unit_price IS NULL OR unit_price >= 0),
                 -- per_call: $ per billable call. flat: $ per period. included: NULL (fee covers it).
  is_active      BOOLEAN NOT NULL DEFAULT true,
  -- Changing a price = INSERT a row with a later effective_date; never UPDATE an old one, or you
  -- move history. The append-only history is also the audit trail: who set what, when, and why.
  effective_date DATE NOT NULL DEFAULT CURRENT_DATE,
  changed_by     TEXT,
  note           TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (plan_key, module_key, effective_date)
);
CREATE INDEX IF NOT EXISTS module_price_lookup
  ON core.module_price(plan_key, module_key, effective_date DESC);

-- A priced mode must carry an amount; `included` must not. Catches the mis-entry that would
-- otherwise bill $0 silently — the exact failure this whole migration is written against.
DO $$ BEGIN
  BEGIN
    ALTER TABLE core.module_price ADD CONSTRAINT module_price_mode_has_amount CHECK (
      (mode = 'included' AND unit_price IS NULL)
      OR (mode IN ('per_call','flat') AND unit_price IS NOT NULL)
    );
  EXCEPTION WHEN duplicate_object THEN NULL; END;
END $$;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. THE STATEMENT RECORD — open is a live view; CLOSED is frozen
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- An OPEN statement is computed on read from the counters and the prices in force. Closing WRITES
-- the whole document here, and from then on it is READ, never recomputed (see MONEY CORRECTNESS 1).
CREATE TABLE IF NOT EXISTS core.billing_statement (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  period_start  DATE NOT NULL,
  period_end    DATE NOT NULL,
  status        TEXT NOT NULL DEFAULT 'closed' CHECK (status IN ('open','closed')),
  plan_key      TEXT,
  plan_name     TEXT,
  currency      TEXT NOT NULL DEFAULT 'USD',
  total_usd     NUMERIC,
  total_calls   BIGINT NOT NULL DEFAULT 0,
  billable_calls BIGINT NOT NULL DEFAULT 0,
  -- The whole itemized document, frozen: every line, its price, its mode, and its note. This is what
  -- makes a closed statement defensible a year later without re-deriving it from tables that have
  -- since changed.
  lines         JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Whether anything was left UNPRICED at close. Frozen WITH the statement on purpose: it must never
  -- be possible to look at a closed statement and not know something was unpriced when it was sent.
  complete      BOOLEAN NOT NULL DEFAULT false,
  unpriced      JSONB NOT NULL DEFAULT '[]'::jsonb,
  closed_by     TEXT,
  closed_at     TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, period_start, period_end)       -- one statement per tenant per period
);
CREATE INDEX IF NOT EXISTS billing_statement_org
  ON core.billing_statement(org_id, period_start DESC);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. SEEDS — STRUCTURE ONLY. No price, no plan assignment, no charge.
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Deliberately EMPTY. Every module therefore reads UNPRICED on the operator grid until the owner
-- prices it, which is the honest starting state: the grid's whole purpose is to show the holes.
-- Applying this migration cannot bill anyone anything.
--
-- ── MONEY-TOUCHING SEEDS — LEFT COMMENTED OUT FOR OWNER APPROVAL (house convention) ────────────
-- (a) Create the plan TIERS the owner named. These are storeops.pricing_package rows — the SAME
--     table the public price list already uses — so they inherit its is_public = false default and
--     nothing reaches the marketing site until the owner publishes it:
--
--   INSERT INTO storeops.pricing_package (key, name, price, cycle, currency, unit_label, sort_order)
--   VALUES ('free',    'Free',    0,   'monthly', 'USD', 'per month', 10),
--          ('starter', 'Starter', 199, 'monthly', 'USD', 'per month', 20),
--          ('premium', 'Premium', 499, 'monthly', 'USD', 'per month', 30)
--   ON CONFLICT (key) DO NOTHING;
--
-- (b) Price a module on a plan. `included` = covered by that plan's monthly fee; `per_call` = a
--     usage charge on top. Do this for EVERY module x plan, or the statement stays incomplete:
--
--   INSERT INTO core.module_price (plan_key, module_key, mode, unit_price, effective_date, changed_by)
--   VALUES ('premium', 'commissions', 'included', NULL,   CURRENT_DATE, '<who approved>'),
--          ('starter', 'commissions', 'per_call', 0.01,   CURRENT_DATE, '<who approved>'),
--          ('free',    'commissions', 'per_call', 0.05,   CURRENT_DATE, '<who approved>');
--
-- (c) Put a tenant on a plan:
--
--   UPDATE storeops.tenants SET package_key = 'starter' WHERE org_id = '<tenant org_id>';
--
-- NOTE effective_date: use CURRENT_DATE or a FUTURE date so a new price applies going forward.
-- Back-dating deliberately re-prices an OPEN period — do it only knowingly. It can never move a
-- CLOSED statement.

ALTER TABLE core.module_price      ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.billing_statement ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  BEGIN REVOKE ALL ON core.module_price, core.billing_statement FROM anon, authenticated;
    EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN GRANT ALL ON core.module_price, core.billing_statement TO service_role;
    EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 975 — per-(plan x module) pricing + frozen itemized tenant statements' AS status;

-- Which module/plan combinations are still UNPRICED (i.e. billing nothing):
--   SELECT p.key AS plan, m.key AS module
--     FROM storeops.pricing_package p CROSS JOIN core.module_catalog m
--    WHERE NOT EXISTS (SELECT 1 FROM core.module_price mp
--                       WHERE mp.plan_key = p.key AND mp.module_key = m.key AND mp.is_active)
--    ORDER BY 1, 2;
-- A tenant's price history for one module (append-only = the audit):
--   SELECT effective_date, mode, unit_price, changed_by, note FROM core.module_price
--    WHERE plan_key = '<plan>' AND module_key = '<module>' ORDER BY effective_date DESC;
-- What a tenant was actually billed:
--   SELECT period_start, period_end, total_usd, complete, closed_at
--     FROM core.billing_statement WHERE org_id = '<tenant>' ORDER BY period_start DESC;
--
-- REVERT:
--   DROP TABLE IF EXISTS core.billing_statement;
--   DROP TABLE IF EXISTS core.module_price;
