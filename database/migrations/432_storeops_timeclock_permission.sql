-- 432_storeops_timeclock_permission.sql   (people band 400-499; storeops schema)
--
-- REP-INITIATED TIME-CLOCK PERMISSIONS — the DM-approval half of the auto-clock-out feature.
--
-- OWNER-APPROVED DESIGN (2026-08-14):
--   1. Auto clock-out with a 5-minute grace. If a rep never clocks out, the force-clockout sweep
--      closes the open punch once now >= scheduled_shift_end + 5 min, and STAMPS the clock-out at
--      scheduled_end + 5 min (owner's choice — NOT at the bare scheduled end). The row is flagged
--      auto_clocked_out so the reason for the stamped-away hours is visible, not silent.
--   2. Late clock-out (worked past shift+5): the rep is capped/auto-clocked-out at end+5; to get the
--      time BEYOND that counted they must request DM approval. That raises a 'late_clockout' pending
--      permission — the base (scheduled) time counts immediately, the EXTRA counts only once approved.
--   3. Re-clock-in after an auto-clock-out earlier the same day (a genuine SECOND session): allowed,
--      but the new punch is held 'pending your DM's permission' and does NOT count toward payroll
--      until the DM approves. A normal lunch-break second session (the prior close was a manual
--      clock-out, not an auto one) is unaffected — it never needs permission.
--
-- WHY A NEW TABLE (not an extension of storeops.shift_extension):
--   shift_extension is a MANAGER-filed, AHEAD-OF-TIME request whose approval mutates where the
--   force-clockout sweep stamps (via _approved_extension_end, which pushes the stamp LATER). These
--   new requests are REP-initiated and AFTER-THE-FACT; overloading shift_extension's requested_end /
--   status onto them would make a rep's post-hoc approval retroactively move the sweep's stamp target
--   and risk double-counting. Keeping them in their own table isolates the payroll reasoning and the
--   audit trail. It reuses the SAME _dm_for_store() resolver and the SAME approve/deny shape.
--
-- MONEY POSTURE. Nothing here computes a payout. Unapproved time is kept OUT of payroll by the punch's
-- own `hours` column staying NULL for a pending/denied second session (every payroll reader —
-- mig-407 RPC and the legacy Python path — already require `hours IS NOT NULL`), and by the
-- late-clockout base punch carrying only the scheduled-through-grace hours until the DM approves the
-- extra. Approval is what materializes the extra hours onto the punch.
--
-- Additive + idempotent: ALTERs are ADD COLUMN IF NOT EXISTS on an existing table, the new table is
-- CREATE IF NOT EXISTS, nothing is seeded, no existing column is dropped or retyped.

-- ── 1. Flags on the punch itself ────────────────────────────────────────────────────────────────
-- auto_clocked_out  : true when the sweep / a stale-close / a late-clockout cap stamped this punch's
--                     clock_out at scheduled_end + grace instead of a real manual punch.
-- permission_status : NULL  = an ordinary punch, counts normally (byte-identical to pre-migration).
--                     'pending'  = a second session awaiting the DM — hours held NULL, never paid yet.
--                     'approved' = the DM allowed it; hours are stamped and it counts.
--                     'denied'   = the DM refused; hours stay NULL, it never counts.
-- permission_id     : link to the storeops.timeclock_permission row that governs this punch.
alter table storeops.timelog add column if not exists auto_clocked_out boolean not null default false;
alter table storeops.timelog add column if not exists permission_status text;
alter table storeops.timelog add column if not exists permission_id uuid;

-- ── 2. The permission request ───────────────────────────────────────────────────────────────────
create table if not exists storeops.timeclock_permission (
  id                 uuid primary key default gen_random_uuid(),
  org_id             uuid not null,
  employee_id        text not null,
  employee_name      text,
  store_code         text,
  work_date          date,
  timelog_id         uuid,               -- the punch this request concerns (storeops.timelog.id)

  -- 'reclock_in'    : a second session after an auto-clock-out — the WHOLE session is pending.
  -- 'late_clockout' : extra time worked past scheduled_end + grace — only the EXTRA is pending.
  kind               text not null check (kind in ('reclock_in', 'late_clockout')),
  status             text not null default 'pending' check (status in ('pending', 'approved', 'denied')),

  -- late_clockout bookkeeping (null for reclock_in):
  anchor_at          timestamptz,        -- the auto-clockout stamp = scheduled_end + grace
  requested_clock_out timestamptz,       -- when the rep actually left / wants counted to
  extra_minutes      integer,            -- pending minutes between anchor_at and requested_clock_out

  reason             text,
  requested_by       text,
  requested_at       timestamptz default now(),

  dm_employee_id     text,               -- resolved District Manager (may be NULL — any admin can act)
  dm_email           text,
  decided_by         text,
  decided_by_name    text,
  decided_at         timestamptz,
  decision_note      text,

  created_at         timestamptz default now()
);
create index if not exists timeclock_permission_org_status
  on storeops.timeclock_permission (org_id, status);
create index if not exists timeclock_permission_emp_date
  on storeops.timeclock_permission (org_id, employee_id, work_date);
create index if not exists timeclock_permission_timelog
  on storeops.timeclock_permission (timelog_id);

alter table storeops.timeclock_permission enable row level security;
drop policy if exists open_all on storeops.timeclock_permission;
create policy open_all on storeops.timeclock_permission for all to anon, authenticated using (true) with check (true);
grant all on storeops.timeclock_permission to anon, authenticated, service_role;

notify pgrst, 'reload schema';

select 'Migration 432 complete — storeops.timeclock_permission + timelog.{auto_clocked_out,permission_status,permission_id}' as status;

-- REVERT (paste and run to undo — touches no payroll number):
-- drop table if exists storeops.timeclock_permission;
-- alter table storeops.timelog drop column if exists permission_id;
-- alter table storeops.timelog drop column if exists permission_status;
-- alter table storeops.timelog drop column if exists auto_clocked_out;
