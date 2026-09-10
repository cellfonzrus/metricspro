-- 1002_management_overview_all_flags_tile.sql
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- OWNER DIRECTIVE 2026-09-10 (verbatim):
--   "sales leak should not be in employee dashbaord, it shoudl be in management overview under all
--    flags tile which is not here right now"
--
-- TWO HALVES, AND ONLY THE SECOND IS HERE.
--   1. Withholding `sales_leak` from the employee dashboard is CODE (core/router.py,
--      REP_HIDDEN_FLAG_TYPES) and ships with the deploy — no migration needed.
--   2. The "All Flags" tile is CONFIG, and this is it.
--
-- NO NEW PAGE, NO NEW TABLE, NO NEW ENDPOINT. /commcalc/flags already lists EVERY flag type with a
-- type filter — `sales_leak` included, written there by commcalc/sales_recon.sync_recon_flags — and
-- /compliance is the per-queue dashboard over the same data. What did not exist was a way to REACH
-- them from Management Overview. Dashboard tiles are D1 CONFIG (mig 068 commcalc.ui_label_override,
-- scope='tiles'), house rows every tenant inherits and may override in the Dashboard Designer
-- (tenant row > house row, tile_layout.resolve_tile_layout), so this is a config row, not a build.
--
-- WHY AN UPDATE AND NOT AN INSERT: mig 948 already seeded key='management-overview' for the house org
-- with `ON CONFLICT DO NOTHING`, so that row exists. This appends one tile to its `tiles` array.
--
-- IDEMPOTENT: the WHERE clause refuses to append when a tile titled "All Flags" is already present,
-- so re-running is a no-op rather than a second copy.
--
-- HOUSE ROW ONLY. A tenant that has already DESIGNED its own Management Overview holds its own row,
-- which wins wholesale — this deliberately does not reach into it. Injecting a tile into somebody's
-- hand-arranged dashboard would be editing their work without asking; they add it in the Dashboard
-- Designer, where /commcalc/flags is already offered (layoutToHubGroups resolves a designed href
-- against EVERY nav group, not just this one).
--
-- ADDITIVE: no column, table, constraint or existing tile is changed. Nothing about who may SEE a
-- flag changes either — the nav entries carry module + scopes byte-identical to their Flags &
-- Compliance originals, so this changes what is reachable from this hub and nothing about access.
--
-- REVERT:
--   UPDATE commcalc.ui_label_override
--      SET label = jsonb_set(label::jsonb, '{tiles}',
--                    (SELECT COALESCE(jsonb_agg(t), '[]'::jsonb)
--                       FROM jsonb_array_elements(label::jsonb -> 'tiles') t
--                      WHERE t ->> 'title' <> 'All Flags'))::text,
--          updated_at = now()
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND scope = 'tiles' AND key = 'management-overview';
-- ─────────────────────────────────────────────────────────────────────────────────────────────────

BEGIN;

UPDATE commcalc.ui_label_override
   SET label = jsonb_set(
         label::jsonb,
         '{tiles}',
         (label::jsonb -> 'tiles') || '[
           {"title":"All Flags","icon":"🚩",
            "desc":"Every flag across the platform - sales leaks, accessory and compliance flags, chargebacks and discrepancies - with type, store and rep filters",
            "items":[{"href":"/commcalc/flags","label":"All Flags"},
                     {"href":"/compliance","label":"Flags & Compliance"}]}
         ]'::jsonb
       )::text,
       updated_at = now()
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND scope   = 'tiles'
   AND key     = 'management-overview'
   AND NOT (label::jsonb -> 'tiles') @> '[{"title":"All Flags"}]'::jsonb;

-- Post-flight: the tile must be present exactly once, and the layout must still parse as the shape
-- the hub reads (version + tiles array). A half-applied dashboard is a blank screen for every
-- manager, so this rolls back rather than leaving one.
DO $$
DECLARE n INT; v INT;
BEGIN
  SELECT count(*), max((label::jsonb ->> 'version')::int)
    INTO n, v
    FROM commcalc.ui_label_override,
         LATERAL jsonb_array_elements(label::jsonb -> 'tiles') t
   WHERE org_id = '00000000-0000-0000-0000-000000000001'
     AND scope = 'tiles' AND key = 'management-overview'
     AND t ->> 'title' = 'All Flags';
  IF n <> 1 THEN
    RAISE EXCEPTION 'expected exactly 1 "All Flags" tile on the house management-overview layout, found %', n;
  END IF;
  IF v IS NULL THEN
    RAISE EXCEPTION 'house management-overview layout lost its version key';
  END IF;
END $$;

COMMIT;
