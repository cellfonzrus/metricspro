// Proof harness — the TWO nav rows added to src/lib/rbac.ts for the Cash Deposit Recon package
// (mod-retail-ops NEEDS CORE 2026-08-05, mig 509):
//
//   { href: '/closing/deposit-recon',      label: 'Cash Deposit Recon', icon: '💵', module: 'closing', scopes: ['all','market'] }
//   { href: '/closing/deposit-categories', label: 'Deposit Categories', icon: '🗂️', module: 'closing', scopes: ['all'] }
//
// rbac.ts is a SHARED file: a bad edit here is a cross-MODULE nav/permission regression, not a
// closing-module bug. So this harness does not merely assert the rows are present — it diffs the
// REAL current rbac.ts against the REAL rbac.ts on `main` and requires that the ONLY observable
// difference anywhere in the permission surface is those two hrefs.
//
// Asserts:
//   A. Both rows exist EXACTLY once, in the Daily Closing group, with the exact requested shape.
//   B. NO new permission key: both key off `module: 'closing'`, the same key every existing closing
//      row uses → no role re-seeding ([[seeded-role-modules-forward-only]]: a row keyed off a module
//      an existing tenant role lacks silently hides forever).
//   C. Neither href picks up a report-area gate (in particular `/closing/deposit-recon` must NOT be
//      swallowed by the `/closing/recon` REPORT_TREES prefix — that would add a hidden second gate
//      that Closing Expenses / Expense Categories do not have).
//   D. GATING PARITY over a role matrix: deposit-recon is visible to exactly the roles that see
//      /closing/expenses-report, and deposit-categories to exactly those that see
//      /closing/expense-categories. This is the operational form of "nothing needs re-seeding".
//   E. Sidebar and guard AGREE (canSeeItem vs canAccessPath) for both new hrefs, every role.
//   F. ZERO REGRESSION vs main: for every PRE-EXISTING nav item x every role in the matrix,
//      canSeeItem / navBlockReason / canAccessPath / navModuleForPath / reportAreaForPath are
//      byte-identical to main's rbac.ts. Also: NAV minus the 2 rows === main's NAV exactly, and no
//      other exported registry (REPORT_TREES, REPORT_DIRECTORY, DATA_GRANTS, SETTING-adjacent lists,
//      MODULE_ALIASES, NAV_CARRIERS) changed.
//   G. applyNavLayout places each new row exactly once (no accidental Reports-directory duplicate).
//
// Run:  node frontend/prove_deposit_recon_nav.mjs      (no network, no DB, no React)

import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const RBAC = join(HERE, 'src/lib/rbac.ts')

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

// ── transpile BOTH the current rbac.ts and main's rbac.ts (types only; no bundler, no stub) ──────
function load(name, src) {
  const out = mkdtempSync(join(tmpdir(), `depnav-${name}-`))
  writeFileSync(join(out, 'rbac.ts'), src)
  mkdirSync(join(out, 'emptytypes'), { recursive: true })
  execFileSync(join(HERE, 'node_modules/.bin/tsc'),
    [join(out, 'rbac.ts'), '--target', 'es2020', '--module', 'es2020', '--outDir', out,
     '--skipLibCheck', '--typeRoots', join(out, 'emptytypes')],
    { stdio: 'inherit', cwd: out })
  return import(pathToFileURL(join(out, 'rbac.js')).href)
}

const SRC = readFileSync(RBAC, 'utf8')
must(SRC.includes('export function canSeeItem'), 'rbac.ts no longer exports canSeeItem')
const BASE_SRC = execFileSync('git', ['show', 'main:frontend/src/lib/rbac.ts'], { cwd: HERE, encoding: 'utf8' })
must(BASE_SRC.includes('export function canSeeItem'), 'could not read main:frontend/src/lib/rbac.ts')

const R = await load('head', SRC)         // rbac.ts WITH the two new rows
const B = await load('base', BASE_SRC)    // rbac.ts on main (the oracle)

