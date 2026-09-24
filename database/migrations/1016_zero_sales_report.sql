-- 1016_zero_sales_report.sql — ZERO SALES: per-org config + the Management Overview tile
-- ─────────────────────────────────────────────────────────────────────────────────────────────────
-- OWNER REQUEST 2026-09-22 (verbatim):
--   "need a zero sales report in management overview dashboard capturing no activations or upgrades
--    using standard filters and date range and notification options"
-- Grain and trigger answered by the owner the same day: BOTH store and rep (a store row with its rep
-- rows underneath), and alert after N CONSECUTIVE zero days — N config, house default 2.
--
-- ⚠ NOT APPLIED. Surfaced for owner approval before anything touches the schema (CLAUDE.md: money-
-- touching changes and migrations are surfaced, never applied unasked). THE REPORT SHIPS WITHOUT IT:
-- `router._zero_sales_config` degrades to the code-level house defaults in `zero_sales.HOUSE_CONFIG`
-- when this table is absent or unreadable, and those defaults ARE the documented shipped behaviour.
-- Applying this migration changes NO number on any screen — it only makes the knobs editable per
-- tenant and adds one tile to the house dashboard.
--
-- ADDITIVE + IDEMPOTENT. No column, table, constraint, payout, ledger or existing tile is changed.
-- Nothing here books money: the zero-sales report is READ-ONLY and computes no dollar at all.
--
-- ── a. commcalc.zero_sales_config — RULE TWO, config never code ─────────────────────────────────
-- Every behaviour of the report is a per-org row with the HOUSE row (00000000-…-0001) as the default
-- every tenant inherits — the `report_pull_map` (mig 207) / `connector_route_policy` (mig 998)
-- inheritance shape, read as `.in_("org_id", [org_id, HOUSE_ORG])` with the tenant row winning.
--
--   grains              which scope keys the report emits. A GRAIN IS A SCOPE KEY, NEVER A BRANCH.
--   count_classes       which activation buckets count as a sale. The values are `line_class.BUCKETS`
--                       — the ONE activation predicate's own vocabulary, not a second list. House
--                       default is ALL THREE ('premium' = new activation + port, 'upgrade', 'byod'):
--                       a BYOD line IS an activation, and a store that sold five of them is not a
--                       zero-sales store. A value the report cannot count is REFUSED on save, never
--                       stored — a typo that made every store read zero would be this report's own
--                       silent zero.
--   trading_day_source  'schedule' (house default) reads a store's OPEN DAYS from scheduled hours —
--                       the same fact `targets_engine.scope_hours_by_day` already serves the Daily
--                       Targets engine. THERE IS NO STORE TRADING-CALENDAR TABLE on this platform
--                       (storeops.stores carries no hours/open-days column), and this migration
--                       deliberately does NOT invent one: a second calendar would drift from the
--                       schedule the stores are actually staffed by. 'all_days' is the opt-out for a
--                       tenant that does not schedule here.
--   excluded_weekdays   0=Mon … 6=Sun, applied under either source — the explicit fallback for a
--                       tenant with no schedule, so a store shut on Sundays is not alerted forever.
--   consecutive_days    N. House default 2: one quiet day is noise, two days running is a signal.
--   gap_policy          what a NOT-REPORTED day does to a run of zero days.
--                       'break'  (HOUSE DEFAULT) the run ends at the gap — consecutiveness cannot be
--                                asserted across a day nobody measured. The gap is NOT silent: the
--                                row names it ("2 zero days, run ended by 1 not-reported day").
--                       'bridge' the run continues; the unknown day adds NOTHING to the count, and
--                                every alert built from a bridged run NAMES the gap.
--                       DELIBERATELY NOT OFFERED: counting a not-reported day AS a zero day. The
--                       CHECK below refuses it in the database as well as in the code, because a
--                       config row that switched the silent zero back on is the one thing this whole
--                       report exists to prevent.
--   rep_requires_shift  a rep with no shift that day is 'off', not a zero against them.
--   include_today       today is still trading; a zero at 9am is not a zero day.
--   alerts_enabled      OPT-IN per tenant, exactly like storeops.tenants.epay_alerts_enabled.
--
-- ── b. the Management Overview tile ─────────────────────────────────────────────────────────────
-- Dashboard tiles are D1 CONFIG (mig 068 commcalc.ui_label_override, scope='tiles'), house rows every
-- tenant inherits and may override in the Dashboard Designer (tile_layout.resolve_tile_layout). This
-- APPENDS one tile to the existing house 'management-overview' layout that mig 948 seeded — the mig
-- 1002 "All Flags" precedent verbatim, including refusing to append when the tile is already there
-- and leaving a tenant's own hand-arranged layout completely alone.
--
-- ── c. the daily alert cron ─────────────────────────────────────────────────────────────────────
-- Registered the same way the mig-905 ePay alert cron is, against the EXISTING alert path:
-- storeops.alert_log scope 'zero_sales', recipients DM ∪ above-DM, dedup per (recipient, store,
-- last-zero-day, scope) — no new alert table, no second dedup rule, no second recipient resolution.
--
-- NO LINEAGE ENTRY: this introduces no external feed. The report reads feeds that are already
-- registered (daily_sales_feed / raw_sales, §2) and storeops.shifts, and writes nothing.
--
-- REVERT (paste and run to undo):
--   DROP TABLE IF EXISTS commcalc.zero_sales_config;
--   UPDATE commcalc.ui_label_override
--      SET label = jsonb_set(label::jsonb, '{tiles}',
--                    (SELECT COALESCE(jsonb_agg(t), '[]'::jsonb)
--                       FROM jsonb_array_elements(label::jsonb -> 'tiles') t
--                      WHERE t ->> 'title' <> 'Zero Sales'))::text,
--          updated_at = now()
--    WHERE org_id = '00000000-0000-0000-0000-000000000001'
--      AND scope = 'tiles' AND key = 'management-overview';
--   SELECT cron.unschedule('zero-sales-alerts-daily');
--   NOTIFY pgrst, 'reload schema';
-- ─────────────────────────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ── a. per-org config ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commcalc.zero_sales_config (
  org_id              UUID PRIMARY KEY,
  grains              TEXT[]  NOT NULL DEFAULT ARRAY['store','rep'],
  count_classes       TEXT[]  NOT NULL DEFAULT ARRAY['premium','upgrade','byod'],
  trading_day_source  TEXT    NOT NULL DEFAULT 'schedule',
  excluded_weekdays   INT[]   NOT NULL DEFAULT ARRAY[]::INT[],
  consecutive_days    INT     NOT NULL DEFAULT 2,
  gap_policy          TEXT    NOT NULL DEFAULT 'break',
  rep_requires_shift  BOOLEAN NOT NULL DEFAULT TRUE,
  include_today       BOOLEAN NOT NULL DEFAULT FALSE,
  alerts_enabled      BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- THE INVARIANT, enforced in the database as well as in the code: a not-reported day may never be
  -- counted as a zero day. 'count' is refused here so no future writer can store it.
  CONSTRAINT zero_sales_gap_policy_ck  CHECK (gap_policy IN ('break','bridge')),
  CONSTRAINT zero_sales_trading_src_ck CHECK (trading_day_source IN ('schedule','all_days')),
  CONSTRAINT zero_sales_consecutive_ck CHECK (consecutive_days >= 1)
);

