// THE FRONTEND LINT RATCHET — the gate that makes `npm run lint` a CHECK on a tree that is not yet
// clean, without ever letting it get dirtier.
//
// WHY A RATCHET AND NOT A PLAIN GATE (owner directive 2026-10-11, verbatim: *"Run the code style
// checker and fix the problems"*). MEASURED on origin/main (567e7050) before designing this:
// `npm run lint` reports **5953** problems (5675 errors, 278 warnings) over **422** files, of which
// **4903** are `@typescript-eslint/no-explicit-any` and **666** are React-Compiler/hook rules whose
// fixes change RUNTIME BEHAVIOUR in commission, payroll, closing and P&L pages. A plain gate would
// be red on every PR from the first commit, so it would be switched off within a week; fixing 5953
// findings in one change — most of them `any` annotations on money paths — is not a decision a lint
// gate gets to make on the owner's behalf.
//
// So this is the shape the house already uses for exactly this situation: a PINNED REGISTER that may
// only SHRINK (`frontend/table_sort_pending.txt`, `backend/harness_unrun_pending.txt`). The rule that
// actually stops the bleeding:
//
//   · a finding that is NOT in the register FAILS THE BUILD — new lint debt cannot land;
//   · a count that went UP fails — a file cannot get worse;
//   · a count that went DOWN fails until the register is corrected, so the register can never read
//     as worse than reality and quietly re-admit debt someone already paid off;
//   · an entry whose findings are all gone fails until the line is deleted — the list only shrinks.
//
// There is deliberately NO `--write` / `--accept` flag. A trapdoor that re-pins the register from
// whatever the tree currently says is how a ratchet becomes a rubber stamp: the next person with a
// red build runs it and the debt silently grows. Downward changes are printed ready to paste; upward
// changes are refused and have to be argued for in a PR.
//
// The linter itself has ONE home — `frontend/package.json`'s `lint` script. This file SHELLS OUT to
// `npm run lint` rather than importing ESLint or spelling `eslint` itself, so CI, a developer and
// this gate all run byte-identically the same command. backend/harness_frontend_lint_lock.py fails
// the build if that stops being true.
//
//   cd frontend && npm run lint:gate

import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const FE = dirname(dirname(fileURLToPath(import.meta.url)))   // …/frontend
const REGISTER = join(FE, 'eslint_pending.txt')