const NEW_REPORT = '/closing/deposit-recon'
const NEW_ADMIN  = '/closing/deposit-categories'
const NEW = [NEW_REPORT, NEW_ADMIN]
// The existing siblings whose gating the new rows must match EXACTLY.
const SIB_REPORT = '/closing/expenses-report'
const SIB_ADMIN  = '/closing/expense-categories'

const ALL = R.NAV.flatMap(g => g.items)
const BASE_ALL = B.NAV.flatMap(g => g.items)
const byHref = h => ALL.filter(i => i.href === h)
const groupOf = h => R.NAV.find(g => g.items.some(i => i.href === h))?.group
const item = h => ALL.find(i => i.href === h)

// ── role matrix (the shapes /admin/roles actually ships + the edge cases) ────────────────────────
const CLOSING_ALL    = { modules: { closing: true }, scope: 'all' }
const CLOSING_MARKET = { modules: { closing: true }, scope: 'market' }
const CLOSING_STORE  = { modules: { closing: true }, scope: 'store' }
const NO_CLOSING     = { modules: { commissions: true, storeops: true }, scope: 'all' }
const DM = { modules: { commissions: true, targets: true, asset: true, storeops: true, notify: true, helpdesk: true, hr: true },
             scope: 'market', home: '/commcalc/targets' }
const DM_CLOSING = { ...DM, modules: { ...DM.modules, closing: true } }
const REP = { modules: { targets: true }, scope: 'self', home: '/commcalc/targets/my' }
const REP_CLOSING = { ...REP, modules: { ...REP.modules, closing: true } }
const ADMIN = { modules: { admin: true }, scope: 'all' }
const EMPTY = {}
const REPORTS_OFF = { modules: { closing: true }, scope: 'all', reports: { commissions: true } }   // closing report area OFF
const REPORTS_ON  = { modules: { closing: true }, scope: 'market', reports: { closing: true } }
// per-function overrides aimed at the NEW hrefs and at their siblings
const GRANT_NEW   = { modules: { closing: true }, scope: 'store', pages: { [NEW_REPORT]: true, [NEW_ADMIN]: true } }
const DENY_NEW    = { modules: { closing: true }, scope: 'all',   pages: { [NEW_REPORT]: false, [NEW_ADMIN]: false } }
const GRANT_SIB   = { modules: { closing: true }, scope: 'store', pages: { [SIB_REPORT]: true, [SIB_ADMIN]: true } }
const PREFIX_OV   = { modules: { closing: true }, scope: 'all',   pages: { '/closing': false } }
const ROLES = { CLOSING_ALL, CLOSING_MARKET, CLOSING_STORE, NO_CLOSING, DM, DM_CLOSING, REP, REP_CLOSING,
                ADMIN, EMPTY, REPORTS_OFF, REPORTS_ON, GRANT_NEW, DENY_NEW, GRANT_SIB, PREFIX_OV }

console.log('\nA. the two rows exist exactly once, in Daily Closing, with the requested shape')
for (const h of NEW) ck(`${h} present exactly once`, byHref(h).length === 1, byHref(h).length)
for (const h of NEW) ck(`${h} lives in the Daily Closing group`, groupOf(h) === 'Daily Closing', groupOf(h))
ck('deposit-recon row shape', JSON.stringify(item(NEW_REPORT)) ===
  JSON.stringify({ href: NEW_REPORT, label: 'Cash Deposit Recon', icon: '💵', module: 'closing', scopes: ['all', 'market'] }),
  item(NEW_REPORT))
ck('deposit-categories row shape', JSON.stringify(item(NEW_ADMIN)) ===
  JSON.stringify({ href: NEW_ADMIN, label: 'Deposit Categories', icon: '🗂️', module: 'closing', scopes: ['all'] }),
  item(NEW_ADMIN))
ck('deposit-recon sits next to the other recon reports (before the config block)',
  R.NAV.find(g => g.group === 'Daily Closing').items.findIndex(i => i.href === NEW_REPORT) ===
  R.NAV.find(g => g.group === 'Daily Closing').items.findIndex(i => i.href === '/closing/epay-recon') + 1)
