// Proof for the ADMIN ATTENTION gate + the /admin/import-health nav entry (mig 717, owner directive
// 2026-07-25). Run:  node frontend/scratchpad/prove_import_health_nav.mjs
//
// Strategy (same as prove_nav_layout / prove_reports_directory): verbatim re-implementations of the
// shipped rbac.ts predicates, PLUS source-parity guards that assert the real file still contains those
// exact bodies — so this can never drift into proving a stale copy.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const SRC = readFileSync(join(here, '..', 'src', 'lib', 'rbac.ts'), 'utf8')
const LAYOUT = readFileSync(join(here, '..', 'src', 'app', '(platform)', 'layout.tsx'), 'utf8')
const COMP = readFileSync(join(here, '..', 'src', 'components', 'AdminAttention.tsx'), 'utf8')
const PAGE = readFileSync(join(here, '..', 'src', 'app', '(platform)', 'admin', 'import-health', 'page.tsx'), 'utf8')

let pass = 0, fail = 0
const ok = (name, cond, extra) => { if (cond) { pass++; console.log('  ok  ', name) } else { fail++; console.log('  FAIL', name, extra ?? '') } }

// ── verbatim re-impl of the shipped predicates ──────────────────────────────────────────────────────
const isSuperAdmin = p => !!p?.modules?.admin
function canSeeAttention(perms) {
  const ov = perms?.pages?.['/admin/import-health']
  if (typeof ov === 'boolean') return ov
  if (isSuperAdmin(perms)) return true
  return (perms?.scope || 'all') === 'all'
}
// canSeeItem, reduced to what a Configuration-group item exercises (no report area, no MGMT_ONLY, no cap)
function canSeeItem(perms, item) {
  if (isSuperAdmin(perms)) return true
  if (item.scopes && !item.scopes.includes(perms.scope || 'all')) return false
  const ov = perms.pages?.[item.href]
  if (typeof ov === 'boolean') return ov
  return !!perms.modules?.[item.module]
}

// ── A. source parity: the real rbac.ts holds exactly this gate ──────────────────────────────────────
console.log('\nA. source parity with the shipped rbac.ts')
ok('A1 canSeeAttention is exported from rbac.ts', /export function canSeeAttention\(perms: Permissions\): boolean/.test(SRC))
ok('A2 …reads the /admin/import-health page override first', /const ov = perms\?\.pages\?\.\['\/admin\/import-health'\]/.test(SRC))
ok('A3 …then isSuperAdmin, then company-wide scope',
  /if \(typeof ov === 'boolean'\) return ov\s*\n\s*if \(isSuperAdmin\(perms\)\) return true\s*\n\s*return \(perms\?\.scope \|\| 'all'\) === 'all'/.test(SRC))
ok('A4 no NEW permission concept is invented (no bespoke key)',
  !/import_health_admin|attention_admin|canSeeAttentionRole/.test(SRC))

// ── B. NAV entry ────────────────────────────────────────────────────────────────────────────────────
console.log('\nB. nav entry')
const navLine = SRC.split('\n').find(l => l.includes("href: '/admin/import-health'"))
ok('B1 the Configuration group gains /admin/import-health', !!navLine, navLine)
ok('B2 …tagged module admin', /module: 'admin'/.test(navLine || ''), navLine)
ok('B3 …with NO scopes restriction (identical to its Failure Logs sibling)', !/scopes:/.test(navLine || ''), navLine)
const failuresLine = SRC.split('\n').find(l => l.includes("href: '/failures'"))
const shapeOf = l => ({ module: /module: '([a-z]+)'/.exec(l || '')?.[1], scoped: /scopes:/.test(l || '') })
ok('B4 gate shape is byte-identical to /failures',
  JSON.stringify(shapeOf(navLine)) === JSON.stringify(shapeOf(failuresLine)), [navLine, failuresLine])
const dirBlock = /export const REPORT_DIRECTORY[\s\S]*?\n\]/.exec(SRC)?.[0] || ''
ok('B5 deliberately NOT in REPORT_DIRECTORY (it edits schedules; directory excludes config surfaces)',
  dirBlock.length > 100 && !dirBlock.includes('/admin/import-health'), dirBlock.length)
// 2026-09-03 (mig 948): the Flags & Compliance group carries a tileOnly DUPLICATE of this item
// (the Reports-directory duplicate precedent — same module, no scopes, zero RBAC change), so the
// href now appears exactly twice: the Configuration original + the compliance-dashboard copy.
ok('B6 NAV carries the Configuration original + the Flags & Compliance tileOnly copy',
  (SRC.match(/href: '\/admin\/import-health'/g) || []).length === 2)

