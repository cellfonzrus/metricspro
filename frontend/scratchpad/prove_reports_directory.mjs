// Proof for the Reports directory (rbac.ts applyNavLayout code-level default) + backward-compat of the
// existing move/duplicate/hide layout mechanism. Verbatim re-impl of the NEW applyNavLayout, run against
// the REAL NAV / REPORT_DIRECTORY / REPORT_CATEGORIES parsed out of frontend/src/lib/rbac.ts.
// Run: node scratchpad/prove_reports_directory.mjs
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
const __dir = dirname(fileURLToPath(import.meta.url))
const SRC = readFileSync(join(__dir, '..', 'src', 'lib', 'rbac.ts'), 'utf8')

let pass = 0, fail = 0
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b)
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

// ── parse the REAL rbac.ts ─────────────────────────────────────────────────────────────────────────
// NAV groups + items (line-based; NAV items are one per line: { href: '..', label: '..', icon: '..', module: '..' ... })
function parseNav(src) {
  const start = src.indexOf('export const NAV: NavGroup[]')
  const end = src.indexOf('// Per-item override', start)
  const body = src.slice(start, end)
  const groups = []
  let cur = null
  for (const line of body.split('\n')) {
    const gm = line.match(/\{\s*group:\s*'([^']+)',\s*module:\s*'([^']+)',\s*items:\s*\[/)
    if (gm) { cur = { group: gm[1], module: gm[2], items: [] }; groups.push(cur) }
    const im = line.match(/\{\s*href:\s*'([^']+)',\s*label:\s*'[^']*',\s*icon:\s*'[^']*',\s*module:\s*'([^']+)'/)
    if (im && cur) cur.items.push({ href: im[1], label: im[1], icon: 'x', module: im[2] })
  }
  return groups
}
function parseCategories(src) {
  const seg = src.slice(src.indexOf('export const REPORT_CATEGORIES'), src.indexOf('export const REPORT_DIRECTORY'))
  return [...seg.matchAll(/\{\s*key:\s*'(\w+)',\s*label:\s*'([^']+)'\s*\}/g)].map(m => ({ key: m[1], label: m[2] }))
}
function parseDirectory(src) {
  const seg = src.slice(src.indexOf('export const REPORT_DIRECTORY'), src.indexOf('const REPORT_CATEGORY_LABEL'))
  return [...seg.matchAll(/\['(\/[^']+)',\s*'(\w+)'\]/g)].map(m => [m[1], m[2]])
}
const NAV = parseNav(SRC)
const REPORT_CATEGORIES = parseCategories(SRC)
const REPORT_DIRECTORY = parseDirectory(SRC)
const REPORT_CATEGORY_LABEL = Object.fromEntries(REPORT_CATEGORIES.map(c => [c.key, c.label]))
const navHrefs = new Set(NAV.flatMap(g => g.items.map(i => i.href)))

// ── verbatim re-impl of the NEW applyNavLayout (frontend/src/lib/rbac.ts) ────────────────────────────
function applyNavLayout(groups, layout) {
  const ov = layout?.items
  const moduleByGroup = {}, defaultOrder = []
  groups.forEach(g => { if (!(g.group in moduleByGroup)) { moduleByGroup[g.group] = g.module; defaultOrder.push(g.group) } })
  const targets = []
  const placedGH = new Set()
  const push = (group, it) => { const k = group + '|' + it.href; if (placedGH.has(k)) return; placedGH.add(k); targets.push({ group, it }) }
  const surviving = new Map()
  for (const g of groups) for (const it of g.items) {
    const o = ov?.[it.href]
    if (o?.hidden) continue
    surviving.set(it.href, it)
    const primary = (o?.group && o.group.trim()) || g.group
    push(primary, it)
    if (o?.also && o.also.length) { for (const a of o.also) { const ag = (a || '').trim(); if (ag) push(ag, it) } }
  }
  if (!layout?.hideReportsDirectory) {
    for (const [href, catKey] of REPORT_DIRECTORY) {
      const it = surviving.get(href); const cat = REPORT_CATEGORY_LABEL[catKey]
      if (it && cat) { moduleByGroup[cat] = moduleByGroup[cat] || it.module; push(cat, it) }
    }
  }
  const seen = new Set(), order = []
  defaultOrder.forEach(g => { if (targets.some(t => t.group === g)) { order.push(g); seen.add(g) } })
  targets.forEach(t => { if (!seen.has(t.group)) { order.push(t.group); seen.add(t.group) } })
  return order.map(group => ({
    group, module: moduleByGroup[group] || (targets.find(t => t.group === group)?.it.module || ''),
    items: targets.filter(t => t.group === group).map(t => t.it),
  })).filter(g => g.items.length > 0)
}
const shape = gs => gs.map(g => ({ group: g.group, items: g.items.map(i => i.href) }))
const catLabels = new Set(REPORT_CATEGORIES.map(c => c.label))
const moduleGroups = g => g.filter(x => !catLabels.has(x.group))
const reportGroups = g => g.filter(x => catLabels.has(x.group))

// ── A. SOURCE PARITY ─────────────────────────────────────────────────────────────────────────────
ok('parse: NAV non-empty', NAV.length >= 10 && navHrefs.size >= 60)
ok('parse: 8 report categories', REPORT_CATEGORIES.length === 8)
ok('parse: directory non-empty', REPORT_DIRECTORY.length >= 50)
const deadHrefs = REPORT_DIRECTORY.filter(([h]) => !navHrefs.has(h)).map(([h]) => h)
ok('every REPORT_DIRECTORY href is a real NAV item (no dead entries): ' + deadHrefs.join(','), deadHrefs.length === 0)
const badCat = REPORT_DIRECTORY.filter(([, k]) => !REPORT_CATEGORY_LABEL[k]).map(([h, k]) => h + '=' + k)
ok('every directory category key resolves to a category label: ' + badCat.join(','), badCat.length === 0)
const dirHrefSet = new Set(REPORT_DIRECTORY.map(([h]) => h))
ok('no href listed in TWO categories', dirHrefSet.size === REPORT_DIRECTORY.length)

// ── B. FULL SUPER-ADMIN NAV (every item survives) ──────────────────────────────────────────────────
const full = applyNavLayout(NAV, undefined)
// B1: module groups preserved byte-identically (order + items) — directory is purely ADDITIVE
ok('module groups unchanged vs raw NAV (order + items)', eq(shape(moduleGroups(full)), shape(NAV)))
// B2: the report categories all appear, in REPORT_CATEGORIES order, after the module groups
const rgLabels = reportGroups(full).map(g => g.group)
const expectedOrder = REPORT_CATEGORIES.map(c => c.label).filter(l => rgLabels.includes(l))
ok('report categories render in declared order, after module groups', eq(rgLabels, expectedOrder))
// B3: every directory href appears under its category (a duplicate), AS THE SAME OBJECT as its module copy
{
  let sameObj = true, present = true
  for (const [href, catKey] of REPORT_DIRECTORY) {
    const cat = REPORT_CATEGORY_LABEL[catKey]
    const rg = full.find(g => g.group === cat)
    const inCat = rg?.items.find(i => i.href === href)
    // its ORIGINAL module-group copy
    let orig = null
    for (const g of full) { if (!catLabels.has(g.group)) { const f = g.items.find(i => i.href === href); if (f) orig = f } }
    if (!inCat) present = false
    if (inCat && orig && inCat !== orig) sameObj = false
  }
  ok('every report href is duplicated into its category', present)
  ok('directory copy is the SAME NavItem object as the module copy (identical RBAC surface)', sameObj)
}
// B4: no duplicate href within any single group (module OR report)
{
  let dupd = false
  for (const g of full) { const s = new Set(); for (const i of g.items) { if (s.has(i.href)) dupd = true; s.add(i.href) } }
  ok('no href renders twice within one group', !dupd)
}

// ── C. RBAC-IDENTICAL: a role lacking module X sees NO X reports in the directory ───────────────────
// Simulate the layout.tsx pre-filter (canSeeItem) dropping every `asset` + `accounts` item.
// Gate on the item's MODULE (the real RBAC key), not its URL prefix: /commcalc/asset/hotsheet-recon is a
// `commissions` item that lives under the asset URL, so it legitimately survives an asset-less role.
{
  const filtered = NAV.map(g => ({ ...g, items: g.items.filter(it => it.module !== 'asset' && it.module !== 'accounts') }))
                      .filter(g => g.items.length > 0)
  const out = applyNavLayout(filtered, undefined)
  const leaked = out.flatMap(g => g.items).filter(i => i.module === 'asset' || i.module === 'accounts').map(i => i.href)
  ok('no asset/accounts-gated report anywhere (incl. directory) when the role lacks those modules: ' + leaked.join(','), leaked.length === 0)
  // the Finance category (all `accounts`/finance items gated off) fully collapses
  const financeCat = out.find(g => g.group === REPORT_CATEGORY_LABEL['finance'])
  const financeAccounts = financeCat ? financeCat.items.filter(i => i.module === 'accounts').length : 0
  ok('Finance category holds no accounts-gated report for an accounts-less role', financeAccounts === 0)
}

// ── D. TENANT MENU-LAYOUT OVERRIDES still apply, and interact correctly ─────────────────────────────
// D1: hidden item → gone from BOTH its module group AND the directory
{
  const out = applyNavLayout(NAV, { items: { '/commcalc/sales-report': { hidden: true } } })
  const all = out.flatMap(g => g.items.map(i => i.href))
  ok('hidden item removed everywhere incl. Reports directory', !all.includes('/commcalc/sales-report'))
}
// D2: moved item still gets its directory copy (Reports is orthogonal to primary placement)
{
  const out = applyNavLayout(NAV, { items: { '/commcalc/sales-report': { group: 'Assets' } } })
  const salesCat = out.find(g => g.group === REPORT_CATEGORY_LABEL['sales'])
  ok('moved item still appears in its Reports category', !!salesCat?.items.find(i => i.href === '/commcalc/sales-report'))
  const assetsGrp = out.find(g => g.group === 'Assets')
  ok('moved item now sits under its new primary group too', !!assetsGrp?.items.find(i => i.href === '/commcalc/sales-report'))
}
// D3: admin manually `also`-duplicates an item INTO a Reports category → no double render (global dedup)
{
  const out = applyNavLayout(NAV, { items: { '/commcalc/sales-report': { also: ['Reports · Sales'] } } })
  const salesCat = out.find(g => g.group === 'Reports · Sales')
  const count = salesCat.items.filter(i => i.href === '/commcalc/sales-report').length
  ok('manual also into a Reports category does not double-render', count === 1)
}
// D4: whole-directory opt-out
{
  const out = applyNavLayout(NAV, { hideReportsDirectory: true })
  ok('hideReportsDirectory suppresses the entire directory', reportGroups(out).length === 0)
  ok('opt-out leaves module groups byte-identical to raw NAV', eq(shape(out), shape(NAV)))
}

// ── E. ACCORDION TIE-BREAK: a report page resolves to its MODULE group, not the Reports category ─────
// layout.tsx picks the longest-href match, FIRST group encountered on ties. Module groups come first.
{
  const out = applyNavLayout(NAV, undefined)
  const path = '/commcalc/sales-report'
  let activeGroup = null, bestLen = -1
  for (const g of out) for (const it of g.items) {
    if ((path === it.href || path.startsWith(it.href + '/')) && it.href.length > bestLen) { activeGroup = g.group; bestLen = it.href.length }
  }
  ok('active accordion group for a report page is the MODULE group (Commissions), not a Reports category',
     activeGroup === 'Commissions')
}

// ── F. HIDE-REPORTS TOGGLE (OWNER DIRECTIVE 2026-07-18) — designer round-trip + full suppression ─────
// buildPayload / loadFlag mirror the admin/menu designer: the flag rides in the SAME nav-layout object,
// emitted ONLY when ON, so a tenant that never toggles it stores byte-identically to before.
function buildPayload(items, extraGroups, hideReports) {
  return { items, groups: extraGroups, ...(hideReports ? { hideReportsDirectory: true } : {}) }
}
const loadFlag = navcfg => !!navcfg?.layout?.hideReportsDirectory

// F1: OFF (default) → no key emitted → byte-identical to a pre-flag payload
ok('toggle OFF: payload omits hideReportsDirectory (byte-identical to before)', eq(buildPayload({}, [], false), { items: {}, groups: [] }))
// F2: ON → key present and true
ok('toggle ON: payload carries hideReportsDirectory:true', buildPayload({}, [], true).hideReportsDirectory === true)
// F3: load ← save round-trip through the one layout object, honored end-to-end by applyNavLayout
{
  const saved = buildPayload({ '/commcalc/sales-report': { hidden: true } }, ['Field Ops'], true)
  ok('round-trip: load reads the flag back as ON', loadFlag({ layout: saved }) === true)
  ok('round-trip: flag rides alongside items+groups in ONE object', 'items' in saved && 'groups' in saved && saved.hideReportsDirectory === true)
  const out = applyNavLayout(NAV, saved)
  ok('round-trip: the persisted ON object suppresses the directory via applyNavLayout', reportGroups(out).length === 0)
}
// F4: absent flag (legacy layout) loads as OFF
ok('load: legacy layout with no flag → toggle OFF', loadFlag({ layout: { items: {}, groups: [] } }) === false && loadFlag({}) === false)
// F5: FULL SUPPRESSION rigor — flag ON yields ZERO "Reports ·" groups, no empty stub, no orphan entry,
//     and module groups are byte-identical (order + membership + count) to the flag-OFF module groups.
{
  const on = applyNavLayout(NAV, { hideReportsDirectory: true })
  const offModules = moduleGroups(applyNavLayout(NAV, undefined))
  ok('suppress: no group label starts with "Reports ·"', on.every(g => !g.group.startsWith('Reports ·')))
  ok('suppress: no empty group stub survives', on.every(g => g.items.length > 0))
  ok('suppress: module groups byte-identical to the flag-off module groups', eq(shape(on), shape(offModules)))
  ok('suppress: total item count equals module-only count (no lingering directory duplicates)',
     on.reduce((n, g) => n + g.items.length, 0) === offModules.reduce((n, g) => n + g.items.length, 0))
}

console.log(`\nReports directory: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
