// Proof harness — the FLAGS DM GATE (owner directive 2026-08-07, verbatim:
//   "all flags need to be fed thru the dm, so yes route it thru the dm and then visible to the
//    scoped user.")
//
// The package is ONE property on ONE nav row, mirrored onto the second registry that renders the
// same href:
//
//   src/lib/rbac.ts     { href: '/commcalc/flags', …, module: 'commissions', scopes: ['all','market'] }
//   src/lib/reports.ts  { href: '/commcalc/flags', label: 'Flags', module: 'commissions', scopes: ['all','market'] }
//
// rbac.ts is a SHARED file: a bad edit is a cross-MODULE nav/permission regression, not a commissions
// bug. So this harness does not merely assert the property is present — it diffs the REAL current
// rbac.ts/reports.ts against the REAL ones on the base commit and requires that the ONLY observable
// difference anywhere in the permission surface is the Flags href.
//
// Asserts:
//   A. The row exists exactly once, in Commissions, with the exact requested shape, next to Chargebacks.
//   B. NO new permission key, no module change, no cap, report-area mapping untouched.
//   C. GATING PARITY with Chargebacks & Fraud over a synthetic role matrix — Flags is now visible to
//      exactly the roles that see Chargebacks (the "matches the chargebacks pattern" requirement).
//   D. LIVE ROLE MATRIX — the 17 REAL roles in production (2 tenants, 98 active users), permissions
//      copied verbatim from storeops.roles. Proves the blast radius on real data: which users' access
//      changes today (answer: ZERO) and which gate now does the blocking.
//   E. THE HOLE ACTUALLY CLOSED — counterfactual: ONE admin tick ("Commission reports" on a
//      store-scoped role) hands that role the un-reviewed Flags queue on the base build and does NOT
//      on this build, while Chargebacks stays gated in both. This is the DM gate in one assertion.
//   F. Sidebar (canSeeItem) and guard (canAccessPath) AGREE on /commcalc/flags for every role, and a
//      super-admin can never be locked out.
//   G. SECOND SURFACE — src/lib/reports.ts (Report Center /reports + employee portal) gates Flags
//      identically, so the change can never leave a visible link the guard bounces.
//   H. ZERO REGRESSION vs the base commit: every PRE-EXISTING nav item x every role answers
//      byte-identically for canSeeItem / navBlockReason / canAccessPath / navModuleForPath /
//      reportAreaForPath; NAV is identical except the one `scopes` property; no other registry moved.
//   I. NEGATIVE CONTROL — the C/E/F/G assertions are RE-RUN against the base build and MUST fail
//      there. A harness that passes on the unfixed tree proves nothing.
//
// Run:  node frontend/prove_flags_dm_gate.mjs      (no network, no DB, no React)

import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..')

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

// ── base commit (the oracle) ─────────────────────────────────────────────────────────────────────
let BASE
try { BASE = execFileSync('git', ['merge-base', 'HEAD', 'origin/main'], { cwd: REPO, encoding: 'utf8' }).trim() }
catch { BASE = '88dcac9' }
const show = (ref, p) => execFileSync('git', ['show', `${ref}:${p}`], { cwd: REPO, encoding: 'utf8' })

// ── transpile a {rbac.ts, reports.ts} pair (types only; no bundler, no stub) ──────────────────────
async function load(name, rbacSrc, reportsSrc) {
  const out = mkdtempSync(join(tmpdir(), `flagsgate-${name}-`))
  writeFileSync(join(out, 'rbac.ts'), rbacSrc)
  // reports.ts imports from '@/lib/rbac'; there is no tsconfig path map in this throwaway dir.
  writeFileSync(join(out, 'reports.ts'), reportsSrc.replace(/'@\/lib\/rbac'/g, "'./rbac.js'"))
  mkdirSync(join(out, 'emptytypes'), { recursive: true })
  execFileSync(join(HERE, 'node_modules/.bin/tsc'),
    [join(out, 'rbac.ts'), join(out, 'reports.ts'), '--target', 'es2020', '--module', 'es2020',
     '--outDir', out, '--skipLibCheck', '--typeRoots', join(out, 'emptytypes')],
    { stdio: 'inherit', cwd: out })
  return {
    rbac: await import(pathToFileURL(join(out, 'rbac.js')).href),
    reports: await import(pathToFileURL(join(out, 'reports.js')).href),
  }
}

