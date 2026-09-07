-- 986_marketing_event_module.sql — MARKETING module, PHASE 1: outside-store event management
--
-- OWNER DIRECTIVE 2026-09-06 (verbatim): "Under market we will Need an event management module for
-- outside store events, give me the framework of what should be involved including not limited to
-- the following with gps enabled: Theme of the event - back to school etc or byod plan / Location /
-- Venue / Goal for the event - how many activations or accessories / What items are needed, a user
-- created checklist / Social media and other marketing planned links for the creatives / Time /
-- What time do the employees have to get there / Who is the outside party if there is one e.g DJ/
-- food truck / table event / Employees planned for the event / Back up employee if they don't show
-- up / How are employees getting there / Who is picking up who if needed / Giveaways
--   Again none of the options I mentioned above are hard coded but options pre added with plus sign
--   to add more as per user discretion"
--
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- DUPLICATE CHECK (CLAUDE.md build gate, owner 2026-09-02) — what was searched, and what is REUSED
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- Searched docs/SYSTEM_DATA_FLOW_INDEX.md for: event, campaign, marketing, promotion, geofence,
-- check-in, attendance, giveaway, vendor. Findings:
--
--   · NO event or campaign concept exists anywhere in the platform. The module is genuinely new,
--     so this migration creates the entity — it does not fork one.
--   · GPS CAPTURE already exists — storevisit (mig 027) records check_in_lat / check_in_lng /
--     check_in_accuracy on a DM's store visit. That CAPTURE CONTRACT is adopted verbatim by
--     marketing_event_checkin below (same three column names, same "one point-in-time fix taken at
--     the moment the human presses the button" semantics). What did NOT exist anywhere in the
--     repository is any distance / geofence DECISION — storevisit stores the fix and never judges
--     it. Rather than invent a second geolocation idea, the decision now lives in ONE shared pure
--     module, app/modules/core/geo.py, which marketing calls and storevisit can adopt unchanged.
--     There is exactly one geofence rule in the platform, and it is not in this module.
--   · SALES ACTUALS already exist — commcalc router._sales_cell_agg is THE one shared per
--     (store, rep, day) pass behind the Sales Report, Executive MTD and Daily Targets (§3), reached
--     through _compute_feed_actuals_py. Event ACTUALS are DERIVED from that pass through the §14
--     read-contract discipline (app/modules/marketing/actuals.py calls it; it re-derives nothing).
--     THERE IS NO SALES COLUMN ON ANY TABLE IN THIS MIGRATION — goals are typed, results are read.
--   · DOCUMENTS already exist — storeops.store_document (mig 946) + the private `store-docs` bucket
--     + signed-URL-by-id. EXTENDED here with a nullable event_id and three event doc kinds, exactly
--     as mig 964 extended it with policy_id. No second documents table, no second bucket.
--   · ATTENTION + NOTIFICATION already exist — core.import_health providers (the ~40-provider
--     universal admin-attention system) and storeops.alert_log (mig 433) for dedupe. Marketing
--     registers providers; it adds no second notifier and no alert table.
--   · CONTROL BOX lamps already exist — core.system_check (mig 970) derives one lamp per LIVE
--     attention provider with no code change, so the providers registered by this module light up
--     automatically. Mig 987 additionally declares the parts this phase does NOT monitor as
--     `unmonitored` rows, so a coverage gap is visible rather than silently green.
--
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- SCHEMA CHOICE — core.marketing_*, not a new schema
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- PostgREST serves only the project's exposed schemas (public / commcalc / storeops / core /
-- notify); a `.schema("marketing")` call would 404. Same reasoning migs 053, 715, 800 and 980
-- recorded. CRM (mig 800), the most recent full new module, chose core.crm_*; this mirrors it.
--
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- RULE TWO — config, never code
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The owner was explicit: "none of the options I mentioned above are hard coded but options pre
-- added with plus sign to add more as per user discretion". So EVERY option list the owner named —
-- theme, venue type, outside-party type, transport mode, giveaway type, role at the event, creative
-- channel, goal metric — is a ROW in ONE generic table, core.marketing_option, keyed by list_key.
-- There is no CHECK constraint on any option column and no Python enum: a new theme is an INSERT,
-- never a deploy. Resolution is HOUSE rows ∪ TENANT rows with the tenant winning per key (the
-- ui_label_override / report_labels precedent), so a tenant starts with a working vocabulary and
-- can rename, deactivate or extend any of it.
--
-- ONE generic option table rather than eight JSONB columns on storeops.tenants (the mig 964
-- precedent) because there are eight lists here, they need per-row activation and ordering for the
-- "+" UI, and later phases add more lists — eight columns would have become sixteen.
--
-- The only string this migration treats as behavioural is `status`, which is a LIFECYCLE
-- (draft → approved → live → closed / cancelled) driving what may be edited — not tenant vocabulary.
--
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- EMPLOYEE GPS — sensitive personal data (owner constraint, and the law)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- core.marketing_event_checkin holds ONE coordinate pair per person per event, captured at the
-- instant that person presses "I'm here". There is no schema here that could hold a track: no
-- repeated-position table, no interval column, no background-report shape. Check-OUT records a
-- timestamp and NO second coordinate, on purpose. Retention is EXPLICIT and per-org
-- (marketing_config.checkin_geo_retention_days, house default 180): purge_after_date is stamped on
-- the row at write time so what happens to it is visible in the row itself rather than buried in a
-- job. A rep can read back every check-in row recorded about them (GET /marketing/my-checkins).
--
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- MONEY / SAFETY
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- No payout, rate, plan, commission, accrual or paid/earned column is created or touched. Two
-- INFORMATIONAL dollar amounts exist (vendor cost, giveaway unit cost); no P&L, statement or
-- payout reader reads them, and NOTHING money-valued is seeded — mig 987 seeds vocabulary only.
-- Additive + idempotent + re-runnable. RLS on, zero policies, zero anon/authenticated grants.

