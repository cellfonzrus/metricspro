-- 257_commission_accessory_definition.sql   (mod-commission, band 200-299)
--
-- WHAT COUNTS AS AN ACCESSORY — a per-tenant DEFINITION.
--
-- OWNER DIRECTIVE 2026-08-01 (verbatim): "accessory option will be as per mapped manually and anything
-- which says accesspories or category accesory since every company defines in a different way ,
-- generally all screen protectors, cases headset , earphones, charger, cables , adapters fall under th
-- ecategory of accessories".
--
-- TWO MECHANISMS, ONE OBSERVATION:
--   (1) MANUAL MAPPING — the tenant maps its OWN observed SKUs / product descriptions / categories /
--       departments. Values are PICKED from what commcalc.raw_sales actually contains (RULE THREE), so
--       nothing is typed free-hand.
--   (2) THE FIELD RULE — when the line's DEPARTMENT or CATEGORY field itself says "accessor…", it is an
--       accessory. Deliberately NOT a product-NAME keyword rule: a 'case' or 'charger' name keyword
--       hits 'Casement' and 'Charger Port Repair', and this codebase already has a live scar from
--       name-keyword matching ('TW EDGE SPF' is the EDGE financing tender, not a Motorola Edge). The
--       code REFUSES to point the token rule at product_desc/sku and reports the refusal.
--   (3) "every company defines in a different way" -> per-tenant config, never code (contract RULE TWO).
--
-- ── THIS MIGRATION MOVES $0 ──────────────────────────────────────────────────────────────────────
-- It creates two tables + one nullable column and seeds a CLASS VOCABULARY for the house org as
-- status='proposed'. Nothing that decides a payout reads any of it: not calculator.py, not
-- commission_engine.py, not sale_installment_engine.py, not _run_calculation, not rep_commissions, not
-- targets_engine.py, not the P&L. The existing five accessory classifiers are UNCHANGED and still
-- decide every existing number. What ships alongside is a READ-ONLY agreement report showing, per
-- item, where each existing surface agrees with this definition and where it does not. Adopting the
-- definition as the PAY basis is a separate, owner-gated change (see docs/handoffs/commission.md).
--
-- ── WHY A NEW TABLE AND NOT commcalc.accessory_config ────────────────────────────────────────────
-- accessory_config holds three FLAT LISTS (departments / categories / product_keywords) that the live
-- money path reads through accessory_catalog.AccessoryClassifier. The shape needed here is ONE ROW PER
-- OBSERVED VALUE with a class, an explicit include/EXCLUDE flag and a proposed->confirmed lifecycle —
-- and, critically, adding rows to accessory_config would CHANGE WHAT IS PAID the moment they are
-- saved. A sibling table cannot. This mirrors the config-home decision already recorded for
-- ma_product_class (mig 254) and the gp_category_map shape (mig 069).
-- The one thing that DOES belong on accessory_config is the field-rule TOGGLE, so it is added there as
-- a nullable JSONB column rather than in a third table.
--
-- ── UNTIL THIS RUNS ──────────────────────────────────────────────────────────────────────────────
-- Every GET still returns 200 using the built-in class vocabulary + the default field rule
-- (read-only, nothing pre-confirmed) with ready:false and this filename named; the write endpoints
-- return a clear 400 naming this file, never a 500. The agreement report still renders — it simply
-- shows the definition with no manual mappings.
--
-- ADDITIVE + IDEMPOTENT + RLS-ZERO-POLICY: safe to re-run; RLS on, NO policies, NO anon/authenticated
-- grants (contract §5 — all access is via the backend service role).

-- ── 1) the class vocabulary — the owner's own seven classes, as PROPOSALS ───────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.accessory_class (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  class_key    TEXT NOT NULL,
  label        TEXT NOT NULL,
  description  TEXT,
  sort_order   INT  NOT NULL DEFAULT 500,
  status       TEXT NOT NULL DEFAULT 'proposed',   -- proposed | confirmed (the owner confirms in the UI)
  is_active    BOOLEAN NOT NULL DEFAULT true,
  confirmed_by TEXT,
  confirmed_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, class_key)
);
CREATE INDEX IF NOT EXISTS accessory_class_org ON commcalc.accessory_class (org_id, sort_order);

