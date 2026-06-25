// RBAC: nav definition (module-tagged) + access helpers. Permissions come from
// storeops.roles.permissions (resolved by the backend /core/me for the logged-in user).
export type Scope = 'all' | 'market' | 'store' | 'self'
export type Permissions = {
  modules?: Record<string, boolean>
  scope?: Scope
  home?: string
}

export type NavItem = { href: string; label: string; icon: string; module: string; scopes?: Scope[] }
export type NavGroup = { group: string; module: string; items: NavItem[] }

// scopes (when present) further restricts an item to those scope tiers, e.g. settings = admin only.
export const NAV: NavGroup[] = [
  { group: 'Commissions', module: 'commissions', items: [
    { href: '/commcalc', label: 'Dashboard', icon: '📊', module: 'commissions' },
    { href: '/commcalc/exec', label: 'Owner Overview', icon: '🏆', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/upload', label: 'Upload Files', icon: '📁', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/upload/wizard', label: 'Upload Wizard', icon: '🧭', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/reports', label: 'All Reports', icon: '📋', module: 'commissions' },
    { href: '/commcalc/gp', label: 'Gross Profit', icon: '💰', module: 'commissions' },
    { href: '/commcalc/kpi', label: 'KPI Metrics', icon: '🎯', module: 'commissions' },
    { href: '/commcalc/coaching', label: 'Rep Coaching', icon: '🎓', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/sales-analyzer', label: 'Sales Analyzer', icon: '📉', module: 'commissions', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/comp-trend', label: 'Total Compensation', icon: '📡', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/flags', label: 'Flags', icon: '🚩', module: 'commissions' },
    { href: '/commcalc/chargebacks', label: 'Chargebacks & Fraud', icon: '🔻', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/accessory-flags', label: 'Accessory Flags', icon: '🔖', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/discrepancy', label: 'Pay Discrepancy', icon: '⚠️', module: 'commissions' },
    { href: '/commcalc/settings', label: 'Commission Rates', icon: '⚙️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/connectors', label: 'Connectors', icon: '🔌', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/onboarding', label: 'Onboarding Wizard', icon: '🚀', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/expenses', label: 'Store Expenses', icon: '🏪', module: 'commissions', scopes: ['all', 'market'] },
  ]},
  { group: 'Mapping', module: 'commissions', items: [
    { href: '/commcalc/mapping', label: 'All Mappings', icon: '🗂️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/store-match', label: 'Store Matching', icon: '🏬', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/carrier-mapping', label: 'Carrier Mapping', icon: '🗺️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/column-mapping', label: 'Column Mapping', icon: '🧩', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/item-mapping', label: 'Item / Model Mapping', icon: '🧩', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/rep-aliases', label: 'Rep Aliases', icon: '🔗', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/asset/hotsheet-recon', label: 'Pricing Hotsheet', icon: '🏷️', module: 'commissions', scopes: ['all', 'market'] },
  ]},
  { group: 'Targets', module: 'targets', items: [
    { href: '/commcalc/targets', label: 'Daily Targets', icon: '📈', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/action-plan', label: 'Action Plan', icon: '✅', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/settings', label: 'Target Settings', icon: '🎚️', module: 'targets', scopes: ['all'] },
    { href: '/commcalc/targets/my', label: 'My Targets', icon: '🙋', module: 'targets' },
    { href: '/employee', label: 'Employee Dashboard', icon: '🧑‍💼', module: 'targets' },
  ]},
  { group: 'Asset', module: 'asset', items: [
    { href: '/commcalc/asset', label: 'Asset Ledger', icon: '📦', module: 'asset' },
  ]},
  { group: 'VIP', module: 'vip', items: [
    { href: '/commcalc/vip', label: 'VIP Invoices', icon: '🧾', module: 'vip' },
  ]},
  { group: 'Accounts', module: 'accounts', items: [
    { href: '/accounts', label: 'Dashboard', icon: '💼', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/pl', label: 'P&L Statement', icon: '📈', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/balance-sheet', label: 'Balance Sheet', icon: '⚖️', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/inventory', label: 'Inventory Values', icon: '📦', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/recon', label: 'Reconciliation', icon: '🔎', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/journal', label: 'Journal', icon: '📒', module: 'accounts', scopes: ['all'] },
    { href: '/accounts/companies', label: 'Companies', icon: '🏢', module: 'accounts', scopes: ['all'] },
  ]},
  { group: 'StoreOps', module: 'storeops', items: [
    { href: '/storeops', label: 'Dashboard', icon: '🏠', module: 'storeops' },
    { href: '/storeops/schedule', label: 'Schedule', icon: '📅', module: 'storeops' },
    { href: '/storeops/visits', label: 'Store Visits', icon: '📝', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/visits/settings', label: 'Visit Checklist', icon: '🧾', module: 'storeops', scopes: ['all'] },
    { href: '/storeops/employees', label: 'Employees', icon: '👥', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/timeoff', label: 'Time Off', icon: '🌴', module: 'storeops' },
    { href: '/storeops/swaps', label: 'Shift Swaps', icon: '🔄', module: 'storeops' },
    { href: '/storeops/timeclock', label: 'Time Clock', icon: '⏱️', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/reports', label: 'Reports', icon: '📋', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/payroll', label: 'Payroll', icon: '💵', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/admin', label: 'Admin', icon: '🛠️', module: 'storeops', scopes: ['all', 'market'] },
  ]},
  { group: 'Daily Closing', module: 'closing', items: [
    { href: '/closing', label: 'Dashboard', icon: '🧾', module: 'closing', scopes: ['all', 'market', 'store'] },
    { href: '/closing/submit', label: 'Submit Closing', icon: '➕', module: 'closing' },
    { href: '/closing/verify', label: 'DM Verify', icon: '✅', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/recon', label: 'Reconciliation', icon: '🔎', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/pickup', label: 'Cash Pickup', icon: '💵', module: 'closing', scopes: ['all', 'market'] },
    { href: '/closing/imports', label: 'Auto-Import', icon: '🔄', module: 'closing', scopes: ['all'] },
  ]},
  { group: 'Notify', module: 'notify', items: [
    { href: '/notify', label: 'Notify', icon: '📤', module: 'notify' },
    { href: '/notify/report-recipients', label: 'Report Recipients', icon: '📬', module: 'notify' },
  ]},
  { group: 'Configurations', module: 'admin', items: [
    { href: '/configurations', label: 'All Settings', icon: '⚙️', module: 'admin' },
    { href: '/admin/roles', label: 'Roles & Access', icon: '🔐', module: 'admin' },
  ]},
]

export function moduleForPath(path: string): string {
  if (path.startsWith('/admin')) return 'admin'
  if (path.startsWith('/configurations')) return 'admin'
  if (path.startsWith('/employee')) return 'targets'
  if (path.startsWith('/storeops')) return 'storeops'
  if (path.startsWith('/closing')) return 'closing'
  if (path.startsWith('/accounts')) return 'accounts'
  if (path.startsWith('/notify')) return 'notify'
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

export function canSeeItem(perms: Permissions, item: NavItem): boolean {
  if (!isSuperAdmin(perms) && !perms?.modules?.[item.module]) return false
  if (item.scopes && !item.scopes.includes(perms.scope || 'all')) return false
  return true
}

// Pages a self-scoped (rep) user may always reach, on top of their home.
const SELF_ALLOWED = ['/commcalc/targets/my', '/account/password']

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
  return !!perms?.modules?.[moduleForPath(path)]
}

export function homeFor(perms: Permissions): string {
  return perms.home || '/commcalc'
}