BEGIN;

CREATE SCHEMA IF NOT EXISTS core;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 1. THE OPTION REGISTRY — every "+ add more" list in the module (RULE TWO)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- list_key ∈ the LIST_KEYS tuple in app/modules/marketing/event_logic.py. That tuple names the
-- lists; it never names a VALUE. Adding a value = one row. Adding a whole new list = one constant
-- plus rows, still no behavioural branch.
CREATE TABLE IF NOT EXISTS core.marketing_option (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  list_key    TEXT NOT NULL,         -- 'theme' | 'venue_type' | 'party_type' | 'transport_mode' |
                                     -- 'giveaway_type' | 'event_role' | 'link_channel' | 'goal_metric'
  key         TEXT NOT NULL,         -- stable per-list identifier, [a-z0-9_.-]
  label       TEXT NOT NULL,         -- what a human sees; freely renameable
  sort_order  INT  NOT NULL DEFAULT 100,
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  -- Per-option extras that are DATA, not behaviour. For goal_metric: {"unit":"count"|"money",
  -- "derivable":true,"source":"sales_cell_agg","field":"activations"} — `derivable` says whether
  -- the platform can compute an actual for it from the SHARED sales pass, so a tenant-invented
  -- metric is accepted and simply reported as "no automatic actual" instead of silently zero.
  extra       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by  TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by  TEXT,
  UNIQUE (org_id, list_key, key)
);
CREATE INDEX IF NOT EXISTS marketing_option_lookup
  ON core.marketing_option (org_id, list_key, is_active, sort_order);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 2. PER-ORG SWITCHES — approval (DEFAULT OFF, as directed), geofence, GPS retention
