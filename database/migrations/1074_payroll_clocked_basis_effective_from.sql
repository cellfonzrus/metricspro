-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- 1074 — THE CLOCKED-BASIS EFFECTIVE-FROM DATE (owner ruling 2026-10-12)
--
-- OWNER, VERBATIM: "Payroll from clack in should be from Oct 4 th onwards which would be the new
--                   cycle"
--
-- WHAT THIS ADDS
--   storeops.payroll_hours_basis_config.clocked_basis_effective_from  date, NULLABLE
--
--   On and after this date, the org's `hours_basis_mode` applies as declared. STRICTLY BEFORE it,
--   the day is resolved exactly as it is today — from the schedule when nothing measured it — so a
--   recompute of a closed month restates NOTHING. NULL means NO FLOOR, which is every org until a
--   row carries a date, so this column changes nobody on apply.
--
-- WHY IT IS NEEDED, IN NUMBERS (measured on live house data, read-only, 2026-10-12)
--   The house org (00000000-0000-0000-0000-000000000001) is LIVE on `hours_basis_mode =
--   'clocked_only'` with `pay_from_schedule_default = false`. The punches only start in July 2026
--   and are partial thereafter, so a Run Calculation would have booked the P&L wage line as:
--       Jun  555 shifts  $75,837.00 -> $0.00      Jul  640  $88,128.00 -> $0.00
--       Aug  613 shifts  $85,389.00 -> $0.00      Sep  603  $81,132.02 -> $0.00
--       Oct  341 shifts  $44,247.00 -> $0.00      TOTAL  $374,733.02 -> $0.00
--   Two separate defects produced that: the four pay-path callers reached the hours resolver with no
--   punch evidence at all (fixed in code — app/modules/storeops/payroll_evidence.py), and the new
--   basis applied to months that were already closed on the old one (fixed by THIS column).
--
-- RULE TWO — CONFIG, NEVER CODE. The date 2026-10-04 appears in this migration and NOWHERE in the
--   application source. `app/modules/storeops/payroll_hours_basis.py` reads it from this column
--   (`effective_from` / `day_is_before_floor` / `config_for_day`), and
--   `backend/harness_payroll_hours_basis_lock.py` §H fails the build if a date literal appears in
--   app code.
--
-- MONEY: this migration BOOKS NOTHING and MOVES NO FIGURE. It adds one nullable column and sets it
--   for one org. Its entire effect is to make four closed months keep the figures they already have.
--   STEP 3 PROVES that with a row count, not a promise.
--
-- ADAPTIVE BY CONSTRUCTION: every statement is `if not exists` / guarded by `is null`, so applying
--   it twice is a no-op and applying it to a partially-migrated database completes the rest.
--
-- REVERT (per step, in reverse order):
--   3. (nothing to revert — read-only proof)
--   2. update storeops.payroll_hours_basis_config
--        set clocked_basis_effective_from = null
--        where org_id = '00000000-0000-0000-0000-000000000001';
--   1. alter table storeops.payroll_hours_basis_config
--        drop column if exists clocked_basis_effective_from;
-- ════════════════════════════════════════════════════════════════════════════════════════════════

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 1 — THE COLUMN. NULLABLE on purpose: NULL = "no date floor", i.e. the mode applies to every
--          day, which is exactly today's behaviour for every org. A NOT NULL DEFAULT would have to
--          pick a date for every tenant, and picking a date for a tenant nobody asked about is the
--          per-tenant guess RULE TWO exists to prevent.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
alter table storeops.payroll_hours_basis_config
  add column if not exists clocked_basis_effective_from date;

comment on column storeops.payroll_hours_basis_config.clocked_basis_effective_from is
  'Owner ruling 2026-10-12: "Payroll from clack in should be from Oct 4 th onwards which would be '
  'the new cycle". The date on and after which this org''s hours_basis_mode applies. A business date '
  'STRICTLY BEFORE it resolves exactly as hours_basis_mode = schedule_fallback would, whatever the '
  'mode says and whatever the per-employee pay_from_schedule tick box says, so recomputing a closed '
  'month restates nothing. NULL = NO FLOOR (the mode applies to every day) and is the shipped value '
  'for every org. Read ONLY through app/modules/storeops/payroll_hours_basis.effective_from / '
  'day_is_before_floor / config_for_day; no date literal exists in application code.';

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 2 — THE HOUSE ORG'S OWN DATE, which is the one the owner ruled on.
--          IDEMPOTENT by the `is null` guard: a second run finds the date already set and changes
--          nothing, and it can never overwrite a date somebody chose later by hand.
--          ADDITIVE: it touches one row of one table and books no figure.
--          NO ROW IS CREATED. If the house org has no config row yet it has no clocked-only mode
--          either (mig 1073 seeds 'schedule_fallback'), so there is nothing for a floor to protect —
--          inventing a row here would be this migration deciding a tenant's pay basis for it.
--          EVERY OTHER ORG IS LEFT NULL, deliberately: the ruling is the house owner's, about his
--          own payroll cycle. A second tenant that switches to clocked-only sets its own date, and
--          /mnt/project-files/payroll-clocked-hours/ carries the one-run SQL for doing so.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
update storeops.payroll_hours_basis_config
   set clocked_basis_effective_from = date '2026-10-04'
 where org_id = '00000000-0000-0000-0000-000000000001'::uuid
   and clocked_basis_effective_from is null;

-- ════════════════════════════════════════════════════════════════════════════════════════════════
-- STEP 3 — THE PROOF, read-only. Expect:
--            · one row per org, `floor` set only where a human set it;
--            · `orgs_on_clocked_only_without_a_floor` = the orgs whose closed months WOULD be
--              restated by a recompute. For the house estate that must read 0 after this runs; a
--              non-zero count is a REPORT to the owner, not something this migration may silently
--              fix for a tenant nobody ruled on.
-- ════════════════════════════════════════════════════════════════════════════════════════════════
select org_id,
       hours_basis_mode,
       pay_from_schedule_default,
       clocked_basis_effective_from as floor
  from storeops.payroll_hours_basis_config
 order by org_id;

select count(*) as orgs_on_clocked_only_without_a_floor
  from storeops.payroll_hours_basis_config
 where hours_basis_mode = 'clocked_only'
   and clocked_basis_effective_from is null;
