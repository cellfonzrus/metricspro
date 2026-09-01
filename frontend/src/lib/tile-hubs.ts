// ── Tiled-hub layout resolution (dashboard-builder Phase D2, owner spec 2026-09-01) ──────────────
// PURE, DB/network-free logic shared by the generic hub route ((platform)/hub/[group]/page.tsx) and
// the drag-and-drop designer ((platform)/admin/dashboards/page.tsx). Everything here is deterministic
// and proven in frontend/scratchpad/prove_tile_hubs.mjs (verbatim re-impl, prove_nav_layout style).
//
// The MODEL: every NAV group can render as a tiled dashboard of MASTER tiles (HubTiles.tsx cards),
// each holding interior page links. WHICH tiles exist and what sits inside them is per-org CONFIG
// (backend commcalc/tile_layout.py — tenant row > house/platform-default row > null). When no row
// exists anywhere, `defaultHubGroups` auto-derives a sensible layout from the group's own NAV items,
// so every module has a working dashboard on day one with zero configuration.
//
// THE NEWLY-SHIPPED-PAGE INVARIANT (mirrors applyNavLayout's "anything unlisted keeps its natural
// place"): a saved layout never freezes a module against future pages. `mergeUnplacedItems` appends
// any visible NAV item the layout does not name to a trailing 'More' tile, so a page that ships
// after an admin designed the dashboard still appears — it is never silently unreachable.
import type { HubGroup, HubItem } from '@/components/HubTiles'
import type { NavItem, NavSub, NavLayout } from '@/lib/rbac'

// Canonical API layout shape — mirror of backend tile_layout.sanitize_tile_layout output.
export type TileLayoutItem = { href: string; icon?: string; label?: string; desc?: string }
export type TileLayoutTile = { title: string; icon?: string; desc?: string; items: TileLayoutItem[] }
export type TileLayout = { version: number; tiles: TileLayoutTile[] }

/** The trailing catch-all tile `mergeUnplacedItems` appends unplaced pages to. */
export const MORE_TILE = 'More'
/** Fallback glyphs when neither the layout nor the NAV item carries one. */
const ITEM_ICON = '📄'
const TILE_ICON = '🗂️'

// ── slugGroup ────────────────────────────────────────────────────────────────────────────────────
/** URL slug for a NAV group name: 'Daily Closing' → 'daily-closing', 'Targets & Coaching' →
 *  'targets-coaching'. Lowercase; every run of non-alphanumerics collapses to one '-'; no leading/
 *  trailing '-'. Deterministic + collision-free across the current NAV (all group names differ in
 *  their alphanumerics). Also used VERBATIM as the backend tile-layout `module` key, which accepts
 *  `[A-Za-z0-9][A-Za-z0-9_.:/-]{0,59}` — this alphabet is a strict subset. */
