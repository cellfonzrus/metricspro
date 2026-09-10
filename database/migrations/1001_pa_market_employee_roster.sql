-- 1001_pa_market_employee_roster.sql — THE PA MARKET GETS EMPLOYEE ROWS, NAMED EXACTLY AS THE FEED
--                                      NAMES THEM.
--
-- ⚠ OWNER-RUN. This file is a HAND-OFF, not something an agent applied. Nothing in it has been
--   executed. Run it in the Supabase SQL editor when the roster below is approved.
--
-- Owner directive, 2026-09-10, verbatim: "also assign an agent to create the users from PA Market
-- using the same names as in b2b reports so we can do thier scdhuleing also , the data shoudl flow
-- in all systems as indexed, , this way the usernames will be same and no mapping needed"
--
-- ── THE FINDING THIS ANSWERS ──────────────────────────────────────────────────────────────────
-- `storeops.employees` has 45 rows for the house org and NOT ONE of them has a PA home_store. The
-- nine PA stores (B-1710 B-2701 B-2778 B-3605 B-5619 B-60TH B-6149 B-6507 B-723) have zero staff on
-- the roster, so the Schedule / Hours Approval / Attendance / Time Clock surfaces have nobody to
-- put on a shift — while the b2b POS feed has been carrying those same people's sales, and
-- `commcalc.rep_commissions` has been PAYING them, for 15 months (74 rows, Apr 2026 + Aug 2026 +
-- Jun/Jul 2024, `epay_salesperson` set and `storeops_name` BLANK because no roster row exists).
--
-- ── NO NEW MECHANISM. NO NEW MAPPING TABLE. ───────────────────────────────────────────────────
-- Duplicate check (index §14 "Rep→StoreOps name map", §2 raw_sales/daily_sales_feed):
--   * `commcalc.name_map` (mig 002:171 — epay_login / epay_salesperson / storeops_name) is the
--     platform's EXISTING b2b-name→roster-name bridge. It holds 28 rows, ALL of them LI/NYC/NJ
--     people, and ZERO PA people. It is exactly the mapping table the owner does not want to need.
--   * `storeops.employees.epay_login` + `.epay_salesperson` (mig 003:21) are the columns that make
--     a name_map row UNNECESSARY: `commission_engine` matches a feed seller on
--     `epay_salesperson || name` (commission_engine.py:554,613,1141) and its own remediation text
--     tells the operator to "set <person>'s ePay/POS name (epay_salesperson) to exactly '<the feed
--     spelling>'" (commission_engine.py:1195). `GET /commcalc/rep-employee-map` returns
--     {name, epay_salesperson, aliases} for the same reason.
-- So this migration EXTENDS the mechanism that already exists by filling those two columns with the
-- feed's own bytes. It creates no table, no endpoint, no importer and no name_map row.
--
-- ── NAME FIDELITY IS THE DELIVERABLE ──────────────────────────────────────────────────────────
-- Every `name`, `epay_salesperson` and `epay_login` value below is a VERBATIM copy of a real value
-- in `commcalc.daily_sales_feed` / `commcalc.raw_sales` for the house org — same bytes, same case,
-- same "Last, First" ordering, no trimming, no re-ordering to "First Last", no title-casing.
--   * `name` is set to the feed spelling TOO (not just epay_salesperson) because any re-ordering is
--     a derivation, and a derivation is the mapping problem coming back. "Ranganath, Ranganath",
--     "Namir, Md" and "chowdary, Thanvi" are three separate reasons a "First Last" transform cannot
--     be trusted. The owner can rename any of these in the Employee Database later — the b2b join
--     keeps working, because it rides `epay_salesperson`, which this file pins.
--   * Proof that these bytes are the feed's bytes: backend/harness_pa_roster_names.py parses the
--     roster OUT of THIS FILE and checks it against feed fixtures. It cannot pass against a roster
--     this migration does not contain.
--
-- ── WHAT IS DELIBERATELY LEFT NULL, AND WHY ───────────────────────────────────────────────────
--   pay_rate  → EXPLICIT NULL. The column is `NUMERIC DEFAULT 0` (mig 003:28), so an INSERT that
--               simply omits it lands 0.00 — "this person earns nothing", the lie this platform has
--               already been bitten by (six pay_rate=0 employees producing $0.00 across 351 August
--               hours). NULL is written on purpose so the value reads as UNSET in the database and
--               in the Employee Database editor.
--               ⚠ HONEST LIMIT: every payroll read coerces with `float(pay_rate or 0)`
--               (storeops/router.py:1208,1232,1269,1310,1507; payroll_salary.py:364), so these
--               people will still PRINT $0.00 on the payroll surfaces until a rate is entered.
--               There is no "rate not set" state distinct from zero anywhere downstream. Hours will
--               accrue correctly; PAY MUST BE SET BEFORE THE FIRST PAYROLL RUN THAT INCLUDES THEM.
--   email / phone / role / hire_date / date_of_birth / address / emergency_* → NULL. Not known.
--   org_unit_id → NULL. There is NO PA Region and NO PA District in `storeops.org_units` (the tree
--               has LI / NJ / NYC / TT only) and not one PA store carries an org_unit_id. Inventing
--               a unit here would be inventing an org structure. See "STILL OPEN" below.
--   pay_amount  → NULL. `pay_basis` cannot be NULL (mig 416: NOT NULL DEFAULT 'hourly') so it takes
--               the platform default; that is a basis label, not a dollar figure.
--   home_store  → NULL for anyone whose PA sales are spread across stores with no clear home (see
--               the per-row evidence). A home store is a real fact about a person, not a blank to
--               fill so the row looks complete.
--
-- ── employee_id ───────────────────────────────────────────────────────────────────────────────
-- Step 3 assigns 'E' || id, byte-identical to the platform's OWN generator
-- (`storeops.router._ensure_employee_id`, :2245). It is NOT an invented external payroll number —
-- it is the surrogate key the app would have assigned had these rows come through POST
-- /storeops/employees. It is REQUIRED, not cosmetic: `payroll_approval._roster` drops every row
-- with a blank employee_id (`return [r for r in rows if r.get("employee_id")]`,
-- payroll_approval.py:390), and `create_shift` canonicalizes to it
-- (`_canonical_shift_employee_id`), so a NULL employee_id means no hours approval and no shifts.
--
-- ── IDEMPOTENT ────────────────────────────────────────────────────────────────────────────────
-- Each INSERT is guarded by NOT EXISTS on (org_id, epay_salesperson) OR (org_id, name) for the same
-- verbatim string, so running this file twice creates nobody twice — and it also will not duplicate
-- a person an operator has already added by hand under either spelling. Step 3 only fills a NULL
-- employee_id. Additive: no UPDATE of an existing person's data, no DELETE, no schema change.
--
-- ⚠ MONEY: no payout, ledger, statement, accrual or commission figure is written or changed here.
-- These rows carry NO pay figure at all (see pay_rate above). What DOES change once they exist:
-- `rep_commissions.storeops_name` will start resolving for PA sellers (it is blank today) and the
-- PA stores' scheduled/actual hours will start reaching the payroll and store-expense surfaces —
-- at a $0 rate until a rate is entered.
--
-- RULE TWO: this is tenant DATA (people), not behavior. No market name, tenant name or carrier name
-- enters any code path from here; nothing branches on 'PA'. The org is named by UUID.
--
-- REVERT: DELETE FROM storeops.employees
--          WHERE org_id = '00000000-0000-0000-0000-000000000001'
--            AND epay_salesperson IN ( … the verbatim names in step 2 … )
--            AND NOT EXISTS (SELECT 1 FROM storeops.shifts s
--                             WHERE s.org_id = employees.org_id
--                               AND s.employee_id = employees.employee_id);
--         (refuse to delete anyone who has already been scheduled — deactivate instead:
--          UPDATE storeops.employees SET is_active = false WHERE …)

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- STEP 1 — PRE-FLIGHT. Read-only. Confirms the gap is still real before anything is written.
--          Expected before this migration: 0 rows.
-- ─────────────────────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE n INT;
BEGIN
  SELECT count(*) INTO n
    FROM storeops.employees e
   WHERE e.org_id = '00000000-0000-0000-0000-000000000001'
     AND upper(coalesce(e.home_store, '')) IN
         ('B-1710','B-2701','B-2778','B-3605','B-5619','B-60TH','B-6149','B-6507','B-723',
          'B-1','B-1598');
  RAISE NOTICE 'PA-home_store employees BEFORE this migration: %', n;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- STEP 2 — THE ROSTER. Ten people with PA-market b2b activity in the current window
