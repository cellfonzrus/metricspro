-- 973_ai_usage_billing.sql — per-tenant AI usage BILLING: margin config + closed-period snapshots
--
-- OWNER DIRECTIVE 2026-09-05 (sanjot@): "For every tenant ai usage counter needs to be built and a
-- cost assigned at the super admin level, the cost for the tenant will be cost of the super admin /
-- platform per token paid plus % or flat margin assigned by the super admin".
--
--     billable = PLATFORM COST (what we pay per token) + MARGIN (super-admin assigned, per tenant)
--
-- DUPLICATE CHECK (CLAUDE.md build gate). Searched the index for token / cost / rate / margin /
-- billing before writing. Three of the four pieces ALREADY EXISTED and are reused untouched:
--   · `core.token_rates` (mig 718) — THE only $/MTok source. NOT re-created, NOT shadowed, and no
--     fallback rate is introduced anywhere: a model with no rate row reports "no active rate", never
--     $0 (mig 718's rule, kept verbatim).
--   · `core.fix_pipeline.rate_for` — resolves WHICH rate applies (tenant row over house row, newest
--     effective_date <= the date). That is exactly the effective-dating this billing needs, so
--     `billing/ai_usage.py` imports and calls it rather than writing a second resolver.
--   · `core.ai_call_audit` (mig 972) — the per-call meter. It already stores input and output tokens
--     SEPARATELY, per org, with a timestamp, which is what lets this bill on the real in/out split
--     instead of mig 718's blended assumption (output tokens cost ~5x input; blending would
--     systematically over-bill input-heavy tenants and under-bill output-heavy ones).
--   · `app/modules/billing/` — tenant billing already has a home (pricing.py, platform_costs.py,
--     trial.py, router.py). The margin config and the tenant price live THERE, not in a new module.
-- Only the MARGIN and the CLOSED-PERIOD RECORD were genuinely missing. That is all this adds.
--
-- ══ MONEY CORRECTNESS: WHY BOTH EFFECTIVE DATING **AND** A SNAPSHOT ══════════════════════════════
-- This bills real tenants, so "what were they charged in August" must have exactly one answer
-- forever. Two different failure modes need two different defences:
--
--   (a) EFFECTIVE DATING protects against a NEW rate/margin. Every call is priced with the row in
--       force ON THE DAY OF THE CALL, so publishing a new rate today cannot re-price yesterday. The
--       live seeded data already exercises this: claude-sonnet-5 is $2/$10 from 2026-01-01 and
--       $3/$15 from 2026-09-01, and an August call must stay on the introductory rate forever.
--       This is why `ai_margin_config` is EFFECTIVE-DATED and APPEND-ONLY by convention: changing a
--       tenant's margin means INSERTING a row with a new effective_date, never editing the old one.
--       A side benefit is that the margin history IS its own audit trail — every row carries who set
--       it and when, so "who changed this tenant's margin, when, and from what" is a SELECT.
--
--   (b) A SNAPSHOT protects against an EDITED rate/margin. Effective dating cannot help if somebody
--       edits an EXISTING row in place — mig 718 explicitly allows that (`updated_by`), and it is a
--       legitimate thing to do when a rate was typed wrong. So closing a period FREEZES the applied
--       rate, the applied margin and the resulting figures onto `ai_usage_period`, and a closed
--       period is thereafter READ, never recomputed. `ai_usage.price_period(..., frozen=)` returns
--       the stored numbers untouched.
--       Proven in `backend/harness_ai_usage.py` §C: close a period, then edit the rate row in place
--       AND publish a 90% margin, re-read — the closed figures are byte-identical.
--
-- ROUNDING (stated so it can be checked): per-call costs are carried at FULL Decimal precision and
-- the TOTAL is quantised ONCE — cost to 6 dp, the billed amount to 2 dp, ROUND_HALF_UP. Rounding
-- each call to cents and summing loses up to half a cent per call (~$5 per 1,000 calls); the harness
-- demonstrates the drift on 1,000 sub-cent calls. HALF_UP rather than Python's default banker's
-- rounding because HALF_EVEN surprises accountants.
--
-- WHAT "FLAT" MEANS — stated plainly FOR THE OWNER TO CORRECT. "% or flat margin" has three
-- defensible readings and they are not close to each other, so this does not guess:
--     flat_basis = 'period' (DEFAULT) — one fixed USD amount per tenant per billing period. Shipped
--         as the default because that is what a service fee on top of pass-through cost normally
--         means, and it is the only reading that stays predictable when usage is spiky.
--     flat_basis = 'call'             — a fixed USD amount per AI call.
-- A per-TOKEN flat is deliberately NOT offered: that is a rate, not a margin, and the owner already
-- controls per-token pricing in core.token_rates. Percent and flat may combine
-- (mode 'percent_plus_flat') so "cost + 20% + $50/month" needs no third mode.
--
-- SAFE: additive + idempotent. Re-runnable.
-- MONEY-TOUCHING: YES — this table decides what a tenant is charged. Per house convention the seed
--   that would assign a REAL margin to a REAL tenant is left COMMENTED OUT at the bottom for owner
--   approval. What IS seeded is a house row at ZERO margin, i.e. pass-through: shipping this
--   migration cannot, by itself, start charging anybody anything.
-- SECURITY: RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5). Every
--   endpoint over these tables is super-admin gated, fail-closed, server-side.

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. MARGIN CONFIG — per tenant, effective-dated, append-only (so history IS the audit trail)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS core.ai_margin_config (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL,            -- the house org's row is the platform default
  mode           TEXT NOT NULL DEFAULT 'percent'
                 CHECK (mode IN ('percent','flat','percent_plus_flat')),
  percent        NUMERIC NOT NULL DEFAULT 0 CHECK (percent >= 0),   -- e.g. 20 = +20% on platform cost
  flat_usd       NUMERIC NOT NULL DEFAULT 0 CHECK (flat_usd >= 0),
  flat_basis     TEXT NOT NULL DEFAULT 'period' CHECK (flat_basis IN ('period','call')),
  -- The rate/margin in force ON THE DAY OF A CALL prices that call. Changing a margin = INSERT a new
  -- row with a later effective_date; never UPDATE an old one, or you move history (see (a) above).
  effective_date DATE NOT NULL DEFAULT CURRENT_DATE,
  is_active      BOOLEAN NOT NULL DEFAULT true,
  -- The audit the owner asked for specifically: who set this, when, and (in `note`) why. Because
  -- rows are append-only, the previous values are still here — old -> new is two rows, not a diff.
  changed_by     TEXT,
  note           TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (org_id, effective_date)
);
CREATE INDEX IF NOT EXISTS ai_margin_config_org ON core.ai_margin_config(org_id, effective_date DESC);

