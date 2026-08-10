-- 431_storeops_payroll_approval.sql   (people band 400-499; storeops schema)
--
-- TWO-STAGE PAYROLL HOURS APPROVAL + DISBURSEMENT ROUTING.
--
-- OWNER DIRECTIVE 2026-08-10, verbatim: "dm needs to approve the hours for the employees who have
-- worked and then the hr approves it to send to accounting or the related parties to pay it."
--
-- Three things were asked for:
--   1. Monday morning the DM is notified to check LAST week's hours and tick them approved.
--   2. HR approves after the DM, then sends to the parties who actually pay.
--   3. Per-employee choice of WHO pays: accounting (default, email from config), the stores' DM, or a
--      third party who disburses cash / issues a cheque.
--
-- OWNER RULINGS taken before building (they change the shape, so they are recorded here):
--   • The DM may CORRECT hours inline and then approve. Every correction writes the old value, the
--     new value, who and a REQUIRED reason to storeops.payroll_change_log — the existing audit trail
--     for exactly this kind of edit. There is no silent path to a different number.
--   • An employee missing either approval is WARNED LOUDLY and excluded from dispatch by default, but
--     an admin may override with a recorded reason (`override_*` below) so nobody misses a paycheque
--     because a DM was on holiday. Approval is a gate with a documented key, not a wall.
--   • The payer is a STORE-level default with a per-employee override, so the weekly run is only ever
--     about the exceptions.
--
-- MONEY POSTURE. This migration creates NO payout number and alters none. `hours_source` is a SNAPSHOT
-- of what the engine already computed and `hours_approved` is the reviewed value; payroll itself still
-- reads shifts/timelog/pay_rate exactly as it does today. Nothing here is wired into a payout until the
-- dispatch step, which only ever EMAILS a statement to a human who pays.
--
-- Additive + idempotent: every object is IF NOT EXISTS, nothing is seeded (an org with no payers
-- configured simply has no routing yet, and the UI says so), and no existing table is altered.

-- ── 1. WHO CAN PAY (RULE TWO: config, never hard-coded; RULE THREE: the UI picks from this list) ───
create table if not exists storeops.payroll_payer (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null,
  name             text not null,
  kind             text not null check (kind in ('accounting', 'dm', 'third_party')),
  email            text,
  phone            text,
  -- kind='dm' with a NULL employee_id means "resolve the DM of each row's own store at send time"
  -- (the common case). Pinning an employee_id here routes every store to that one person instead.
  dm_employee_id   text,
  note             text,
  is_active        boolean not null default true,
  is_default       boolean not null default false,
  created_at       timestamptz not null default now(),
  created_by       text
);

create unique index if not exists payroll_payer_org_name_idx
  on storeops.payroll_payer (org_id, lower(name));

-- At most ONE default payer per org. The default is what pre-fills every unrouted employee, so two of
-- them would make "the accounting email as defined by the user" ambiguous.
create unique index if not exists payroll_payer_one_default_idx
  on storeops.payroll_payer (org_id) where is_default;

comment on table storeops.payroll_payer is
  'Who disburses payroll: accounting, a district manager, or a third party who pays cash / issues '
  'cheques. Per-org config (owner directive 2026-08-10). Unseeded on purpose — an org configures its '
  'own; the default row pre-fills every employee not routed elsewhere.';

-- ── 2. STORE DEFAULT ROUTING ──────────────────────────────────────────────────────────────────────
create table if not exists storeops.payroll_store_payer (
  org_id      uuid not null,
  store_code  text not null,
  payer_id    uuid not null references storeops.payroll_payer (id) on delete cascade,
  updated_at  timestamptz not null default now(),
  updated_by  text,
  primary key (org_id, store_code)
);

comment on table storeops.payroll_store_payer is
  'Default payer per store. An employee inherits their store''s payer; payroll_approval.payer_id '
  'overrides it for one person for one period.';

-- ── 3. THE APPROVAL ROW ───────────────────────────────────────────────────────────────────────────
create table if not exists storeops.payroll_approval (
  id                uuid primary key default gen_random_uuid(),
  org_id            uuid not null,
  period_start      date not null,
  period_end        date not null,
  employee_id       text not null,
  store_code        text,

  -- hours_source = what the payroll engine computed when the row was first raised (the snapshot the
  -- DM was actually looking at). hours_approved = the value the DM signed off; NULL means "no
  -- correction, take hours_source". Keeping both is what makes a correction auditable after the fact.
  hours_source      numeric(10, 2),
  hours_approved    numeric(10, 2),

  dm_status         text not null default 'pending' check (dm_status in ('pending', 'approved', 'sent_back')),
  dm_by             text,
  dm_at             timestamptz,
  dm_note           text,

  hr_status         text not null default 'pending' check (hr_status in ('pending', 'approved', 'sent_back')),
  hr_by             text,
  hr_at             timestamptz,
  hr_note           text,

  -- Effective payer for THIS employee THIS period. NULL = inherit the store default, then the org
  -- default. Resolved at read time so a config change is picked up without rewriting history.
  payer_id          uuid references storeops.payroll_payer (id) on delete set null,

  -- The "warn loudly but allow" escape hatch. Set ONLY by an admin, reason REQUIRED by the endpoint.
  override_by       text,
  override_at       timestamptz,
  override_reason   text,

  dispatch_status   text not null default 'none' check (dispatch_status in ('none', 'sent', 'failed')),
  dispatched_at     timestamptz,
  dispatch_to       text,
  dispatch_error    text,

  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- One row per person per period. The read endpoint upserts on this key, so re-opening the week never
-- duplicates a review or loses a decision already made.
create unique index if not exists payroll_approval_period_emp_idx
  on storeops.payroll_approval (org_id, period_start, period_end, employee_id);

create index if not exists payroll_approval_period_idx
  on storeops.payroll_approval (org_id, period_start, period_end);
create index if not exists payroll_approval_store_idx
  on storeops.payroll_approval (org_id, store_code);

comment on table storeops.payroll_approval is
  'Two-stage weekly hours approval (DM then HR) plus the disbursement route for one employee for one '
  'pay period. Owner directive 2026-08-10. hours_source is a snapshot, hours_approved is the reviewed '
  'value; corrections are additionally written to storeops.payroll_change_log.';

notify pgrst, 'reload schema';

select 'Migration 431 complete — payroll_payer + payroll_store_payer + payroll_approval installed (all empty)' as status;

-- REVERT (paste and run to undo — drops the workflow, touches no payroll number):
-- drop table if exists storeops.payroll_approval;
-- drop table if exists storeops.payroll_store_payer;
-- drop table if exists storeops.payroll_payer;
