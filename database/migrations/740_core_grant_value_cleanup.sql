-- 740_core_grant_value_cleanup.sql  ·  band 700-799 (mod-platform-core)
-- OWNER RULING #5, 2026-08-08 (verbatim): "clean the bad vlaues and make it drop down with option
-- to select many instead of free text".
--
-- WHAT THIS IS. A one-time normalisation of the STORE values that live in two permission-adjacent
-- columns, `storeops.app_users.store_code` / `.store_codes` (the reporting grant) and
-- `storeops.employees.home_store` (the roster field the Roles page used to copy INTO that grant).
-- Both were free-text boxes, so they accumulated addresses, truncated addresses, typos and one
-- word that is not a place at all.
--
-- WHAT IT IS NOT. It does NOT change WHICH STORE anybody is granted. Every statement below rewrites
-- a spelling into the code for the SAME PHYSICAL STORE, verified against the tenant's own roster
-- (`storeops.stores` + `commcalc.store_mapping` + `commcalc.store_aliases`) and cross-checked
-- against where the person actually clocks in (`storeops.shifts` / `storeops.timelog`). Nobody
-- gains a store, nobody loses a store, and no market grant is touched — narrowing a live person is
-- the owner's decision in the UI, not a migration's (ruling #6).
--
-- WHY IT MATTERS. A grant written as an address only matches rows spelled that exact way. The span
-- keyset is anchored on CODES, so "4640 Diversey Chicago" matched a single string while the store's
-- own rows (`Diversey`, `4640-A W Diversey Ave`) did not — which is why reps and store managers saw
-- blank pages that a super-admin could see fine.
--
-- RESOLUTION RULES, strongest first. Every row below was produced by one of these and each one is
-- named in the comment above its statement:
--   R1 exact store CODE, ignoring case and punctuation      ("B - 2612" -> "B-2612", "ave u" -> "Ave U")
--   R2 exact synonym in commcalc.store_aliases              ("2640 Narragansett" -> Narragansett)
--   R3 exact ADDRESS in either vocabulary                   ("4640 Diversey Chicago" -> Diversey)
--   R4 unique address PREFIX  (one store, reviewed)         ("639 Lincoln" -> Chicago heights)
--   R5 unique HOUSE NUMBER + >=0.70 similarity (reviewed)   ("3248 Lawarance" -> lawrence)
-- R1-R3 are the deterministic rules the running code uses (app/core/scope.resolve_store_grant).
-- R4/R5 are human-reviewed ONE-TIME rules and deliberately do NOT exist in the runtime resolver —
-- a permission value must never be guessed by a matcher.
--
-- PARKED, NOT APPLIED (owner decides; see docs/handoffs/platform-core.md for the evidence):
--   * `Floating`          — E204 Vanessa Jacobo. Not a store. She really does float: 43 work records
--                           at Armitage (27), Grand (11), Diversey (5). Suggested: grant all three
--                           with the new multi-select. NOT guessed here.
--   * `3738 26th Street`  — E177 Alondra Navarro (store_manager). No store has house number 3738.
--                           42 of 42 work records are at `3735 26th`. Suggested: 3735 26th.
--   * `market = "15, NYC, LI"` — mohdusa5366@gmail.com. `15` is in neither market vocabulary and
--                           grants nothing; NYC + LI are real and untouched. Suggested: drop `15`.
--   * `market = "<li>"`   — one house admin. It IS a real market string (it exists in
--                           commcalc.store_mapping for store `<2022>`), so it is not "bad data" the
--                           resolver can reject; it is junk vocabulary. Inert today: that login is
--                           scope `all`. Suggested: fix the `<2022>` / `<li>` mapping row instead.
--
-- IDEMPOTENT: every statement is guarded on the EXACT old value, so a second run matches no rows.
-- REVERSIBLE: the old value is in the comment above each statement.
-- No DDL, no grants, no schema change, no money column.

begin;

-- app_users: 23 distinct (org, old -> new) pairs covering 35 grant values
-- [HOUSE] 'B - 2612' -> 'B-2612'   (1 login: mohdusa5366@gmail.com)
update storeops.app_users set store_code = 'B-2612', store_codes = array_replace(store_codes, 'B - 2612', 'B-2612')
  where org_id = '00000000-0000-0000-0000-000000000001' and store_code = 'B - 2612';
-- [LUX] '4640 Diversey Chicago' -> 'Diversey'   (3 logins: brenda.romero@luxelinkwireless.com, carolina.espinoza@luxelinkwireless.com, diana.antunez@luxelinkwireless.com)
update storeops.app_users set store_code = 'Diversey', store_codes = array_replace(store_codes, '4640 Diversey Chicago', 'Diversey')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '4640 Diversey Chicago';
-- [LUX] '3966 Grand Chicago' -> 'Grand'   (3 logins: eduardo.brito@luxelinkwireless.com, tatiana.vergara@luxelinkwireless.com, yuvia.esparza@luxelinkwireless.com)
update storeops.app_users set store_code = 'Grand', store_codes = array_replace(store_codes, '3966 Grand Chicago', 'Grand')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '3966 Grand Chicago';
-- [LUX] '6500 Irving' -> 'Irving Park'   (1 login: alejandro.galarza@luxelinkwireless.com)
update storeops.app_users set store_code = 'Irving Park', store_codes = array_replace(store_codes, '6500 Irving', 'Irving Park')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '6500 Irving';
-- [LUX] '2640 Narragansett Chicago' -> 'Narragansett'   (1 login: brendaliz.aviles@luxelinkwireless.com)
update storeops.app_users set store_code = 'Narragansett', store_codes = array_replace(store_codes, '2640 Narragansett Chicago', 'Narragansett')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '2640 Narragansett Chicago';
-- [LUX] '2414 Cermak Chicago' -> 'Cermark'   (2 logins: daisy.brito@luxelinkwireless.com, nancy.espinoza@luxelinkwireless.com)
update storeops.app_users set store_code = 'Cermark', store_codes = array_replace(store_codes, '2414 Cermak Chicago', 'Cermark')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '2414 Cermak Chicago';
-- [LUX] '5601 Belmont Chicago' -> 'Belmont'   (3 logins: christopher.romero@luxelinkwireless.com, emily.olvera@luxelinkwireless.com, jocelyn.hernandez@luxelinkwireless.com)
update storeops.app_users set store_code = 'Belmont', store_codes = array_replace(store_codes, '5601 Belmont Chicago', 'Belmont')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '5601 Belmont Chicago';
-- [LUX] '3248 Lawrence Chicago' -> 'lawrence'   (1 login: genvieve.montijo@luxelinkwireless.com)
update storeops.app_users set store_code = 'lawrence', store_codes = array_replace(store_codes, '3248 Lawrence Chicago', 'lawrence')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '3248 Lawrence Chicago';
-- [LUX] '104-08' -> 'Lefferts'   (1 login: kallol.salman@gmail.com)
update storeops.app_users set store_code = 'Lefferts', store_codes = array_replace(store_codes, '104-08', 'Lefferts')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '104-08';
-- [LUX] '18226 Kedzie Hazel Crest' -> 'kedzie'   (2 logins: mea.collins@luxelinkwireless.com, melennie.perez@luxelinkwireless.com)
update storeops.app_users set store_code = 'kedzie', store_codes = array_replace(store_codes, '18226 Kedzie Hazel Crest', 'kedzie')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '18226 Kedzie Hazel Crest';
-- [LUX] '6500 Irving Park Chicago' -> 'Irving Park'   (1 login: natalie.chico@luxelinkwireless.com)
update storeops.app_users set store_code = 'Irving Park', store_codes = array_replace(store_codes, '6500 Irving Park Chicago', 'Irving Park')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '6500 Irving Park Chicago';
-- [LUX] '639 Lincoln Chicago Heights' -> 'Chicago heights'   (1 login: natalie.mendoza@luxelinkwireless.com)
update storeops.app_users set store_code = 'Chicago heights', store_codes = array_replace(store_codes, '639 Lincoln Chicago Heights', 'Chicago heights')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '639 Lincoln Chicago Heights';
-- [LUX] '2640 Narragansett' -> 'Narragansett'   (1 login: natasha.cabrera@luxelinkwireless.com)
update storeops.app_users set store_code = 'Narragansett', store_codes = array_replace(store_codes, '2640 Narragansett', 'Narragansett')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '2640 Narragansett';
-- [LUX] '3352 26th Chicago' -> '3352 26th'   (3 logins: janet.garibay@luxelinkwireless.com, nicolas.navarrete@luxelinkwireless.com, zuleicka.lopez@luxelinkwireless.com)
update storeops.app_users set store_code = '3352 26th', store_codes = array_replace(store_codes, '3352 26th Chicago', '3352 26th')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '3352 26th Chicago';
-- [LUX] 'ave u' -> 'Ave U'   (1 login: no.thanks.my.way@gmail.com)
update storeops.app_users set store_code = 'Ave U', store_codes = array_replace(store_codes, 'ave u', 'Ave U')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = 'ave u';
-- [LUX] '3966 Grand' -> 'Grand'   (1 login: ruby.ortiz@luxelinkwireless.com)
update storeops.app_users set store_code = 'Grand', store_codes = array_replace(store_codes, '3966 Grand', 'Grand')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '3966 Grand';
-- [LUX] '3560 Norstand Ave' -> 'Nostrand'   (1 login: satyasreshta1411@gmail.com)
update storeops.app_users set store_code = 'Nostrand', store_codes = array_replace(store_codes, '3560 Norstand Ave', 'Nostrand')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '3560 Norstand Ave';
-- [LUX] '2317 Cicero Cicero' -> 'Cicero'   (1 login: terry.pitre@luxelinkwireless.com)
update storeops.app_users set store_code = 'Cicero', store_codes = array_replace(store_codes, '2317 Cicero Cicero', 'Cicero')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '2317 Cicero Cicero';
-- [LUX] '2414 Cermak' -> 'Cermark'   (2 logins: nallely.espinoza@luxelinkwireless.com, yesica.perez@luxelinkwireless.com)
update storeops.app_users set store_code = 'Cermark', store_codes = array_replace(store_codes, '2414 Cermak', 'Cermark')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '2414 Cermak';
-- [LUX] '3248 Lawarance' -> 'lawrence'   (1 login: angelica.chacon@luxelinkwireless.com)
update storeops.app_users set store_code = 'lawrence', store_codes = array_replace(store_codes, '3248 Lawarance', 'lawrence')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '3248 Lawarance';
-- [LUX] '639 Lincoln' -> 'Chicago heights'   (1 login: antonio.chavez@luxelinkwireless.com)
update storeops.app_users set store_code = 'Chicago heights', store_codes = array_replace(store_codes, '639 Lincoln', 'Chicago heights')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '639 Lincoln';
-- [LUX] '3735 26th Chicago' -> '3735 26th'   (2 logins: liset.jacobo@luxelinkwireless.com, luis.martinez@luxelinkwireless.com)
update storeops.app_users set store_code = '3735 26th', store_codes = array_replace(store_codes, '3735 26th Chicago', '3735 26th')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '3735 26th Chicago';
-- [LUX] '4801 Armitage Chicago' -> 'Armitage'   (1 login: silvia.nava@luxelinkwireless.com)
update storeops.app_users set store_code = 'Armitage', store_codes = array_replace(store_codes, '4801 Armitage Chicago', 'Armitage')
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and store_code = '4801 Armitage Chicago';

-- employees.home_store: 26 distinct pairs covering 39 employees
-- [LUX] '2317 Cicero Cicero' -> 'Cicero'   (1: E201)
update storeops.employees set home_store = 'Cicero'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '2317 Cicero Cicero';
-- [LUX] '3560 Nostrand' -> 'Nostrand'   (2: E212, E213)
update storeops.employees set home_store = 'Nostrand'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '3560 Nostrand';
-- [LUX] '3248 Lawrence Chicago' -> 'lawrence'   (1: E188)
update storeops.employees set home_store = 'lawrence'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '3248 Lawrence Chicago';
-- [LUX] '2414 Cermak Chicago' -> 'Cermark'   (2: E141, E184)
update storeops.employees set home_store = 'Cermark'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '2414 Cermak Chicago';
-- [HOUSE] 'b-418' -> 'B-418'   (1: E003)
update storeops.employees set home_store = 'B-418'
  where org_id = '00000000-0000-0000-0000-000000000001' and home_store = 'b-418';
-- [HOUSE] 'B - 2612' -> 'B-2612'   (1: E029)
update storeops.employees set home_store = 'B-2612'
  where org_id = '00000000-0000-0000-0000-000000000001' and home_store = 'B - 2612';
-- [LUX] '3248 Lawarance' -> 'lawrence'   (1: E178)
update storeops.employees set home_store = 'lawrence'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '3248 Lawarance';
-- [LUX] '5601 Belmont Chicago' -> 'Belmont'   (3: E183, E187, E190)
update storeops.employees set home_store = 'Belmont'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '5601 Belmont Chicago';
-- [LUX] '18226 Kedzie Hazel Crest' -> 'kedzie'   (2: E193, E194)
update storeops.employees set home_store = 'kedzie'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '18226 Kedzie Hazel Crest';
-- [LUX] '2640 Narragansett' -> 'Narragansett'   (1: E206)
update storeops.employees set home_store = 'Narragansett'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '2640 Narragansett';
-- [LUX] '3560 Norstand Ave' -> 'Nostrand'   (1: E229)
update storeops.employees set home_store = 'Nostrand'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '3560 Norstand Ave';
-- [LUX] '3966 Grand Chicago' -> 'Grand'   (3: E151, E153, E202)
update storeops.employees set home_store = 'Grand'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '3966 Grand Chicago';
-- [LUX] '4801 Armitage Chicago' -> 'Armitage'   (1: E199)
update storeops.employees set home_store = 'Armitage'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '4801 Armitage Chicago';
-- [LUX] '2640 Narragansett Chicago' -> 'Narragansett'   (1: E181)
update storeops.employees set home_store = 'Narragansett'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '2640 Narragansett Chicago';
-- [LUX] '3352 26th Chicago' -> '3352 26th'   (3: E154, E189, E198)
update storeops.employees set home_store = '3352 26th'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '3352 26th Chicago';
-- [LUX] '6500 Irving Park Chicago' -> 'Irving Park'   (1: E165)
update storeops.employees set home_store = 'Irving Park'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '6500 Irving Park Chicago';
-- [LUX] '4640 Diversey Chicago' -> 'Diversey'   (3: E180, E182, E186)
update storeops.employees set home_store = 'Diversey'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '4640 Diversey Chicago';
-- [LUX] '3966 Grand' -> 'Grand'   (1: E207)
update storeops.employees set home_store = 'Grand'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '3966 Grand';
-- [LUX] '3735 26th Chicago' -> '3735 26th'   (2: E191, E192)
update storeops.employees set home_store = '3735 26th'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '3735 26th Chicago';
-- [LUX] '639 Lincoln' -> 'Chicago heights'   (1: E179)
update storeops.employees set home_store = 'Chicago heights'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '639 Lincoln';
-- [LUX] 'Ave u' -> 'Ave U'   (1: E215)
update storeops.employees set home_store = 'Ave U'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = 'Ave u';
-- [LUX] '639 Lincoln Chicago Heights' -> 'Chicago heights'   (1: E197)
update storeops.employees set home_store = 'Chicago heights'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '639 Lincoln Chicago Heights';
-- [LUX] '2414 Cermak' -> 'Cermark'   (2: E142, E203)
update storeops.employees set home_store = 'Cermark'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '2414 Cermak';
-- [LUX] '6500 Irving' -> 'Irving Park'   (1: E176)
update storeops.employees set home_store = 'Irving Park'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '6500 Irving';
-- [LUX] '104-08' -> 'Lefferts'   (1: E235)
update storeops.employees set home_store = 'Lefferts'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = '104-08';
-- [LUX] 'ave u' -> 'Ave U'   (1: E236)
update storeops.employees set home_store = 'Ave U'
  where org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c' and home_store = 'ave u';

commit;

-- ── VERIFY (run after; a bare [] from a DDL batch is not proof) ────────────────────────────────
-- Expect: zero rows. Every remaining unresolvable value is one of the PARKED ones above.
--
-- select u.org_id, u.email, u.role, u.store_code
-- from storeops.app_users u
-- where u.is_active is not false and coalesce(u.store_code,'') <> ''
--   and not exists (select 1 from storeops.stores s
--                   where s.org_id = u.org_id
--                     and upper(regexp_replace(s.store_code,'[^A-Za-z0-9]','','g'))
--                       = upper(regexp_replace(u.store_code,'[^A-Za-z0-9]','','g')))
-- order by 1,2;
