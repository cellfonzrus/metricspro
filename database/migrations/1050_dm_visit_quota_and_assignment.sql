-- 1050_dm_visit_quota_and_assignment.sql — THE DM DAILY VISIT QUOTA, WHO ASSIGNS THE STORES, AND
-- WHAT GETS PICKED WHEN NOBODY DID.
--
-- OWNER ASK 2026-10-03, verbatim (OWNER-QUOTE-BEGIN — the owner's own words, kept for provenance;
-- the carrier and tenant names in it are theirs and appear nowhere in this migration's behaviour,
-- which is what harness_dm_visit_plan.py §I checks):
--   "each Dm is required to do 2 store visists every day , confuhgurable by each tenant , set uo 2
--    for now editable in the system, the Market manager to monitor the dm performance and assign
--    them the stores, if there are no stores assigned by friday evening then assign stores to the dm
--    based on where the performance is low in an order of priority specailly where the activations
--    and accessories sales are low , then kpi , all these should be confgurable by the tenant ,
--    nothing harcoded, create these for now and give options to assign other deliverables as a drop
--    down menu to add to the priorty list, it could be sales items like edge in luxelink or xfinity
--    in boost , the drop down will be carrier spefici"
-- (OWNER-QUOTE-END)
--
-- DUPLICATE CHECK (the build gate). Searched docs/SYSTEM_DATA_FLOW_INDEX.md and backend/ for an
-- existing visit quota, DM-to-store visit assignment, or "things a DM should push" list. There is
-- none — and four facts this needs DO already have one home each, so every one is DEREFERENCED
-- rather than copied:
--   • which stores a DM owns .................. storeops/org_chain.dm_by_store (index §48.7), the ONE
--     org-tree walk, read inverted by visit_plan.invert_dm_by_store. No second walk.
--   • a store's activations / accessories ..... commcalc/targets_engine via the Daily Targets store
--     summary (index §5). Nothing is recounted from sales here.
--   • attainment % of a target ................ targets_engine.attainment_pct — factored out in this
--     same change so the area roll-up and the visit plan share ONE formula.
--   • the carrier-specific deliverables ....... commcalc.carrier_kpi_metric (mig 060), the per-carrier
--     KPI registry that already exists. The owner's dropdown IS that registry read for the org's own
--     carrier — not a new list of sales items. A tenant wanting a deliverable the registry does not
--     carry yet adds it on the KPI-metrics screen it already has and it appears in the dropdown.
--
-- NOTHING HARDCODED — the owner said it twice. Every number is a column or a row, with a house
-- default in storevisit/visit_plan.HOUSE_CONFIG / HOUSE_PRIORITY_RULES:
--   • how many visits a day      dm_visit_quota_per_day         2      ("set up 2 for now, editable")
--   • which days carry a quota   dm_visit_quota_days            Mon–Fri
--   • the quota switch           dm_visit_quota_enabled         TRUE
--   • the assignment deadline    dm_visit_assign_deadline_dow   5 = Friday
--   •                            dm_visit_assign_deadline_time  '17:00' tenant-local ("friday evening")
--   • how far ahead to fill      dm_visit_assign_horizon_days   7
--   • the auto-fill switch       dm_visit_assign_auto_enabled   TRUE (the owner asked for the fill)
--   • the PRIORITY ORDER itself  storeops.dm_visit_priority_rule rows, seeded below
--
-- THE PRIORITY ORDER IS ROWS, NOT CODE. The seed is exactly the owner's order — activations first,
-- accessories second, KPI third — with a WEIGHT per rule, which is what makes it an order rather
-- than a blend, and a DIRECTION, so a tenant can also prioritise the high end of a metric without
-- this module growing a second mechanism. backend/harness_dm_visit_plan.py parses these rows OUT of
-- this file, so the seed and visit_plan.HOUSE_PRIORITY_RULES cannot drift.
--
-- THE HONESTY RULE (§15z). A store with no target and no measured value on a rule has attainment
-- NULL. It is not scored as a perfect store (which hides it) and not as a total failure (which sends
-- a DM to a store nobody set a target for). It contributes nothing, is named in `unmeasured` in every
-- payload, and a store whose org tree yields no DM at all comes back in `unowned` rather than being
-- dropped — nobody can read the board as "that is everything".
--
-- WHAT A MANAGER ASSIGNED IS NEVER OVERWRITTEN. The fill is per (DM x date) and only TOPS A DAY UP
-- to the quota, so a market manager who assigned one of two visits gets the second filled, not their
-- choice replaced. It is idempotent on (org, date, DM, store): run it twice and the second run writes
-- nothing.
--
-- RULE TWO: no carrier, tenant, store or product name appears in this file. The owner's own examples
-- are deliberately absent — they are rows in a tenant's own KPI registry.
--
-- Additive, idempotent, no backfill, no data movement, NO MONEY MOVED and no money read: the Daily
-- Targets numbers are read only to RANK stores, and nothing here writes to a target, a payout or a
-- commission row.
--
-- REVERT:
--   DROP TABLE IF EXISTS storeops.dm_visit_assignment;
--   DROP TABLE IF EXISTS storeops.dm_visit_priority_rule;
--   ALTER TABLE storeops.tenants
--     DROP COLUMN IF EXISTS dm_visit_quota_enabled,
--     DROP COLUMN IF EXISTS dm_visit_quota_per_day,
--     DROP COLUMN IF EXISTS dm_visit_quota_days,
--     DROP COLUMN IF EXISTS dm_visit_assign_auto_enabled,
--     DROP COLUMN IF EXISTS dm_visit_assign_deadline_dow,
--     DROP COLUMN IF EXISTS dm_visit_assign_deadline_time,
--     DROP COLUMN IF EXISTS dm_visit_assign_horizon_days,
--     DROP COLUMN IF EXISTS dm_visit_assign_last_run,
--     DROP COLUMN IF EXISTS dm_visit_assign_last_detail;
--   (Dropping them restores the house defaults in visit_plan.HOUSE_CONFIG, which are the same values.)