-- ══════════════════════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS core.marketing_config (
  org_id                      UUID PRIMARY KEY,
  -- APPROVAL IS OFF BY DEFAULT. An org that never touches this setting can plan, staff and run an
  -- event with nobody having to approve anything — the owner asked for the switch, not the policy.
  approval_required           BOOLEAN NOT NULL DEFAULT FALSE,
  -- When approval is ON, an event whose planned spend is at or below this needs no approval. NULL
  -- (the default) = every event needs approval once the switch is on. Ignored entirely while the
  -- switch is off — the threshold can never turn approval ON by itself.
  approval_spend_threshold    NUMERIC,
  -- Geofence: how close "here" has to be, and how bad a GPS fix may be before it is untrustworthy.
  default_checkin_radius_m    INT NOT NULL DEFAULT 150,
  max_checkin_accuracy_m      INT NOT NULL DEFAULT 200,
  -- A check-in outside the radius is RECORDED and FLAGGED, never silently dropped and never
  -- (by default) refused: a real person standing at a real event with a bad fix must still be able
  -- to say they are there. An org that wants it hard-blocked flips this.
  block_checkin_outside_fence BOOLEAN NOT NULL DEFAULT FALSE,
  -- EXPLICIT RETENTION for the coordinate rows (see the GPS header note).
  checkin_geo_retention_days  INT NOT NULL DEFAULT 180,
  -- Attention: how far ahead "tomorrow's event isn't staffed" starts nagging.
  staffing_alert_lead_hours   INT NOT NULL DEFAULT 48,
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by                  TEXT
);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 3. THE EVENT — identity, place, time, approval, debrief
-- ══════════════════════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS core.marketing_event (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  title              TEXT NOT NULL,
  description        TEXT,
  theme_key          TEXT,                    -- vocabulary = marketing_option list_key 'theme'
  status             TEXT NOT NULL DEFAULT 'draft',   -- lifecycle, see event_logic.STATUSES

  -- WHOSE event: a market, and the store(s) whose performance the window is read against
  -- (core.marketing_event_store). primary_store_code is the headline owner, not the whole set.
  market             TEXT,
  primary_store_code TEXT,

  -- PLACE
  venue_name         TEXT,
  venue_type_key     TEXT,                    -- vocabulary = list_key 'venue_type'
  address            TEXT,
  city               TEXT,
  state              TEXT,
  postal_code        TEXT,
  geo_lat            NUMERIC,                 -- the venue pin (NOT a person's location)
  geo_lng            NUMERIC,
  checkin_radius_m   INT,                     -- NULL = marketing_config.default_checkin_radius_m
  setup_notes        TEXT,
  parking_notes      TEXT,

  -- TIME. staff_call_at is a FIRST-CLASS COLUMN, deliberately separate from event_start: the owner
  -- asked "Time / What time do the employees have to get there" as two distinct questions, and the
  -- answer to the second is what the staffing screen, the reminder and the lateness read use.
  event_start        TIMESTAMPTZ,
  event_end          TIMESTAMPTZ,
  staff_call_at      TIMESTAMPTZ,
  setup_start_at     TIMESTAMPTZ,
  teardown_end_at    TIMESTAMPTZ,

  -- SPEND (planned) — the number the approval threshold is compared against. Informational:
  -- no P&L, statement or payout reader consumes it.
  planned_spend      NUMERIC,

  -- APPROVAL. `approval_state` is 'not_required' whenever the org switch is off (or the event is
  -- under the threshold), so the audit trail records WHY an event went live without a signature
  -- instead of leaving a NULL that reads like a missing approval.
  approval_state     TEXT NOT NULL DEFAULT 'not_required',  -- not_required|pending|approved|rejected
  approval_reason    TEXT,                    -- why it was/wasn't required (from the pure decision)
  approved_by        TEXT,
  approved_at        TIMESTAMPTZ,
  approval_note      TEXT,

  -- DEBRIEF (post-event). Photos are storeops.store_document rows with doc_kind 'event_photo'.
  debrief_what_worked  TEXT,
  debrief_what_didnt   TEXT,
  debrief_notes        TEXT,
  debrief_at           TIMESTAMPTZ,
  debrief_by           TEXT,

  is_active          BOOLEAN NOT NULL DEFAULT TRUE,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by         TEXT,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by         TEXT
);
CREATE INDEX IF NOT EXISTS marketing_event_org_when
  ON core.marketing_event (org_id, event_start DESC);
CREATE INDEX IF NOT EXISTS marketing_event_org_status
  ON core.marketing_event (org_id, status, event_start DESC);

-- Stores whose performance the event window is read against (§ actuals). Many-to-many on purpose:
-- one table event outside a mall can be worked by two stores.
CREATE TABLE IF NOT EXISTS core.marketing_event_store (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     UUID NOT NULL,
  event_id   UUID NOT NULL REFERENCES core.marketing_event(id) ON DELETE CASCADE,
  store_code TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, event_id, store_code)
);
CREATE INDEX IF NOT EXISTS marketing_event_store_by_store
  ON core.marketing_event_store (org_id, store_code);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 4. GOALS — typed once. ACTUALS ARE NOT STORED HERE.
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- "Goal for the event - how many activations or accessories" (owner). metric_key is CONFIG
-- (list_key 'goal_metric'), so a tenant can add "leads collected" or "trade-ins" without a deploy.
-- There is deliberately NO actual_value column: an actual read from a stored copy would be a second
-- sales derivation and would drift from the Sales Report the day someone edited it. The actual is
-- computed on read from commcalc's shared pass — see app/modules/marketing/actuals.py.
CREATE TABLE IF NOT EXISTS core.marketing_event_goal (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  event_id     UUID NOT NULL REFERENCES core.marketing_event(id) ON DELETE CASCADE,
  metric_key   TEXT NOT NULL,        -- vocabulary = list_key 'goal_metric'
  target_value NUMERIC,
  note         TEXT,
  sort_order   INT NOT NULL DEFAULT 100,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, event_id, metric_key)
);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 5. PEOPLE — planned staff, the named backup, transport, and who drives whom
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The owner asked for four separate things and this is one table because they are four columns of
-- one fact ("this person, at this event"): the role, whether they are the BACKUP for a named
-- primary, how they are getting there, and who is picking them up.
--
--   is_backup=false, backup_for_staff_id NULL   -> a planned primary
--   is_backup=true,  backup_for_staff_id = <primary row>  -> "back up employee if they don't show up"
--   pickup_by_staff_id = <another row on this event>      -> "who is picking up who if needed"
--
-- backup_for_staff_id and pickup_by_staff_id are SELF-references, so both graphs are data. Nothing
-- in code knows a role name, a transport name, or who drives.
CREATE TABLE IF NOT EXISTS core.marketing_event_staff (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL,
  event_id            UUID NOT NULL REFERENCES core.marketing_event(id) ON DELETE CASCADE,
  employee_id         TEXT,                 -- storeops.employees.employee_id (TEXT, cross-schema:
                                            -- no FK, same as storeops.shifts)
  employee_name       TEXT,                 -- denormalized display name at planning time
  role_key            TEXT,                 -- vocabulary = list_key 'event_role'
  is_backup           BOOLEAN NOT NULL DEFAULT FALSE,
  backup_for_staff_id UUID REFERENCES core.marketing_event_staff(id) ON DELETE SET NULL,
  -- planned | confirmed | declined | no_show — the human's answer, not an inference. no_show is set
  -- by a person after the fact; the platform never decides someone didn't turn up.
  confirm_state       TEXT NOT NULL DEFAULT 'planned',
  confirmed_at        TIMESTAMPTZ,
  transport_mode_key  TEXT,                 -- vocabulary = list_key 'transport_mode'
  pickup_by_staff_id  UUID REFERENCES core.marketing_event_staff(id) ON DELETE SET NULL,
  pickup_at           TIMESTAMPTZ,
  pickup_location     TEXT,
  call_time_override  TIMESTAMPTZ,          -- this person's own call time; NULL = event.staff_call_at
  notes               TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by          TEXT,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by          TEXT,
  UNIQUE (org_id, event_id, employee_id, role_key, is_backup)
);
CREATE INDEX IF NOT EXISTS marketing_event_staff_by_event
  ON core.marketing_event_staff (org_id, event_id);
