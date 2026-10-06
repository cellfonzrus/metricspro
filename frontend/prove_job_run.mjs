// Proof harness — a background job is running until the SERVER says it is not, and the control says
// so (owner report 2026-10-06, index §6q).
//
// THE REPORTED DEFECT, replayed as §A. The owner pressed ⚡ Run Calculation for August 2026 and
// reported: *"it does not show it is runnig it shows run calculation again, then when you press it
// again it says it is already runnig - fix it"*. The run itself was fine — 02:27:40 to 02:28:36, 56
// seconds. Three faults, all in the surface:
//
//   1. the button had no `disabled` and a fixed label, so a run in flight looked identical to none;
//   2. the page read the status ONCE, 2s after the press, against a 56s job, and never again;
//   3. the second press was refused with the single-flight guard's 409 — the surface blaming the
//      person for a gap the surface left.
//
// The CLASS behind all three: the UI treated "the request was accepted" as "the job has finished".
// §A asserts each fault is now impossible, and §A5 is the ARMED control proving §A passes because
// the rules work rather than because the button is disabled unconditionally.
//
// No stub and no copy: Node strips the erasable TypeScript of the REAL src/lib/job-run.ts (which
// imports nothing) and drives its exported functions.
//
//   A. the reported defect — the regression, with the armed control
//   B. isRunning / isError / isDone — one vocabulary, every server word, and what is NOT one
//   C. settleStatus — a status read that predates the press may not downgrade it
//   D. nextPollDelay / POLL_SCHEDULE — follows a 56s job to its end, and is BOUNDED
//   E. buttonState — the badge and the button can never disagree
//   F. purity, determinism, RULE TWO (no tenant/carrier/product name anywhere in the home)
//   G. every sibling surface by name — wired, or excused in writing
//
// Run:  node frontend/prove_job_run.mjs      (no network, no DB, no React, no npm install)
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { readFileSync } from 'node:fs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = dirname(HERE)
const J = await import(pathToFileURL(join(HERE, 'src/lib/job-run.ts')).href)

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}
const section = t => console.log(`\n${t}`)
const read = rel => readFileSync(join(ROOT, rel), 'utf8')
// Comments stripped, STRING LITERALS KEPT — a check for code shape must not be fooled by a comment
// that quotes the shape it forbids (this file's own siblings quote the retired `finally` line), and
// a check for a string literal must not be blanked into vacuity. §F6 proves this is not vacuous.
const codeOnly = rel => read(rel)
  .split('\n')
  .filter(l => !/^\s*(\/\/|\*|\/\*)/.test(l))
  .map(l => l.replace(/\s\/\/.*$/, ''))
  .join('\n')

const IDLE = '⚡ Run Calculation'
// The reported run, to the second: pressed 02:27:40, server finished 02:28:36.
const T_PRESS = Date.parse('2026-10-06T02:27:40.420Z')
const T_DONE = Date.parse('2026-10-06T02:28:36.078Z')

// ── A. the reported defect ────────────────────────────────────────────────────
section('A. the reported defect — August 2026, pressed 02:27:40, finished 02:28:36')

