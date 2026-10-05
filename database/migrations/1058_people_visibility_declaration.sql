-- 1058_people_visibility_declaration.sql
-- WHOSE PERSON-KEYED ROWS MAY A LOGIN READ — declared per role (owner 2026-10-05, index §14w)
--
-- Owner, verbatim: "currently employees can see the schdule of the whoel company, i saw when i used
-- rana to clok in as him, it should only show the reps wown schdule and the managers his own schdule
-- and if any employee works under him" … and, the same hour: "same for sales report, they shoudl be
-- gated out of all stores other and thier own, also the flags should only be seen by them for thier
-- own not all stores"
--
-- ONE declaration, every surface that names a person: the schedule and its recurring templates,
-- time off, shift swaps, the punch list, and the per-rep rows of a report (the flags queue; the
-- commission / coaching surfaces read it through `payout_audience`). The SALES REPORT is store-keyed,
-- not person-keyed, and is fixed in code instead — a market pin no longer widens a store-scoped
-- reporting span — so it needs nothing from this file.
--
-- WHY A CONFIG ROW AND NOT CODE (RULE TWO). In the house org BOTH `sales_rep` AND `store_manager`
-- carry `permissions.scope = 'store'`, so the schema genuinely cannot tell a rep from their manager.
-- The server's fallback therefore fails NARROW for that one ambiguous scope (a scope-'store' role
-- with nothing declared reads only its own shifts — `app/core/scope.py::people_visibility`), and
-- this migration DECLARES every live role so nothing rides that fallback.
--
--   'self' — own rows only                            (reps)
--   'span' — own rows + everyone who works under them (store / district / market managers)
--   'all'  — every person's rows                      (admin, director, executive, accountant…)
--
-- REQUIRED BEFORE THE CODE SHIPS, for managers. Without it a scope-'store' MANAGER role falls to
-- 'self', so a store manager would read only their own week AND would be treated as an individual
-- contributor on the per-rep report surfaces. Reps are correct either way.
--
-- Schema-additive only: `storeops.roles.permissions` is jsonb and already carries `scope` and
-- `scheduling_reach` beside it (this is the THIRD such knob, and the three answer three different
-- questions — see §14w). No table, column, index or money is touched. Idempotent — re-running
-- re-asserts the same declarations.
--
-- REVERT: UPDATE storeops.roles SET permissions = permissions - 'people_visibility';
--         (removing the key returns every role to the server's derived fallback, which is NARROWER
--          than the pre-1058 behaviour, not wider — the leak cannot come back by reverting.)

BEGIN;

-- 1. Managers whose scope is a MARKET or a REGION: their market grant binds, so 'span'.
UPDATE storeops.roles
   SET permissions = permissions || jsonb_build_object('people_visibility', 'span')
 WHERE lower(coalesce(permissions->>'scope', '')) IN ('market', 'region', 'regional')
   AND coalesce(permissions->>'people_visibility', '') NOT IN ('self', 'span', 'all');

-- 2. Org-wide roles: the whole schedule.
UPDATE storeops.roles
   SET permissions = permissions || jsonb_build_object('people_visibility', 'all')
 WHERE lower(coalesce(permissions->>'scope', '')) IN ('all', 'company')
   AND coalesce(permissions->>'people_visibility', '') NOT IN ('self', 'span', 'all');

-- 3. Individual contributors: their own schedule, nobody else's.
UPDATE storeops.roles
   SET permissions = permissions || jsonb_build_object('people_visibility', 'self')
 WHERE lower(coalesce(permissions->>'scope', '')) = 'self'
   AND coalesce(permissions->>'people_visibility', '') NOT IN ('self', 'span', 'all');

-- 4. THE AMBIGUOUS SCOPE. `scope = 'store'` covers both a rep and the manager of that store, so it
--    is declared BY ROLE, per tenant — the only place this distinction exists. A role named here
--    that a tenant does not have is simply not matched; a store-scoped role NOT named here keeps
--    the narrow fallback ('self') until somebody declares it on Admin → Roles & Access.
UPDATE storeops.roles
   SET permissions = permissions || jsonb_build_object('people_visibility', 'span')
 WHERE lower(coalesce(permissions->>'scope', '')) = 'store'
   AND lower(name) IN ('store_manager', 'assistant_manager', 'store')
   AND coalesce(permissions->>'people_visibility', '') NOT IN ('self', 'span', 'all');

UPDATE storeops.roles
   SET permissions = permissions || jsonb_build_object('people_visibility', 'self')
 WHERE lower(coalesce(permissions->>'scope', '')) = 'store'
   AND lower(name) IN ('sales_rep', 'sales_consultant', 'rep')
   AND coalesce(permissions->>'people_visibility', '') NOT IN ('self', 'span', 'all');

COMMIT;

-- VERIFY — read this before and after. Every row should carry a people_visibility, and no rep
-- role should read 'span' or 'all'.
--   SELECT org_id, name,
--          permissions->>'scope'               AS scope,
--          permissions->>'people_visibility' AS people_visibility
--     FROM storeops.roles
--    ORDER BY org_id, name;