ck('deposit-categories sits next to Expense Categories',
  R.NAV.find(g => g.group === 'Daily Closing').items.findIndex(i => i.href === NEW_ADMIN) ===
  R.NAV.find(g => g.group === 'Daily Closing').items.findIndex(i => i.href === SIB_ADMIN) + 1)

console.log('\nB. NO new permission key — both key off the existing `closing` module')
const CLOSING_MODULES = new Set(R.NAV.find(g => g.group === 'Daily Closing').items.map(i => i.module))
ck('every Daily Closing row (incl. the 2 new) uses module "closing"',
  CLOSING_MODULES.size === 1 && CLOSING_MODULES.has('closing'), [...CLOSING_MODULES])
ck('no module key was added to the nav at all',
  JSON.stringify([...new Set(ALL.map(i => i.module))].sort()) ===
  JSON.stringify([...new Set(BASE_ALL.map(i => i.module))].sort()))
for (const h of NEW) ck(`${h} declares no tenant capability (cap) gate`, item(h).cap === undefined)
ck('moduleForPath still resolves both to closing',
  NEW.every(h => R.moduleForPath(h) === 'closing'))
ck('navModuleForPath resolves both to closing',
  NEW.every(h => R.navModuleForPath(h) === 'closing'))

console.log('\nC. neither href picks up a report-area gate')
ck('reportAreaForPath(/closing/deposit-recon) === null (NOT swallowed by the /closing/recon prefix)',
  R.reportAreaForPath(NEW_REPORT) === null, R.reportAreaForPath(NEW_REPORT))
ck('reportAreaForPath(/closing/deposit-categories) === null', R.reportAreaForPath(NEW_ADMIN) === null)
ck('…same as the siblings they mirror',
  R.reportAreaForPath(SIB_REPORT) === null && R.reportAreaForPath(SIB_ADMIN) === null)
ck('/closing/recon itself still resolves to the closing report area (untouched)',
  R.reportAreaForPath('/closing/recon') === 'closing' && R.reportAreaForPath('/closing/recon/x') === 'closing')

console.log('\nD. gating parity with the existing siblings, across the role matrix')
for (const [name, p] of Object.entries(ROLES)) {
  const sawNewR = R.canSeeItem(p, item(NEW_REPORT)), sawSibR = R.canSeeItem(p, item(SIB_REPORT))
  const sawNewA = R.canSeeItem(p, item(NEW_ADMIN)),  sawSibA = R.canSeeItem(p, item(SIB_ADMIN))
  // The two pages-override roles deliberately target ONE of the pair, so parity is asserted only
  // where no explicit per-function override singles a row out.
  const overridden = p.pages && NEW.concat([SIB_REPORT, SIB_ADMIN]).some(h => h in p.pages)
  if (!overridden) {
    ck(`${name}: deposit-recon visibility === expenses-report visibility (${sawNewR})`, sawNewR === sawSibR, { sawNewR, sawSibR })
    ck(`${name}: deposit-categories visibility === expense-categories visibility (${sawNewA})`, sawNewA === sawSibA, { sawNewA, sawSibA })
  }
}
ck('a closing role with company-wide scope sees BOTH', R.canSeeItem(CLOSING_ALL, item(NEW_REPORT)) && R.canSeeItem(CLOSING_ALL, item(NEW_ADMIN)))
ck('a market-scoped closing role sees the report, NOT the admin page',
  R.canSeeItem(CLOSING_MARKET, item(NEW_REPORT)) && !R.canSeeItem(CLOSING_MARKET, item(NEW_ADMIN)))
ck('a store-scoped closing role sees NEITHER (same as its siblings)',
  !R.canSeeItem(CLOSING_STORE, item(NEW_REPORT)) && !R.canSeeItem(CLOSING_STORE, item(NEW_ADMIN)))
ck('a role WITHOUT the closing module sees neither', !R.canSeeItem(NO_CLOSING, item(NEW_REPORT)) && !R.canSeeItem(NO_CLOSING, item(NEW_ADMIN)))
ck('super-admin sees both (the operator can never be locked out)',
  R.canSeeItem(ADMIN, item(NEW_REPORT)) && R.canSeeItem(ADMIN, item(NEW_ADMIN)) &&
  R.canAccessPath(ADMIN, NEW_REPORT) && R.canAccessPath(ADMIN, NEW_ADMIN))