{
  // FAULT 1 — the button must SHOW the run and refuse a press while it is in flight.
  const mid = J.buttonState({ status: 'running', idleLabel: IDLE, elapsedMs: 10_000 })
  ck('A1 a run in flight disables the button', mid.disabled === true, mid)
  ck('A2 a run in flight changes the label away from the idle wording', mid.label !== IDLE, mid)
  ck('A3 a run in flight says so in the badge', /running/i.test(mid.badge), mid)

  // FAULT 2 — the page must still be following the job at 56 seconds. The old code asked once at 2s.
  const elapsedAtFinish = T_DONE - T_PRESS
  ck('A4 the 56s run is still being followed when it finishes',
    J.nextPollDelay(elapsedAtFinish) !== null, { elapsedAtFinish, delay: J.nextPollDelay(elapsedAtFinish) })
  const asked = J.POLL_SCHEDULE.filter(at => at <= elapsedAtFinish)
  ck('A4b the page asks more than once before the run ends', asked.length > 1, asked)

  // ARMED CONTROL — §A1–A3 must pass because a RUNNING job disables the button, not because the
  // button is disabled unconditionally. An idle job must offer the press, with the idle wording.
  const idle = J.buttonState({ status: 'not_run', idleLabel: IDLE })
  ck('A5 ARMED: an idle job offers the press with the idle label',
    idle.disabled === false && idle.label === IDLE, idle)
  const done = J.buttonState({ status: 'done', idleLabel: IDLE })
  ck('A5b ARMED: a finished job offers the press again', done.disabled === false && done.label === IDLE, done)

  // FAULT 3 — the race that let the mid-run page look idle. A status read issued before the press
  // landed reports the PREVIOUS run as 'done'; it must not overwrite the press.
  const stale = J.settleStatus('done', { pressedAt: T_PRESS, readAt: T_PRESS - 500 })
  ck('A6 a read taken BEFORE the press cannot downgrade it', stale === 'running', stale)
  const sameMs = J.settleStatus('done', { pressedAt: T_PRESS, readAt: T_PRESS })
  ck('A6b a read in the same millisecond as the press cannot downgrade it', sameMs === 'running', sameMs)
  const settled = J.settleStatus('done', { pressedAt: T_PRESS, readAt: T_DONE })
  ck('A7 ARMED: a read AFTER the run finished does settle it to done', settled === 'done', settled)
  // And the whole point: once settled, the control offers the press again.
  ck('A8 the settled state re-enables the button',
    J.buttonState({ status: settled, idleLabel: IDLE }).disabled === false)
}

// ── B. one vocabulary ─────────────────────────────────────────────────────────
section('B. isRunning / isError / isDone — one vocabulary for the server words')

for (const w of ['running', 'queued', 'started', 'pending', 'busy', 'in_progress']) {
  ck(`B1 '${w}' means a run is in flight`, J.isRunning(w) === true)
}
for (const w of ['done', 'complete', 'completed', 'ok', 'calculated']) {
  ck(`B2 '${w}' means finished and successful`, J.isDone(w) === true && J.isRunning(w) === false)
}
for (const w of ['error', 'failed', 'refused']) {
  ck(`B3 '${w}' means finished and failed`, J.isError(w) === true && J.isRunning(w) === false)
}
ck('B4 case and surrounding space do not change the answer',
  J.isRunning('  RUNNING ') === true && J.isDone('Done') === true)
ck('B5 nothing known yet is not a run in flight',
  [null, undefined, '', '   '].every(v => !J.isRunning(v) && !J.isDone(v) && !J.isError(v)))
ck('B6 ARMED: an unknown word is none of the three',
  !J.isRunning('not_run') && !J.isDone('not_run') && !J.isError('not_run'))
ck('B7 ARMED: a word that merely CONTAINS a known one is not a match',
  !J.isRunning('rerunning') && !J.isDone('undone'))
ck('B8 the three sets are disjoint', (() => {
  const all = [...J.RUNNING_STATES, ...J.DONE_STATES, ...J.ERROR_STATES]
  return new Set(all).size === all.length
})(), { RUNNING: J.RUNNING_STATES, DONE: J.DONE_STATES, ERROR: J.ERROR_STATES })

// ── C. settleStatus ───────────────────────────────────────────────────────────
section('C. settleStatus — the press is evidence, and a read must be later to contradict it')

ck('C1 with no press of ours, the read is simply the answer',
  J.settleStatus('done', { pressedAt: null, readAt: 5 }) === 'done')
ck('C2 with no press and nothing stored, the answer is not_run',
  J.settleStatus(null, {}) === 'not_run')