-- ── 2) the manual mapping: (tenant, field, EXACT observed value) -> accessory yes/no + class ────────
-- match_value stores the tenant's own spelling; matching folds case + trims (accessory_definition.
-- normalize). is_accessory=false is an explicit EXCLUSION — one product carved out of an otherwise
-- accessory department — and it beats the field rule.
CREATE TABLE IF NOT EXISTS commcalc.accessory_definition_map (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  match_field     TEXT NOT NULL,                   -- sku | product_desc | category | department
  match_value     TEXT NOT NULL,                   -- an observed value, picked from raw_sales
  is_accessory    BOOLEAN NOT NULL DEFAULT true,
  accessory_class TEXT,                            -- references accessory_class.class_key (soft)
  status          TEXT NOT NULL DEFAULT 'proposed',-- proposed | confirmed
  note            TEXT,
  confirmed_by    TEXT,
  confirmed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, match_field, match_value)
);
CREATE INDEX IF NOT EXISTS accessory_definition_map_lookup ON commcalc.accessory_definition_map (org_id, match_field);
CREATE INDEX IF NOT EXISTS accessory_definition_map_status ON commcalc.accessory_definition_map (org_id, status);
CREATE INDEX IF NOT EXISTS accessory_definition_map_class  ON commcalc.accessory_definition_map (org_id, accessory_class);

-- ── 3) the field-rule toggle, on the config table that already owns accessory classification ───────
-- NULL = the code default {"enabled":true,"token_fields":["department","category"],"tokens":["accessor"]}.
-- token_fields is VALIDATED in code against ('department','category'); anything else (product_desc,
-- sku) is refused and the refusal is reported — a stored value can never turn this into a
-- product-name keyword matcher.
ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS definition_field_rule JSONB;

COMMENT ON COLUMN commcalc.accessory_config.definition_field_rule IS
  'Field rule for the per-tenant ACCESSORY DEFINITION (mig 257): treat a line as an accessory when its '
  'DEPARTMENT or CATEGORY field contains one of `tokens` (case-insensitive). NULL = code default '
  '{"enabled":true,"token_fields":["department","category"],"tokens":["accessor"]}. token_fields is '
  'restricted in code to department/category — product_desc and sku are REFUSED so this can never '
  'become a product-name keyword matcher. Read only by accessory_definition.py and its read-only '
  'report; no payout calculation reads it.';

-- RLS ON, ZERO POLICIES, ZERO GRANTS (contract §5).
ALTER TABLE commcalc.accessory_class           ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.accessory_definition_map  ENABLE ROW LEVEL SECURITY;

-- ── 4) seed the CLASS VOCABULARY for the house org, as PROPOSALS ───────────────────────────────────
-- These are the classes the owner listed, and they are LABELS, not matchers: nothing is classified by
-- them. status='proposed' on every row — the owner confirms (or renames, or deletes) on
-- /commcalc/accessory-definition. Every other tenant seeds itself via
-- POST /commcalc/accessory-definition/seed-classes, which stamps the CALLER's org_id.
-- Generated from accessory_definition.DEFAULT_CLASSES — the proof re-parses this SQL and set-compares,
-- so code and migration cannot drift.
INSERT INTO commcalc.accessory_class (org_id, class_key, label, description, sort_order, status)
VALUES
  ('00000000-0000-0000-0000-000000000001', 'screen_protector', 'Screen protectors', 'Tempered glass, film, privacy screens.', 10, 'proposed'),
  ('00000000-0000-0000-0000-000000000001', 'case', 'Cases', 'Phone/tablet cases, covers, folios, bumpers.', 20, 'proposed'),
  ('00000000-0000-0000-0000-000000000001', 'headset', 'Headsets', 'Over-ear / on-ear headsets and headphones.', 30, 'proposed'),
  ('00000000-0000-0000-0000-000000000001', 'earphone', 'Earphones', 'Earbuds, in-ear phones, wireless buds.', 40, 'proposed'),
  ('00000000-0000-0000-0000-000000000001', 'charger', 'Chargers', 'Wall bricks, car chargers, wireless pads, power banks.', 50, 'proposed'),
  ('00000000-0000-0000-0000-000000000001', 'cable', 'Cables', 'USB / lightning / HDMI and other cables.', 60, 'proposed'),
  ('00000000-0000-0000-0000-000000000001', 'adapter', 'Adapters', 'Dongles, converters, jack and port adapters.', 70, 'proposed'),
  ('00000000-0000-0000-0000-000000000001', 'other_accessory', 'Other accessory', 'An accessory that does not fit the classes above. Exists so a mapping is never forced into the wrong class.', 900, 'proposed')
ON CONFLICT (org_id, class_key) DO NOTHING;

-- NO MAPPING ROWS ARE SEEDED. A mapping keys on a value THIS tenant's own raw_sales contains; seeding
-- guesses would be exactly the free-text drift RULE THREE exists to prevent.

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 257 complete — accessory_class + accessory_definition_map + '
       'accessory_config.definition_field_rule. Eight class PROPOSALS seeded for the house org; zero '
       'mappings seeded; no payout, target, GP or P&L number reads any of it.' AS status;
