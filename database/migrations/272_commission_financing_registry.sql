-- 272_commission_financing_registry.sql — FINANCING VENDOR REGISTRY + per-store financing targets.
--
-- OWNER DIRECTIVE (in-chat 2026-08-04, verbatim): "need another report for tracking the financing, edge
-- in case of total and acima in case of boost, acima could also be added to total at a later date and
-- more vendors can be added to both carriers, this will be called Financing report, should have
-- assignable target for each store in target area and target based commission payout right now we have
-- flat payment, need it tiered levels."
--
-- WHAT THIS IS (RULE TWO — nothing about a vendor is hard-coded):
--   * a per-tenant list of FINANCING VENDORS (Edge, ACIMA, and whatever comes next),
--   * each assigned to ONE OR MORE CARRIERS (so "ACIMA could also be added to Total at a later date" is
--     one row in financing_vendor_carrier, not a code change),
--   * each with DETECTION RULES that say how a financed sale line is recognised — the same
--     (field, operator, value) vocabulary the commission-plan matcher already uses, defaulting to the
--     WORD-ANCHORED operator so a vendor name can never be matched as a substring of a device model
--     (see [[edge-is-financing-not-device-model]]: 'edge' is a TENDER, not a Motorola Edge),
--   * plus ASSIGNABLE PER-STORE FINANCING TARGETS (whole-store, or per vendor) used by the Financing
--     report's attainment and by the tiered payout in migration 273.
--
-- RUNNING THIS MIGRATION CHANGES NO PAY. It creates empty tables. The Financing report degrades to
-- "detection not configured" until a human maps a vendor, and the tiered payout (273) is inert until
-- tiers exist.
--
-- MULTI-TENANT: org_id NOT NULL on every table, indexed, and every uniqueness constraint is org-scoped.
-- RLS: enabled with ZERO policies and zero grants — all access is via the backend service role.

-- ── 1. the vendors ────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.financing_vendor (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,
  vendor_key        TEXT NOT NULL,                  -- stable machine key, e.g. 'edge', 'acima'
  label             TEXT NOT NULL,                  -- what a human sees, e.g. 'Total Edge financing'
  enabled           BOOLEAN NOT NULL DEFAULT true,
  -- WHERE the detection comes from:
  --   'rules'        the vendor's own financing_detection_rule rows (the normal case)
  --   'plan_rule'    INHERIT the matcher of an existing commcalc.commission_rule (detection_ref
  --                  ->> 'rule_ids') — this is how the Edge vendor reuses the SAME tender matcher the
  --                  edge pay rule already uses instead of forking a second classifier
  --   'acima_config' INHERIT the tenant's legacy ACIMA tender mapping
  --                  (commcalc.commission_config.acima_tenders, migration 094) — again a reuse, so the
  --                  report and the Boost ACIMA spiff can never disagree about what an ACIMA sale is
  detection_source  TEXT NOT NULL DEFAULT 'rules',
  detection_ref     JSONB,                          -- {"rule_ids": ["…"]} for detection_source='plan_rule'
  -- Which dollar figure the report calls the FINANCED AMOUNT. raw_sales does NOT carry the POS export's
  -- own "Financed Amount" column (verified on a real 78-col export: populated on 1 of 12,988 rows), so
  -- this is an explicit, labelled choice rather than a silent guess:
  --   'unit_line'   (default) the Ext Price of the financed DEVICE line only
  --   'transaction' the Ext Price of every detected line of the transaction
  amount_basis      TEXT NOT NULL DEFAULT 'unit_line',
  sort_order        INT  NOT NULL DEFAULT 100,
  notes             TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, vendor_key)
);
CREATE INDEX IF NOT EXISTS financing_vendor_org_idx ON commcalc.financing_vendor (org_id, sort_order);

COMMENT ON TABLE commcalc.financing_vendor IS
  'Per-tenant financing vendors (Edge, ACIMA, …). A vendor serves one or more carriers via '
  'financing_vendor_carrier, and is recognised in the sales data via financing_detection_rule or by '
  'INHERITING an existing matcher (a commission_rule, or the legacy acima_tenders mapping). No vendor, '
  'carrier or pattern is hard-coded anywhere in the application.';

