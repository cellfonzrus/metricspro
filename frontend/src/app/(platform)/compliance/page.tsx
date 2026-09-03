'use client'
// Flags & Compliance Dashboard (owner directive 2026-09-03: "Flags and Compliance should be a
// separate Dashboard and every flag and compliance issue should be under that").
//
// TWO layers, both over existing machinery:
//   · COUNTS — GET /commcalc/compliance-summary: one thin count pass over the platform's existing
//     flag/exception/compliance queues (same queries as the pages that own them). A failed probe
//     shows "—" with a note, never a fake 0.
//   · TILES — the dashboard-builder D1 tile layout for module 'flags-compliance' (house default
//     seeded by mig 948, tenant-editable in the Dashboard Designer), rendered with the same RBAC
//     filtering as the generic /hub/[group] route: a tile can never surface a page its viewer
//     could not already open from the menu. Falls back to the auto-derived tiling when no layout
//     resolves (the D2 degradation contract).
import { useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { useAuth, useActiveCarrier } from '@/lib/auth-context'
import { useCachedApi, CONFIG } from '@/lib/cache'
import { NAV, canSeeItem, carrierOKActive, type NavItem, type NavLayout } from '@/lib/rbac'
import HubTiles from '@/components/HubTiles'
import StatTile from '@/components/StatTile'
import { slugGroup, defaultHubGroups, layoutToHubGroups, mergeUnplacedItems, subsFromNavLayout,
         type TileLayout } from '@/lib/tile-hubs'

type NavCfg = { labels?: Record<string, string>; capabilities?: Record<string, boolean | null>; layout?: NavLayout }
type TileResp = { module: string; layout: TileLayout | null; resolved_from: 'tenant' | 'house' | null }

const GROUP_NAME = 'Flags & Compliance'
const SLUG = slugGroup(GROUP_NAME) // 'flags-compliance' — the D1 tile-layout module key

export default function ComplianceDashboardPage() {
  const { permissions, session, rbacEnabled } = useAuth()
  const { activeCarrier } = useActiveCarrier()
  const [summary, setSummary] = useState<any>(null)
  const [sumErr, setSumErr] = useState('')

  useEffect(() => {
    api('/api/v1/commcalc/compliance-summary')
      .then(setSummary)
      .catch(e => { setSumErr(e?.message || String(e)); setSummary(null) })
  }, [])

  const navGroup = useMemo(() => NAV.find(g => slugGroup(g.group) === SLUG) || null, [])
  const { data: navCfg } = useCachedApi<NavCfg>('/api/v1/commcalc/nav-config', CONFIG)
  const { data: tileResp } = useCachedApi<TileResp>(`/api/v1/commcalc/tile-layout?module=${SLUG}`, CONFIG)

  // exact sidebar gating (the /hub/[group] predicates) — this page's own entry excluded
  const visibleItems = useMemo<NavItem[]>(() => {
    if (!navGroup) return []
    const caps = navCfg?.capabilities || {}
    const gated = rbacEnabled !== false && !!session
    return navGroup.items
      .filter(it => it.href !== '/compliance' && !it.href.startsWith('/hub/'))
      .filter(it => !gated || canSeeItem(permissions, it))
      .filter(it => !it.cap || caps[it.cap] !== false)
      .filter(it => carrierOKActive(it.href, activeCarrier, caps))
      .filter(it => !navCfg?.layout?.items?.[it.href]?.hidden)
  }, [navGroup, navCfg, permissions, session, rbacEnabled, activeCarrier])

  const groups = useMemo(() => {
    if (!navGroup) return []
    const designed = layoutToHubGroups(tileResp?.layout, visibleItems)
    if (designed.length) return mergeUnplacedItems(designed, visibleItems)
    const subs = subsFromNavLayout(navGroup.group, visibleItems, navCfg?.layout)
    return defaultHubGroups(navGroup.group, visibleItems, subs)
  }, [navGroup, tileResp, visibleItems, navCfg])

  const cats: any[] = summary?.categories || []
  // only show count tiles whose target page the viewer can actually open
  const visibleHrefs = useMemo(() => new Set(visibleItems.map(i => i.href)), [visibleItems])
  const shownCats = cats.filter(c => visibleHrefs.has(c.href) || c.href === '/accounts/pl')

  return (
    <div style={{ maxWidth: 1250 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🛡️ Flags &amp; Compliance</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13.5, margin: '4px 0 14px' }}>
        Every flag and compliance queue on the platform in one place — the counts come from each
        queue&apos;s own machinery{summary?.period ? <> (period <b>{summary.period}</b>)</> : null}.
        A dash means that check could not run — it is never silently shown as zero.
      </p>

      {sumErr && <div className="card" style={{ padding: 12, color: 'crimson', marginBottom: 12, fontSize: 13 }}>Summary unavailable: {sumErr}</div>}

      {shownCats.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 10, marginBottom: 18 }}>
          {shownCats.map(c => (
            <StatTile key={c.key} label={c.label} href={c.href}
              value={c.count == null ? '—' : c.count}
              accent={c.count ? 'crimson' : undefined}
              sub={c.count == null ? (c.note || 'unavailable') : (c.count === 0 ? 'nothing open' : 'open items')} />
          ))}
        </div>
      )}

      <HubTiles groups={groups} />
      {groups.length === 0 && (
        <div className="card" style={{ padding: 16, fontSize: 13, color: 'var(--text2)' }}>
          None of the compliance pages are visible for your role.
        </div>
      )}
    </div>
  )
}
