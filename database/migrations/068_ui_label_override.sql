-- 068_ui_label_override.sql — per-tenant DISPLAY-LABEL nicknames for nav items + groups (SaaS B-phase2).
--
-- WHY: the default nav labels are already neutral (the Boost→Carrier / VIP→Distributor sweep), but a
-- tenant may want to SEE their own words — "Distributors"→"Suppliers", "Payment Processor"→"VidaPay",
-- "Metrics - Rep/Store"→"Boost Portal". This stores a nickname per (org, scope, key) where key is a nav
-- href (scope='nav') or a group name (scope='group'). Read by GET /commcalc/nav-config and applied in the
-- sidebar at render time.
--
-- ADDITIVE + IDEMPOTENT + GRACEFUL: nothing else references this table; if it's absent, nav-config returns
-- an empty label map and the built-in labels render unchanged. It NEVER renames a DB column or route — it
-- only changes display text, so it can't break any data path.

CREATE TABLE IF NOT EXISTS commcalc.ui_label_override (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  scope      TEXT NOT NULL DEFAULT 'nav',   -- 'nav' (item href) | 'group' (group name)
  key        TEXT NOT NULL,                  -- the href or the group name being relabeled
  label      TEXT NOT NULL,                  -- the nickname shown to this tenant
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, scope, key)
);

ALTER TABLE commcalc.ui_label_override ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='commcalc'
                 AND tablename='ui_label_override' AND policyname='open_all') THEN
    CREATE POLICY open_all ON commcalc.ui_label_override FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 068 complete — commcalc.ui_label_override installed' AS status;
