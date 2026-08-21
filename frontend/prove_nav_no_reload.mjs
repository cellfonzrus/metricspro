// Proof harness — the app must NOT "reload" (full-screen "Loading…" splash + a full /core/bootstrap
// round trip) on a browser tab-focus / visibilitychange. Built 2026-08-21 for the owner's
// "every time the tab is changed the app reloads — it should not do that".
//
// ROOT CAUSE (fixed): supabase-js (auth-js GoTrueClient `_recoverAndRefresh`) emits a fresh
// `SIGNED_IN` event on EVERY `visibilitychange`, i.e. every time the browser tab regains focus. The
// AuthProvider's onAuthStateChange handler used to do `if (event !== 'TOKEN_REFRESHED') setLoading(true)`
// — so every tab focus re-armed the loading splash and re-ran the profile bootstrap. The fix routes
// the decision through the PURE `authEventNeedsReload(event, uid, settledUid)`, which returns false
// for a same-identity re-fire.
//
// This harness does NOT re-implement the decision. It extracts and transpiles the REAL
// `authEventNeedsReload` from `src/lib/auth-context.tsx` with the project's own TypeScript compiler
// and executes it. If the function is renamed/removed, the harness fails loudly.
//
// Run:  node frontend/prove_nav_no_reload.mjs      (no network, no DB, no browser, no React)

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

// ── Extract the REAL function source and transpile it in isolation ────────────────────────────────
const ts = require_('typescript')
const SRC_PATH = join(HERE, 'src/lib/auth-context.tsx')
const SRC = readFileSync(SRC_PATH, 'utf8')
const ANCHOR = 'export function authEventNeedsReload('
const start = SRC.indexOf(ANCHOR)
must(start >= 0, `anchor "${ANCHOR}" not found in auth-context.tsx — did the function move/rename?`)
// Grab from the anchor to the end of its body: find the matching close of the first "{ ... }" block.
const braceStart = SRC.indexOf('{', SRC.indexOf('):', start))
let depth = 0, end = -1
for (let i = braceStart; i < SRC.length; i++) {
  if (SRC[i] === '{') depth++
  else if (SRC[i] === '}') { depth--; if (depth === 0) { end = i + 1; break } }
}
must(end > braceStart, 'could not locate the end of authEventNeedsReload')
const fnSrc = SRC.slice(start, end)
const js = ts.transpileModule(fnSrc, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText

const dir = mkdtempSync(join(tmpdir(), 'navproof-'))
const modPath = join(dir, 'fn.mjs')
writeFileSync(modPath, js, 'utf8')
const { authEventNeedsReload } = await import(pathToFileURL(modPath).href)
must(typeof authEventNeedsReload === 'function', 'authEventNeedsReload did not export a function')

const UID = 'user-abc-123'
const OTHER = 'user-def-456'

console.log('\nA. tab-focus re-fires must NOT reload (the bug)')
// After the profile is loaded for UID, supabase re-fires SIGNED_IN / INITIAL_SESSION on every
// visibilitychange with the SAME session → must be a no-op (no splash, no bootstrap).
ck('SIGNED_IN re-fire, same identity → no reload', authEventNeedsReload('SIGNED_IN', UID, UID) === false)
ck('INITIAL_SESSION re-fire, same identity → no reload', authEventNeedsReload('INITIAL_SESSION', UID, UID) === false)
ck('TOKEN_REFRESHED (hourly auto-refresh) → no reload', authEventNeedsReload('TOKEN_REFRESHED', UID, UID) === false)
ck('TOKEN_REFRESHED never reloads even if id looks different', authEventNeedsReload('TOKEN_REFRESHED', OTHER, UID) === false)

console.log('\nB. genuine transitions MUST reload (no regression on the login race)')
ck('first load ever (settledUid undefined) → reload', authEventNeedsReload('INITIAL_SESSION', UID, undefined) === true)
ck('first SIGNED_IN ever (settledUid undefined) → reload', authEventNeedsReload('SIGNED_IN', UID, undefined) === true)
ck('real login: identity changes → reload', authEventNeedsReload('SIGNED_IN', OTHER, UID) === true)
ck('tenant switch to a session with a new user → reload', authEventNeedsReload('SIGNED_IN', OTHER, UID) === true)
ck('sign-out: identity → null → reload (resets profile)', authEventNeedsReload('SIGNED_OUT', null, UID) === true)
ck('USER_UPDATED → reload (rare metadata change)', authEventNeedsReload('USER_UPDATED', UID, UID) === true)
ck('sign back in as same user after sign-out → reload', authEventNeedsReload('SIGNED_IN', UID, null) === true)

console.log('\nC. edge: undefined-settled with null session (pre-auth INITIAL_SESSION, signed out)')
// Before anything loads, an INITIAL_SESSION with no session (null uid) is the first event: it must
// run once so `loading` is released (settle → setLoading(false)); returning true is correct.
ck('first INITIAL_SESSION with null session → reload (releases splash)', authEventNeedsReload('INITIAL_SESSION', null, undefined) === true)
// Once settled as signed-out (null), a repeat INITIAL_SESSION/SIGNED_IN with null must NOT reload.
ck('repeat null-session INITIAL_SESSION after settle → no reload', authEventNeedsReload('INITIAL_SESSION', null, null) === false)

console.log(`\n${fail === 0 ? 'PASS' : 'FAIL'} — ${pass} ok, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