CREATE INDEX IF NOT EXISTS marketing_event_staff_by_employee
  ON core.marketing_event_staff (org_id, employee_id);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 6. ATTENDANCE — ONE point-in-time GPS fix, taken when the person presses the button
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- SENSITIVE PERSONAL DATA. Read the GPS block in this file's header before adding a column here.
-- The three coordinate columns carry the SAME names storevisit (mig 027) has used since day one —
-- this is the same capture contract, not a second one. The judgement (distance, inside/outside,
-- accuracy trust) comes from the ONE shared pure decision in app/modules/core/geo.py and is stored
-- as evidence so a dispute can be re-read, not re-litigated from raw numbers.
CREATE TABLE IF NOT EXISTS core.marketing_event_checkin (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  event_id           UUID NOT NULL REFERENCES core.marketing_event(id) ON DELETE CASCADE,
  staff_id           UUID REFERENCES core.marketing_event_staff(id) ON DELETE SET NULL,
  employee_id        TEXT,
  employee_name      TEXT,
  checked_in_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  check_in_lat       NUMERIC,              -- storevisit capture contract (mig 027)
  check_in_lng       NUMERIC,
  check_in_accuracy  NUMERIC,              -- metres, as the browser reported it
  -- Evidence of the ONE geofence decision (core/geo.evaluate_checkin), stored so it is auditable:
  distance_m         NUMERIC,
  radius_m           INT,
  within_geofence    BOOLEAN,
  decision           TEXT,                 -- inside | outside | unverified_no_fix | unverified_accuracy
  decision_note      TEXT,
  -- Check-OUT is a TIMESTAMP ONLY. There is deliberately no second coordinate pair: knowing when
  -- someone left does not require knowing where they were when they left.
  checked_out_at     TIMESTAMPTZ,
  -- EXPLICIT RETENTION, stamped at write time from marketing_config.checkin_geo_retention_days.
  purge_after_date   DATE,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS marketing_event_checkin_by_event
  ON core.marketing_event_checkin (org_id, event_id, checked_in_at DESC);
CREATE INDEX IF NOT EXISTS marketing_event_checkin_by_employee
  ON core.marketing_event_checkin (org_id, employee_id, checked_in_at DESC);
CREATE INDEX IF NOT EXISTS marketing_event_checkin_purge
  ON core.marketing_event_checkin (purge_after_date);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 7. THIRD PARTIES — "Who is the outside party if there is one e.g DJ/ food truck / table event"
-- ══════════════════════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS core.marketing_event_vendor (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL,
  event_id        UUID NOT NULL REFERENCES core.marketing_event(id) ON DELETE CASCADE,
  party_type_key  TEXT,                  -- vocabulary = list_key 'party_type' (DJ, food truck, …)
  vendor_name     TEXT,
  contact_name    TEXT,
  contact_phone   TEXT,
  contact_email   TEXT,
  -- INFORMATIONAL money. No P&L / statement / payout reader consumes this column; a vendor cost
  -- becomes a booked number only when a human enters it in the finance module.
  cost            NUMERIC,
  confirm_state   TEXT NOT NULL DEFAULT 'planned',   -- planned | confirmed | declined | cancelled
  confirmed_at    TIMESTAMPTZ,
  arrival_at      TIMESTAMPTZ,
  -- The signed contract: a storeops.store_document row (doc_kind 'event_vendor_contract'). The id
  -- only — the storage path is never echoed to a client, exactly as leases and COIs work.
  contract_document_id UUID,
  notes           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by      TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by      TEXT
);
CREATE INDEX IF NOT EXISTS marketing_event_vendor_by_event
  ON core.marketing_event_vendor (org_id, event_id);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 8. CHECKLIST — "What items are needed, a user created checklist"
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- User-created per event, and template-able per theme so the same table event doesn't get retyped
-- every August. packed → returned is what makes shrinkage visible for borrowed kit (the tent, the
-- speaker) the same way qty_out → qty_returned does for giveaways.
CREATE TABLE IF NOT EXISTS core.marketing_checklist_template (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL,
  name        TEXT NOT NULL,
  theme_key   TEXT,                     -- NULL = offered for every theme
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by  TEXT,
  UNIQUE (org_id, name)
);
CREATE TABLE IF NOT EXISTS core.marketing_checklist_template_item (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       UUID NOT NULL,
  template_id  UUID NOT NULL REFERENCES core.marketing_checklist_template(id) ON DELETE CASCADE,
  label        TEXT NOT NULL,
  category     TEXT,
  qty          NUMERIC,
  is_returnable BOOLEAN NOT NULL DEFAULT TRUE,
  sort_order   INT NOT NULL DEFAULT 100,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS marketing_checklist_template_item_by_tpl
  ON core.marketing_checklist_template_item (org_id, template_id, sort_order);

CREATE TABLE IF NOT EXISTS core.marketing_event_checklist_item (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL,
  event_id           UUID NOT NULL REFERENCES core.marketing_event(id) ON DELETE CASCADE,
  label              TEXT NOT NULL,
  category           TEXT,
  qty                NUMERIC,
  owner_staff_id     UUID REFERENCES core.marketing_event_staff(id) ON DELETE SET NULL,
  owner_employee_id  TEXT,
  is_returnable      BOOLEAN NOT NULL DEFAULT TRUE,
  is_packed          BOOLEAN NOT NULL DEFAULT FALSE,
  packed_at          TIMESTAMPTZ,
  packed_by          TEXT,
  is_returned        BOOLEAN NOT NULL DEFAULT FALSE,
  returned_at        TIMESTAMPTZ,
  returned_by        TEXT,
  sort_order         INT NOT NULL DEFAULT 100,
  notes              TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by         TEXT
);
CREATE INDEX IF NOT EXISTS marketing_event_checklist_by_event
  ON core.marketing_event_checklist_item (org_id, event_id, sort_order);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 9. CREATIVE LINKS — "Social media and other marketing planned links for the creatives"
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- PHASE-1 SEAM. This phase models the planned LINK per channel and nothing else. The later phases
-- (creative gallery on bring-your-own cloud storage, marketing-portal asset pull) attach to the
-- two nullable columns below and need no change to any other table:
--   · asset_ref     — the external identifier of a pulled/stored asset, whatever that turns out to
--                     be (a gallery row id, a portal asset key, an object key). TEXT, no FK, no
--                     meaning assigned in this phase.
--   · asset_source  — which system asset_ref belongs to. NULL for every row this phase writes.
-- Nothing in phase-1 code reads either column. They exist so the gallery phase is an INSERT into an
-- existing row rather than a new link table that would compete with this one.
CREATE TABLE IF NOT EXISTS core.marketing_event_link (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL,
  event_id         UUID NOT NULL REFERENCES core.marketing_event(id) ON DELETE CASCADE,
  channel_key      TEXT,                  -- vocabulary = list_key 'link_channel'
  label            TEXT,
  url              TEXT,
  planned_post_at  TIMESTAMPTZ,
  posted_at        TIMESTAMPTZ,
  status           TEXT NOT NULL DEFAULT 'planned',   -- planned | scheduled | posted | cancelled
  notes            TEXT,
  asset_ref        TEXT,                  -- SEAM (later phase) — see the block comment above
  asset_source     TEXT,                  -- SEAM (later phase)
  sort_order       INT NOT NULL DEFAULT 100,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by       TEXT,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by       TEXT
);
CREATE INDEX IF NOT EXISTS marketing_event_link_by_event
  ON core.marketing_event_link (org_id, event_id, sort_order);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 10. GIVEAWAYS / inventory taken — out vs back, so shrinkage is visible
-- ══════════════════════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS core.marketing_event_giveaway (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL,
  event_id          UUID NOT NULL REFERENCES core.marketing_event(id) ON DELETE CASCADE,
  giveaway_type_key TEXT,                 -- vocabulary = list_key 'giveaway_type'
  item_label        TEXT NOT NULL,
  qty_out           NUMERIC,              -- taken to the event
  qty_returned      NUMERIC,              -- brought back. out - returned - given = unaccounted
  qty_given         NUMERIC,              -- handed to customers, when anyone counted
  -- INFORMATIONAL money (see the vendor.cost note). Never seeded, never read by a money path.
  unit_cost         NUMERIC,
  notes             TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by        TEXT,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by        TEXT
);
CREATE INDEX IF NOT EXISTS marketing_event_giveaway_by_event
  ON core.marketing_event_giveaway (org_id, event_id);

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 11. DOCUMENTS — storeops.store_document EXTENDED, never forked (the mig 964 precedent)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- A second documents table would be a duplicate data path (CLAUDE.md build gate). store_document
-- gains a nullable event_id and three event doc kinds:
--   store_code NOT NULL + event_id NULL      -> every existing per-store row, every existing reader,
--                                               byte-for-byte unchanged.
--   store_code NULL     + event_id NOT NULL  -> an event document.
-- Existing readers all filter .eq("store_code", <code>), so an event document can never surface in
-- a store's document list. The append-only contract (upload INSERTs a new version; current = newest
-- uploaded_at), the private `store-docs` bucket and the signed-URL-by-id download path are shared
-- unchanged — this migration adds no new way to reach a file.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'storeops' AND table_name = 'store_document') THEN

    EXECUTE 'ALTER TABLE storeops.store_document ADD COLUMN IF NOT EXISTS event_id UUID';

    -- store_code was made nullable by mig 964 (policy documents); keep it nullable, and guard again
    -- in case only this migration ran.
    BEGIN
      EXECUTE 'ALTER TABLE storeops.store_document ALTER COLUMN store_code DROP NOT NULL';
    EXCEPTION WHEN others THEN NULL;
    END;

    -- Widen the doc_kind vocabulary. Both the mig-946 CHECK and the mig-964 replacement are dropped
    -- by name first so this is re-runnable whichever one is present.
    BEGIN EXECUTE 'ALTER TABLE storeops.store_document DROP CONSTRAINT IF EXISTS store_document_doc_kind_check'; EXCEPTION WHEN others THEN NULL; END;
    BEGIN EXECUTE 'ALTER TABLE storeops.store_document DROP CONSTRAINT IF EXISTS store_document_doc_kind_ck';    EXCEPTION WHEN others THEN NULL; END;
    BEGIN
      EXECUTE $ck$ALTER TABLE storeops.store_document ADD CONSTRAINT store_document_doc_kind_ck
        CHECK (doc_kind IN ('lease','insurance_coi','insurance_policy',
                            'event_vendor_contract','event_photo','event_permit'))$ck$;
    EXCEPTION WHEN others THEN NULL;
    END;

    -- An owner is required: a store, a policy, or an event — never a floating document.
    BEGIN EXECUTE 'ALTER TABLE storeops.store_document DROP CONSTRAINT IF EXISTS store_document_owner_ck'; EXCEPTION WHEN others THEN NULL; END;
    BEGIN
      IF EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_schema='storeops' AND table_name='store_document' AND column_name='policy_id') THEN
        EXECUTE $ck2$ALTER TABLE storeops.store_document ADD CONSTRAINT store_document_owner_ck
          CHECK (store_code IS NOT NULL OR policy_id IS NOT NULL OR event_id IS NOT NULL)$ck2$;
      ELSE
        EXECUTE $ck3$ALTER TABLE storeops.store_document ADD CONSTRAINT store_document_owner_ck
          CHECK (store_code IS NOT NULL OR event_id IS NOT NULL)$ck3$;
      END IF;
    EXCEPTION WHEN others THEN NULL;
    END;

    EXECUTE 'CREATE INDEX IF NOT EXISTS store_document_event '
            'ON storeops.store_document (org_id, event_id, uploaded_at DESC)';
  END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 12. DOCUMENTATION ON THE OBJECTS THEMSELVES
