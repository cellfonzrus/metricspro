-- 1070_payroll_clocked_hours_basis.sql — PAYROLL FROM THE CLOCK, NOT THE SCHEDULE (owner ask 2026-10-10)
--
-- ── WHY ────────────────────────────────────────────────────────────────────────────────────────
-- Owner, verbatim: "for the payroll menu need to turn off the payroll automatically being calculated
-- as per schedule, it is only based on actual hours clocked in unless a button is checked for each
-- employee separately against that employee name." And: "need to import the clock in clock out report
-- from b2b and match with what we have in metrics pro and asses the difference and ask the DM on every
-- monday and require the correction to be done before the payroll is run … block them from logging in
-- unless they fix it by tuesday so the payroll can be run on Wednesday. this will be picked from the
-- emails for each store like other reports."
--
-- THE CLASS, NOT THE INSTANCE. The instance is "payroll is calculated as per schedule". The class is
-- that paid hours were decided by `actual_hours or scheduled_hours` in six places at once, over a
-- column effectively nothing writes — so the `or` fell through to the schedule on ~100% of rows and no
-- reader was ever told. MEASURED on production, read-only, 2026-10-10:
--   · storeops.shifts        7,740 rows, SEVEN with actual_hours > 0 (all four April days, and in all
--                            seven actual_hours == scheduled_hours == 9 — copies, not measurements).
--   · storeops.timelog       3,663 rows, 3,555 with hours > 0, live today. 40 open, 290 auto-closed.
--   · storeops.manual_hours  569 rows (37 house, 532 Luxelink) — and GET /storeops/payroll has never
--                            read them, so the DM's corrections never reached pay either.
-- The punches were never missing. The measurement never reached the column payroll reads.
--
-- WHAT SHIPS HERE (all additive; nothing is backfilled; no existing row's meaning changes):
--   1. storeops.employees.pay_from_schedule           — the owner's per-employee tick box.
--   2. storeops.payroll_hours_basis_config            — the per-org knobs (RULE TWO: no tenant,
--                                                        employee or weekday literal in code).
--   3. storeops.raw_clock_punch                       — the B2B clock report as a CLAIM about the day.
--   4. storeops.clock_variance_correction             — what a human decided about one variance.
--   5. storeops.clock_feed_login_link                 — which B2B login is which employee, APPROVED.
--
-- ★ NOTHING CHANGES ON APPLY ★ — and STEP 7 PROVES IT with a row count, not a promise.
--   `pay_from_schedule` is seeded TRUE for every existing employee and `hours_basis_mode` is seeded
--   'schedule_fallback' for every existing org, which is exactly today's behaviour. `clock_feed_enabled`,
--   `variance_ask_enabled`, `login_gate_enabled` and `payroll_run_gate_enabled` are all FALSE: no
--   mailbox rule is seeded, no cron job is registered, no gate can fire. Turning it on is a separate,
--   deliberate act — see the README beside the owner-facing SQL.
--
-- WHY THE DEFAULT MATTERS, in numbers. Measured 2026-10-10, flipping to clocked-only right now would
-- move paid hours like this (today's engine vs a clocked-only basis over the same rows):
--     tenant    month    today's paid h   clocked-only h    delta
--     Cellfonz  2026-06         4,830.0              0.0  -100.0%   (no punches exist before July)
--     Cellfonz  2026-07         6,274.7          5,066.5   -19.3%
--     Cellfonz  2026-08         5,244.6          3,212.8   -38.7%
--     Cellfonz  2026-09         5,523.0          3,500.1   -36.6%
--     Luxelink  2026-07         8,265.7          4,218.8   -49.0%
--     Luxelink  2026-08         7,138.8          4,768.5   -33.2%
--     Luxelink  2026-09         5,907.1          4,561.8   -22.8%
--   PARTIAL coverage is the COMMON case (per-day punch coverage of scheduled days: 72–85% Cellfonz,
--   68–96% Luxelink), which is why the interlock in app/modules/storeops/payroll_hours_basis.py trips
--   on a declared COVERAGE THRESHOLD and not merely on zero punches.
--
-- MONEY: this migration moves no booked figure. It adds columns and tables and seeds them to the
-- values that reproduce today. The behaviour change is the owner ticking a box afterwards.
--
-- ADAPTIVE BY CONSTRUCTION: every statement is `if not exists` / `on conflict do nothing`, so applying
-- it twice is a no-op and applying it to a database that already has part of it completes the rest.
--
-- REVERT (per step, in reverse order):
--   7. (nothing to revert — read-only proof)
--   6. delete from commcalc.data_lineage where source_key = 'clock_punch_report';  (seeded by 925, not here)
--   5. drop table if exists storeops.clock_feed_login_link;
--   4. drop table if exists storeops.clock_variance_correction;
--   3. drop table if exists storeops.raw_clock_punch;
--   2. drop table if exists storeops.payroll_hours_basis_config;
--   1. alter table storeops.employees drop column if exists pay_from_schedule;

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 1 — THE PER-EMPLOYEE TICK BOX ("a button … checked for each employee separately against that
--          employee name"). NOT NULL DEFAULT FALSE would unpay everybody the moment clocked-only was
--          switched on for an org that had never ticked anybody, so the column is NULLABLE and NULL
--          means "use this org's default" (payroll_hours_basis.pay_from_schedule). STEP 2 seeds every
--          existing org's default to TRUE, which is today.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
alter table storeops.employees add column if not exists pay_from_schedule boolean;

comment on column storeops.employees.pay_from_schedule is
  'Owner ask 2026-10-10. TRUE = when no punch and no correction measured a day, pay this person that '
  'day''s SCHEDULED hours. FALSE = pay nothing for that day and report it (basis not_measured_unpaid). '
  'NULL = use this org''s payroll_hours_basis_config.pay_from_schedule_default. Read ONLY through '
  'app/modules/storeops/payroll_hours_basis.pay_from_schedule; every flip is logged to '
  'storeops.payroll_change_log with entry_point = pay_from_schedule_change.';

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 2 — THE PER-ORG CONFIG (RULE TWO). One row per org; the house row is the documented default.
--          The weekday columns are the owner's Mon / Tue / Wed as DATA (0 = Monday, the same
--          convention storeops.shift_templates.weekday already uses), so a tenant that runs payroll
--          on Thursday changes a row and no code.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
create table if not exists storeops.payroll_hours_basis_config (
  org_id                        uuid primary key,
  -- 'schedule_fallback' = today (an unmeasured day pays its schedule). 'clocked_only' = the owner's ask.
  hours_basis_mode              text    not null default 'schedule_fallback',
  -- The org-level value of the per-employee tick box, for an employee whose own column is NULL.
  pay_from_schedule_default     boolean not null default true,
  -- Is an auto-clocked-out punch a measurement? FALSE in clocked-only mode. In schedule-fallback mode
  -- the code forces TRUE unless this column is set, because that is what happens today (290 live
  -- auto-closed punches are already being paid) and the default must move nobody's pay.
  count_auto_clockout_punches   boolean,
  -- Is an OPEN punch (no clock_out) a measurement? Never, in the house default: there is no span.
  count_open_punches            boolean not null default false,
  -- Do storeops.manual_hours rows count as corrections? The code forces FALSE in schedule-fallback
  -- mode (GET /storeops/payroll has never read them, and folding 569 rows in would move money on
  -- apply); they become payable when clocked-only is switched on, which is the owner's decision.
  count_manual_hours            boolean not null default true,
  -- THE INTERLOCK. A clocked-only period whose measured share of scheduled employee-days is below this
  -- is REFUSED and reported as a data defect, never paid as a shortfall.
  min_punch_coverage_pct        numeric not null default 85.0,
  interlock_refuses             boolean not null default true,
  -- ── the clock-report feed, the Monday ask, the Tuesday gate, the Wednesday run ──
  clock_feed_enabled            boolean not null default false,
  variance_ask_enabled          boolean not null default false,
  login_gate_enabled            boolean not null default false,
  payroll_run_gate_enabled      boolean not null default false,
  ask_weekday                   integer not null default 0,   -- Monday
  gate_weekday                  integer not null default 1,   -- Tuesday
  run_weekday                   integer not null default 2,   -- Wednesday
  variance_tolerance_hours      numeric not null default 0.25,
  -- ── OWNER RULING 2026-10-10: the clock report is a PAY SOURCE, not only a check ──────────────
  -- Sanjot, verbatim: "if they are clocking out at earlier time from the elevate then it means they
  -- are spending time in store to make extra hours and they ahve no more work to do, use that as the
  -- time used to create payroll and mention on the report why you are doing so."
  --
  -- The ruling as a DECLARED CHOICE, not a min() buried in a handler, and with no feed name in code:
  --   'narrowest_window'  (HOUSE DEFAULT) — pay the window the two records AGREE on: the LATER of the
  --                        two clock-ins to the EARLIER of the two clock-outs.
  --   'earlier_clock_out' — the ruling read literally: take whichever record clocked out earlier, in
  --                        FULL. MEASURED on 2026-10-09: this pays MORE than today on 3 of 16 rows,
  --                        because that record's earlier clock-OUT arrives with its earlier clock-IN.
  --                        Khan Amir, Mohammed Abdullah and Seelam Sunethri are those three rows, and
  --                        they are the reason the house default is the narrower reading. Selectable
  --                        by changing this one value if the owner wants the literal words.
  --   'ours_only'         — today: our own punch decides, the report only raises a variance.
  clock_source_rule             text    not null default 'narrowest_window',
  -- Below this, the two records AGREE and ours stands unreconciled — so a rounding difference never
  -- quietly shaves a minute off somebody's pay. Two minutes (0.034h) by default.
  clock_agreement_tolerance_hours numeric not null default 0.034,
  -- THE MASTER SWITCH for the ruling. FALSE = the feed is only a check (today). Nothing above fires
  -- until this is TRUE for the org, which is the owner's decision and not this migration's.
  clock_feed_is_pay_source      boolean not null default false,
  -- ── the FEED's own shape knobs (clock_feed.py). RULE TWO: the real values are DATA, seeded below
  --    for the house org, never spelled in Python.
  -- The `Type` values that are payable work. An unlisted type reads as NOT payable and is reported
  -- (the component_is_evidence allowlist pattern): the sample file carries only 'Work', and a break,
  -- a lunch or a holiday code appearing later must surface rather than default to paid.
  payable_punch_types           text[],
  -- Logins whose punches are NOT a store shift. MEASURED on the real export: 14 of 45 rows are one
  -- back-office login, 12.48h in bursts of 0:00-5:18 across twelve stores as somebody moves between
  -- them in the back office. Those hours are SURFACED, never silently dropped.
  non_shift_logins              text[],
  -- The file's own `Duration Decimal` is TRUNCATED, not rounded (8:28 -> 8.466666). Hours come from
  -- the timestamps; the decimal is a cross-check, and a gap beyond this is reported per row.
  duration_tolerance_hours      numeric not null default 0.02,
  -- ROW 47 OF THE EXPORT IS A TRAILER AND IT IS A CHECKSUM: 'Duration' = '249:10', which equals the
  -- 249.17h sum of the rows exactly. A file whose rows do not tie to its own total is truncated,
  -- filtered or doubled — and it is about to decide pay, so by default it is REJECTED, the way the
  -- carrier statement work ties to the penny.
  trailer_tolerance_hours       numeric not null default 0.05,
  reject_on_trailer_mismatch    boolean not null default true,
  -- Roles this org gates (any spelling; folded case/space-insensitively by the reader). NULL = the
  -- house default list in clock_variance.DEFAULT_CONFIG.
  gated_roles                   text[],
  -- The escape hatch as a CONFIG ROW rather than a name in code.
  gate_exempt_logins            text[],
  created_at                    timestamptz not null default now(),
  updated_at                    timestamptz not null default now(),
  constraint payroll_hours_basis_mode_chk
    check (hours_basis_mode in ('schedule_fallback', 'clocked_only')),
  constraint payroll_hours_basis_weekday_chk
    check (ask_weekday between 0 and 6 and gate_weekday between 0 and 6 and run_weekday between 0 and 6),
  constraint payroll_hours_basis_coverage_chk
    check (min_punch_coverage_pct >= 0 and min_punch_coverage_pct <= 100),
  constraint payroll_hours_basis_tolerance_chk
    check (variance_tolerance_hours >= 0),
  -- The three declared readings and nothing else. A typo in this column must fail the WRITE, not
  -- silently fall through to some default at pay time.
  constraint payroll_hours_basis_clock_rule_chk
    check (clock_source_rule in ('narrowest_window', 'earlier_clock_out', 'ours_only')),
  constraint payroll_hours_basis_clock_tol_chk
    check (clock_agreement_tolerance_hours >= 0)
);

-- ADDITIVE for an org that already has this table from an earlier run of this file (the three
-- owner-ruling columns were added on 2026-10-10 after the table itself). `create table if not
-- exists` above is a no-op in that case, so the columns are added here explicitly.
alter table storeops.payroll_hours_basis_config
  add column if not exists clock_source_rule text not null default 'narrowest_window';
alter table storeops.payroll_hours_basis_config
  add column if not exists clock_agreement_tolerance_hours numeric not null default 0.034;
alter table storeops.payroll_hours_basis_config
  add column if not exists clock_feed_is_pay_source boolean not null default false;
do $$ begin
  alter table storeops.payroll_hours_basis_config
    add constraint payroll_hours_basis_clock_rule_chk
    check (clock_source_rule in ('narrowest_window', 'earlier_clock_out', 'ours_only'));
exception when duplicate_object then null; end $$;

comment on table storeops.payroll_hours_basis_config is
  'Owner ask 2026-10-10. Per-org knobs for THE one home of "which hours is this employee paid for, and '
  'on what basis" (app/modules/storeops/payroll_hours_basis.py, index §14y) and the clock-report '
  'variance / ask / gate built on it (app/modules/storeops/clock_variance.py, index §14z). Every row '
  'ships at today''s behaviour; the whole feature is OFF until a column is changed.';

-- A row for every org that exists, at TODAY'S behaviour. An org with no row behaves the same way (the
-- reader falls back to the house defaults), so this seed is for visibility, not for correctness.
insert into storeops.payroll_hours_basis_config (org_id)
select distinct org_id from storeops.employees where org_id is not null
on conflict (org_id) do nothing;
insert into storeops.payroll_hours_basis_config (org_id)
values ('00000000-0000-0000-0000-000000000001')
on conflict (org_id) do nothing;

-- THE FEED'S SHAPE VALUES, AS DATA. These are the only tenant-specific strings in this package and
-- they are rows, not code (RULE TWO). `payable_punch_types` is the vocabulary the sample file uses;
-- `non_shift_logins` is the back-office login whose 14 rows / 12.48h are not a store shift. A tenant
-- whose export uses different words edits these two arrays and nothing else.
update storeops.payroll_hours_basis_config
   set payable_punch_types = coalesce(payable_punch_types, array['work']),
       non_shift_logins    = coalesce(non_shift_logins, array['PAADMIN'])
 where org_id = '00000000-0000-0000-0000-000000000001';
update storeops.payroll_hours_basis_config
   set payable_punch_types = coalesce(payable_punch_types, array['work'])
 where payable_punch_types is null;

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 3 — THE B2B CLOCK REPORT, AS A CLAIM. "picked from the emails for each store like other
--          reports" — it lands through the EXISTING email sweep (upload_type 'clock_punch'), through
--          the EXISTING column_mapping engine, into its own table.
--
--  ★ THIS TABLE NEVER OVERWRITES storeops.timelog OR storeops.shifts.actual_hours ★
--  The feed is what the CARRIER'S clock says — the same posture as the distributor figure in the
--  carrier work ("the distributor figure is a CLAIM about the same payment"). The VARIANCE between
--  the claim and our own measurement is what a human corrects, through the mechanisms that already
--  own those numbers. If the feed could write our hours, every variance would vanish by being
--  asserted away.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
create table if not exists storeops.raw_clock_punch (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null,
  period          text,
  -- The raw strings the report carried, kept verbatim so an unbound row is REPORTED, never guessed.
  store_raw       text,
  employee_raw    text,
  -- Bound through the platform's OWN homes: account/store_identity + coa.store_resolver for the store,
  -- storeops/payroll_identity for the person. NULL = did not bind; that is a variance, not a drop.
  store_code      text,
  employee_id     text,
  -- The feed's own login column (`User Login`: 'gsantos', 'M.saul', 'Abdullah.M'). MEASURED: it is
  -- the natural join key to a person and it is NOT stored on the employee record at all — only 14 of
  -- 57 house employees carry `epay_login`. So it is landed here and linked by an APPROVED row in
  -- storeops.clock_feed_login_link; a NAME match only ever PROPOSES.
  feed_login      text,
  business_date   date,
  -- UTC, converted from the file's LOCAL wall clock through the store's own zone
  -- (storeops/router._biz_tz_for_store) — never a literal offset.
  clock_in        timestamptz,
  clock_out       timestamptz,
  -- The local strings exactly as the file wrote them, so a conversion can always be re-checked.
  clock_in_local  text,
  clock_out_local text,
  -- Computed from the timestamps. `feed_hours_claimed` is the file's own `Duration Decimal`, which is
  -- TRUNCATED not rounded, and is a cross-check only.
  hours             numeric,
  feed_hours_claimed numeric,
  punch_type      text,
  -- Why this row is not a payable punch (non_shift_login / type_not_payable / store_unbound / …).
  -- NULL = a payable punch. The row is landed either way: a landing that dropped the back-office
  -- rows could never explain where 12.48 hours went.
  excluded        text,
  overnight       boolean not null default false,
  -- B2B'S OWN ORG, as metadata. 'Default Division' / 'Default Region' / 'PA' / 'NJ'. These may NEVER
  -- resolve a store, a market or a recipient — MetricsPro's tree is storeops.org_units, and
  -- clock_variance.ask_plan walks storeops.org_chain and nothing else.
  feed_division   text,
  feed_region     text,
  feed_district   text,
  source_file     text,
  -- THE ARRIVAL STAMP. Declared in data_lineage_registry.FRESHNESS_COLUMN_BY_TABLE, because this
  -- table's rows are replaced per slice and `created_at` would move on a re-write (the daily_sales_feed
  -- trap, index §19.18).
  arrived_at      timestamptz not null default now(),
  created_at      timestamptz not null default now()
);

