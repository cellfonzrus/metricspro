// Proof harness — a deep-link menu entry is a second DOOR into a page, never a second GATE (index §19.40).
//
// Owner 2026-10-02: *"make Employees & Pay its own menu item"*. Employees & Pay is a TAB of /hr; its new NAV
// entry is `/hr?tab=employees`. The promise this proves: NOBODY can see the new entry who could not already open
// /hr, and nobody who could open /hr loses it — for every role shape, including the per-function grant/deny on
// /hr and a stray per-function key on the deep link itself (which must be ignored: there is ONE gate).
//
// No stub, no copy: Node >= 22.18 strips the erasable TypeScript of the REAL src/lib/rbac.ts and
// src/lib/urlTab.ts (neither imports anything), and this drives their exported functions.
//
//   A. the entry exists, is a deep link, and resolves to the /hr page entry
//   B. canSeeItem(deep) === canSeeItem(/hr) and navBlockReason agree, over a role matrix (incl. negative cells)
//   C. a per-function key on the DOOR is ignored — the page's own switch governs
//   D. every NAV page entry is unchanged by navPath (identity), so the carrier/vertical gates are byte-identical
//      for them; for the deep link they answer exactly as /hr does
//   E. path-based guards never match the door (title, module, longest prefix stay /hr's)
//   F. urlTab.ts: parseTab / tabHref — unknown → default, default drops ?tab=, other params kept
//   G. negative controls: a forged deep link to a page with no entry is refused; a forged door with WIDER scopes
//      than its page still answers as the page (the delegation, not the door's own fields, decides)
//
// Run:  node frontend/prove_nav_deep_link.mjs      (no network, no DB, no React, no npm install)
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const R = await import(pathToFileURL(join(HERE, 'src/lib/rbac.ts')).href)
const U = await import(pathToFileURL(join(HERE, 'src/lib/urlTab.ts')).href)

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}

const DEEP_HREF = '/hr?tab=employees'
// Every deep-link NAV entry there is. A door added without a line here reddens D2; a door here that
// does not answer exactly as its own page reddens §B′. The second pair (index §65) is listed in TWO
// sidebar groups, which is why §B′ walks them rather than §B naming /hr alone.
const DECLARED = [DEEP_HREF, '/commcalc/device-line-reimbursement?view=transferred']
const all = R.NAV.flatMap(g => g.items)
const deep = all.find(it => it.href === DEEP_HREF)
const page = all.find(it => it.href === '/hr')

console.log('A. the Employees & Pay entry')
ck('A1 NAV carries /hr?tab=employees', !!deep)
ck('A2 labelled "Employees & Pay"', deep?.label === 'Employees & Pay', deep?.label)
ck('A3 shown in the sidebar (not tileOnly, not platform-only)', deep && !deep.tileOnly && !deep.platformOnly)
ck('A4 it is a deep link (navPath strips the query)', deep && R.isDeepLinkItem(deep) && R.navPath(deep.href) === '/hr')
ck('A5 it resolves to the /hr page entry', deep && R.deepLinkPage(deep) === page, R.deepLinkPage(deep || { href: '' })?.href)
ck('A6 /hr itself is not a deep link', page && !R.isDeepLinkItem(page) && R.deepLinkPage(page) === null)

// ── a role matrix: module on/off × scope × /hr per-function override × report config × super admin ──────────
const SCOPES = ['all', 'market', 'store', 'self', undefined]
const MODS = [{}, { hr: true }, { hr: false }, { storeops: true }, { hr: true, storeops: true }, { admin: true }]
const PAGE_OV = [undefined, true, false]
const REPORTS = [undefined, {}, { storeops: true }]
const roles = []
for (const scope of SCOPES) for (const modules of MODS) for (const ov of PAGE_OV) for (const reports of REPORTS) {
  const p = { modules, scope }
  if (reports !== undefined) p.reports = reports
  if (ov !== undefined) p.pages = { '/hr': ov }
  roles.push(p)
}

console.log(`B. the door answers exactly as the page, over ${roles.length} role shapes`)
const diff = roles.filter(p => R.canSeeItem(p, deep) !== R.canSeeItem(p, page))
ck('B1 canSeeItem(door) === canSeeItem(/hr) for every role', diff.length === 0, diff.slice(0, 3))
const rdiff = roles.filter(p => JSON.stringify(R.navBlockReason(p, deep)) !== JSON.stringify(R.navBlockReason(p, page)))
ck('B2 navBlockReason(door) === navBlockReason(/hr) for every role', rdiff.length === 0, rdiff.slice(0, 3))
const seen = roles.filter(p => R.canSeeItem(p, deep)).length
ck('B3 the matrix has both outcomes (not vacuous)', seen > 0 && seen < roles.length, { seen, of: roles.length })
// the named cells the owner cares about
ck('B4 a market manager with the hr module sees it', R.canSeeItem({ modules: { hr: true }, scope: 'market' }, deep))
ck('B5 a store manager with the hr module does NOT (scopes all/market, same as /hr)',
  !R.canSeeItem({ modules: { hr: true }, scope: 'store' }, deep))
