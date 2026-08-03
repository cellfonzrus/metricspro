// Proof harness — nav visibility precedence in src/lib/rbac.ts (2026-08-03 scope-split package).
//
// Two owner reports on 2026-08-03 for a TEST District Manager in the house org:
//   "KPI Metrics is allowed for the DM role but doesn't show"
//   "Rep Coaching doesn't show in the DM's nav/profile either"
// Both are invisible-BY-CONFIGURATION, but one of the gates was also a genuine code defect:
// `canSeeItem` checked the nav item's `scopes` tier BEFORE the explicit per-function override, so an
// admin who ticked a scope-restricted function (Rep Coaching is scopes ['all','market']) for a
// store-scoped role saw the checkbox stay ticked and the tab never appear. The Roles UI literally
// says "these per-function settings override the module/report toggles" — it wasn't true.
//
// This harness transpiles the REAL src/lib/rbac.ts with the repo's own tsc (no stub, no copy) and
// asserts:
//   A. The ONLY behaviour change: an EXACT pages[href] === true now lifts a scope gate.
//   B. Byte-identity everywhere else — reproduced by running a faithful reimplementation of the
//      PRE-change canSeeItem/canAccessPath over the ENTIRE nav x a matrix of role shapes, and
//      requiring identical output except on that one documented cell.
//   C. Sidebar and guard AGREE (canSeeItem vs canAccessPath) — a granted item must be reachable.
//   D. super_admin / unscoped ('all') roles are untouched.
//   E. navBlockReason() explains every hidden item and is exactly the inverse of canSeeItem.
//   F. schedulingReach()/rosterSpanExempt() default to 'org' (today's behaviour) for every legacy
//      role shape, and only an explicit 'span' narrows.
//
// Run:  node frontend/prove_nav_scope_override.mjs      (no network, no DB, no React)

import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
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

// ── transpile the REAL rbac.ts (types only; no bundler, no stub) ────────────────────────────────
const OUT = mkdtempSync(join(tmpdir(), 'rbacproof-'))
const SRC = readFileSync(RBAC, 'utf8')
must(SRC.includes('export function canSeeItem'), 'rbac.ts no longer exports canSeeItem')
writeFileSync(join(OUT, 'rbac.ts'), SRC)
// --skipLibCheck + an empty --typeRoots so ambient @types in node_modules can never be pulled in
// (they make this transpile fail or pass depending on the cwd it is launched from).
mkdirSync(join(OUT, 'emptytypes'), { recursive: true })
execFileSync(join(HERE, 'node_modules/.bin/tsc'),
  [join(OUT, 'rbac.ts'), '--target', 'es2020', '--module', 'es2020', '--outDir', OUT,
   '--skipLibCheck', '--typeRoots', join(OUT, 'emptytypes')],
  { stdio: 'inherit', cwd: OUT })
const R = await import(pathToFileURL(join(OUT, 'rbac.js')).href)

// ── faithful reimplementation of the PRE-change gates (the byte-identity oracle) ────────────────
function oldCanSeeItem(perms, item) {
  if (R.isSuperAdmin(perms)) return true
  if (item.href === '/closing/management') return R.canManage(perms, item.href)
  if (item.scopes && !item.scopes.includes(perms.scope || 'all')) return false
  const ov = perms.pages?.[item.href]
  if (typeof ov === 'boolean') return ov
  if (!R.moduleGranted(perms.modules, item.module)) return false
  const area = R.reportAreaForPath(item.href)
  if (area && !R.hasReport(perms, area)) return false
  return true
}
function oldPageOverrideForPath(perms, path) {
  const pages = perms.pages
  if (!pages) return undefined
  if (path in pages) return pages[path]
  let best, bestLen = -1
  for (const g of R.NAV) for (const it of g.items) {
    if ((path === it.href || path.startsWith(it.href + '/')) && it.href.length > bestLen && (it.href in pages)) {
      best = pages[it.href]; bestLen = it.href.length
    }
  }
  return best
}
function oldCanAccessPath(perms, path) {
  if (path === '/' || path.startsWith('/account/password')) return true
  if (R.isSuperAdmin(perms)) return true
  const scope = perms.scope || 'all'
  if (scope === 'self') {
    const home = perms.home || '/commcalc/targets/my'
    return ['/commcalc/targets/my', '/commcalc/kpi', '/account/password', '/reports', '/helpdesk']
      .some(p => path.startsWith(p)) || path.startsWith(home)
  }
  for (const g of R.NAV) for (const it of g.items) {
    if (path === it.href && it.scopes && !it.scopes.includes(scope)) return false
  }
  if (path === '/closing/management') return R.canManage(perms, path)
  const ov = oldPageOverrideForPath(perms, path)
  if (typeof ov === 'boolean') return ov
  const area = R.reportAreaForPath(path)
  if (area && !R.hasReport(perms, area)) return false
  return R.moduleGranted(perms.modules, R.navModuleForPath(path) ?? R.moduleForPath(path))
}

