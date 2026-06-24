-- 038_carrier_category_map.sql
-- SaaS framework Phase 1: the canonical category-mapping layer. A carrier's raw compensation
-- category string → one of the 4 canonical components (RESIDUAL / COMMISSION / SPIFF / REIMBURSEMENT),
-- config-driven (no code), so reports that group by component work for Boost/Cricket/Metro/Total alike.
-- Onboard a new carrier = add its rows here, no code change. See docs/SAAS_FRAMEWORK.md §2–3.

CREATE TABLE IF NOT EXISTS commcalc.carrier (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  name        TEXT NOT NULL,            -- Boost, Cricket, Metro, Total
  code        TEXT,
  is_default  BOOLEAN DEFAULT false,
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, name)
);

CREATE TABLE IF NOT EXISTS commcalc.carrier_category_map (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  carrier_id   UUID,                    -- NULL = applies to any carrier (fallback rule)
  raw_category TEXT NOT NULL,           -- the pattern (interpreted per match_type)
  match_type   TEXT DEFAULT 'exact',    -- exact | prefix | contains | regex
  component    TEXT NOT NULL,           -- RESIDUAL | COMMISSION | SPIFF | REIMBURSEMENT
  subtype      TEXT,                    -- base | promo | bounty | subsidy | ...
  priority     INT DEFAULT 100,         -- lower = evaluated first (most-specific first)
  is_active    BOOLEAN DEFAULT true,
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, carrier_id, raw_category, match_type)
);
CREATE INDEX IF NOT EXISTS ccmap_lookup ON commcalc.carrier_category_map (org_id, carrier_id, priority);

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['commcalc.carrier', 'commcalc.carrier_category_map'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

-- Seed the default Boost carrier + its canonical category rules (derived from the live comp data).
INSERT INTO commcalc.carrier (org_id, name, code, is_default)
VALUES ('00000000-0000-0000-0000-000000000001', 'Boost', 'BOOST', true)
ON CONFLICT (org_id, name) DO NOTHING;

INSERT INTO commcalc.carrier_category_map (org_id, carrier_id, raw_category, match_type, component, subtype, priority)
SELECT c.org_id, c.id, v.raw, v.mt, v.comp, v.sub, v.pri
FROM commcalc.carrier c
CROSS JOIN (VALUES
  ('MI',            'exact',    'RESIDUAL',      'base',    10),
  ('ATU',           'exact',    'RESIDUAL',      'base',    10),
  ('Residual',      'contains', 'RESIDUAL',      'base',    15),
  ('Bounty',        'contains', 'SPIFF',         'bounty',  20),
  ('SPIFF',         'contains', 'SPIFF',         'bounty',  20),
  ('Accelerator',   'contains', 'SPIFF',         'bonus',   20),
  ('Reimbursement', 'contains', 'REIMBURSEMENT', 'subsidy', 30),
  ('Ramp Up',       'contains', 'REIMBURSEMENT', 'subsidy', 30),
  ('Subsidy',       'contains', 'REIMBURSEMENT', 'subsidy', 30),
  ('Promo',         'contains', 'COMMISSION',    'promo',   40),
  ('Offer',         'contains', 'COMMISSION',    'promo',   40),
  ('Activation',    'contains', 'COMMISSION',    'promo',   45),
  ('Upgrade',       'contains', 'COMMISSION',    'promo',   45),
  ('Port',          'contains', 'COMMISSION',    'promo',   45)
) AS v(raw, mt, comp, sub, pri)
WHERE c.org_id = '00000000-0000-0000-0000-000000000001' AND c.name = 'Boost'
ON CONFLICT (org_id, carrier_id, raw_category, match_type) DO NOTHING;