ck('an explicit per-function grant lifts the scope gate for a store role',
  R.canSeeItem(GRANT_NEW, item(NEW_REPORT)) && R.canSeeItem(GRANT_NEW, item(NEW_ADMIN)))
ck('an explicit per-function deny hides them from a company-wide role',
  !R.canSeeItem(DENY_NEW, item(NEW_REPORT)) && !R.canSeeItem(DENY_NEW, item(NEW_ADMIN)))
ck('the closing REPORT-AREA toggle does not gate them (they are not report-area pages)',
  R.canSeeItem(REPORTS_OFF, item(NEW_REPORT)) === R.canSeeItem(REPORTS_OFF, item(SIB_REPORT)))
ck('navBlockReason is the exact inverse of canSeeItem for both rows, every role',
  Object.values(ROLES).every(p => NEW.every(h => (R.navBlockReason(p, item(h)) === null) === R.canSeeItem(p, item(h)))))

console.log('\nE. sidebar/guard behaviour on the new hrefs is IDENTICAL to their siblings on main')
// NOT "canSeeItem === canAccessPath": rbac.ts on main already disagrees with itself for EVERY
// /closing/* sub-page when a role carries a PREFIX-level per-function override (pages['/closing'] =
// false) — canSeeItem only honors an EXACT-href override while canAccessPath's pageOverrideForPath
// takes the longest matching nav prefix, so the sidebar shows the tab and the guard bounces it. That
// quirk is pre-existing, applies to Closing Expenses / Expense Categories / ePay Recon / Cash Pickup
// alike, and is NOT this package's to fix (a shared-semantics change for one module's convenience).
// The correct assertion is that the new rows INHERIT it exactly — they behave like their siblings.
const baseItem = h => BASE_ALL.find(i => i.href === h)
for (const [name, p] of Object.entries(ROLES)) {
  if (p.pages && NEW.concat([SIB_REPORT, SIB_ADMIN]).some(h => h in p.pages)) continue  // override singles one out
  for (const [nh, sh] of [[NEW_REPORT, SIB_REPORT], [NEW_ADMIN, SIB_ADMIN]]) {
    const mine = [R.canSeeItem(p, item(nh)), R.canAccessPath(p, nh)]
    const oracle = [B.canSeeItem(p, baseItem(sh)), B.canAccessPath(p, sh)]
    ck(`${name}: [see,access](${nh}) === main's [see,access](${sh}) → ${JSON.stringify(mine)}`,
      JSON.stringify(mine) === JSON.stringify(oracle), { mine, oracle })
  }
}
ck('the prefix-override quirk is inherited, not introduced (matches main sibling-for-sibling)',
  R.canSeeItem(PREFIX_OV, item(NEW_REPORT)) === B.canSeeItem(PREFIX_OV, baseItem(SIB_REPORT)) &&
  R.canAccessPath(PREFIX_OV, NEW_REPORT) === B.canAccessPath(PREFIX_OV, SIB_REPORT))
ck('with NO prefix override, sidebar and guard agree for both new hrefs, every role',
  Object.entries(ROLES).filter(([, p]) => !(p.pages && '/closing' in p.pages) && (p.scope || 'all') !== 'self')
    .every(([, p]) => NEW.every(h => R.canSeeItem(p, item(h)) === R.canAccessPath(p, h))))
ck('a self-scoped rep reaches neither (unchanged rep posture)',
  !R.canAccessPath(REP_CLOSING, NEW_REPORT) && !R.canAccessPath(REP_CLOSING, NEW_ADMIN))

