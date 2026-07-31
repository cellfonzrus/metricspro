// RBAC: nav definition (module-tagged) + access helpers. Permissions come from
// storeops.roles.permissions (resolved by the backend /core/me for the logged-in user).
export type Scope = 'all' | 'market' | 'store' | 'self'
export type Permissions = {
  modules?: Record<string, boolean>
  reports?: Record<string, boolean>   // per-AREA report access (separate from the operational module)
  data?: Record<string, boolean>      // per-KEY sensitive-data grants (e.g. carrier_residual) — see DATA_GRANTS
  pages?: Record<string, boolean>     // per-FUNCTION override (by nav href): explicit true/false wins over module
  scope?: Scope
  home?: string
}

// Report (analytical) pages are gated by a per-area `reports` permission that is SEPARATE from the
// operational module, so e.g. a market manager can run schedules/targets but see no reports.
// Operational pages (targets entry, schedule, My Team, time clock…) are NOT listed here.
export const REPORT_AREAS: { key: string; label: string }[] = [
  { key: 'commissions', label: 'Commission reports' },
  { key: 'asset', label: 'Asset reports' },
  { key: 'vip', label: 'Distributor reports' },
  { key: 'accounts', label: 'Accounting reports (P&L / BS)' },
  { key: 'storeops', label: 'StoreOps reports (hours / payroll)' },
  { key: 'closing', label: 'Daily Closing reports' },
]
// Exact report pages (a bare path that is a report but whose tree also holds non-reports).
const REPORT_EXACT: Record<string, string> = { '/commcalc': 'commissions', '/reports': '*' }
// Report page TREES (prefix → area), boundary-matched; longest prefix wins.
const REPORT_TREES: [string, string][] = [
  ['/commcalc/exec', 'commissions'], ['/commcalc/reports', 'commissions'], ['/commcalc/gp', 'commissions'],
  ['/commcalc/coaching', 'commissions'], ['/commcalc/sales-analyzer', 'commissions'],
  ['/commcalc/comp-trend', 'commissions'], ['/commcalc/flags', 'commissions'], ['/commcalc/chargebacks', 'commissions'],
  ['/commcalc/discrepancy', 'commissions'], ['/commcalc/sales-recon', 'commissions'],
  ['/commcalc/asset', 'asset'], ['/commcalc/vip', 'vip'], ['/accounts', 'accounts'],
  ['/storeops/reports', 'storeops'], ['/storeops/payroll', 'storeops'], ['/storeops/payroll-tax', 'storeops'],
  ['/closing/recon', 'closing'],
]
// The report area for a path, or null if it's an operational (non-report) page.
export function reportAreaForPath(path: string): string | null {
  if (REPORT_EXACT[path]) return REPORT_EXACT[path]
  let best: string | null = null, bestLen = -1
  for (const [pre, area] of REPORT_TREES) {
    if ((path === pre || path.startsWith(pre + '/')) && pre.length > bestLen) { best = area; bestLen = pre.length }
  }
  return best
}
// May this user see report area `area`? Explicit `reports` config wins; otherwise default by scope —
// company-wide ('all') leadership keeps reports, everyone else (market/store/self) gets none. So a
// market manager has NO default report access, while admins/execs keep theirs, with no re-seeding.
export function hasReport(perms: Permissions, area: string): boolean {
  if (isSuperAdmin(perms)) return true
  const r = perms.reports
  if (r && Object.keys(r).length) {
    return area === '*' ? Object.values(r).some(Boolean) : !!r[area]
  }
  return (perms.scope || 'all') === 'all'
}

// ── Sensitive-data grants (separate from module/report access) ────────────────────────────────────
// Some data is carrier/finance-sensitive and gated by its OWN per-key grant, independent of whether a
// role can open the operational module. `carrier_residual` is the first: the raw_mi-derived carrier
// residual reports (backend commcalc `_can_view_carrier_residual`, commission-0 P1) are visible to
// everyone by default (tenant setting residual_visibility='all'), but a tenant that flips it to
// 'permissioned' requires this grant. Company-wide leadership (scope 'all') and admins always pass;
// grant it here to let a scoped manager see it too. Register a new sensitive-data key by adding a row
// AND gating the matching backend read (same contract as REPORT_AREAS + settings).
export const DATA_GRANTS: { key: string; label: string; help?: string }[] = [
  { key: 'carrier_residual', label: 'Carrier residual (raw carrier data)',
    help: 'Raw carrier/processor residual reports (raw_mi-derived). Only enforced when the tenant sets residual visibility to "permissioned".' },
  { key: 'device_commission', label: 'Device history commission amounts',
    help: 'Per-period commission & rebate $ on the Device History Lookup (backend commcalc `_can_view_device_commission`). DEFAULT-CLOSED — admin-only until granted; the device history / prompts / tenure stay visible to everyone regardless.' },
  { key: 'imei_rebates', label: 'IMEI rebate reconciliation report',
    help: 'Access to /commcalc/imei-rebates — the whole report, not just the $ (backend commcalc `_can_view_imei_rebates`). DEFAULT-CLOSED — admin-only until granted; money columns additionally ride the carrier-residual gate when the tenant sets residual visibility to "permissioned".' },
  { key: 'residual_per_sub', label: 'Residual per Subscriber report',
    help: 'Access to /accounts/residual-per-sub. DEFAULT-CLOSED — admin-only until granted.' },
  { key: 'account_trends', label: 'Trends report (all metrics)',
    help: 'Access to /accounts/trends. DEFAULT-CLOSED — admin-only until granted.' },
  { key: 'ma_handset_cogs', label: 'Marketplace handset COGS report',
    help: 'Access to /commcalc/ma-handsets — the whole report (lines, quantities and handset costs), not just the totals (backend commcalc `_can_view_ma_handset_cogs`). DEFAULT-CLOSED — admin-only until granted.' },
  { key: 'device_cost_recon', label: 'Device cost reconciliation',
    help: 'Access to /commcalc/device-cost-recon — every device-cost row from all four sources at once (marketplace purchase price, consignment/VIP billing, POS-derived cost, inventory unit cost) plus the policy delta preview (backend commcalc `_can_view_device_cost_recon`). DEFAULT-CLOSED — admin-only until granted. Strictly MORE sensitive than the per-source reports it reconciles.' },
]
// Frontend mirror of backend commcalc `_can_view_carrier_residual` — KEEP IN SYNC. Super-admins /
// company-wide ('all') roles / admins always pass; otherwise the grant is honored under either the
// `data` bucket (what the roles UI writes) or a `modules` key of the same name (backend also accepts it).
export function hasDataGrant(perms: Permissions, key: string): boolean {
  if (isSuperAdmin(perms)) return true
  if ((perms.scope || 'all') === 'all') return true
  if (perms.modules?.[key]) return true
  if (perms.data?.[key]) return true
  return false
}

