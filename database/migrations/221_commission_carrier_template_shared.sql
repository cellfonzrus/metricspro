-- 221_commission_carrier_template_shared.sql — mark a carrier's payout config as a SHAREABLE TEMPLATE.
--
-- WHY: a tenant (e.g. Luxelink / Total) needs the SAME carrier payout structure the house already built
-- (the Total Wireless carrier + its payout_schedule/_line curves + product_mrc catalog — mig 078). Rather
-- than a hard-coded one-off SQL copy, the "carrier payout template" CLONER (GET /commcalc/carrier-template/
-- sources + POST /commcalc/carrier-template/clone) re-stamps that config into the target org with new UUIDs.
--
-- Cloning READS ACROSS ORGS (source = the house template), so it MUST NOT become a cross-tenant read hole.
-- The gate: a source carrier is only clonable when it is EXPLICITLY marked shareable. `template_shared`
-- is that opt-in flag. /sources lists ONLY template_shared=true carriers (regardless of org); the clone
-- endpoint REFUSES any source that is not template_shared. This is the ONLY cross-org read in the module.
--
-- ADDITIVE + IDEMPOTENT: ADD COLUMN IF NOT EXISTS + a naturally-idempotent seed UPDATE. Safe to re-run.
-- Until this migration runs, the cloner degrades to a SAFE REFUSAL (no shareable sources, no clone) — it
-- can never leak another tenant's config, and no unrelated page breaks.

ALTER TABLE commcalc.carrier
  ADD COLUMN IF NOT EXISTS template_shared BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN commcalc.carrier.template_shared IS
  'When true, this carrier''s payout config (payout_schedule/_line + product_mrc) is a SHAREABLE template: '
  'it appears in /commcalc/carrier-template/sources for OTHER orgs to clone. Default false. The clone '
  'endpoint refuses any source that is not template_shared — this flag is the only cross-org read gate.';

-- small partial index: /sources reads every shared carrier across orgs (rare rows) with no org filter.
CREATE INDEX IF NOT EXISTS carrier_template_shared_idx
  ON commcalc.carrier (template_shared) WHERE template_shared = true;

-- SEED (the ONLY house-specific line in this migration — pure DATA MARKING, not code logic):
-- make the house's mig-078 'Total Wireless' carrier a shareable template so Luxelink (and any future
-- Total tenant) can import it from the UI. Naming, not branching: no carrier/tenant logic is hard-coded.
UPDATE commcalc.carrier
   SET template_shared = true
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND name  = 'Total Wireless';

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 221 complete — carrier.template_shared added; '
       || (SELECT count(*) FROM commcalc.carrier WHERE template_shared = true)::text
       || ' carrier(s) marked shareable' AS status;
