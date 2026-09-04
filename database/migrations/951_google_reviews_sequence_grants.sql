-- 951_google_reviews_sequence_grants.sql — THE google-reviews "0/20 store(s) · 20 error(s)" fix
--
-- ROOT CAUSE (owner report 2026-09-04 "still not able to pull google reviews"; failed sweeps
-- recorded 2026-08-17 LuxeLink / 2026-08-20 house, both 20/20 errors; reproduced LIVE from the
-- module's own code path on 2026-09-04):
--
--     Postgres 42501 — permission denied for sequence google_review_store_id_seq
--
-- Migrations 411/412/413 created storeops.google_review_store / google_review_snapshot /
-- google_review_item / action_plan_area with BIGSERIAL ids and granted the TABLES to service_role
-- (Gate-1 N1/N2 hardening) — but `GRANT ALL ON <table>` does NOT cover the sequence behind a
-- BIGSERIAL DEFAULT, and NO grant was ever issued for these sequences (the house convention is
-- EXPLICIT grants, never relying on blanket/default privileges — migration 232's and 414's own
-- comments on this exact lesson). So in prod EVERY sweep write died on the very first insert in
-- the flow — resolve_place_for_store's upsert into google_review_store — before a single snapshot
-- or review item could land. That is why google_review_store, google_review_snapshot AND
-- google_review_item all have ZERO rows despite valid API keys (the Google side answers fine; the
-- sweep's own text-search/details calls succeed from the module code — verified live 2026-09-04).
-- The API key was never the problem; the DB write right after the first successful Google call was.
--
-- The fix is grants only: USAGE (nextval for the DEFAULT) + SELECT (currval-style reads) to
-- service_role on the four sequences, and the mig 722 lockdown posture (revoke anon/authenticated/
-- PUBLIC) re-asserted explicitly. Idempotent — GRANT/REVOKE re-apply safely.
--
-- storeops.action_plan (mig 413) needs nothing: UUID PK, no sequence. payroll_change_log_id_seq is
-- verified working in prod (2,170 rows) — not touched.

DO $$
DECLARE s text;
BEGIN
  FOREACH s IN ARRAY ARRAY[
    'storeops.google_review_store_id_seq',
    'storeops.google_review_snapshot_id_seq',
    'storeops.google_review_item_id_seq',
    'storeops.action_plan_area_id_seq'
  ] LOOP
    BEGIN
      EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM PUBLIC', s);
      EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM anon, authenticated', s);
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO service_role', s);
    EXCEPTION WHEN undefined_table THEN
      RAISE NOTICE 'sequence % absent (migration 411/412/413 not applied here) — skipped', s;
    END;
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 951 — google-reviews sequence grants (fixes 42501 permission denied for sequence; unblocks the sweep writes)' AS status;

-- To verify:
--   set role service_role; select nextval('storeops.google_review_store_id_seq'); reset role;
-- (or just run the sweep — POST /storeops/google-reviews/sweep/run-now)
--
-- REVERT (returns to the broken pre-951 state; only for a rollback drill):
--   REVOKE USAGE, SELECT ON SEQUENCE storeops.google_review_store_id_seq    FROM service_role;
--   REVOKE USAGE, SELECT ON SEQUENCE storeops.google_review_snapshot_id_seq FROM service_role;
--   REVOKE USAGE, SELECT ON SEQUENCE storeops.google_review_item_id_seq     FROM service_role;
--   REVOKE USAGE, SELECT ON SEQUENCE storeops.action_plan_area_id_seq       FROM service_role;