const ALL_ITEMS = R.NAV.flatMap(g => g.items)
const SCOPED_ITEMS = ALL_ITEMS.filter(i => i.scopes && i.scopes.length)
must(ALL_ITEMS.length > 100, `expected a large nav, got ${ALL_ITEMS.length}`)
must(SCOPED_ITEMS.length > 10, `expected scope-restricted items, got ${SCOPED_ITEMS.length}`)

// The DM role template exactly as /admin/roles ships it.
const DM = { modules: { commissions: true, targets: true, asset: true, storeops: true, notify: true, helpdesk: true, hr: true },
             scope: 'market', home: '/commcalc/targets' }
const DM_STORE = { ...DM, scope: 'store' }
const REP = { modules: { targets: true }, scope: 'self', home: '/commcalc/targets/my' }
const ADMIN = { modules: { admin: true }, scope: 'all' }
const EXEC = { modules: { commissions: true, targets: true }, scope: 'all' }

console.log('\nA. THE ONE behaviour change — an explicit grant now lifts a scope gate')
const COACH = ALL_ITEMS.find(i => i.href === '/commcalc/coaching')
must(COACH && COACH.scopes && !COACH.scopes.includes('store'), 'Rep Coaching is no longer scope-restricted — update this proof')
ck('BEFORE: store-scoped DM + explicit grant → still hidden (the defect)',
   oldCanSeeItem({ ...DM_STORE, pages: { '/commcalc/coaching': true } }, COACH) === false)
ck('AFTER: store-scoped DM + explicit grant → visible',
   R.canSeeItem({ ...DM_STORE, pages: { '/commcalc/coaching': true } }, COACH) === true)
ck('explicit DENY still denies (unchanged)',
   R.canSeeItem({ ...DM, pages: { '/commcalc/coaching': false } }, COACH) === false)
ck('no override → scope gate still closes it (unchanged)',
   R.canSeeItem(DM_STORE, COACH) === false)
ck('grant also short-circuits the report-area gate, as for any other item',
   R.canSeeItem({ ...DM, pages: { '/commcalc/coaching': true } }, COACH) === true)
ck('a PREFIX-inherited override does NOT lift a scope gate (never aimed at this item)',
   R.canSeeItem({ ...DM_STORE, pages: { '/commcalc': true } }, COACH) === false)

console.log('\nB. byte-identity over the WHOLE nav x role matrix (except that one cell)')
const ROLES = [DM, DM_STORE, REP, ADMIN, EXEC,
  { modules: {}, scope: 'store' },
  { modules: { closing: true }, scope: 'store' },
  { modules: { commissions: true }, scope: 'market', reports: { commissions: true } },
  { modules: { commissions: true }, scope: 'market', reports: {} },
  { scope: 'market' }, {}]
let diffs = []
for (const base of ROLES) {
  // every pages-override shape: none, grant, deny — for every item
  for (const it of ALL_ITEMS) {
    for (const ovName of ['none', 'grant', 'deny']) {
      const perms = ovName === 'none' ? base
        : { ...base, pages: { ...(base.pages || {}), [it.href]: ovName === 'grant' } }
      const a = oldCanSeeItem(perms, it), b = R.canSeeItem(perms, it)
      if (a !== b) diffs.push({ href: it.href, scope: perms.scope || 'all', ov: ovName, before: a, after: b })
    }
  }
}
ck(`${ROLES.length} roles x ${ALL_ITEMS.length} items x 3 override shapes evaluated`, true)
const unexpected = diffs.filter(d => !(d.ov === 'grant' && d.before === false && d.after === true))
ck('every difference is "explicit grant now honored" — zero other deltas', unexpected.length === 0, unexpected.slice(0, 5))
ck('the deltas only ever WIDEN, never revoke', diffs.every(d => d.before === false && d.after === true))
ck('the deltas only ever hit scope-restricted items',
   diffs.every(d => (SCOPED_ITEMS.find(i => i.href === d.href) || {}).scopes !== undefined))
