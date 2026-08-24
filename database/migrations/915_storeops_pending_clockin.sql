-- 915_storeops_pending_clockin.sql   (storeops schema; timeclock band, follows 912/914)
--
-- BLOCK-AND-HOLD unscheduled clock-in — the capture half of "manager schedules the rep, then the rep
-- is AUTO clocked in" (owner-approved, 2026-08-24).
--
-- OWNER-APPROVED DESIGN (locked):
--   1. FULLY BLOCKED. A rep who is NOT scheduled at the store they tap at CANNOT accrue time. There is
--      NO unpaid-override path. Their tap opens NO storeops.timelog punch and inserts NO zero-hour
--      shift shell — it is captured HERE, as a PENDING request, accruing zero time until a manager
--      schedules them.
--   2. AUTO CLOCK-IN. When the manager saves a schedule covering this (employee, store, work_date), the
--      held pending row is ACTIVATED: an OPEN storeops.timelog punch is created back-dated to the
--      ORIGINAL tap time (requested_at). The rep does not tap again.
--
-- WHY A NEW TABLE (not storeops.timelog, not storeops.timeclock_permission):
--   * NOT timelog — decision (1) forbids any punch until a manager approves; a held request is not a
--     punch and must never reach a payroll reader (all of which read storeops.timelog). Parking it in
--     timelog (even permission_status='pending') would collide with the mig-912 one-open unique index
--     and risk being read as worked time. It stays OUT of timelog by construction.
--   * NOT timeclock_permission (mig 432) — that is a REP-initiated, AFTER-THE-FACT request ABOUT an
--     existing punch (a second session / extra minutes past a scheduled end). This is a BEFORE-THE-FACT
--     request that NO punch exists for yet, resolved by a MANAGER creating a schedule. Different life
--     cycle, different resolver, different audit trail — its own table keeps the reasoning isolated.
--
-- MONEY POSTURE. Nothing here is payable. A pending row is zero hours. Only ACTIVATION materialises an
-- open timelog punch (at the original tap time); the ordinary clock-out / auto-clock-out path then
-- stamps its hours exactly as for any other punch. A denied/expired row never becomes a punch.
--
-- Additive + idempotent, every statement single-line-safe for the tenant SQL runner. Nothing seeded,
-- no existing column dropped or retyped, no hours math touched.

-- ── The pending clock-in request ────────────────────────────────────────────────────────────────
create table if not exists storeops.pending_clockin (
  id                  uuid primary key default gen_random_uuid(),
  org_id              uuid not null,
  employee_id         text not null,
  employee_name       text,
  store_code          text,               -- the store the rep TAPPED at (normalized UPPER by the app)
  requested_at        timestamptz not null,   -- the ORIGINAL tap time (UTC); activation back-dates to this
  work_date           date,               -- store-LOCAL calendar day of the tap (mig 851 zone)

  -- pending   : held, waiting for a manager to schedule the rep (accrues zero time).
  -- activated : a schedule now covers it; the open timelog punch was created at requested_at.
  -- denied    : a manager declined (never becomes a punch).
  -- expired   : aged out by a future sweep (never becomes a punch). Reserved; no sweep writes it yet.
  status              text not null default 'pending'
                        check (status in ('pending', 'approved', 'activated', 'denied', 'expired')),

  client_request_id   text,               -- the kiosk's stable punch id (mig 912) — reused for dedupe
  shift_id            bigint,             -- the schedule row that activated this (storeops.shifts.id)
  timelog_id          uuid,               -- the OPEN punch activation created (storeops.timelog.id)
  approval_request_id text,               -- the unified-approvals row (mig 867) notifying the manager

  reason              text,
  requested_by        text,
  decided_by          text,
  decided_at          timestamptz,

  created_at          timestamptz default now(),
  activated_at        timestamptz
);

-- Idempotency key: a retap carrying the SAME stable client_request_id (mig 912) never stacks a 2nd row.
create unique index if not exists pending_clockin_client_req_idx
  on storeops.pending_clockin (org_id, employee_id, client_request_id) where client_request_id is not null;

-- ONE-OPEN-PENDING guard: at most one PENDING request per (org, employee, store, day), so repeated taps
-- with no client_request_id also collapse to one held request instead of a growing pile.
create unique index if not exists pending_clockin_one_open_idx
  on storeops.pending_clockin (org_id, employee_id, store_code, work_date) where status = 'pending';

create index if not exists pending_clockin_emp_status
  on storeops.pending_clockin (org_id, employee_id, status);
create index if not exists pending_clockin_lookup
  on storeops.pending_clockin (org_id, store_code, work_date, status);

alter table storeops.pending_clockin enable row level security;
drop policy if exists open_all on storeops.pending_clockin;
create policy open_all on storeops.pending_clockin for all to anon, authenticated using (true) with check (true);
grant all on storeops.pending_clockin to anon, authenticated, service_role;

notify pgrst, 'reload schema';

select 'Migration 915 complete — storeops.pending_clockin (block-and-hold unscheduled clock-in)' as status;

-- REVERT (paste and run to undo — touches no payroll number, opens no punch):
-- drop table if exists storeops.pending_clockin;