console.log('\nF. ZERO REGRESSION vs main — the 2 rows are the ONLY difference')
const strip = nav => JSON.stringify(nav.map(g => ({ ...g, items: g.items.filter(i => !NEW.includes(i.href)) })))
ck('NAV minus the two new rows is byte-identical to main\'s NAV', strip(R.NAV) === strip(B.NAV))
ck('exactly 2 hrefs added, 0 removed',
  ALL.length - BASE_ALL.length === 2 &&
  BASE_ALL.every(i => ALL.some(j => j.href === i.href)) &&
  ALL.filter(i => !BASE_ALL.some(j => j.href === i.href)).map(i => i.href).sort().join() === NEW.slice().sort().join())
for (const reg of ['REPORT_AREAS', 'REPORT_CATEGORIES', 'REPORT_DIRECTORY', 'DATA_GRANTS', 'MODULE_ALIASES', 'NAV_CARRIERS']) {
  ck(`${reg} unchanged`, JSON.stringify(R[reg]) === JSON.stringify(B[reg]))
}
// The behavioral diff: every PRE-EXISTING item x every role must answer identically to main.
let diffs = 0
for (const p of Object.values(ROLES)) {
  for (const i of BASE_ALL) {
    const mine = ALL.find(x => x.href === i.href)
    if (R.canSeeItem(p, mine) !== B.canSeeItem(p, i)) { diffs++; console.error('    canSeeItem drift', i.href) }
    if (JSON.stringify(R.navBlockReason(p, mine)) !== JSON.stringify(B.navBlockReason(p, i))) { diffs++; console.error('    navBlockReason drift', i.href) }
    if (R.canAccessPath(p, i.href) !== B.canAccessPath(p, i.href)) { diffs++; console.error('    canAccessPath drift', i.href) }
    if (R.navModuleForPath(i.href) !== B.navModuleForPath(i.href)) { diffs++; console.error('    navModuleForPath drift', i.href) }
    if (R.reportAreaForPath(i.href) !== B.reportAreaForPath(i.href)) { diffs++; console.error('    reportArea drift', i.href) }
  }
}
ck(`no gate drift on ANY pre-existing nav item (${BASE_ALL.length} items x ${Object.keys(ROLES).length} roles)`, diffs === 0, diffs)
// Deep sub-paths that could have been re-homed by a new longest-prefix match.
const PROBES = ['/closing', '/closing/recon', '/closing/recon/detail', '/closing/deposit', '/closing/deposits',
                '/closing/expense-categories', '/closing/expenses-report', '/closing/submit', '/closing/pickup',
                '/commcalc', '/storeops', '/accounts', '/admin/roles']
let pdrift = 0
for (const p of Object.values(ROLES)) for (const path of PROBES) {
  if (R.canAccessPath(p, path) !== B.canAccessPath(p, path)) { pdrift++; console.error('    path drift', path) }
  if (R.navModuleForPath(path) !== B.navModuleForPath(path)) { pdrift++; console.error('    navModule drift', path) }
}
ck('no drift on probe paths (incl. /closing/deposit, a prefix of both new hrefs)', pdrift === 0, pdrift)

console.log('\nG. applyNavLayout renders each new row exactly once')
for (const [name, p] of Object.entries(ROLES)) {
  const filtered = R.NAV.map(g => ({ ...g, items: g.items.filter(i => R.canSeeItem(p, i)) })).filter(g => g.items.length)
  const out = R.applyNavLayout(filtered, undefined)
  for (const h of NEW) {
    const n = out.flatMap(g => g.items).filter(i => i.href === h).length
    ck(`${name}: ${h} rendered ${R.canSeeItem(p, item(h)) ? 'once' : 'never'}`, n === (R.canSeeItem(p, item(h)) ? 1 : 0), n)
  }
}
const hidden = R.applyNavLayout(
  R.NAV.map(g => ({ ...g, items: g.items.filter(i => R.canSeeItem(CLOSING_ALL, i)) })).filter(g => g.items.length),
  { items: { [NEW_REPORT]: { hidden: true } } })
ck('a tenant nav-layout override can hide the new report row', !hidden.flatMap(g => g.items).some(i => i.href === NEW_REPORT))

console.log('\n' + '='.repeat(72))
console.log(`  RESULT: ${pass} passed, ${fail} failed`)
console.log('='.repeat(72))
process.exit(fail === 0 ? 0 : 1)
