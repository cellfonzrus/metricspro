// RBAC: nav definition (module-tagged) + access helpers. Permissions come from
// storeops.roles.permissions (resolved by the backend /core/me for the logged-in user).
export type Scope = 'all' | 'market' | 'store' | 'self'
export type Permissions = {
  modules?: Record<string, boolean>
  reports?: Record<string, boolean>   // per-AREA report access (separate from the operational module)
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
  ['/commcalc/kpi', 'commissions'], ['/commcalc/coaching', 'commissions'], ['/commcalc/sales-analyzer', 'commissions'],
  ['/commcalc/comp-trend', 'commissions'], ['/commcalc/flags', 'commissions'], ['/commcalc/chargebacks', 'commissions'],
  ['/commcalc/accessory-flags', 'commissions'], ['/commcalc/discrepancy', 'commissions'], ['/commcalc/sales-recon', 'commissions'],
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
    { href: '/commcalc/exec', label: 'Owner Overview', icon: '🏆', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/reports', label: 'Rep Commission Report', icon: '📋', module: 'commissions' },
    { href: '/commcalc/kpi', label: 'KPI Metrics', icon: '🎯', module: 'commissions' },
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
    { href: '/commcalc/recovery', label: 'Appeal Recovery', icon: '💰', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/sales-recon', label: 'Sales Feed Recon', icon: '🔁', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/settings', label: 'Commission Rates', icon: '⚙️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/payout-schedules', label: 'Payout Schedules', icon: '📆', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/commission-plans', label: 'Commission Plans', icon: '🧮', module: 'commissions', scopes: ['all'] },
  ]},
  { group: 'Targets & Coaching', module: 'targets', items: [
    { href: '/commcalc/targets', label: 'Daily Targets', icon: '📈', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/action-plan', label: 'Action Plan', icon: '✅', module: 'targets', scopes: ['all', 'market', 'store'] },
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
    { href: '/commcalc/asset/dashboard', label: 'Charges Dashboard', icon: '📊', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/owed-weekly', label: 'Weekly Owed-to-Distributor', icon: '📅', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/aging', label: 'Inventory Aging', icon: '⏳', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/missing-phones', label: 'Missing Phones', icon: '📵', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/on-inventory', label: 'On-Inventory by Store', icon: '🏬', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/payables', label: 'Forecasting & Vendor Payables', icon: '📱', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/borrowed', label: 'Borrowed / Lending', icon: '🔁', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/lending', label: 'Asset Lending (PayGo)', icon: '📲', module: 'asset', scopes: ['all', 'market'], cap: 'asset_lending' },
    { href: '/commcalc/asset/charges/rma', label: 'RMA', icon: '↩️', module: 'asset', scopes: ['all', 'market'] },
    { href: '/commcalc/asset/inventory-recon', label: 'Inventory Recon', icon: '🔎', module: 'asset', scopes: ['all', 'market'] },
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
    { href: '/hr', label: 'HR · Total Comp', icon: '📊', module: 'hr', scopes: ['all', 'market'] },
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
    { href: '/closing/cash-config', label: 'Cash Setup', icon: '⚙️', module: 'closing', scopes: ['all'] },
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
    { href: '/commcalc/commission-import', label: 'Commission Import Wizard', icon: '🪄', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/mapping', label: 'All Mappings', icon: '🗂️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/store-match', label: 'Store Matching', icon: '🏬', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/carrier-mapping', label: 'Carrier Mapping', icon: '🗺️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/column-mapping', label: 'Column Mapping', icon: '🧩', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/target-fields', label: 'Custom Target Fields', icon: '🧱', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/commission-category-map', label: 'Commission Category Map', icon: '🗺️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/gp-category-map', label: 'GP Category Map', icon: '💰', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/item-mapping', label: 'Item / Model Mapping', icon: '🧩', module: 'commissions', scopes: ['all'] },
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
  { group: 'Configuration', module: 'admin', items: [
    { href: '/configurations', label: 'All Settings', icon: '⚙️', module: 'admin' },
    { href: '/admin/tenants', label: 'Companies (Tenants)', icon: '🏢', module: 'admin' },
    { href: '/admin/tenant-settings', label: 'Pay Period & Work-Week', icon: '📅', module: 'admin' },
    { href: '/admin/billing', label: 'Billing (Tenants)', icon: '💳', module: 'admin' },
    { href: '/admin/roles', label: 'Roles & Access', icon: '🔐', module: 'admin' },
    { href: '/admin/org', label: 'Org Structure', icon: '🌳', module: 'admin' },
    { href: '/admin/org-chart', label: 'Employee Org Chart', icon: '👥', module: 'admin' },
    { href: '/admin/labels', label: 'Display Labels', icon: '🏷️', module: 'admin' },
    { href: '/admin/menu', label: 'Menu Layout', icon: '🧭', module: 'admin' },
  ]},
]

export type NavLayout = { items?: Record<string, { group?: string; hidden?: boolean }> }

// Apply a per-org sidebar layout (admin config) ON TOP of the already-filtered groups: move an item to
// a different group, or hide it. Items with no override keep their built-in group. Default group order
// is preserved; a brand-new group name lands at the end. Returns groups ready to render.
export function applyNavLayout(groups: NavGroup[], layout?: NavLayout): NavGroup[] {
  const ov = layout?.items
  if (!ov || !Object.keys(ov).length) return groups
  const moduleByGroup: Record<string, string> = {}
  const defaultOrder: string[] = []
  groups.forEach(g => { if (!(g.group in moduleByGroup)) { moduleByGroup[g.group] = g.module; defaultOrder.push(g.group) } })
  const targets: { group: string; it: NavItem }[] = []
  for (const g of groups) for (const it of g.items) {
    const o = ov[it.href]
    if (o?.hidden) continue
    targets.push({ group: (o?.group && o.group.trim()) || g.group, it })
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

// A super-admin (role-management rights) implicitly has EVERY module. This keeps newly
// added modules (e.g. Accounts, added after the roles were seeded) visible to admins
// without re-seeding each role's permissions JSONB. Non-admin roles still need the flag.
export function isSuperAdmin(perms: Permissions): boolean {
  return !!perms?.modules?.admin
}

// ── Carrier-scoped nav ──────────────────────────────────────────────────────────────────────────
// Some pages belong to a specific carrier (Boost vs Total). An href listed here shows ONLY when the
// tenant has a matching carrier — UNLESS an admin override says otherwise. Everything not listed is
// generic (all carriers). Currently DLAR-driven reporting (KPI/coaching) lives under Boost; as another
// carrier gets its own DLAR/KPI reporting, add that carrier's code to the relevant hrefs.
export type CarrierRef = { name?: string; code?: string }
export const NAV_CARRIERS: Record<string, string[]> = {
  '/commcalc/vip': ['boost'], '/commcalc/vip/paygo': ['boost'], '/commcalc/vip/sweep': ['boost'],
  '/commcalc/distributors': ['boost'], '/commcalc/asset/lending': ['boost'],
  '/commcalc/asset/owed-weekly': ['boost'], '/commcalc/asset/hotsheet-recon': ['boost'],
  '/commcalc/kpi': ['boost'], '/commcalc/coaching': ['boost'],
  '/commcalc/ma-commission': ['total'],
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

export function canSeeItem(perms: Permissions, item: NavItem): boolean {
  if (isSuperAdmin(perms)) return true
  if (MGMT_ONLY.has(item.href)) return canManage(perms, item.href)
  if (item.scopes && !item.scopes.includes(perms.scope || 'all')) return false
  // Per-function override wins (either direction) — lets an admin grant/deny each function per role.
  const ov = perms.pages?.[item.href]
  if (typeof ov === 'boolean') return ov
  // Default: operational module gate + (for report pages) the report-area gate.
  if (!perms?.modules?.[item.module]) return false
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
const SELF_ALLOWED = ['/commcalc/targets/my', '/account/password', '/reports', '/helpdesk']

export function canAccessPath(perms: Permissions, path: string): boolean {
  if (path === '/' || path.startsWith('/account/password')) return true
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
  if (isSuperAdmin(perms)) return true
  if (MGMT_ONLY.has(path)) return canManage(perms, path)   // management-only pages, DMs excluded
  const ov = pageOverrideForPath(perms, path)   // per-function override wins
  if (typeof ov === 'boolean') return ov
  const area = reportAreaForPath(path)   // report pages need the separate report permission
  if (area && !hasReport(perms, area)) return false
  return !!perms?.modules?.[moduleForPath(path)]
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
