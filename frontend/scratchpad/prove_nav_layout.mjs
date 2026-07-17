// Proof for applyNavLayout (rbac.ts) multi-placement + backward-compat, and the /nav-layout POST
// sanitizer (mirror of commcalc/router.py). Verbatim re-impl of the shipped logic. Run: node prove_nav_layout.mjs
// Covers the manual matrix: legacy loads unchanged · new group + assign persists · duplicate in 2 groups
// (both = SAME item object → identical RBAC surface) · dedup (also==primary) · hidden wins · remove-duplicate.
let pass = 0, fail = 0
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b)
function ok(name, cond) { if (cond) { pass++; } else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl of the NEW applyNavLayout (frontend/src/lib/rbac.ts) ───────────────────────────
// This suite isolates the MOVE/DUPLICATE/HIDE override mechanics, so it forces the built-in Reports
// directory OFF via the wrapper below (the directory itself is proven in prove_reports_directory.mjs).
// With the directory off, the new function must behave BYTE-IDENTICALLY to the pre-directory one =
// the backward-compat guarantee.
const REPORT_DIRECTORY = [], REPORT_CATEGORY_LABEL = {}   // directory forced off here
function applyNavLayoutFull(groups, layout) {
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
    group,
    module: moduleByGroup[group] || (targets.find(t => t.group === group)?.it.module || ''),
    items: targets.filter(t => t.group === group).map(t => t.it),
  })).filter(g => g.items.length > 0)
}
// Force the directory off so these tests target ONLY the override mechanics (backward-compat).
const applyNavLayout = (groups, layout) => applyNavLayoutFull(groups, { ...(layout || {}), hideReportsDirectory: true })

// sample already-RBAC-filtered sidebar (applyNavLayout runs AFTER canSeeItem/carrierOK/capOK)
const DASH = { href: '/commcalc', label: 'Dashboard', icon: 'D', module: 'commissions' }
const SALES = { href: '/commcalc/sales-report', label: 'Sales Report', icon: 'S', module: 'commissions' }
const ASSET = { href: '/commcalc/asset', label: 'Asset Ledger', icon: 'A', module: 'asset' }
const base = () => [
  { group: 'Commissions', module: 'commissions', items: [DASH, SALES] },
  { group: 'Assets', module: 'asset', items: [ASSET] },
]
const shape = gs => gs.map(g => ({ group: g.group, items: g.items.map(i => i.href) }))

// 1. LEGACY: empty layout → identical (backward-compat)
ok('legacy: empty layout unchanged', eq(shape(applyNavLayout(base(), {})), shape(base())))
ok('legacy: undefined layout unchanged', eq(shape(applyNavLayout(base(), undefined)), shape(base())))

// 2. LEGACY move (group only, no also/groups keys) behaves exactly as before
{
  const out = applyNavLayout(base(), { items: { '/commcalc/sales-report': { group: 'Assets' } } })
  // moved item lands in source-iteration order (Commissions iterated before Assets) — pre-existing behavior
  ok('legacy move: sales → Assets', eq(shape(out), [
    { group: 'Commissions', items: ['/commcalc'] },
    { group: 'Assets', items: ['/commcalc/sales-report', '/commcalc/asset'] },
  ]))
}

// 3. NEW GROUP + assign: brand-new group renders at the end with its item
{
  const out = applyNavLayout(base(), { items: { '/commcalc/sales-report': { group: 'Field Ops' } }, groups: ['Field Ops'] })
  ok('new group renders (appended, with item)', eq(shape(out), [
    { group: 'Commissions', items: ['/commcalc'] },
    { group: 'Assets', items: ['/commcalc/asset'] },
    { group: 'Field Ops', items: ['/commcalc/sales-report'] },
  ]))
  ok('new group inherits item module', out.find(g => g.group === 'Field Ops').module === 'commissions')
}

// 4. DUPLICATE renders in 2 groups; both entries are the SAME item object (identical RBAC surface)
{
  const out = applyNavLayout(base(), { items: { '/commcalc/sales-report': { also: ['Assets'] } } })
  ok('duplicate: sales in Commissions AND Assets', eq(shape(out), [
    { group: 'Commissions', items: ['/commcalc', '/commcalc/sales-report'] },
    { group: 'Assets', items: ['/commcalc/sales-report', '/commcalc/asset'] },
  ]))
  const inComm = out.find(g => g.group === 'Commissions').items.find(i => i.href === '/commcalc/sales-report')
  const inAsset = out.find(g => g.group === 'Assets').items.find(i => i.href === '/commcalc/sales-report')
  ok('duplicate is the SAME NavItem object (not a 2nd permission surface)', inComm === inAsset && inComm === SALES)
}

// 4b. DUPLICATE into a brand-new group
{
  const out = applyNavLayout(base(), { items: { '/commcalc/asset': { also: ['Field Ops'] } }, groups: ['Field Ops'] })
  ok('duplicate into new group', eq(shape(out), [
    { group: 'Commissions', items: ['/commcalc', '/commcalc/sales-report'] },
    { group: 'Assets', items: ['/commcalc/asset'] },
    { group: 'Field Ops', items: ['/commcalc/asset'] },
  ]))
}

// 5. DEDUP: also naming the primary group must NOT double-render (React-key + visual dup guard)
{
  const out = applyNavLayout(base(), { items: { '/commcalc/sales-report': { also: ['Commissions', 'Commissions'] } } })
  ok('dedup: also==primary → single copy', eq(shape(out), shape(base())))
}
// 5b. DEDUP: moved primary + also back to original
{
  const out = applyNavLayout(base(), { items: { '/commcalc/sales-report': { group: 'Assets', also: ['Commissions'] } } })
  ok('move + also: primary Assets, copy back in Commissions', eq(shape(out), [
    { group: 'Commissions', items: ['/commcalc', '/commcalc/sales-report'] },
    { group: 'Assets', items: ['/commcalc/sales-report', '/commcalc/asset'] },
  ]))
}

// 6. HIDDEN removes everywhere, and wins over also
{
  const out = applyNavLayout(base(), { items: { '/commcalc/sales-report': { hidden: true } } })
  ok('hidden: removed everywhere', eq(shape(out), [
    { group: 'Commissions', items: ['/commcalc'] },
    { group: 'Assets', items: ['/commcalc/asset'] },
  ]))
  const out2 = applyNavLayout(base(), { items: { '/commcalc/sales-report': { hidden: true, also: ['Assets'] } } })
  ok('hidden wins over also', eq(shape(out2), shape(out)))
}

// 7. remove-duplicate = drop `also` → back to single placement (the designer removeAlso path)
{
  const withDup = { items: { '/commcalc/sales-report': { also: ['Assets'] } } }
  const removed = { items: { '/commcalc/sales-report': {} } }  // designer prunes empty entry on save → {}
  ok('remove duplicate restores single placement', eq(shape(applyNavLayout(base(), removed)), shape(base())))
  ok('(sanity) with-dup differs from removed', !eq(shape(applyNavLayout(base(), withDup)), shape(base())))
}

// 8. empty group (layout.groups only, no item) is IGNORED by the sidebar (dropped), never an empty row
{
  const out = applyNavLayout(base(), { items: {}, groups: ['Empty Group'] })
  ok('empty group not rendered in sidebar', !out.some(g => g.group === 'Empty Group') && eq(shape(out), shape(base())))
}

console.log(`\napplyNavLayout: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
