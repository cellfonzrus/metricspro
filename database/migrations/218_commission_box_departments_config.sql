-- 218_commission_box_departments_config.sql — PER-ORG "box" (device unit) departments (RULE TWO).
--
-- WHY: the box classifier was the HARD-CODED module constant _BOX_DEPTS =
--   {'Android - XP', 'IPHONE - XP', 'TABLET - XP'}  (router.py). Those are Boost's b2bsoft XP department
-- labels. Every "box" number flows through this ONE constant via _sales_cell_agg — the Sales Report box
-- count, Daily-Targets conversion, and the Productivity / Stack-Ranking / Performance-Review boxes. For a
-- tenant whose POS labels device departments differently (Luxelink/Total b2bsoft, etc.) the constant
-- matches nothing → boxes read 0 / undercount on ALL those surfaces (the "productivity & reviews showing
-- WRONG box numbers" report). This adds a per-org, admin-editable list so the box departments are mapped
-- WITHOUT a code change, consistent across every surface that shares _sales_cell_agg.
--
-- BOOST-SAFE / GRACEFUL DEGRADE: the resolver (_accessory_config) reads this column in its OWN defensive
-- query and falls back to the code default _BOX_DEPTS when the column/row is empty — so a missing column
-- can never break box counting, and the house/Boost numbers stay BYTE-IDENTICAL until a tenant configures
-- their own. Additive + idempotent + re-runnable. Reuses the existing commcalc.accessory_config table
-- (mig 208) rather than inventing a parallel one.

ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS box_departments TEXT[] NOT NULL DEFAULT '{}';

-- Seed every existing org that has NOT hand-edited box departments (empty array) with the Boost default set
-- so box counting is unchanged out of the box. A hand-edited non-empty list is never clobbered.
UPDATE commcalc.accessory_config
   SET box_departments = ARRAY['Android - XP', 'IPHONE - XP', 'TABLET - XP']
 WHERE box_departments IS NULL OR box_departments = '{}';

-- Ensure the house/Boost org row carries the default (mig 208 backfilled its accessory config).
INSERT INTO commcalc.accessory_config (org_id, box_departments)
VALUES ('00000000-0000-0000-0000-000000000001', ARRAY['Android - XP', 'IPHONE - XP', 'TABLET - XP'])
ON CONFLICT (org_id) DO UPDATE
   SET box_departments = CASE
         WHEN commcalc.accessory_config.box_departments IS NULL
           OR commcalc.accessory_config.box_departments = '{}'
         THEN EXCLUDED.box_departments
         ELSE commcalc.accessory_config.box_departments END;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 218 complete — commcalc.accessory_config.box_departments installed (per-org; seeded Boost XP depts)' AS status;