const HEAD_RBAC = readFileSync(join(HERE, 'src/lib/rbac.ts'), 'utf8')
const HEAD_REPS = readFileSync(join(HERE, 'src/lib/reports.ts'), 'utf8')
const BASE_RBAC = show(BASE, 'frontend/src/lib/rbac.ts')
const BASE_REPS = show(BASE, 'frontend/src/lib/reports.ts')
must(HEAD_RBAC.includes('export function canSeeItem'), 'rbac.ts no longer exports canSeeItem')
must(BASE_RBAC.includes('export function canSeeItem'), `could not read ${BASE}:frontend/src/lib/rbac.ts`)

const H = await load('head', HEAD_RBAC, HEAD_REPS)
const Bm = await load('base', BASE_RBAC, BASE_REPS)
const R = H.rbac, B = Bm.rbac
const RREP = H.reports, BREP = Bm.reports

const FLAGS = '/commcalc/flags'
const CHARGEBACKS = '/commcalc/chargebacks'

const ALL = R.NAV.flatMap(g => g.items)
const BASE_ALL = B.NAV.flatMap(g => g.items)
const item = h => ALL.find(i => i.href === h)
const baseItem = h => BASE_ALL.find(i => i.href === h)
const groupOf = h => R.NAV.find(g => g.items.some(i => i.href === h))?.group

console.log(`\nbase = ${BASE}  ·  head rbac.ts + reports.ts from the worktree`)

// ── A. the row ───────────────────────────────────────────────────────────────────────────────────
console.log('\nA. the Flags row: exact shape, exactly once, next to Chargebacks')
ck('/commcalc/flags present exactly once in NAV', ALL.filter(i => i.href === FLAGS).length === 1)
ck('it lives in the Commissions group', groupOf(FLAGS) === 'Commissions', groupOf(FLAGS))
ck('exact row shape (only `scopes` added vs base)', JSON.stringify(item(FLAGS)) ===
  JSON.stringify({ href: FLAGS, label: 'Flags', icon: '🚩', module: 'commissions', scopes: ['all', 'market'] }),
  item(FLAGS))
ck('base row had NO scopes at all (the gap this closes)', baseItem(FLAGS).scopes === undefined, baseItem(FLAGS))
ck('scopes are byte-identical to the Chargebacks row', JSON.stringify(item(FLAGS).scopes) === JSON.stringify(item(CHARGEBACKS).scopes),
  { flags: item(FLAGS).scopes, chargebacks: item(CHARGEBACKS).scopes })
ck('Flags still sits immediately before Chargebacks & Fraud',
  R.NAV.find(g => g.group === 'Commissions').items.findIndex(i => i.href === CHARGEBACKS) ===
  R.NAV.find(g => g.group === 'Commissions').items.findIndex(i => i.href === FLAGS) + 1)
ck('label/icon/module unchanged from base',
  item(FLAGS).label === baseItem(FLAGS).label && item(FLAGS).icon === baseItem(FLAGS).icon &&
  item(FLAGS).module === baseItem(FLAGS).module)

// ── B. no new permission key ─────────────────────────────────────────────────────────────────────
console.log('\nB. NO new permission key — nothing to re-seed')
ck('module key still "commissions" (no new key ⇒ no forward-only re-seed trap)', item(FLAGS).module === 'commissions')
ck('no module key added to the nav at all',
  JSON.stringify([...new Set(ALL.map(i => i.module))].sort()) ===
  JSON.stringify([...new Set(BASE_ALL.map(i => i.module))].sort()))
ck('no tenant capability (cap) gate introduced', item(FLAGS).cap === undefined)
ck('reportAreaForPath(/commcalc/flags) still "commissions" (unchanged)',
  R.reportAreaForPath(FLAGS) === 'commissions' && R.reportAreaForPath(FLAGS) === B.reportAreaForPath(FLAGS))
ck('navModuleForPath / moduleForPath unchanged',
  R.navModuleForPath(FLAGS) === B.navModuleForPath(FLAGS) && R.moduleForPath(FLAGS) === B.moduleForPath(FLAGS))
