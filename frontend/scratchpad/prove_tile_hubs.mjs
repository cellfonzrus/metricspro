// Proof for the tile-hub resolver (frontend/src/lib/tile-hubs.ts — dashboard-builder Phase D2).
// Verbatim re-impl of the shipped pure logic (prove_nav_layout.mjs style — KEEP IN SYNC), run
// against BOTH synthetic fixtures and the REAL NAV parsed out of rbac.ts (prove_reports_directory
// style), so the chunking/merge invariants are checked over every actual module group.
// Run: node scratchpad/prove_tile_hubs.mjs
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
const __dir = dirname(fileURLToPath(import.meta.url))
const SRC = readFileSync(join(__dir, '..', 'src', 'lib', 'rbac.ts'), 'utf8')

let pass = 0, fail = 0
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b)
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl of tile-hubs.ts ─────────────────────────────────────────────────────────────
const MORE_TILE = 'More'
const ITEM_ICON = '📄'
const TILE_ICON = '🗂️'
function slugGroup(name) {
  return String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}
const toHubItem = (it) => ({ href: it.href, icon: it.icon || ITEM_ICON, label: it.label || it.href, desc: '' })
const tileDesc = (items) => {
  const labels = items.map(i => i.label).slice(0, 3)
  return labels.join(' · ') + (items.length > 3 ? ' · …' : '')
}
const mkTile = (title, items, icon) => ({ title, icon: icon || items[0]?.icon || TILE_ICON, desc: tileDesc(items), items })
function defaultHubGroups(groupName, items, subs) {
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
    const extra = all.length % k
    const tiles = []
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
function subsFromNavLayout(groupName, items, layout) {
  const ov = layout?.items
  if (!ov) return []
  const names = []
  const byName = {}
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
function layoutToHubGroups(layout, visibleItems, opts) {
  if (!layout || !Array.isArray(layout.tiles)) return []
  const byHref = new Map(visibleItems.map(it => [it.href, it]))
  const groups = []
  for (const t of layout.tiles) {
    const items = []
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
    groups.push({ title: t.title || 'Untitled tile', icon: t.icon || items[0]?.icon || TILE_ICON, desc: t.desc || '', items })
  }
  return groups
}
function hubGroupsToLayout(groups) {
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
function mergeUnplacedItems(groups, navItems) {
  const placed = new Set(groups.flatMap(g => g.items.map(i => i.href)))
  const missing = navItems.filter(it => !placed.has(it.href))
  if (!missing.length) return groups
  const strays = missing.map(toHubItem)
  const at = groups.findIndex(g => g.title === MORE_TILE)
  if (at >= 0) return groups.map((g, i) => (i === at ? { ...g, items: [...g.items, ...strays] } : g))
  return [...groups, { title: MORE_TILE, icon: TILE_ICON, desc: 'Pages in this module not yet placed on a tile.', items: strays }]
}

// ── parse the REAL NAV out of rbac.ts (same line-based parser as prove_reports_directory) ─────────
function parseNav(src) {
  const start = src.indexOf('export const NAV: NavGroup[]')
  const end = src.indexOf('// Per-item override', start)
  const body = src.slice(start, end)
  const groups = []
  let cur = null
  for (const line of body.split('\n')) {
    const gm = line.match(/\{\s*group:\s*'([^']+)',\s*module:\s*'([^']+)',\s*items:\s*\[/)
    if (gm) { cur = { group: gm[1], module: gm[2], items: [] }; groups.push(cur) }
    const im = line.match(/\{\s*href:\s*'([^']+)',\s*label:\s*'([^']*)',\s*icon:\s*'([^']*)',\s*module:\s*'([^']+)'/)
    if (im && cur) cur.items.push({ href: im[1], label: im[2], icon: im[3], module: im[4], tileOnly: /tileOnly:\s*true/.test(line) })
  }
  return groups
}
const NAV = parseNav(SRC)
ok('parse: NAV non-empty', NAV.length >= 20)

// ── 1. slugGroup ─────────────────────────────────────────────────────────────────────────────────
ok('slug: Daily Closing → daily-closing', slugGroup('Daily Closing') === 'daily-closing')
ok('slug: Targets & Coaching → targets-coaching', slugGroup('Targets & Coaching') === 'targets-coaching')
ok('slug: Incentive Payout Plans', slugGroup('Incentive Payout Plans') === 'incentive-payout-plans')
ok('slug: Payroll & HR → payroll-hr', slugGroup('Payroll & HR') === 'payroll-hr')
ok('slug: trims edge separators', slugGroup('  ~Weird -- Name!  ') === 'weird-name')
ok('slug: empty/garbage → empty', slugGroup('') === '' && slugGroup('&&&') === '')
{
  const slugs = NAV.map(g => slugGroup(g.group))
  ok('slug: collision-free across every real NAV group', new Set(slugs).size === slugs.length)
  const keyRe = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,59}$/  // backend tile_layout module-key alphabet
  ok('slug: every real group slug is a valid backend module key', slugs.every(s => keyRe.test(s)))
}

// ── 2. defaultHubGroups: determinism + chunking ──────────────────────────────────────────────────
const items = (n, pfx = '/p') => Array.from({ length: n }, (_, i) =>
  ({ href: `${pfx}${i}`, label: `Page ${i}`, icon: `I${i}`, module: 'm' }))
{
  const a = defaultHubGroups('G', items(23))
  const b = defaultHubGroups('G', items(23))
  ok('deterministic: same input → identical output', eq(a, b))
}
ok('empty group → no tiles', eq(defaultHubGroups('G', []), []))
{
  const out = defaultHubGroups('CRM', items(9))
  ok('small group (≤10) → ONE tile titled "<Group> pages"', out.length === 1 && out[0].title === 'CRM pages')
  ok('small tile holds every item in natural order', eq(out[0].items.map(i => i.href), items(9).map(i => i.href)))
  ok('tile icon = first item icon', out[0].icon === 'I0')
}
for (const n of [11, 15, 23, 31, 40, 55]) {
  const out = defaultHubGroups('G', items(n))
  const k = Math.min(4, Math.max(2, Math.ceil(n / 10)))
  const sizes = out.map(t => t.items.length)
  ok(`big group n=${n}: ${k} tiles`, out.length === k)
  ok(`big group n=${n}: balanced (sizes differ ≤1) and complete`,
    Math.max(...sizes) - Math.min(...sizes) <= 1 && sizes.reduce((a, b) => a + b, 0) === n)
  ok(`big group n=${n}: tiles titled by their first item, order preserved`,
    out.every(t => t.title === t.items[0].label) &&
    eq(out.flatMap(t => t.items.map(i => i.href)), items(n).map(i => i.href)))
}
{
  // Sub-categories become tiles; unclaimed items form a trailing '<Group> pages' tile.
  const its = items(6)
  const subs = [{ name: 'Setup', items: [its[1], its[3]] }, { name: 'Reports', items: [its[4]] }]
  const out = defaultHubGroups('Ops', its, subs)
  ok('subs → one tile per sub, titled by sub name', out[0].title === 'Setup' && out[1].title === 'Reports')
  ok('subs → loose items land in trailing "<Group> pages" tile',
    out[2].title === 'Ops pages' && eq(out[2].items.map(i => i.href), ['/p0', '/p2', '/p5']))
  ok('empty subs list behaves as no subs', eq(defaultHubGroups('Ops', its, []), defaultHubGroups('Ops', its)))
}
// Real-NAV sanity: every group's auto default covers EVERY item exactly once (no merge needed).
for (const g of NAV) {
  const real = g.items.filter(it => !it.href.startsWith('/hub/'))
  const out = defaultHubGroups(g.group, real)
  const flat = out.flatMap(t => t.items.map(i => i.href))
  if (!eq([...flat].sort(), real.map(i => i.href).sort()) || out.some(t => t.items.length === 0)) {
    ok(`real NAV "${g.group}": default tiles cover every item, no empty tile`, false)
  } else { pass++ }
  ok(`real NAV "${g.group}": default needs no merge (same reference back)`,
    mergeUnplacedItems(out, real) === out)
}

// ── 3. subsFromNavLayout ─────────────────────────────────────────────────────────────────────────
{
  const its = items(5)
  const layout = { items: { '/p1': { sub: 'B' }, '/p2': { sub: 'A' }, '/p4': { sub: 'B' } }, subOrder: { G: ['A', 'B'] } }
  const subs = subsFromNavLayout('G', its, layout)
  ok('subsFromNavLayout: groups by sub, ranked by subOrder', eq(subs.map(s => s.name), ['A', 'B'])
    && eq(subs[1].items.map(i => i.href), ['/p1', '/p4']))
  ok('subsFromNavLayout: no layout → none', eq(subsFromNavLayout('G', its, undefined), []))
  const noOrder = subsFromNavLayout('G', its, { items: layout.items })
  ok('subsFromNavLayout: no subOrder → first-appearance order', eq(noOrder.map(s => s.name), ['B', 'A']))
}

// ── 4. layout ⇄ groups round-trip ────────────────────────────────────────────────────────────────
{
  const nav = items(6)
  const L = { version: 1, tiles: [
    { title: 'T1', icon: '🧭', desc: 'first', items: [{ href: '/p0', icon: 'X', label: 'Zero', desc: 'd0' }, { href: '/p2' }] },
    { title: 'T2', items: [{ href: '/p5' }] },
  ]}
  const groups = layoutToHubGroups(L, nav)
  ok('layout→groups: tile meta carried; item meta = layout fields, NAV fills gaps', eq(groups, [
    { title: 'T1', icon: '🧭', desc: 'first', items: [
      { href: '/p0', icon: 'X', label: 'Zero', desc: 'd0' },
      { href: '/p2', icon: 'I2', label: 'Page 2', desc: '' }] },
    { title: 'T2', icon: 'I5', desc: '', items: [{ href: '/p5', icon: 'I5', label: 'Page 5', desc: '' }] },
  ]))
  const round = hubGroupsToLayout(groups)
  ok('groups→layout→groups is stable (fixpoint round-trip)', eq(layoutToHubGroups(round, nav), groups))
  ok('round-trip preserves tile titles/order + item hrefs/order',
    eq(round.tiles.map(t => t.title), ['T1', 'T2'])
    && eq(round.tiles.map(t => t.items.map(i => i.href)), [['/p0', '/p2'], ['/p5']]))
  ok('groups→layout: version 1 + empty-string desc omitted (minimal stored JSON)', round.version === 1
    && !('desc' in round.tiles[1]) && !('desc' in round.tiles[0].items[1]))
}
{
  // RBAC filter: an item NOT among visibleItems is dropped; a tile emptied by that is dropped too.
  const nav = items(3)
  const L = { version: 1, tiles: [
    { title: 'Vis', items: [{ href: '/p0' }, { href: '/secret' }] },
    { title: 'Gone', items: [{ href: '/hidden' }] },
  ]}
  const out = layoutToHubGroups(L, nav)
  ok('unknown/invisible hrefs dropped; emptied tiles dropped', eq(out.map(t => t.title), ['Vis'])
    && eq(out[0].items.map(i => i.href), ['/p0']))
  const kept = layoutToHubGroups(L, nav, { keepUnknown: true })
  ok('keepUnknown (designer): unknown hrefs survive as raw hrefs; empty tiles survive',
    kept.length === 2 && kept[0].items.length === 2 && kept[0].items[1].label === '/secret')
  ok('null/garbage layout → no groups', eq(layoutToHubGroups(null, nav), []) && eq(layoutToHubGroups({ version: 1 }, nav), []))
}

// ── 5. mergeUnplacedItems — the newly-shipped-page invariant ─────────────────────────────────────
{
  const nav = items(5)
  const designed = layoutToHubGroups({ version: 1, tiles: [{ title: 'Main', items: [{ href: '/p0' }, { href: '/p3' }] }] }, nav)
  const merged = mergeUnplacedItems(designed, nav)
  ok('unplaced items append to a trailing More tile', merged.length === 2
    && merged[1].title === MORE_TILE && eq(merged[1].items.map(i => i.href), ['/p1', '/p2', '/p4']))
  ok('merge does not mutate the input', designed.length === 1 && designed[0].items.length === 2)
  const again = mergeUnplacedItems(merged, nav)
  ok('idempotent: nothing missing → same reference back', again === merged)
  // An admin-designed tile literally titled 'More' gains the strays instead of a second More tile.
  const withMore = [{ title: 'More', icon: 'M', desc: '', items: [{ href: '/p0', icon: 'I0', label: 'Page 0', desc: '' }] }]
  const m2 = mergeUnplacedItems(withMore, nav)
  ok('existing More tile absorbs strays (no duplicate More)', m2.length === 1
    && eq(m2[0].items.map(i => i.href), ['/p0', '/p1', '/p2', '/p3', '/p4']))
  // Full-coverage invariant, per REAL group: designed-with-omissions + merge always covers all items.
  let covered = true
  for (const g of NAV) {
    const real = g.items.filter(it => !it.href.startsWith('/hub/'))
    if (real.length < 2) continue
    const partial = layoutToHubGroups({ version: 1, tiles: [{ title: 'Only one', items: [{ href: real[0].href }] }] }, real)
    const out = mergeUnplacedItems(partial, real)
    const flat = new Set(out.flatMap(t => t.items.map(i => i.href)))
    if (!real.every(it => flat.has(it.href))) covered = false
  }
  ok('REAL NAV: partial layout + merge covers every item of every group', covered)
}

// ── 6. D2 NAV conversion invariants (real rbac.ts source) ────────────────────────────────────────
{
  const hubGroups = NAV.filter(g => g.items.some(it => it.href.startsWith('/hub/')))
  ok('16 groups carry a /hub dashboard entry', hubGroups.length === 16)
  let slugOK = true, first = true, restTileOnly = true, hubNotTileOnly = true
  for (const g of hubGroups) {
    const hubs = g.items.filter(it => it.href.startsWith('/hub/'))
    if (hubs.length !== 1 || hubs[0].href !== '/hub/' + slugGroup(g.group)) slugOK = false
    if (g.items[0] !== hubs[0]) first = false
    if (hubs[0].tileOnly) hubNotTileOnly = false
    if (!g.items.filter(it => !it.href.startsWith('/hub/')).every(it => it.tileOnly)) restTileOnly = false
  }
  ok('each hub href == /hub/<slugGroup(group)> (route and entry can never disagree)', slugOK)
  ok('hub entry is FIRST in its group', first)
  ok('hub entry itself is never tileOnly', hubNotTileOnly)
  ok('every other item in a converted group is tileOnly', restTileOnly)
  const skip = ['Configuration', 'Reports', 'Approvals', 'Chat', 'Workforce', 'Payroll & HR']
  ok('skipped groups carry no hub entry', NAV.filter(g => skip.includes(g.group))
    .every(g => !g.items.some(it => it.href.startsWith('/hub/'))))
}

console.log(`\ntile-hubs: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
