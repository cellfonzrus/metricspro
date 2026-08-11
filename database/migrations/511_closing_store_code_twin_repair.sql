-- 511_closing_store_code_twin_repair.sql — luxelink store-code TWIN repair.
-- Companion to the code fix in the same commit (closing_stores collapses twins to one option).
--
-- ⚠️ MONEY-ATTRIBUTION (not money-changing). OWNER-APPROVED 2026-08-11 ("Both steps").
-- Consolidated cash is UNCHANGED to the cent — this moves $9,413.16 of already-declared cash off 13
-- phantom store identities and onto the real ones. No amount column is touched anywhere.
--
-- ── WHAT HAPPENED ──────────────────────────────────────────────────────────────────────────────────
-- 2026-08-05 02:50:00.089037Z a single bulk insert (all 19 rows share the timestamp to the
-- microsecond) added structured 'LUX-*' codes to commcalc.store_mapping for stores that ALREADY had
-- short codes — 39 rows across 20 distinct addresses. GET /closing/stores keyed purely on store_code,
-- so the Daily Closing picker offered every store TWICE, and between 2026-08-05 and 2026-08-10 reps
-- filed 29 closings against the twin nobody else reads.
--
-- MEASURED before this migration (prod, read-only):
--   commcalc.daily_closing     29 rows · 13 stores · $9,413.16 declared cash
--   commcalc.closing_attempt   29 rows (the submit-audit trail for the same closings)
--   commcalc.cash_pickup        0 rows   <- no cash was ever picked up against a phantom
--   commcalc.bank_deposit       0 rows   <- nor deposited
-- Every other store_code-bearing table in commcalc/storeops was scanned: ZERO LUX-* rows.
--
-- 'LUX-NY-PENN' is 957 Pennsylvania Avenue. Real code '957' holds 22 closings / $8,090.00; the twin
-- held the single $120.00 sheet (Yasir, 2026-08-09) that the owner reported as "not true".
--
-- ── OWNER RULING 2026-08-11 ────────────────────────────────────────────────────────────────────────
-- "LUX-NY-PENN is actually 957 pennsylvania ave, it is a great way to assign but should be mapped so
--  they don't look like a different store."
-- => KEEP the LUX-* scheme. Nothing is deleted. It becomes the ALIAS vocabulary; the canonical code
--    stays the short one, because that is what storeops.stores (the store master) carries, what
--    store_aliases already maps addresses to, and what 203 of the 232 closings were filed under.
--
-- ── SAFETY ─────────────────────────────────────────────────────────────────────────────────────────
-- The twin<->real pairing is derived from store_mapping.store_address, never hard-coded: each LUX-*
-- row shares its address string EXACTLY with one short-code row (that is how the duplication was
-- found), so the join is 1:1 and verifiable. The asserts at the end fail the whole transaction if the
-- pairing is not 1:1 or if any LUX-* row survives.

BEGIN;

-- Pairing, derived once and reused by all three statements.
CREATE TEMP TABLE _twin_map ON COMMIT DROP AS
SELECT l.org_id,
       l.store_code   AS phantom,
       s.store_code   AS canonical,
       l.store_address
FROM commcalc.store_mapping l
JOIN commcalc.store_mapping s
  ON s.org_id = l.org_id
 AND s.store_address = l.store_address
 AND s.store_code NOT LIKE 'LUX-%'
WHERE l.org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
  AND l.store_code LIKE 'LUX-%';

-- GUARD: the join must be exactly 1:1. A phantom matching 0 or 2+ canonical rows means the address
-- assumption is wrong and NOTHING should be re-pointed on a guess.
DO $$
DECLARE bad int;
BEGIN
  SELECT count(*) INTO bad FROM (
    SELECT phantom FROM _twin_map GROUP BY phantom HAVING count(*) <> 1) x;
  IF bad > 0 THEN
    RAISE EXCEPTION 'ABORT: % phantom code(s) do not pair 1:1 with a canonical code', bad;
  END IF;
END $$;

-- (a) Register the mapping the owner asked for. Additive; the LUX-* store_mapping rows STAY.
--     ON CONFLICT guards the (org_id, lower(trim(alias))) unique index — re-running is a no-op.
INSERT INTO commcalc.store_aliases (org_id, alias, store_code, note, source, confidence)
SELECT org_id, phantom, canonical,
       'Onboarding twin: bulk-inserted 2026-08-05 alongside the existing short code for the same '
       'address. Owner ruling 2026-08-11 — keep the scheme, map it (mig 511).',
       'mig_511_twin_repair', 1.0
FROM _twin_map
ON CONFLICT (org_id, (lower(btrim(alias)))) DO NOTHING;

-- (b) Re-point the misfiled closings onto the real store. Amounts are untouched.
UPDATE commcalc.daily_closing d
   SET store_code = m.canonical
  FROM _twin_map m
 WHERE d.org_id = m.org_id AND d.store_code = m.phantom;

-- ...and the submit-audit trail for those same closings, so the two never disagree.
UPDATE commcalc.closing_attempt a
   SET store_code = m.canonical
  FROM _twin_map m
 WHERE a.org_id = m.org_id AND a.store_code = m.phantom;

-- GUARD: nothing may remain on a phantom identity.
DO $$
DECLARE left_dc int; left_ca int; aliased int;
BEGIN
  SELECT count(*) INTO left_dc FROM commcalc.daily_closing
   WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' AND store_code LIKE 'LUX-%';
  SELECT count(*) INTO left_ca FROM commcalc.closing_attempt
   WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' AND store_code LIKE 'LUX-%';
  SELECT count(*) INTO aliased FROM commcalc.store_aliases
   WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' AND alias LIKE 'LUX-%';
  IF left_dc <> 0 OR left_ca <> 0 THEN
    RAISE EXCEPTION 'ABORT: % daily_closing + % closing_attempt rows still on a phantom code',
                    left_dc, left_ca;
  END IF;
  IF aliased < 19 THEN
    RAISE EXCEPTION 'ABORT: only % of 19 twin aliases registered', aliased;
  END IF;
  RAISE NOTICE 'mig 511 OK — % aliases registered, 0 rows left on a phantom code', aliased;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 511 complete — LUX-* twins registered as aliases; 29 daily_closing + 29 '
       'closing_attempt rows re-pointed to their canonical store. No amount changed.' AS status;
