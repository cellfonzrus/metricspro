-- 050_org_hierarchy.sql — Configurable org-unit TREE + level config + manager assignments.
-- Run this in the Supabase SQL editor (Claude cannot run SQL). Idempotent — safe to re-run.
--
-- WHY: storeops.employees is a flat roster (no manager/level fields) and all data scoping is
-- client-side only. There is no way to model a real org (regions/districts/multi-market managers)
-- or to show a manager "the reps under me". This adds a CONFIGURABLE tree of org units with
-- USER-DEFINED levels (any depth), seeded from the existing store->market data.
--
-- DESIGN: stores stay in storeops.stores (the canonical store_code join key for the whole app) and
-- ATTACH to the tree via a new org_unit_id FK. The tree models only the levels ABOVE the store
-- (Company -> Region -> Market -> ...). Span resolution returns a store_codes[] array that drops
-- straight into the existing .in_('store_code', ...) filters -> zero changes to targets_engine or the
-- rollup endpoints. A manager assigned to a node sees that node's whole subtree (multi-node capable).
--
-- All objects live in storeops (PostgREST exposes storeops + commcalc; core is NOT .rpc-callable).
--
-- SECURITY NOTE: RLS is open_all here (matches the rest of the app). The span RPCs are a
-- DEFAULT-SCOPING convenience for the manager views, NOT a security boundary, until the Phase 5
-- backend enforcement lands.

-- ── Level config: named, ranked depth (rank 0 = root/Company) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.org_levels (
  id         BIGSERIAL PRIMARY KEY,
  org_id     UUID NOT NULL,
  name       TEXT NOT NULL,                 -- 'Company','Region','Market','District',...
  rank       INT  NOT NULL,                 -- 0..n depth
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, rank),
  UNIQUE (org_id, name)
);

