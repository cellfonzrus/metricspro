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
export type ModuleKey =
  | 'timeclock'
  | 'schedule'
  | 'closing'
  | 'hr'
  | 'pos'
  | 'crm'
  | 'earnings'

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
    key: 'schedule',
    title: 'My Schedule',
    short: 'Schedule',
    route: '/schedule',
    icon: '🗓️',
    description: 'The week you are rostered for, time off, and asking for cover.',
    primaryTab: true,
    visible: provisioned,
    entitlementKey: 'modules.storeops',
  },
  {
    // The STORE's own side of the day. The DM's sign-off on it is app/(app)/dm/verify.tsx, reached
    // from the DM hub — two jobs with two gates, so two entries, not one screen with a mode.
    key: 'closing',
    title: 'Daily Closing',
    short: 'Closing',
    route: '/closing/submit',
    icon: '🧮',
    description: 'Submit your day: money collected, what you sold, expenses, envelope photo.',
    primaryTab: false,
    visible: provisioned,
    entitlementKey: 'modules.closing',
  },
  {
    // The rep's own HR RECORD — paperwork, documents, details. Their PAY and performance record is
    // app/(app)/dashboards/me.tsx and stays there; these are two different questions about one
    // person, not one screen with two tabs.
    key: 'hr',
    title: 'My HR',
    short: 'HR',
    route: '/hr',
    icon: '🧾',
    description: 'Your paperwork, documents and details.',
    primaryTab: false,
    visible: provisioned,
    entitlementKey: 'modules.hr',
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
// The store VISIT inspection shipped as app/(app)/dm/checklist.tsx and the daily CLOSING as the
// `closing` module above, so neither is listed here any more — a "coming soon" tile for something
// that has arrived sends a rep looking for it in the wrong place.
export type RoadmapModule = { id: string; title: string; icon: string; description: string }
export const ROADMAP_MODULES: RoadmapModule[] = [
  { id: 'commcalc', title: 'Commission Admin', icon: '📊', description: 'CommCalc reports, payouts & rates' },
  // Shifts, time off and cover requests shipped as the `schedule` module above, and the rep's own HR
  // record as `hr`. What is still only on the web is EDITING the rota (publish, assign, approve) and
  // the HR-SIDE screens — the employee database, letters, payroll expenses and compliance review.
  { id: 'storeops-admin', title: 'Rota & HR Admin', icon: '🗂️', description: 'Publish shifts, employee database, letters' },
  { id: 'assets', title: 'Assets & Inventory', icon: '📦', description: 'Phone lending, transfers' },
]

export function visibleModules(me: MePayload | null): ModuleDef[] {
  return MODULES.filter((m) => !m.comingSoon && m.visible(me))
}

export function primaryTabs(me: MePayload | null): ModuleDef[] {
  return visibleModules(me).filter((m) => m.primaryTab)
}
