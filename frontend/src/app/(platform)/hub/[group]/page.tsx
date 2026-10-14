'use client'
// Generic tiled hub dashboard for ANY nav group (dashboard-builder Phase D2, owner spec 2026-09-01).
// /hub/<slug> renders the group's pages as collapsed master tiles (HubTiles) — the front door every
// converted sidebar group links to (rbac.ts '/hub/…' entries; the group's other items are tileOnly).
//
// LAYOUT RESOLUTION (mirrors the backend precedence, then degrades further client-side):
//   1. GET /commcalc/tile-layout?module=<slug>  → the designed layout (tenant override, else the
//      platform default the house org saved) — resolved server-side, cached client-side (CONFIG).
//   2. No layout anywhere (or the fetch fails)  → defaultHubGroups(): a deterministic auto-derived
//      tiling of the group's own NAV items, honoring the tenant's /admin/menu sub-categories.
// Either way mergeUnplacedItems() appends any visible page the layout does not name to a trailing
// 'More' tile — a page that ships after the dashboard was designed can never become unreachable.
//
// RBAC: every interior link is filtered through lib/hub-gate.ts — the ONE home for "may this viewer
// open this page from a tile", shared with /compliance and the two curated hubs — so a tile can never
// surface a page its viewer could not already see in the menu. A designed layout naming a page the
// viewer may not see simply renders without it (and a tile left empty by that filtering is dropped).
// The curated /payroll and /storeops hubs deliberately do NOT route here (they keep their hand-woven
// tiles + KPI rows), but they now dereference the same gate: not doing so was the 2026-10-14 defect.
import { useMemo } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import { useCachedApi, CONFIG } from '@/lib/cache'
import { NAV, platformOK, type NavItem } from '@/lib/rbac'
import { useHubGate } from '@/lib/hub-gate'
import HubTiles from '@/components/HubTiles'
import { slugGroup, defaultHubGroups, layoutToHubGroups, mergeUnplacedItems, subsFromNavLayout,
         subsFromItemTiles, type TileLayout } from '@/lib/tile-hubs'

type TileResp = { module: string; layout: TileLayout | null; resolved_from: 'tenant' | 'house' | null }

export default function HubDashboardPage() {
  const { group: slug } = useParams<{ group: string }>()
  const { user } = useAuth()

  // A platform-only group (the Super Admin Toolbox, index §38) resolves for the platform super admin
  // alone; anyone else gets the same "no dashboard here" notice as an unknown slug.
  const navGroup = useMemo(
    () => NAV.find(g => slugGroup(g.group) === slug && platformOK(g, user)) || null, [slug, user])

  // Both fetches are best-effort + cached (the same nav-config entry the sidebar shares). A failed
  // tile-layout read degrades to the auto-derived default; a failed nav-config read means no
  // caps/layout refinement — the hub still renders.
  const { gateItems, navCfg } = useHubGate()
  const { data: tileResp, loading } = useCachedApi<TileResp>(
    navGroup ? `/api/v1/commcalc/tile-layout?module=${encodeURIComponent(slug)}` : null, CONFIG)

  // THE VIEWER GATE is lib/hub-gate.ts — the ONE home, shared with /compliance and the two curated
  // hubs (/storeops, /payroll), which had no gate at all until 2026-10-14. It mirrors the sidebar
  // ((platform)/layout.tsx): RBAC only while login is enforced and a session exists, then the
  // platform-only gate, tenant capability, active-carrier lens, vertical, and the tenant nav-layout
  // `hidden` override. The group's own '/hub/…' entry is excluded there — a dashboard must not tile
  // a link to itself.
  // THIS group's own items — what the auto-derived default tiles, and the "not yet placed" tile, are
  // built from. A dashboard with no design still shows its own module, not the whole app.
  const visibleItems = useMemo<NavItem[]>(
    () => (navGroup ? gateItems(navGroup.items) : []), [navGroup, gateItems])

  // EVERY group's items, same gate. A DESIGNED tile may name a page from another group — that is the
  // entire point of the designer (owner 2026-09-07: "I just added inventory aging and inventory
  // values from finance to management overview … they are not showing up").
  //
  // `layoutToHubGroups` resolves each designed href against this map and DROPS anything it cannot
  // find. It used to be handed only `visibleItems`, so a cross-group pick could never resolve:
  // 'Inventory Values' (/accounts/inventory) lives in Finance and 'Inventory Aging'
  // (/commcalc/asset/aging) in Assets, while the hub being designed is Management Overview. Both
  // saved correctly and both vanished at render, with nothing said — and a tile made ONLY of
  // cross-group picks disappeared whole.
  //
  // The gate is applied to the wider set too, so composing across groups can never surface a page
  // the viewer is not allowed to open: this widens what a DESIGNER may place, never what a VIEWER
  // may see.
  const allVisibleItems = useMemo<NavItem[]>(
    () => gateItems(NAV.filter(g => platformOK(g, user)).flatMap(g => g.items)), [gateItems, user])

  const groups = useMemo(() => {
    if (!navGroup) return []
    const designed = layoutToHubGroups(tileResp?.layout, allVisibleItems)
    if (designed.length) return mergeUnplacedItems(designed, visibleItems)
    const menuSubs = subsFromNavLayout(navGroup.group, visibleItems, navCfg?.layout)
    const subs = menuSubs.length ? menuSubs : subsFromItemTiles(visibleItems)
    return defaultHubGroups(navGroup.group, visibleItems, subs)
  }, [navGroup, tileResp, visibleItems, allVisibleItems, navCfg])

  if (!navGroup) {
    return (
      <div className="card" style={{ maxWidth: 520, margin: '60px auto', padding: 32, textAlign: 'center' }}>
        <div style={{ fontSize: 28, marginBottom: 8 }}>🧭</div>
        <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 6 }}>No dashboard here</div>
        <p style={{ fontSize: 13.5, color: 'var(--text2)', margin: '0 0 16px', lineHeight: 1.5 }}>
          There is no module group called “{slug}”. It may have been renamed, or the link is out of
          date — the sidebar always lists every dashboard you can open.
        </p>
        <Link href="/" className="btn">← Back home</Link>
      </div>
    )
  }

  const hubMeta = navGroup.items.find(it => it.href === `/hub/${slug}`)
  const provenance = tileResp?.resolved_from === 'tenant' ? 'Customized for your company'
    : tileResp?.resolved_from === 'house' ? 'Platform default layout' : null

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>
          {hubMeta?.icon || '🧭'} {navGroup.group}
        </h1>
        {provenance && (
          <span title="Where this tile arrangement comes from — designable at Configuration → Dashboard Designer"
            style={{ fontSize: 11, color: 'var(--text3)', border: '1px solid var(--border)',
              borderRadius: 10, padding: '1px 8px' }}>{provenance}</span>
        )}
      </div>
      {loading && groups.length === 0 ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : groups.length === 0 ? (
        <div className="card" style={{ padding: 24, fontSize: 13.5, color: 'var(--text2)' }}>
          Nothing to show — none of this module&apos;s pages are enabled for your role. Ask your
          administrator if you believe you should see them.
        </div>
      ) : (
        <>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '-12px 0 16px' }}>
            Everything in {navGroup.group}, tiled. Click a tile to see the pages inside; single-page
            tiles open directly.
          </p>
          <HubTiles groups={groups} />
        </>
      )}
    </div>
  )
}
