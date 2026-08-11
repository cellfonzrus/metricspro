-- 744_roles_grant_closing_module.sql
-- 2026-08-11 — OWNER-APPROVED in session (owner picked "Managers only, not store_manager"
-- for luxelink, and "match it" for the house org).
--
-- WHY: `permissions.modules.closing` was never seeded onto pre-existing roles, only onto tenants
-- provisioned after the Daily Closing module shipped (Vzone has it; luxelink and the house org do
-- not). rbac.ts::moduleGranted() returns FALSE for a missing key, and (platform)/layout.tsx:503
-- HARD-REDIRECTS an ungranted path — so the nav item is hidden AND the URL is unreachable.
-- luxelink has ZERO super-admins, so no one on that tenant could open DM Verify, Envelope Payouts
-- or Cash Pickup at all. MEASURED 2026-08-11: luxelink 232 closings filed (2026-07-06 → 2026-08-10),
-- 0 ever verified; all 164 verifications system-wide belong to the house org, whose market_manager
-- was the only role anywhere holding closing:true. Reps still filed because /portal embeds
-- ClosingSubmitForm OUTSIDE the (platform) gate. Same class as [[seeded-role-modules-forward-only]].
--
-- SCOPE: adds exactly ONE key (modules.closing = true) to 7 named roles. It grants no money
-- authority by itself — every closing endpoint keeps its own span/scope check, and the nav items
-- carry their own scopes ('all'/'market') on top of this module gate.
-- DELIBERATELY EXCLUDED: sales_rep (36 users on luxelink — they submit via /portal today and would
-- otherwise newly see the Closing dashboard), store_manager (owner's explicit choice), accountant, hr.
--
-- The `coalesce(permissions->'modules','{}') || '{"closing":true}'` form is used instead of
-- jsonb_set() on purpose: jsonb_set() silently returns the row UNCHANGED when the parent path is
-- missing, which would have looked like success on any role lacking a modules object.

BEGIN;

UPDATE storeops.roles
   SET permissions = jsonb_set(
         coalesce(permissions, '{}'::jsonb),
         '{modules}',
         coalesce(permissions->'modules', '{}'::jsonb) || '{"closing": true}'::jsonb)
 WHERE (org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
        AND name IN ('admin','company','market','market_manager','district_manager'))
    OR (org_id = '00000000-0000-0000-0000-000000000001'
        AND name IN ('admin','district_manager'));

-- ABORT unless exactly the 7 intended roles now carry the grant. A silent partial apply on a
-- permissions table is the failure mode worth failing loudly over.
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM storeops.roles
   WHERE permissions->'modules'->>'closing' = 'true'
     AND ((org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
           AND name IN ('admin','company','market','market_manager','district_manager'))
       OR (org_id = '00000000-0000-0000-0000-000000000001'
           AND name IN ('admin','district_manager')));
  IF n <> 7 THEN
    RAISE EXCEPTION 'expected 7 roles granted closing, got %', n;
  END IF;
END $$;

COMMIT;