-- ══════════════════════════════════════════════════════════════════════════════════════════════
COMMENT ON TABLE core.marketing_option IS
  'RULE TWO option registry for the marketing module (mig 986, owner 2026-09-06: "none of the '
  'options I mentioned above are hard coded but options pre added with plus sign to add more as '
  'per user discretion"). One row per selectable value, keyed by list_key. Resolution is HOUSE org '
  'rows UNION this org''s rows with the tenant winning per (list_key, key) — so a tenant starts '
  'with a working vocabulary and may rename, deactivate or extend any of it. No CHECK constraint '
  'and no code enum guards these values: adding a theme is an INSERT, never a deploy.';
COMMENT ON COLUMN core.marketing_option.extra IS
  'Per-option DATA, never behaviour. goal_metric rows carry {"unit":"count"|"money","derivable":'
  'true|false,"field":"<sales_cell_agg field>"}: derivable=false means the platform reports "no '
  'automatic actual" for a tenant-invented metric rather than showing a misleading zero.';
COMMENT ON TABLE core.marketing_config IS
  'Per-org marketing switches (mig 986). approval_required DEFAULTS TO FALSE by owner directive — '
  'the approval workflow exists but is never forced on. approval_spend_threshold only narrows an '
  'already-enabled approval requirement and can never enable it. checkin_geo_retention_days is the '
  'EXPLICIT retention for employee GPS rows and is stamped onto each row as purge_after_date.';
