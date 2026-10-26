-- 1093_payable_device_class_field.sql
-- WHAT: two nullable declaration columns on commcalc.payable_source_map —
--         device_class_field : the carrier feed column that says WHETHER a handset was bought on
--                              this line (e.g. raw_ma_commission.activation_type2);
--         no_device_tokens   : optional per-org override of the values on that column which mean
--                              "no device" (house default: byop / byod / bring your own).
-- WHY : the payables ledger wrote a row for every activation, including the ones where the customer
--        brought their own phone. 869 Luxelink lines named no product because NO PRODUCT WAS SOLD,
--        and two of them were showing as `discrepancy` — i.e. telling the owner he is carrying a
--        device cost that never existed. The feed itself already declares this per line; nothing was
--        reading the declaration. With this column selected those lines get status `no_device` and
--        leave the payable totals entirely, while staying listed and auditable.
--        RULE TWO: the column NAME and the tokens are config, so no carrier name enters code.
--        The tokens themselves have ONE home (commcalc/product_mix.HOUSE_NO_DEVICE_TOKENS), which
--        this column overrides rather than copies. Index §COMMCALC-31.
-- SAFE : additive and nullable. Left NULL (every pre-existing row, including both Total rows until
--        the config runbook is applied) behaviour is EXACTLY as before — the question is simply not
--        asked. No amount is recomputed by applying this file; the next ledger rebuild is what
--        re-statuses lines, and that is a button the owner presses.
-- REVERT: alter table commcalc.payable_source_map drop column if exists device_class_field;
--         alter table commcalc.payable_source_map drop column if exists no_device_tokens;

alter table commcalc.payable_source_map
  add column if not exists device_class_field text,
  add column if not exists no_device_tokens   text[];

COMMENT ON COLUMN commcalc.payable_source_map.device_class_field IS
  'The column on the carrier source feed that DECLARES whether a handset was bought on this line '
  '(commcalc.raw_ma_commission.activation_type2 for the MA feeds: ''branded'' = a handset was sold, '
  '''byop'' = the customer brought their own). Left NULL the question is not asked and every '
  'bring-your-own-phone line reads as an unpriced device payable. Only the feed''s OWN declaration '
  'counts here (index §66, basis-is-evidence): a line that merely names no product is reported as '
  '''no product named'', never read as proof that no device was sold.';

COMMENT ON COLUMN commcalc.payable_source_map.no_device_tokens IS
  'Optional per-org override of the values on device_class_field that mean "no handset was bought". '
  'NULL (the normal case) uses the house vocabulary, whose one home is '
  'backend/app/modules/commcalc/product_mix.py::HOUSE_NO_DEVICE_TOKENS - matched as a substring, '
  'case-insensitively. Set it only for a feed whose spelling the house list does not cover.';

-- A migration grants what it creates. No new relation here, but the grant is restated so a fresh
-- environment that applies files out of order cannot end up with a table the backend cannot reach.
grant select, insert, update, delete on commcalc.payable_source_map to service_role;
