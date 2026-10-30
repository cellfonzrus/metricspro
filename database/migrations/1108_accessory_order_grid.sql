-- 1108_accessory_order_grid.sql — ONE PHONE IS ONE ROW, AND THE ORDERING CATALOG IS DECLARED
-- (index §COMMCALC-49.11 / §COMMCALC-49.12)
--
-- OWNER ASK 2026-10-30, verbatim: *"accessory order plan should combine the accessories for all line
-- items for iphone 13 oin different colors with a drill down option to see what was sold, , iph 13 is
-- different from 13 pro . the proption of blue black should not be hard coded and but an option
-- editable by the user and the order is per phone ... the accessory order should be per phone model
-- and on one column and the cases type on the other columns , the screen protector in the other
-- column and charger and cables should be consolidated based on the sales, not per phone , the cables
-- are common to a lot of phones, current standard is type c which should be assigned ot the phone
-- model in the beinging but it should be editable as a drop down if oit changes later ... scren
-- protectors are clear and privacy, cases are silicon , kicstand, drop proof heavy duty, flowers,
-- diamonds etc and option to add more with pictures"*.
--
-- WHAT THIS DOES
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
--   1. FOUR COLUMNS on `commcalc.accessory_config`, the org's existing accessory-vocabulary row —
--      the same extension shape migs 1031 and 1104 used. No sibling config table.
--   2. ONE NEW TABLE, `commcalc.model_accessory_spec`: which cable / charger / earphone a single
--      phone takes when the house standard is not right for it. It is a new table because the fact
--      has a new GRAIN (org x model x category x dimension) that no existing table carries;
--      `device_model_alias` is one row per model and could not hold four assignments per model
--      without becoming a column-per-accessory table that every new catalog entry would widen.
--   3. A SEED of each org's own COLOUR WORDS, measured from that org's own feed (below).
--
-- NO MONEY MOVES. Every column here is NULL on arrival and the new table is empty. The report that
-- reads them plans QUANTITIES TO BUY: it reads no payout, writes no ledger and feeds no P&L line, GP
-- figure, commission or tax number. Running this file changes no figure anywhere on the platform.
--
-- NOTHING IS SWITCHED ON. The owner's longer-term ask is for this plan to feed the vendor's store
-- automatically (§51's `order_transport` → `shopify_draft_order` → `vendor_customer`). This file
-- wires none of that.
--
-- WHY THE COLOUR WORDS ARE SEEDED PER ORG AND NOT DEFAULTED IN CODE. CLAUDE.md, *"every feature is
-- for EVERY tenant"*: a colour word is a TENANT'S OWN SPELLING, not a policy, and a house default
-- holding one tenant's spellings is the per-tenant code RULE TWO forbids — it is also exactly the
-- defect the owner reported on this same report hours earlier. So the code has no colour list at all,
-- an org that has declared none gets no grouping and the screen says so, and the seeds below are
-- each org's own words read off its own September/October 2026 device lines. A new colour is added on
-- the screen, not in a migration.
--
-- ADDITIVE + IDEMPOTENT. Safe to run twice. Run it as ONE statement batch in the Supabase SQL Editor
-- (one Run, which commits itself — never split a BEGIN from its COMMIT across two Runs).
--
-- REVERT:
--   ALTER TABLE commcalc.accessory_config
--     DROP COLUMN IF EXISTS accessory_order_catalog,
--     DROP COLUMN IF EXISTS accessory_order_option_mix,
--     DROP COLUMN IF EXISTS accessory_order_device_classes,
--     DROP COLUMN IF EXISTS device_colour_tokens;
--   DROP TABLE IF EXISTS commcalc.model_accessory_spec;
--   (Reverting loses the declared catalog, the style mixes and the per-phone connectors. The report
--   falls back to the house catalog with every phone on the current standard, and stops combining
--   colour variants — its correct un-declared behaviour, not a broken one.)

-- ── 1. THE ORDERING CATALOG, THE STYLE MIXES, AND THE TWO OTHER DECLARATIONS ─────────────────────
-- All four NULL on arrival → the house answers: the catalog the owner dictated, an EVEN split across
-- each category's styles (labelled as such on screen, never presented as a measurement), handsets and
-- tablets as the classes that take an accessory, and NO colour grouping until words are declared.
ALTER TABLE commcalc.accessory_config
  ADD COLUMN IF NOT EXISTS accessory_order_catalog        JSONB,
  ADD COLUMN IF NOT EXISTS accessory_order_option_mix     JSONB,
  ADD COLUMN IF NOT EXISTS accessory_order_device_classes TEXT[],
  ADD COLUMN IF NOT EXISTS device_colour_tokens           TEXT[];

COMMENT ON COLUMN commcalc.accessory_config.accessory_order_catalog IS
  'Accessory Order Plan (index COMMCALC-49.12): the kinds of accessory this org orders, each with its '
  'option dimensions — case styles, protector types, charger ports and wattages, cable connectors, '
  'earphone fits — and an optional picture per option. NULL = the house catalog. A category is SPLIT '
  '(bought per phone, divided across its styles) or ASSIGNED (bought by connector, consolidated '
  'across phones). Written by PUT /api/v1/commcalc/accessory-order-plan/config.';
COMMENT ON COLUMN commcalc.accessory_config.accessory_order_option_mix IS
  'How a SPLIT category divides across its options, e.g. {"screen_protector": [{"key":"clear",'
  '"share":75},{"key":"privacy","share":25}]}. Shares may be percentages or fractions; the total '
  'decides which was meant. NULL = an even split, which the screen labels as one.';
COMMENT ON COLUMN commcalc.accessory_config.accessory_order_device_classes IS
  'Which product classes (index §62) take an accessory. A POLICY, not a vocabulary: a router and a '
  'SIM kit sit inside a tenant''s own device departments and take no case, and one tenant''s '
  'September carried 498 such units. NULL = the house answer, handsets and tablets.';
COMMENT ON COLUMN commcalc.accessory_config.device_colour_tokens IS
  'This org''s own colour WORDS as its product descriptions spell them, so one phone is one row '
  'whatever colour it sold in (index COMMCALC-49.11). A multi-word colour is one entry. There is NO '
  'house default on purpose: a colour word is this tenant''s spelling, and a default holding another '
  'tenant''s words is the per-tenant code RULE TWO forbids. NULL = no grouping, and the report says '
  'so rather than borrowing another tenant''s list.';

-- ── 2. WHICH CABLE / CHARGER / EARPHONE ONE PHONE TAKES ──────────────────────────────────────────
-- A ROW IS AN OVERRIDE, NOT A SETTING. A phone with no row here takes the catalog's declared current
-- standard, and the sheet SAYS which it was ('standard' against a chosen one) — the same distinction
-- the lifecycle ruling makes between "ruled" and "not ruled on". Clearing an assignment DELETES the
-- row rather than writing the standard in as though somebody had chosen it.
CREATE TABLE IF NOT EXISTS commcalc.model_accessory_spec (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  model       TEXT NOT NULL,          -- the PHONE (a model family), as the report rows name it
  category    TEXT NOT NULL,          -- 'cable' | 'charger' | 'earphones' | a declared category key
  dimension   TEXT NOT NULL,          -- 'connector' | 'ports' | 'wattage' | 'fit' | a declared one
  option      TEXT NOT NULL,          -- the option key, which must exist in the org's catalog
  declared_by TEXT,
  declared_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT model_accessory_spec_once UNIQUE (org_id, model, category, dimension)
);

-- The vocabulary is NOT closed at the database, because the catalog is extensible by design: the
-- owner asked for an *"option to add another type if it comes later"*. The route refuses an option
-- the org's catalog does not offer, and the reader ignores one, so a stale row cannot produce an
-- order line nobody can buy.
CREATE INDEX IF NOT EXISTS model_accessory_spec_org ON commcalc.model_accessory_spec (org_id, model);

-- A MIGRATION GRANTS WHAT IT CREATES (CLAUDE.md). The backend reaches every relation through
-- PostgREST and Supabase no longer grants new tables automatically. `service_role` only: the browser
-- never reads this table directly, so an `anon` grant would widen exposure rather than fix anything.
GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc.model_accessory_spec TO service_role;

COMMENT ON TABLE commcalc.model_accessory_spec IS
  'Accessory Order Plan (index COMMCALC-49.12): which cable, charger or earphone ONE phone takes '
  'when the declared current standard is not right for it. Absent = the standard, and the order '
  'sheet says which. Written by PUT /api/v1/commcalc/accessory-order-plan/model-spec.';

-- ── 3. EACH ORG'S OWN COLOUR WORDS, MEASURED FROM ITS OWN FEED ───────────────────────────────────
-- Read on 2026-10-30 off `commcalc.daily_sales_feed`, August to October 2026, each org's own declared
-- device departments, through the same model parse the report uses. ONLY the words that are plainly
-- colours are seeded: a word that merely looked like one was left out, because dropping a word that
-- is NOT a colour merges two phones that take different cases, which is worse than leaving a phone
-- split in two. Anything missing is added on the screen in a second.
--
-- `WHERE device_colour_tokens IS NULL` — a seed never overwrites a declaration a human has made.
UPDATE commcalc.accessory_config
   SET device_colour_tokens = ARRAY[
         'blk','black','jetblack','jet black','blue','dblue','navy','sea','gray','grey',
         'silver','cloud silver','white','mist','orange','corange','violet','purple','pink',
         'red','green','gold','cosmic','space']
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND device_colour_tokens IS NULL;

UPDATE commcalc.accessory_config
   SET device_colour_tokens = ARRAY[
         'black','white','midnight','pink','blue','red','purple','gray','grey','silver',
         'green','gold','navy','violet','orange']
 WHERE org_id = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
   AND device_colour_tokens IS NULL;

UPDATE commcalc.accessory_config
   SET device_colour_tokens = ARRAY[
         'blk','black','jetblack','blue','navy','gray','grey','silver','white','orange',
         'violet','purple','pink','red','green','gold']
 WHERE org_id = 'f4f1c16e-2acf-4221-854a-c29a605754a7'
   AND device_colour_tokens IS NULL;
