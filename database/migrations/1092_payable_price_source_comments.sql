-- 1092_payable_price_source_comments.sql
-- WHAT: name, on the columns themselves, which table `owed_price_table` is supposed to point at.
-- WHY : mig 1091 shipped `owed_price_table` / `owed_price_model_field` with no COMMENT, and the very
--        first row written against them (2026-10-22) declared `raw_ma_fulfillment.sku`. That reads as
--        "the price list", which is the natural reading of the NAME and is wrong: the price list is
--        always the distributor's handset-ordering sheet and is deliberately not configurable. These
--        two columns name the EVIDENCE feed — the one carrying the skus handsets actually activate
--        under, i.e. the commission feed. `raw_ma_fulfillment` has no `sku`, so the read raised, the
--        price map came back empty, and 2,923 unsold devices were written with no cost at all while
--        reading as though no cost source had ever been configured. Index §COMMCALC-29.
-- SAFE : comments only. No column, constraint, grant or row is touched, so there is nothing to
--        recompute and no behaviour changes on applying it.
-- REVERT: COMMENT ON COLUMN commcalc.payable_source_map.owed_price_table IS NULL;
--         COMMENT ON COLUMN commcalc.payable_source_map.owed_price_model_field IS NULL;
--         COMMENT ON COLUMN commcalc.payable_source_map.owed_mode IS NULL;

COMMENT ON COLUMN commcalc.payable_source_map.owed_mode IS
  'How a device gets its cost when the carrier''s own feed row carries no amount. '
  '''field'' (the default, every pre-1091 map): the row''s own owed_field. '
  '''unit_price_map'': the weighted unit price off the distributor''s handset-ordering sheet, joined '
  'by model name. ''none'': no cost source; owed stays NULL and the surface says so.';

COMMENT ON COLUMN commcalc.payable_source_map.owed_price_table IS
  'owed_mode=''unit_price_map'' only. The EVIDENCE feed, NOT the price list. It is the table whose '
  'rows prove which product names are handsets at all - the commission feed (e.g. '
  'commcalc.raw_ma_commission), the one carrying a sku per activated line. The price list itself is '
  'always the handset-ordering / fulfillment sheet and is not configurable. Naming the fulfillment '
  'table here reads NOTHING (it has no sku column) and leaves every device unpriced. Left NULL, the '
  'price home falls back to its own order-type allowlist, which still prices most handsets.';

COMMENT ON COLUMN commcalc.payable_source_map.owed_price_model_field IS
  'owed_mode=''unit_price_map'' only. The column on owed_price_table that carries the product name '
  '(''sku'' on the commission feed). It must exist on that table: a name that does not resolve is '
  'reported with the rebuild result rather than silently pricing nothing.';