ck('REPORT_DIRECTORY still maps flags to the comm category (same NavItem object is reused)',
  JSON.stringify(R.REPORT_DIRECTORY) === JSON.stringify(B.REPORT_DIRECTORY))

// ── synthetic role matrix ────────────────────────────────────────────────────────────────────────
const COMM_ALL     = { modules: { commissions: true }, scope: 'all' }
const COMM_MARKET  = { modules: { commissions: true }, scope: 'market', reports: { commissions: true } }
const COMM_STORE   = { modules: { commissions: true }, scope: 'store', reports: { commissions: true } }
const COMM_SELF    = { modules: { commissions: true }, scope: 'self', reports: { commissions: true }, home: '/commcalc/targets/my' }
const NO_COMM      = { modules: { storeops: true, closing: true }, scope: 'all' }
const DM           = { modules: { commissions: true, targets: true, storeops: true }, scope: 'market', home: '/commcalc/targets' }
const REP          = { modules: { targets: true }, scope: 'self', home: '/commcalc/targets/my' }
const SUPER        = { modules: { admin: true }, scope: 'all' }
const EMPTY        = {}
const REPORTS_OFF  = { modules: { commissions: true }, scope: 'all', reports: { closing: true } }
const GRANT_FLAGS  = { modules: { commissions: true }, scope: 'store', pages: { [FLAGS]: true } }
const DENY_FLAGS   = { modules: { commissions: true }, scope: 'all', pages: { [FLAGS]: false } }
const GRANT_CB     = { modules: { commissions: true }, scope: 'store', pages: { [CHARGEBACKS]: true } }
const PREFIX_OV    = { modules: { commissions: true }, scope: 'all', pages: { '/commcalc': false } }
const SYNTH = { COMM_ALL, COMM_MARKET, COMM_STORE, COMM_SELF, NO_COMM, DM, REP, SUPER, EMPTY,
                REPORTS_OFF, GRANT_FLAGS, DENY_FLAGS, GRANT_CB, PREFIX_OV }

// ── C. parity with Chargebacks ───────────────────────────────────────────────────────────────────
console.log('\nC. gating parity with Chargebacks & Fraud (the requested pattern)')
for (const [name, p] of Object.entries(SYNTH)) {
  const singled = p.pages && [FLAGS, CHARGEBACKS].some(h => h in p.pages)
  if (singled) continue
  const f = R.canSeeItem(p, item(FLAGS)), c = R.canSeeItem(p, item(CHARGEBACKS))
  ck(`${name}: canSeeItem(flags) === canSeeItem(chargebacks) → ${f}`, f === c, { f, c })
  const fa = R.canAccessPath(p, FLAGS), ca = R.canAccessPath(p, CHARGEBACKS)
  ck(`${name}: canAccessPath(flags) === canAccessPath(chargebacks) → ${fa}`, fa === ca, { fa, ca })
}
ck('a company-wide commissions role still sees Flags', R.canSeeItem(COMM_ALL, item(FLAGS)))
ck('a DM (scope market) still sees Flags — the gate lets the DM through', R.canSeeItem(COMM_MARKET, item(FLAGS)) && R.canAccessPath(COMM_MARKET, FLAGS))
ck('a store-scoped role WITH commission reports no longer sees Flags',
  !R.canSeeItem(COMM_STORE, item(FLAGS)) && !R.canAccessPath(COMM_STORE, FLAGS))
ck('a self-scoped rep WITH commission reports no longer sees Flags',
  !R.canSeeItem(COMM_SELF, item(FLAGS)) && !R.canAccessPath(COMM_SELF, FLAGS))
ck('an EXACT per-function grant still lifts the gate (identical to chargebacks)',
  R.canSeeItem(GRANT_FLAGS, item(FLAGS)) === R.canSeeItem(GRANT_CB, item(CHARGEBACKS)) && R.canSeeItem(GRANT_FLAGS, item(FLAGS)))
ck('an explicit per-function deny still hides it from a company-wide role', !R.canSeeItem(DENY_FLAGS, item(FLAGS)))
ck('navBlockReason is the exact inverse of canSeeItem for Flags, every synthetic role',
  Object.values(SYNTH).every(p => (R.navBlockReason(p, item(FLAGS)) === null) === R.canSeeItem(p, item(FLAGS))))