ck('B6 a role without the hr module does NOT', !R.canSeeItem({ modules: { storeops: true }, scope: 'all' }, deep))
ck('B7 /hr denied per function → the door is denied too (nobody keeps a way in)',
  !R.canSeeItem({ modules: { hr: true }, scope: 'all', pages: { '/hr': false } }, deep))
ck('B8 /hr granted per function to a store-scope role → the door opens too',
  R.canSeeItem({ modules: {}, scope: 'store', pages: { '/hr': true } }, deep))

console.log('C. ONE gate — a per-function key on the door is ignored')
const stray = { modules: { storeops: true }, scope: 'store', pages: { [DEEP_HREF]: true } }
ck('C1 pages["/hr?tab=employees"] = true does NOT open the door without /hr', !R.canSeeItem(stray, deep))
const strayDeny = { modules: { hr: true }, scope: 'all', pages: { [DEEP_HREF]: false } }
ck('C2 pages["/hr?tab=employees"] = false does not hide it while /hr is open (no second switch)',
  R.canSeeItem(strayDeny, deep) === R.canSeeItem(strayDeny, page))

console.log('D. every page entry is untouched by navPath; carrier / vertical gates follow the page')
const nonIdentity = all.filter(it => !R.isDeepLinkItem(it) && R.navPath(it.href) !== it.href).map(it => it.href)
ck('D1 navPath(href) === href for every non-deep NAV entry', nonIdentity.length === 0, nonIdentity)
const deeps = all.filter(it => R.isDeepLinkItem(it)).map(it => it.href)
const uniqDeeps = [...new Set(deeps)].sort()
ck('D2 the only deep-link entries are the declared ones', JSON.stringify(uniqDeeps) === JSON.stringify([...DECLARED].sort()), deeps)
ck('D2b every door, in every menu it is listed in, resolves to a real page entry',
  deeps.length >= DECLARED.length && deeps.every(h => all.some(it => it.href === R.navPath(h) && !R.isDeepLinkItem(it))), deeps)
const CAPS = [{}, { 'carrier:/hr': false }, { 'carrier:/hr': true }, { 'vertical:/hr': false }]
const VERTS = [null, { hidden_modules: ['hr'] }, { nav_hidden: ['/hr$'] }, { nav_hidden: ['/payroll'] }]
let cd = 0, vd = 0
for (const caps of CAPS) {
  for (const a of ['boost', 'total', '']) if (R.carrierOKActive(deep.href, a, caps) !== R.carrierOKActive(page.href, a, caps)) cd++
  if (R.carrierOK(deep.href, [], caps) !== R.carrierOK(page.href, [], caps)) cd++
  for (const v of VERTS) if (R.verticalOK(deep, v, caps) !== R.verticalOK(page, v, caps)) vd++
}
ck('D3 carrierOK / carrierOKActive answer for the door exactly as for /hr', cd === 0, cd)
ck('D4 verticalOK answers for the door exactly as for /hr', vd === 0, vd)

