-- 1111_plan_edit_restate_months.sql
-- HOW FAR BACK A PLAN EDIT RESTATES (index §COMMCALC-41.9, owner report 2026-10-31)
--
-- Owner, verbatim: *"i upldated the commission for luxelink in ny plan but it did not get auto updated
-- as we confirmed yesterday that everytime anything is change it will auto update acorss the board, i
-- changed teh comission from june - oct"*.
--
-- Editing a commission plan now restates every month that has stored rep figures, through the one
-- post-landing hook. That has to be BOUNDED: Cellfonz R Us holds a `July 2024` row beside its 2026
-- months (measured 2026-10-31), and a rate corrected today must not rewrite 2024.
--
-- RULE TWO — config, never code. The horizon is a POLICY (a number of months), which is the one kind of
-- fact a house default is still right for; it is NOT a tenant's own vocabulary, which the 2026-10-30
-- directive forbids defaulting. The ladder is: this tenant's row, then the house org's row
-- (00000000-0000-0000-0000-000000000001), then the code default of 12
-- (`auto_calc.DEFAULT_RESTATE_MONTHS`, bounded 1..36 by `RESTATE_MONTHS_BOUNDS`).
--
-- APPLYING THIS IS OPTIONAL. `auto_calc.restate_months_config` reads the column defensively and falls
-- back to the code default when it is absent, so a database without this migration restates on a
-- 12-month horizon and nothing refuses. Applying it only makes the horizon editable per tenant.
--
-- IDEMPOTENT and ADDITIVE: one nullable column, no backfill, no existing value changed. It moves no
-- money — it bounds which months a LATER recalculation covers, and that recalculation is the same
-- `_run_calculation` a person presses, with every refusal guard armed.
--
-- NO GRANT CLAUSE IS NEEDED: this creates no relation. `commcalc.commission_org_config` already carries
-- its service_role grants (§CORE-13 applies to `create table` / `create view`).

alter table if exists commcalc.commission_org_config
    add column if not exists plan_edit_restate_months integer;

comment on column commcalc.commission_org_config.plan_edit_restate_months is
    'How many months back a commission-plan edit restates (index §COMMCALC-41.9). NULL = inherit the '
    'house org row, then the code default of 12. Clamped to 1..36 by auto_calc.RESTATE_MONTHS_BOUNDS.';

-- REVERT: alter table commcalc.commission_org_config drop column if exists plan_edit_restate_months;
--         (Safe at any time: every reader falls back to the house default when the column is absent.)