COMMENT ON TABLE core.marketing_event IS
  'One outside-store marketing event (mig 986, owner 2026-09-06). Goals are typed here; ACTUALS ARE '
  'NEVER STORED — they are derived on read from commcalc router._sales_cell_agg via '
  '_compute_feed_actuals_py (app/modules/marketing/actuals.py), the SAME shared pass behind the '
  'Sales Report, Executive MTD and Daily Targets. Sales in the event window are reported as STORE '
  'PERFORMANCE OVER THE WINDOW VERSUS A BASELINE, explicitly not as sales caused by the event.';
COMMENT ON COLUMN core.marketing_event.staff_call_at IS
  'What time the employees have to BE THERE — a first-class column, deliberately distinct from '
  'event_start (owner asked the two as separate questions). Per-person overrides live on '
  'core.marketing_event_staff.call_time_override.';
COMMENT ON COLUMN core.marketing_event.approval_state IS
  'not_required | pending | approved | rejected. ''not_required'' is written explicitly (with '
  'approval_reason) whenever the org switch is off or the event is under the spend threshold, so '
  'an event that went live without a signature records WHY instead of leaving an ambiguous NULL.';
COMMENT ON TABLE core.marketing_event_staff IS
  'Planned people at an event. is_backup + backup_for_staff_id express "back up employee if they '
  'don''t show up"; transport_mode_key + pickup_by_staff_id express "how are employees getting '
  'there / who is picking up who". Both graphs are DATA: no role, transport mode or driver is named '
  'in code. confirm_state ''no_show'' is set by a human after the fact — the platform never infers '
  'that someone failed to turn up.';
