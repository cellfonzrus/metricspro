#!/usr/bin/env node
// PROOF — a manager-only payout report the SERVER refuses a rep is never offered to that rep in the menu, the
// report directory, a hub or by URL (owner 2026-09-26: "pay discrepancy should be hidden"). The pages come from
// /me (`permissions.payout.refused_pages`, built by backend payout_audience.viewer_payload from THE registry
// MANAGER_ONLY_SURFACES) and are read by lib/rbac.ts `payoutRefused` inside canSeeItem / canAccessPath — the two
// gates every NAV consumer already goes through. Since 2026-09-28 (index §6m) they are refused to every viewer
// WITHOUT the carrier permission (a store manager as much as a rep). Backend half + lock:
// backend/harness_payout_audience*.py.
//
// Run:  node frontend/tools/payout-nav-proof.mjs
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import os from 'node:os'
import path from 'node:path'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const OUT = mkdtempSync(path.join(os.tmpdir(), 'paynav-'))
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

// THE registry's pages, read from the backend source (the same list the server refuses)
const PA = readFileSync(path.join(FRONTEND, '../backend/app/modules/commcalc/payout_audience.py'), 'utf8')
const reg = PA.slice(PA.indexOf('MANAGER_ONLY_SURFACES = ('), PA.indexOf('MANAGER_ONLY_PAGES ='))
const REFUSED = [...reg.matchAll(/"pages":\s*\(([^)]*)\)/g)].flatMap(m => [...m[1].matchAll(/"([^"]+)"/g)].map(x => x[1]))
ok('the registry names the Pay Discrepancy page', REFUSED.includes('/commcalc/discrepancy'), REFUSED.join(','))

// the registry's menu-less pages ("menu": False — reached only through in-app links, e.g. the commission-explain
// carrier diagnostic): no NAV entry to hide, the route guard refuses the URL (asserted below for every page)
const MENULESS = [...reg.matchAll(/\{"key":[^{}]*?"pages":\s*\(([^)]*)\)[^{}]*?"menu":\s*False/g)]
  .flatMap(m => [...m[1].matchAll(/"([^"]+)"/g)].map(x => x[1]))
ok('the registry names the commission-explain carrier diagnostic as a menu-less page',
  MENULESS.includes('/commcalc/commission-explain') && REFUSED.includes('/commcalc/commission-explain'), MENULESS.join(','))

const base = { modules: { commissions: true }, reports: { comm: true, commissions: true } }
const rep = { ...base, scope: 'self', payout: { audience: 'employee', carrier_view: false, refused_pages: REFUSED } }
// OWNER DECISION 2026-09-28 (index §6m): carrier commission is "only for the eyes of the management, gated out from
// all levels" — a store manager WITHOUT the carrier permission is refused the same pages as a rep; top management
// (the permission's house default) is refused nothing. (Before: "a manager still sees Pay Discrepancy".)
const sm = { ...base, scope: 'store', payout: { audience: 'manager', carrier_view: false, refused_pages: REFUSED } }
const mgr = { ...base, scope: 'all', payout: { audience: 'manager', carrier_view: true, refused_pages: [] } }
const items = R.NAV.flatMap(g => g.items)
const refusedItems = items.filter(it => REFUSED.includes(it.href))
ok('every registered MENU page has a NAV entry (the menu can be told to hide it)',
  REFUSED.filter(h => !MENULESS.includes(h)).every(h => items.some(it => it.href === h)),
  REFUSED.filter(h => !MENULESS.includes(h) && !items.some(it => it.href === h)).join(','))
ok('a rep sees NONE of them (sidebar, hub, report directory, screen links — all canSeeItem)',
  refusedItems.every(it => !R.canSeeItem(rep, it)), refusedItems.filter(it => R.canSeeItem(rep, it)).map(i => i.href).join(','))
ok('a rep cannot open any of them by URL (canAccessPath)', REFUSED.every(h => !R.canAccessPath(rep, h)))
ok('nor a sub-path of one', !R.canAccessPath(rep, '/commcalc/discrepancy/2026-07'))
const granted = { ...rep, pages: Object.fromEntries(REFUSED.map(h => [h, true])) }
ok('a per-function grant cannot reopen what the server refuses',
  refusedItems.every(it => !R.canSeeItem(granted, it)) && REFUSED.every(h => !R.canAccessPath(granted, h)))
const adminRep = { ...rep, modules: { ...rep.modules, admin: true } }
ok('nor can the admin module (the server refuses by who is looking, not by module)',
  refusedItems.every(it => !R.canSeeItem(adminRep, it)))
ok('the roles editor can say why (navBlockReason gate "payout")',
  refusedItems.every(it => (R.navBlockReason(rep, it) || {}).gate === 'payout'))
const payDisc = items.find(it => it.href === '/commcalc/discrepancy')
ok('a STORE MANAGER without the carrier permission sees none of them and reaches none by URL (incl. the diagnostic)',
  refusedItems.every(it => !R.canSeeItem(sm, it)) && REFUSED.every(h => !R.canAccessPath(sm, h))
  && !R.canAccessPath(sm, '/commcalc/commission-explain'))
ok('...and no per-function grant or admin module reopens them for that manager',
  refusedItems.every(it => !R.canSeeItem({ ...sm, pages: Object.fromEntries(REFUSED.map(h => [h, true])),
    modules: { ...sm.modules, admin: true } }, it)))
ok('top management (holds the carrier permission) still sees Pay Discrepancy and reaches the diagnostic',
  !!payDisc && R.canSeeItem(mgr, payDisc) && R.canAccessPath(mgr, '/commcalc/discrepancy')
  && R.canAccessPath(mgr, '/commcalc/commission-explain'))
ok('the Rep Incentive report stays open to a store manager without the permission',
  R.canAccessPath({ ...sm, pages: { '/commcalc/reports': true } }, '/commcalc/reports') && !R.payoutRefused(sm, '/commcalc/reports'))
ok('the Rep Incentive report stays open to the rep', R.canAccessPath({ ...rep, pages: { '/commcalc/reports': true } }, '/commcalc/reports'))
ok('no payout field on /me (an older server) → nothing hidden by this gate', !R.payoutRefused(base, '/commcalc/discrepancy'))
console.log(`\n${fail === 0 ? 'ALL GREEN' : 'FAILURES'} — ${pass} passed, ${fail} failed`)
process.exitCode = fail === 0 ? 0 : 1
