-- 936_envelope_count.sql — Envelope report: management count / over-short / comment / chargeback
-- link (owner directive 2026-09-02, item 2).
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "a new report when all the envelopes can be filtered by using the standard
-- filters... user can put their comments after counting the actual cash marking it short or over
-- and if it is short then checkmark for assigning it to the sales rep as a chargeback if the cash
-- is coming back as short - all comments chargebacks or any discrepancy over or short must be
-- filterable with the date range with all our filters."
--
-- One row per counted envelope (= one commcalc.daily_closing row: one rep / store / day — the
-- grain the envelope photo + declared cash already live at). The chargeback itself is NOT stored
-- here: an assigned shortage inserts a PARENT row into the EXISTING commcalc.ops_chargeback
-- (mig 504, reason 'envelope_short', applied_to 'commission', amount = the actual shortage) and
-- this table only carries the link (chargeback_id) — decide/settlement stay on the existing
-- machinery (ops_chargebacks.decide_chargeback; the commission module's cascade settlement).
--
-- NOT directly money-moving: rows here are management data entry; a chargeback lands PENDING and
-- only moves pay after the existing management-gated post decision. No seeds.
-- Additive + idempotent. Run in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS commcalc.envelope_count (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  closing_row_id  UUID NOT NULL,             -- commcalc.daily_closing.id (the envelope)
  store_code      TEXT,
  close_date      DATE,
  employee_name   TEXT,
  expected_amount NUMERIC,                   -- declared cash snapshot at count time
  counted_amount  NUMERIC,                   -- what management actually counted
  variance        NUMERIC,                   -- counted - expected (negative = short)
  status          TEXT CHECK (status IN ('short', 'over', 'match')),
  comment         TEXT,
  counted_by      TEXT,
  counted_at      TIMESTAMPTZ,
  chargeback_id   UUID,                      -- commcalc.ops_chargeback.id (reason envelope_short)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, closing_row_id)            -- one count per envelope; re-counts update in place
);
CREATE INDEX IF NOT EXISTS envelope_count_day ON commcalc.envelope_count (org_id, close_date);
CREATE INDEX IF NOT EXISTS envelope_count_store ON commcalc.envelope_count (org_id, store_code, close_date);

-- RLS open_all to match the sibling closing tables (029/504 precedent; backend uses service key).
DO $$
BEGIN
  EXECUTE 'ALTER TABLE commcalc.envelope_count ENABLE ROW LEVEL SECURITY';
  BEGIN
    EXECUTE 'CREATE POLICY open_all ON commcalc.envelope_count FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)';
  EXCEPTION WHEN OTHERS THEN NULL; END;
  EXECUTE 'GRANT ALL ON commcalc.envelope_count TO anon, authenticated, service_role';
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 936 complete — commcalc.envelope_count (envelope report counts / over-short / chargeback links)' AS status;

-- REVERT:
--   DROP TABLE IF EXISTS commcalc.envelope_count;
--   (The envelope-report endpoints are try/except-guarded and degrade to "no counts recorded".
--    Any already-created ops_chargeback rows with reason='envelope_short' survive independently —
--    they are decided/settled by the existing mig-504 machinery.)
