-- 016_rep_aliases.sql — Rep name merge map (#4). Lets the user collapse same-person
-- name variants (e.g. "Abdul K" / "Abdul Kakar", "David" / "David Caba") into ONE
-- canonical rep so Targets dedupes + the hours↔actuals join populates correctly.
CREATE TABLE IF NOT EXISTS commcalc.rep_aliases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL,
  alias TEXT NOT NULL,
  canonical TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(org_id, alias)
);
ALTER TABLE commcalc.rep_aliases ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY open_all ON commcalc.rep_aliases FOR ALL TO anon, authenticated USING (true) WITH CHECK (true);
EXCEPTION WHEN OTHERS THEN NULL; END $$;
GRANT ALL ON commcalc.rep_aliases TO anon, authenticated, service_role;
NOTIFY pgrst, 'reload schema';
SELECT 'Migration 016 complete — commcalc.rep_aliases ready' AS status;
