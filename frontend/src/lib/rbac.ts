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
    { href: '/commcalc/upload', label: 'Upload Files', icon: '📁', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/reports', label: 'All Reports', icon: '📋', module: 'commissions' },
    { href: '/commcalc/gp', label: 'Gross Profit', icon: '💰', module: 'commissions' },
    { href: '/commcalc/kpi', label: 'KPI Metrics', icon: '🎯', module: 'commissions' },
    { href: '/commcalc/flags', label: 'Flags', icon: '🚩', module: 'commissions' },
    { href: '/commcalc/discrepancy', label: 'Pay Discrepancy', icon: '⚠️', module: 'commissions' },
    { href: '/commcalc/settings', label: 'Commission Rates', icon: '⚙️', module: 'commissions', scopes: ['all'] },
    { href: '/commcalc/expenses', label: 'Store Expenses', icon: '🏪', module: 'commissions', scopes: ['all', 'market'] },
  ]},
  { group: 'Targets', module: 'targets', items: [
    { href: '/commcalc/targets', label: 'Daily Targets', icon: '📈', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/action-plan', label: 'Action Plan', icon: '✅', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/settings', label: 'Target Settings', icon: '🎚️', module: 'targets', scopes: ['all'] },
    { href: '/commcalc/targets/my', label: 'My Targets', icon: '🙋', module: 'targets' },
  ]},
  { group: 'Asset', module: 'asset', items: [
    { href: '/commcalc/asset', label: 'Asset Ledger', icon: '📦', module: 'asset' },
  ]},
  { group: 'VIP', module: 'vip', items: [
    { href: '/commcalc/vip', label: 'VIP Invoices', icon: '🧾', module: 'vip' },
  ]},
  { group: 'StoreOps', module: 'storeops', items: [
    { href: '/storeops', label: 'Dashboard', icon: '🏠', module: 'storeops' },
    { href: '/storeops/schedule', label: 'Schedule', icon: '📅', module: 'storeops' },
    { href: '/storeops/employees', label: 'Employees', icon: '👥', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/timeoff', label: 'Time Off', icon: '🌴', module: 'storeops' },
    { href: '/storeops/swaps', label: 'Shift Swaps', icon: '🔄', module: 'storeops' },
    { href: '/storeops/reports', label: 'Reports', icon: '📋', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/payroll', label: 'Payroll', icon: '💵', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/admin', label: 'Admin', icon: '🛠️', module: 'storeops', scopes: ['all', 'market'] },
  ]},
  { group: 'Notify', module: 'notify', items: [
    { href: '/notify', label: 'Notify', icon: '📤', module: 'notify' },
  ]},
  { group: 'Admin', module: 'admin', items: [
    { href: '/admin/roles', label: 'Roles & Access', icon: '🔐', module: 'admin' },
  ]},
]

export function moduleForPath(path: string): string {
  if (path.startsWith('/admin')) return 'admin'
  if (path.startsWith('/storeops')) return 'storeops'
  if (path.startsWith('/notify')) return 'notify'
  if (path.startsWith('/commcalc/targets')) return 'targets'
  if (path.startsWith('/commcalc/asset')) return 'asset'
  if (path.startsWith('/commcalc/vip')) return 'vip'
  if (path.startsWith('/commcalc')) return 'commissions'
  return 'commissions'
}

export function canSeeItem(perms: Permissions, item: NavItem): boolean {
  if (!perms?.modules?.[item.module]) return false
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
  return !!perms?.modules?.[moduleForPath(path)]
}

export function homeFor(perms: Permissions): string {
  return perms.home || '/commcalc'
}
