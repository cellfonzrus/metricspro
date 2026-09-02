-- 935_dm_verification_audit.sql — DM-verification revision history (owner directive 2026-09-02).
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "under Dm verification when the dm changes the data in the field after
-- verifying the management is not able to see the modified data when exported after selecting the
-- date range, the user should be able to see the original data entered by the store, the picture
-- of the envelope and the modified data by the DM."
--
-- ROOT CAUSE (2026-09-02 investigation, backend/app/modules/closing/verification_audit.py):
-- the store-entered ORIGINALS are safe (they live untouched on the per-rep commcalc.daily_closing
-- rows; the DM's corrections go to the SEPARATE dm_* columns of daily_closing_verification) — but
--   (a) the date-range exports never surfaced the dm_* modified values or the envelope photo, and
--   (b) daily_closing_verification is an UPSERT: a second DM save OVERWRITES the previous dm_*
--       values in place with no record of what changed, when, or by whom.
-- (a) is fixed in code (GET /closing/submissions + GET /closing/summary now return original AND
-- modified side by side, plus an envelope-view link). (b) is THIS table: an append-only revision
-- log, one row per POST /closing/verify save that changed anything, carrying the new values, the
-- prior values, the changed-field list, and the `edited_after_verify` flag (= the owner's exact
-- scenario: a money figure changed on an ALREADY-verified store-day).
--
-- NOT money-moving: pure audit history — no report sums these rows; the authoritative figures
-- stay on daily_closing (originals) and daily_closing_verification (current DM corrections).
-- Additive + idempotent. Run in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS commcalc.daily_closing_verification_audit (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  close_date          DATE NOT NULL,
  store_code          TEXT NOT NULL,
  store_name          TEXT,
  -- the values THIS save wrote
  verified            BOOLEAN,
  verified_by         TEXT,
  note                TEXT,
  dm_store_cash       NUMERIC,
  dm_store_cc         NUMERIC,
  dm_epay_cash        NUMERIC,
  dm_epay_cc          NUMERIC,
  dm_acc_sale         NUMERIC,
  dm_other            NUMERIC,
  -- the values it REPLACED (all NULL on the first revision for a store-day)
  prior_verified      BOOLEAN,
  prior_verified_by   TEXT,
  prior_note          TEXT,
  prior_dm_store_cash NUMERIC,
  prior_dm_store_cc   NUMERIC,
  prior_dm_epay_cash  NUMERIC,
  prior_dm_epay_cc    NUMERIC,
  prior_dm_acc_sale   NUMERIC,
  prior_dm_other      NUMERIC,
  changed_fields      TEXT[],                  -- which fields this save actually changed
  edited_after_verify BOOLEAN NOT NULL DEFAULT false,  -- a money figure changed on an ALREADY-verified day
  first_revision      BOOLEAN NOT NULL DEFAULT false,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dcv_audit_store_day
  ON commcalc.daily_closing_verification_audit (org_id, store_code, close_date);
CREATE INDEX IF NOT EXISTS dcv_audit_created
  ON commcalc.daily_closing_verification_audit (org_id, created_at);

-- RLS open_all to match the sibling closing tables (029/504 precedent; backend uses service key).
DO $$
BEGIN
  EXECUTE 'ALTER TABLE commcalc.daily_closing_verification_audit ENABLE ROW LEVEL SECURITY';
  BEGIN
    EXECUTE 'CREATE POLICY open_all ON commcalc.daily_closing_verification_audit FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)';
  EXCEPTION WHEN OTHERS THEN NULL; END;
  EXECUTE 'GRANT ALL ON commcalc.daily_closing_verification_audit TO anon, authenticated, service_role';
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 935 complete — commcalc.daily_closing_verification_audit (append-only DM revision history)' AS status;

-- REVERT:
--   DROP TABLE IF EXISTS commcalc.daily_closing_verification_audit;
--   (POST /closing/verify's audit insert is try/except-guarded — dropping the table only stops
--    NEW history from being recorded; nothing else reads it for money.)
