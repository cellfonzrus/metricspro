-- 1020_tenant_vertical.sql — THE TENANT VERTICAL: what KIND of business a tenant is (wireless retail, a
-- shipping/print franchise, …), declared once at onboarding and read by every gate. Band 1000+ (platform).
-- Additive + idempotent (safe to re-run). NOT money-touching: nothing here reads or writes a rate, plan,
-- payout or any calculation input — it decides WHICH modules and pages a tenant is shown.
--
-- OWNER (2026-09-24/25), verbatim (abridged): "UPS store … a new tenant … keep them gated from wireless
-- vendors if ups store is selected" · "Royalty report … only show up if a ups store is selected while
-- onboarding".
--
-- THE CLASS (CLAUDE.md "A fix is a DESIGN fix"): the platform had NO notion of tenant type. Every gate was
-- carrier-shaped (NAV_CARRIERS, report_kind / connector_registry applies_to_carrier), and a tenant with NO
-- carrier was shown EVERYTHING: `carrierOK` hides nothing on an empty carrier list, `defaultActiveCarrier`
-- falls back to one named carrier, and the generic wireless pages (incentives, activations, distributors)
-- were never carrier-tagged at all. A non-wireless tenant would therefore have seen the whole wireless
-- product. The fix is ONE declaration with ONE home, read by the existing gates — not a per-tenant patch.
--
-- DUPLICATE CHECK (build gate — searched, and why none of it is this):
--   · storeops.tenants.package_key (908)  — the PRICING plan picked at signup; pricing_package carries no
--                                           module list and nothing gates on it.
--   · report_kinds.tenant_declaration     — THE reader of POS + carriers; it has no business-type axis.
--                                           core/verticals.tenant_vertical is its sibling for this ONE
--                                           fact, and report_kinds/connector_registry are left untouched
--                                           (their carrier axis already withholds wireless connectors).
--   · core.module_catalog (700) + entitlements.effective_modules/module_enabled — the module gate. EXTENDED
--                                           here with `applies_to_vertical` rather than a second gate.
--   · frontend NAV_CARRIERS / carrierOK    — page gating by carrier. The vertical's hidden-page list lives in
--                                           DATA below (nav_hidden), so the frontend spells no vertical.
--
-- SHAPE:
--   core.tenant_vertical   — the vocabulary: key, label, is_default (exactly one: the value an unset tenant
--                            reads as — so every existing tenant keeps today's behaviour), uses_carriers
--                            (false ⇒ no active carrier and every carrier-tagged page hidden), nav_hidden
--                            (page hrefs this vertical does not see; 'x$' = exactly x, else x and x/…).
--   storeops.tenants.vertical — the tenant's declaration (NULL = the default vertical).
--   core.module_catalog.applies_to_vertical — '{}' = any vertical (every existing module, unchanged).
--
-- The code mirror is backend/app/modules/core/verticals.py HOUSE_VERTICALS (byte-equal, parsed back by
-- backend/harness_tenant_vertical.py §A) so the app answers identically before this migration runs.

CREATE TABLE IF NOT EXISTS core.tenant_vertical (
  key            TEXT PRIMARY KEY,
  label          TEXT NOT NULL,
  is_default     BOOLEAN NOT NULL DEFAULT false,
  uses_carriers  BOOLEAN NOT NULL DEFAULT true,
  nav_hidden     TEXT[] NOT NULL DEFAULT '{}',
  sort_order     INT NOT NULL DEFAULT 100,
  is_active      BOOLEAN NOT NULL DEFAULT true,
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tenant_vertical_one_default ON core.tenant_vertical (is_default) WHERE is_default;
ALTER TABLE core.tenant_vertical ENABLE ROW LEVEL SECURITY;

ALTER TABLE storeops.tenants ADD COLUMN IF NOT EXISTS vertical TEXT;
ALTER TABLE core.module_catalog ADD COLUMN IF NOT EXISTS applies_to_vertical TEXT[] NOT NULL DEFAULT '{}';

-- ── HOUSE SEED (mirrored byte-equal in core/verticals.py HOUSE_VERTICALS) ───────────────────────────────
INSERT INTO core.tenant_vertical (key, label, is_default, uses_carriers, nav_hidden, sort_order, notes) VALUES
  ('wireless_retail', 'Wireless retail', true, true, '{}', 10,
   'Carrier-authorised wireless retail. The default: every tenant created before mig 1020 reads as this.'),
  ('ups_store', 'The UPS Store (franchise)', false, false,
   '{/pos/activations,/pos/activation-report,/hub/management-overview,/commcalc/sales-report,/commcalc/exec,/commcalc/sales-comparison,/commcalc/kpi-failing,/commcalc/zero-sales,/commcalc/flags,/commcalc/accessory-flags,/commcalc/chargebacks,/commcalc/commission-discrepancy,/commcalc/discrepancy,/commcalc/recovery,/commcalc/ingest-guard,/hub/incentives,/commcalc/pay-simulator,/commcalc$,/commcalc/custom-report,/commcalc/activations,/commcalc/schematic,/commcalc/reports-index,/commcalc/reports,/commcalc/kpi,/commcalc/device-history,/commcalc/ma-handsets,/commcalc/device-cost-recon,/commcalc/inventory-sold-recon,/commcalc/bill-payments,/commcalc/productivity,/commcalc/productivity-insights,/commcalc/coaching,/commcalc/sales-analyzer,/commcalc/whatif,/commcalc/comp-trend,/commcalc/commission-ledger,/commcalc/ma-commission,/commcalc/ma-overview-recon,/commcalc/commission-legs,/commcalc/accessory-cost-audit,/commcalc/expected-commission,/commcalc/daily-commission,/commcalc/imei-rebates,/commcalc/carrier-vs-pay,/commcalc/vendor-rebates,/commcalc/sales-recon,/commcalc/epay-fee-recon,/commcalc/imei-recon,/commcalc/carrier-recon,/commcalc/agency,/hub/incentive-payout-plans,/commcalc/commission-structure,/commcalc/payout-plans,/commcalc/commission-plans,/commcalc/management-incentive,/commcalc/plan-installments,/commcalc/payout-schedules,/commcalc/settings,/commcalc/carrier-mapping,/commcalc/commission-category-map,/commcalc/ma-product-class,/commcalc/accessory-definition,/commcalc/commission-import,/hub/targets-coaching,/commcalc/targets,/commcalc/financing,/commcalc/atu-opportunity,/employee,/commcalc/gp,/accounts/device-purchases,/accounts/device-payable,/accounts/residual-per-sub,/hub/assets,/commcalc/asset$,/commcalc/asset/marketplace-purchases,/commcalc/asset/dashboard,/commcalc/asset/owed-weekly,/commcalc/asset/aging,/commcalc/asset/missing-phones,/commcalc/asset/aging-rebate,/commcalc/asset/on-inventory,/commcalc/processor-ledger,/commcalc/asset/borrowed,/commcalc/asset/lending,/commcalc/asset/charges,/commcalc/asset/inventory-recon,/commcalc/asset/hotsheet-recon,/hub/distributors,/commcalc/distributors,/commcalc/vip,/closing/accessory-recon,/closing/billpay-pickup,/closing/epay-recon,/onboarding/intake,/commcalc/carrier-comm-file,/commcalc/epay,/commcalc/dlar,/commcalc/gp-category-map}',
   20, 'Shipping / print / mailbox franchise. No carrier: activations, commission, device and distributor pages are wireless-only.')
ON CONFLICT (key) DO NOTHING;

-- ── MODULES THAT BELONG TO ONE VERTICAL ─────────────────────────────────────────────────────────────────
-- Mirrored in entitlements.MODULE_CATALOG (label) + core/verticals.HOUSE_MODULE_VERTICALS (scope).
INSERT INTO core.module_catalog (key, label, sort_order, applies_to_vertical) VALUES
  ('franchise_ops',   'Store Operations Dashboard',        140, '{ups_store}'),
  ('supply_ordering', 'Supply Ordering & Price Compare',   150, '{ups_store}'),
  ('royalty',         'Franchise Royalty & Cost Centers',  160, '{ups_store}')
ON CONFLICT (key) DO NOTHING;
UPDATE core.module_catalog SET applies_to_vertical = '{wireless_retail}'
 WHERE key = 'vip' AND applies_to_vertical = '{}';

SELECT 'Migration 1020 complete — core.tenant_vertical (2 house rows), storeops.tenants.vertical, module_catalog.applies_to_vertical (+3 franchise modules; vip → wireless)' AS status;

-- REVERT:
--   UPDATE core.module_catalog SET applies_to_vertical = '{}' WHERE key = 'vip';
--   DELETE FROM core.module_catalog WHERE key IN ('franchise_ops','supply_ordering','royalty');
--   ALTER TABLE core.module_catalog DROP COLUMN IF EXISTS applies_to_vertical;
--   ALTER TABLE storeops.tenants DROP COLUMN IF EXISTS vertical;
--   DROP TABLE IF EXISTS core.tenant_vertical;