COMMENT ON TABLE core.marketing_event_checkin IS
  'SENSITIVE PERSONAL DATA. ONE point-in-time GPS fix per person per event, captured at the instant '
  'that person presses check-in — the SAME capture contract storevisit (mig 027) has used since day '
  'one (check_in_lat / check_in_lng / check_in_accuracy), not a second one. There is deliberately '
  'no shape here that could hold a track: no repeated-position table, no interval, and check-OUT '
  'stores a timestamp and no second coordinate. The inside/outside judgement comes from the one '
  'shared pure decision app/modules/core/geo.evaluate_checkin and is stored as evidence. Retention '
  'is explicit (purge_after_date, from marketing_config.checkin_geo_retention_days) and a rep can '
  'read back every row recorded about them via GET /marketing/my-checkins.';
COMMENT ON COLUMN core.marketing_event_checkin.checked_out_at IS
  'Timestamp only, by design. Knowing when someone left does not require knowing where they were.';
COMMENT ON COLUMN core.marketing_event_vendor.cost IS
  'INFORMATIONAL. No P&L, statement, payable or payout reader consumes this column; an event cost '
  'becomes a booked number only when a human enters it in the finance module.';
COMMENT ON TABLE core.marketing_event_link IS
  'Planned social / marketing creative links per channel (mig 986). PHASE-1 SEAM: asset_ref + '
  'asset_source are reserved, unread and always NULL in this phase — the later creative-gallery and '
  'marketing-portal-pull phases attach there instead of adding a competing link table.';
