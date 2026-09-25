#!/usr/bin/env node
// PROOF — the multi-month option is offered only when the org has it configured, and multi-month money is
// never hidden (owner 2026-09-25). The frontend half of THE predicate, _lib/multimonthOffer.ts (import-free).
// Backend half: backend/harness_multimonth_offer.py; lock: backend/harness_multimonth_offer_lock.py.
//
// Run:  node frontend/tools/multimonth-offer-proof.mjs
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import os from 'node:os'
import path from 'node:path'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const OUT = mkdtempSync(path.join(os.tmpdir(), 'mmproof-'))
writeFileSync(path.join(OUT, 'package.json'), '{"type":"module"}')
process.on('exit', () => rmSync(OUT, { recursive: true, force: true }))
let pass = 0, fail = 0
const ok = (name, cond) => { if (cond) { pass++; console.log(`  PASS  ${name}`) } else { fail++; console.log(`  FAIL  ${name}`) } }
execFileSync(path.join(FRONTEND, 'node_modules/.bin/tsc'), [
  path.join(FRONTEND, 'src/app/(platform)/commcalc/_lib/multimonthOffer.ts'), '--outDir', OUT, '--target', 'es2020',
  '--module', 'es2020', '--moduleResolution', 'node'], { stdio: 'inherit' })
const M = await import(pathToFileURL(path.join(OUT, 'multimonthOffer.js')).href)

const OFF = { configured: false, offered: false, state: 'off', money: {}, note: null }
const ON = { configured: true, offered: true, state: 'configured', money: {}, note: null }
const OFFM = { configured: false, offered: true, state: 'off_with_money', money: { 'July 2026': 42 }, note: 'x' }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b)
ok('off, $0 → no multi-month row at all (the option is not offered)', eq(M.multimonthRows(OFF, 0, 0), { sale: false, resid: false }))
ok('configured, $0 → the $0 sale-triggered drill row is offered (today\'s behaviour)', eq(M.multimonthRows(ON, 0, 0), { sale: true, resid: false }))
ok('off but $25 sale-triggered on the row → shown (money is never hidden)', eq(M.multimonthRows(OFF, 25, 0), { sale: true, resid: false }))
ok('off but $42 residual on the row → shown', eq(M.multimonthRows(OFF, 0, 42), { sale: false, resid: true }))
ok('off_with_money, $0 on THIS rep → the option is offered (the org has multi-month money)', M.multimonthRows(OFFM, 0, 0).sale === true)
ok('configured + residual only → no meaningless second $0 row', eq(M.multimonthRows(ON, 0, 42), { sale: false, resid: true }))
ok('unknown / pending / failed read → offered (never hides a live option)', M.multimonthOffered(null) && M.multimonthOffered(undefined)
  && M.multimonthOffered(M.MULTIMONTH_UNKNOWN))
ok('the pre-2026-09-25 rule is exactly the configured column', [[0, 0], [5, 0], [0, 5], [5, 5]].every(([s, r]) =>
  eq(M.multimonthRows(ON, s, r), { sale: s !== 0 || r === 0, resid: r !== 0 })))
console.log(`\n${fail === 0 ? 'ALL GREEN' : 'FAILURES'} — ${pass} passed, ${fail} failed`)
process.exitCode = fail === 0 ? 0 : 1
