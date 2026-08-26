import { Permissions, Scope, canSeeItem } from '@/lib/rbac'

// Canonical catalog of REPORTS across every module — the single source for the unified Reports hub
// (/reports) and the employee portal surfacing. Curated (not every nav item is a report); keep in
// sync when a new report page ships.
export type ReportDef = { href: string; label: string; module: string; scopes?: Scope[]; desc?: string }
export type PortalCfg = Record<string, { enabled: boolean; roles: string[]; label?: string; category?: string }>

export const REPORT_CATEGORIES: { category: string; reports: ReportDef[] }[] = [
  { category: 'Commissions', reports: [
    { href: '/commcalc', label: 'Commissions Dashboard', module: 'commissions' },
    { href: '/commcalc/exec', label: 'Owner Overview', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/activations', label: 'Activations', module: 'commissions', scopes: ['all', 'market'], desc: 'b2b Activation Details basis of truth — distinct devices by market/store, Upgrade toggle, and automatic reconciliation against the sales feed' },
    { href: '/commcalc/gp', label: 'Gross Profit', module: 'commissions' },
    { href: '/commcalc/kpi', label: 'KPI Metrics', module: 'commissions' },
    { href: '/commcalc/coaching', label: 'Rep Coaching', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/sales-analyzer', label: 'Sales Analyzer', module: 'commissions', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/sales-comparison', label: 'Sales Comparison', module: 'commissions', desc: 'Month-over-month / year-over-year % change per item sold (phones, BYOD, accessories, tablets, financing) across all stores' },
    { href: '/commcalc/comp-trend', label: 'Total Compensation', module: 'commissions', scopes: ['all', 'market'] },
    // DM GATE — mirrors the rbac.ts NAV row 1:1 (owner directive 2026-08-07). This catalog is the
    // SECOND door to the same page (Report Center /reports + the employee portal), and clearedFor()
    // gates it with canSeeItem() on THIS object — so without the same `scopes` a store-scoped user
    // would still be shown a Flags link here that the layout Guard (canAccessPath, which reads NAV)
    // then bounces: the "the tab is there but clicking it does nothing" class. Chargebacks already
    // carries the pair; Flags now matches it on both surfaces.
    { href: '/commcalc/flags', label: 'Flags', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/chargebacks', label: 'Chargebacks & Fraud', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/accessory-flags', label: 'Accessory Flags', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/discrepancy', label: 'Pay Discrepancy', module: 'commissions' },
    { href: '/commcalc/imei-rebates', label: 'IMEI Rebate Reconciliation', module: 'commissions', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/ma-handsets', label: 'Marketplace Handset COGS', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/device-cost-recon', label: 'Device Cost Reconciliation', module: 'commissions', scopes: ['all', 'market'] },
    { href: '/commcalc/sales-recon', label: 'Sales Feed Recon', module: 'commissions', scopes: ['all', 'market'] },
  ] },
  { category: 'Targets', reports: [
    { href: '/commcalc/targets', label: 'Daily Targets', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/action-plan', label: 'Action Plan', module: 'targets', scopes: ['all', 'market', 'store'] },
    { href: '/commcalc/targets/my', label: 'My Targets', module: 'targets' },
  ] },
  { category: 'Team', reports: [
    { href: '/storeops/team', label: 'My Team', module: 'storeops', scopes: ['all', 'market', 'store'] },
  ] },
  { category: 'Asset', reports: [
    { href: '/commcalc/asset', label: 'Asset Ledger', module: 'asset' },
  ] },
  { category: 'Distributor', reports: [
    { href: '/commcalc/vip', label: 'Distributor Invoices', module: 'vip' },
  ] },
  { category: 'Accounts', reports: [
    { href: '/accounts', label: 'Accounts Dashboard', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/pl', label: 'P&L Statement', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/balance-sheet', label: 'Balance Sheet', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/recon', label: 'Reconciliation', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/residual-per-sub', label: 'Residual per Subscriber', module: 'accounts', scopes: ['all', 'market'] },
    { href: '/accounts/trends', label: 'Trends (all metrics)', module: 'accounts', scopes: ['all', 'market'] },
  ] },
  { category: 'StoreOps', reports: [
    { href: '/storeops/reports', label: 'Hours / Payroll Reports', module: 'storeops', scopes: ['all', 'market'] },
    { href: '/storeops/payroll', label: 'Payroll', module: 'storeops', scopes: ['all', 'market'] },
  ] },
  { category: 'Daily Closing', reports: [
    { href: '/closing', label: 'Closing Dashboard', module: 'closing', scopes: ['all', 'market', 'store'] },
    { href: '/closing/recon', label: 'Closing Reconciliation', module: 'closing', scopes: ['all', 'market'] },
  ] },
]

const asNav = (r: ReportDef) => ({ href: r.href, label: r.label, icon: '', module: r.module, scopes: r.scopes })

// Does the report's clearance (module + scope) allow this user? (Roles & Access rules.)
export function clearedFor(perms: Permissions, r: ReportDef): boolean {
  return canSeeItem(perms, asNav(r) as any)
}

// Reports to surface in a user's portal: enabled in config + role allowed + has clearance.
export function myPortalReports(perms: Permissions, roleName: string | null, cfg: PortalCfg) {
  const out: { category: string; reports: ReportDef[] }[] = []
  for (const grp of REPORT_CATEGORIES) {
    const reports = grp.reports.filter(r => {
      const c = cfg[r.href]
      if (!c || !c.enabled) return false
      if (c.roles && c.roles.length && roleName && !c.roles.includes(roleName)) return false
      return clearedFor(perms, r)
    })
    if (reports.length) out.push({ category: grp.category, reports })
  }
  return out
}
