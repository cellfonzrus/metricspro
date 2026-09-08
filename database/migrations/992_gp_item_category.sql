-- 992_gp_item_category.sql — GP CATEGORISATION AT THE ITEM GRAIN, AND TENANT-ADDED CATEGORIES.
--
-- Owner directive, 2026-09-08, verbatim: "on gp category map i should be able to click on the line
-- items to properly assign them to the right category it is showing 2447 blank department, all of
-- them need to be categorized, new categories should be able to add".
--
-- ── WHAT WAS WRONG (measured live, org-scoped reads 2026-09-08) ────────────────────────────────
-- `commcalc.gp_category_map` (mig 069) keys an override by DEPARTMENT only, and the built-in rule
-- sends a BLANK department to 'plan'. Live counts:
--     org 854f6d7b… (Luxelink)      2,447 blank-department lines of  14,823  (16.5%)
--     org 00000000…0001 (house)    27,010 blank-department lines of 155,677  (17.4%)
-- The 2,447 lines the owner is looking at carry $31,084.02 of gross profit and resolve to just
-- TWENTY distinct product descriptions. They are NOT all plans:
--     385  Device Protection             \
--     247  Total Wireless Protect+        >  917 lines that are protection / home internet /
--     149  Total Wireless Home Internet  /   upgrade and are counted as 'plan' today
--     136  Total Wireless Device Upgrade
-- Every one of them has a blank department AND a blank category, so the department map cannot tell
-- them apart at any setting: one department label, twenty products, four different meanings. The
-- department grain is simply not expressive enough for this data, which is why the owner is asking
-- to click into the line items.
--
-- ── WHAT THIS MIGRATION ADDS (no new table — two columns on two existing ones) ──────────────────
-- 1. `commcalc.item_mapping.gp_category` — the GP bucket for ONE item, keyed by the item_key that
--    table already uses (SKU when there is one, else the description; mig 041). This is the SAME row
--    that already carries `item_type`, `sales_category` and `kpi_category`, so an item's GP meaning
--    lives beside its other classifications rather than in a fourth mapping table. NULL = no item
--    override, and the department rules decide exactly as they do today.
--
-- 2. `commcalc.item_category_config.rolls_up_to` — the money bucket a tenant-added category counts
--    into. `item_category_config` (mig 210) is ALREADY the per-org editable category registry, with
--    a free-text `dimension` and no CHECK constraint on it, so the GP dimension is added simply by
--    writing rows with dimension = 'gp'. No schema change is needed for the dimension itself.
--
--    WHY `rolls_up_to` IS NOT OPTIONAL. The GP report aggregates into exactly FOUR money buckets —
--    device (at ext_price), accessory (at the configured basis), plan (at gp), other (at gp) — plus
--    'exclude', which drops the line. A tenant-invented category matching none of them would have
--    its money counted into NOTHING: those lines would leave the GP report with no error raised and
--    no total moving anywhere a reader could see. So a category is a LABEL the tenant may create
--    freely that ALWAYS declares where its money goes. Worst case it rolls into 'other' — which is
--    exactly where an unmapped line sits today, so a new category can never lose a dollar.
--    A row that tries to re-point a BUILT-IN ('device'…'exclude') at a different bucket is ignored
--    in code: honouring it would silently restate every prior month.
--
-- NOTHING IS SEEDED HERE and nothing is reclassified. Both columns are nullable and additive, so
-- applying this migration alone leaves every existing GP number BYTE-IDENTICAL: with no item
-- override rows the item map is empty and the classifier reaches the same department branches it
-- reaches today. The five built-in categories keep their meaning; the code seeds the 'gp' dimension
-- rows on first read (the mig-210 `_item_category_values` pattern) so they become editable.
--
-- ⚠ Applying this moves NO money. Assigning an item to a different category DOES change which GP
-- bucket its dollars land in — that is the point, and it is a deliberate act by the tenant in the
-- UI, not a consequence of this migration.
--
-- Proof: backend/harness_gp_item_category.py — precedence (item > accessory > department > built-in
-- default), custom categories resolving through their bucket, a built-in that cannot be re-pointed,
-- and the 2,447-line regression rebuilt from the real product mix.
--
-- REVERT:
--   ALTER TABLE commcalc.item_mapping         DROP COLUMN IF EXISTS gp_category;
--   ALTER TABLE commcalc.item_category_config DROP COLUMN IF EXISTS rolls_up_to;
--   DELETE FROM commcalc.item_category_config WHERE dimension = 'gp';
--   -- Dropping these removes the OVERRIDES only. No sale, no ledger row and no department mapping
--   -- is touched, and GP reverts to the department-grain behaviour it has today.

-- ── 1. the item's GP bucket, beside its other classifications ──────────────────────────────────
ALTER TABLE commcalc.item_mapping
  ADD COLUMN IF NOT EXISTS gp_category TEXT;

COMMENT ON COLUMN commcalc.item_mapping.gp_category IS
  'GP bucket override for THIS item (mig 992), keyed by the row''s own item_key (SKU else '
  'description). NULL = no override; the commcalc.gp_category_map department rules decide. Takes '
  'precedence over every department rule because it is the tenant naming one product explicitly - '
  'the case a blank department cannot express. Value is a key in commcalc.item_category_config '
  'dimension ''gp'' (or one of the five built-ins).';

CREATE INDEX IF NOT EXISTS item_mapping_org_gp_category_ix
  ON commcalc.item_mapping (org_id, gp_category)
  WHERE gp_category IS NOT NULL;

-- ── 2. where a tenant-added category's money counts ────────────────────────────────────────────
ALTER TABLE commcalc.item_category_config
  ADD COLUMN IF NOT EXISTS rolls_up_to TEXT;

COMMENT ON COLUMN commcalc.item_category_config.rolls_up_to IS
  'For dimension ''gp'' only (mig 992): the money bucket this category counts into - one of '
  'device | accessory | plan | other | exclude. REQUIRED for a tenant-added GP category: the GP '
  'report has exactly those buckets, so a category pointing at none of them would drop its lines '
  'from the report silently. Defaults to ''other'' in code when unset or unrecognised, which is '
  'where an unmapped line already sits, so a new category can never lose money. Ignored for the '
  'five built-ins - re-pointing one would restate history.';
