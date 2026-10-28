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
const PINNED_MAX = 4 // length of proof_red_pending.txt on 2026-10-28; may only go DOWN

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
