// Proof harness — the "role assigned vs login exists" access chip on /admin/roles
// (auth-ux hardening, 2026-08-03 incident: a TEST DM had a working login but no role, signed in
// fine, and hit the Guard's "Account not set up" wall; the Roles grid showed only the LOGIN half of
// the story, so the admin had no way to see the gap).
//
// Extracts the REAL `accessState()` out of
// `src/app/(platform)/admin/roles/page.tsx` (types stripped, logic verbatim) and asserts the chip
// for every combination of (role assigned) x (login state). If the anchors move, extraction throws
// and this fails loudly rather than testing a stale copy.
//
// Also asserts the ANTI-ENUMERATION invariant (platform-core-11): a freshly-created login and a
// pending cross-tenant invite must be INDISTINGUISHABLE — both read "invited" — so the roster never
// reveals that an email already exists in another tenant.
//
// Run:  node frontend/prove_role_access_state.mjs      (no network, no DB, no React)

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const PAGE = join(HERE, 'src/app/(platform)/admin/roles/page.tsx')
const SRC = readFileSync(PAGE, 'utf8')

let pass = 0, fail = 0
const ck = (label, cond) => { if (cond) { pass++; console.log(`  ok  ${label}`) } else { fail++; console.error(`  XX  ${label}`) } }
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

// ── extract the real accessState() ──────────────────────────────────────────────────────────────
const i0 = SRC.indexOf('function accessState(e: Emp): Access {')
const i1 = SRC.indexOf('\ntype Role = ')
must(i0 > 0, 'anchor not found: function accessState(e: Emp): Access {')
must(i1 > i0, 'anchor not found: type Role =')
const JS = SRC.slice(i0, i1).replace('function accessState(e: Emp): Access {', 'function accessState(e) {')
must(!/:\s*Access\b/.test(JS), 'a TypeScript annotation survived the strip — extraction is out of date')
const accessState = new Function(`${JS}\nreturn accessState`)()

const state = (o) => accessState(o).key

console.log('A. the incident state — a login with no role is called out in RED')
ck('login active, role null → login_no_role',
   state({ app_role: null, has_login: true, login_status: 'active' }) === 'login_no_role')
ck('login invited, role null → login_no_role',
   state({ app_role: null, has_login: true, login_status: 'invited' }) === 'login_no_role')
ck('blank/whitespace role counts as NO role',
   state({ app_role: '   ', has_login: true, login_status: 'active' }) === 'login_no_role')
ck('chip is loud (bold + red) for that state', (() => {
  const a = accessState({ app_role: null, has_login: true, login_status: 'active' })
  return a.label.includes('NO ROLE') && a.fg === '#991b1b'
})())
ck('its tooltip names the exact screen the user will see', (() => {
  const a = accessState({ app_role: null, has_login: true, login_status: 'active' })
  return a.title.includes('Account not set up')
})())

console.log('B. the four healthy states')
ck('role + signed in            → active',
   state({ app_role: 'district_manager', has_login: true, login_status: 'active' }) === 'active')
ck('role + login not used yet   → invited',
   state({ app_role: 'district_manager', has_login: true, login_status: 'invited' }) === 'invited')
ck('role, no login              → role_only',
   state({ app_role: 'district_manager', has_login: false, login_status: '' }) === 'role_only')
ck('no role, no login           → none',
   state({ app_role: null, has_login: false, login_status: '' }) === 'none')

console.log('C. ANTI-ENUMERATION — a fresh login and a pending cross-tenant invite look identical')
{
  // Fresh login: app_users.auth_id stamped, never signed in  → backend sends has_login=true.
  const fresh = accessState({ app_role: 'sales_rep', has_login: true, login_status: 'invited' })
  // Pending invite (email exists in ANOTHER tenant): NO auth_id in this tenant, backend still
  // reports login_status='invited' precisely so the two are indistinguishable.
  const pending = accessState({ app_role: 'sales_rep', has_login: false, login_status: 'invited' })
  ck('same key', fresh.key === pending.key)
  ck('same label', fresh.label === pending.label)
  ck('same colours', fresh.bg === pending.bg && fresh.fg === pending.fg)
  ck('same tooltip (no distinguishing text)', fresh.title === pending.title)
}

console.log('D. tolerant of an older /core/employees payload (no login_status field)')
ck('has_login only, with role → invited',
   state({ app_role: 'sales_rep', has_login: true }) === 'invited')