-- One row per (org, store string, person string, day, clock-in instant). Keyed on the RAW strings as
-- well as the bound ones so a re-sent file is idempotent even for rows that bind nowhere — an unbound
-- row must not multiply on every sweep.
create unique index if not exists raw_clock_punch_org_key_uq
  on storeops.raw_clock_punch (org_id, coalesce(store_raw, ''), coalesce(employee_raw, ''),
                               business_date, coalesce(clock_in_local, ''));
create index if not exists raw_clock_punch_org_date_idx
  on storeops.raw_clock_punch (org_id, business_date);
create index if not exists raw_clock_punch_org_arrived_idx
  on storeops.raw_clock_punch (org_id, arrived_at desc);

comment on table storeops.raw_clock_punch is
  'The B2B clock in / clock out report as a CLAIM about each store-day (owner ask 2026-10-10). Landed by '
  'the existing email sweep under upload_type ''clock_punch''. NEVER written to storeops.timelog or '
  'storeops.shifts.actual_hours: the variance between this claim and our own measurement is the thing a '
  'DM corrects (app/modules/storeops/clock_variance.py, index §14z).';

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 4 — WHAT A HUMAN DECIDED ABOUT ONE VARIANCE. Clearing a variance never edits hours here; the
--          hours are fixed through POST /storeops/manual-hours or PATCH /storeops/shifts, which
--          already log to storeops.payroll_change_log. This table records the DECISION and who made
--          it, so "all clear" is an assertion by a named person and not an absence of rows.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
create table if not exists storeops.clock_variance_correction (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,
  period_start   date not null,
  period_end     date not null,
  store_code     text not null,
  employee_id    text not null,
  business_date  date not null,
  variance_kind  text not null,
  -- 'corrected'      the hours were fixed (through the mechanism that owns them)
  -- 'accepted_ours'  our number is right and the report is wrong
  -- 'accepted_feed'  the report is right and ours has been corrected to match
  resolution     text not null,
  feed_hours     numeric,
  our_hours      numeric,
  note           text,
  corrected_by   text,
  corrected_at   timestamptz not null default now(),
  -- ── THE DM's PER-ROW OVERRIDE (owner ruling 2026-10-10: "The DM must be able to override any
  --    row, with a reason, and the override must out-rank the rule"). It lives on the SAME row as
  --    the decision rather than in a second table, because "what did a human decide about this
  --    (store, employee, day)" is ONE question and two tables answering it would drift.
  --
  --    `override_hours` is what this person is paid for the day, FULL STOP: payroll_hours_basis
  --    checks it FIRST, ahead of the reconciliation rule, and returns basis 'clocked_override'.
  --    `override_reason` is NOT optional — the CHECK constraint below refuses an override without one,
  --    because an unexplained number on a pay row is the thing this whole package exists to stop.
  override_hours   numeric,
  override_reason  text,
  override_by      text,
  override_at      timestamptz,
  constraint clock_variance_resolution_chk
    check (resolution in ('corrected', 'accepted_ours', 'accepted_feed')),
  constraint clock_variance_override_hours_chk
    check (override_hours is null or (override_hours >= 0 and override_hours <= 24)),
  -- An override is a number, a reason and a name, or it is nothing. Enforced in the SCHEMA so no
  -- endpoint, script or hand-written UPDATE can land a bare number on a pay row.
  constraint clock_variance_override_complete_chk
    check (override_hours is null
           or (nullif(btrim(coalesce(override_reason, '')), '') is not null
               and nullif(btrim(coalesce(override_by, '')), '') is not null))
);

