-- 1134_ups_store_nav_hidden_exact.sql — THE UPS-STORE HIDE-LIST: an exact entry for Daily Targets, and
-- three dashboards given back (index §CORE-06.5, owner report 2026-11-27).
--
-- Owner, in the NY LOGISTICS tenant: "i cannot see the targtet setting dashboard and the incentives
-- dashboard in ny logistcis - why is that and fix it".
--
-- Nothing was switched off for that tenant. `core.tenant_vertical.nav_hidden` for `ups_store` (mig 1020)
-- carried two over-reaches, and this migration closes both. It is a CONFIG row, not code: no vertical
-- key, page or tenant is branched on anywhere (RULE TWO).
--
--   1. `/commcalc/targets` was written WITHOUT the exact marker, and core/verticals.href_hidden reads a
--      bare entry as "this page AND everything under it". So one intended hide (Daily Targets) also hid
--      SIX pages nobody named: /commcalc/targets/settings (Target Settings — the owner's report),
--      /commcalc/targets/my, /commcalc/targets/report-cards, /commcalc/targets/action-plan,
--      /commcalc/targets/accessories and /commcalc/targets/rep-map. Now `/commcalc/targets$`, which
--      hides exactly the one page, the same form eight other entries in this list already use.
--
--   2. Three GROUP HUB dashboards were hidden while the groups kept pages this vertical uses. A hub is
--      the group's dashboard; hiding it hid no page by itself, and the pages below it are each declared
--      here on their own merits. Removed, so the dashboards the owner asked for exist:
--        /hub/incentives              — the group retains 6 pages for this vertical
--        /hub/incentive-payout-plans  — retains "Daily Incentive Rules" and "What a Day Pays", the
--                                       mig-1072 daily-incentive mechanism built FOR this vertical
--        /hub/targets-coaching        — retains Target Settings and ten more after (1) above
--      /hub/management-overview, /hub/assets and /hub/distributors stay hidden: not reported, and their
--      surviving pages reach the sidebar directly via the §CORE-06.5 render fix.
--
-- Idempotent (an UPDATE to a declared value), additive (no schema change), money-neutral: nothing here
-- reads or writes a rate, plan, payout or any calculation input. No relation is created, so no grant.
-- The byte-equal mirror is backend/app/modules/core/verticals.HOUSE_VERTICALS; harness_tenant_vertical.py
-- §A folds this statement over the mig-1020 seed exactly as it already folds later module re-scopes.

UPDATE core.tenant_vertical
   SET nav_hidden = '{/pos/activations,/pos/activation-report,/hub/management-overview,/commcalc/sales-report,/commcalc/exec,/commcalc/sales-comparison,/commcalc/kpi-failing,/commcalc/zero-sales,/commcalc/flags,/commcalc/accessory-flags,/commcalc/chargebacks,/commcalc/commission-discrepancy,/commcalc/discrepancy,/commcalc/recovery,/commcalc/ingest-guard,/commcalc/pay-simulator,/commcalc$,/commcalc/custom-report,/commcalc/activations,/commcalc/schematic,/commcalc/reports-index,/commcalc/reports,/commcalc/kpi,/commcalc/device-history,/commcalc/ma-handsets,/commcalc/device-cost-recon,/commcalc/inventory-sold-recon,/commcalc/bill-payments,/commcalc/productivity,/commcalc/productivity-insights,/commcalc/coaching,/commcalc/sales-analyzer,/commcalc/whatif,/commcalc/comp-trend,/commcalc/commission-ledger,/commcalc/ma-commission,/commcalc/ma-overview-recon,/commcalc/commission-legs,/commcalc/accessory-cost-audit,/commcalc/expected-commission,/commcalc/daily-commission,/commcalc/imei-rebates,/commcalc/carrier-vs-pay,/commcalc/vendor-rebates,/commcalc/sales-recon,/commcalc/epay-fee-recon,/commcalc/imei-recon,/commcalc/carrier-recon,/commcalc/agency,/commcalc/commission-structure,/commcalc/payout-plans,/commcalc/commission-plans,/commcalc/management-incentive,/commcalc/plan-installments,/commcalc/payout-schedules,/commcalc/settings,/commcalc/carrier-mapping,/commcalc/commission-category-map,/commcalc/ma-product-class,/commcalc/accessory-definition,/commcalc/commission-import,/commcalc/targets$,/commcalc/financing,/commcalc/atu-opportunity,/employee,/commcalc/gp,/accounts/device-purchases,/accounts/device-payable,/accounts/residual-per-sub,/hub/assets,/commcalc/asset$,/commcalc/asset/marketplace-purchases,/commcalc/asset/dashboard,/commcalc/asset/owed-weekly,/commcalc/asset/aging,/commcalc/asset/missing-phones,/commcalc/asset/aging-rebate,/commcalc/asset/on-inventory,/commcalc/processor-ledger,/commcalc/asset/borrowed,/commcalc/asset/lending,/commcalc/asset/charges,/commcalc/asset/inventory-recon,/commcalc/asset/hotsheet-recon,/hub/distributors,/commcalc/distributors,/commcalc/vip,/closing/accessory-recon,/closing/billpay-pickup,/closing/epay-recon,/onboarding/intake,/commcalc/carrier-comm-file,/commcalc/epay,/commcalc/dlar,/commcalc/gp-category-map}'
 WHERE key = 'ups_store';

-- REVERT: UPDATE core.tenant_vertical SET nav_hidden = '{/pos/activations,/pos/activation-report,/hub/management-overview,/commcalc/sales-report,/commcalc/exec,/commcalc/sales-comparison,/commcalc/kpi-failing,/commcalc/zero-sales,/commcalc/flags,/commcalc/accessory-flags,/commcalc/chargebacks,/commcalc/commission-discrepancy,/commcalc/discrepancy,/commcalc/recovery,/commcalc/ingest-guard,/hub/incentives,/commcalc/pay-simulator,/commcalc$,/commcalc/custom-report,/commcalc/activations,/commcalc/schematic,/commcalc/reports-index,/commcalc/reports,/commcalc/kpi,/commcalc/device-history,/commcalc/ma-handsets,/commcalc/device-cost-recon,/commcalc/inventory-sold-recon,/commcalc/bill-payments,/commcalc/productivity,/commcalc/productivity-insights,/commcalc/coaching,/commcalc/sales-analyzer,/commcalc/whatif,/commcalc/comp-trend,/commcalc/commission-ledger,/commcalc/ma-commission,/commcalc/ma-overview-recon,/commcalc/commission-legs,/commcalc/accessory-cost-audit,/commcalc/expected-commission,/commcalc/daily-commission,/commcalc/imei-rebates,/commcalc/carrier-vs-pay,/commcalc/vendor-rebates,/commcalc/sales-recon,/commcalc/epay-fee-recon,/commcalc/imei-recon,/commcalc/carrier-recon,/commcalc/agency,/hub/incentive-payout-plans,/commcalc/commission-structure,/commcalc/payout-plans,/commcalc/commission-plans,/commcalc/management-incentive,/commcalc/plan-installments,/commcalc/payout-schedules,/commcalc/settings,/commcalc/carrier-mapping,/commcalc/commission-category-map,/commcalc/ma-product-class,/commcalc/accessory-definition,/commcalc/commission-import,/hub/targets-coaching,/commcalc/targets,/commcalc/financing,/commcalc/atu-opportunity,/employee,/commcalc/gp,/accounts/device-purchases,/accounts/device-payable,/accounts/residual-per-sub,/hub/assets,/commcalc/asset$,/commcalc/asset/marketplace-purchases,/commcalc/asset/dashboard,/commcalc/asset/owed-weekly,/commcalc/asset/aging,/commcalc/asset/missing-phones,/commcalc/asset/aging-rebate,/commcalc/asset/on-inventory,/commcalc/processor-ledger,/commcalc/asset/borrowed,/commcalc/asset/lending,/commcalc/asset/charges,/commcalc/asset/inventory-recon,/commcalc/asset/hotsheet-recon,/hub/distributors,/commcalc/distributors,/commcalc/vip,/closing/accessory-recon,/closing/billpay-pickup,/closing/epay-recon,/onboarding/intake,/commcalc/carrier-comm-file,/commcalc/epay,/commcalc/dlar,/commcalc/gp-category-map}' WHERE key = 'ups_store';
