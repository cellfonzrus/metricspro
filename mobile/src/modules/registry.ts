import type { MePayload } from '@/api/core'

// ── Module registry ──────────────────────────────────────────────────────────────────────────────
// The extensibility backbone. Every feature area (POS, CRM, Time Clock today; admin/back-office
// modules later) is declared here as data. The tab bar, the home grid and the route guard all read
// this ONE list, so "add a module" means: (1) add an entry here, (2) drop its screen(s) under
// app/(app)/<key>/, (3) — when the backend gate lands — set `permission`. Nothing else changes.
//
// `visible(me)` decides whether the signed-in user sees the module. Today POS/CRM/Time Clock have no
// server-side entitlement gate (see pos/router.py docstring), so they default to visible for any
// provisioned user; fine-grained actions (PII reveal, void) still gate at the action. When the
// backend `modules.<key>` entitlement is seeded, tighten `visible` to check it — the UI is already
// wired to respect it.
export type ModuleKey = 'timeclock' | 'pos' | 'crm' | 'earnings'

export type ModuleDef = {
  key: ModuleKey
  title: string
  short: string
  // Expo-router route (relative to the (app) group).
  route: string
  // A lucide-style glyph name is overkill here; we use an emoji for zero-dependency icons in v1.
  icon: string
  description: string
  // Show in the primary bottom tab bar (max ~4; the rest live on the Home grid + More).
  primaryTab: boolean
  // Gate. Return true to show. Kept a function so future entries can read me.permissions.
  visible: (me: MePayload | null) => boolean
  // Reserved for when server-side module entitlements land (core.module_catalog / roles.modules).
  entitlementKey?: string
  // Roadmap flag — renders as "coming soon" on the Home grid, never routed to.
  comingSoon?: boolean
}

const provisioned = (me: MePayload | null) => Boolean(me?.provisioned)

export const MODULES: ModuleDef[] = [
  {
    key: 'timeclock',
    title: 'Time Clock',
    short: 'Clock',
    route: '/timeclock',
    icon: '⏱️',
    description: 'Clock in and out, see your status and hours.',
    primaryTab: true,
    visible: provisioned,
    entitlementKey: 'modules.storeops',
  },
  {
    key: 'pos',
    title: 'Point of Sale',
    short: 'POS',
    route: '/pos',
    icon: '🛒',
    description: 'Ring up a sale, search the catalog, take payment.',
    primaryTab: true,
    visible: provisioned,
    entitlementKey: 'modules.pos',
  },
  {
    key: 'crm',
    title: 'CRM',
    short: 'CRM',
    route: '/crm',
    icon: '📇',
    description: 'Work your leads and follow-up tasks.',
    primaryTab: true,
    visible: provisioned,
    entitlementKey: 'modules.crm',
  },
  {
    key: 'earnings',
    title: 'Earnings',
    short: 'Earnings',
    route: '/earnings',
    icon: '💰',
    description: 'Your commission, targets and how close you are to hitting them.',
    primaryTab: true,
    visible: provisioned,
    entitlementKey: 'modules.commcalc',
  },
]

// ── Roadmap ──────────────────────────────────────────────────────────────────────────────────────
// Admin / back-office modules that the platform already has on the web and that will be ported. Listed
// so the Home grid can show a truthful "coming soon" provision (the user asked for a path to add all
// items eventually) without pretending they exist yet.
export type RoadmapModule = { id: string; title: string; icon: string; description: string }
export const ROADMAP_MODULES: RoadmapModule[] = [
  { id: 'commcalc', title: 'Commission Admin', icon: '📊', description: 'CommCalc reports, payouts & rates' },
  { id: 'storeops', title: 'Scheduling & HR', icon: '🗓️', description: 'Shifts, PTO, payroll' },
  { id: 'assets', title: 'Assets & Inventory', icon: '📦', description: 'Phone lending, transfers' },
  { id: 'closing', title: 'Store Visits & Closing', icon: '✅', description: 'Daily closing, audits' },
]

export function visibleModules(me: MePayload | null): ModuleDef[] {
  return MODULES.filter((m) => !m.comingSoon && m.visible(me))
}

export function primaryTabs(me: MePayload | null): ModuleDef[] {
  return visibleModules(me).filter((m) => m.primaryTab)
}
