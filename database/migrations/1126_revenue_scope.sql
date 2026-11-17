-- 1126_revenue_scope.sql
-- DOES MONEY WE COULD NOT CLASSIFY COUNT AS A DAY'S REVENUE — declared per org.
--
-- OWNER'S ANSWER (Sanjot, 2026-11-17), choosing "Whole day's revenue counts" when asked how
-- $22,653.31 of his October sales report's unclassified product lines should count toward the day.
-- And earlier, of his store's daily revenue gate: "that is the total revenue for the day, which ever
-- sales rep generates the most revenue he get his proportional share of commission".
--
-- THE CLASS THIS FIXES, not the instance. The general fact that was wrong is "a day's revenue is the
-- sum of the categories we could place". Whether WE can classify a line says nothing about whether
-- the store sold it. Two questions were being answered with one number: "how much did category X do"
-- (only a placeable line can answer) and "what was the day's revenue" (every line answers). The sum
-- over placed categories is the right answer to the first and the wrong answer to the second.
--
-- WHY A COLUMN AND NOT A CODE CHANGE. Reading unclassified money into revenue house-wide would
-- restate every tenant's gross revenue, and commcalc.closing/metric_catalog.gross_revenue is what
-- every revenue target and every percent-of-amount payout measures against. That is a money move
-- nobody approved. So this is a per-org DECLARATION whose absence reads as today's behaviour.
--
-- RULE TWO. This names no tenant, carrier, product or category. It is a POLICY switch, which is the
-- one thing a house default is still right for; the categories themselves stay the tenant's own,
-- required and never defaulted (owner directive 2026-10-30).
--
-- DUPLICATE CHECK (build gate — index SS16/SS17 searched, and the lineage registry):
--   - commcalc.commission_org_config (mig 1059+) - REUSED, and this is a new column on it rather
--     than a new table. It is already the home of every per-org commission POLICY, including
--     declared_metric_authority (mig 1124), which answers the sibling question "WHICH source
--     answers this day" and is read by the same endpoint. A second config table for one switch
--     would be the sibling derivation the index rules forbid.
--   - commcalc.closing_count_field_def (mig 1072) - read, not extended: that registry says what a
--     tenant DECLARES, one row per category. "What to do with a line that matches none of them" is
--     not a property of any one category.
--   - commcalc.data_source.portal_calibration - read, not extended: a calibration is per REPORT
--     (its columns, its line map, its sign). This is the org's definition of revenue, which the
--     targets engine and every payout read far away from any one report.
--   - commcalc.targets / target_line (migs 006 / 1074), commcalc.period_goal (mig 1125) - read.
--     They set WHAT IS AIMED AT; this sets WHAT COUNTS toward it.
--
-- NOT MONEY-TOUCHING AS APPLIED. It adds one nullable column and writes no value. NULL resolves to
-- 'classified_only' in revenue_scope.resolve, which is byte-for-byte today's behaviour for every tenant
-- including this one. No figure moves until an operator declares 'whole_day' for an org, and the
-- code that reads the column degrades to NULL while this migration waits (the endpoint answers
-- "nothing declared" rather than 500, the same shape mig 1124's reader uses).
--
-- REVERT: alter table commcalc.commission_org_config drop column if exists declared_revenue_scope;
--         NOTIFY pgrst, 'reload schema';

alter table commcalc.commission_org_config
  add column if not exists declared_revenue_scope text;

do $$ begin
  execute 'alter table commcalc.commission_org_config
             add constraint commission_org_config_revenue_scope_chk
             check (declared_revenue_scope is null
                    or declared_revenue_scope in (''classified_only'', ''whole_day''))';
exception when others then null; end $$;

comment on column commcalc.commission_org_config.declared_revenue_scope is
  'Does money on a sales-report line that no declared category claimed count toward the day''s gross revenue? classified_only (the house default, and what NULL means) = no, revenue is the categories we could place. whole_day = yes, the whole day''s takings count. Read ONLY through commcalc/revenue_scope.resolve; a caller that compares this string itself is the second copy the house rules forbid. Declaring whole_day does not excuse an unmapped line: the money counts toward the day AND the line is still reported as unmapped, so each category still gets its own figure. An unclassified line can never pay a per-unit or percent-of-amount rule under either scope, because metric_authority.lines_from_feed carries no row without a category.';

-- The backend reaches every relation through PostgREST, and this table is already granted; a column
-- inherits the table's grants, so nothing further is needed. Stated rather than assumed, because the
-- house rule is that a migration grants what it CREATES and the next reader should not have to check.

NOTIFY pgrst, 'reload schema';
