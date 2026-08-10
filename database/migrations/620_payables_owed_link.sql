-- 620 — payable_source_map.owed_link: price a device from a RELATED report, not just its own row.
--
-- WHY (owner 2026-08-10): "for daily owed use 20 days from the day it shows it is shipped on the
-- handset report, as a default which is changeable when we get the proper reporting from the email."
--
-- The problem it solves: luxelink's only source map is `Total / MA` over `raw_ma_commission`, which has
-- NO owed_field — the MA reports record what was ACTIVATED, never what was INVOICED. So every device
-- priced at NULL, Daily Owed grouped nothing, and all 1,147 sold-without-rebate devices sat in
-- `discrepancy` at $0.00. The amount was in the database the whole time, one table over:
-- `commcalc.raw_ma_fulfillment` (the Marketplace Handset Fulfillment report) carries `price` and
-- `date_shipped` — 404 luxelink rows, 391 shipped, 404 priced, $290,940.21.
--
-- Rather than hard-code "Total reads its price from the fulfillment report", this is a CONFIG link
-- (SAP-configurable rule): any source map can say "my amount lives in table X, joined on my key K to
-- its column R, amount A, dated D, due D + N days". A tenant on a different processor describes their
-- own join instead of waiting for code.
--
--   {"table":"raw_ma_fulfillment","key_field":"activation_order","ref_field":"order_number",
--    "amount_field":"price","date_field":"date_shipped","terms_days":20}
--
-- `terms_days` is the owner's CHANGEABLE default, not a constant: when the real invoices land by email
-- the link is repointed at the invoice table and the true terms replace the 20.
-- An explicit `owed_field` on the row still WINS — this only fills a source map that has none, so the
-- Boost/VIP map (owed_to_vip) is untouched.

ALTER TABLE commcalc.payable_source_map
  ADD COLUMN IF NOT EXISTS owed_link jsonb;

COMMENT ON COLUMN commcalc.payable_source_map.owed_link IS
  'Optional join that prices a device from a RELATED report when the source row has no owed_field. '
  'Keys: table, key_field (on the source row), ref_field (on the target), amount_field, date_field, '
  'terms_days (due = date_field + terms_days). An explicit owed_field always wins over this.';

-- ⛔ DELIBERATELY NOT SEEDED — the obvious link does not exist, and this is the record of why.
--
-- The intended seed was activation_order -> raw_ma_fulfillment.order_number. VERIFIED AGAINST PROD
-- BEFORE SHIPPING: it matches **0 of 1,192** luxelink commission rows. The two id spaces overlap in
-- range (340,022,255..352,490,218 vs 340,440,409..352,197,251) so a range check would have looked
-- fine — they are simply different orders. An ACTIVATION order is the line activation; a FULFILLMENT
-- order is the handset shipment. `raw_ma_fulfillment` carries no IMEI either, and neither does
-- `raw_ma_daily_tx` (which DOES bridge to fulfillment orders, 799 rows) — so there is no device
-- identity anywhere on the fulfillment path.
--
-- CONCLUSION: a per-IMEI payable CANNOT be priced from the handset fulfillment report. Seeding this
-- link would have written a config that silently produces nothing.
--
-- WHAT TO USE INSTEAD (found while verifying): `commcalc.raw_ma_daily_tx` already carries the
-- vendor's OWN due dates — `due_date` is populated on all 45,525 luxelink rows, spanning
-- 2026-02-02..2026-09-24, with $1,138,933.16 owed by the dealer against $961,262.30 paid to it. Daily
-- Owed does not need a ship+20 ESTIMATE at all; it needs to read the real dates at ORDER-LINE grain
-- (e.g. 2026-08-10: 473 lines, $45,855.02). That is a change to what the tab reads, not to this
-- column, and is tracked separately.
--
-- The column stays: it is the right generic mechanism for the moment real invoices land with both a
-- device key and an amount. It is simply left NULL until a join is verified to resolve.