-- CHECK constraints cannot express "negative is meaningless" for a margin any better than >= 0
-- above, but a MODE/VALUE mismatch can be caught: a 'percent' margin with only a flat amount set is
-- almost certainly a mis-entry, so it is rejected rather than silently billing $0 margin.
DO $$ BEGIN
  BEGIN
    ALTER TABLE core.ai_margin_config ADD CONSTRAINT ai_margin_mode_has_value CHECK (
      (mode = 'percent'          AND percent > 0)
      OR (mode = 'flat'          AND flat_usd > 0)
      OR (mode = 'percent_plus_flat' AND (percent > 0 OR flat_usd > 0))
      OR (percent = 0 AND flat_usd = 0)      -- an explicit ZERO margin (pass-through) is legitimate
    );
  EXCEPTION WHEN duplicate_object THEN NULL; END;
END $$;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. THE BILLED PERIOD — open while it accrues, FROZEN once closed
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- An OPEN period is a live view: nothing is stored, `price_period` computes it from the audit rows
-- with each call priced on its own day. Closing WRITES the answer here, and from then on the stored
-- figures are what the tenant was charged — read, never recomputed.
CREATE TABLE IF NOT EXISTS core.ai_usage_period (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  period_start       DATE NOT NULL,
  period_end         DATE NOT NULL,
  status             TEXT NOT NULL DEFAULT 'closed' CHECK (status IN ('open','closed')),
  calls              INTEGER NOT NULL DEFAULT 0,
  priced_calls       INTEGER NOT NULL DEFAULT 0,
  unpriced_calls     INTEGER NOT NULL DEFAULT 0,   -- real spend we could NOT price (no rate row)
  input_tokens       BIGINT  NOT NULL DEFAULT 0,
  output_tokens      BIGINT  NOT NULL DEFAULT 0,
  tokens             BIGINT  NOT NULL DEFAULT 0,
  platform_cost_usd  NUMERIC,                      -- what WE paid (from core.token_rates)
  billable_usd       NUMERIC,                      -- what the TENANT is charged
  margin_usd         NUMERIC,                      -- billable - platform_cost
  -- Everything needed to DEFEND this invoice later, frozen at close: the applied margin, the
  -- per-purpose/per-model split, the unpriced accounting, and the metering coverage at the time.
  margin_snapshot    JSONB NOT NULL DEFAULT '{}'::jsonb,
  breakdown_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  closed_by          TEXT,
  closed_at          TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- One closed record per tenant per period. Re-closing the same period is an explicit re-open +
  -- close by a super-admin, never an accidental second invoice.
  UNIQUE (org_id, period_start, period_end)
);
CREATE INDEX IF NOT EXISTS ai_usage_period_org ON core.ai_usage_period(org_id, period_start DESC);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. SEEDS
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The HOUSE default is ZERO margin — pass-through at platform cost. Applying this migration
-- therefore cannot start charging any tenant anything; a charge requires the owner to set a margin.
INSERT INTO core.ai_margin_config (org_id, mode, percent, flat_usd, flat_basis, effective_date,
                                   changed_by, note)
