// Proof harness — the shared client-side SWR cache (`src/lib/cache-core.ts`), built 2026-08-04 for
// the owner's "it takes some time to load the screen when moving from one menu to the other".
//
// This harness does NOT re-implement the cache. It transpiles the REAL `src/lib/cache-core.ts` with
// the project's own TypeScript compiler, swaps ONLY the `./client` import for a controllable stub,
// and executes the genuine engine. If the source stops compiling or the import anchor moves, the
// harness fails loudly instead of testing a stale copy.
//
// SECTIONS
//   A. keying                — path + identity namespacing, scope material
//   B. CROSS-TENANT SAFETY   — the critical one: a cached entry can NEVER be served to another org
//   C. cross-USER safety     — same org, different login (span-scoped endpoints differ per caller)
//   D. no identity           — degrades to a plain uncached api() passthrough
//   E. SWR semantics         — fresh / stale+revalidate / expired
//   F. de-duplication        — concurrent callers share ONE request
//   G. errors                — failures are never cached; good data survives a blip
//   H. purge                 — tenant switch / sign-out empties the store and discards in-flight
//   I. invalidation          — invalidateApiCache() by substring and by predicate
//   J. wiring                — auth-context publishes the identity; the hook uses the real engine
//
// Run:  node frontend/prove_api_cache.mjs      (no network, no DB, no browser)

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'

const HERE = dirname(fileURLToPath(import.meta.url))
const require_ = createRequire(import.meta.url)

let pass = 0, fail = 0
const ck = (label, cond) => { if (cond) { pass++; console.log(`  ok  ${label}`) } else { fail++; console.error(`  XX  ${label}`) } }
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

// ── Build: transpile the REAL source with the REAL compiler ──────────────────────────────────────
const ts = require_('typescript')
const SRC_PATH = join(HERE, 'src/lib/cache-core.ts')
const SRC = readFileSync(SRC_PATH, 'utf8')
const IMPORT_ANCHOR = "import { api } from './client'"
must(SRC.includes(IMPORT_ANCHOR), `import anchor not found in cache-core.ts: ${IMPORT_ANCHOR}`)

const TMP = mkdtempSync(join(tmpdir(), 'mp-cache-proof-'))
writeFileSync(join(TMP, 'client.mjs'), `
export const calls = []
export let impl = async (p) => ({ path: p })
export function setImpl(f) { impl = f }
export async function api(path) { calls.push(path); return impl(path) }
`)
const out = ts.transpileModule(SRC.replace(IMPORT_ANCHOR, "import { api } from './client.mjs'"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  reportDiagnostics: true,
})
must(!(out.diagnostics || []).length, 'cache-core.ts failed to transpile: ' +
  (out.diagnostics || []).map(d => ts.flattenDiagnosticMessageText(d.messageText, ' ')).join('; '))
writeFileSync(join(TMP, 'cache-core.mjs'), out.outputText)

const C = await import(pathToFileURL(join(TMP, 'cache-core.mjs')).href)
const stub = await import(pathToFileURL(join(TMP, 'client.mjs')).href)
const reset = () => { stub.calls.length = 0; stub.setImpl(async (p) => ({ path: p })) }
const sleep = (ms) => new Promise(r => setTimeout(r, ms))

// ── A. keying ────────────────────────────────────────────────────────────────────────────────────
console.log('\nA. keying / namespacing')
C.setCacheIdentity(null, null); reset()
ck('A1 no identity ⇒ cacheReady() false', C.cacheReady() === false)
ck('A2 no identity ⇒ cacheNamespace() null', C.cacheNamespace() === null)
C.setCacheIdentity('u1', 'orgA')
ck('A3 identity set ⇒ cacheReady() true', C.cacheReady() === true)
ck('A4 namespace is "<user>::<org>"', C.cacheNamespace() === 'u1::orgA')
await C.apiCached('/api/v1/storeops/stores')
ck('A5 key = "<user>::<org>::<path>"', C._keys()[0] === 'u1::orgA::/api/v1/storeops/stores')
await C.apiCached('/api/v1/storeops/stores', { scope: 'week-2026-08-03' })
ck('A6 scope material widens the key, does not collide',
   C._keys().includes('u1::orgA::week-2026-08-03::/api/v1/storeops/stores') && C._size() === 2)
ck('A7 two different paths ⇒ two entries', (await C.apiCached('/api/v1/storeops/employees'), C._size() === 3))

