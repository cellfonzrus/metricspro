#!/usr/bin/env node
// THE ONE RUNNER for frontend proof scripts, and the ratchet that keeps them honest.
//
// WHY. `tools/*-proof.mjs` are standalone Node programs that prove pure frontend logic — the
// frontend's counterpart to `backend/harness_*.py`. Until 2026-10-28 no workflow executed a single
// one of them, which is the §19.25 class the backend already named: *a proof that nothing runs is
// not a gate, it is a file*. This runner executes every one of them in CI.
//
// THE RATCHET, identical in shape to backend/harness_unrun_pending.txt:
//   · a proof NOT in proof_red_pending.txt must exit 0 — new breakage cannot land;
//   · a proof IN the list that now PASSES fails the build until its line is deleted, so the list
//     can never read as worse than reality and quietly re-admit debt someone already paid off;
//   · the list may only SHRINK: PINNED_MAX below is its length today, and a longer list fails.
//
// There is deliberately no --accept flag. A trapdoor that re-pins the register from whatever the
// tree currently says is how a ratchet becomes a rubber stamp.
import { readFileSync, readdirSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const PINNED_MAX = 0 // length of proof_red_pending.txt: 4 on 2026-10-28, 0 since 2026-11-04. May only go DOWN.
const PINNED_TRANSPILE_MAX = 6 // length of proof_transpile_pending.txt on 2026-11-04. May only go DOWN.

const pending = new Set(
  readFileSync(join(HERE, 'proof_red_pending.txt'), 'utf8')
    .split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#')),
)

if (pending.size > PINNED_MAX) {
  console.error(`FAIL  proof_red_pending.txt has ${pending.size} entries; PINNED_MAX is ${PINNED_MAX}.`)
  console.error('      The list may only shrink. A new proof must PASS, not be added here.')
  process.exit(1)
}

const proofs = readdirSync(HERE).filter(f => f.endsWith('-proof.mjs')).sort()
let failed = 0

// ── THE TRANSPILE RATCHET — a proof that transpiles app code dereferences the ONE home ──────────
// See tools/proof_transpile_pending.txt for why, and tools/_proof-transpile.mjs for what it fixes.
// Derived set vs declared set, compared BOTH WAYS (§CORE-10), so a proof that stops transpiling
// names itself here instead of leaving a stale pin that silently re-permits a hand-rolled copy.
{
  const pendingTranspile = new Set(
    readFileSync(join(HERE, 'proof_transpile_pending.txt'), 'utf8')
      .split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#')),
  )

  if (pendingTranspile.size > PINNED_TRANSPILE_MAX) {
    console.error(`FAIL  proof_transpile_pending.txt has ${pendingTranspile.size} entries; ` +
                  `PINNED_TRANSPILE_MAX is ${PINNED_TRANSPILE_MAX}. The list may only shrink.`)
    failed++
  }

  const HOME = '_proof-transpile.mjs'
  const transpiles = new Set()
  for (const proof of proofs) {
    const src = readFileSync(join(HERE, proof), 'utf8')
    // Strip line comments so the google-ratings header, which NAMES the hack it removed, is not
    // read as still carrying it. A guard must read code, not prose — the §CORE-33 lesson applied
    // to the guard that enforces §CORE-33.
    const code = src.split('\n').filter(l => !l.trim().startsWith('//')).join('\n')
    const handRolled = /\btsc\b/.test(code) && !code.includes(HOME)
    const viaHome = code.includes(HOME)
    if (handRolled || viaHome) transpiles.add(proof)
    if (handRolled && !pendingTranspile.has(proof)) {
      console.error(`FAIL  ${proof} transpiles app code with its own tsc call.`)
      console.error(`      Use tools/${HOME} — tsc leaves '@/…' aliases and extensionless relative`)
      console.error(`      imports in the JS it emits, so a hand-rolled copy breaks the day the`)
      console.error(`      module it transpiles gains an import. See index §CORE-33.`)
      failed++
    }
  }
  for (const listed of [...pendingTranspile].sort()) {
    if (!proofs.includes(listed)) {
      console.error(`FAIL  proof_transpile_pending.txt lists ${listed}, which is not a proof here.`)
      console.error(`      Delete the line and lower PINNED_TRANSPILE_MAX; a stale pin goes blind.`)
      failed++
    } else if (readFileSync(join(HERE, listed), 'utf8').includes(HOME)) {
      console.error(`FAIL  ${listed} now uses tools/${HOME} but is still on the pending list.`)
      console.error(`      Delete its line and lower PINNED_TRANSPILE_MAX.`)
      failed++
    } else if (!transpiles.has(listed)) {
      // The third direction, and the one the first draft of this ratchet MISSED: a pin for a proof
      // that does not transpile at all. It excuses nothing, so it reads as debt that is not there
      // and it keeps the ceiling one higher than reality — room for a real hand-rolled copy to
      // land later without the count ever going up. Caught on its first run: asset-export-proof.mjs
      // was pinned here and only MENTIONS `tsc` in a comment.
      console.error(`FAIL  proof_transpile_pending.txt lists ${listed}, which does not transpile ` +
                    `app code at all.`)
      console.error(`      Delete the line and lower PINNED_TRANSPILE_MAX; a pin that excuses ` +
                    `nothing hides room for a real one.`)
      failed++
    }
  }
  console.log(`transpile ratchet: ${transpiles.size} proofs transpile app code, ` +
              `${pendingTranspile.size} still hand-rolled (max ${PINNED_TRANSPILE_MAX})\n`)
}

for (const proof of proofs) {
  let ok = true
  try {
    execFileSync(process.execPath, [join(HERE, proof)], { stdio: 'pipe', cwd: dirname(HERE) })
  } catch {
    ok = false
  }
  const known = pending.has(proof)
  if (ok && !known) console.log(`PASS  ${proof}`)
  else if (!ok && known) console.log(`RED   ${proof}  (known, listed in proof_red_pending.txt)`)
  else if (!ok && !known) {
    console.error(`FAIL  ${proof}  — this proof is not on the pending list and must pass.`)
    console.error('      Run it directly for the detail:  node frontend/tools/' + proof)
    failed++
  } else {
    console.error(`FAIL  ${proof}  — listed as RED but it PASSES now.`)
    console.error('      Delete its line from tools/proof_red_pending.txt and drop PINNED_MAX in run-proofs.mjs.')
    failed++
  }
}

console.log(`\n${proofs.length} proofs, ${pending.size} known red, ${failed} unexpected.`)
if (failed) process.exit(1)
console.log('OK — every frontend proof either passes or is a declared, shrinking debt.')
