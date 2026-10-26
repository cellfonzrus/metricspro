-- 1095_payable_billing_cadence.sql
-- Index §COMMCALC-34. Additive, idempotent, nullable. NO MONEY MOVES.
--
-- OWNER REPORT, verbatim: "Weekly Owed to Distributor does not show anything in luxelink".
--
-- WHY. `asset/router.get_owed_weekly` read commcalc.asset_ledger filtered on `billing_friday` and
-- nothing else. That column is ONE carrier's weekly consignment rhythm, stamped by ONE carrier's
-- portal sweep, on ONE carrier's table. MEASURED 2026-10-26: Luxelink has 0 asset_ledger rows, so
-- every figure on that report was a sum over an empty set. It is §COMMCALC-26.1's class - a
-- carrier's arrangement written into code instead of declared - arriving on a fifth surface.
--
-- A WEEKLY FRIDAY IS NOT A UNIVERSAL FACT ABOUT OWING MONEY. Boost/VIP consigns and bills a batch
-- each week, so "which Friday does this device bill on" is a real question with a stored answer.
-- Total / MA raises an invoice per order and it falls due on its own terms - there is no Friday at
-- all, and inventing one would put a date on real money that no feed and no owner ever stated
-- (§COMMCALC-26.3 made the same ruling about net terms). So the rhythm is DECLARED, per carrier,
-- on the config spine that already carries the selling window and the cost source.
--
-- EVERY COLUMN IS NULL AFTER THIS RUNS, WHICH IS BYTE-IDENTICAL TO TODAY. The resolver's house
-- default is `per_due_date` - the money is owed on the date it is owed - which asserts no rhythm.
-- The VALUES are a separate, approval-gated data step.
--
-- REVERT:
--   alter table commcalc.payable_source_map drop column if exists billing_cadence;
--   alter table commcalc.payable_source_map drop column if exists billing_weekday;
--   alter table commcalc.payable_source_map drop column if exists billing_period_field;

alter table commcalc.payable_source_map
  add column if not exists billing_cadence      text,
  add column if not exists billing_weekday      integer,
  add column if not exists billing_period_field text;

-- A declared weekday is 0..6 with Monday 0, matching datetime.date.weekday(), so the column needs
-- no translation and there is no off-by-one to get wrong between here and the comparison.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'payable_source_map_billing_weekday_ck') then
    alter table commcalc.payable_source_map
      add constraint payable_source_map_billing_weekday_ck
      check (billing_weekday is null or (billing_weekday >= 0 and billing_weekday <= 6));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'payable_source_map_billing_cadence_ck') then
    alter table commcalc.payable_source_map
      add constraint payable_source_map_billing_cadence_ck
      check (billing_cadence is null or billing_cadence in ('per_due_date', 'weekly'));
  end if;
end $$;

COMMENT ON COLUMN commcalc.payable_source_map.billing_cadence IS
  'This carrier''s billing RHYTHM, declared. ''weekly'': it bills on one weekday, so a due date '
  'rolls forward to the first such day on or after it (Boost/VIP). ''per_due_date'' (and what NULL '
  'means): each invoice falls due on its own date and there is no weekly batch (Total/MA). The '
  'default asserts no rhythm on purpose - a guessed billing day puts a date on real money that no '
  'feed ever stated. The vocabulary lives in backend/app/modules/payables/billing_period.py '
  '(CADENCES) and a value it has not ruled on falls to the house default rather than being honoured.';

COMMENT ON COLUMN commcalc.payable_source_map.billing_weekday IS
  'Which weekday this carrier bills on, 0=Monday .. 6=Sunday, matching Python''s '
  'date.weekday() so the number needs no translation. Only meaningful with billing_cadence = '
  '''weekly''. An UNDECLARED weekday under that cadence is NOT MEASURED: the money is still shown '
  'and still counted, grouped by its own due date, and the screen says the grouping is not the '
  'carrier''s own. Defaulting it to Friday would be the same defect in a config column instead of '
  'in code.';

COMMENT ON COLUMN commcalc.payable_source_map.billing_period_field IS
  'The column on this carrier''s own feed that ALREADY carries the billing period it assigned '
  '(Boost/VIP: asset_ledger.billing_friday). Where set, that stamped value IS the period and '
  'nothing recomputes it - a derived billing day that disagreed with the sweep''s own would move a '
  'figure every other surface reconciles against, and the two would then drift exactly as §19.18 '
  'describes. Where unset, the period is derived from the due date under billing_cadence.';

-- A migration grants what it creates. No new relation here, but the grant is restated so a fresh
-- environment applying files out of order cannot end up with a table the backend cannot reach.
grant select, insert, update, delete on commcalc.payable_source_map to service_role;
