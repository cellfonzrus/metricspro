-- 1080_closing_grain_config.sql — how many daily closings ONE STORE-DAY takes (index §29.13).
--
-- OWNER BUG REPORT (2026-10-13), verbatim: "2 reps did the closing for Oct 10 for nostrand ave, for
-- luxelink, by rana Bilal and Moza Ali, one the system should not have taken duplicate entries - it
-- should have flagged the second person that they cannot enter".
--
-- Numbered, idempotent, ADDITIVE. It creates ONE config table and writes NOTHING else: no column is
-- altered, no row of commcalc.daily_closing is touched, and no money moves. Safe to run twice.
--
-- WHY NO dedup_key RECOMPUTE, stated so nobody adds one later. The behaviour change lives entirely
-- in code: backend/app/modules/closing/closing_grain.py declares the grain, and
-- closing/dedup_key.for_row composes the key at that grain, so the EXISTING partial unique index
-- daily_closing_one_active_per_rep_day (mig 502, widened by mig 1037) enforces one closing per
-- store-day for every NEW submit with no new index and no new column. Rewriting the STORED keys to
-- the store-day spelling would collide on the 16 historical store-days that legitimately hold two
-- different reps' rows (measured 2026-10-13: B-2612 07-02, B-11634 07-04, B-418 07-08, B-196 07-28,
-- B-3565 07-29, B-196 08-05, B-103 08-14, Lefferts 08-19, B-3565 08-24, kedzie 09-09, B-3565 09-16,
-- B-652 09-19, Lefferts 09-22, B-196 09-25, Cicero 10-04, Nostrand 10-10) — and mig 1037's own §3
-- guard would correctly abort. History keeps the key it was written with; new submits get the new one.
--
-- REVERT: drop table if exists commcalc.closing_grain_config;
--         (The code's house default is 'store_day', so dropping the table does NOT restore the old
--          per-rep behaviour — reverting the behaviour means reverting the code, or setting every
--          org's row to 'rep_day'. Stated here rather than left to be discovered.)

CREATE TABLE IF NOT EXISTS commcalc.closing_grain_config (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  store_code  TEXT,
  grain       TEXT NOT NULL DEFAULT 'store_day',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by  TEXT
);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'closing_grain_config_grain_chk') THEN
    ALTER TABLE commcalc.closing_grain_config ADD CONSTRAINT closing_grain_config_grain_chk
      CHECK (grain IN ('store_day', 'rep_day'));
  END IF;
END $$;

-- ONE row per (org, store) and ONE org-default row per org. Two partial unique indexes rather than a
-- UNIQUE on a COALESCE expression, so the override key stays case-insensitive the way every other
-- store_code key in this schema is read (the same shape as closing_source_config, mig 1035).
CREATE UNIQUE INDEX IF NOT EXISTS closing_grain_config_org_default
  ON commcalc.closing_grain_config (org_id) WHERE store_code IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS closing_grain_config_org_store
  ON commcalc.closing_grain_config (org_id, upper(store_code)) WHERE store_code IS NOT NULL;

COMMENT ON TABLE commcalc.closing_grain_config IS
  'How many daily closings one store-day takes: store_day (the house default — whoever closes first '
  'owns the day, a second person is refused) or rep_day (each rep declares their own envelope). '
  'Read ONLY through backend/app/modules/closing/closing_grain.resolve; a per-store row overrides '
  'the org default row (store_code IS NULL), which overrides the code house default. A tenant with '
  'no row here, and a tenant on which this migration has not run, resolve to store_day.';

-- ── House default (org 00000000-0000-0000-0000-000000000001) ─────────────────────────────────────
-- Seeded EXPLICITLY at the new behaviour so the house row documents the decision rather than leaving
-- it implicit in code. Idempotent: ON CONFLICT DO NOTHING never overwrites an admin's edit.
INSERT INTO commcalc.closing_grain_config (org_id, store_code, grain, updated_by)
VALUES ('00000000-0000-0000-0000-000000000001', NULL, 'store_day', 'migration 1080')
ON CONFLICT DO NOTHING;

NOTIFY pgrst, 'reload schema';

-- VERIFY (expect one house row at store_day, and zero store overrides):
--   SELECT org_id, store_code, grain FROM commcalc.closing_grain_config
--    ORDER BY org_id, store_code NULLS FIRST;
-- The store-days that already hold more than one closing are NOT changed by this migration. To see
-- them (expect 54 house-wide on 2026-10-13, of which 16 carry two different reps):
--   SELECT org_id, store_code, close_date, count(*) AS closings,
--          count(DISTINCT lower(btrim(coalesce(employee_name,'')))) AS reps
--     FROM commcalc.daily_closing
--    GROUP BY 1,2,3 HAVING count(*) > 1
--    ORDER BY close_date DESC;