ck('has_login only, no role  → login_no_role',
   state({ app_role: null, has_login: true }) === 'login_no_role')
ck('neither field present    → none', state({}) === 'none')

console.log('E. every state carries a plain-English tooltip (no error codes)')
for (const s of [
  { app_role: null, has_login: true, login_status: 'active' },
  { app_role: 'x', has_login: true, login_status: 'active' },
  { app_role: 'x', has_login: true, login_status: 'invited' },
  { app_role: 'x', has_login: false, login_status: '' },
  { app_role: null, has_login: false, login_status: '' },
]) {
  const a = accessState(s)
  ck(`${a.key}: has label + tooltip`, !!a.label && a.title.length > 20)
}

console.log('F. the login-without-role WARNING BANNER keys off the same predicate (no drift)')
ck("banner filters on accessState(e).key === 'login_no_role'",
   /loginNoRole = emps\.filter\(e => accessState\(e\)\.key === 'login_no_role'\)/.test(SRC))
ck('banner names the exact screen the user sees', /Account set up|Account &ldquo;|&ldquo;Account not set up&rdquo;/.test(SRC))

console.log('G. creation-time guardrail exists on BOTH write paths')
ck('Save asks before defaulting to sales_rep',
   /async function assign\(e: Emp, opts: \{ skipRoleConfirm\?: boolean \} = \{\}\) \{\s*\n\s*if \(!opts\.skipRoleConfirm && !confirmNoRole\(e, 'save'\)\) return/.test(SRC))
ck('Create-login asks before defaulting to sales_rep',
   /if \(!confirmNoRole\(e, 'login'\)\) return/.test(SRC))
ck('Create-login does NOT double-prompt (assign is called with skipRoleConfirm)',
   /await assign\(e, \{ skipRoleConfirm: true \}\)/.test(SRC))
ck('a no-role create-login reports the applied default back to the admin',
   /No role was picked — the default "Sales Rep" role was applied/.test(SRC))
ck('the grid stops showing "none" once the server default is applied',
   /if \(!\(e\.app_role \|\| ''\)\.trim\(\)\) setEmp\(e\.id, \{ app_role: 'sales_rep' \}\)/.test(SRC))

console.log('H. no new backend read was introduced (multi-tenant surface unchanged)')
{
  const calls = [...SRC.matchAll(/api\('([^']+)'/g)].map(m => m[1])
  const known = new Set([
    '/api/v1/core/auth-config', '/api/v1/core/roles', '/api/v1/core/employees',
    // 2026-08-03 scope-split: the market/store GRANT pickers moved OFF the span-scoped
    // /api/v1/storeops/stores onto the canonical org-scoped /api/v1/core/markets (union of
    // storeops.stores.market + commcalc.store_mapping.market — the same source that RESOLVES a
    // market grant). Still org-scoped, still no cross-tenant read.
    '/api/v1/core/markets', '/api/v1/core/setting-areas', '/api/v1/core/users/assign',
    '/api/v1/core/employee-widgets', '/api/v1/core/employees/purge', '/api/v1/core/users/create-login',
    '/api/v1/core/users/resend-invite', '/api/v1/core/users/reveal-code',
    '/api/v1/core/users/bulk-provision', '/api/v1/core/users/bulk-assign',
  ])
  const unknown = calls.filter(c => !known.has(c))
  ck(`no unexpected endpoint (${calls.length} literal api() calls)`, unknown.length === 0,
     unknown)
  ck('the span-scoped store list is no longer the grant-picker source',
     !SRC.includes("api('/api/v1/storeops/stores')"))
  // Template-literal api() calls (path built at runtime) — enumerated separately so a new one can
  // never slip past the literal scan above.
  const tpl = [...SRC.matchAll(/api\(`([^`]+)`/g)].map(m => m[1].replace(/\$\{[^}]*\}/g, '{}'))
  const knownTpl = new Set([
    '/api/v1/core/roles/{}', '/api/v1/storeops/employees/{}',
    '/api/v1/core/scope-preview?email={}',
  ])
  ck(`no unexpected templated endpoint (${tpl.length} calls)`,
     tpl.every(t => knownTpl.has(t)), tpl.filter(t => !knownTpl.has(t)))
  if (unknown.length) console.error('   unexpected:', unknown)
}

console.log(`\n${fail === 0 ? 'PASS' : 'FAIL'}: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
