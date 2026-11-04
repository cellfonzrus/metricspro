#!/usr/bin/env node
// PROOF — a confined employee's menu and route guard are the SERVER's declaration, and the prefix
// hole that let a rep open the Hours Adjustment screen by URL is gone.
//
// OWNER (2026-11-04): "on ny logistics Manan was able to go past it and also see the adjustment
// hours, this should not be allowed for any sales reps."
//
// §A REPRODUCES THE DEFECT FIRST, against the REAL compiled `lib/rbac.ts` on the legacy code path
// (the one a browser takes when its backend sends no `rep_surface` payload): a self-scoped rep with
// the Workforce module reaches `/storeops/timeclock` — Hours Adjustment — and 30-odd other manager
// pages, because `canAccessPath` prefix-matched the `/storeops` menu row it could see.
//
// The pages in §B are read out of `backend/app/core/rep_surface.py` — THE declaration — so this
// proof cannot pass against a list this file made up.
//
// Run:  node frontend/tools/rep-surface-nav-proof.mjs
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import os from 'node:os'
import path from 'node:path'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const OUT = mkdtempSync(path.join(os.tmpdir(), 'repsurf-'))
writeFileSync(path.join(OUT, 'package.json'), '{"type":"module"}')
process.on('exit', () => rmSync(OUT, { recursive: true, force: true }))
let pass = 0, fail = 0
const ok = (name, cond, why = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) } else { fail++; console.log(`  FAIL  ${name}${why ? ' — ' + why : ''}`) }
}
execFileSync(path.join(FRONTEND, 'node_modules/.bin/tsc'), [
  path.join(FRONTEND, 'src/lib/rbac.ts'), '--outDir', OUT, '--target', 'es2020',
  '--module', 'es2020', '--moduleResolution', 'node', '--skipLibCheck'], { stdio: 'inherit' })
const R = await import(pathToFileURL(path.join(OUT, 'rbac.js')).href)

// ── THE declaration, read from the backend source ────────────────────────────────────────────────
const SRC = readFileSync(path.join(FRONTEND, '../backend/app/core/rep_surface.py'), 'utf8')
const quoted = re => [...SRC.matchAll(re)].flatMap(m => [...m[1].matchAll(/"([^"]+)"/g)].map(x => x[1]))
const PAGES = quoted(/"pages":\s*\(([^)]*)\)/g)
const EXCEPT = quoted(/"page_except":\s*\(([^)]*)\)/g)
const LANDING = (SRC.match(/^LANDING_PAGE = "([^"]+)"/m) || [])[1]
ok('the declaration was read (pages, carve-outs and the landing)',
  PAGES.length >= 10 && EXCEPT.length >= 1 && !!LANDING, `${PAGES.length}/${EXCEPT.length}/${LANDING}`)
ok('the landing route this file names is the one the server declares', R.REP_LANDING === LANDING,
  `${R.REP_LANDING} vs ${LANDING}`)

const mods = (...ks) => Object.fromEntries(ks.map(k => [k, true]))
// The manager pages the owner's report is about, plus the rest of the subtree that came with them.
const MANAGER = ['/storeops/timeclock', '/storeops/payroll', '/storeops/payroll/approvals',
  '/storeops/payroll-tax', '/storeops/employees', '/storeops/setup/employees', '/storeops/setup/stores',
  '/storeops/attendance', '/storeops/accountability', '/storeops/admin', '/storeops/reports',
  '/storeops/hours-budget', '/storeops/timeclock-permissions', '/storeops/visits', '/storeops/team',
  '/helpdesk/settings', '/helpdesk/dashboard', '/reports', '/commcalc/kpi']

// ══ A. THE DEFECT, on the legacy path (no rep_surface payload — an older backend) ════════════════
console.log('\nA. the defect, reproduced on the pre-fix path')
const legacy = { modules: mods('targets', 'helpdesk', 'storeops'), scope: 'self', home: '/commcalc/targets/my' }
ok('A1 a rep with the Workforce module reached Hours Adjustment by URL',
  R.canAccessPath(legacy, '/storeops/timeclock') === true)
ok('A2 …and Hours Approval', R.canAccessPath(legacy, '/storeops/payroll/approvals') === true)
const legacyReach = MANAGER.filter(p => R.canAccessPath(legacy, p))
ok('A3 …and most of the rest of the manager subtree with it', legacyReach.length >= 15, `${legacyReach.length}`)
ok('A4 the menu never offered any of them — the hole was the URL, not the sidebar',
  R.NAV.flatMap(g => g.items).filter(it => MANAGER.includes(it.href) && R.canSeeItem(legacy, it)).length <= 2)

// ══ B. THE FIX — the server's declaration, enforced here ════════════════════════════════════════
console.log('\nB. the confined employee')
const rep = { ...legacy, rep_surface: { confined: true, landing: LANDING, pages: PAGES, page_except: EXCEPT } }
ok('B1 the viewer is confined because the SERVER said so, not because this file worked it out',
  R.repConfined(rep) && !R.repConfined(legacy))