// ── C. persona matrix — the popup gate and the nav item must agree ──────────────────────────────────
console.log('\nC. persona matrix (popup gate vs nav visibility)')
const item = { href: '/admin/import-health', label: 'Import Health', icon: '📡', module: 'admin' }
const failuresItem = { href: '/failures', label: 'Failure Logs', icon: '🩺', module: 'admin' }
const personas = [
  ['super-admin / owner',        { modules: { admin: true }, scope: 'all' },                       true,  true],
  ['company-wide exec (no admin module)', { modules: { commissions: true }, scope: 'all' },        true,  false],
  ['market manager',             { modules: { commissions: true, storeops: true }, scope: 'market' }, false, false],
  ['store manager',              { modules: { closing: true }, scope: 'store' },                   false, false],
  ['rep (self)',                 { modules: { targets: true }, scope: 'self' },                    false, false],
  ['manager with explicit GRANT',{ modules: { storeops: true }, scope: 'market', pages: { '/admin/import-health': true } }, true, true],
  ['admin with explicit DENY',   { modules: { admin: true }, scope: 'all', pages: { '/admin/import-health': false } },      false, true],
]
for (const [name, perms, wantPopup, wantNav] of personas) {
  ok(`C:${name} popup=${wantPopup}`, canSeeAttention(perms) === wantPopup, canSeeAttention(perms))
  ok(`C:${name} nav=${wantNav}`, canSeeItem(perms, item) === wantNav, canSeeItem(perms, item))
}
ok('C8 a non-admin sees NOTHING (popup false for every scoped persona)',
  personas.filter(([, , w]) => !w).every(([, p]) => canSeeAttention(p) === false))
ok('C9 the new NAV item is gated exactly like /failures for every persona WITHOUT a per-page override',
  personas.filter(([, p]) => !p.pages).every(([, p]) => canSeeItem(p, item) === canSeeItem(p, failuresItem)))
// INTENTIONAL divergence from canSeeItem, documented: canSeeItem checks isSuperAdmin (= modules.admin,
// a ROLE grant) BEFORE the per-page override, so an admin role with an explicit DENY still sees the nav
// TAB. canSeeAttention checks the override FIRST, which is what the BACKEND gate does (its super_admin
// is the cross-tenant DB column, not modules.admin) — so the popup and the API agree, and an owner who
// explicitly denies a role the page also stops its popup. The tab remaining visible is pre-existing
// canSeeItem behaviour shared with /failures, not something this change introduces.
ok('C10 an explicit page DENY suppresses the POPUP even for an admin role',
  canSeeAttention({ modules: { admin: true }, scope: 'all', pages: { '/admin/import-health': false } }) === false)
ok('C11 …matching the backend gate order (override before the admin-module default)',
  canSeeAttention({ modules: { admin: true }, scope: 'all', pages: { '/admin/import-health': true } }) === true)

// ── D. wiring + the /api/v1 trap ────────────────────────────────────────────────────────────────────
console.log('\nD. wiring')
ok('D1 layout.tsx imports the component', /import AdminAttention from '@\/components\/AdminAttention'/.test(LAYOUT))
ok('D2 layout.tsx renders it once in the header', (LAYOUT.match(/<AdminAttention \/>/g) || []).length === 1)
ok('D3 layout.tsx is otherwise unchanged in structure (Guard/PlatformShell intact)',
  /function Guard\(/.test(LAYOUT) && /function PlatformShell\(/.test(LAYOUT))
const apiCalls = [...COMP.matchAll(/api\(`?([^`'")]+)/g)].map(m => m[1])
ok('D4 every component api() path carries the explicit /api/v1 prefix',
  apiCalls.length > 0 && apiCalls.every(p => p.startsWith('/api/v1/')), apiCalls)
const pageCalls = [...PAGE.matchAll(/api\(`?([^`'")]+)/g)].map(m => m[1])
ok('D5 every admin-page api() path carries the explicit /api/v1 prefix',
  pageCalls.length > 0 && pageCalls.every(p => p.startsWith('/api/v1/')), pageCalls)
ok('D6 the component gates on canSeeAttention (never a hand-rolled check)',
  /import \{ canSeeAttention \} from '@\/lib\/rbac'/.test(COMP) && /const allowed = canSeeAttention\(permissions\)/.test(COMP))
ok('D7 it renders nothing when not allowed / nothing to report',
  /if \(!allowed \|\| !data \|\| !\(data\.items \|\| \[\]\)\.length\) return null/.test(COMP))
ok('D8 it is fail-silent on error (catch → setData(null))', /catch \{\s*\n\s*setData\(null\)/.test(COMP))
ok('D9 the popup fires ONCE per login session, keyed on the ACTING org',
  /sessionStorage/.test(COMP) && /mp_attention_seen_\$\{getActiveOrg\(\) \|\| 'default'\}/.test(COMP))
ok('D10 a persistent indicator remains after dismissal',
  /needs attention/.test(COMP) && /setOpen\(true\)/.test(COMP))
ok('D11 the login call is the CHEAP one; deep=1 is behind an explicit button',
  /load\(false\)/.test(COMP) && /load\(true\)/.test(COMP) && /Run full check/.test(COMP))
ok('D12 every attention item renders its deep link button', /\{it\.deep_link_label \|\| 'Fix'\}/.test(COMP))
ok('D13 no hardcoded org_id anywhere in the new frontend files',
  !/00000000-0000-0000-0000-000000000001/.test(COMP) && !/00000000-0000-0000-0000-000000000001/.test(PAGE)
  && !/ORG_ID/.test(COMP) && !/ORG_ID/.test(PAGE))

console.log(`\n${pass}/${pass + fail} passed`)
process.exit(fail ? 1 : 0)
