-- 1085_prospect_feed_tally.sql
-- ────────────────────────────────────────────────────────────
-- COUNT THE ROWS WHERE THE ROWS ARE (owner report 2026-10-16, index §COMMCALC-TALLY)
--
-- Owner, verbatim: "Store Takeover Evaluation takes along time to load , and existing evaluation is
-- not in the pull up list"
--
-- ONE CAUSE, BOTH COMPLAINTS. `GET /commcalc/prospects` answers "which stores am I evaluating" —
-- two rows, one indexed query, milliseconds. It then decorated each row with "and how many rows has
-- each feed got in each month", and computed that by SHIPPING EVERY FEED ROW TO PYTHON AND COUNTING
-- THEM. Measured live 2026-10-16 on the acting org: commcalc.prospect_feed_row holds 202,865 rows,
-- which is 203 round trips at the platform's 1,000-row page and about 59 seconds, growing with every
-- upload. So the list the owner was looking for was EMPTY for a minute while a count he had not
-- asked for was assembled — and with `gunicorn --timeout 120` and two workers behind it, that read
-- also occupied half the platform's capacity and was on a path to being killed outright.
--
-- The code fix is that the list no longer waits on the tally. THIS migration is the other half: it
-- lets the tally itself be one query instead of 203, by doing the GROUP BY in the database, which is
-- where counting rows belongs. 202,865 rows in, a few dozen counted slices out.
--
-- NOT REQUIRED, AND THE APPLICATION SAYS WHICH IT USED. The endpoint prefers this function and falls
-- back to the row-by-row count when it is absent, reporting `source` either way, so the page is
-- correct before this is applied and merely slow. Nothing is hidden by the fallback.
--
-- READ-ONLY AND MOVES NO MONEY. One function, no table, no column, no trigger, no data change. It
-- cannot alter a figure on any report: it returns counts of rows that already exist.
--
-- ORG-SCOPED BY ARGUMENT, not by the caller's good intentions: the org is a parameter and the WHERE
-- clause is inside the function, so there is no shape of this call that returns another tenant's
-- counts. SECURITY INVOKER (the default) deliberately — the caller's own rights still apply.
--
-- REVERT: DROP FUNCTION IF EXISTS commcalc.prospect_feed_tally(uuid);

CREATE OR REPLACE FUNCTION commcalc.prospect_feed_tally(p_org_id uuid)
RETURNS TABLE (prospect_id uuid, feed_key text, period text, n bigint)
LANGUAGE sql
STABLE
AS $$
    SELECT r.prospect_id,
           r.feed_key::text,
           COALESCE(r.period, '')::text AS period,
           COUNT(*)::bigint             AS n
      FROM commcalc.prospect_feed_row r
     WHERE r.org_id = p_org_id
     GROUP BY r.prospect_id, r.feed_key, COALESCE(r.period, '')
$$;

COMMENT ON FUNCTION commcalc.prospect_feed_tally(uuid) IS
  'Per (prospect, feed, month) row counts for one org. The ONE home for the takeover list''s tally; '
  'read through commcalc/prospect_feed_tally.py so the grouped and row-by-row readings share one '
  'shape. Counting in the database replaced a 203-round-trip read of 202,865 rows (owner 2026-10-16).';

-- A month with no rows is absent rather than zero, which is the same promise the Python shape makes:
-- unknown is not zero, and empty is a fact only when it was actually counted.