for (const p of MANAGER) ok(`B- ${p} is refused`, R.canAccessPath(rep, p) === false)
ok('B5 every page the server declared is reachable',
  PAGES.map(e => e.replace(/\/\*\*$/, '')).every(p => R.canAccessPath(rep, p)),
  PAGES.filter(e => !R.canAccessPath(rep, e.replace(/\/\*\*$/, ''))).join(','))
ok('B6 a path inside a declared SUBTREE is reachable', R.canAccessPath(rep, '/helpdesk/41'))
ok('B7 a carve-out inside that same subtree is not', R.canAccessPath(rep, '/helpdesk/settings') === false)
ok('B8 the menu shows ONLY declared pages — the sidebar and the guard cannot disagree',
  R.NAV.flatMap(g => g.items).filter(it => R.canSeeItem(rep, it))
    .every(it => R.canAccessPath(rep, it.href)))
ok('B9 …and every menu row it does show is one of them',
  R.NAV.flatMap(g => g.items).filter(it => R.canSeeItem(rep, it)).every(it => R.repPageAllowed(rep, it.href)))
ok('B10 a per-function grant cannot reopen a manager page for a confined viewer (the gate is CLOSED)',
  R.canAccessPath({ ...rep, pages: { '/storeops/timeclock': true } }, '/storeops/timeclock') === false)
ok('B11 the admin module cannot either', R.canAccessPath({ ...rep, modules: mods('admin') }, '/storeops/payroll') === false)
ok('B12 they land on the tiled page', R.homeFor(rep) === LANDING && R.safeHomeFor(rep) === LANDING)
ok('B13 …and can always open it', R.canAccessPath(rep, LANDING))
ok('B14 their own password is never refused', R.canAccessPath(rep, '/account/password'))
ok('B15 the page title of the landing comes from NAV like every other page',
  R.navLabelForPath(LANDING) !== null, String(R.navLabelForPath(LANDING)))

// ══ C. THE STORE MANAGER — landed, NOT confined ═════════════════════════════════════════════════
console.log('\nC. the store manager keeps their manager features')
const smPerms = {
  modules: mods('commissions', 'targets', 'asset', 'storeops', 'closing', 'helpdesk', 'ai_assistant'),
  scope: 'store', home: '/commcalc/targets',
  rep_surface: { confined: false, landing: LANDING },   // what the server sends a store-level role
}
ok('C1 a store manager is not confined', !R.repConfined(smPerms))
ok('C2 …lands on the tiled page', R.homeFor(smPerms) === LANDING)
ok('C3 …can open it', R.canAccessPath(smPerms, LANDING))
ok('C4 …and keeps the manager pages their scope allows',
  ['/storeops/schedule', '/storeops/team', '/closing/submit', '/commcalc/targets']
    .every(p => R.canAccessPath(smPerms, p)))
ok('C5 …while a page declared market-and-above stays refused ONE SEGMENT DEEP too (the sibling of '
  + 'the same defect: /storeops/payroll is all+market, and /storeops/payroll/approvals used to fall through)',
  R.canAccessPath(smPerms, '/storeops/payroll') === false
  && R.canAccessPath(smPerms, '/storeops/payroll/approvals') === false)
ok('C6 the governing nav item is the longest matching href, not an exact one',
  R.governingNavItem('/storeops/payroll/approvals')?.href === '/storeops/payroll/approvals'
  && R.governingNavItem('/storeops/payroll/unknown-child')?.href === '/storeops/payroll')

// ══ D. EVERY OTHER ROLE IS UNTOUCHED ════════════════════════════════════════════════════════════
console.log('\nD. nothing changes for a role the server says nothing about')
const admin = { modules: mods('admin', 'storeops', 'commissions', 'targets'), scope: 'all', home: '/commcalc' }
ok('D1 an admin carries no rep_surface and is confined by nothing', !R.repConfined(admin))
ok('D2 …reaches the hours screens', R.canAccessPath(admin, '/storeops/timeclock'))
ok('D3 …and keeps their configured home', R.homeFor(admin) === '/commcalc')
// `reports` is given because /storeops/payroll is a REPORT page and a market scope does not pass the
// report gate by default — that gate predates this change and is not what is being proved here.
const market = { modules: mods('storeops', 'targets'), scope: 'market', home: '/commcalc/targets',
                 reports: { payroll: true, ops: true, storeops: true } }
ok('D4 a market manager keeps theirs', R.homeFor(market) === '/commcalc/targets')
ok('D5 …and still reaches the market-and-above pages', R.canAccessPath(market, '/storeops/payroll'))

console.log('\n' + '='.repeat(100))
console.log(`${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
