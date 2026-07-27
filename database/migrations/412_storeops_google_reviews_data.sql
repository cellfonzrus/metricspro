-- 412_storeops_google_reviews_data.sql — Google Reviews module (Phase 1), data layer.
-- mod-people, band 400-499. Companion to migration 411 (config). Additive + idempotent.
--
-- storeops.google_review_snapshot — history of a store's Google rating/review-count over time (one
-- row per sweep, insert-only), so a trend can be shown later. Never updated in place.
CREATE TABLE IF NOT EXISTS storeops.google_review_snapshot (
  id            BIGSERIAL PRIMARY KEY,
  org_id        UUID NOT NULL,
  store_code    TEXT NOT NULL,
  place_id      TEXT,
  rating        NUMERIC,
  review_count  INT,
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_google_review_snapshot_org_store_time
  ON storeops.google_review_snapshot (org_id, store_code, fetched_at DESC);

-- storeops.google_review_item — individual reviews pulled from Google Places (New) Place Details.
-- HONEST LIMITATION (surfaced in the UI too): the Places API returns only Google's own curated
-- subset of "most relevant" reviews (typically ~5), never the full list — see the people handoff's
-- Phase-2 note (Google Business Profile API / OAuth) for the upgrade path.
-- `review_hash` is Google's own stable review resource id ("places/…/reviews/…") when the API
-- returns one, else a content hash of (author + text + publish_time) — either way it is the dedupe
-- key so a re-sweep never creates a duplicate row for a review already seen.
CREATE TABLE IF NOT EXISTS storeops.google_review_item (
  id                      BIGSERIAL PRIMARY KEY,
  org_id                  UUID NOT NULL,
  store_code              TEXT NOT NULL,
  review_hash             TEXT NOT NULL,
  author_name             TEXT,
  rating                  INT,
  review_text             TEXT,
  review_time             TIMESTAMPTZ,     -- Google's publishTime, when the API returns one
  relative_time           TEXT,            -- Google's own "3 weeks ago" label (fallback display)
  matched_employee_id     TEXT,            -- NULLABLE — see the name-matching note below
  matched_employee_name   TEXT,
  match_confidence        TEXT,            -- always 'possible' when set — see the guard note
  match_note              TEXT,            -- e.g. an ambiguous-match explanation (never a hard claim)
  notified_at             TIMESTAMPTZ,     -- when the matched-employee/store notification fired
  fetched_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- NOTE on matched_employee_id/match_confidence: name matching is a CONSERVATIVE, case-insensitive
-- word-boundary match of an employee's first name (or "First L" form) against the review text,
-- scoped to employees scheduled/home at that store — it is a hint, never a claim of certainty, hence
-- match_confidence is always 'possible' and the UI must label it "possible mention." False positives
-- are expected (common first names that are also ordinary English words); ambiguous matches
-- (2+ candidates) are left unmatched (NULL) with the ambiguity recorded in match_note instead of
-- guessing. See backend/app/modules/storeops/google_reviews.py `match_employees_in_text`.
CREATE UNIQUE INDEX IF NOT EXISTS uq_google_review_item_org_store_hash
  ON storeops.google_review_item (org_id, store_code, review_hash);
CREATE INDEX IF NOT EXISTS ix_google_review_item_org_store
  ON storeops.google_review_item (org_id, store_code, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS ix_google_review_item_matched_emp
  ON storeops.google_review_item (org_id, matched_employee_id);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['storeops.google_review_snapshot', 'storeops.google_review_item'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS open_all ON %s', t);
    EXECUTE format('CREATE POLICY open_all ON %s FOR ALL TO anon, authenticated USING (true) WITH CHECK (true)', t);
    EXECUTE format('GRANT ALL ON %s TO anon, authenticated, service_role', t);
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 412 complete — storeops.google_review_snapshot + google_review_item' AS status;