// ── B. CROSS-TENANT SAFETY (the privacy invariant) ───────────────────────────────────────────────
console.log('\nB. CROSS-TENANT: a cached entry can never be served to another org')
const HOUSE = '00000000-0000-0000-0000-000000000001'
const LUX = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
const ROSTER = '/api/v1/storeops/employees'
reset()
stub.setImpl(async () => ({ tenant: 'HOUSE', rows: ['house-rep-1', 'house-rep-2'] }))
C.setCacheIdentity('u1', HOUSE)
const h1 = await C.apiCached(ROSTER)
const h2 = await C.apiCached(ROSTER)                       // served from cache
ck('B1 house: 2nd read is a cache HIT (1 network call)', stub.calls.length === 1)
ck('B2 house data correct', h1.tenant === 'HOUSE' && h2.tenant === 'HOUSE')

stub.setImpl(async () => ({ tenant: 'LUX', rows: ['lux-rep-1'] }))
C.setCacheIdentity('u1', LUX)                              // SAME login, switches tenant
const l1 = await C.apiCached(ROSTER)
ck('B3 lux read did NOT hit the house entry (network was called again)', stub.calls.length === 2)
ck('B4 lux got LUX data, not HOUSE data', l1.tenant === 'LUX' && !JSON.stringify(l1).includes('house-rep'))
ck('B5 switching tenant PURGED the house entry entirely',
   C._keys().every(k => !k.includes(HOUSE)))
ck('B6 every live key is namespaced by the ACTING org', C._keys().every(k => k.startsWith(`u1::${LUX}::`)))

// exhaustive: no path can produce the same key under two orgs
const PATHS = ['/api/v1/storeops/employees', '/api/v1/storeops/stores', '/api/v1/closing/stores',
               '/api/v1/commcalc/carriers', '/api/v1/core/roles', '/api/v1/asset/filter-options',
               '/api/v1/core/tenant-settings', `/api/v1/x?org_id=${HOUSE}`, `/api/v1/x?org_id=${LUX}`]
const keysUnder = async (org) => {
  C.setCacheIdentity('u1', org); reset()
  for (const p of PATHS) await C.apiCached(p)
  return C._keys().slice().sort()
}
const kh = await keysUnder(HOUSE), kl = await keysUnder(LUX)
ck('B7 key sets for the two orgs are DISJOINT (no key appears under both)',
   kh.length === PATHS.length && kl.length === PATHS.length && kh.every(k => !kl.includes(k)))

// the adversarial case: same login, same path, tenant switched back and forth
reset()
let n = 0
stub.setImpl(async () => ({ n: ++n }))
C.setCacheIdentity('u1', HOUSE); const a = await C.apiCached(ROSTER)
C.setCacheIdentity('u1', LUX);   const b = await C.apiCached(ROSTER)
C.setCacheIdentity('u1', HOUSE); const c = await C.apiCached(ROSTER)
ck('B8 switching back re-fetches (purged, not resurrected): 3 distinct responses',
   a.n === 1 && b.n === 2 && c.n === 3 && stub.calls.length === 3)

// ── C. cross-USER safety (same tenant, different login) ──────────────────────────────────────────
console.log('\nC. cross-USER: span-scoped payloads never leak between two logins of one tenant')
reset(); n = 0
stub.setImpl(async () => ({ forUser: C.cacheNamespace() }))
C.setCacheIdentity('dm-alice', HOUSE); const ua = await C.apiCached(ROSTER)
C.setCacheIdentity('rep-bob', HOUSE);  const ub = await C.apiCached(ROSTER)
ck('C1 second user did not read the first user\'s entry', stub.calls.length === 2)
ck('C2 payloads are per-user', ua.forUser === `dm-alice::${HOUSE}` && ub.forUser === `rep-bob::${HOUSE}`)
ck('C3 the first user\'s entry was purged on identity change', C._keys().every(k => k.startsWith('rep-bob::')))

// ── D. no identity ⇒ passthrough ────────────────────────────────────────────────────────────────
console.log('\nD. no identity ⇒ plain uncached api()')
reset()
C.setCacheIdentity(null, null)
await C.apiCached(ROSTER); await C.apiCached(ROSTER); await C.apiCached(ROSTER)
ck('D1 every call went to the network', stub.calls.length === 3)
ck('D2 nothing was stored (no anonymous bucket a later identity could inherit)', C._size() === 0)
C.setCacheIdentity('u1', HOUSE)
ck('D3 establishing an identity does not resurrect anything', C._size() === 0)
C.setCacheIdentity(null, null)
ck('D4 partial identity (user, no org) is still "not ready"',
   (C.setCacheIdentity('u1', null), C.cacheReady() === false))