COMMENT ON TABLE core.marketing_event_giveaway IS
  'Giveaways / inventory taken to an event. qty_out minus qty_returned minus qty_given is what makes '
  'shrinkage visible; unit_cost is informational and read by no money path.';
COMMENT ON COLUMN storeops.store_document.event_id IS
  'Set (with store_code NULL) for a marketing-event document — doc_kind event_vendor_contract | '
  'event_photo | event_permit (mig 986). NULL for per-store lease/COI rows and for policy rows. '
  'Existing per-store readers filter on store_code, so event documents never appear in a store''s '
  'document list; the private store-docs bucket and signed-URL-by-id path are shared unchanged.';

-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- 13. SECURITY — RLS on, zero policies, zero anon/authenticated grants (AGENT_CONTRACT §5)
-- ══════════════════════════════════════════════════════════════════════════════════════════════
-- The backend service role bypasses RLS and needs no policy; the frontend anon key is auth-only and
-- must never reach these tables. Every read the API makes is org-scoped in application code
-- (harness_marketing_event.py §J is the static guard that proves it).
DO $$
DECLARE t RECORD;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables
           WHERE schemaname = 'core' AND tablename LIKE 'marketing\_%' LOOP
    EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    BEGIN EXECUTE format('REVOKE ALL ON core.%I FROM anon, authenticated', t.tablename); EXCEPTION WHEN others THEN NULL; END;
    BEGIN EXECUTE format('GRANT ALL ON core.%I TO service_role', t.tablename); EXCEPTION WHEN others THEN NULL; END;
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
SELECT 'Migration 986 complete — marketing module phase 1: core.marketing_option (RULE TWO registry), marketing_config (approval DEFAULT OFF), marketing_event (+ store/goal/staff/checkin/vendor/checklist/link/giveaway), checklist templates, storeops.store_document extended with event_id' AS status;

COMMIT;

-- REVERT:
--   -- documents first (the CHECK and the column reference events)
--   ALTER TABLE storeops.store_document DROP CONSTRAINT IF EXISTS store_document_owner_ck;
--   ALTER TABLE storeops.store_document DROP CONSTRAINT IF EXISTS store_document_doc_kind_ck;
--   DELETE FROM storeops.store_document WHERE event_id IS NOT NULL;   -- else the CHECK below fails
--   ALTER TABLE storeops.store_document ADD CONSTRAINT store_document_doc_kind_ck
--     CHECK (doc_kind IN ('lease','insurance_coi','insurance_policy'));
--   ALTER TABLE storeops.store_document ADD CONSTRAINT store_document_owner_ck
--     CHECK (store_code IS NOT NULL OR policy_id IS NOT NULL);        -- mig 964's version
--   DROP INDEX IF EXISTS storeops.store_document_event;
--   ALTER TABLE storeops.store_document DROP COLUMN IF EXISTS event_id;
--   -- then the module (children before parents; the FKs are ON DELETE CASCADE so order is a courtesy)
--   DROP TABLE IF EXISTS core.marketing_event_giveaway;
--   DROP TABLE IF EXISTS core.marketing_event_link;
--   DROP TABLE IF EXISTS core.marketing_event_checklist_item;
--   DROP TABLE IF EXISTS core.marketing_checklist_template_item;
--   DROP TABLE IF EXISTS core.marketing_checklist_template;
--   DROP TABLE IF EXISTS core.marketing_event_vendor;
--   DROP TABLE IF EXISTS core.marketing_event_checkin;
--   DROP TABLE IF EXISTS core.marketing_event_staff;
--   DROP TABLE IF EXISTS core.marketing_event_goal;
--   DROP TABLE IF EXISTS core.marketing_event_store;
--   DROP TABLE IF EXISTS core.marketing_event;
--   DROP TABLE IF EXISTS core.marketing_config;
--   DROP TABLE IF EXISTS core.marketing_option;
--   -- and mig 987's registry row:  DELETE FROM core.module_catalog WHERE key = 'marketing';
--   (Uploaded event files in the private store-docs bucket survive a table drop — manual cleanup.)