COMMENT ON TABLE commcalc.zero_sales_config IS
  'Zero Sales report + alert config, per org, HOUSE row = the platform default every tenant '
  'inherits (tenant row wins). No row anywhere = zero_sales.HOUSE_CONFIG, the shipped behaviour. '
  'gap_policy may never be "count": a not-reported day is not a zero day.';
COMMENT ON COLUMN commcalc.zero_sales_config.trading_day_source IS
  'schedule = a store''s open days are the days it has scheduled hours (targets_engine.'
  'scope_hours_by_day, the ONE place this platform knows a store''s trading days). A store with no '
  'schedule in the window has an UNKNOWN calendar, not a closed one: every day is evaluated and the '
  'report says the calendar is unknown.';
COMMENT ON COLUMN commcalc.zero_sales_config.count_classes IS
  'The line_class.BUCKETS that count as a sale. Default all three - a BYOD line IS an activation.';

-- the HOUSE default row (every tenant inherits it until it saves its own)
INSERT INTO commcalc.zero_sales_config (org_id) VALUES ('00000000-0000-0000-0000-000000000001')
ON CONFLICT (org_id) DO NOTHING;

-- ── b. the Management Overview tile (mig 1002's precedent, verbatim) ────────────────────────────
UPDATE commcalc.ui_label_override
   SET label = jsonb_set(
         label::jsonb,
         '{tiles}',
         (label::jsonb -> 'tiles') || '[
           {"title":"Zero Sales","icon":"🚫",
            "desc":"Store-days - and the rep-days underneath them - with no activation and no upgrade. A day nothing landed for reads \"not reported\", never zero.",
            "items":[{"href":"/commcalc/zero-sales","label":"Zero Sales"}]}
         ]'::jsonb
       )::text,
       updated_at = now()
 WHERE org_id = '00000000-0000-0000-0000-000000000001'
   AND scope   = 'tiles'
   AND key     = 'management-overview'
   AND NOT (label::jsonb -> 'tiles') @> '[{"title":"Zero Sales"}]'::jsonb;

-- Post-flight: the tile must be present exactly once and the layout must still parse as the shape
-- the hub reads. A half-applied dashboard is a blank screen for every manager, so this rolls back
-- rather than leaving one.
DO $$
DECLARE n INT; v INT;
BEGIN
  SELECT COUNT(*) INTO n
    FROM commcalc.ui_label_override o,
         LATERAL jsonb_array_elements(o.label::jsonb -> 'tiles') t
   WHERE o.org_id = '00000000-0000-0000-0000-000000000001'
     AND o.scope = 'tiles' AND o.key = 'management-overview'
     AND t ->> 'title' = 'Zero Sales';
  IF n <> 1 THEN
    RAISE EXCEPTION 'expected exactly 1 "Zero Sales" tile on the house management-overview layout, found %', n;
  END IF;
  SELECT (label::jsonb ->> 'version')::INT INTO v
    FROM commcalc.ui_label_override
   WHERE org_id = '00000000-0000-0000-0000-000000000001'
     AND scope = 'tiles' AND key = 'management-overview';
  IF v IS NULL THEN
    RAISE EXCEPTION 'house management-overview layout lost its version key';
  END IF;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ── c. the daily alert cron (mig 905's ePay registration, same shape) ───────────────────────────
-- Runs once a day; each tenant is skipped unless ITS OWN zero_sales_config.alerts_enabled is true,
-- so applying this sends nobody anything until a tenant opts in.
--
--   SELECT cron.schedule('zero-sales-alerts-daily', '0 13 * * *', $$
--     SELECT net.http_post(
--       url     := '<API_BASE>/api/v1/commcalc/zero-sales/alerts/run-due',
--       headers := jsonb_build_object('Content-Type','application/json',
--                                     'X-Notify-Secret', '<NOTIFY_SECRET>'),
--       body    := '{}'::jsonb);
--   $$);