ck('a blocked store role is told WHY it is blocked (gate = scope, actionable text)',
  R.navBlockReason(COMM_STORE, item(FLAGS))?.gate === 'scope' &&
  /all\/market/.test(R.navBlockReason(COMM_STORE, item(FLAGS))?.detail || ''),
  R.navBlockReason(COMM_STORE, item(FLAGS)))

// ── D. LIVE role matrix (verbatim from storeops.roles, 2026-08-08) ───────────────────────────────
// org label only — no UUIDs, no credentials. `users` = active app_users on that role.
const LIVE = [
  { org: 'house',  name: 'admin',            users: 7,  perms: { modules: { commissions: true, admin: true } } },
  { org: 'house',  name: 'director',         users: 0,  perms: { modules: { commissions: true }, scope: 'all' } },
  { org: 'house',  name: 'district_manager', users: 3,  perms: { modules: { commissions: false }, scope: 'market' } },
  { org: 'house',  name: 'executive',        users: 0,  perms: { modules: { commissions: true }, scope: 'all' } },
  { org: 'house',  name: 'market_manager',   users: 1,  perms: { modules: { commissions: false }, scope: 'market',
      reports: { vip: false, asset: false, closing: true, accounts: false, storeops: false, commissions: false },
      pages: { '/commcalc/flags': true } } },
  { org: 'house',  name: 'regional_manager', users: 0,  perms: { modules: { commissions: false }, scope: 'market' } },
  { org: 'house',  name: 'sales_consultant', users: 0,  perms: { modules: { commissions: false }, scope: 'self' } },
  { org: 'house',  name: 'sales_rep',        users: 31, perms: { modules: { commissions: false }, scope: 'self' } },
  { org: 'house',  name: 'store_manager',    users: 0,  perms: { modules: { commissions: true }, scope: 'store' } },
  { org: 'house',  name: 'support_agent',    users: 0,  perms: { modules: { support: true }, scope: 'all' } },
  { org: 'tenantB', name: 'accountant',      users: 1,  perms: { modules: { accounts: true }, scope: 'all',
      reports: { vip: true, asset: true, closing: false, accounts: true, storeops: true, commissions: true } } },
  { org: 'tenantB', name: 'admin',           users: 5,  perms: { modules: { commissions: true, admin: true } } },
  { org: 'tenantB', name: 'company',         users: 0,  perms: { modules: { commissions: true }, scope: 'all',
      reports: { vip: true, asset: true, closing: true, accounts: true, storeops: true, commissions: true } } },
  { org: 'tenantB', name: 'market',          users: 0,  perms: { modules: { commissions: true }, scope: 'all',
      reports: { vip: true, asset: true, closing: true, accounts: true, storeops: true, commissions: true } } },
  { org: 'tenantB', name: 'market_manager',  users: 3,  perms: { modules: { commissions: true }, scope: 'all',
      reports: { vip: false, asset: false, closing: true, accounts: false, storeops: false, commissions: false },
      pages: { '/commcalc/chargebacks': true } } },
  { org: 'tenantB', name: 'sales_rep',       users: 34, perms: { modules: { commissions: false }, scope: 'self' } },
  { org: 'tenantB', name: 'store_manager',   users: 13, perms: { modules: { commissions: false }, scope: 'store',
      pages: { '/commcalc/flags': false, '/commcalc/chargebacks': false } } },
]
console.log('\nD. LIVE role matrix — 17 real roles, 2 tenants, 98 active users')
let liveChanged = 0, liveUsersChanged = 0, gateChanged = 0
const rows = []
for (const r of LIVE) {
  const p = r.perms
  const before = { see: B.canSeeItem(p, baseItem(FLAGS)), access: B.canAccessPath(p, FLAGS),
                   why: B.navBlockReason(p, baseItem(FLAGS))?.gate ?? '-' }
  const after  = { see: R.canSeeItem(p, item(FLAGS)),     access: R.canAccessPath(p, FLAGS),
                   why: R.navBlockReason(p, item(FLAGS))?.gate ?? '-' }
  if (before.see !== after.see || before.access !== after.access) { liveChanged++; liveUsersChanged += r.users }
  if (before.why !== after.why) gateChanged++
  rows.push(`   ${(r.org + '/' + r.name).padEnd(26)} scope=${String(p.scope ?? (p.modules?.admin ? 'super' : 'all')).padEnd(7)} users=${String(r.users).padStart(2)}  ` +
            `before[see=${before.see} access=${before.access} gate=${before.why}]  after[see=${after.see} access=${after.access} gate=${after.why}]`)
}
rows.forEach(l => console.log(l))
ck('ZERO live roles change Flags visibility or reachability', liveChanged === 0, liveChanged)
ck('ZERO of the 98 active users lose (or gain) access to Flags today', liveUsersChanged === 0, liveUsersChanged)
ck('every role that could open Flags before can still open it', LIVE.every(r => !B.canAccessPath(r.perms, FLAGS) || R.canAccessPath(r.perms, FLAGS)))
ck('house market_manager keeps its explicit per-function Flags grant (scope market passes the gate)',
  R.canSeeItem(LIVE.find(r => r.org === 'house' && r.name === 'market_manager').perms, item(FLAGS)))