console.log(`B\u2032. every declared door answers exactly as its own page (${DECLARED.length} doors)`)
for (const href of DECLARED) {
  const d = all.find(it => it.href === href)
  const pg = d ? R.deepLinkPage(d) : null
  if (!d || !pg) { ck(`B\u20320 ${href}: the door and its page both exist`, false); continue }
  const mod = pg.module
  const rs = []
  for (const scope of SCOPES) for (const on of [true, false]) for (const ov of PAGE_OV) {
    const p = { modules: { [mod]: on }, scope }
    if (ov !== undefined) p.pages = { [pg.href]: ov }
    rs.push(p)
  }
  const dd = rs.filter(p => R.canSeeItem(p, d) !== R.canSeeItem(p, pg))
  ck(`B\u20321 ${href}: canSeeItem(door) === canSeeItem(page) for every role`, dd.length === 0, dd.slice(0, 2))
  const rr = rs.filter(p => JSON.stringify(R.navBlockReason(p, d)) !== JSON.stringify(R.navBlockReason(p, pg)))
  ck(`B\u20322 ${href}: navBlockReason(door) === navBlockReason(page) for every role`, rr.length === 0, rr.slice(0, 2))
  const sn = rs.filter(p => R.canSeeItem(p, d)).length
  ck(`B\u20323 ${href}: the matrix has both outcomes (not vacuous)`, sn > 0 && sn < rs.length, { sn, of: rs.length })
  ck(`B\u20324 ${href}: a per-function key on the DOOR opens nothing (ONE gate)`,
    R.canSeeItem({ modules: { [mod]: false }, scope: 'all', pages: { [href]: true } }, d) === false)
  ck(`B\u20325 ${href}: the page denied per function closes the door too`,
    R.canSeeItem({ modules: { [mod]: true }, scope: 'all', pages: { [pg.href]: false } }, d) === false)
  let c2 = 0, v2 = 0
  for (const caps of [{}, { [`carrier:${pg.href}`]: false }, { [`carrier:${pg.href}`]: true }, { [`vertical:${pg.href}`]: false }]) {
    for (const a of ['boost', 'total', '']) if (R.carrierOKActive(href, a, caps) !== R.carrierOKActive(pg.href, a, caps)) c2++
    if (R.carrierOK(href, [], caps) !== R.carrierOK(pg.href, [], caps)) c2++
    for (const v of [null, { hidden_modules: [mod] }, { nav_hidden: [`${pg.href}$`] }]) {
      if (R.verticalOK(d, v, caps) !== R.verticalOK(pg, v, caps)) v2++
    }
  }
  ck(`B\u20326 ${href}: carrier gates answer as the page`, c2 === 0, c2)
  ck(`B\u20327 ${href}: vertical gates answer as the page`, v2 === 0, v2)
}

console.log('E. path-based guards never match the door')
ck('E1 the /hr route keeps its title', R.navLabelForPath('/hr') === page.label, R.navLabelForPath('/hr'))
ck('E2 the /hr route keeps its module', R.navModuleForPath('/hr') === 'hr')
const roleOK = { modules: { hr: true }, scope: 'market' }
ck('E3 canAccessPath(/hr) for a market manager with hr', R.canAccessPath(roleOK, '/hr'))
ck('E4 canAccessPath(/hr) refused without hr', !R.canAccessPath({ modules: { storeops: true }, scope: 'market' }, '/hr'))

console.log('F. urlTab.ts — the pure meaning of ?tab=')
const K = ['comp', 'employees', 'payroll', 'timeoff']
ck('F1 a declared key is that tab', U.parseTab('employees', K, 'comp') === 'employees')
ck('F2 an unknown key is the default (a stale link lands on the page)', U.parseTab('bogus', K, 'comp') === 'comp')
ck('F3 no key is the default', U.parseTab(null, K, 'comp') === 'comp' && U.parseTab(undefined, K, 'comp') === 'comp')
ck('F4 tabHref writes ?tab=', U.tabHref('/hr', '', 'employees', 'comp') === '/hr?tab=employees')
ck('F5 the default tab drops ?tab= (the bare page URL stays canonical)', U.tabHref('/hr', 'tab=employees', 'comp', 'comp') === '/hr')
ck('F6 other parameters are kept', U.tabHref('/x', 'a=1&tab=b', 'c', 'd') === '/x?a=1&tab=c', U.tabHref('/x', 'a=1&tab=b', 'c', 'd'))
ck('F7 the param name is "tab" (what every ?tab= link spells)', U.TAB_PARAM === 'tab')

console.log('G. negative controls')
const forged = { href: '/no-such-page?tab=x', label: 'Forged', icon: '', module: 'hr', scopes: ['all'] }
ck('G1 a deep link to a page with no NAV entry is refused (never guessed)', R.canSeeItem({ modules: { hr: true }, scope: 'all' }, forged) === false)
ck('G2 …and says why', R.navBlockReason({ modules: { hr: true }, scope: 'all' }, forged)?.gate === 'page')
const wider = { ...deep, scopes: ['all', 'market', 'store', 'self'], module: 'storeops' }
const leak = roles.filter(p => R.canSeeItem(p, wider) !== R.canSeeItem(p, page))
ck('G3 a door declaring WIDER scopes / another module still answers as /hr (the page decides)', leak.length === 0, leak.slice(0, 2))
ck('G4 the control is armed: that wider item WOULD differ if judged on its own fields',
  roles.some(p => R.canSeeItem(p, { ...wider, href: '/hr' }) !== R.canSeeItem(p, page)))

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