// `cap` (optional) is a tenant CAPABILITY gate, separate from RBAC: the sidebar hides the item only when
// the tenant's capability is explicitly false (e.g. asset_lending=false → no consignment distributor).
// Unknown/true → shown, so it never hides anything by default. RBAC (module/scopes) still applies first.
export type NavItem = { href: string; label: string; icon: string; module: string; scopes?: Scope[]; cap?: string }
export type NavGroup = { group: string; module: string; items: NavItem[] }

// scopes (when present) further restricts an item to those scope tiers, e.g. settings = admin only.
// ── NAV taxonomy (reorganized 2026-06-28) ──────────────────────────────────────────────
// Grouping is purely an information-architecture concern: a group renders if ANY of its
// items passes canSeeItem(), which keys on item.module — so regrouping/relabeling here is a
// ZERO-RBAC-CHANGE operation as long as each item keeps its existing `module` + `scopes`.
// group.module is a representative tag only (not a gate). The old single /commcalc menu was a
// catch-all spanning Commissions / Finance / Assets / VIP / Targets / Integrations; it's split
// into those real domains below. Pages keep their URLs (a deeper re-home is a separate phase).
export const NAV: NavGroup[] = [
  { group: 'Reports', module: 'targets', items: [
    { href: '/reports', label: 'Report Center', icon: '📊', module: 'targets' },
  ]},
  { group: 'Commissions', module: 'commissions', items: [
    { href: '/commcalc', label: 'Dashboard', icon: '📊', module: 'commissions' },
    { href: '/commcalc/sales-report', label: 'Sales Report', icon: '🧾', module: 'commissions' },
    { href: '/commcalc/custom-report', label: 'Custom Report', icon: '🧩', module: 'commissions' },
    { href: '/commcalc/exec', label: 'Owner Overview', icon: '🏆', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/exec/mtd', label: 'Executive MTD', icon: '📅', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/reports', label: 'Rep Commission Report', icon: '📋', module: 'commissions' },
    { href: '/commcalc/kpi', label: 'KPI Metrics', icon: '🎯', module: 'commissions' },
    { href: '/commcalc/device-history', label: 'Device History', icon: '📱', module: 'commissions' },
    { href: '/commcalc/ma-handsets', label: 'Handset COGS', icon: '📦', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/device-cost-recon', label: 'Device Cost Recon', icon: '🧮', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/productivity', label: 'Productivity & Reviews', icon: '🏅', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/coaching', label: 'Rep Coaching', icon: '🎓', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/sales-analyzer', label: 'Sales Analyzer', icon: '📉', module: 'commissions', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/whatif', label: 'What‑If Analysis', icon: '🔮', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/comp-trend', label: 'Total Compensation', icon: '📡', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/commission-ledger', label: 'Commission Ledger', icon: '🧾', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/ma-commission', label: 'Total Processor', icon: '📡', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/flags', label: 'Flags', icon: '🚩', module: 'commissions' },
    { href: '/commcalc/chargebacks', label: 'Chargebacks & Fraud', icon: '🔻', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/accessory-flags', label: 'Accessory Flags', icon: '🔖', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/discrepancy', label: 'Pay Discrepancy', icon: '⚠️', module: 'commissions' },
    { href: '/commcalc/imei-rebates', label: 'IMEI Rebates', icon: '🔁', module: 'commissions' },
    { href: '/commcalc/recovery', label: 'Appeal Recovery', icon: '💰', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/sales-recon', label: 'Sales Feed Recon', icon: '🔁', module: 'commissions', scopes: ['all', 'market'] },
    // Agency (Master/Sub-Agent) console — config + billing, admin/owner scope only (NEEDS CORE for
    // agency-phase1). Intentionally NOT in REPORT_DIRECTORY: it is a config+invoicing surface, not a report.
    { href: '/commcalc/agency', label: 'Agency', icon: '🏢', module: 'commissions', scopes: ['all'] },
  ]},
  // ── Commission Payout Plans ────────────────────────────────────────────────────────────────
  // ONE home for HOW reps get paid, per carrier. 'Overview' maps each enabled carrier to the engine
  // that actually pays it (Boost KPI-tier rates vs configurable Commission Plans / Payout Schedules).
  // Boost Rates is carrier-gated to Boost tenants (NAV_CARRIERS) so a Total-only tenant never sees the
  // hardcoded Boost tiers. Regroup is a ZERO-RBAC-CHANGE move — every item keeps its module + scopes.
  { group: 'Commission Payout Plans', module: 'commissions', items: [
    { href: '/commcalc/payout-plans', label: 'Overview', icon: '💳', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/commission-plans', label: 'Commission Plans', icon: '🧮', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/plan-installments', label: 'Multi‑Month Installments', icon: '🗓️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/payout-schedules', label: 'Payout Schedules', icon: '📆', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/settings', label: 'Boost Rates (KPI‑tier)', icon: '⚙️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/carrier-mapping', label: 'Carrier Mapping', icon: '📡', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/commission-category-map', label: 'Category → Bucket Map', icon: '🗺️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/ma-product-class', label: 'MA Product Name Classification', icon: '🏷️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/commission-import', label: 'Import Wizard', icon: '🪄', module: 'commissions', scopes: ['all'] },
  ]},
  { group: 'Targets & Coaching', module: 'targets', items: [
    { href: '/commcalc/targets', label: 'Daily Targets', icon: '📈', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/action-plan', label: 'Action Plan', icon: '✅', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/accessories', label: 'Accessory Targets', icon: '🔖', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/rep-map', label: 'Rep → Store Map', icon: '🗺️', module: 'targets', scopes: ['all', 'market'] },
    { href: '/commcalc/targets/settings', label: 'Target Settings', icon: '🎚️', module: 'targets', scopes: ['all'] },
    { href: '/commcalc/targets/my', label: 'My Targets', icon: '🙋', module: 'targets' },
    { href: '/employee', label: 'Employee Dashboard', icon: '🧑‍💼', module: 'targets' },
  ]},
  { group: 'Finance', module: 'accounts', items: [
    { href: '/accounts', label: 'Dashboard', icon: '💼', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/trends', label: 'Trends', icon: '📊', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/pl', label: 'P&L Statement', icon: '📈', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/commcalc/gp', label: 'Gross Profit', icon: '💰', module: 'commissions' },
    { href: '/commcalc/expenses', label: 'Store Expenses', icon: '🏪', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/tax-collected', label: 'Tax Collected', icon: '🧾', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/accounts/balance-sheet', label: 'Balance Sheet', icon: '⚖️', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/inventory', label: 'Inventory Values', icon: '📦', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/recon', label: 'Reconciliation', icon: '🔎', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/residual-per-sub', label: 'Residual per Subscriber', icon: '📉', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/journal', label: 'Journal', icon: '📒', module: 'accounts', scopes: ['all'] },
    { href: '/accounts/companies', label: 'Companies', icon: '🏢', module: 'accounts', scopes: ['all'] },
  ]},
  { group: 'Assets', module: 'asset', items: [
    { href: '/commcalc/asset', label: 'Asset Ledger', icon: '📦', module: 'asset' },
    // MA / VidaPay marketplace-purchase orders (mig 207). First-class nav entry per mod-asset NEEDS CORE
    // [asset-10] — was reachable only via a button on the VIP-styled landing. carrier-gated to Total in
    // NAV_CARRIERS (the one asset page that applies to luxelink/Total, and only to them).
    { href: '/commcalc/asset/marketplace-purchases', label: 'Marketplace Purchases', icon: '🛒', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/dashboard', label: 'Charges Dashboard', icon: '📊', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/owed-weekly', label: 'Weekly Owed-to-Distributor', icon: '📅', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/aging', label: 'Inventory Aging', icon: '⏳', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/missing-phones', label: 'Missing Phones', icon: '📵', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/aging-rebate', label: 'Aging · Rebate Received', icon: '💵', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/on-inventory', label: 'On-Inventory by Store', icon: '🏬', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/payables', label: 'Forecasting & Vendor Payables', icon: '📱', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/borrowed', label: 'Borrowed / Lending', icon: '🔁', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/lending', label: 'Asset Lending (PayGo)', icon: '📲', module: 'asset', scopes: ['all', 'market'], cap: 'asset_lending' },
    { href: '/commcalc/asset/charges/rma', label: 'RMA', icon: '↩️', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/inventory-recon', label: 'Inventory Recon', icon: '🔎', module: 'asset', scopes: ['all', 'market'] },
    // Purchase Orders (mig 301) — proposed PO → receiving → sold tally → unsold aging. First-class nav
    // entry per mod-asset NEEDS CORE [asset-11] (was reachable only via a button on /commcalc/asset).
    // Carrier-NEUTRAL by design (buying/receiving is universal) → deliberately NOT in NAV_CARRIERS.
    // scopes ['all','market'] mirrors every sibling asset item: the PO endpoints are ORG-scoped, not
    // store-scoped, so a store-scoped user would see other stores' POs. If store-level receiving is
    // wanted, mod-asset should store-scope the reads first and then file for the scope widening.
    { href: '/commcalc/asset/purchase-orders', label: 'Purchase Orders', icon: '📦', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/hotsheet-recon', label: 'Pricing Hotsheet', icon: '🏷️', module: 'commissions', scopes: ['all', 'market'] },
  ]},
  { group: 'Distributors', module: 'vip', items: [
    { href: '/commcalc/distributors', label: 'Distributors', icon: '🏬', module: 'vip', scopes: ['all', 'market'] },
    { href: '/commcalc/vip', label: 'Distributor · Invoices', icon: '🧾', module: 'vip' },
    { href: '/commcalc/vip/paygo', label: 'Distributor · PayGo / Asset Lending', icon: '📲', module: 'vip', scopes: ['all', 'market'], cap: 'asset_lending' },
    { href: '/commcalc/vip/sweep', label: 'Distributor · Sweep', icon: '🧹', module: 'vip', scopes: ['all'] },
  ]},
  { group: 'Workforce', module: 'storeops', items: [
    { href: '/storeops', label: 'Dashboard', icon: '🏠', module: 'storeops' },
    { href: '/storeops/schedule', label: 'Schedule', icon: '📅', module: 'storeops' },
    { href: '/storeops/timeoff', label: 'Time Off', icon: '🌴', module: 'storeops' },
    { href: '/storeops/swaps', label: 'Shift Swaps', icon: '🔄', module: 'storeops' },
    { href: '/storeops/shift-extensions', label: 'Shift Extensions', icon: '⏱️', module: 'storeops', scopes: ['all', 'market', 'store'] },
    { href: '/storeops/hours-budget', label: 'Hours Budget', icon: '📊', module: 'storeops', scopes: ['all', 'market', 'store'] },
    { href: '/storeops/timeclock', label: 'Time Clock', icon: '⏱️', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/employees', label: 'Employees', icon: '👥', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/team', label: 'My Team', icon: '🫂', module: 'storeops', scopes: ['all', 'market', 'store'] },
    { href: '/storeops/visits', label: 'Store Visits', icon: '📝', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/visits/settings', label: 'Visit Checklist', icon: '🧾', module: 'storeops', scopes: ['all'] },
    { href: '/storeops/reports', label: 'Reports', icon: '📋', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/admin', label: 'Admin', icon: '🛠️', module: 'storeops', scopes: ['all', 'market'] },
  ]},
  { group: 'Payroll & HR', module: 'storeops', items: [
    { href: '/hr/people', label: 'People (add employees)', icon: '🧑‍💼', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr/onboarding', label: 'Onboarding Checklist', icon: '🧩', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr/compliance', label: 'Compliance', icon: '📋', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr/employee-database', label: 'Employee Database', icon: '🗄️', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr/letters', label: 'HR Communications', icon: '✉️', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr', label: 'HR · Total Comp', icon: '📊', module: 'hr', scopes: ['all', 'market'] },
    { href: '/hr/payroll-expenses', label: 'Payroll Expenses', icon: '💼', module: 'hr', scopes: ['all', 'market'] },
    { href: '/storeops/payroll', label: 'Payroll', icon: '💵', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/payroll-tax', label: 'Payroll (Tax)', icon: '🧾', module: 'storeops', scopes: ['all', 'market'] },
  ]},
  { group: 'Daily Closing', module: 'closing', items: [
    { href: '/closing', label: 'Dashboard', icon: '🧾', module: 'closing', scopes: ['all', 'market', 'store'] },
    { href: '/closing/submit', label: 'Submit Closing', icon: '➕', module: 'closing' },
    { href: '/closing/verify', label: 'DM Verify', icon: '✅', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/management', label: 'Management Review', icon: '🛡️', module: 'closing' },
    { href: '/closing/recon', label: 'Reconciliation', icon: '🔎', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/tender-recon', label: 'X-Tender Recon', icon: '🧾', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/tender-recon-3way', label: '3-Way Tender Recon', icon: '🧮', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/accessory-recon', label: 'Accessory Recon', icon: '🔖', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/pickup', label: 'Cash Pickup', icon: '💵', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/epay-recon', label: 'ePay Bank-Deposit Recon', icon: '🏦', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/cash-config', label: 'Cash Setup', icon: '⚙️', module: 'closing', scopes: ['all'] },
    { href: '/closing/tender-config', label: 'Tender Setup', icon: '🧾', module: 'closing', scopes: ['all'] },
    { href: '/closing/imports', label: 'Auto-Import', icon: '🔄', module: 'closing', scopes: ['all'] },
  ]},
  { group: 'Integrations & Imports', module: 'commissions', items: [
    { href: '/commcalc/connectors', label: 'Connectors', icon: '🔌', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/onboarding', label: 'Onboarding Wizard', icon: '🚀', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/implementation', label: 'Implementation Wizard', icon: '🧭', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/upload', label: 'Upload Files', icon: '📁', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/upload/wizard', label: 'Upload Wizard', icon: '🧭', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/carrier-comm-file', label: 'Carrier Comm File → Table', icon: '📑', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/ftp-imports', label: 'FTP Auto-Import', icon: '🔁', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/email-imports', label: 'Email & Portal Logins (2FA)', icon: '📨', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/epay/sweep', label: 'Payment Processor Sync', icon: '🧹', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/dlar/sweep', label: 'Metrics Rep/Store Sync', icon: '🧹', module: 'commissions', scopes: ['all'] },
  ]},
  { group: 'Mapping', module: 'commissions', items: [
    { href: '/commcalc/mapping', label: 'All Mappings', icon: '🗂️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/store-match', label: 'Store Matching', icon: '🏬', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/column-mapping', label: 'Column Mapping', icon: '🧩', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/target-fields', label: 'Custom Target Fields', icon: '🧱', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/gp-category-map', label: 'GP Category Map', icon: '💰', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/item-mapping', label: 'Item / Model Mapping', icon: '🧩', module: 'commissions', scopes: ['all'] },
    // Catalog Categories (migs 230/231) — the per-tenant category-override layer on top of the uploaded
    // product catalog; feeds catalog-driven accessory classification. Nav entry per mod-commission
    // NEEDS CORE (catalog-accessory-byod). Filed under Mapping, not Commissions: it is a mapping/config
    // surface like GP Category Map / Item Mapping, so it is also intentionally NOT in REPORT_DIRECTORY
    // (config pages are excluded) and has no report-area gate. Carrier-NEUTRAL — the page accepts BOTH
    // the house (product-ID) and TOTAL (UPC) catalog files, so it must NOT go in NAV_CARRIERS.
    { href: '/commcalc/catalog', label: 'Catalog Categories', icon: '🏷️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/rep-aliases', label: 'Rep Aliases', icon: '🔗', module: 'commissions', scopes: ['all'] },
  ]},
  { group: 'Notify', module: 'notify', items: [
    { href: '/notify', label: 'Notify', icon: '📤', module: 'notify' },
    { href: '/notify/report-recipients', label: 'Report Recipients', icon: '📬', module: 'notify' },
  ]},
  { group: 'Helpdesk', module: 'helpdesk', items: [
    { href: '/helpdesk', label: 'Tickets', icon: '🎫', module: 'helpdesk' },
    { href: '/helpdesk/new', label: 'Raise a Ticket', icon: '➕', module: 'helpdesk' },
    { href: '/helpdesk/dashboard', label: 'Dashboard', icon: '📊', module: 'helpdesk', scopes: ['all', 'market', 'store'] },
    { href: '/helpdesk/settings', label: 'Settings', icon: '⚙️', module: 'helpdesk', scopes: ['all'] },
    { href: '/remediation', label: 'Auto-Remediation', icon: '🤖', module: 'helpdesk', scopes: ['all', 'market'] },
  ]},
  // Tech Support (mig 715) — the HOUSE support team's cross-tenant console + per-page help docs. Gated on
  // module 'support' (NOT 'admin'): support agents need not be admins. The console's backend endpoints are
  // additionally house-gated + super-admin-only cross-tenant, so a tenant user granted the module still
  // can't read another tenant's cases.
  { group: 'Support', module: 'support', items: [
    { href: '/admin/support', label: 'Support Console', icon: '🎧', module: 'support', scopes: ['all', 'market'] },
    { href: '/admin/support/failures', label: 'Fleet Failure Triage', icon: '🩺', module: 'support', scopes: ['all', 'market'] },
    { href: '/admin/support/fix-requests', label: 'Fix Requests', icon: '🛠️', module: 'support', scopes: ['all', 'market'] },
    { href: '/admin/support/docs', label: 'Help Docs', icon: '📚', module: 'support', scopes: ['all'] },
  ]},
  { group: 'Configuration', module: 'admin', items: [
    { href: '/configurations', label: 'All Settings', icon: '⚙️', module: 'admin' },
    { href: '/admin/tenants', label: 'Companies (Tenants)', icon: '🏢', module: 'admin' },
    { href: '/admin/tenant-settings', label: 'Pay Period & Work-Week', icon: '📅', module: 'admin' },
    { href: '/admin/billing', label: 'Billing (Tenants)', icon: '💳', module: 'admin' },
    { href: '/admin/roles', label: 'Roles & Access', icon: '🔐', module: 'admin' },
    { href: '/admin/security', label: 'Security Settings', icon: '🛡️', module: 'admin' },
    { href: '/admin/org', label: 'Org Structure', icon: '🌳', module: 'admin' },
    { href: '/admin/org-chart', label: 'Employee Org Chart', icon: '👥', module: 'admin' },
    { href: '/admin/labels', label: 'Display Labels', icon: '🏷️', module: 'admin' },
    { href: '/admin/menu', label: 'Menu Layout', icon: '🧭', module: 'admin' },
    { href: '/failures', label: 'Failure Logs', icon: '🩺', module: 'admin' },
    // Import Health (mig 717) — the universal import-freshness registry behind the admin login popup.
    // module 'admin' + no `scopes`, IDENTICAL to its Failure Logs sibling: an existing admin role already
    // carries modules.admin, so this adds no new permission surface and needs no SEED_VERSION bump.
    // Deliberately NOT in REPORT_DIRECTORY — it edits import schedules, and the directory excludes
    // config/entry surfaces by design (one line to add if the operator prefers it listed).
    { href: '/admin/import-health', label: 'Import Health', icon: '📡', module: 'admin' },
    // Auto-Fix Pipeline board (mig 718) — the fix-request registry + AI token/$ accounting. Tagged
    // module 'admin' with NO `scopes`, byte-identical in shape to its /admin/tenants sibling: the PAGE
    // itself is super-admin-only (it renders an explainer for anyone else, and every backend endpoint
    // 403s a non-super-admin independently), so this nav line adds no new permission surface and needs
    // no SEED_VERSION bump for roles. Deliberately NOT a new module key: it is a platform surface, not
    // a billable tenant module.
    { href: '/admin/fix-requests', label: 'Auto-Fix Pipeline', icon: '🛠️', module: 'admin' },
  ]},
]

// Per-item override: `group` = the item's PRIMARY group (a MOVE); `also` = ADDITIONAL groups the SAME
// item also appears in (DUPLICATE links to the same href — never a second permission surface); `hidden`
// removes it everywhere. `groups` = admin-created group names that may have no items yet (kept so the
// designer can show an empty group); the sidebar ignores empty groups. BACKWARD-COMPATIBLE: a legacy
// layout carrying only `{group?, hidden?}` behaves exactly as before (`also`/`groups` absent → no-op).
// `hideReportsDirectory` (optional, per-tenant) suppresses the built-in Reports directory (below) for a
// tenant that doesn't want the duplicate categorized copies. Default OFF (every tenant gets the directory).
// It flows through the existing nav-layout JSON; wiring a designer toggle for it is an optional follow-up.
export type NavLayout = {
  items?: Record<string, { group?: string; hidden?: boolean; also?: string[] }>
  groups?: string[]
  hideReportsDirectory?: boolean
}

// ── Reports directory (OWNER DIRECTIVE 2026-07-17) ────────────────────────────────────────────────
// Every report/list/dashboard surface ALSO appears — as a DUPLICATE entry — under a categorized
// "Reports · <Category>" area, WITHOUT leaving its own module group. This is a CODE-LEVEL DEFAULT
// (no per-tenant rows, no migration): applyNavLayout injects each surviving report item into its
// category, reusing the SAME NavItem object AFTER access filtering, so every copy is byte-identically
// RBAC/carrier/entitlement-gated (never a second permission surface). A tenant whose role lacks module X
// therefore sees NO X entry in Reports either; a tenant admin can still hide/move any item on /admin/menu
// (a hide removes it from Reports too), or suppress the whole area via `hideReportsDirectory`.
// ONLY analytical reports + operational lists/dashboards are listed here — pure config/mapping/import/
// settings/entry pages are intentionally EXCLUDED (they are inputs, not reports; same exemption as
// RULES FOUR/FIVE). Category ORDER below is the sidebar order (categories render after the module groups).
export const REPORT_CATEGORIES: { key: string; label: string }[] = [
  { key: 'sales',    label: 'Reports · Sales' },
  { key: 'comm',     label: 'Reports · Commissions & Pay' },
  { key: 'targets',  label: 'Reports · Targets & Coaching' },
  { key: 'assets',   label: 'Reports · Assets & Inventory' },
  { key: 'finance',  label: 'Reports · Finance & Accounting' },
  { key: 'payroll',  label: 'Reports · Payroll & HR' },
  { key: 'ops',      label: 'Reports · Store Operations' },
  { key: 'admin',    label: 'Reports · Admin & System' },
]
// href → category KEY. Ordered by category (drives intra-area order). A duplicate is only rendered when
// the SAME href survives access filtering in a module group, so this map never widens visibility.
export const REPORT_DIRECTORY: [string, string][] = [
  // Sales
  ['/commcalc/sales-report', 'sales'], ['/commcalc/custom-report', 'sales'],
  ['/commcalc/sales-analyzer', 'sales'], ['/commcalc/sales-recon', 'sales'],
  // Commissions & Pay
  ['/commcalc', 'comm'], ['/commcalc/exec', 'comm'], ['/commcalc/exec/mtd', 'comm'],
  ['/commcalc/reports', 'comm'], ['/commcalc/comp-trend', 'comm'], ['/commcalc/commission-ledger', 'comm'],
  ['/commcalc/ma-commission', 'comm'], ['/commcalc/device-history', 'comm'], ['/commcalc/whatif', 'comm'],
  ['/commcalc/discrepancy', 'comm'], ['/commcalc/recovery', 'comm'], ['/commcalc/flags', 'comm'],
  ['/commcalc/chargebacks', 'comm'], ['/commcalc/accessory-flags', 'comm'],
  // Targets & Coaching
  ['/commcalc/targets', 'targets'], ['/commcalc/targets/action-plan', 'targets'],
  ['/commcalc/targets/accessories', 'targets'], ['/commcalc/targets/my', 'targets'],
  ['/commcalc/kpi', 'targets'], ['/commcalc/productivity', 'targets'], ['/commcalc/coaching', 'targets'],
  // Assets & Inventory (incl. Distributors/VIP consignment)
  ['/commcalc/asset', 'assets'], ['/commcalc/asset/dashboard', 'assets'], ['/commcalc/asset/owed-weekly', 'assets'],
  ['/commcalc/asset/aging', 'assets'], ['/commcalc/asset/missing-phones', 'assets'],
  ['/commcalc/asset/aging-rebate', 'assets'], ['/commcalc/asset/on-inventory', 'assets'],
  ['/commcalc/asset/borrowed', 'assets'], ['/commcalc/asset/lending', 'assets'],
  ['/commcalc/asset/charges/rma', 'assets'], ['/commcalc/asset/inventory-recon', 'assets'],
  ['/commcalc/asset/hotsheet-recon', 'assets'], ['/commcalc/asset/marketplace-purchases', 'assets'],
  ['/commcalc/payables', 'assets'],
  ['/commcalc/distributors', 'assets'], ['/commcalc/vip', 'assets'], ['/commcalc/vip/paygo', 'assets'],
  // Finance & Accounting
  ['/accounts', 'finance'], ['/accounts/trends', 'finance'], ['/accounts/pl', 'finance'],
  ['/accounts/balance-sheet', 'finance'], ['/accounts/inventory', 'finance'], ['/accounts/recon', 'finance'],
  ['/accounts/residual-per-sub', 'finance'], ['/accounts/journal', 'finance'],
  ['/commcalc/gp', 'finance'], ['/commcalc/expenses', 'finance'], ['/commcalc/tax-collected', 'finance'],
  // Payroll & HR
  ['/hr', 'payroll'], ['/hr/people', 'payroll'], ['/hr/onboarding', 'payroll'], ['/hr/compliance', 'payroll'],
  ['/hr/payroll-expenses', 'payroll'], ['/storeops/payroll', 'payroll'], ['/storeops/payroll-tax', 'payroll'],
  ['/admin/org-chart', 'payroll'],
  // Store Operations (workforce + daily closing)
  ['/storeops', 'ops'], ['/storeops/schedule', 'ops'], ['/storeops/timeoff', 'ops'], ['/storeops/swaps', 'ops'],
  ['/storeops/shift-extensions', 'ops'], ['/storeops/hours-budget', 'ops'], ['/storeops/timeclock', 'ops'],
  ['/storeops/employees', 'ops'], ['/storeops/team', 'ops'], ['/storeops/visits', 'ops'], ['/storeops/reports', 'ops'],
  ['/closing', 'ops'], ['/closing/verify', 'ops'], ['/closing/management', 'ops'], ['/closing/recon', 'ops'],
  ['/closing/tender-recon', 'ops'], ['/closing/tender-recon-3way', 'ops'], ['/closing/accessory-recon', 'ops'],
  ['/closing/pickup', 'ops'], ['/closing/epay-recon', 'ops'],
  // Admin & System
  ['/failures', 'admin'], ['/helpdesk', 'admin'], ['/helpdesk/dashboard', 'admin'], ['/remediation', 'admin'],
  ['/admin/tenants', 'admin'],
  // The fix-request board IS a report surface (status board + rollup tile + full ReportShell export set),
  // so it belongs in the directory — same treatment as its super-admin-only sibling /admin/tenants, and
  // unlike the pure config surfaces (Import Health, Roles & Access), which are deliberately excluded.
  // NOTE: do not name those config hrefs literally here — prove_import_health_nav.mjs asserts the
  // directory block does not CONTAIN that string, and a mention in a comment would trip it.
  ['/admin/fix-requests', 'admin'],
]
const REPORT_CATEGORY_LABEL: Record<string, string> = Object.fromEntries(REPORT_CATEGORIES.map(c => [c.key, c.label]))

// Apply a per-org sidebar layout (admin config) ON TOP of the already-filtered groups: move an item to
// a different group, DUPLICATE it into additional groups (`also`), or hide it. Items with no override
// keep their built-in group. Default group order is preserved; a brand-new group name lands at the end.
// A DUPLICATE reuses the SAME NavItem object — and applyNavLayout runs AFTER access filtering, so an item
// only exists here if it passed canSeeItem/carrierOK/capOK, meaning every copy is identically RBAC-gated.
// Returns groups ready to render. Empty groups (a created group with no items) are dropped from the
// sidebar; `layout.groups` is intentionally ignored here (it exists only for the designer's persistence).
export function applyNavLayout(groups: NavGroup[], layout?: NavLayout): NavGroup[] {
  const ov = layout?.items
  const moduleByGroup: Record<string, string> = {}
  const defaultOrder: string[] = []
  groups.forEach(g => { if (!(g.group in moduleByGroup)) { moduleByGroup[g.group] = g.module; defaultOrder.push(g.group) } })
  const targets: { group: string; it: NavItem }[] = []
  // Global (group|href) dedup so no href renders twice in one group — protects the built-in Reports
  // directory from ever double-placing an item a tenant already duplicated there via `also`.
  const placedGH = new Set<string>()
  const push = (group: string, it: NavItem) => {
    const k = group + '|' + it.href
    if (placedGH.has(k)) return
    placedGH.add(k); targets.push({ group, it })
  }
  const surviving = new Map<string, NavItem>()   // href → item that passed access filtering (for the directory)
  for (const g of groups) for (const it of g.items) {
    const o = ov?.[it.href]
    if (o?.hidden) continue
    surviving.set(it.href, it)
    const primary = (o?.group && o.group.trim()) || g.group
    push(primary, it)
    // Additional placements (duplicates). Dedup within one item so the same href never renders twice in
    // one group (React-key + visual dup), and never re-adds its own primary group.
    if (o?.also && o.also.length) {
      for (const a of o.also) { const ag = (a || '').trim(); if (ag) push(ag, it) }
    }
  }
  // Built-in Reports directory (code-level default, EVERY tenant unless opted out). Each surviving report
  // href is duplicated into its category — SAME object, so identical gating. Iterated in REPORT_DIRECTORY
  // order (grouped by category) so categories appear in REPORT_CATEGORIES order after the module groups.
  if (!layout?.hideReportsDirectory) {
    for (const [href, catKey] of REPORT_DIRECTORY) {
      const it = surviving.get(href)
      const cat = REPORT_CATEGORY_LABEL[catKey]
      if (it && cat) { moduleByGroup[cat] = moduleByGroup[cat] || it.module; push(cat, it) }
    }
  }
  const seen = new Set<string>(); const order: string[] = []
  defaultOrder.forEach(g => { if (targets.some(t => t.group === g)) { order.push(g); seen.add(g) } })
  targets.forEach(t => { if (!seen.has(t.group)) { order.push(t.group); seen.add(t.group) } })
  return order.map(group => ({
    group,
    module: moduleByGroup[group] || (targets.find(t => t.group === group)?.it.module || ''),
    items: targets.filter(t => t.group === group).map(t => t.it),
  })).filter(g => g.items.length > 0)
}

export function moduleForPath(path: string): string {
  if (path.startsWith('/reports')) return 'targets'
  if (path.startsWith('/admin/support')) return 'support'   // tech-support console (mig 715), gated on 'support' not 'admin'
  if (path.startsWith('/admin')) return 'admin'
  if (path.startsWith('/configurations')) return 'admin'
  if (path.startsWith('/employee')) return 'targets'
  if (path.startsWith('/storeops')) return 'storeops'
  if (path.startsWith('/hr')) return 'hr'
  if (path.startsWith('/closing')) return 'closing'
  if (path.startsWith('/accounts')) return 'accounts'
  if (path.startsWith('/notify')) return 'notify'
  if (path.startsWith('/helpdesk')) return 'helpdesk'
  if (path.startsWith('/remediation')) return 'helpdesk'
  if (path.startsWith('/commcalc/targets')) return 'targets'
  if (path.startsWith('/commcalc/asset')) return 'asset'
  if (path.startsWith('/commcalc/vip')) return 'vip'
  if (path.startsWith('/commcalc')) return 'commissions'
  return 'commissions'
}

// The module that GOVERNS a path = the nav item whose href is the longest boundary-matched prefix
// (exact wins). This is the SAME source the sidebar gates on (canSeeItem keys off item.module), so the
// guard (canAccessPath) and the sidebar can never disagree about which module a page belongs to.
// Some items are placed in an information-architecture group whose URL prefix implies a DIFFERENT
// module than the item's real one (e.g. `/commcalc/payables` is an Asset feature, `/commcalc/distributors`
// a Distributor/vip feature, `/commcalc/asset/hotsheet-recon` a Commissions pricing page). Path-prefix
// derivation (moduleForPath) got those wrong, so a tab the sidebar SHOWED under module X was gated by
// the guard under module Y → clicking it failed canAccessPath and bounced to the dashboard. Returns null
// for paths no nav item governs (deep sub-pages of a module whose root isn't itself a nav href) → the
// caller falls back to moduleForPath.
export function navModuleForPath(path: string): string | null {
  let best: string | null = null, bestLen = -1
  for (const g of NAV) {
    for (const it of g.items) {
      if ((path === it.href || path.startsWith(it.href + '/')) && it.href.length > bestLen) {
        best = it.module; bestLen = it.href.length
      }
    }
  }
  return best
}

// A super-admin (role-management rights) implicitly has EVERY module. This keeps newly
// added modules (e.g. Accounts, added after the roles were seeded) visible to admins
// without re-seeding each role's permissions JSONB. Non-admin roles still need the flag.
export function isSuperAdmin(perms: Permissions): boolean {
  return !!perms?.modules?.admin
}

// May this user see the ADMIN ATTENTION popup / indicator (overdue imports, pending mappings,
// duplicate-data signals)? MIRROR of backend `core.import_health.can_view_attention` — KEEP IN SYNC.
// It reuses the EXISTING admin-ish concept (no parallel gate is invented): an explicit per-page override
// for /admin/import-health wins, then the `admin` module, then company-wide scope. A non-admin gets
// `false` here and the component renders nothing; the backend 403s them independently.
export function canSeeAttention(perms: Permissions): boolean {
  const ov = perms?.pages?.['/admin/import-health']
  if (typeof ov === 'boolean') return ov
  if (isSuperAdmin(perms)) return true
  return (perms?.scope || 'all') === 'all'
}

// ── Carrier-scoped nav ──────────────────────────────────────────────────────────────────────────
// Some pages belong to a specific carrier (Boost vs Total). An href listed here shows ONLY when the
// tenant has a matching carrier — UNLESS an admin override says otherwise. Everything not listed is
// generic (all carriers). Currently DLAR-driven reporting (KPI/coaching) lives under Boost; as another
// carrier gets its own DLAR/KPI reporting, add that carrier's code to the relevant hrefs.
export type CarrierRef = { name?: string; code?: string; is_default?: boolean }

// Mirror of backend _resolve_carrier_mode (commcalc/router.py): 'boost' = the legacy verified Boost
// KPI-tier engine; 'plan' = pay ONLY from configurable Commission Plans / Payout Schedules. Conservative
// so Boost tenants are never flipped. KEEP IN SYNC with the backend.
export function carrierMode(carriers: CarrierRef[] | undefined): 'boost' | 'plan' {
  const cs = carriers || []
  const isB = (c: CarrierRef) => /boost/i.test((c.code || '') + ' ' + (c.name || ''))
  if (cs.length === 0) return 'boost'
  const def = cs.find(c => c.is_default)
  if (def) return isB(def) ? 'boost' : 'plan'
  if (cs.some(isB)) return 'boost'
  return 'plan'
}
export const NAV_CARRIERS: Record<string, string[]> = {
  '/commcalc/vip': ['boost'], '/commcalc/vip/paygo': ['boost'], '/commcalc/vip/sweep': ['boost'],
  '/commcalc/distributors': ['boost'], '/commcalc/asset/lending': ['boost'],
  '/commcalc/asset/owed-weekly': ['boost'], '/commcalc/asset/hotsheet-recon': ['boost'],
  // The rest of the VIP(Boost)-financing asset reports (they read the VIP asset_ledger / ePay appeals
  // and are empty for a non-Boost tenant). Added per mod-asset NEEDS CORE [asset-10]. Boost-byte-identical:
  // a Boost tenant HAS the boost carrier → carrierOK stays true → these still show; only a Total-only
  // tenant (luxelink) loses them. Admin can override per item at /admin/labels (caps['carrier:<href>']).
  '/commcalc/asset/dashboard': ['boost'], '/commcalc/asset/aging': ['boost'],
  '/commcalc/asset/missing-phones': ['boost'], '/commcalc/asset/aging-rebate': ['boost'],
  '/commcalc/asset/on-inventory': ['boost'], '/commcalc/asset/charges/rma': ['boost'],
  '/commcalc/kpi': ['boost'], '/commcalc/coaching': ['boost'],
  // MA / VidaPay (T-CETRA) pages — the mirror gate of Boost's ePay pages. Total-processor only.
  // Marketplace Purchases reads commcalc.raw_ma_marketplace_orders (VidaPay MA orders), so it is
  // processor-specific just like ma-commission → gated to Total, NOT ungated. STEWARD JUDGMENT (differs
  // from mod-asset's "leave ungated" note): an ungated item would add a permanently-empty tab to the
  // Boost sidebar, violating "Boost byte-identical"; ['total'] shows it for luxelink/Total (the actual
  // goal) and any admin can widen it per tenant at /admin/labels if a non-Total tenant ever needs it.
  '/commcalc/ma-commission': ['total'],
  '/commcalc/asset/marketplace-purchases': ['total'],
  // Boost Rates page = the hardcoded Boost KPI-tier config; only meaningful for Boost tenants. A
  // Total-only tenant (e.g. luxelink) never sees it — they configure pay via Commission Plans instead.
  '/commcalc/settings': ['boost'],
}
// Carrier gate: admin per-item override wins (caps['carrier:<href>'] true/false); else a carrier-scoped
// item shows only when the tenant has a matching carrier. No carrier chosen yet → hide nothing.
export function carrierOK(href: string, tenantCarriers: CarrierRef[] | undefined, caps: Record<string, boolean | null>): boolean {
  const ov = caps['carrier:' + href]
  if (ov === true) return true
  if (ov === false) return false
  const need = NAV_CARRIERS[href]
  if (!need || need.length === 0) return true
  if (!tenantCarriers || tenantCarriers.length === 0) return true
  const have = tenantCarriers.map(c => (c.code || c.name || '').toLowerCase()).filter(Boolean)
  return need.some(k => have.some(t => t.includes(k) || k.includes(t)))
}

// Pages restricted to management (company-wide leadership by default; DMs excluded), but still
// grantable/revocable per role via a `pages` override — mirrors the backend _can_mgmt_review gate.
const MGMT_ONLY = new Set<string>(['/closing/management'])
export function canManage(perms: Permissions, href: string): boolean {
  if (isSuperAdmin(perms)) return true
  const ov = perms.pages?.[href]
  if (typeof ov === 'boolean') return ov
  return (perms.scope || 'all') === 'all'
}

// Canonical module keys mirror the backend core.module_catalog (mig 700). The frontend historically
// tagged Finance nav + roles with `accounts`; the backend canonical entitlement key is `account`.
// This read-side alias map treats them as ONE module so a grant under either key is honored — no data
// migration of stored role permissions. Behavior-neutral today (no role stores `account`), forward-safe.
export const MODULE_ALIASES: Record<string, string> = { accounts: 'account', account: 'accounts' }
export function moduleGranted(mods: Record<string, boolean> | undefined, key: string): boolean {
  if (!mods) return false
  return !!(mods[key] || mods[MODULE_ALIASES[key]])
}

export function canSeeItem(perms: Permissions, item: NavItem): boolean {
  if (isSuperAdmin(perms)) return true
  if (MGMT_ONLY.has(item.href)) return canManage(perms, item.href)
  if (item.scopes && !item.scopes.includes(perms.scope || 'all')) return false
  // Per-function override wins (either direction) — lets an admin grant/deny each function per role.
  const ov = perms.pages?.[item.href]
  if (typeof ov === 'boolean') return ov
  // Default: operational module gate (alias-aware) + (for report pages) the report-area gate.
  if (!moduleGranted(perms.modules, item.module)) return false
  const area = reportAreaForPath(item.href)
  if (area && !hasReport(perms, area)) return false
  return true
}

// The per-function override for a path (exact href, else the longest matching nav-href prefix).
function pageOverrideForPath(perms: Permissions, path: string): boolean | undefined {
  const pages = perms.pages
  if (!pages) return undefined
  if (path in pages) return pages[path]
  let best: boolean | undefined, bestLen = -1
  for (const g of NAV) for (const it of g.items) {
    if ((path === it.href || path.startsWith(it.href + '/')) && it.href.length > bestLen && (it.href in pages)) {
      best = pages[it.href]; bestLen = it.href.length
    }
  }
  return best
}

// Pages a self-scoped (rep) user may always reach, on top of their home.
const SELF_ALLOWED = ['/commcalc/targets/my', '/commcalc/kpi', '/account/password', '/reports', '/helpdesk']

export function canAccessPath(perms: Permissions, path: string): boolean {
  if (path === '/' || path.startsWith('/account/password')) return true
  // Super-admin bypass FIRST — mirrors canSeeItem's own precedence (isSuperAdmin checked before scope).
  // Keeping it ahead of the per-item scope loop guarantees the operator/super-admin (who sees every tab)
  // can never be bounced by a scope-restricted nav item → the sidebar and the guard stay consistent.
  if (isSuperAdmin(perms)) return true
  const scope = perms.scope || 'all'
  if (scope === 'self') {
    const home = perms.home || '/commcalc/targets/my'
    return SELF_ALLOWED.some(p => path.startsWith(p)) || path.startsWith(home)
  }
  // For settings/manager-only sub-pages, honor the matching nav item's scope restriction.
  for (const g of NAV) {
    for (const it of g.items) {
      if (path === it.href && it.scopes && !it.scopes.includes(scope)) return false
    }
  }
  if (MGMT_ONLY.has(path)) return canManage(perms, path)   // management-only pages, DMs excluded
  const ov = pageOverrideForPath(perms, path)   // per-function override wins
  if (typeof ov === 'boolean') return ov
  const area = reportAreaForPath(path)   // report pages need the separate report permission
  if (area && !hasReport(perms, area)) return false
  // Gate on the GOVERNING nav item's module (same source the sidebar uses), not the path-prefix guess —
  // so any tab the sidebar shows is guaranteed reachable. Fall back to moduleForPath for un-navved paths.
  return moduleGranted(perms.modules, navModuleForPath(path) ?? moduleForPath(path))
}

export function homeFor(perms: Permissions): string {
  return perms.home || '/commcalc'
}

// A landing the user can actually open. The configured home may be gated off — e.g. it
// defaults to '/commcalc', which is the report-gated 'commissions' area, so a role without
// commission-report clearance can't enter it. The (platform) guard would then redirect to that
// same home forever (infinite "Redirecting…", presenting as "can't log in"). Fall back to the
// first nav item the user can open, then to the always-allowed password page so we never loop.
export function safeHomeFor(perms: Permissions): string {
  const home = homeFor(perms)
  if (canAccessPath(perms, home)) return home
  for (const g of NAV) {
    for (const it of g.items) {
      if (canSeeItem(perms, it) && canAccessPath(perms, it.href)) return it.href
    }
  }
  return '/account/password'   // canAccessPath() always allows this → guaranteed non-looping
}