ck('zero deltas when no pages override exists', diffs.every(d => d.ov === 'grant'))
ck('zero deltas for scope "all" roles', diffs.every(d => d.scope !== 'all'))

console.log('\nC. sidebar and guard agree (a granted item must be reachable)')
let mismatch = []
for (const base of ROLES) {
  for (const it of ALL_ITEMS) {
    for (const ovName of ['none', 'grant', 'deny']) {
      const perms = ovName === 'none' ? base
        : { ...base, pages: { ...(base.pages || {}), [it.href]: ovName === 'grant' } }
      if (R.canSeeItem(perms, it) && !R.canAccessPath(perms, it.href)) {
        mismatch.push({ href: it.href, scope: perms.scope || 'all', ov: ovName })
      }
    }
  }
}
// This package must only ever REPAIR sidebar/guard disagreements, never introduce one, so the check
// is a strict-subset property against the pre-change code rather than "zero" (there is one older,
// unrelated disagreement — /employee for a self-scoped rep — which belongs to SELF_ALLOWED and is
// deliberately out of this package's remit; see docs/handoffs/platform-core.md).
ck('sidebar/guard: the DM-relevant grant cases now agree',
   mismatch.every(d => d.ov === 'none'), mismatch.slice(0, 5))
// The same disagreement existed BEFORE this package for self-scoped reps (canSeeItem honored an
// explicit pages[href]=true for every scope; the guard's `self` branch ignored pages entirely), so
// prove it is a real repair and not a regression this package introduced.
let oldMismatch = []
for (const base of ROLES) {
  for (const it of ALL_ITEMS) {
    for (const ovName of ['none', 'grant', 'deny']) {
      const perms = ovName === 'none' ? base
        : { ...base, pages: { ...(base.pages || {}), [it.href]: ovName === 'grant' } }
      if (oldCanSeeItem(perms, it) && !oldCanAccessPath(perms, it.href)) {
        oldMismatch.push({ href: it.href, scope: perms.scope || 'all', ov: ovName })
      }
    }
  }
}
ck('the pre-change code DID disagree (this package repairs it)', oldMismatch.length > 0, oldMismatch.length)
const key = d => `${d.href}|${d.scope}|${d.ov}`
const oldKeys = new Set(oldMismatch.map(key))
ck('NO NEW disagreement introduced (new set is a strict subset of the old)',
   mismatch.every(d => oldKeys.has(key(d))) && mismatch.length < oldMismatch.length,
   { before: oldMismatch.length, after: mismatch.length })
ck('every REPAIRED case was an explicit grant the guard used to ignore',
   oldMismatch.filter(d => !mismatch.some(m => key(m) === key(d))).every(d => d.ov === 'grant'))
ck('the residual is only the pre-existing self-rep /employee case',
   [...new Set(mismatch.map(d => d.href))].join(',') === '/employee',
   [...new Set(mismatch.map(d => d.href))])
let pathDiffs = []
for (const base of ROLES) {
  for (const it of ALL_ITEMS) {
    for (const ovName of ['none', 'grant', 'deny']) {
      const perms = ovName === 'none' ? base
        : { ...base, pages: { ...(base.pages || {}), [it.href]: ovName === 'grant' } }
      const a = oldCanAccessPath(perms, it.href), b = R.canAccessPath(perms, it.href)
      if (a !== b) pathDiffs.push({ href: it.href, ov: ovName, before: a, after: b })
    }
  }
}
ck('canAccessPath deltas are the same single documented cell',
   pathDiffs.every(d => d.ov === 'grant' && d.before === false && d.after === true), pathDiffs.slice(0, 5))