ck('both tenants\' super-admins (modules.admin) still reach Flags — operator never locked out',
  LIVE.filter(r => r.perms.modules?.admin).length === 2 &&
  LIVE.filter(r => r.perms.modules?.admin).every(r => R.canSeeItem(r.perms, item(FLAGS)) && R.canAccessPath(r.perms, FLAGS)))
console.log(`   → ${gateChanged} role(s) now blocked by the SCOPE gate instead of the soft report gate (same outcome, hard gate)`)

// ── E. the hole actually closed ──────────────────────────────────────────────────────────────────
console.log('\nE. THE HOLE — one admin tick used to hand a store role the un-reviewed queue')
// Counterfactual 1: the LIVE house store_manager, plus the single tick "Commission reports".
const CF_HOUSE_SM = { modules: { commissions: true }, scope: 'store', reports: { commissions: true } }
ck('base: store_manager + "Commission reports" SEES Flags', B.canSeeItem(CF_HOUSE_SM, baseItem(FLAGS)))
ck('base: …and the guard LETS THEM IN (real reachability, no DM in between)', B.canAccessPath(CF_HOUSE_SM, FLAGS))
ck('base: the same role does NOT see Chargebacks (proving Flags was the outlier)', !B.canSeeItem(CF_HOUSE_SM, baseItem(CHARGEBACKS)))
ck('HEAD: store_manager + "Commission reports" no longer sees Flags', !R.canSeeItem(CF_HOUSE_SM, item(FLAGS)))
ck('HEAD: …and the guard blocks the direct URL too', !R.canAccessPath(CF_HOUSE_SM, FLAGS))
// Counterfactual 2: tenant B's 13-user store_manager, given the commissions module + reports.
const CF_B_SM = { modules: { commissions: true }, scope: 'store', reports: { commissions: true },
                  pages: { '/commcalc/chargebacks': false } }
ck('base: tenantB store_manager (13 users) + module + reports reaches Flags', B.canAccessPath(CF_B_SM, FLAGS))
ck('HEAD: same role is blocked', !R.canAccessPath(CF_B_SM, FLAGS))
// Counterfactual 3: a self-scoped rep whose role got the commissions module + reports — the sidebar
// used to advertise Flags and the guard then bounced it (the "clicking does nothing" class).
ck('base: a rep with commissions+reports was SHOWN Flags in the sidebar', B.canSeeItem(COMM_SELF, baseItem(FLAGS)))
ck('base: …but the guard bounced it — sidebar/guard DISAGREED', !B.canAccessPath(COMM_SELF, FLAGS))
ck('HEAD: the rep is no longer shown it — sidebar and guard now AGREE',
  !R.canSeeItem(COMM_SELF, item(FLAGS)) && !R.canAccessPath(COMM_SELF, FLAGS))

