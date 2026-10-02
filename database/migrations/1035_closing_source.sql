-- 1035_closing_source.sql — WHERE a store's daily closing comes from: reps type it, or it is derived
-- from the sales feed (index §19.39 / §48).
--
-- OWNER (2026-10-02): "the admin should be able to check a box to input daily closing by sales reps for all stores
-- or pull b2b data from directly into daily closing in case the tenant does not want to have people submit daily
-- closing, so it is derived via the permission selected at the time of setting up the store - it could be changed
-- later at any time by the tenant admin, all other features like cash pick up etc will stay as they are a following
-- action / reports after the data gets populated."
--
-- DUPLICATE CHECK (the build gate). Searched docs/SYSTEM_DATA_FLOW_INDEX.md for an existing mechanism that answers
-- "who produces this store-day's closing" and there is none — the platform had exactly one producer-shape
-- (a rep submitting) plus the sheet-upload backfill, each hardcoded in its own caller. What IS reused, not rebuilt:
--   · the money + counts come from closing/router._b2b_day / _b2b_counts_by_store — the SAME unified B2B aggregate
--     the close gate and the money recon already use (index §16, "_b2b_counts_by_store + _b2b_day"); no second
--     derivation of the day's cash/card or activation counts;
--   · the derived row is a plain commcalc.daily_closing row, so cash pickup, envelope report, deposit
--     accountability, the five-stage chain, DM verify and the P&L bookings all keep working unchanged — exactly the
--     owner's "all other features like cash pick up etc will stay as they are";
--   · the org-default-plus-per-store-override SHAPE is the one commcalc.envelope_payout_config already uses
--     (mig 507), read by closing/router._envelope_config; this table follows it rather than inventing a new shape;
--   · the per-store setting is edited on the EXISTING Store Setup page (/storeops/setup/stores) — no new screen.
-- No new table was created for the derived data itself, and no column was added to storeops.stores: a store's
-- closing source is CONFIG (RULE TWO — per-org config rows with a house default), and keeping it out of the store
-- master means the org default needs no fan-out write across every store row.
--
-- Additive, idempotent, MOVES NO MONEY. It creates one config table and two reporting columns on
-- commcalc.daily_closing; it writes no daily_closing row and changes no existing value. The house default is
-- 'rep_entry', so every tenant that never opens this screen is byte-identical to today.
--
-- REVERT:
--   DROP TABLE IF EXISTS commcalc.closing_source_config;
--   ALTER TABLE commcalc.daily_closing DROP COLUMN IF EXISTS derived_other, DROP COLUMN IF EXISTS derived_at;
--   NOTIFY pgrst, 'reload schema';

-- ── The one home for the fact ────────────────────────────────────────────────────────────────────
-- store_code IS NULL  = the org default (what a store with no override does)
-- store_code = 'B-123' = that store's override
CREATE TABLE IF NOT EXISTS commcalc.closing_source_config (
  id          BIGSERIAL PRIMARY KEY,
  org_id      UUID NOT NULL,
  store_code  TEXT,
  source      TEXT NOT NULL DEFAULT 'rep_entry',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_by  TEXT
);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'closing_source_config_source_chk') THEN
    ALTER TABLE commcalc.closing_source_config ADD CONSTRAINT closing_source_config_source_chk
      CHECK (source IN ('rep_entry', 'b2b_derived'));
  END IF;
END $$;

-- ONE row per (org, store) and ONE org-default row per org. Two partial unique indexes rather than a
-- UNIQUE on a COALESCE expression, so the override key stays case-insensitive the way every other
-- store_code key in this schema is read.
CREATE UNIQUE INDEX IF NOT EXISTS closing_source_config_org_default
  ON commcalc.closing_source_config (org_id) WHERE store_code IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS closing_source_config_org_store
  ON commcalc.closing_source_config (org_id, upper(store_code)) WHERE store_code IS NOT NULL;

-- ── The two reporting columns on the closing row ─────────────────────────────────────────────────
-- A derived row's money comes from the feed, which carries no gift / store-account / Zelle split: any
-- sales line whose tender the feed does not classify lands in its 'other' bucket. That amount is
-- RECORDED here rather than folded into a tender it is not — the owner's standing rule that a data gap
-- is reported, never hidden. `derived_at` is when the sweep last wrote the row (NULL on every
-- rep-submitted and sheet-uploaded row, so the three producers stay distinguishable).
ALTER TABLE commcalc.daily_closing ADD COLUMN IF NOT EXISTS derived_other NUMERIC;
ALTER TABLE commcalc.daily_closing ADD COLUMN IF NOT EXISTS derived_at TIMESTAMPTZ;

-- Find a day's derived rows without scanning the table.
CREATE INDEX IF NOT EXISTS daily_closing_derived_at ON commcalc.daily_closing (org_id, close_date)
  WHERE derived_at IS NOT NULL;

-- ── House default (org 00000000-0000-0000-0000-000000000001) ─────────────────────────────────────
-- Seeded EXPLICITLY at the current behaviour so the house row documents the default rather than
-- leaving it implicit in code. Idempotent: ON CONFLICT DO NOTHING never overwrites an edit.
INSERT INTO commcalc.closing_source_config (org_id, store_code, source, updated_by)
VALUES ('00000000-0000-0000-0000-000000000001', NULL, 'rep_entry', 'migration 1035')
ON CONFLICT DO NOTHING;

NOTIFY pgrst, 'reload schema';

-- VERIFY (expect source='rep_entry', one house row, and zero stores switched):
--   SELECT org_id, store_code, source FROM commcalc.closing_source_config ORDER BY org_id, store_code NULLS FIRST;
--   SELECT count(*) AS derived_rows FROM commcalc.daily_closing WHERE derived_at IS NOT NULL;  -- expect 0
