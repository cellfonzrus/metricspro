'use client'
// ── "May THIS viewer open THIS page from a tile?" — ONE home (owner report 2026-10-14) ───────────
//
// OWNER 2026-10-14: *"I cannot see the schedule on the app, where are we at making that live to all
// employees"*. Two separate facts, and the second is a code defect of the class CLAUDE.md names.
//
// THE CLASS, not the instance. Five surfaces render a tiled hub (HubTiles), and each one answers
// "which of these pages may the viewer open":
//
//   · /hub/[group]        — the generic hub: the predicate stack, written out inline
//   · /compliance         — the same stack, RETYPED
//   · /admin/dashboards   — a THIRD copy, for a different question (what a DESIGNER may place)
//   · /storeops           — hand-woven HubGroup[] literal, **NO GATE AT ALL**
//   · /payroll            — hand-woven HubGroup[] literal, **NO GATE AT ALL**
//
// So the Workforce dashboard — the only door to the Schedule page, which is `tileOnly` and therefore
// absent from the sidebar — showed a store-scoped rep 11 of its 21 interior links and 2 of its 6 KPI
// tiles pointing at pages `canAccessPath` bounces: Employees, Attendance, Lateness %, Hours Budget,
// Store Setup, Employee Setup, Reports, Visits and the two Visit/Reviews setup pages. That is exactly
// the "the tab is there but clicking it does nothing" failure the rbac.ts comments already name twice
// (TKT-1009, TKT-1012) — here reached through a tile instead of the menu, because the curated hubs
// were the two consumers that never asked the question.
//
// ONE FACT, ONE HOME, DEREFERENCED. `useHubGate()` IS the question, for every hub. It mirrors the
// sidebar's own chain verbatim ((platform)/layout.tsx): RBAC only while login is enforced AND a
// session exists (the open app shows everything, as the sidebar's `open` path does), then the
// platform-only gate, the tenant capability flag, the active-carrier lens, the tenant vertical, and
// the tenant nav-layout `hidden` override.
//
//   groupOK(group)     — the GROUP half: a platform-only group opens for the super admin alone (§38)
//   gateItems(items)   — for a hub built from NAV items (the generic hub, /compliance)
//   canOpen(href)      — for a hand-woven tile/KPI literal, whose items are hrefs, not NAV items
//   gateHubGroups(gs)  — canOpen over a HubGroup[] literal, dropping a tile left empty by it
//                        (mirrors tile-hubs.ts::layoutToHubGroups, which drops such a tile too)
//
// WHY canOpen FALLS BACK TO canAccessPath. A hand-woven tile may name an href with no NAV entry of
// its own. Fail-closed would hide a page the route guard would happily admit — a tile vanishing for
// no reason is the same defect wearing a hat. So an href NAV does not carry is answered by
// `canAccessPath`, which IS the route guard, and the two can never disagree.
//
// LOCKED: harness_hub_viewer_gate.py fails the build if a HubTiles consumer stops dereferencing this
// module, or if the predicate stack is retyped anywhere outside it.
import { useCallback, useMemo } from 'react'
import { useAuth, useActiveCarrier } from '@/lib/auth-context'
import { useCachedApi, CONFIG } from '@/lib/cache'
import { NAV, navPath, platformOK, canSeeItem, canAccessPath, carrierOKActive, verticalOK,
         type NavItem, type NavLayout } from '@/lib/rbac'
import type { HubGroup } from '@/components/HubTiles'

export type HubNavCfg = { labels?: Record<string, string>; capabilities?: Record<string, boolean | null>; layout?: NavLayout }

export type HubGate = {
  /** May this viewer see this NAV GROUP at all? The platform-only gate (index §38). */
  groupOK: (group: { platformOnly?: boolean }) => boolean
  /** Filter NAV items to the ones this viewer may open. */
  gateItems: (items: NavItem[]) => NavItem[]
  /** May this viewer open this in-app href? For hand-woven tile/KPI literals. */
  canOpen: (href: string) => boolean
  /** `canOpen` over a hand-woven HubGroup[]; a tile left with no items is dropped. */
  gateHubGroups: (groups: HubGroup[]) => HubGroup[]
  /** The shared nav-config read (labels / capabilities / tenant layout), so a hub needs only one hook. */
  navCfg: HubNavCfg | null
}

export function useHubGate(): HubGate {
  const { permissions, session, rbacEnabled, tenant, user } = useAuth()
  const { activeCarrier } = useActiveCarrier()
  // The same cached nav-config entry the sidebar and the generic hub share. Best-effort: a failed
  // read means no capability/layout refinement, never a blank hub.
  const { data: navCfg } = useCachedApi<HubNavCfg>('/api/v1/commcalc/nav-config', CONFIG)

  const caps = useMemo(() => navCfg?.capabilities || {}, [navCfg])
  const gated = rbacEnabled !== false && !!session
  const hidden = useMemo(() => navCfg?.layout?.items || {}, [navCfg])

  // THE predicate. Everything else in this file is a shape adapter over it.
  const itemOK = useCallback((it: NavItem): boolean => (
    platformOK(it, user)
    && (!gated || canSeeItem(permissions, it))
    && (!it.cap || caps[it.cap] !== false)
    && carrierOKActive(it.href, activeCarrier, caps)
    && verticalOK(it, tenant?.vertical, caps)
    && !hidden[it.href]?.hidden
  ), [user, gated, permissions, caps, activeCarrier, tenant?.vertical, hidden])

  // The GROUP half of the same question (index §38): a platform-only group opens for the platform
  // super admin alone. Here rather than retyped per hub, for the same reason as the item half.
  const groupOK = useCallback((group: { platformOnly?: boolean }) => platformOK(group, user), [user])

  const gateItems = useCallback((items: NavItem[]) => items
    .filter(it => !it.href.startsWith('/hub/'))   // a dashboard must not tile a link to itself
    .filter(itemOK), [itemOK])

  const canOpen = useCallback((href: string): boolean => {
    const path = navPath(href)
    // An EXACT NAV entry (including a deep-link entry like '/hr?tab=employees', which carries its
    // page's module + scopes verbatim) is answered by the item predicate.
    for (const g of NAV) {
      if (!platformOK(g, user)) continue
      for (const it of g.items) if (it.href === href || it.href === path) return itemOK(it)
    }
    // No NAV entry: the route guard's own answer, so a tile and the guard can never disagree.
    return !gated || canAccessPath(permissions, path)
  }, [user, itemOK, gated, permissions])

  const gateHubGroups = useCallback((groups: HubGroup[]) => groups
    .map(g => ({ ...g, items: g.items.filter(i => canOpen(i.href)) }))
    .filter(g => g.items.length > 0), [canOpen])

  return { groupOK, gateItems, canOpen, gateHubGroups, navCfg: navCfg || null }
}