-- ── 1. Per-tenant config ──────────────────────────────────────────────────────────────────────────
ALTER TABLE storeops.tenants
  ADD COLUMN IF NOT EXISTS dm_visit_quota_enabled        boolean     NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS dm_visit_quota_per_day        integer     NOT NULL DEFAULT 2,
  ADD COLUMN IF NOT EXISTS dm_visit_quota_days           jsonb       NOT NULL DEFAULT '[1,2,3,4,5]'::jsonb,
  ADD COLUMN IF NOT EXISTS dm_visit_assign_auto_enabled  boolean     NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS dm_visit_assign_deadline_dow  integer     NOT NULL DEFAULT 5,
  ADD COLUMN IF NOT EXISTS dm_visit_assign_deadline_time text        NOT NULL DEFAULT '17:00',
  ADD COLUMN IF NOT EXISTS dm_visit_assign_horizon_days  integer     NOT NULL DEFAULT 7,
  ADD COLUMN IF NOT EXISTS dm_visit_assign_last_run      timestamptz,
  ADD COLUMN IF NOT EXISTS dm_visit_assign_last_detail   text;

COMMENT ON COLUMN storeops.tenants.dm_visit_quota_per_day IS
  'How many store visits each District Manager is required to complete per quota day. The owner set 2 '
  'and asked that it be editable per tenant. 0 is legal and means this tenant does not require a daily '
  'visit; a negative or unreadable value falls back to the house 2 rather than to "no quota", because a '
  'quota nobody is measured on is indistinguishable from the feature being off — and the switch for '
  'that is dm_visit_quota_enabled.';

COMMENT ON COLUMN storeops.tenants.dm_visit_quota_days IS
  'ISO weekdays (1 = Monday … 7 = Sunday) that carry a quota. Default Mon-Fri. A day outside this set '
  'is neither required nor auto-filled.';