--          (last sale on or after 2026-08-01; feed runs to 2026-09-07).
--
--  # name / epay_salesperson (VERBATIM)  login (VERBATIM)  home_store  source rows   last b2b sale
--  1 Arora, Tanish                       Tanish            B-5619      16,755 (100% B-5619)  2026-09-07
--  2 Ranganath, Ranganath                Ranganath         B-6149      13,025 (100% B-6149)  2026-09-07
--  3 Paul, Rikita                        Rikita            B-6507      11,017 (100% B-6507)  2026-09-07
--  4 Rani, Nisha                         Nisha             B-2701      10,435 (100% B-2701)  2026-09-07
--  5 Jaladhi, Akhila                     Akhila            B-60TH      10,422 (89% B-60TH)   2026-09-07
--  6 chowdary, Thanvi                    Thanvi            B-723        9,376 (100% B-723)   2026-09-07
--  7 Saul, Mark                          M.saul            B-2778       8,238 (99% B-2778)   2026-09-06
--  8 Taneeru, Mona                       Mona              B-1710       1,736 (90% B-1710)   2026-09-07
--  9 Reddy, Nithin                       Nithin            B-3605       1,631 (99% B-3605)   2026-09-07
-- 10 onteru, satish                      satish            NULL           396 across FIVE PA
--                                                                         stores, top store 31%
--                                                                         — a floater, no home
--                                                                         store in evidence  2026-08-29
--
-- Store codes are the `storeops.stores` spellings (the vocabulary `home_store` is edited and
-- displayed in). Two of them are ambiguous in the DATA, not in this file — see "STILL OPEN".
-- ─────────────────────────────────────────────────────────────────────────────────────────────
INSERT INTO storeops.employees
  (org_id, name, epay_salesperson, epay_login, home_store,
   role, pay_rate, pay_amount, email, phone, org_unit_id, is_active, notes)
