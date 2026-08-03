// Proof harness — dead client session detection in `src/lib/client.ts` (auth-ux hardening,
// 2026-08-03 live incident: a stale/invalid Supabase session made EVERY module call return the
// tenant middleware's 401 {"detail":"authentication required"}; the user saw "all modules broken"
// instead of "sign in again").
//
// This harness does NOT re-implement the rule — it EXTRACTS the real source of the constants and of
// `markSessionInvalid()` out of `src/lib/client.ts`, strips only the TypeScript type annotations,
// and evaluates the genuine code. If the anchors ever move, extraction throws and the proof fails
// loudly rather than silently testing a stale copy.
//
// It also proves the ERROR CONTRACT is untouched: with the new block and the two added call lines
// removed, client.ts is BYTE-IDENTICAL to origin/main — so the `throw new Error(errMsg(...))` that
// 210 files consume cannot have changed for any status.
//
// Run:  node frontend/prove_dead_session_detect.mjs      (no network, no DB, no browser)

import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const CLIENT_TS = join(HERE, 'src/lib/client.ts')
const SRC = readFileSync(CLIENT_TS, 'utf8')

let pass = 0, fail = 0
function ck(label, cond) {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}`) }
}
function must(cond, msg) { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

// ── 1. Extract the REAL logic out of client.ts ──────────────────────────────────────────────────
const START = '// ── DEAD CLIENT SESSION detection'
const END = '\n// Render a FastAPI error body as a readable string.'
const i0 = SRC.indexOf(START), i1 = SRC.indexOf(END)
must(i0 > 0, `anchor not found in client.ts: ${START}`)
must(i1 > i0, `anchor not found in client.ts: ${END.trim()}`)
const BLOCK = SRC.slice(i0, i1)

// Strip ONLY what node cannot parse: `export ` keywords and the explicit type annotations on the
// four declarations in this block. Everything else (regexes, ordering, the latch) is verbatim.
const JS = BLOCK
  .replace(/\bexport function /g, 'function ')
  .replace(/let _sessionInvalid = false/, 'let _sessionInvalid = false')
  .replace(/const _sessionInvalidCbs = new Set<\(\) => void>\(\)/, 'const _sessionInvalidCbs = new Set()')
  .replace(/function isSessionInvalid\(\): boolean/, 'function isSessionInvalid()')
  .replace(/function onSessionInvalid\(cb: \(\) => void\): \(\) => void/, 'function onSessionInvalid(cb)')
  .replace(/function markSessionInvalid\(path: string, detail: unknown, hadToken: boolean\)/,
           'function markSessionInvalid(path, detail, hadToken)')
must(!/:\s*(string|boolean|unknown)\b/.test(JS.replace(/\/\/.*$/gm, '')),
     'a TypeScript annotation survived the strip — extraction is out of date')

// A tiny window shim so PUBLIC_ROUTE_RE can be exercised against a browser pathname.
// `pathname === null` models server-side rendering (no `window` at all).
function build(pathname) {
  const factory = new Function('window', `
    ${JS}
    return { markSessionInvalid, onSessionInvalid, clearSessionInvalid, isSessionInvalid,
             AUTH_FLOW_PATH_RE, PUBLIC_ROUTE_RE, DEAD_SESSION_DETAIL }
  `)
  return factory(pathname === null ? undefined : { location: { pathname } })
}

// Fire markSessionInvalid once on a fresh module instance and report whether it latched.
function fires({ path = '/api/v1/commcalc/summary', detail = 'authentication required',
                 hadToken = true, page = '/commcalc' } = {}) {
  const M = build(page)
  let notified = 0
  M.onSessionInvalid(() => { notified++ })
  M.markSessionInvalid(path, detail, hadToken)
  return { notified, latched: M.isSessionInvalid() }
}

console.log('A. the incident case — a module call 401s while the client believes it has a session')
{
  const r = fires()
  ck('fires and latches', r.notified === 1 && r.latched === true)
}

console.log('B. must NOT fire when the client had no session (open mode / anonymous kiosk)')
ck('no bearer attached → silent', fires({ hadToken: false }).notified === 0)

console.log('C. exact-match on the middleware string only')
for (const [detail, want] of [
  ['authentication required', true],
  ['Authentication Required', true],              // case/whitespace tolerated (same string)
  ['  authentication required  ', true],
  ['two-factor authentication required', false],  // the 2FA challenge is a DIFFERENT state
  ['authentication required for this store', false],
  ['Not authorized', false],
  ['', false],
]) ck(`detail ${JSON.stringify(detail)} → fires=${want}`, (fires({ detail }).notified === 1) === want)
ck('non-string detail (422 array / object) → silent',
   fires({ detail: [{ msg: 'x' }] }).notified === 0 && fires({ detail: { msg: 'x' } }).notified === 0)

console.log('D. NO redirect storms — the auth/bootstrap endpoints never trigger it')
for (const p of ['/api/v1/core/bootstrap', '/api/v1/core/me', '/api/v1/core/me/2fa/start',
                 '/api/v1/core/auth-config', '/api/v1/core/auth/forgot-password',
                 '/api/v1/core/my-tenants', '/api/v1/core/pending-connections',
                 '/api/v1/core/connect-tenant', '/api/v1/core/disable-and-switch',
                 '/api/v1/core/signup', '/api/v1/core/password-policy/public'])
  ck(`${p} → silent`, fires({ path: p }).notified === 0)
ck('a genuine module path still fires', fires({ path: '/api/v1/storeops/employees' }).notified === 1)
ck('/api/v1/core/members-style path is NOT swallowed by the /me rule',
   fires({ path: '/api/v1/core/members' }).notified === 1)

console.log('E. public / token-authenticated browser routes are never bounced')
for (const page of ['/login', '/signup', '/onboard/abc123', '/portal', '/privacy'])
  ck(`page ${page} → silent`, fires({ page }).notified === 0)
for (const page of ['/commcalc', '/admin/roles', '/account/password'])
  ck(`page ${page} → fires`, fires({ page }).notified === 1)
ck('server-side render (no window) → silent', fires({ page: null }).notified === 0)

console.log('F. single-shot latch — 30 concurrent failures notify ONCE')
{
  const M = build('/commcalc')
  let n = 0
  M.onSessionInvalid(() => { n++ })
  for (let i = 0; i < 30; i++) M.markSessionInvalid(`/api/v1/mod/${i}`, 'authentication required', true)
  ck('one notification for 30 failures', n === 1)
  // A component mounting AFTER the fact still learns about it (fire-on-subscribe).
  let late = 0
  M.onSessionInvalid(() => { late++ })
  ck('late subscriber is told immediately', late === 1)
  // ...and clearing (sign-out) re-arms it for the next session.
  M.clearSessionInvalid()
  ck('cleared → not invalid', M.isSessionInvalid() === false)
  let after = 0
  M.onSessionInvalid(() => { after++ })
  ck('post-clear subscriber is NOT spuriously fired', after === 0)
  M.markSessionInvalid('/api/v1/mod/x', 'authentication required', true)
  ck('re-arms after clear', after === 1)
}

console.log('G. a throwing listener can never break the failing request')
{
  const M = build('/commcalc')
  M.onSessionInvalid(() => { throw new Error('boom') })
  let ok = true
  try { M.markSessionInvalid('/api/v1/mod/x', 'authentication required', true) } catch { ok = false }
  ck('markSessionInvalid swallowed the listener throw', ok && M.isSessionInvalid() === true)
}

console.log('H. ERROR CONTRACT unchanged — client.ts minus the new code is byte-identical to main')
{
  let MAIN = null
  try {
    MAIN = execFileSync('git', ['show', 'origin/main:frontend/src/lib/client.ts'],
                        { cwd: join(HERE, '..'), encoding: 'utf8' })
  } catch { console.log('  --  skipped (origin/main not available in this checkout)') }
  if (MAIN !== null) {
    const ADDED_API = "    // Detect-only: never changes what is thrown (see DEAD CLIENT SESSION block above).\n" +
      "    if (res.status === 401) markSessionInvalid(path, (err as any)?.detail, !!authHeader.Authorization)\n"
    const ADDED_UPLOAD =
      "    if (res.status === 401) markSessionInvalid(path, (err as any)?.detail, !!authHeader.Authorization)\n"
    let stripped = SRC.slice(0, i0) + SRC.slice(i1 + 1)   // remove the whole new block (+ its newline)
    must(stripped.includes(ADDED_API), 'api() call-site line not found verbatim')
    stripped = stripped.replace(ADDED_API, '')
    must(stripped.includes(ADDED_UPLOAD), 'apiUpload() call-site line not found verbatim')
    stripped = stripped.replace(ADDED_UPLOAD, '')
    ck('client.ts with the additions removed === origin/main byte-for-byte', stripped === MAIN)
  }
}

console.log(`\n${fail === 0 ? 'PASS' : 'FAIL'}: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