COMMENT ON COLUMN storeops.tenants.dm_visit_assign_deadline_dow IS
  'The weekday by whose evening the market manager is expected to have assigned the coming week. Past '
  'dm_visit_assign_deadline_time on this day, the auto-fill TOPS UP any DM-day that is under quota — it '
  'never replaces a store the manager chose, so assigning one of two visits gets the second filled.';

-- ── 2. The tenant-configurable priority order (the owner's dropdown lives here) ────────────────────
CREATE TABLE IF NOT EXISTS storeops.dm_visit_priority_rule (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid        NOT NULL,
  -- The nil uuid = the org's carrier-neutral default set; a real carrier_id narrows a rule to one
  -- carrier, which is what makes the deliverables dropdown carrier-specific. NOT NULL with the nil
  -- default is the SAME convention commcalc.carrier_kpi_metric (mig 060) already uses for its own
  -- rows — and it is also what lets the unique index below be a plain column list, which ON CONFLICT
  -- can actually target (an expression index over COALESCE cannot be named by a column list).
  carrier_id   uuid        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000'::uuid,
  sort         integer     NOT NULL DEFAULT 100,
  -- WHERE the number comes from. Every value is a DEREFERENCE of an existing home, never a source of
  -- its own: 'target_category' = one of commcalc/targets_engine.CATEGORIES on the Daily Targets store
  -- row; 'kpi_metric' = one metric_key of commcalc.carrier_kpi_metric valued from commcalc.kpi_actual;
  -- 'kpi_all' = the mean attainment across that registry's active metrics, which is the owner's
  -- "then kpi" without this module deciding which KPIs count.
  basis        text        NOT NULL DEFAULT 'target_category',
  metric_key   text        NOT NULL DEFAULT '',
  weight       numeric     NOT NULL DEFAULT 1,
  direction    text        NOT NULL DEFAULT 'low_first',
  label        text,
  is_active    boolean     NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT dm_visit_priority_rule_basis_chk
    CHECK (basis IN ('target_category', 'kpi_metric', 'kpi_all')),
  CONSTRAINT dm_visit_priority_rule_direction_chk
    CHECK (direction IN ('low_first', 'high_first')),
  CONSTRAINT dm_visit_priority_rule_weight_chk CHECK (weight > 0)
);

-- One rule per (org, carrier, basis, metric) — a second row for the same metric would be two voices
-- on one question, which is the duplicate defect in miniature.
CREATE UNIQUE INDEX IF NOT EXISTS dm_visit_priority_rule_uniq
  ON storeops.dm_visit_priority_rule (org_id, carrier_id, basis, metric_key);
CREATE INDEX IF NOT EXISTS dm_visit_priority_rule_org_idx
  ON storeops.dm_visit_priority_rule (org_id, is_active, sort);

COMMENT ON TABLE storeops.dm_visit_priority_rule IS
  'The tenant-configurable order in which under-performing stores are offered to a District Manager '
  'when nobody assigned their week. Seeded as the owner asked — activations, then accessory sales, '
  'then KPI — with a weight per rule, which is what makes it an ORDER rather than a blend. The options '
  'the dropdown offers are not listed here: they are dereferenced from targets_engine.CATEGORIES and '
  'commcalc.carrier_kpi_metric, so a deliverable a tenant adds to its own carrier KPI registry shows '
  'up in the dropdown with no schema change and no code change.';

-- ── 3. The assignment itself ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS storeops.dm_visit_assignment (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid        NOT NULL,
  visit_date      date        NOT NULL,
  -- The DM's employee_id — the SAME identity storeops/org_chain and every other manager surface keys
  -- on, so an assignment and an alert can never mean two different people.
  dm_employee_id  text        NOT NULL,
  dm_email        text,
  dm_name         text,
  store_code      text        NOT NULL,
  market          text,
  source          text        NOT NULL DEFAULT 'manual',
  status          text        NOT NULL DEFAULT 'open',
  priority_rank   integer,
  priority_score  numeric,
  -- WHY this store, in the DM's own words on their list. Written only for an auto-filled row; a
  -- manager's own pick needs no justification from the system.
  priority_reason text,
  priority_detail jsonb,
  assigned_by     text,
  note            text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT dm_visit_assignment_source_chk CHECK (source IN ('manual', 'auto')),
  CONSTRAINT dm_visit_assignment_status_chk CHECK (status IN ('open', 'visited', 'skipped'))
);

-- THE IDEMPOTENCE KEY. One store, one DM, one day, once. This is what makes the Friday fill safe to
-- run on an hourly tick: the second run collides on every row it would re-add and writes nothing.
CREATE UNIQUE INDEX IF NOT EXISTS dm_visit_assignment_uniq
  ON storeops.dm_visit_assignment (org_id, visit_date, dm_employee_id, store_code);
CREATE INDEX IF NOT EXISTS dm_visit_assignment_org_date_idx
  ON storeops.dm_visit_assignment (org_id, visit_date);
CREATE INDEX IF NOT EXISTS dm_visit_assignment_dm_idx
  ON storeops.dm_visit_assignment (org_id, dm_employee_id, visit_date);

COMMENT ON COLUMN storeops.dm_visit_assignment.source IS
  '''manual'' = a market manager chose this store. ''auto'' = the deadline passed with the DM-day under '
  'quota and the priority rules picked it. The fill only ever TOPS UP a day, so a manual row is never '
  'replaced and the two sources coexist on the same day.';

COMMENT ON COLUMN storeops.dm_visit_assignment.priority_reason IS
  'One line saying why the priority rules picked this store (which rule, and how far under target it '
  'was), rendered on the DM''s list. A DM sent somewhere by a machine is owed the reason.';

-- ── 4. The seed — the owner's priority order, for the house org, as ROWS ───────────────────────────
-- Inherited by every tenant that has not written its own rules (visit_plan.normalize_rules falls back
-- to the identical HOUSE_PRIORITY_RULES when a tenant has no rows). The nil carrier_id is the org's
-- carrier-neutral set, so these three apply whatever carrier the tenant later chooses; a tenant adds
-- carrier-specific deliverables alongside them.
INSERT INTO storeops.dm_visit_priority_rule
  (org_id, carrier_id, sort, basis, metric_key, weight, direction, label)
VALUES
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 10, 'target_category', 'activations', 3, 'low_first', 'Activations below target'),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 20, 'target_category', 'accessories', 2, 'low_first', 'Accessory sales below target'),
  ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000000', 30, 'kpi_all',         '',            1, 'low_first', 'KPI attainment')
ON CONFLICT DO NOTHING;

NOTIFY pgrst, 'reload schema';

-- ── pg_cron registration (run AFTER deploy, once, in the Supabase SQL editor with the real secret) ──
-- HOURLY, the mig-433 convention: the per-tenant deadline weekday + local time is compared inside the
-- handler, so ONE job serves every tenant in every timezone, and the unique index above makes the
-- fill idempotent however often it ticks.
--
--   SELECT cron.schedule('dm-visit-assign-run-due', '20 * * * *', $$
--     SELECT net.http_post(
--       url     := 'https://metricspro-production.up.railway.app/api/v1/storevisit/visit-assignments/auto-fill/run-due',
--       headers := jsonb_build_object('Content-Type','application/json','X-Notify-Secret','<NOTIFY_RUN_SECRET>'),
--       body    := '{}'::jsonb); $$);

SELECT 'Migration 1050 complete — DM daily visit quota (2/day, Mon-Fri, editable per tenant), the '
       'market manager''s assignment table, and the tenant-configurable priority order seeded '
       'activations -> accessories -> KPI. Register the pg_cron job with the real NOTIFY_RUN_SECRET '
       'to turn on the Friday-evening fill.' AS status;
