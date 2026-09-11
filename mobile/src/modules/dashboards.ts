import type { MePayload } from '@/api/core'
import { atLeast, scopeOf, type Scope } from '@/lib/scope'

// ── Dashboard catalog ────────────────────────────────────────────────────────────────────────────
// Data-driven, like the module registry: the Dashboards hub, the tab and the route guard all read
// this one list. To add a dashboard: add an entry here + a screen at app/(app)/dashboards/<slug>.tsx.
//
// `minScope` is the REPORTING span required to SEE the dashboard (see src/lib/scope.ts for why the
// app — not just the server — has to gate these). Org-wide financials need 'market' or better; the
// store-scoped POS KPIs and the personal dashboard are open to everyone ('self').
export type DashboardDef = {
  slug: string
  route: string
  title: string
  subtitle: string
  icon: string
  minScope: Scope
  // financial dashboards get a subtle accent so they read as "company numbers" at a glance
  group: 'personal' | 'store' | 'company'
}

export const DASHBOARDS: DashboardDef[] = [
  {
    slug: 'me',
    route: '/dashboards/me',
    title: 'My performance',
    subtitle: 'Your commission, tier and KPI progress this period.',
    icon: '🎯',
    minScope: 'self',
    group: 'personal',
  },
  {
    slug: 'store-kpis',
    route: '/dashboards/store-kpis',
    title: 'Store KPIs',
    subtitle: "Today, this week and this month's sales, plus stock on hand.",
    icon: '🏬',
    minScope: 'self',
    group: 'store',
  },
  {
    slug: 'exec-mtd',
    route: '/dashboards/exec-mtd',
    title: 'Executive MTD',
    subtitle: 'Activations and the full sales mix, by store and by rep.',
    icon: '📈',
    minScope: 'market',
    group: 'company',
  },
  {
    slug: 'sales',
    route: '/dashboards/sales',
    title: 'Sales report',
    subtitle: 'Transactions, revenue and gross profit for the period.',
    icon: '🧾',
    minScope: 'market',
    group: 'company',
  },
  {
    slug: 'pnl',
    route: '/dashboards/pnl',
    title: 'P&L / Accounts',
    subtitle: 'Revenue, gross profit and net income across the business.',
    icon: '💵',
    minScope: 'market',
    group: 'company',
  },
  {
    slug: 'store-pnl',
    route: '/dashboards/store-pnl',
    title: 'Store P&L',
    subtitle: 'Net profit and target attainment, store by store.',
    icon: '🏆',
    minScope: 'market',
    group: 'company',
  },
  {
    slug: 'commission',
    route: '/dashboards/commission',
    title: 'Commission payouts',
    subtitle: 'Incentive payout by rep, and the company total.',
    icon: '💸',
    minScope: 'market',
    group: 'company',
  },
]

export function visibleDashboards(me: MePayload | null): DashboardDef[] {
  const s = scopeOf(me)
  return DASHBOARDS.filter((d) => atLeast(s, d.minScope))
}

export function dashboardBySlug(slug: string): DashboardDef | undefined {
  return DASHBOARDS.find((d) => d.slug === slug)
}