await C.apiCached(ROSTER)
ck('D5 partial identity ⇒ still nothing stored', C._size() === 0)

// ── E. SWR semantics ─────────────────────────────────────────────────────────────────────────────
console.log('\nE. stale-while-revalidate')
reset(); n = 0
stub.setImpl(async () => ({ n: ++n }))
C.setCacheIdentity('u1', HOUSE)
const P = '/api/v1/storeops/stores'
const r1 = await C.apiCached(P, { ttlMs: 50, maxMs: 5000 })
ck('E1 miss ⇒ network', r1.n === 1 && stub.calls.length === 1)
const r2 = await C.apiCached(P, { ttlMs: 50, maxMs: 5000 })
ck('E2 FRESH ⇒ served from memory, no network', r2.n === 1 && stub.calls.length === 1)
await sleep(70)
const t0 = Date.now()
const r3 = await C.apiCached(P, { ttlMs: 50, maxMs: 5000 })
const dt = Date.now() - t0
ck('E3 STALE ⇒ returns the OLD value IMMEDIATELY (this is what makes a menu hop instant)',
   r3.n === 1 && dt < 15)
await sleep(30)
ck('E4 STALE ⇒ a background revalidation was fired', stub.calls.length === 2)
const r4 = await C.apiCached(P, { ttlMs: 50, maxMs: 5000 })
ck('E5 the background refresh replaced the entry', r4.n === 2)
await sleep(60)
const r5 = await C.apiCached(P, { ttlMs: 10, maxMs: 40 })
ck('E6 past maxAge ⇒ the caller WAITS for fresh data (no unbounded staleness)', r5.n === 3)
ck('E7 force:true bypasses a fresh entry', (await C.apiCached(P, { ttlMs: 60000, force: true })).n === 4)

// ── F. de-duplication ────────────────────────────────────────────────────────────────────────────
console.log('\nF. concurrent de-duplication')
reset(); n = 0
C.setCacheIdentity('u1', HOUSE)
stub.setImpl(async () => { await sleep(30); return { n: ++n } })
const many = await Promise.all(Array.from({ length: 8 }, () => C.apiCached('/api/v1/core/roles')))
ck('F1 8 concurrent callers ⇒ exactly ONE network request', stub.calls.length === 1)
ck('F2 all 8 got the same payload', many.every(m => m.n === 1))

// ── G. errors are never cached ───────────────────────────────────────────────────────────────────
console.log('\nG. error handling')
reset(); n = 0
C.setCacheIdentity('u1', HOUSE); C.clearApiCache()   // F left a live entry under this identity
stub.setImpl(async () => { throw new Error('boom') })
let threw = false
try { await C.apiCached('/api/v1/core/roles', { ttlMs: 1000 }) } catch { threw = true }
ck('G1 a failing call still throws to the caller (contract unchanged)', threw)
ck('G2 the failure was NOT cached', C._size() === 0)
stub.setImpl(async () => ({ n: ++n }))
ck('G3 the next call succeeds normally', (await C.apiCached('/api/v1/core/roles', { ttlMs: 1000 })).n === 1)
// a blip while a good value is held must not blank the page
stub.setImpl(async () => { throw new Error('blip') })
await sleep(5)
const held = await C.apiCached('/api/v1/core/roles', { ttlMs: 1000 })
ck('G4 a background blip leaves the good value in place', held.n === 1)

// ── H. purge ─────────────────────────────────────────────────────────────────────────────────────
console.log('\nH. purge on sign-out / in-flight discard')
reset(); n = 0
C.setCacheIdentity('u1', HOUSE)
stub.setImpl(async () => ({ n: ++n }))
await C.apiCached('/api/v1/core/roles'); await C.apiCached('/api/v1/storeops/stores')
ck('H1 two entries held', C._size() === 2)
C.setCacheIdentity(null, null)
ck('H2 sign-out purges everything', C._size() === 0)
C.setCacheIdentity('u1', HOUSE)
await C.apiCached('/api/v1/core/roles')
C.clearApiCache()
ck('H3 clearApiCache() empties the store', C._size() === 0)
// in-flight started under identity X must never land under identity Y
reset(); n = 0
C.setCacheIdentity('u1', HOUSE)
stub.setImpl(async () => { await sleep(40); return { tenant: 'HOUSE', n: ++n } })
const pending = C.apiCached('/api/v1/storeops/employees')
C.setCacheIdentity('u1', LUX)                              // switch WHILE the house call is in flight
await pending.catch(() => {})
await sleep(10)
ck('H4 an in-flight response from the OLD identity is discarded, never stored under the new one',
   C._keys().every(k => !k.includes(HOUSE)) && !C._keys().some(k => k.startsWith(`u1::${LUX}::/api/v1/storeops/employees`) && false))
