-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- 1135 — THE DAY A STORE STARTED CLOCKING IN, per store, accepted by a human.
--
-- ★ SURFACED FOR OWNER APPROVAL. NOT APPLIED FROM A SESSION. ★
-- CLAUDE.md: "money-touching changes and migrations are still surfaced for owner approval before
-- applying". This table is the SWITCH for a change in who gets paid what out of a fixed incentive
-- pool, so it is the most money-touching DDL in the repo's recent history. Apply it deliberately.
--
-- Owner Sanjot, 2026-11-27 14:35 ET, verbatim:
--   "let the daily cincentive as itonly affects ny logistics bee ffeceive going forward based on eh
--    acual clock in, if te hours are udpated manuay then we cannot oay for thsose hours"
--
-- WHAT IT IS FOR. `commcalc/router._incentive_hours_for_day` weighted an hours-split
-- `threshold_pool` by `actual_hours` else `scheduled_hours`. `storeops.shifts.actual_hours` carries
-- a figure on 7 of 7,740 live rows (index §14y), so the pool was in practice split by THE SCHEDULE:
-- a rep rostered 9h who punched 4h drew a 9/Σ share. Moving to closed punches changes WHO IS PAID
-- WHAT out of a pool whose total never moves, so it may only apply going forward.
--
-- ★ THIS TABLE IS THE SWITCH, AND AN EMPTY TABLE MEANS NOTHING CHANGES ★
-- `commcalc/incentive_clock_floor.status` reads NO ROW as `no_floor_accepted`, which keeps today's
-- weighting exactly. So APPLYING THIS MIGRATION MOVES NO MONEY BY ITSELF — the behaviour changes
-- only when a row is accepted for a store. That is deliberate: the DDL and the money decision are
-- separated, and the second one is per store.
--
-- ★ WHY A STORED DATE RATHER THAN A DERIVED ONE ★ — the date is DERIVED from each store's own punch
-- history (`incentive_clock_floor.derive` = the first CLOSED punch per store), but the money reads
-- the STORED value and never recomputes. A punch backfilled into an earlier day would move the
-- derivation, and a derivation read at money time would therefore walk the floor BACKWARDS and
-- restate a period already paid. Derived proposes; a human accepts; the row is what pays.
--
-- ★ WHY PER STORE ★ — stores began clocking at different times and will continue to; a tenant
-- rolling the clock out store by store is the normal case. An org-wide date would either hold back
-- the stores that are ready or restate the ones that are not.
--
-- ★ WHY NOT A COLUMN ON `storeops.payroll_hours_basis_config` ★ — that table is the per-ORG home
-- for the PAY basis knobs (§14y/§14z: one row, one feature). This is a per-STORE boundary for an
-- INCENTIVE. Sharing the pay row would also hand this rule the date an org chose for PAYROLL, which
-- is the exact mistake `corrections_effective_from` already has its own column to avoid.
--
-- ★ NO HOUSE DEFAULT ROW IS SEEDED, FOR ANY ORG ★ — "the day they started clocking in" is a fact in
-- each tenant's own history, never a policy with a house default behind it. A tenant with no closed
-- punch has NO floor and is reported unmeasurable; it is never handed another tenant's date
-- (§COMMCALC-49.10, RULE TWO). This is the one place a house default would be a per-tenant fact
-- masquerading as a policy.
--
-- Idempotent, additive, no backfill, no UPDATE of any existing row.
--
-- REVERT: drop table if exists commcalc.incentive_clock_floor;
--         (Dropping it restores today's schedule-weighted split for every store, because
--          `incentive_clock_floor.load` reports an unreadable table as "no floor accepted" and the
--          reader then keeps today's behaviour. Nothing else reads this table.)
-- ════════════════════════════════════════════════════════════════════════════════════════════════

create table if not exists commcalc.incentive_clock_floor (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,
  store_code     text not null,
  -- The accepted boundary. On and AFTER this business date, this store's hours-split incentive
  -- pools are weighted by closed clock punches. INCLUSIVE — the owner said "going forward" from the
  -- day they started clocking in, so that day itself counts.
  effective_from date not null,
  -- What the derivation PROPOSED when this row was accepted (the first closed punch found for the
  -- store). Kept beside the accepted value so a later audit can see whether a human moved it, and
  -- so a backfilled punch changing the derivation is VISIBLE as a difference rather than silently
  -- altering what pays.
  derived_from   date,
  -- WHO accepted it. A money boundary with no name on it is an assertion nobody made.
  accepted_by    uuid,
  accepted_at    timestamptz not null default now(),
  note           text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  -- One accepted floor per store. A second row would be two answers to one question, and the
  -- reader (`load`) would pick whichever the database happened to return first.
  unique (org_id, store_code)
);

create index if not exists incentive_clock_floor_org
  on commcalc.incentive_clock_floor(org_id);

-- A migration grants what it creates (house convention, enforced by
-- `harness_data_api_grant_guard.py`, index §CORE-13). `service_role` ONLY: the browser never reads
-- this table, so an `anon` grant would widen exposure rather than fix anything.
grant select, insert, update, delete on commcalc.incentive_clock_floor to service_role;

comment on table commcalc.incentive_clock_floor is
  'THE DAY A STORE STARTED CLOCKING IN, accepted by a human, for incentive weighting only (owner
   ruling 2026-11-27). On and after `effective_from` an hours-split incentive pool at this store is
   weighted by CLOSED clock punches; before it, and for any store with NO row here, the pool is
   weighted exactly as it is today. AN EMPTY TABLE THEREFORE CHANGES NOTHING. Derived by
   `commcalc/incentive_clock_floor.derive` (first closed punch per store) but READ as stored and
   never recomputed, so a punch backfilled into an earlier day cannot walk the boundary backwards
   and restate a period already paid. This is NOT a pay control: payroll''s basis and its
   hand-correction fold are `storeops.payroll_hours_basis_config` (§14y, PR #679), and a corrected
   day is PAID while carrying no incentive weight.';

comment on column commcalc.incentive_clock_floor.derived_from is
  'What the derivation proposed when this row was accepted. A difference from `effective_from` means
   a human chose a different date, or a punch has since been backfilled into an earlier day — both
   are things an audit should be able to see rather than infer.';