// ── F. sidebar/guard agreement + super-admin ─────────────────────────────────────────────────────
console.log('\nF. sidebar ↔ guard agreement on /commcalc/flags')
for (const [name, p] of Object.entries(SYNTH)) {
  if (p.pages && '/commcalc' in p.pages) continue          // pre-existing prefix-override quirk (see below)
  if ((p.scope || 'all') === 'self' && !p.pages) {
    // self scope: canAccessPath is home/SELF_ALLOWED-driven; agreement is asserted, not inherited
    ck(`${name}: self-scope → both false`, !R.canSeeItem(p, item(FLAGS)) && !R.canAccessPath(p, FLAGS))
    continue
  }
  ck(`${name}: canSeeItem === canAccessPath`, R.canSeeItem(p, item(FLAGS)) === R.canAccessPath(p, FLAGS),
    { see: R.canSeeItem(p, item(FLAGS)), access: R.canAccessPath(p, FLAGS) })
}
ck('the pre-existing /commcalc prefix-override quirk is INHERITED, not introduced (flags behaves like chargebacks)',
  R.canSeeItem(PREFIX_OV, item(FLAGS)) === R.canSeeItem(PREFIX_OV, item(CHARGEBACKS)) &&
  R.canAccessPath(PREFIX_OV, FLAGS) === R.canAccessPath(PREFIX_OV, CHARGEBACKS))
ck('super-admin sees and reaches Flags', R.canSeeItem(SUPER, item(FLAGS)) && R.canAccessPath(SUPER, FLAGS))
ck('super-admin bypass is unchanged for EVERY nav item',
  ALL.every(i => R.canSeeItem(SUPER, i)) && BASE_ALL.every(i => B.canSeeItem(SUPER, i)))
// Flags is in REPORT_DIRECTORY, so applyNavLayout renders it TWICE when visible (its module group +
// the "Reports · Commissions & Pay" category) — by design, and exactly what Chargebacks does. The
// meaningful assertion is parity with Chargebacks + zero copies when the role is gated out.
const layoutCount = (mod, p, href) => {
  const filtered = mod.NAV.map(g => ({ ...g, items: g.items.filter(i => mod.canSeeItem(p, i)) })).filter(g => g.items.length)
  return mod.applyNavLayout(filtered, undefined).flatMap(g => g.items).filter(i => i.href === href).length
}
ck('applyNavLayout: Flags renders exactly as many times as Chargebacks, every role',
  [COMM_ALL, COMM_MARKET, COMM_STORE, COMM_SELF, DM, REP, SUPER, PREFIX_OV, EMPTY, NO_COMM]
    .every(p => layoutCount(R, p, FLAGS) === layoutCount(R, p, CHARGEBACKS)))
ck('applyNavLayout: a gated-out store role gets ZERO Flags copies (sidebar AND Reports directory)',
  layoutCount(R, COMM_STORE, FLAGS) === 0 && layoutCount(B, COMM_STORE, FLAGS) === 2,
  { head: layoutCount(R, COMM_STORE, FLAGS), base: layoutCount(B, COMM_STORE, FLAGS) })
ck('applyNavLayout: a company-wide role still gets both copies (module group + Reports directory)',
  layoutCount(R, COMM_ALL, FLAGS) === 2 && layoutCount(R, COMM_ALL, FLAGS) === layoutCount(B, COMM_ALL, FLAGS))

// ── G. second surface: src/lib/reports.ts ────────────────────────────────────────────────────────
console.log('\nG. SECOND SURFACE — Report Center /reports + employee portal (src/lib/reports.ts)')
const rdef = (mod, h) => mod.REPORT_CATEGORIES.flatMap(c => c.reports).find(r => r.href === h)
ck('reports.ts lists /commcalc/flags exactly once',
  RREP.REPORT_CATEGORIES.flatMap(c => c.reports).filter(r => r.href === FLAGS).length === 1)
ck('base reports.ts entry had NO scopes (the second door was wide open)', rdef(BREP, FLAGS).scopes === undefined)
ck('HEAD reports.ts entry now carries scopes ["all","market"]',
  JSON.stringify(rdef(RREP, FLAGS).scopes) === JSON.stringify(['all', 'market']))
ck('…identical to the Chargebacks entry that already had it',
  JSON.stringify(rdef(RREP, FLAGS).scopes) === JSON.stringify(rdef(RREP, CHARGEBACKS).scopes))