-- ── The tree ───────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.org_units (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL,
  parent_id  UUID REFERENCES storeops.org_units(id) ON DELETE CASCADE,
  level_id   BIGINT REFERENCES storeops.org_levels(id),
  name       TEXT NOT NULL,
  code       TEXT,                          -- optional stable slug for idempotent seed match
  sort_order INT DEFAULT 0,
  is_active  BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS org_units_parent ON storeops.org_units (org_id, parent_id);
CREATE UNIQUE INDEX IF NOT EXISTS org_units_code_uq ON storeops.org_units (org_id, code) WHERE code IS NOT NULL;

-- ── Manager -> node (many-to-many: a person can manage many nodes; a node can have many managers) ─
CREATE TABLE IF NOT EXISTS storeops.org_managers (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  unit_id     UUID NOT NULL REFERENCES storeops.org_units(id) ON DELETE CASCADE,
  employee_id TEXT NOT NULL,                -- -> storeops.employees.employee_id (same key as app_users)
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE (org_id, unit_id, employee_id)
);
CREATE INDEX IF NOT EXISTS org_managers_emp ON storeops.org_managers (org_id, employee_id);

-- ── Attach stores + (optionally) reps to the tree ───────────────────────────────────────────────
ALTER TABLE storeops.stores    ADD COLUMN IF NOT EXISTS org_unit_id UUID REFERENCES storeops.org_units(id);
ALTER TABLE storeops.employees ADD COLUMN IF NOT EXISTS org_unit_id UUID REFERENCES storeops.org_units(id);
CREATE INDEX IF NOT EXISTS stores_org_unit    ON storeops.stores (org_unit_id);
CREATE INDEX IF NOT EXISTS employees_org_unit ON storeops.employees (org_unit_id);

-- ── Idempotent seed: Company -> Market -> (anchor stores) from storeops.stores ──────────────────
CREATE OR REPLACE FUNCTION storeops.seed_org_from_stores(
  p_org_id UUID DEFAULT '00000000-0000-0000-0000-000000000001'
)
RETURNS TEXT LANGUAGE plpgsql AS $$
DECLARE
  lvl_co BIGINT; lvl_mk BIGINT; root_id UUID; mkt RECORD; mkt_id UUID;
BEGIN
  -- Levels: Company (rank 0), Market (rank 1). Re-run safe via UNIQUE(org_id,rank).
  INSERT INTO storeops.org_levels(org_id, name, rank)
    VALUES (p_org_id,'Company',0), (p_org_id,'Market',1)
  ON CONFLICT (org_id, rank) DO NOTHING;
  SELECT id INTO lvl_co FROM storeops.org_levels WHERE org_id=p_org_id AND rank=0;
  SELECT id INTO lvl_mk FROM storeops.org_levels WHERE org_id=p_org_id AND rank=1;

  -- Root company node (matched by code='__ROOT__').
  INSERT INTO storeops.org_units(org_id, parent_id, level_id, name, code)
    VALUES (p_org_id, NULL, lvl_co, 'Company', '__ROOT__')
  ON CONFLICT (org_id, code) WHERE code IS NOT NULL DO NOTHING;
  SELECT id INTO root_id FROM storeops.org_units WHERE org_id=p_org_id AND code='__ROOT__';

  -- One Market node per distinct market label; blank/NULL -> 'Unassigned' (never orphan a store).
  FOR mkt IN
    SELECT COALESCE(NULLIF(TRIM(market),''),'Unassigned') AS m
    FROM storeops.stores
    WHERE COALESCE(org_id, p_org_id) = p_org_id
    GROUP BY 1
  LOOP
    INSERT INTO storeops.org_units(org_id, parent_id, level_id, name, code)
      VALUES (p_org_id, root_id, lvl_mk, mkt.m, 'mkt:'||lower(mkt.m))
    ON CONFLICT (org_id, code) WHERE code IS NOT NULL DO NOTHING;
    SELECT id INTO mkt_id FROM storeops.org_units WHERE org_id=p_org_id AND code='mkt:'||lower(mkt.m);

    -- Anchor stores to their market node ONLY when unplaced (manual moves survive a re-seed).
    UPDATE storeops.stores s
       SET org_unit_id = mkt_id
     WHERE COALESCE(s.org_id, p_org_id) = p_org_id
       AND COALESCE(NULLIF(TRIM(s.market),''),'Unassigned') = mkt.m
       AND s.org_unit_id IS NULL;
  END LOOP;

  RETURN 'seeded; markets='||(SELECT count(*) FROM storeops.org_units WHERE org_id=p_org_id AND level_id=lvl_mk)
         ||'; unplaced_stores='||(SELECT count(*) FROM storeops.stores
                                   WHERE COALESCE(org_id,p_org_id)=p_org_id AND org_unit_id IS NULL);
END $$;

-- ── Span: store_codes a manager may see (their assigned subtrees) ────────────────────────────────
CREATE OR REPLACE FUNCTION storeops.org_span_for_manager(p_org_id TEXT, p_employee_id TEXT)
RETURNS TABLE(store_code TEXT) LANGUAGE sql STABLE AS $$
  WITH RECURSIVE roots AS (
    SELECT u.id
    FROM storeops.org_units u
    JOIN storeops.org_managers m ON m.unit_id = u.id AND m.org_id::text = p_org_id
    WHERE m.employee_id = p_employee_id
  ),
  subtree AS (
    SELECT id FROM roots
    UNION
    SELECT c.id FROM storeops.org_units c
    JOIN subtree s ON c.parent_id = s.id
    WHERE c.org_id::text = p_org_id
  )
  SELECT DISTINCT st.store_code
  FROM storeops.stores st
  WHERE (st.org_id::text = p_org_id OR st.org_id IS NULL)
    AND st.org_unit_id IN (SELECT id FROM subtree)
    AND st.store_code IS NOT NULL
  UNION
  -- reps/stores pinned directly via employees.org_unit_id resolve to their home_store
  SELECT DISTINCT e.home_store
  FROM storeops.employees e
  WHERE (e.org_id::text = p_org_id OR e.org_id IS NULL)
    AND e.org_unit_id IN (SELECT id FROM subtree)
    AND e.home_store IS NOT NULL AND TRIM(e.home_store) <> '';
$$;

-- ── Subtree of any unit (admin tree drill + per-unit rollup) ────────────────────────────────────
CREATE OR REPLACE FUNCTION storeops.org_subtree(p_org_id TEXT, p_unit_id UUID)
RETURNS TABLE(id UUID, parent_id UUID, level_id BIGINT, name TEXT, code TEXT, depth INT)
LANGUAGE sql STABLE AS $$
  WITH RECURSIVE t AS (
    SELECT u.id, u.parent_id, u.level_id, u.name, u.code, 0 AS depth
    FROM storeops.org_units u
    WHERE u.id = p_unit_id AND u.org_id::text = p_org_id
    UNION ALL
    SELECT c.id, c.parent_id, c.level_id, c.name, c.code, t.depth + 1
    FROM storeops.org_units c
    JOIN t ON c.parent_id = t.id
    WHERE c.org_id::text = p_org_id
  )
  SELECT id, parent_id, level_id, name, code, depth FROM t;
$$;

-- ── store_codes under a chosen unit (for the "chosen unit" rollup within a span) ─────────────────
CREATE OR REPLACE FUNCTION storeops.org_store_codes_for_unit(p_org_id TEXT, p_unit_id UUID)
RETURNS TABLE(store_code TEXT) LANGUAGE sql STABLE AS $$
  WITH RECURSIVE subtree AS (
    SELECT id FROM storeops.org_units WHERE id = p_unit_id AND org_id::text = p_org_id
    UNION
    SELECT c.id FROM storeops.org_units c
    JOIN subtree s ON c.parent_id = s.id
    WHERE c.org_id::text = p_org_id
  )
  SELECT DISTINCT st.store_code
  FROM storeops.stores st
  WHERE (st.org_id::text = p_org_id OR st.org_id IS NULL)
    AND st.org_unit_id IN (SELECT id FROM subtree)
    AND st.store_code IS NOT NULL
  UNION
  SELECT DISTINCT e.home_store
  FROM storeops.employees e
  WHERE (e.org_id::text = p_org_id OR e.org_id IS NULL)
    AND e.org_unit_id IN (SELECT id FROM subtree)
    AND e.home_store IS NOT NULL AND TRIM(e.home_store) <> '';
$$;

-- ── RLS open_all (matches the rest of storeops.*) ───────────────────────────────────────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['storeops.org_levels','storeops.org_units','storeops.org_managers'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;
GRANT USAGE, SELECT ON SEQUENCE storeops.org_levels_id_seq TO anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION storeops.seed_org_from_stores(UUID)                 TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION storeops.org_span_for_manager(TEXT, TEXT)           TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION storeops.org_subtree(TEXT, UUID)                    TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION storeops.org_store_codes_for_unit(TEXT, UUID)       TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';

-- Status (and one-time seed hint — run the SELECT below after this migration):
--   SELECT storeops.seed_org_from_stores();
--   SELECT count(*) AS unplaced FROM storeops.stores WHERE org_unit_id IS NULL;  -- expect 0
SELECT 'org hierarchy ready: '
       || (SELECT count(*) FROM storeops.org_levels) || ' levels, '
       || (SELECT count(*) FROM storeops.org_units)  || ' units' AS status;