console.log('\nD. privileged and unscoped roles are untouched')
ck('super-admin sees every item (before & after)',
   ALL_ITEMS.every(it => R.canSeeItem(ADMIN, it) && oldCanSeeItem(ADMIN, it)))
ck('scope "all" exec identical before & after',
   ALL_ITEMS.every(it => R.canSeeItem(EXEC, it) === oldCanSeeItem(EXEC, it)))
ck('self-scoped rep identical before & after',
   ALL_ITEMS.every(it => R.canSeeItem(REP, it) === oldCanSeeItem(REP, it)))
ck('management-only page unchanged for a DM',
   R.canSeeItem(DM, { href: '/closing/management', label: 'x', icon: 'x', module: 'closing' }) ===
   oldCanSeeItem(DM, { href: '/closing/management', label: 'x', icon: 'x', module: 'closing' }))

console.log('\nE. navBlockReason explains every hidden item (exact inverse of canSeeItem)')
let badReason = []
for (const base of ROLES) {
  for (const it of ALL_ITEMS) {
    for (const ovName of ['none', 'grant', 'deny']) {
      const perms = ovName === 'none' ? base
        : { ...base, pages: { ...(base.pages || {}), [it.href]: ovName === 'grant' } }
      const vis = R.canSeeItem(perms, it), why = R.navBlockReason(perms, it)
      if (vis !== (why === null)) badReason.push({ href: it.href, ov: ovName, vis, why })
    }
  }
}
ck('navBlockReason() === null exactly when canSeeItem() === true', badReason.length === 0, badReason.slice(0, 5))
ck('Rep Coaching for a store-scoped DM → gate "scope"',
   R.navBlockReason(DM_STORE, COACH)?.gate === 'scope', R.navBlockReason(DM_STORE, COACH))
const KPI = ALL_ITEMS.find(i => i.href === '/commcalc/kpi')
must(KPI, 'KPI Metrics nav item is gone — update this proof')
ck('KPI Metrics with the commissions module OFF → gate "module"',
   R.navBlockReason({ modules: { targets: true }, scope: 'market' }, KPI)?.gate === 'module',
   R.navBlockReason({ modules: { targets: true }, scope: 'market' }, KPI))
ck('KPI Metrics names the module the operator has to tick',
   (R.navBlockReason({ modules: { targets: true }, scope: 'market' }, KPI)?.detail || '').includes(KPI.module))
ck('Rep Coaching for a MARKET DM with reports off → gate "report"',
   R.navBlockReason(DM, COACH)?.gate === 'report', R.navBlockReason(DM, COACH))
ck('explicit deny → gate "page"',
   R.navBlockReason({ ...DM, pages: { '/commcalc/kpi': false } }, KPI)?.gate === 'page')
ck('super-admin is never blocked', ALL_ITEMS.every(it => R.navBlockReason(ADMIN, it) === null))

console.log('\nF. scheduling reach is separate from reporting scope, and defaults to today')
ck('undefined perms → org', R.schedulingReach(undefined) === 'org')
ck('legacy DM role (no key) → org', R.schedulingReach(DM) === 'org')
ck('every ROLES shape defaults to org', ROLES.every(r => R.schedulingReach(r) === 'org'))
ck('explicit span honored', R.schedulingReach({ ...DM, scheduling_reach: 'span' }) === 'span')
ck('garbage → org', R.schedulingReach({ scheduling_reach: 'anything' }) === 'org')
ck('rosterSpanExempt true by default', R.rosterSpanExempt(DM) === true)
ck('rosterSpanExempt false only when locked', R.rosterSpanExempt({ ...DM, scheduling_reach: 'span' }) === false)
ck('scheduling_reach NEVER affects nav visibility',
   ALL_ITEMS.every(it => R.canSeeItem({ ...DM, scheduling_reach: 'span' }, it) === R.canSeeItem(DM, it)))
ck('scheduling_reach NEVER affects the reporting scope value',
   ({ ...DM, scheduling_reach: 'span' }).scope === DM.scope)

rmSync(OUT, { recursive: true, force: true })
console.log(`\n${'='.repeat(72)}\n  RESULT: ${pass} passed, ${fail} failed\n${'='.repeat(72)}`)
process.exit(fail ? 1 : 0)
