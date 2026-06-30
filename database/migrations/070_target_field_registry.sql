-- 070_target_field_registry.sql — make the canonical TARGET FIELDS for ANY report type data, not code.
--
-- WHY (C-Phase2): column_mapping.TARGET_FIELDS hard-codes the canonical fields for the five seeded Boost
-- report keys (sales, comp_report, mi_report, payment_detail, carrier_commission). A tenant who needs a
-- report type we never shipped (an expenses feed, a chart-of-accounts import, a product catalog), or who
-- just wants to relabel a default field / add a header alias for better auto-detect, has nowhere to put it
-- → their columns can't be mapped. This generic registry stores each canonical target field as a ROW,
-- per (org, report_key). The backend MERGES it on top of the hard-coded defaults for EVERY report key —
-- the generalisation of the commission_field_catalog merge (066), but for any report, with no commission
-- semantics and (deliberately) NO schema DDL.
--
-- ADDITIVE + IDEMPOTENT + BOOST-SAFE: empty/un-migrated registry → the mapper falls back byte-for-byte to
-- the seeded Python defaults (today's behaviour). This table ONLY changes the FIELD LIST the mapping UI
-- offers + auto-suggests; it performs NO ALTER TABLE (unlike 066/067 it never grows a physical table) and
-- never touches the live calc, rep_commissions, or the legacy upload branches. A registry field on a
-- seeded report key is for relabel/alias/required; a brand-new field is meant for a NEW report type whose
-- target_table the tenant controls (set on report_definitions).

CREATE TABLE IF NOT EXISTS commcalc.target_field_registry (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  report_key     TEXT NOT NULL,                 -- the report this field belongs to (any key, seeded or new)
  target_field   TEXT NOT NULL,                 -- canonical field name (sanitised: ^[a-z][a-z0-9_]{0,62}$)
  label          TEXT,                          -- human label shown in the mapping UI
  transform      TEXT DEFAULT 'text',           -- column_mapping transform: text|number|int|date10|mdn|upper|lower|bool
  required       BOOLEAN DEFAULT false,         -- counts toward the report's readiness in the wizard matrix
  default_source TEXT DEFAULT '',               -- default source header (drives seed + auto-suggest)
  aliases        TEXT[] DEFAULT '{}',           -- extra source-header synonyms for auto-detect
  sort_order     INT DEFAULT 100,
  is_seeded      BOOLEAN DEFAULT false,         -- true = shipped default (protected); false = tenant-created
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, report_key, target_field)
);
CREATE INDEX IF NOT EXISTS target_field_registry_lookup
  ON commcalc.target_field_registry (org_id, report_key, sort_order);

ALTER TABLE commcalc.target_field_registry ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='target_field_registry' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.target_field_registry FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

-- No seed: the hard-coded column_mapping.TARGET_FIELDS remain the defaults; this registry is overlay-only,
-- so an empty table reproduces today's behaviour exactly.

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 070 complete — commcalc.target_field_registry installed' AS status;
