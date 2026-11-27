-- 1136_business_type_rep_tiles.sql — WHICH TILES A REP'S BUSINESS TYPE GIVES THEM (index §CORE-33.7,
-- owner reports 2026-11-27).
--
-- Owner, checking a rep of the NY LOGISTICS tenant (registered as The UPS Store):
--   "barcode resue does not belong to the wireless stors it is only for the ups store, also the
--    device history is also missing from alis dashbpard"
--   "every sales rep should be show which phones ar sititng in thier store inventory hwere they are
--    currenly clocked in so they an address those on a top priortiy"
--
-- Both are the same fact: a rep's tiles must follow WHAT KIND OF BUSINESS the tenant is, which this
-- table has declared since mig 1020 and which the sidebar has always obeyed. The landing page's tiles
-- now read it too (app/core/rep_surface.page_off -> core/verticals.href_hidden), so this migration is
-- the whole UPS/wireless split: two CONFIG rows, no vertical key branched on anywhere (RULE TWO).
--
--   1. wireless_retail HIDES /storeops/dropoff-barcode. The drop-off barcode rescue is a UPS-store
--      counter job; it was opened to reps on 2026-11-20 for that tenant and reached every tenant's
--      menu (and, from this morning, every tenant's rep tiles) because nothing said whose it was.
--      The UPS vertical keeps it.
--   2. ups_store HIDES /commcalc/asset/my-shelf, the new per-rep phone-aging page. A UPS store sells
--      no handsets: measured 2026-11-27, that tenant has 0 rows in commcalc.inventory_aging_device
--      while the three wireless tenants hold 1,402 / 1,654 / 455 units on hand. A tile that can only
--      ever read empty is worse than no tile.
--
-- Idempotent (UPDATEs to declared values), additive (no schema change), money-neutral: nothing here
-- reads or writes a rate, plan, payout or any calculation input. No relation is created, so no grant.
-- The byte-equal mirror is backend/app/modules/core/verticals.HOUSE_VERTICALS; harness_tenant_vertical.py
-- §A folds both statements over the mig-1020 seed in migration order.

UPDATE core.tenant_vertical
   SET nav_hidden = '{/storeops/dropoff-barcode}'
 WHERE key = 'wireless_retail';

UPDATE core.tenant_vertical
   SET nav_hidden = '{/pos/activations,/pos/activation-report,/hub/management-overview,/commcalc/sales-report,/commcalc/exec,/commcalc/sales-comparison,/commcalc/kpi-failing,/commcalc/zero-sales,/commcalc/flags,/commcalc/accessory-flags,/commcalc/chargebacks,/commcalc/commission-discrepancy,/commcalc/discrepancy,/commcalc/recovery,/commcalc/ingest-guard,/commcalc/pay-simulator,/commcalc$,/commcalc/custom-report,/commcalc/activations,/commcalc/schematic,/commcalc/reports-index,/commcalc/reports,/commcalc/kpi,/commcalc/device-history,/commcalc/ma-handsets,/commcalc/device-cost-recon,/commcalc/inventory-sold-recon,/commcalc/bill-payments,/commcalc/productivity,/commcalc/productivity-insights,/commcalc/coaching,/commcalc/sales-analyzer,/commcalc/whatif,/commcalc/comp-trend,/commcalc/commission-ledger,/commcalc/ma-commission,/commcalc/ma-overview-recon,/commcalc/commission-legs,/commcalc/accessory-cost-audit,/commcalc/expected-commission,/commcalc/daily-commission,/commcalc/imei-rebates,/commcalc/carrier-vs-pay,/commcalc/vendor-rebates,/commcalc/sales-recon,/commcalc/epay-fee-recon,/commcalc/imei-recon,/commcalc/carrier-recon,/commcalc/agency,/commcalc/commission-structure,/commcalc/payout-plans,/commcalc/commission-plans,/commcalc/management-incentive,/commcalc/plan-installments,/commcalc/payout-schedules,/commcalc/settings,/commcalc/carrier-mapping,/commcalc/commission-category-map,/commcalc/ma-product-class,/commcalc/accessory-definition,/commcalc/commission-import,/commcalc/targets$,/commcalc/financing,/commcalc/atu-opportunity,/employee,/commcalc/gp,/accounts/device-purchases,/accounts/device-payable,/accounts/residual-per-sub,/hub/assets,/commcalc/asset$,/commcalc/asset/marketplace-purchases,/commcalc/asset/dashboard,/commcalc/asset/owed-weekly,/commcalc/asset/aging,/commcalc/asset/missing-phones,/commcalc/asset/aging-rebate,/commcalc/asset/on-inventory,/commcalc/processor-ledger,/commcalc/asset/borrowed,/commcalc/asset/lending,/commcalc/asset/charges,/commcalc/asset/inventory-recon,/commcalc/asset/hotsheet-recon,/hub/distributors,/commcalc/distributors,/commcalc/vip,/closing/accessory-recon,/closing/billpay-pickup,/closing/epay-recon,/onboarding/intake,/commcalc/carrier-comm-file,/commcalc/epay,/commcalc/dlar,/commcalc/gp-category-map,/commcalc/asset/my-shelf}'
 WHERE key = 'ups_store';

-- REVERT: UPDATE core.tenant_vertical SET nav_hidden = '{}' WHERE key = 'wireless_retail';
-- REVERT: UPDATE core.tenant_vertical SET nav_hidden = '{/pos/activations,/pos/activation-report,/hub/management-overview,/commcalc/sales-report,/commcalc/exec,/commcalc/sales-comparison,/commcalc/kpi-failing,/commcalc/zero-sales,/commcalc/flags,/commcalc/accessory-flags,/commcalc/chargebacks,/commcalc/commission-discrepancy,/commcalc/discrepancy,/commcalc/recovery,/commcalc/ingest-guard,/commcalc/pay-simulator,/commcalc$,/commcalc/custom-report,/commcalc/activations,/commcalc/schematic,/commcalc/reports-index,/commcalc/reports,/commcalc/kpi,/commcalc/device-history,/commcalc/ma-handsets,/commcalc/device-cost-recon,/commcalc/inventory-sold-recon,/commcalc/bill-payments,/commcalc/productivity,/commcalc/productivity-insights,/commcalc/coaching,/commcalc/sales-analyzer,/commcalc/whatif,/commcalc/comp-trend,/commcalc/commission-ledger,/commcalc/ma-commission,/commcalc/ma-overview-recon,/commcalc/commission-legs,/commcalc/accessory-cost-audit,/commcalc/expected-commission,/commcalc/daily-commission,/commcalc/imei-rebates,/commcalc/carrier-vs-pay,/commcalc/vendor-rebates,/commcalc/sales-recon,/commcalc/epay-fee-recon,/commcalc/imei-recon,/commcalc/carrier-recon,/commcalc/agency,/commcalc/commission-structure,/commcalc/payout-plans,/commcalc/commission-plans,/commcalc/management-incentive,/commcalc/plan-installments,/commcalc/payout-schedules,/commcalc/settings,/commcalc/carrier-mapping,/commcalc/commission-category-map,/commcalc/ma-product-class,/commcalc/accessory-definition,/commcalc/commission-import,/commcalc/targets$,/commcalc/financing,/commcalc/atu-opportunity,/employee,/commcalc/gp,/accounts/device-purchases,/accounts/device-payable,/accounts/residual-per-sub,/hub/assets,/commcalc/asset$,/commcalc/asset/marketplace-purchases,/commcalc/asset/dashboard,/commcalc/asset/owed-weekly,/commcalc/asset/aging,/commcalc/asset/missing-phones,/commcalc/asset/aging-rebate,/commcalc/asset/on-inventory,/commcalc/processor-ledger,/commcalc/asset/borrowed,/commcalc/asset/lending,/commcalc/asset/charges,/commcalc/asset/inventory-recon,/commcalc/asset/hotsheet-recon,/hub/distributors,/commcalc/distributors,/commcalc/vip,/closing/accessory-recon,/closing/billpay-pickup,/closing/epay-recon,/onboarding/intake,/commcalc/carrier-comm-file,/commcalc/epay,/commcalc/dlar,/commcalc/gp-category-map}' WHERE key = 'ups_store';