VALUES ('00000000-0000-0000-0000-000000000001', 'percent', 0, 0, 'period', DATE '2026-01-01',
        'migration 973',
        'House default: ZERO margin (pass-through at platform cost). A tenant with no row of its own '
        'inherits this. Set a real margin per tenant at /admin/ai-usage — deliberately not seeded.')
ON CONFLICT (org_id, effective_date) DO NOTHING;

-- ── MONEY-TOUCHING SEED — LEFT COMMENTED OUT FOR OWNER APPROVAL (house convention) ─────────────
-- Assigning a real margin to a real tenant CHANGES WHAT THEY ARE BILLED, so no agent applies it.
-- To charge a tenant platform cost + 20%, the owner runs (substituting the org_id and their name):
--
--   INSERT INTO core.ai_margin_config (org_id, mode, percent, effective_date, changed_by, note)
--   VALUES ('<tenant org_id>', 'percent', 20, CURRENT_DATE, '<who approved>', 'Owner-approved margin');
--
-- To charge cost + 20% + $50/month instead:
--
--   INSERT INTO core.ai_margin_config (org_id, mode, percent, flat_usd, flat_basis, effective_date,
--                                      changed_by, note)
--   VALUES ('<tenant org_id>', 'percent_plus_flat', 20, 50, 'period', CURRENT_DATE, '<who>', '<why>');
--
-- NOTE effective_date: use CURRENT_DATE (or a FUTURE date) so the new margin applies going forward.
-- Back-dating deliberately re-prices an OPEN period — only do that knowingly. It can never move a
-- CLOSED one.

-- ── Security posture (AGENT_CONTRACT §5): RLS on, no policies, no anon/authenticated grants ────
ALTER TABLE core.ai_margin_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.ai_usage_period  ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  BEGIN REVOKE ALL ON core.ai_margin_config, core.ai_usage_period FROM anon, authenticated;
    EXCEPTION WHEN OTHERS THEN NULL; END;
  BEGIN GRANT ALL ON core.ai_margin_config, core.ai_usage_period TO service_role;
    EXCEPTION WHEN OTHERS THEN NULL; END;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 973 — per-tenant AI usage billing: effective-dated margin config + frozen period snapshots' AS status;

-- Who changed a tenant's margin, when, from what (append-only history = the audit):
--   SELECT effective_date, mode, percent, flat_usd, flat_basis, changed_by, note
--     FROM core.ai_margin_config WHERE org_id = '<tenant>' ORDER BY effective_date DESC;
-- What a tenant was actually billed, and what it cost us:
--   SELECT period_start, period_end, tokens, platform_cost_usd, margin_usd, billable_usd, closed_at
--     FROM core.ai_usage_period WHERE org_id = '<tenant>' ORDER BY period_start DESC;
-- Models producing real spend that we CANNOT price (add a core.token_rates row for each):
--   SELECT model, count(*), sum(input_tokens + output_tokens) FROM core.ai_call_audit
--    WHERE allowed AND model IS NOT NULL GROUP BY 1 ORDER BY 3 DESC;
--
-- REVERT:
--   DROP TABLE IF EXISTS core.ai_usage_period;
--   DROP TABLE IF EXISTS core.ai_margin_config;
