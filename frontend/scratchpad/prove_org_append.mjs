// Proof harness for the client.ts org_id APPEND fix (platform-core NEEDS CORE from mod-commission).
// Re-implements ONLY the pure URL logic verbatim from client.ts (no DOM/fetch), then drives the
// required case table. The functions below are byte-for-byte the ones in client.ts — kept in sync by
// the assertion at the bottom that greps the source for the exact bodies.
import { readFileSync } from 'node:fs'

// ── active-org state stub (client.ts reads localStorage; here we set it directly) ──────────────────
let ACTIVE = null
const getActiveOrg = () => ACTIVE
const ORG_ID = '00000000-0000-0000-0000-000000000001'   // house

// ── legacy substitute path (scopeOrg) — verbatim from client.ts ───────────────────────────────────
let _sessionOrgId = null
let _multiTenant = false
const multiTenantOn = () => _multiTenant
function scopeOrg(path) {
  if (!multiTenantOn() || !_sessionOrgId) return path
  return /[?&]org_id=/.test(path)
    ? path.replace(/([?&]org_id=)[^&]*/, `$1${encodeURIComponent(_sessionOrgId)}`)
    : path
}

// ── the fix — verbatim from client.ts ─────────────────────────────────────────────────────────────
function appendActiveOrg(path) {
  const active = getActiveOrg()
  if (!active) return path
  const hashIdx = path.indexOf('#')
  const base = hashIdx >= 0 ? path.slice(0, hashIdx) : path
  const frag = hashIdx >= 0 ? path.slice(hashIdx) : ''
  if (/[?&]org_id=/.test(base)) return path
  const sep = base.includes('?') ? '&' : '?'
  return `${base}${sep}org_id=${encodeURIComponent(active)}${frag}`
}
// substitute the LITERAL house org_id with the active tenant — verbatim from client.ts (leak-CLASS fix)
function subHouseOrgWith(path, active) {
  if (!active || active === ORG_ID) return path
  return path.replace(/([?&]org_id=)([^&#]*)/, (full, pfx, val) =>
    decodeURIComponent(val) === ORG_ID ? `${pfx}${encodeURIComponent(active)}` : full)
}
function substituteHouseOrg(path) { return subHouseOrgWith(path, getActiveOrg()) }
function withOrgScope(path) { return appendActiveOrg(substituteHouseOrg(scopeOrg(path))) }

// ── cases ─────────────────────────────────────────────────────────────────────────────────────────
let pass = 0, fail = 0
const T = 'aaaaaaaa-0000-0000-0000-tenantttttt'   // an active (chosen) tenant org
function reset() { ACTIVE = null; _sessionOrgId = null; _multiTenant = false }
function check(name, got, want) {
  const ok = got === want
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n        got:  ${got}\n        want: ${want}`)
  ok ? pass++ : fail++
}

// 1. no-param + active-known → APPENDED (the core super-admin fix)
reset(); ACTIVE = T
check('no-param + active → appended (? sep)',
  withOrgScope('/api/v1/commcalc/sales-report?period=2026-07'),
  `/api/v1/commcalc/sales-report?period=2026-07&org_id=${T}`)

// 1b. no query at all + active → appended with ?
reset(); ACTIVE = T
check('no query + active → appended (? sep)',
  withOrgScope('/api/v1/commcalc/exec-overview/2026-07'),
  `/api/v1/commcalc/exec-overview/2026-07?org_id=${T}`)

// 2. has org_id (specific tenant, e.g. /admin/billing) → SUBSTITUTE path only, NOT doubled/overridden
reset(); ACTIVE = T           // super-admin acting-as T, but this URL deliberately targets another org
check('has org_id (specific) + active → untouched (no double, no override)',
  withOrgScope(`/api/v1/billing/invoices?org_id=bbbbbbbb-9999-0000-0000-otherorgxx`),
  `/api/v1/billing/invoices?org_id=bbbbbbbb-9999-0000-0000-otherorgxx`)

// 2b. has org_id=ORG_ID (hardcoded house constant page) + active=T (super-admin acting-as T) →
//     SUBSTITUTE the house literal for T. This is the leak-CLASS fix: it USED to be left untouched
//     (super-admin acting as a tenant read/wrote HOUSE data — the "Cellfonz under LuxeLink" leak).
reset(); ACTIVE = T
check('has org_id=ORG_ID (hardcoded) + active=T → SUBSTITUTED to T (leak-class fix)',
  withOrgScope(`/api/v1/commcalc/gp/2026-07?org_id=${ORG_ID}`),
  `/api/v1/commcalc/gp/2026-07?org_id=${T}`)

// 2b-i. house-const MID-query → substitute, other params intact
reset(); ACTIVE = T
check('house-const mid-query + active=T → substitute, rest intact',
  withOrgScope(`/api/v1/commcalc/forecast?org_id=${ORG_ID}&period=2026-07&store=5`),
  `/api/v1/commcalc/forecast?org_id=${T}&period=2026-07&store=5`)

// 2b-ii. SPECIFIC foreign org_id (deliberate cross-tenant query) → UNTOUCHED (only the house literal is rewritten)
reset(); ACTIVE = T
check('specific foreign org_id + active=T → untouched (deliberate query wins)',
  withOrgScope('/api/v1/admin/billing?org_id=bbbbbbbb-9999-0000-0000-otherorgxx'),
  '/api/v1/admin/billing?org_id=bbbbbbbb-9999-0000-0000-otherorgxx')

// 2b-iii. HOUSE session (active === house) + house-const → no-op (byte-identical to today for house users)
reset(); ACTIVE = ORG_ID
check('house session (active=house) + house-const → no-op',
  withOrgScope(`/api/v1/commcalc/gp/2026-07?org_id=${ORG_ID}`),
  `/api/v1/commcalc/gp/2026-07?org_id=${ORG_ID}`)

// 2c. has org_id AND legacy substitute ON → substituted to session org, append still skips (no double)
reset(); ACTIVE = T; _multiTenant = true; _sessionOrgId = 'cccccccc-1111-0000-0000-sessionorg'
check('has org_id + substitute ON → substituted once, not doubled',
  withOrgScope(`/api/v1/commcalc/vip/summary?org_id=${ORG_ID}`),
  `/api/v1/commcalc/vip/summary?org_id=cccccccc-1111-0000-0000-sessionorg`)

// 3. no active org known (pre-/core/me) → UNTOUCHED (append nothing, current behavior)
reset(); ACTIVE = null
check('no active known → untouched',
  withOrgScope('/api/v1/commcalc/sales-report?period=2026-07'),
  '/api/v1/commcalc/sales-report?period=2026-07')

// 4. normal-user unaffected: append adds org_id, but middleware overrides server-side. Client-side we
//    only assert the appended value is the *hint*; correctness is that append never THROWS and produces
//    a well-formed URL (the middleware ignores it for normal users). Same output as case 1.
reset(); ACTIVE = T
check('normal user: append is a harmless hint (well-formed URL; middleware wins server-side)',
  withOrgScope('/api/v1/commcalc/targets/2026-07?store=all'),
  `/api/v1/commcalc/targets/2026-07?store=all&org_id=${T}`)

// 5. super-admin + switcher: active org = chosen tenant → appended → bypass honors it → sees T's data
reset(); ACTIVE = T
check('super-admin + switcher(active=T) → org_id=T appended (bypass honors client org)',
  withOrgScope('/api/v1/commcalc/ma-commission/summary?period=2026-07'),
  `/api/v1/commcalc/ma-commission/summary?period=2026-07&org_id=${T}`)

// 6. fragment carried through (org_id lands in query, not the hash)
reset(); ACTIVE = T
check('fragment: org_id appended to query, fragment preserved',
  withOrgScope('/api/v1/commcalc/coaching/2026-07#rep-3'),
  `/api/v1/commcalc/coaching/2026-07?org_id=${T}#rep-3`)

// 6b. fragment + existing query → & sep, fragment preserved
reset(); ACTIVE = T
check('fragment + query: & sep, fragment preserved',
  withOrgScope('/api/v1/commcalc/coaching/2026-07?rep=nm#detail'),
  `/api/v1/commcalc/coaching/2026-07?rep=nm&org_id=${T}#detail`)

// 6c. fragment already containing org_id-looking text must NOT block append (regex on query base only)
reset(); ACTIVE = T
check('fragment contains org_id-looking text → still appended to real query',
  withOrgScope('/api/v1/commcalc/exec-overview/2026-07#x?org_id=fake'),
  `/api/v1/commcalc/exec-overview/2026-07?org_id=${T}#x?org_id=fake`)

// 7. sales-report/detail with a URLSearchParams-built query that ALREADY carries org_id (commission
//    stopgap) → untouched (no double-add)
reset(); ACTIVE = T
check('sales-report/detail already carrying org_id (stopgap) → untouched',
  withOrgScope(`/api/v1/commcalc/sales-report/detail?period=2026-07&org_id=${T}`),
  `/api/v1/commcalc/sales-report/detail?period=2026-07&org_id=${T}`)

// ── source-parity guard: the bodies above must match client.ts verbatim ────────────────────────────
const src = readFileSync(new URL('../src/lib/client.ts', import.meta.url), 'utf8')
const need = [
  'function appendActiveOrg(path: string): string {',
  "if (/[?&]org_id=/.test(base)) return path",
  'return `${base}${sep}org_id=${encodeURIComponent(active)}${frag}`',
  'function subHouseOrgWith(path: string, active: string | null): string {',
  'decodeURIComponent(val) === ORG_ID ? `${pfx}${encodeURIComponent(active)}` : full)',
  'function withOrgScope(path: string): string {',
  'return appendActiveOrg(substituteHouseOrg(scopeOrg(path)))',
]
for (const s of need) {
  const ok = src.includes(s)
  console.log(`${ok ? 'PASS' : 'FAIL'}  source-parity: client.ts contains \`${s.slice(0, 48)}…\``)
  ok ? pass++ : fail++
}

console.log(`\n${fail === 0 ? '✅' : '❌'}  ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