ck('C3 a read that AGREES a run is in flight is always accepted',
  J.settleStatus('running', { pressedAt: 1000, readAt: 500 }) === 'running')
ck('C4 a later read reporting an error does settle to error',
  J.settleStatus('error', { pressedAt: 1000, readAt: 2000 }) === 'error')
ck('C5 a later read of nothing settles to not_run',
  J.settleStatus('', { pressedAt: 1000, readAt: 2000 }) === 'not_run')
ck('C6 a missing readAt is treated as NOT later than the press (the safe direction)',
  J.settleStatus('done', { pressedAt: 1000, readAt: null }) === 'running')
ck('C7 ARMED: C6 is the rule, not a refusal to ever settle — a timestamped later read settles',
  J.settleStatus('done', { pressedAt: 1000, readAt: 1001 }) === 'done')

// ── D. the poll schedule ──────────────────────────────────────────────────────
section('D. nextPollDelay — quick at first, backing off, and BOUNDED')

ck('D1 the first read is soon after the press', J.nextPollDelay(0) <= 3000, J.nextPollDelay(0))
ck('D2 the delay returned is always the time until the NEXT scheduled offset',
  J.nextPollDelay(3000) === J.POLL_SCHEDULE.find(a => a > 3000) - 3000)
ck('D3 the schedule is strictly increasing',
  J.POLL_SCHEDULE.every((v, i) => i === 0 || v > J.POLL_SCHEDULE[i - 1]), J.POLL_SCHEDULE)
ck('D4 the gaps widen — it backs off rather than hammering', (() => {
  const gaps = J.POLL_SCHEDULE.map((v, i) => i === 0 ? v : v - J.POLL_SCHEDULE[i - 1])
  return gaps.every((g, i) => i === 0 || g >= gaps[i - 1])
})())
ck('D5 it STOPS — a run followed past the give-up point returns null',
  J.nextPollDelay(J.POLL_GIVE_UP_MS + 1) === null)
ck('D6 isOverdue agrees with the schedule exactly',
  J.isOverdue(J.POLL_GIVE_UP_MS + 1) === true && J.isOverdue(J.POLL_GIVE_UP_MS - 1) === false)
ck('D7 a nonsense elapsed does not throw and does not stop the poll',
  J.nextPollDelay(NaN) !== null && J.nextPollDelay(-5) !== null)
ck('D8 ARMED: the give-up point is long enough for the reported 56s run',
  J.POLL_GIVE_UP_MS > (T_DONE - T_PRESS), { giveUp: J.POLL_GIVE_UP_MS, run: T_DONE - T_PRESS })

// ── E. buttonState ────────────────────────────────────────────────────────────
section('E. buttonState — the badge and the button cannot disagree')

{
  const sub = J.buttonState({ status: null, idleLabel: IDLE, submitting: true })
  ck('E1 while the POST is in flight the control is already disabled', sub.disabled === true, sub)
  ck('E2 submitting outranks a stored status', J.buttonState(
    { status: 'done', idleLabel: IDLE, submitting: true }).disabled === true)

  const over = J.buttonState({ status: 'running', idleLabel: IDLE, elapsedMs: J.POLL_GIVE_UP_MS + 1 })
  ck('E3 an overdue run stays DISABLED — we stopped watching, we did not learn it died',
    over.disabled === true && over.phase === 'overdue', over)
  ck('E4 an overdue run says so rather than claiming progress', /longer than expected/i.test(over.badge), over)

  const err = J.buttonState({ status: 'error', idleLabel: IDLE })
  ck('E5 a failed run is offerable again and says it failed',
    err.disabled === false && err.phase === 'error' && /failed/i.test(err.badge), err)

  // THE DISAGREEMENT THAT WAS REPORTED: a disabled control must never carry the idle call to action,
  // and an enabled one must never claim to be running.
  const every = ['running', 'queued', 'busy', 'done', 'ok', 'error', 'failed', 'not_run', '', null]
  ck('E6 no disabled state carries the idle label', every.every(st => {
    const b = J.buttonState({ status: st, idleLabel: IDLE })
    return !b.disabled || b.label !== IDLE
  }))
  ck('E7 no enabled state claims a run is in flight', every.every(st => {
    const b = J.buttonState({ status: st, idleLabel: IDLE })
    return b.disabled || !/running/i.test(b.badge)
  }))
  ck('E8 disabled is exactly isRunning-or-submitting', every.every(st =>
    J.buttonState({ status: st, idleLabel: IDLE }).disabled === J.isRunning(st)))
  ck('E9 the phase and the vocabulary agree for every word', every.every(st => {
    const p = J.buttonState({ status: st, idleLabel: IDLE }).phase
    if (J.isRunning(st)) return p === 'running'
    if (J.isError(st)) return p === 'error'
    if (J.isDone(st)) return p === 'done'
    return p === 'idle'
  }))
  ck('E10 the surface supplies its own idle wording and gets it back',
    J.buttonState({ status: 'done', idleLabel: '▶ Run now' }).label === '▶ Run now')
}