create unique index if not exists clock_variance_correction_uq
  on storeops.clock_variance_correction
     (org_id, period_start, period_end, store_code, employee_id, business_date);
create index if not exists clock_variance_correction_org_period_idx
  on storeops.clock_variance_correction (org_id, period_start, period_end);

comment on table storeops.clock_variance_correction is
  'One row per clock-report variance a human has settled (owner ask 2026-10-10). Clearing a variance '
  'records a DECISION; the hours themselves are corrected through storeops.manual_hours / '
  'storeops.shifts, which carry their own payroll_change_log audit.';

comment on column storeops.clock_variance_correction.override_hours is
  'The DM''s own figure for this (store, employee, day), out-ranking the org''s clock_source_rule '
  '(owner ruling 2026-10-10). payroll_hours_basis.resolve_day reads it FIRST and returns basis '
  '''clocked_override''. Never settable without override_reason and override_by — the schema refuses it.';

-- WHY THERE IS NO TRIGGER HERE, THOUGH AN OVERRIDE IS MONEY.
-- `storeops.payroll_change_log` has exactly ONE writer, on purpose and under a lock:
-- `storeops/router.py:_log_payroll_change` (`harness_payroll_log_identity_lock.py`). That writer is
-- not a convenience wrapper — it resolves WHO THE ROW IS ABOUT from the STORED employee through
-- `payroll_log_identity.resolve_log_identity` and mints a missing business id (index §19.45), which
-- is why the 1054 backfill existed at all. A database trigger writing the log directly would be a
-- SECOND writer that cannot do any of that, so it would reintroduce exactly the NULL-identity defect
-- 1054 was written to repair.
--
-- So the override is logged where every other pay change is logged: the correction endpoint calls
-- `_log_payroll_change(entry_point='clock_variance_override', field='override_hours', ...)` with the
-- stored employee row in hand. The SCHEMA's job is to make an unexplained override impossible
-- (`clock_variance_override_complete_chk` above, which no endpoint or hand-written UPDATE can skip);
-- the LOG's job is to say who did it, and that stays with the one writer.


-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 5 — WHICH B2B LOGIN IS WHICH EMPLOYEE. A name is a PROPOSAL; only an APPROVED row here binds.
--
-- WHY THIS TABLE EXISTS, measured. The export names a person as `Last, First` (sometimes
-- `Last, First, M`) and by a `User Login` ('gsantos', 'M.saul', 'Abdullah.M', 'Debo'). Only 14 of 57
-- house employee records carry `epay_salesperson`, 14 carry `epay_login`, and the feed's login is
-- stored nowhere. 29 of 45 rows match a roster employee by NAME TOKEN — and a name is exactly what
-- this house forbids resolving an identity by at runtime (core.scope._squash says so in its own
-- docstring; migration 740's cleanup was human-reviewed for the same reason). So a name match is a
-- PROPOSAL on a review screen, persisted here once approved, and "this login is nobody" is a
-- first-class variance a DM resolves. A 'rejected' row is a human saying "not this person" and is
-- never re-proposed as though nobody had looked.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
create table if not exists storeops.clock_feed_login_link (
  id            uuid primary key default gen_random_uuid(),
  org_id        uuid not null,
  feed_login    text not null,
  employee_id   text not null,
  status        text not null default 'proposed',
  proposed_name text,
  proposed_by   text,
  approved_by   text,
  approved_at   timestamptz,
  note          text,
  created_at    timestamptz not null default now(),
  constraint clock_feed_login_status_chk
    check (status in ('proposed', 'approved', 'rejected'))
);

create unique index if not exists clock_feed_login_link_uq
  on storeops.clock_feed_login_link (org_id, lower(feed_login), employee_id);
create index if not exists clock_feed_login_link_org_status_idx
  on storeops.clock_feed_login_link (org_id, status);

comment on table storeops.clock_feed_login_link is
  'Which B2B clock-report login is which employee (owner ask 2026-10-10). ONLY status=''approved'' '
  'attributes hours (app/modules/storeops/payroll_identity.match_is_binding); a name-token match is a '
  'proposal for a human to approve and NEVER an attribution.';

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 6 — THE LINEAGE EDGES live in 925_data_lineage_seed.sql, NOT here. ON PURPOSE.
--          925 is a WHOLESALE reseed (`delete from commcalc.data_lineage;` then one insert), so an
--          edge written here would be silently wiped the next time 925 ran — the registry would
--          claim a feed it no longer documented, which is exactly the drift the lineage guard
--          exists to catch. The three `clock_punch_report` edges (ingest · variance derive · the
--          ask/gate) are therefore appended to 925 in this same PR, and
--          `harness_data_lineage_guard.py` proves they are there.
-- ════════════════════════════════════════════════════════════════════════════════════════════════

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 6b — GRANTS + RLS for the four new tables, and the PostgREST schema reload. Same shape as
--           every other storeops table (mig 915 onward): RLS on, one open policy, grants to the
--           three roles. The ROW-LEVEL privacy that matters here is not RLS — it is that every
--           query in this package is org-scoped in code and that pay money is stripped by
--           `storeops/pay_visibility.strip_pay` before it leaves the API (mig 434).
-- ════════════════════════════════════════════════════════════════════════════════════════════════
do $$
declare t text;
begin
  foreach t in array array['payroll_hours_basis_config', 'raw_clock_punch',
                           'clock_variance_correction', 'clock_feed_login_link']
  loop
    execute format('alter table storeops.%I enable row level security', t);
    execute format('drop policy if exists open_all on storeops.%I', t);
    execute format('create policy open_all on storeops.%I for all to anon, authenticated using (true) with check (true)', t);
    execute format('grant all on storeops.%I to anon, authenticated, service_role', t);
  end loop;
end $$;

notify pgrst, 'reload schema';

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 7 — THE PROOF THAT NOBODY'S HOURS MOVE. Read-only. Every row must read 'OK'.
--          Run this in the SAME execution as the steps above; it is the receipt for the claim that
--          applying this migration is behaviour-neutral.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
select 'employees with pay_from_schedule = FALSE (must be 0 — FALSE is what stops paying a day)' as check,
       count(*)::text as value,
       case when count(*) = 0 then 'OK' else 'STOP — somebody would be unpaid on apply' end as verdict
from storeops.employees where pay_from_schedule is false
union all
select 'orgs whose hours_basis_mode is NOT schedule_fallback (must be 0 on apply)',
       count(*)::text,
       case when count(*) = 0 then 'OK' else 'STOP — an org would switch basis on apply' end
from storeops.payroll_hours_basis_config where hours_basis_mode <> 'schedule_fallback'
union all
select 'orgs whose pay_from_schedule_default is not TRUE (must be 0 on apply)',
       count(*)::text,
       case when count(*) = 0 then 'OK' else 'STOP — an org would stop paying unmeasured days' end
from storeops.payroll_hours_basis_config where pay_from_schedule_default is not true
union all
-- OWNER RULING 2026-10-10 makes the clock report a PAY SOURCE. That is a real change to how pay is
-- computed, so it must be OFF on apply and turned on deliberately, per org, by the owner.
select 'orgs where the clock report is already a PAY SOURCE (must be 0 on apply)',
       count(*)::text,
       case when count(*) = 0 then 'OK' else 'STOP — the feed would decide pay on apply' end
from storeops.payroll_hours_basis_config where clock_feed_is_pay_source is true
union all
select 'orgs whose clock_source_rule is not the seeded house default (must be 0 on apply)',
       count(*)::text,
       case when count(*) = 0 then 'OK' else 'STOP — an org carries a non-default reading' end
from storeops.payroll_hours_basis_config where clock_source_rule <> 'narrowest_window'
union all
select 'variance rows carrying a DM override (must be 0 — the table is new)',
       count(*)::text,
       case when count(*) = 0 then 'OK' else 'STOP — an override exists before the feature is on' end
from storeops.clock_variance_correction where override_hours is not null
union all
select 'orgs with any half of the feature switched on (must be 0 on apply)',
       count(*)::text,
       case when count(*) = 0 then 'OK' else 'STOP — a gate or a feed is live on apply' end
from storeops.payroll_hours_basis_config
where clock_feed_enabled or variance_ask_enabled or login_gate_enabled or payroll_run_gate_enabled
union all
select 'clock-report rows landed (expected 0 until a mailbox rule is added)',
       count(*)::text, 'OK'
from storeops.raw_clock_punch
union all
select 'shifts rows that actually carry a measured actual_hours (the dead column: 7 of 7,740 on 2026-10-10)',
       count(*)::text, 'OK'
from storeops.shifts where coalesce(actual_hours, 0) > 0
union all
select 'timelog punches that ARE a measurement (the hours payroll should be reading)',
       count(*)::text, 'OK'
from storeops.timelog where clock_out is not null and hours is not null
union all
select 'manual_hours corrections that GET /storeops/payroll has never read',
       count(*)::text, 'OK'
from storeops.manual_hours;