SELECT v.org_id, v.name, v.name, v.epay_login, v.home_store,
       NULL, NULL, NULL, NULL, NULL, NULL, true,
       'Created from the b2b POS feed (commcalc.daily_sales_feed / raw_sales) 2026-09-10 — '
       'name/epay_salesperson/epay_login are verbatim feed values. pay_rate deliberately UNSET.'
  FROM (VALUES
    ('00000000-0000-0000-0000-000000000001'::uuid, 'Arora, Tanish',        'Tanish',    'B-5619'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 'Ranganath, Ranganath', 'Ranganath', 'B-6149'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 'Paul, Rikita',         'Rikita',    'B-6507'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 'Rani, Nisha',          'Nisha',     'B-2701'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 'Jaladhi, Akhila',      'Akhila',    'B-60TH'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 'chowdary, Thanvi',     'Thanvi',    'B-723'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 'Saul, Mark',           'M.saul',    'B-2778'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 'Taneeru, Mona',        'Mona',      'B-1710'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 'Reddy, Nithin',        'Nithin',    'B-3605'),
    ('00000000-0000-0000-0000-000000000001'::uuid, 'onteru, satish',       'satish',    NULL)
  ) AS v(org_id, name, epay_login, home_store)
 WHERE NOT EXISTS (
         SELECT 1 FROM storeops.employees e
          WHERE e.org_id = v.org_id
            AND (e.epay_salesperson = v.name OR e.name = v.name));