export function slugGroup(name: string): string {
  return String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

// ── defaultHubGroups ─────────────────────────────────────────────────────────────────────────────
const toHubItem = (it: NavItem): HubItem => ({
  href: it.href, icon: it.icon || ITEM_ICON, label: it.label || it.href, desc: '',
})
// Deterministic one-line tile description from its items: first three labels, elided when longer.
const tileDesc = (items: HubItem[]): string => {
  const labels = items.map(i => i.label).slice(0, 3)
  return labels.join(' · ') + (items.length > 3 ? ' · …' : '')
}
const mkTile = (title: string, items: HubItem[], icon?: string): HubGroup => ({
  title, icon: icon || items[0]?.icon || TILE_ICON, desc: tileDesc(items), items,
})

/**
 * Auto-derive a HubGroup[] (master tiles) for ANY nav group from its (already access-filtered)
 * items — the built-in default a module renders when no tile layout is saved anywhere. Deterministic:
 * same inputs → same tiles, always.
 *
 *   · The group HAS sub-categories (tenant NavLayout `sub` assignments — pass them via `subs`):
 *     each sub becomes one master tile (title = sub name, icon = its first item's icon); items no
 *     sub claims form a trailing '<Group> pages' tile.
 *   · No subs, BIG group (>10 items): split the natural order into 2–4 balanced chunks
 *     (⌈n/10⌉ tiles, capped at 4, floored at 2; chunk sizes differ by at most one), each tile
 *     titled by its first item's label.
 *   · No subs, small group: ONE tile titled '<Group> pages' holding everything.
 */
export function defaultHubGroups(groupName: string, items: NavItem[], subs?: NavSub[]): HubGroup[] {
  const all = items.map(toHubItem)
  const realSubs = (subs || []).filter(s => s.items.length > 0)
  if (realSubs.length) {
    const claimed = new Set(realSubs.flatMap(s => s.items.map(i => i.href)))
    const tiles = realSubs.map(s => mkTile(s.name, s.items.map(toHubItem)))
    const loose = all.filter(i => !claimed.has(i.href))
    if (loose.length) tiles.push(mkTile(`${groupName} pages`, loose))
    return tiles
  }
  if (all.length === 0) return []
  if (all.length > 10) {
    const k = Math.min(4, Math.max(2, Math.ceil(all.length / 10)))
    const base = Math.floor(all.length / k)
    const extra = all.length % k // first `extra` chunks take one more — balanced, deterministic
    const tiles: HubGroup[] = []
    let at = 0
    for (let c = 0; c < k; c++) {
      const size = base + (c < extra ? 1 : 0)
      const chunk = all.slice(at, at + size)
      at += size
      tiles.push(mkTile(chunk[0].label, chunk))
    }
    return tiles
  }
  return [mkTile(`${groupName} pages`, all)]
}

// ── Tenant-NavLayout sub-categories for ONE group ────────────────────────────────────────────────
/** The sub-categories the tenant's /admin/menu layout assigns WITHIN one group, over the given
 *  (already-filtered) items — same claim rule as applyNavLayout: an item nests only when ITS OWN
 *  override names a `sub`, and sub order honors `layout.subOrder[group]` ("listed first, natural
 *  order after"). Lets defaultHubGroups turn the tenant's own menu sub-headings into master tiles. */
export function subsFromNavLayout(groupName: string, items: NavItem[], layout?: NavLayout): NavSub[] {
  const ov = layout?.items
  if (!ov) return []
  const names: string[] = []
  const byName: Record<string, NavItem[]> = {}
  for (const it of items) {
    const s = (ov[it.href]?.sub || '').trim()
    if (!s) continue
    if (!byName[s]) { byName[s] = []; names.push(s) }
    byName[s].push(it)
  }
  const want = layout?.subOrder?.[groupName]
  const ranked = !want?.length ? names : [...names].sort((a, b) => {
    const ia = want.indexOf(a), ib = want.indexOf(b)
    if (ia === -1 && ib === -1) return names.indexOf(a) - names.indexOf(b)
    if (ia === -1) return 1
    if (ib === -1) return -1
    return ia - ib
  })
  return ranked.map(name => ({ name, items: byName[name] }))
}

// ── layout ⇄ HubGroup converters ─────────────────────────────────────────────────────────────────
/**
 * Saved API layout → renderable HubGroup[]. `visibleItems` are the caller-visible NAV items of the
 * group (canSeeItem/cap/carrier-filtered): a layout item whose href is NOT among them is DROPPED —
 * that is exactly how a designed dashboard stays per-role RBAC-gated (an admin's layout can name a
 * page a store rep may not see; the rep's render simply omits it). Pass `keepUnknown` (designer
 * only) to instead keep such items visible as raw hrefs, so loading + resaving a layout that names
 * a since-renamed page never silently deletes the admin's own design. Metadata precedence per
 * field: the layout's own label/icon/desc win; the NAV item fills the gaps. Tiles left with zero
 * items are dropped (never a dead card), except under keepUnknown where the designer shows them.
 */
export function layoutToHubGroups(
  layout: TileLayout | null | undefined, visibleItems: NavItem[], opts?: { keepUnknown?: boolean },
): HubGroup[] {
  if (!layout || !Array.isArray(layout.tiles)) return []
  const byHref = new Map(visibleItems.map(it => [it.href, it]))
  const groups: HubGroup[] = []
  for (const t of layout.tiles) {
    const items: HubItem[] = []
    for (const raw of t.items || []) {
      const nav = byHref.get(raw.href)
      if (!nav && !opts?.keepUnknown) continue
      items.push({
        href: raw.href,
        icon: raw.icon || nav?.icon || ITEM_ICON,
        label: raw.label || nav?.label || raw.href,
        desc: raw.desc || '',
      })
    }
    if (!items.length && !opts?.keepUnknown) continue
    groups.push({
      title: t.title || 'Untitled tile',
      icon: t.icon || items[0]?.icon || TILE_ICON,
      desc: t.desc || '',
      items,
    })
  }
  return groups
}

/** Renderable/designed HubGroup[] → the API layout to PUT. Empty-string icon/desc are omitted so
 *  the stored JSON stays minimal and round-trips through the backend sanitizer byte-stably. */
export function hubGroupsToLayout(groups: HubGroup[]): TileLayout {
  return {
    version: 1,
    tiles: groups.map(g => ({
      title: g.title,
      ...(g.icon ? { icon: g.icon } : {}),
      ...(g.desc ? { desc: g.desc } : {}),
      items: g.items.map(it => ({
        href: it.href,
        ...(it.icon ? { icon: it.icon } : {}),
        ...(it.label ? { label: it.label } : {}),
        ...(it.desc ? { desc: it.desc } : {}),
      })),
    })),
  }
}

// ── mergeUnplacedItems — the newly-shipped-page invariant ────────────────────────────────────────
/**
 * Append every visible NAV item the layout does not name to a trailing 'More' tile, so a page that
 * ships AFTER a dashboard was designed still surfaces (a saved layout must never freeze a module).
 * PURE: returns the input array untouched (same reference) when nothing is missing; otherwise a new
 * array where an existing 'More' tile (if any) gains the strays, else one is created at the end.
 */
export function mergeUnplacedItems(groups: HubGroup[], navItems: NavItem[]): HubGroup[] {
  const placed = new Set(groups.flatMap(g => g.items.map(i => i.href)))
  const missing = navItems.filter(it => !placed.has(it.href))
  if (!missing.length) return groups
  const strays = missing.map(toHubItem)
  const at = groups.findIndex(g => g.title === MORE_TILE)
  if (at >= 0) {
    return groups.map((g, i) => (i === at ? { ...g, items: [...g.items, ...strays] } : g))
  }
  return [...groups, {
    title: MORE_TILE, icon: TILE_ICON,
    desc: 'Pages in this module not yet placed on a tile.', items: strays,
  }]
}
