-- 019_b2b_inventory.sql — b2bsoft "inventory quantity by date" snapshots, for the
-- on-inventory ↔ b2bsoft reconciliation (#7). Each row = qty of one device category at
-- one store as of a date. Categories are normalized to the 5 we reconcile:
-- iphone / android / tablet / watch / hotspot. Populated by manual upload now; by the
-- wsreports.b2bsoft.com sweep once creds are supplied (degrades gracefully until then).
CREATE TABLE IF NOT EXISTS commcalc.b2b_inventory (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  store       TEXT NOT NULL,
  category    TEXT NOT NULL,            -- iphone|android|tablet|watch|hotspot
  qty         INTEGER NOT NULL DEFAULT 0,
  as_of_date  DATE NOT NULL,
  source      TEXT DEFAULT 'upload',    -- 'upload' | 'sweep'
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (org_id, store, category, as_of_date)
);
CREATE INDEX IF NOT EXISTS b2b_inventory_org_date_idx ON commcalc.b2b_inventory(org_id, as_of_date);

ALTER TABLE commcalc.b2b_inventory ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON commcalc.b2b_inventory FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON commcalc.b2b_inventory TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 019 complete — commcalc.b2b_inventory ready' AS status;
