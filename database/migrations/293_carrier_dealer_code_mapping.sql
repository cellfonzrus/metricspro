-- 293_carrier_dealer_code_mapping.sql   (mod-commission, band 200-299)
--
-- OWNER 2026-08-09: "the dealer codes are not being pulled from the reports" + when asked which field
-- IS the dealer code: "every company will have their own name, in boost it is the salesforce id and
-- in total it is the account id".
--
-- So the dealer code is not one field -- it is a per-CARRIER concept with a per-carrier LABEL. This
-- makes it mappable config rather than a branch in code (SAP-configurable rule; the same reasoning
-- that made carrier_id config in mig 291). Adding Cricket or Verizon later is a row, not a deploy.
--
-- Verified live before writing: the named columns exist.
--   Boost  -> commcalc.raw_mi.salesforce_id            (also on raw_comp_report / raw_dlar_*)
--   Total  -> commcalc.raw_ma_daily_tx.account_id      (with account_name as the human label;
--             raw_ma_commission carries merchant_account_id, a different grain, so it is NOT used)
--
-- Moves NO payout number. Additive + idempotent.

begin;

alter table commcalc.carrier
    add column if not exists dealer_code_label         text,
    add column if not exists dealer_code_source_table  text,
    add column if not exists dealer_code_source_column text,
    add column if not exists dealer_code_name_column   text;

comment on column commcalc.carrier.dealer_code_label is
    'What THIS carrier calls its dealer code -- "Salesforce ID" for Boost, "Account ID" for Total. '
    'Shown as the field label wherever a dealer code is displayed or entered.';
comment on column commcalc.carrier.dealer_code_source_table is
    'commcalc table the codes are harvested from when syncing pos.dealer_codes from report data.';
comment on column commcalc.carrier.dealer_code_source_column is
    'Column in that table holding the code itself.';
comment on column commcalc.carrier.dealer_code_name_column is
    'Optional column holding the human-readable account/door name for that code. NULL when the '
    'report carries no name (Boost MI has none -- the code stands alone).';

-- Boost: Salesforce ID, harvested from the MI/ATU report (the most complete Boost-side roster).
update commcalc.carrier
   set dealer_code_label         = coalesce(nullif(dealer_code_label,''),         'Salesforce ID'),
       dealer_code_source_table  = coalesce(nullif(dealer_code_source_table,''),  'raw_mi'),
       dealer_code_source_column = coalesce(nullif(dealer_code_source_column,''), 'salesforce_id')
 where name ilike 'boost%';

-- Total: Account ID, harvested from MA Daily TX, which also carries the account name.
update commcalc.carrier
   set dealer_code_label         = coalesce(nullif(dealer_code_label,''),         'Account ID'),
       dealer_code_source_table  = coalesce(nullif(dealer_code_source_table,''),  'raw_ma_daily_tx'),
       dealer_code_source_column = coalesce(nullif(dealer_code_source_column,''), 'account_id'),
       dealer_code_name_column   = coalesce(nullif(dealer_code_name_column,''),   'account_name')
 where name ilike 'total%';

-- Verizon deliberately left unmapped: nobody has said what its dealer code is, and guessing would
-- seed a tenant's POS with the wrong identifier. An unmapped carrier simply reports "not configured".

commit;