stub.setImpl(async () => ({ tenant: 'LUX', n: ++n }))
const afterSwitch = await C.apiCached('/api/v1/storeops/employees')
ck('H5 the post-switch read gets LUX data (not the in-flight HOUSE payload)', afterSwitch.tenant === 'LUX')

// ── I2. cacheAs (bypass fetch, canonical cache entry) ────────────────────────────────────────────
console.log('\nI0. cacheAs: a "?fresh=1" re-check refreshes the SAME entry the normal reads use')
reset(); n = 0
C.setCacheIdentity('u1', HOUSE); C.clearApiCache()
stub.setImpl(async (p) => ({ n: ++n, from: p }))
const CANON = '/api/v1/core/attention'
await C.apiCached(CANON, { ttlMs: 60000 })
ck('I0a one entry under the canonical path', C._size() === 1 && C._keys()[0].endsWith(CANON))
const rechecked = await C.apiCached(`${CANON}?fresh=1`, { force: true, cacheAs: CANON, ttlMs: 60000 })
ck('I0b the bypass URL was the one actually fetched', rechecked.from === `${CANON}?fresh=1`)
ck('I0c it did NOT create a second entry', C._size() === 1 && C._keys()[0].endsWith(CANON))
const afterRecheck = await C.apiCached(CANON, { ttlMs: 60000 })
ck('I0d the next ordinary read serves the POST-FIX payload, not the pre-fix one',
   afterRecheck.n === 2 && stub.calls.length === 2)

// ── I. invalidation ──────────────────────────────────────────────────────────────────────────────
console.log('\nI. invalidateApiCache')
reset(); n = 0
C.setCacheIdentity('u1', HOUSE); C.clearApiCache()
stub.setImpl(async () => ({ n: ++n }))
await C.apiCached('/api/v1/storeops/employees')
await C.apiCached('/api/v1/storeops/stores')
await C.apiCached('/api/v1/core/roles')
ck('I1 three entries held', C._size() === 3)
C.invalidateApiCache('/api/v1/storeops/')
ck('I2 substring invalidation drops the matching entries only',
   C._size() === 1 && C._keys()[0].endsWith('/api/v1/core/roles'))
C.invalidateApiCache((p) => p.includes('roles'))
ck('I3 predicate invalidation works', C._size() === 0)

// ── J. wiring ────────────────────────────────────────────────────────────────────────────────────
console.log('\nJ. wiring')
const AUTH = readFileSync(join(HERE, 'src/lib/auth-context.tsx'), 'utf8')
ck('J1 auth-context imports setCacheIdentity', /import \{ setCacheIdentity \} from '\.\/cache'/.test(AUTH))
ck('J2 auth-context publishes (user, acting org) — server-resolved, not localStorage',
   /setCacheIdentity\(user \? \(user\.auth_id \|\| user\.id \|\| null\) : null,\s*\n\s*user \? \(activeOrg \|\| user\.org_id \|\| null\) : null\)/.test(AUTH))
ck('J3 the identity effect depends on BOTH user and activeOrg (so a tenant switch re-namespaces)',
   /setCacheIdentity\([\s\S]{0,200}?\}, \[user, activeOrg\]\)/.test(AUTH))
const HOOK = readFileSync(join(HERE, 'src/lib/cache.ts'), 'utf8')
ck('J4 the React binding re-exports the real engine', /export \* from '\.\/cache-core'/.test(HOOK))
ck('J5 the hook renders from the real engine\'s peek (no re-implementation)',
   /_peek\(path, ttlMs, maxMs, scope\)/.test(HOOK) && /_subscribe\(path, scope,/.test(HOOK))
ck('J6 the engine has NO react import (so this harness runs the shipped code)',
   !/from ['"]react['"]/.test(SRC))
const SRC_NOCOMMENT = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
ck('J7 nothing is persisted to localStorage/sessionStorage/IndexedDB (memory only)',
   !/localStorage|sessionStorage|indexedDB/i.test(SRC_NOCOMMENT))
ck('J7b the store really is an in-memory Map', /const store = new Map<string, Entry>\(\)/.test(SRC))
ck('J8 client.ts api() is untouched by this package',
   readFileSync(join(HERE, 'src/lib/client.ts'), 'utf8').includes('export async function api(path: string, opts: RequestInit = {})'))

console.log(`\n${pass}/${pass + fail} checks passed`)
process.exit(fail ? 1 : 0)