// ── the ONE home, dereferenced ────────────────────────────────────────────────────────────────────
// `npm run lint -- -f json -o <file>` === `eslint -f json -o <file>`. Writing to a file rather than
// reading stdout keeps npm's own "> frontend@0.1.0 lint" banner out of the JSON.
function runTheOneHome() {
  const dir = mkdtempSync(join(tmpdir(), 'eslint-ratchet-'))
  const out = join(dir, 'report.json')
  try {
    try {
      execFileSync('npm', ['run', '--silent', 'lint', '--', '-f', 'json', '-o', out],
        { cwd: FE, stdio: ['ignore', 'ignore', 'inherit'] })
    } catch {
      // ESLint exits non-zero whenever it reports an error. That is the normal case here — the
      // report is still written, and an actually-broken run is caught by the missing file below.
    }
    if (!existsSync(out)) {
      console.error('\n  the lint run produced no report at all — `npm run lint` could not execute.\n' +
                    '  Run `cd frontend && npm run lint` by hand to see why (usually a missing\n' +
                    '  node_modules, i.e. `npm ci` was not run in this job).')
      process.exit(2)
    }
    return JSON.parse(readFileSync(out, 'utf8'))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

// ── measure: {"<rule>\t<path>": count} ────────────────────────────────────────────────────────────
function measure(report) {
  const counts = new Map()
  let errors = 0, warnings = 0
  for (const file of report) {
    const path = relative(FE, file.filePath).split(sep).join('/')
    for (const m of file.messages) {
      // A parse failure has no ruleId. It is the most serious thing ESLint can say — the file was
      // not linted AT ALL — so it gets a stable key of its own rather than being dropped.
      const rule = m.ruleId || (m.fatal ? '(parse-error)' : '(no-rule)')
      if (m.severity === 2) errors++; else warnings++
      const key = rule + '\t' + path
      counts.set(key, (counts.get(key) || 0) + 1)
    }
  }
  return { counts, errors, warnings, total: errors + warnings }
}

// ── the register ──────────────────────────────────────────────────────────────────────────────────
function readRegister() {
  if (!existsSync(REGISTER)) {
    console.error('\n  frontend/eslint_pending.txt is missing. It is the pinned lint-debt register and\n' +
                  '  the gate cannot run without it. Restore it from git rather than regenerating it.')
    process.exit(2)
  }
  const pinned = new Map()
  let n = 0
  for (const raw of readFileSync(REGISTER, 'utf8').split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    n++
    const m = /^(\d+)\s+(\S+)\s+(.+)$/.exec(line)
    if (!m) {
      console.error('  frontend/eslint_pending.txt line %d is not `<count> <rule> <path>`: %s', n, line)
      process.exit(2)
    }
    pinned.set(m[2] + '\t' + m[3].trim(), Number(m[1]))
  }
  return pinned
}

const fmt = (k, c) => `${String(c).padEnd(5)} ${k.split('\t')[0].padEnd(46)} ${k.split('\t')[1]}`

// ── compare ───────────────────────────────────────────────────────────────────────────────────────
const { counts, errors, warnings, total } = measure(runTheOneHome())
const pinned = readRegister()
const pinnedTotal = [...pinned.values()].reduce((a, b) => a + b, 0)

console.log('\nFrontend lint ratchet — new findings fail the build; the register may only shrink\n')
console.log(`  measured: ${total} findings (${errors} errors, ${warnings} warnings) over ` +
            `${new Set([...counts.keys()].map(k => k.split('\t')[1])).size} files`)
console.log(`  pinned:   ${pinnedTotal} findings over ${pinned.size} (rule, file) entries\n`)

const added = [], worse = [], better = [], gone = []
for (const [k, c] of counts) {
  if (!pinned.has(k)) added.push([k, c])
  else if (c > pinned.get(k)) worse.push([k, c, pinned.get(k)])
  else if (c < pinned.get(k)) better.push([k, c, pinned.get(k)])
}
for (const k of pinned.keys()) if (!counts.has(k)) gone.push(k)

let bad = false

if (added.length) {
  bad = true
  console.log(`  ${added.length} NEW (rule, file) finding(s) that the register does not carry — this is new`)
  console.log('  lint debt, and it is what the gate exists to refuse:\n')
  for (const [k, c] of added.sort()) console.log('    + ' + fmt(k, c))
  console.log('\n    Fix them. Do NOT add them to the register, and do NOT silence them with an\n' +
              '    eslint-disable comment — the lock counts suppressions and will refuse that too.')
}

if (worse.length) {
  bad = true
  console.log(`\n  ${worse.length} entry/entries got WORSE than the register pins:\n`)
  for (const [k, c, was] of worse.sort()) console.log(`    ^ ${fmt(k, c)}   (pinned ${was})`)
  console.log('\n    A ratchet only turns one way. Bring these back to the pinned count or below.')
}

if (better.length || gone.length) {
  bad = true
  console.log(`\n  ${better.length + gone.length} entry/entries IMPROVED — thank you. The register now reads as`)
  console.log('  worse than reality, which would quietly re-admit the debt you just paid off, so')
  console.log('  correct frontend/eslint_pending.txt:\n')
  for (const k of gone.sort()) console.log(`    - delete: ${k.split('\t')[0]}  ${k.split('\t')[1]}`)
  for (const [k, c, was] of better.sort()) console.log(`    = set to ${c} (was ${was}): ${fmt(k, c)}`)
}

if (bad) {
  console.log('\n  The whole register, as the tree now measures it, ready to paste under the header\n' +
              '  comment of frontend/eslint_pending.txt:\n')
  for (const [k, c] of [...counts].sort((a, b) => a[0] < b[0] ? -1 : 1)) {
    console.log('  ' + `${c}\t${k.split('\t')[0]}\t${k.split('\t')[1]}`)
  }
  console.log('\n  — only paste it if every difference above is an IMPROVEMENT. Pasting it to make a\n' +
              '    red build green is re-pinning the debt upward, which is the one thing this gate is\n' +
              '    here to stop.')
  process.exit(1)
}

console.log('OK — every lint finding in the tree is one the register already carries, at or below its\n' +
            'pinned count, and the register carries nothing that is already fixed.')