ck('label/module unchanged', rdef(RREP, FLAGS).label === rdef(BREP, FLAGS).label && rdef(RREP, FLAGS).module === rdef(BREP, FLAGS).module)
for (const [name, p] of Object.entries(SYNTH)) {
  const singled = p.pages && [FLAGS, CHARGEBACKS].some(h => h in p.pages)
  if (singled) continue
  ck(`${name}: clearedFor(flags) === clearedFor(chargebacks)`,
    RREP.clearedFor(p, rdef(RREP, FLAGS)) === RREP.clearedFor(p, rdef(RREP, CHARGEBACKS)))
}
// NO NEW DEAD LINKS: anything the Report Center offers should be reachable through the guard. ONE
// pre-existing exception survives and is NOT this package's to fix — a role carrying a PREFIX-level
// per-function override (pages['/commcalc'] = false) is shown every /commcalc/* report and bounced by
// the guard, because canSeeItem honors only an EXACT-href override while canAccessPath takes the
// longest matching nav prefix. That quirk is on main today for Chargebacks, Discrepancy, Recovery and
// every other /commcalc report alike. The honest assertion is that Flags matches Chargebacks exactly.
const SHAPES = [...Object.values(SYNTH), ...LIVE.map(r => r.perms), CF_HOUSE_SM, CF_B_SM]
const deadCount = (mod, rep, href) => SHAPES.filter(p => rep.clearedFor(p, rdef(rep, href)) && !mod.canAccessPath(p, href)).length
const dead = deadCount(R, RREP, FLAGS), deadCB = deadCount(R, RREP, CHARGEBACKS)
const baseDead = deadCount(B, BREP, FLAGS), baseDeadCB = deadCount(B, BREP, CHARGEBACKS)
console.log(`   → dead Flags links over ${SHAPES.length} role shapes: base ${baseDead} → head ${dead}   (Chargebacks: base ${baseDeadCB} → head ${deadCB})`)
ck('HEAD: Flags has exactly as many dead Report-Center links as Chargebacks (quirk inherited, not introduced)',
  dead === deadCB, { dead, deadCB })
ck('HEAD: strictly FEWER dead Flags links than base', dead < baseDead, { base: baseDead, head: dead })
ck('the ONE survivor is the pre-existing /commcalc prefix-override quirk, which base has too',
  dead === 1 && baseDeadCB === deadCB &&
  RREP.clearedFor(PREFIX_OV, rdef(RREP, FLAGS)) && !R.canAccessPath(PREFIX_OV, FLAGS) &&
  BREP.clearedFor(PREFIX_OV, rdef(BREP, CHARGEBACKS)) && !B.canAccessPath(PREFIX_OV, CHARGEBACKS))
ck('excluding that quirk, HEAD has ZERO dead Flags links',
  SHAPES.filter(p => !(p.pages && '/commcalc' in p.pages))
        .filter(p => RREP.clearedFor(p, rdef(RREP, FLAGS)) && !R.canAccessPath(p, FLAGS)).length === 0)
ck('every OTHER reports.ts entry is byte-identical to base',
  JSON.stringify(RREP.REPORT_CATEGORIES.map(c => ({ ...c, reports: c.reports.filter(r => r.href !== FLAGS) }))) ===
  JSON.stringify(BREP.REPORT_CATEGORIES.map(c => ({ ...c, reports: c.reports.filter(r => r.href !== FLAGS) }))))

// ── H. ZERO REGRESSION vs base ───────────────────────────────────────────────────────────────────
console.log('\nH. ZERO REGRESSION — the Flags href is the ONLY difference')
const stripScopes = nav => JSON.stringify(nav.map(g => ({ ...g, items: g.items.map(i => i.href === FLAGS ? { ...i, scopes: undefined } : i) })))
ck('NAV with the flags `scopes` removed is byte-identical to base NAV', stripScopes(R.NAV) === stripScopes(B.NAV))
ck('no href added or removed', ALL.length === BASE_ALL.length &&
  ALL.map(i => i.href).sort().join() === BASE_ALL.map(i => i.href).sort().join())