-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- STEP 3 — employee_id, using the platform's own generator shape ('E' || id).
--          Touches ONLY rows that have none. Required for shifts + hours approval (see header).
-- ─────────────────────────────────────────────────────────────────────────────────────────────
UPDATE storeops.employees e
   SET employee_id = 'E' || e.id
 WHERE e.org_id = '00000000-0000-0000-0000-000000000001'
   AND coalesce(btrim(e.employee_id), '') = ''
   AND e.epay_salesperson IN
       ('Arora, Tanish','Ranganath, Ranganath','Paul, Rikita','Rani, Nisha','Jaladhi, Akhila',
        'chowdary, Thanvi','Saul, Mark','Taneeru, Mona','Reddy, Nithin','onteru, satish')
   AND NOT EXISTS (SELECT 1 FROM storeops.employees x
                    WHERE x.employee_id = 'E' || e.id);

-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- STEP 4 — POST-FLIGHT. Read-only. Expected after: 10 rows, every one with an employee_id,
--          every pay_rate NULL.
-- ─────────────────────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE n INT; no_id INT; priced INT;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE coalesce(btrim(employee_id),'') = ''),
         count(*) FILTER (WHERE pay_rate IS NOT NULL)
    INTO n, no_id, priced
    FROM storeops.employees
   WHERE org_id = '00000000-0000-0000-0000-000000000001'
     AND epay_salesperson IN
         ('Arora, Tanish','Ranganath, Ranganath','Paul, Rikita','Rani, Nisha','Jaladhi, Akhila',
          'chowdary, Thanvi','Saul, Mark','Taneeru, Mona','Reddy, Nithin','onteru, satish');
  RAISE NOTICE 'PA roster rows: %, missing employee_id: %, with a pay figure: %', n, no_id, priced;
  IF n <> 10 OR no_id <> 0 THEN
    -- Rolls the whole thing back and tells a human, rather than half-landing a roster.
    -- The one non-obvious way to get here: someone already created one of these ten by hand under
    -- the same `name` but with a BLANK `epay_salesperson`. Step 2 correctly skips them (no
    -- duplicate person) but this count does not see them, so n comes back 9. That is not a failure
    -- of this migration — it means that existing row needs its POS identity pinned instead:
    --   UPDATE storeops.employees SET epay_salesperson = name, epay_login = '<the feed login>'
    --    WHERE org_id = '00000000-0000-0000-0000-000000000001' AND name = '<the feed name>'
    --      AND coalesce(epay_salesperson,'') = '';
    RAISE EXCEPTION 'PA roster did not land as expected (rows=% of 10, missing employee_id=%). '
                    'Nothing was committed — see the note above this RAISE.', n, no_id;
  END IF;
END $$;

COMMIT;


