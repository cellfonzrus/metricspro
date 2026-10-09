-- 1065_month_focus_declaration.sql
-- THE MONTH'S DECLARED FOCUS, AND THE WEEKLY CHECK-IN (owner 2026-10-09, index §62)
--
-- Owner, verbatim: "in the beginning of the month Market manager or above when they log in should
-- define the focus for the month -, update which initiative is driving spiffs that month and assign
-- targets to the store< the notification will come every week on Monday on the platform to update any
-- new commisison changes or spiff on any new products, assign targets to stores, offer temparoray
-- spiff, this module needs a creative busines smanager to dessign something out of the box to drive
-- sales offer spiff keeping the current oppprtunities in mind"
--
-- ONE ROW PER (org, month). It holds a DECLARATION — what this month is about, which pay type is
-- meant to be driving it, which temporary spiffs are on the table, and which weekly check-ins have
-- been done. It is NOT a pay table and NOTHING is paid from it:
--
--   * No rep, no amount owed and no payout column exists here.
--   * A temporary spiff in `declaration->'temp_spiffs'` carries a STATUS ('proposed' / 'approved' /
--     'live' / 'ended'). The money only moves when somebody edits `commcalc.payout_config`
--     (`custom_spiffs`), which the commission engine reads and this subsystem only ever READS.
--     `month_focus.spiff_reconciliation` reports the two sides disagreeing, in both directions.
--   * So this migration MOVES NO MONEY and changes no payout. It adds one table and two config
--     columns, and recomputes nothing.
--
-- WHY THE DUE LIST IS NOT STORED. "A notification MUST clear when the check says everything is OK"
-- (owner 2026-07-26). There is no reminder table, no send log and no scheduled job in this subsystem:
-- the outstanding list is computed from this row + today + live measurements every time a surface
-- asks (`month_focus.outstanding`), so an item cannot survive its own cause being fixed. The Monday
-- reminder the owner asked for is that same function asked on a Monday — "on the platform", as he
-- said, so no mailbox and no pg_cron job is created by this file.
--
-- WHY THE TWO KNOBS ARE CONFIG COLUMNS (RULE TWO). How many days into the month the focus is still
-- merely "due" rather than "overdue", and which weekday the check-in falls on, are tenant policy, not
-- code. They go on `commcalc.commission_org_config` beside the other per-org commission policy
-- columns, with the house defaults living in code (7 days, weekday 0 = Monday) so a tenant that never
-- sets them behaves exactly as shipped.
--
-- IDEMPOTENT + ADDITIVE: every statement is IF NOT EXISTS / ADD COLUMN IF NOT EXISTS. Re-running it
-- changes nothing. No existing table, column, index, view or row is altered or dropped.
--
-- REVERT:
--   DROP TABLE IF EXISTS commcalc.month_focus;
--   ALTER TABLE commcalc.commission_org_config
--     DROP COLUMN IF EXISTS focus_declaration_days,
--     DROP COLUMN IF EXISTS focus_checkin_weekday;
--   (Dropping the table loses the declarations and nothing else — no money, no payout and no feed
--    reads it. Every surface degrades to "not declared yet", which is also how it behaves before
--    this migration is applied.)

BEGIN;

-- 1. THE DECLARATION. One row per (org, period). `period` is stored as the canonical 'YYYY-MM'; the
--    reader resolves the tenant's other spelling through `router._pvariants` (§19.47), which is that
--    fact's one home, so no second period vocabulary is introduced here.
CREATE TABLE IF NOT EXISTS commcalc.month_focus (
    org_id        uuid        NOT NULL,
    period        text        NOT NULL,
    declaration   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, period)
);

COMMENT ON TABLE commcalc.month_focus IS
  'index §62 — the month''s declared focus, the pay type driving its spiffs, the temporary spiffs on '
  'the table and the weekly check-ins done. A DECLARATION, never a pay table: nothing is paid from '
  'this row, and commcalc.payout_config remains the only money. Written only through '
  'PUT /api/v1/commcalc/month-focus/{period}; decided by commcalc/month_focus.py.';

COMMENT ON COLUMN commcalc.month_focus.declaration IS
  'The shape commcalc/month_focus.normalise_declaration produces: headline, categories, note, '
  'spiff_initiative{pay_type,label,rationale}, temp_spiffs[{name,rate,unit,window_start,window_end,'
  'cap,stores,status,note}], target_note, declared_by, declared_at, checkins[{week_start,confirmed_at,'
  'confirmed_by,changes,note}]. A temp_spiff status of approved/live is what the reconciliation '
  'EXPECTS to find in payout_config.custom_spiffs; proposed is on the table, not in the money.';

-- The one query shape this table serves beyond the primary key: "this org's months, newest first".
CREATE INDEX IF NOT EXISTS month_focus_org_period_idx
    ON commcalc.month_focus (org_id, period DESC);

-- 2. RLS, exactly as the sibling commcalc tables carry it: the service role reads and writes, every
--    endpoint is org-scoped in code (CI enforces that — harness_org_scope_guard.py).
ALTER TABLE commcalc.month_focus ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                    WHERE schemaname = 'commcalc' AND tablename = 'month_focus'
                      AND policyname = 'month_focus_service_all') THEN
        CREATE POLICY month_focus_service_all ON commcalc.month_focus
            FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- 3. THE TWO POLICY KNOBS (RULE TWO — config, never code). NULL means "use the house default", so a
--    tenant that sets neither is byte-identical to the shipped behaviour.
ALTER TABLE commcalc.commission_org_config
    ADD COLUMN IF NOT EXISTS focus_declaration_days  integer,
    ADD COLUMN IF NOT EXISTS focus_checkin_weekday   integer;

COMMENT ON COLUMN commcalc.commission_org_config.focus_declaration_days IS
  'index §62 — how many days into the month the focus declaration is still ''due'' rather than '
  '''overdue''. NULL = the house default (7). Read by commcalc/month_focus.outstanding.';
COMMENT ON COLUMN commcalc.commission_org_config.focus_checkin_weekday IS
  'index §62 — which weekday the in-platform check-in reminder falls on, as date.weekday(): '
  '0 = Monday … 6 = Sunday. NULL = the house default (0 = Monday, as the owner asked). Read by '
  'commcalc/month_focus.checkin_days.';

COMMIT;