// ── F. purity, determinism, RULE TWO ─────────────────────────────────────────
section('F. purity, determinism and RULE TWO')

{
  const home = read('frontend/src/lib/job-run.ts')
  const code = codeOnly('frontend/src/lib/job-run.ts')
  ck('F1 the home imports nothing at runtime', !/^\s*import\s/m.test(code), code.match(/^\s*import\s.*$/m))
  ck('F2 the home does no I/O', !/\bfetch\s*\(|XMLHttpRequest|localStorage|document\.|window\./.test(code))
  ck('F3 the home names no endpoint', !/\/api\/v1/.test(code))
  // RULE TWO — no carrier, tenant or product name anywhere, so this cannot grow a per-tenant branch.
  for (const w of ['boost', 'luxelink', 'cellfonz', 'vzone', 'total wireless', 'acima', 'epay', 'shopify']) {
    ck(`F4 RULE TWO: the home never names '${w}'`, !new RegExp(w, 'i').test(home))
  }
  ck('F5 the home holds no org id', !/0{8}-0{4}-0{4}-0{4}-0{11}1/.test(home))
  // COUNTER-ARMING for codeOnly: it must still SEE code, and must still see string literals.
  ck('F6 ARMED: codeOnly is not vacuous — it still sees code and string literals',
    /export function isRunning/.test(code) && /'running'/.test(code), code.length)

  // Determinism and non-mutation.
  const a = J.buttonState({ status: 'running', idleLabel: IDLE, elapsedMs: 1000 })
  const b = J.buttonState({ status: 'running', idleLabel: IDLE, elapsedMs: 1000 })
  ck('F7 the same input gives the same answer', JSON.stringify(a) === JSON.stringify(b))
  const before = [...J.RUNNING_STATES]
  J.buttonState({ status: 'running', idleLabel: IDLE })
  J.nextPollDelay(10)
  J.settleStatus('done', { pressedAt: 1, readAt: 2 })
  ck('F8 nothing mutates the shared vocabulary',
    JSON.stringify(before) === JSON.stringify([...J.RUNNING_STATES]))
}

// ── G. every sibling surface, by name ────────────────────────────────────────
section('G. the siblings — wired to the one home, or excused in writing')

const HOME_IMPORT = /from '@\/lib\/job-run'/
const SURFACES = [
  ['frontend/src/app/(platform)/commcalc/page.tsx', 'the reported surface — ⚡ Run Calculation'],
  ['frontend/src/app/(platform)/closing/imports/page.tsx', 'the daily-closing sweep'],
  ['frontend/src/app/(platform)/commcalc/connectors/page.tsx', 'the connectors sweep'],
  ['frontend/src/app/(platform)/commcalc/_lib/AutoCalcNotice.tsx', 'the auto-calc notice'],
]
for (const [rel, what] of SURFACES) {
  ck(`G1 ${what} dereferences the one home`, HOME_IMPORT.test(codeOnly(rel)), rel)
}

// THE RETIRED SHAPES. Each of these is how a surface used to treat "accepted" as "finished"; if one
// comes back, the patchwork is restored. Checked on comments-stripped source so the files' own
// explanations of the retired code do not read as the code.
{
  const dash = codeOnly('frontend/src/app/(platform)/commcalc/page.tsx')
  ck('G2 the dashboard no longer asks once, two seconds after the press',
    !/setTimeout\(\s*loadData\s*,\s*2000\s*\)/.test(dash))
  ck('G3 the dashboard button carries a disabled binding', /<button[\s\S]{0,200}?disabled=\{/.test(dash))
  ck('G4 the dashboard renders the shared label, not a hardcoded idle one',
    /\{btn\.label\}/.test(dash))
  ck('G5 the dashboard no longer spells the status words itself',
    !/calcStatus === '(done|running|error|not_run)'/.test(dash), dash.match(/calcStatus === '\w+'/g))

  const closing = codeOnly('frontend/src/app/(platform)/closing/imports/page.tsx')
  ck('G6 the closing sweep no longer clears busy in a finally around the POST',
    !/run-now[\s\S]{0,400}?finally\s*\{\s*setBusy\(false\)\s*\}/.test(closing))
  ck('G7 the closing sweep no longer reads its status once, four seconds later',
    !/setTimeout\(\s*load\s*,\s*4000\s*\)/.test(closing))

  const conn = codeOnly('frontend/src/app/(platform)/commcalc/connectors/page.tsx')
  ck('G8 the connectors sweep no longer reads its status once, four seconds later',
    !/setTimeout\(\s*load\s*,\s*4000\s*\)/.test(conn))
  // EXCUSED IN WRITING, not silently: only the daily-closing sweep declares 'running' today, so this
  // page can follow a run it started but not yet one started elsewhere. The excuse must stay stated.
  ck('G9 the connectors partial fix is excused in writing, naming what remains',
    /PARTIAL BY DESIGN/.test(read('frontend/src/app/(platform)/commcalc/connectors/page.tsx')))

  const notice = codeOnly('frontend/src/app/(platform)/commcalc/_lib/AutoCalcNotice.tsx')
  ck('G10 the auto-calc notice no longer holds a private copy of the running words',
    !/new Set\(\['queued'/.test(notice), notice.match(/new Set\(\[[^\]]*\]\)/g))
}

// THE ONE CROSS-LANGUAGE COPY. A browser cannot import Python, so the backend holds the same set of
// words. This is the check that makes that copy safe rather than a future divergence.
{
  const py = read('backend/app/modules/commcalc/router.py')
  const m = py.match(/^CALC_RUNNING_STATES\s*=\s*\(([^)]*)\)/m)
  ck('G11 the backend declares its running words where the lock can read them', !!m)
  if (m) {
    const words = [...m[1].matchAll(/'([^']+)'/g)].map(x => x[1])
    ck('G12 the backend and frontend running vocabularies are IDENTICAL',
      JSON.stringify([...words].sort()) === JSON.stringify([...J.RUNNING_STATES].sort()),
      { backend: words, frontend: J.RUNNING_STATES })
  }
  ck('G13 the backend status read is no longer an unordered limit(1) over the spellings',
    !/calc_status'\)\.select\('\*'\)[\s\S]{0,200}?_pvariants\(period\)\)\.limit\(1\)/.test(py))
  ck('G14 the backend has ONE named rule for which status row is the month\'s status',
    /def _calc_status_pick\(/.test(py))
}

console.log(`\n${pass} ok, ${fail} FAIL  (${pass + fail} checks)`)
process.exit(fail === 0 ? 0 : 1)
