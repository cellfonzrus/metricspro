-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- 995 — SALES FROM EVENTS: the report's per-org config, and the ROI's own cost rows
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- OWNER DIRECTIVE 2026-09-09 (verbatim): "i need a seaprate reporting menu for only rsk activations
-- done per store and their retention , the report will be called sales from events and in marketing
-- menu, for boost it will eb coming from teh rsk events tender and the others not sure yet but
-- provision will be made - so 2 reports - 1 total sales with all available fields n that report and
-- the second is the retention , the third will be roi from te event, that will include teh cost to
-- set up teh event and teh total commssion received for the lines activated on that day via teh rsk
-- , if teh event was not loaded previously it will still run a report with the roi and ask the user
-- to input teh cost details or link it to the event created in the system if the user inputs teh
-- details it will create the event in the system with the minimal information which is required to
-- compute the cost , cost of event , payroll paid , the number of phones activated and their cosrt
-- willcome from teh sales report and the sku report, it is an unlocked phones given away the the
-- systtem will ask while gatehring this information how much is teh cost of teh phone, for boost
-- check the register of rsk"
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- WHAT THIS DOES *NOT* CREATE (the CLAUDE.md duplicate-check build gate, applied and recorded)
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- Searched the index and the schema for: event, campaign, ROI, event cost, store attribution,
-- register, subscriber retention, phone giveaway, payroll for a day.
--
--   • NO second event table.        core.marketing_event (mig 986) IS the event the owner means by
--                                   "the event created in the system". The ROI report links to it
--                                   and creates through POST /marketing/events — the module's one
--                                   creator.
--   • NO second store attribution.  core.marketing_event_store (mig 986) stays the only event↔store
--                                   map. Nothing here re-implements it.
--   • NO second sales derivation.   The reports call commcalc's shared per-(store, rep, day) pass
--                                   with the register-filtered rows. No table here holds a sales
--                                   number, an activation count or a commission dollar.
--   • NO second retention concept.  Subscriber retention is DERIVED on read from commcalc.raw_mi.
--                                   Nothing is stored. (It is also NOT the GDPR check-in retention
--                                   on /marketing/checkin-retention — different thing, same word.)
--   • NO promotion of giveaway.unit_cost. See the boundary note on marketing_event_cost below.
--
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- MONEY / SAFETY
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- Additive, idempotent, re-runnable. NOTHING money-valued is seeded. No P&L line, statement, payout,
-- accrual or commission figure reads anything created here: `marketing_event_cost` is read by ONE
-- reporting endpoint (GET /marketing/event-sales/roi) and by nothing else. RLS on, zero policies,
-- zero anon/authenticated grants — the module's established posture.
--
-- ⚠ WRITTEN, NOT APPLIED. SQL is owner-only (CLAUDE.md).

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. RULE TWO — the report's vocabulary is per-org DATA on the config row that already exists
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The owner's "the others not sure yet but provision will be made" is answered here and nowhere
-- else: another carrier's event register is an UPDATE to one array, not a deploy and not a branch.
-- The house defaults live in app/modules/marketing/event_sales.py::DEFAULT_EVENT_SALES_CONFIG and
-- are reproduced as column DEFAULTs so the posture is visible in the data, not only in code.
ALTER TABLE core.marketing_config
  -- The POS register value(s) whose sales ARE event sales. Default is the single value
  -- commcalc/flags.py's RSK_ACTIVATIONS flag has always used — so this changes nothing for an org
  -- that configures nothing. NOTE FOR THE OWNER: RSK is the value of raw_sales.register, NOT a
  -- tender type; the tender on those same rows reads Cash / Credit Card / Debit Card.
  ADD COLUMN IF NOT EXISTS event_sales_registers            TEXT[]  NOT NULL DEFAULT '{RSK}',
  -- Which of the SHARED contract-type classifier's buckets the headline "activations" number counts.
  -- These are the classifier's own output vocabulary ('premium' | 'byod' | 'upgrade'), never a
  -- carrier label. Upgrades are out by default (an upgrade is an existing line, so it has nothing
  -- the event created to retain) and are reported beside the headline regardless.
  ADD COLUMN IF NOT EXISTS event_sales_activation_classes   TEXT[]  NOT NULL DEFAULT '{premium,byod}',
  -- The retention windows, in days after the sale. "Still active now" is always reported as well.
  ADD COLUMN IF NOT EXISTS event_retention_windows_days     INT[]   NOT NULL DEFAULT '{30,60,90}',
  -- May the ROI read a phone's cost from the SKU catalog before asking a human? Off would mean
  -- every phone cost is typed. Default ON — the owner asked for the cost to come "from the sales
  -- report and the sku report", with the question asked only where the SKU does not resolve.
  ADD COLUMN IF NOT EXISTS event_roi_phone_cost_from_catalog BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN core.marketing_config.event_sales_registers IS
  'RULE TWO. The POS register value(s) whose sales are event sales (raw_sales.register — NOT a '
  'tender type). House default {RSK} = the value commcalc/flags.py RSK_ACTIVATIONS has always used. '
  'An EMPTY list matches nothing, deliberately: an org with no event register has no event sales, '
  'and the report says so rather than showing it every sale in the store.';