-- ═════════════════════════════════════════════════════════════════════════════════════════════
-- SECTION B — HELD FOR A DECISION. NOT PART OF THE TRANSACTION ABOVE. Uncomment only what the
--             owner confirms is still on staff.
--
-- Ten more people have PA b2b activity in 2026 but STOPPED before 2026-08-01. They are either
-- leavers or seasonal, and creating a leaver as an active schedulable employee is its own kind of
-- wrong answer. Evidence (rows / last PA sale / home-store share):
--
--   'Yogeswarao, Pavuluri'  Yogeswarao    B-3605   4,576  2026-07-31  100% B-3605
--   'Arafath, Yaseer'       Yaseer        B-1710   4,237  2026-07-06  100% B-1710
--   'Rahman, Abdur'         Abdur.Rehman  NULL     1,153  2026-07-24  six stores, top 48%
--   'Addagarla, Teja Sri'   TejaSri       B-1710   1,062  2026-07-30  100% B-1710
--   'haider, shuja'         Shuja         NULL       392  2026-07-18  three stores, top 68%
--   'Purkayastha, Deborshee' Debo         NULL       362  2026-07-19  eight stores, top 38%
--   'pal, Vinay'            Vinay         NULL       195  2026-06-11  two stores, top 62%
--   'sappidi, venkatesh'    venkatesh     NULL       107  2026-04-15  two stores, top 67%
--   'Anand, saurabh'        saurabh       NULL        76  2026-05-21  two stores, top 51%
--   'KOKKU, SAGAR'          SAGAR.K       B-3605      32  2026-04-30  100% B-3605
--
-- INSERT INTO storeops.employees
--   (org_id, name, epay_salesperson, epay_login, home_store,
--    role, pay_rate, pay_amount, email, phone, org_unit_id, is_active, notes)
-- SELECT v.org_id, v.name, v.name, v.epay_login, v.home_store,
--        NULL, NULL, NULL, NULL, NULL, NULL, true,
--        'Created from the b2b POS feed 2026-09-10 — verbatim feed name. pay_rate UNSET.'
--   FROM (VALUES
--     ('00000000-0000-0000-0000-000000000001'::uuid, 'Yogeswarao, Pavuluri',   'Yogeswarao',   'B-3605'),
--     ('00000000-0000-0000-0000-000000000001'::uuid, 'Arafath, Yaseer',        'Yaseer',       'B-1710'),
--     ('00000000-0000-0000-0000-000000000001'::uuid, 'Rahman, Abdur',          'Abdur.Rehman', NULL),
--     ('00000000-0000-0000-0000-000000000001'::uuid, 'Addagarla, Teja Sri',    'TejaSri',      'B-1710'),
--     ('00000000-0000-0000-0000-000000000001'::uuid, 'haider, shuja',          'Shuja',        NULL),
--     ('00000000-0000-0000-0000-000000000001'::uuid, 'Purkayastha, Deborshee', 'Debo',         NULL),
--     ('00000000-0000-0000-0000-000000000001'::uuid, 'pal, Vinay',             'Vinay',        NULL),
--     ('00000000-0000-0000-0000-000000000001'::uuid, 'sappidi, venkatesh',     'venkatesh',    NULL),
--     ('00000000-0000-0000-0000-000000000001'::uuid, 'Anand, saurabh',         'saurabh',      NULL),
--     ('00000000-0000-0000-0000-000000000001'::uuid, 'KOKKU, SAGAR',           'SAGAR.K',      'B-3605')
--   ) AS v(org_id, name, epay_login, home_store)
--  WHERE NOT EXISTS (
--          SELECT 1 FROM storeops.employees e
--           WHERE e.org_id = v.org_id
--             AND (e.epay_salesperson = v.name OR e.name = v.name));
-- -- then re-run STEP 3 with these ten names in the IN-list.
--
-- ═════════════════════════════════════════════════════════════════════════════════════════════
-- NOT CREATED — and why. Each of these is a reported finding, not an oversight.
--
-- (a) NON-PERSON. 'Admin, backoffice' / login 'PAADMIN' (6 rows, 2024-07-12 … 2026-04-23). A back
--     office / system account, not a schedulable human. The platform's own shared sales pass
--     already skips a rep of 'admin' (index §3, gp_report.countable_sale_skip_reason).
--
-- (b) ALREADY ON THE ROSTER. 'Escobar, Angelica' (login 'Angelica', 4 rows, ALL on 2026-08-27, all
--     at B-1710) matches existing employee E232 "Angelica Escobar", legal_name "Angelica escobar
--     canales", home_store B-1800 — an LI store. Creating a second row would manufacture the
--     duplicate person this whole exercise exists to avoid. If E232 IS the same human covering a PA
--     store for a day, the correct one-row fix is to pin her POS identity (owner's call, not run):
--        UPDATE storeops.employees
--           SET epay_salesperson = 'Escobar, Angelica', epay_login = 'Angelica'
--         WHERE org_id = '00000000-0000-0000-0000-000000000001' AND employee_id = 'E232'
--           AND coalesce(epay_salesperson,'') = '';
--     If she is a DIFFERENT person, she needs her own row — say so and it will be added.
--
-- (c) TWO POS LOGINS THAT MAY BE ONE HUMAN EACH. Not created either way, because guessing merges a
--     person or splits one:
--       'Rahman, Abdur' (login 'Abdur.Rehman', 1,153 rows, Apr–Jul 2026)
--         vs 'Rehman, Abdur' (login '.abdur', 2 rows, one day 2026-06-17, B-6149)
--       'Addagarla, Teja Sri' (login 'TejaSri', 1,062 rows, Jun–Jul 2026)
--         vs 'Sri Addagarla, Teja' (login 'Teja', 4 rows, one day 2026-06-16, B-1710)
--     Each pair is a DISTINCT b2b login, so the feed itself treats them as two accounts. If the
--     owner confirms one human, the second spelling belongs in `commcalc.rep_aliases` (the existing
--     alias mechanism, endpoints /rep-aliases) — NOT as a second employee.
--
-- (d) 2024 LEAVERS. Seven names whose last PA sale is 2024-07-16 and who have not appeared in 26
--     months: 'Tiwari, Sumit', 'rogtao, Claron', 'Nath, Dipanjan', 'Reddy, Manoj', 'Rahman, Shaf',
--     'Chatterjee, Apurba', 'Namir, Md'. Historical sellers, not staff to schedule.
--
-- ═════════════════════════════════════════════════════════════════════════════════════════════
-- STILL OPEN — data-quality findings this migration deliberately does NOT paper over.
--
-- (1) NO PA BRANCH IN THE ORG TREE. `storeops.org_units` has LI / NJ / NYC / TT Regions and
--     Districts; there is no PA Region or PA District, and all nine PA stores carry
--     org_unit_id = NULL. Consequence (index §13c, the same shape as the B-1115/LI finding): a
--     manager spanned by ORG-UNIT SUBTREE reaches no PA store, while a `market = 'PA'` grant on
--     `app_users.market` DOES bind them (grants resolve through the canonical union index). Today
--     exactly one house login carries PA in its market grant. Creating a PA District is an org
--     structure decision, so it is reported, not guessed.
--
-- (2) TWO STORES CARRY TWO CODES EACH, one per vocabulary:
--       1 S 60th St  — storeops.stores 'B-60TH' ("1 S 60th St, Philadelphia")
--                      vs commcalc.store_mapping 'B-1' ("1 S 60th street", salesforce_id
--                      0018000001YAWE9AAP). The b2b feed spells it the store_mapping way.
--       2778 Ephraim — storeops.stores 'B-2778' (address NULL) vs store_mapping 'B-1598'
--                      ("1598 Mount Ephraim Ave") + a store_aliases row noting "2778 is the
--                      relocated 1598 Mount Ephraim Ave — same store".
--     Both codes in each pair resolve to market PA, so market filtering and a PA market grant bind
--     either spelling and scheduling works. But `storeops.stores.B-2778` has a NULL address and
--     `store_mapping.B-2778` has the literal placeholder 'B-2778' as its address. Worth a
--     store-setup cleanup so one physical store stops carrying two identities.
--
-- (3) NO LOGINS ARE CREATED HERE. An employee row makes a person SCHEDULABLE; it does not give them
--     a login. The time clock (`timeclock_status` → the caller's app_user → employee_id), the
--     employee dashboard and chat identity all need a `storeops.app_users` row linked by
--     employee_id, which is created through Roles & Access — and needs an email address, which this
--     roster does not have for anybody. Reported, not invented.