-- ── 2. vendor -> carrier(s). A vendor may serve MANY carriers; adding one is a row, not a release ──
CREATE TABLE IF NOT EXISTS commcalc.financing_vendor_carrier (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  vendor_key   TEXT NOT NULL,
  carrier_id   UUID,                                -- commcalc.carrier.id; NULL = "any carrier"
  carrier_name TEXT,                                -- denormalised for display / carrier-less tenants
  enabled      BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS financing_vendor_carrier_uq
  ON commcalc.financing_vendor_carrier (org_id, vendor_key, COALESCE(carrier_id, '00000000-0000-0000-0000-000000000000'::uuid));
CREATE INDEX IF NOT EXISTS financing_vendor_carrier_org_idx
  ON commcalc.financing_vendor_carrier (org_id, vendor_key);

-- ── 3. detection rules — same shape as a commission_rule matcher, word-anchored by default ────────
CREATE TABLE IF NOT EXISTS commcalc.financing_detection_rule (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  vendor_key  TEXT NOT NULL,
  match_field TEXT NOT NULL DEFAULT 'tender_type',  -- tender_type|product_desc|department|category|contract_type|trans_type
  match_op    TEXT NOT NULL DEFAULT 'word',         -- word|equals|contains|in|prefix|suffix  (word = anchored)
  match_value TEXT NOT NULL,
  priority    INT  NOT NULL DEFAULT 100,
  enabled     BOOLEAN NOT NULL DEFAULT true,
  notes       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS financing_detection_rule_org_idx
  ON commcalc.financing_detection_rule (org_id, vendor_key, priority);

COMMENT ON COLUMN commcalc.financing_detection_rule.match_op IS
  'Default ''word'' is WORD-ANCHORED (the token, never a substring). This is the guard against the '
  'model-name collision class: a ''contains'' rule for ''edge'' would classify every Motorola Edge as a '
  'financed sale. Substring matching remains available but must be chosen deliberately.';

-- ── 4. assignable per-store financing targets ─────────────────────────────────────────────────────
-- Deliberately its OWN table rather than a column on commcalc.targets: it supports an optional
-- per-VENDOR breakdown (vendor_key NULL = the store's whole-financing target) and it cannot break the
-- existing Target Settings save if this migration has not been run yet.
CREATE TABLE IF NOT EXISTS commcalc.financing_target (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL,
  period        TEXT NOT NULL,                      -- stored as the page's period spelling; reads use _pvariants
  store_code    TEXT NOT NULL,
  vendor_key    TEXT,                               -- NULL = store total across every vendor
  target_units  NUMERIC NOT NULL DEFAULT 0,
  target_amount NUMERIC,                            -- optional $ target; NULL = not stated (never 0-by-default)
  notes         TEXT,
  updated_by    TEXT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS financing_target_uq
  ON commcalc.financing_target (org_id, period, store_code, COALESCE(vendor_key, ''));
CREATE INDEX IF NOT EXISTS financing_target_org_idx
  ON commcalc.financing_target (org_id, period);

COMMENT ON TABLE commcalc.financing_target IS
  'Assignable monthly financing target per store (and optionally per vendor) for a period. Read by the '
  'Financing report''s attainment column and by the target-attainment tiers in migration 273. A store '
  'with no row has NO financing target — attainment is reported as "no target set", never as 0%.';

-- ── 5. RLS: enabled, ZERO policies, ZERO grants (backend service role only) ───────────────────────
ALTER TABLE commcalc.financing_vendor          ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.financing_vendor_carrier  ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.financing_detection_rule  ENABLE ROW LEVEL SECURITY;
ALTER TABLE commcalc.financing_target          ENABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';

SELECT 'Migration 272 complete — commcalc.financing_vendor + financing_vendor_carrier + '
       'financing_detection_rule + financing_target (registry + per-store targets; no vendor seeded in '
       'SQL, no pay changed)' AS status;