COMMENT ON COLUMN core.marketing_config.event_sales_activation_classes IS
  'Which buckets of the ONE shared classifier (commcalc.calculator.classify_contract_type) the '
  'headline activation count includes. Values are that classifier''s own vocabulary, never a '
  'carrier label. A value outside {premium,byod,upgrade} is ignored by the resolver rather than '
  'stored to count nothing in silence.';
COMMENT ON COLUMN core.marketing_config.event_retention_windows_days IS
  'Days after the sale at which subscriber retention is reported. "Still active now" is always '
  'reported in addition. The subscriber feed is a MONTHLY snapshot, so a window is answered by the '
  'snapshot for the month the target day falls in.';

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. THE ROI'S OWN COST ROWS — and the boundary this migration deliberately does NOT cross
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- ═══ THE GIVEAWAY BOUNDARY, DECIDED AND STATED ════════════════════════════════════════════════
-- core.marketing_event_giveaway.unit_cost already exists, and migration 986 says of it:
--     "INFORMATIONAL money (see the vendor.cost note). Never seeded, never read by a money path."
-- The ROI needs a phone cost. Reading unit_cost would REVERSE that decision — turning informational
-- money into a money path — and would silently change what an existing column MEANS for any tenant
-- that has already typed a number into it as a note-to-self while packing a table.
--
-- RECOMMENDATION IMPLEMENTED (option (a) of the two the owner was offered): the giveaway column is
-- LEFT INFORMATIONAL and untouched. The ROI's costs live here, on their own explicitly-declared
-- rows, where every figure carries who typed it and when. What the ROI takes from the giveaway side
-- is qty_given — a COUNT, which was never informational money — and only ever beside the count the
-- sales rows already give.
--
-- If the owner would rather promote unit_cost, that is one decision in one place: read it in
-- app/modules/marketing/event_sales.py::build_costs and amend the note on 986's column. It is
-- deliberately not something a later edit can do by accident.
--
-- WHY A TABLE AND NOT MORE COLUMNS ON marketing_event: the phone cost is PER PRODUCT (the screen
-- asks once per model and reuses the answer for every unit of it), the figures are append-only
-- evidence of what a human said, and an event can be costed more than once as better numbers turn
-- up. A single planned_spend column can carry none of that. marketing_event.planned_spend is still
-- the FIRST place the event cost is looked for — this table only carries what someone typed on the
-- ROI screen, which then wins.
CREATE TABLE IF NOT EXISTS core.marketing_event_cost (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  event_id     UUID NOT NULL REFERENCES core.marketing_event(id) ON DELETE CASCADE,
  -- 'event_spend' | 'payroll' | 'phones_given' — the three the owner named. Not a CHECK constraint:
  -- the kinds are named in event_sales.py (COST_* ) and a kind nothing reads simply never appears in
  -- a report, which is a better failure than a migration that has to run to add a cost category.
  cost_kind    TEXT NOT NULL,
  -- EITHER a whole-component amount (event spend, payroll) …
  amount       NUMERIC,
  -- … OR a per-product unit cost (the phone models the SKU catalog could not price).
  product_ref  TEXT,                 -- product_id or sku, as the sale line carried it
  qty          NUMERIC,
  unit_cost    NUMERIC,
  -- Always 'entered' today: this table exists precisely to record what a HUMAN said, so that a
  -- derived figure and a typed one can never be mistaken for each other on the ROI screen.
  basis        TEXT NOT NULL DEFAULT 'entered',
  note         TEXT,
  entered_by   TEXT,
  entered_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS marketing_event_cost_by_event
  ON core.marketing_event_cost (org_id, event_id, cost_kind);

COMMENT ON TABLE core.marketing_event_cost IS
  'What a human said an event cost, for the Sales-from-Events ROI report (owner 2026-09-09). '
  'Append-only evidence: every row carries who typed it and when, so a derived figure and a typed '
  'one are never confused. Read by ONE reporting endpoint (GET /marketing/event-sales/roi) and by '
  'NOTHING else — no P&L line, statement, payout, accrual or commission figure reads it. '
  'core.marketing_event_giveaway.unit_cost is deliberately NOT promoted into this money path and '
  'stays informational exactly as migration 986 declared it.';

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. RLS — the module's posture, unchanged: on, with no policy and no anon/authenticated grant
-- ══════════════════════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['marketing_event_cost'] LOOP
    EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON core.%I FROM anon, authenticated', t);
  END LOOP;
END $$;

COMMIT;

SELECT 'Migration 995 complete — Sales from Events: marketing_config gains the RULE TWO report '
       'vocabulary (event registers, activation classes, retention windows, phone-cost source) and '
       'core.marketing_event_cost carries the ROI''s typed cost figures. No second event table, no '
       'second store attribution, no second sales derivation, and marketing_event_giveaway.unit_cost '
       'is left informational.' AS status;

-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- REVERT:
--   DROP TABLE IF EXISTS core.marketing_event_cost;
--   ALTER TABLE core.marketing_config
--     DROP COLUMN IF EXISTS event_sales_registers,
--     DROP COLUMN IF EXISTS event_sales_activation_classes,
--     DROP COLUMN IF EXISTS event_retention_windows_days,
--     DROP COLUMN IF EXISTS event_roi_phone_cost_from_catalog;
--   (The reports keep working after a revert: resolve_event_sales_config is ADAPTIVE and falls back
--    to the house defaults when the columns are absent. Only the ability to RE-POINT them is lost.)
-- ═══════════════════════════════════════════════════════════════════════════════════════════════