for (const reg of ['REPORT_AREAS', 'REPORT_CATEGORIES', 'REPORT_DIRECTORY', 'DATA_GRANTS', 'MODULE_ALIASES', 'NAV_CARRIERS']) {
  ck(`${reg} unchanged`, JSON.stringify(R[reg]) === JSON.stringify(B[reg]))
}
const ROLES_ALL = { ...SYNTH, ...Object.fromEntries(LIVE.map(r => [`${r.org}/${r.name}`, r.perms])), CF_HOUSE_SM, CF_B_SM }
let drifts = 0, driftHrefs = new Set()
for (const p of Object.values(ROLES_ALL)) {
  for (const i of BASE_ALL) {
    if (i.href === FLAGS) continue
    const mine = ALL.find(x => x.href === i.href)
    if (R.canSeeItem(p, mine) !== B.canSeeItem(p, i)) { drifts++; driftHrefs.add('see:' + i.href) }
    if (JSON.stringify(R.navBlockReason(p, mine)) !== JSON.stringify(B.navBlockReason(p, i))) { drifts++; driftHrefs.add('why:' + i.href) }
    if (R.canAccessPath(p, i.href) !== B.canAccessPath(p, i.href)) { drifts++; driftHrefs.add('acc:' + i.href) }
    if (R.navModuleForPath(i.href) !== B.navModuleForPath(i.href)) { drifts++; driftHrefs.add('mod:' + i.href) }
    if (R.reportAreaForPath(i.href) !== B.reportAreaForPath(i.href)) { drifts++; driftHrefs.add('area:' + i.href) }
  }
}
ck(`no gate drift on ANY other nav item (${BASE_ALL.length - 1} items x ${Object.keys(ROLES_ALL).length} roles = ${(BASE_ALL.length - 1) * Object.keys(ROLES_ALL).length} cells)`,
  drifts === 0, [...driftHrefs].slice(0, 10))
const PROBES = ['/commcalc', '/commcalc/flag', '/commcalc/flags', '/commcalc/flags/detail', '/commcalc/flagship',
                '/commcalc/chargebacks', '/commcalc/accessory-flags', '/commcalc/kpi', '/reports', '/admin/roles',
                '/storeops/team', '/closing', '/commcalc/targets/my']
let pdrift = 0, pdriftPaths = []
for (const p of Object.values(ROLES_ALL)) for (const path of PROBES) {
  if (path === FLAGS) continue
  if (R.canAccessPath(p, path) !== B.canAccessPath(p, path)) { pdrift++; pdriftPaths.push(path) }
  if (R.navModuleForPath(path) !== B.navModuleForPath(path)) { pdrift++; pdriftPaths.push('mod:' + path) }
}
ck('no drift on probe paths (incl. /commcalc/flags/detail and the /commcalc/flag* near-misses)', pdrift === 0, [...new Set(pdriftPaths)])
ck('a Flags SUB-path inherits the gate via the nav prefix, same as chargebacks sub-paths',
  R.canAccessPath(COMM_STORE, '/commcalc/flags/detail') === R.canAccessPath(COMM_STORE, '/commcalc/chargebacks/detail'))
ck('the diff is 2 files / 1 href — nothing else in rbac.ts or reports.ts moved',
  stripScopes(R.NAV) === stripScopes(B.NAV) &&
  JSON.stringify(RREP.REPORT_CATEGORIES.flatMap(c => c.reports).filter(r => r.href !== FLAGS)) ===
  JSON.stringify(BREP.REPORT_CATEGORIES.flatMap(c => c.reports).filter(r => r.href !== FLAGS)))

// ── I. NEGATIVE CONTROL ──────────────────────────────────────────────────────────────────────────
console.log('\nI. NEGATIVE CONTROL — the same assertions MUST fail on the base build')
const negatives = [
  ['base flags row has scopes', baseItem(FLAGS).scopes !== undefined],
  ['base: store role w/ commission reports is blocked from Flags', !B.canAccessPath(CF_HOUSE_SM, FLAGS)],
  ['base: flags/chargebacks visibility parity for a store role',
    B.canSeeItem(CF_HOUSE_SM, baseItem(FLAGS)) === B.canSeeItem(CF_HOUSE_SM, baseItem(CHARGEBACKS))],
  ['base: rep sidebar/guard agree on Flags', B.canSeeItem(COMM_SELF, baseItem(FLAGS)) === B.canAccessPath(COMM_SELF, FLAGS)],
  ['base: reports.ts flags entry carries scopes', rdef(BREP, FLAGS).scopes !== undefined],
  ['base: Report Center offers no dead Flags link', baseDead === 0],
]
for (const [label, held] of negatives) ck(`NEG: "${label}" is FALSE on base (harness detects the gap)`, held === false, held)

console.log(`\n${fail === 0 ? 'PASS' : 'FAIL'}  ${pass}/${pass + fail}   (base ${BASE})`)
process.exit(fail === 0 ? 0 : 1)
