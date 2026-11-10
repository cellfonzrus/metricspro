-- 1119_tenant_feature_gate_posture.sql — THE TENANT'S GATING POSTURE: does this tenant get what the
-- platform's house defaults give it (`inherit`), or only what a super admin has GRANTED it (`gated`)?
-- Band 1000+ (platform). Additive + idempotent (safe to re-run). NOT money-touching: nothing here
-- reads or writes a rate, plan, tier, payout or any calculation input — it decides what a tenant is
-- OFFERED.
--
-- OWNER (2026-11-10), verbatim: "why is vidapay connector showing in boost ? we need to get these
-- things under control before we roll out for outside customers — have an agent asses and create a
-- dashboard for the super admin whereby these things can be gated out for the respective tenants
-- right from the get go"
--
-- WHY HIS CASE HAPPENED (measured in production 2026-11-10, before building):
--   · The platform keeps its DEFAULTS on org '00000000-0000-0000-0000-000000000001', which every
--     tenant inherits. That org id IS a real tenant (storeops.tenants → 'Cellfonz R Us'), so "the
--     house default" and "that tenant's own row" are THE SAME ROW. Five commcalc.report_pull_map
--     rows carry processor = 'vidapay'; the carrier-vocabulary rows scope 'report_term:total' spell
--     the vendor. A tenant that doubles as the house org therefore cannot be gated at all.
--   · The gate that WOULD have hidden them is right and was asked by every connector surface
--     (commcalc.connector_registry, mig 1014) — but GET /commcalc/report-pull-map listed
--     connector-keyed config rows without it. Fixed in the same PR, at that read's one home.
--   · Underneath both: entitlement was ALL-ACCESS BY DEFAULT ("a tenant with no billing_plan …
--     automatically receives EVERY module, including every module that ships in the future").
--     Measured: all four tenants hold all 15 modules enabled; storeops.billing_plan is EMPTY.
--
-- THE CLASS (CLAUDE.md "A fix is a DESIGN fix"): INHERITANCE IS NOT A DECISION. Three mechanisms
-- answered "is this on for this tenant" — module entitlement, the connector registry, the report-kind
-- registry — and all three answered by inheriting a house row, so nothing recorded that a human had
-- decided. This column is the missing fact, and ONE truth table reads it:
-- backend/app/modules/core/feature_gate.decide.
--
-- DUPLICATE CHECK (build gate — what was searched and why none of it is this column):
--   · storeops.tenant_modules (053) + core.module_catalog (700) + storeops.billing_plan — WHICH
--     MODULES a tenant holds. Still the entitlement mirror; entitlements.sync_tenant DERIVES it from
--     the answer, which is exactly why it cannot also BE the decision.
--   · commcalc.ui_label_override scope 'cap' (068) — THE super-admin override rows, already keyed
--     'connector:<key>' / 'kind:<key>' / 'carrier:' / 'pos:'. REUSED as the grant home for all three
--     kinds; 'module:<key>' is one more namespace in the mechanism that exists, not a new table.
--   · commcalc.connector_registry (1014) / commcalc.report_kind (1010) — what a connector or report
--     kind APPLIES TO. A fact about the thing; this is a decision about the tenant.
--   · commcalc.connector_route_policy (998) — whether ONE ROUTE of a connector may run. Below this.
--   · core.module_catalog.applies_to_vertical (1020) — which KIND OF BUSINESS a module is for. Feeds
--     the `by_rule` half and is not re-derived.
--   · storeops.tenants.plan_status / trial_ends_at / package_key — billing state, not reach.
-- None holds "a human decided this tenant may / may not have this thing". This does.
--
-- THE DEFAULT IS 'gated' AND EVERY TENANT ALIVE TODAY IS BACKFILLED TO 'inherit'. That pair is the
-- whole migration: the owner's "gated from the get go" applies to tenants created FROM NOW ON, and
-- the four live tenants keep answering exactly as they did this morning. A default of 'gated' with no
-- backfill would lock four running businesses out of their own platform on deploy.
--
-- DEGRADES: until this runs, feature_gate.load_posture returns 'inherit' for everybody and the
-- platform behaves byte-identically — the posture of the entire feature before it is applied.
--
-- REVERT:
--   -- put one tenant back on the house defaults:
--   --   UPDATE storeops.tenants SET feature_gate_posture = 'inherit' WHERE org_id = '<org>';
--   -- undo this migration entirely (the column is read adaptively, so dropping it is safe):
--   --   ALTER TABLE storeops.tenants DROP COLUMN IF EXISTS feature_gate_posture;
--   --   NOTIFY pgrst, 'reload schema';

ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS feature_gate_posture TEXT NOT NULL DEFAULT 'gated';

-- The vocabulary, as a CHECK so a typo cannot become a third posture nobody handles. Added
-- separately and idempotently so a re-run never fails on an existing constraint.
DO $p$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname = 'tenants_feature_gate_posture_chk'
       AND conrelid = 'storeops.tenants'::regclass
  ) THEN
    ALTER TABLE storeops.tenants
      ADD CONSTRAINT tenants_feature_gate_posture_chk
      CHECK (feature_gate_posture IN ('inherit','gated'));
  END IF;
END $p$;

COMMENT ON COLUMN storeops.tenants.feature_gate_posture IS
  'THE TENANT''S GATING POSTURE (owner 2026-11-10). ''inherit'' = answer as the platform did before the gate existed: the house defaults, the tenant''s own declaration, all-access entitlement. ''gated'' = DEFAULT DENY: this tenant is offered only the modules, connectors and report kinds a super admin has GRANTED it (commcalc.ui_label_override scope ''cap'', keys ''module:<key>'' / ''connector:<key>'' / ''kind:<key>''). The column default is ''gated'' so a NEW tenant starts with nothing; every tenant existing when this migration ran was backfilled to ''inherit''. ONE reader: backend/app/modules/core/feature_gate.load_posture; ONE truth table: feature_gate.decide, dereferenced by entitlements.effective_modules, connector_registry.visible/scope and report_kinds.visible_kinds/hidden_kinds. Surface: /admin/tenant-gates (super admin only).';

-- ── THE BACKFILL — every tenant that existed when this was WRITTEN keeps today's behaviour. The
--    cutoff is a FIXED date, deliberately, not NOW(): with NOW() a re-run of this idempotent file
--    would re-open every tenant created since, including a brand-new one a super admin has
--    deliberately left gated. A tenant created after the cutoff is gated, which is the owner's ask.
UPDATE storeops.tenants
   SET feature_gate_posture = 'inherit'
 WHERE feature_gate_posture = 'gated'
   AND created_at < '2026-11-10T00:00:00Z'::timestamptz;

-- storeops.tenants already carries its grants from its own migration; this file creates no relation,
-- so there is nothing new for the data-API grant guard to grant. Stated rather than left unsaid.

NOTIFY pgrst, 'reload schema';
