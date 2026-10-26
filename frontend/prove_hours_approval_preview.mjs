#!/usr/bin/env node
// PROOF: the Hours Approval save confirmation (src/lib/hours-approval-preview.ts).
//
// Runs the REAL module — transpiled, not re-implemented — so a change to the arithmetic that the
// dialog shows, or to the request rows it builds, fails HERE rather than in front of a reviewer
// approving a fortnight of payroll.
//
// WHAT IT PINS, and why each one is a defect somebody actually hit:
//   A  a typed correction OUTRANKS the computed figure, exactly as the server resolves it
//   B  a typed adjustment moves the figure when NO correction is stored, and is masked when one is
//   C  an emptied box over a stored correction is a CLEAR, and the clear is a visible move
//   D  a box retyped with the number already in it produces NO line (never confirm a no-op write)
//   E  the reason gates are stated BEFORE the request, matching the server's own refusals
//   F  multi-row totals are the sum of the lines — the "what the new payroll will be" figure
//   G  previewToRows() sends exactly what the dialog showed, and nothing it did not
//
// Run: node prove_hours_approval_preview.mjs       (from frontend/)
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// ── transpile the real TS with the repo's own tsc (no bundler, no re-implementation) ────────────
// Runs in the CI job that installs the frontend's dependencies, for the same reason the P&L
// drill-path and Account-hub expenses proofs live there: this executes the REAL module, and a
// hand-rolled type stripper would be a second, quietly diverging copy of the thing under proof.
import { execFileSync } from 'node:child_process'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { dirname } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const out = mkdtempSync(join(tmpdir(), 'prove-hap-'))
writeFileSync(join(out, 'preview.ts'), readFileSync(join(HERE, 'src/lib/hours-approval-preview.ts'), 'utf8'))
execFileSync(join(HERE, 'node_modules/.bin/tsc'),
  [join(out, 'preview.ts'), '--target', 'es2020', '--module', 'es2020', '--outDir', out, '--strict', '--skipLibCheck'],
  { stdio: 'inherit', cwd: out })
const { buildPreview, previewToRows, payableNow, payableAfter, round2 } =
  await import(pathToFileURL(join(out, 'preview.js')).href)

let pass = 0
const fails = []
function eq(label, got, want) {
  const g = JSON.stringify(got)
  const w = JSON.stringify(want)
  if (g === w) { pass++; return }
  fails.push(`${label}\n    got  ${g}\n    want ${w}`)
}

// A row shaped like the board's, with the figures from the live case this work came out of:
// E043 / 2026-10-05..18 — 102.84 computed hours, a -10.97 stored adjustment, $17.00/h.
const E043 = { employee_id: 'E043', name: 'Simarjyot Singh', store: 'B-1750',
               hours_source: 102.84, hours_approved: null, adjustment_hours: -10.97, pay_rate: 17 }
const PEER = { employee_id: 'E100', name: 'Second Person', store: 'B-103',
               hours_source: 80, hours_approved: null, adjustment_hours: 0, pay_rate: 20 }

// ── A. a typed correction outranks the computed figure ───────────────────────────────────────────
eq('A1 payableNow = source + adjustment while no correction is stored', payableNow(E043), 91.87)
eq('A2 payableNow = the correction once one is stored',
   payableNow({ ...E043, hours_approved: 86.84 }), 86.84)
{
  const p = buildPreview([E043], { E043: { hours: '86.84', reason: 'wrong clock out' } })
  eq('A3 one line', p.lines.length, 1)
  eq('A4 hours move 91.87 -> 86.84', [p.lines[0].hours_before, p.lines[0].hours_after], [91.87, 86.84])
  eq('A5 pay moves by the delta x rate', p.lines[0].pay_delta, round2((86.84 - 91.87) * 17))
  eq('A6 no problems when the reason is given', p.problems, [])
}

// ── B. an adjustment moves the figure; a stored correction masks it ──────────────────────────────
{
  const p = buildPreview([PEER], { E100: { adj: '-4', adjReason: 'left early' } })
  eq('B1 adjustment moves 80 -> 76', [p.lines[0].hours_before, p.lines[0].hours_after], [80, 76])
  eq('B2 flagged as an adjustment, not a correction',
     [p.lines[0].changed_adjustment, p.lines[0].changed_hours], [true, false])
}
{
  // A correction is stored, so the server keeps paying the correction — the adjustment is recorded
  // but changes nothing. The dialog must say that honestly (delta 0), not hide the line.
  const row = { ...PEER, hours_approved: 70 }
  const p = buildPreview([row], { E100: { adj: '-4', adjReason: 'left early' } })
  eq('B3 a stored correction masks the adjustment — zero delta, still a line',
     [p.lines.length, p.lines[0].hours_delta], [1, 0])
}

// ── C. an emptied box over a stored correction CLEARS it, visibly ────────────────────────────────
{
  const row = { ...E043, hours_approved: 86.84 }
  eq('C1 payableAfter reverts to the computation when cleared',
     payableAfter(row, { hours: '' }), 91.87)
  const p = buildPreview([row], { E043: { hours: '', reason: 'typed in error' } })
  eq('C2 the clear is one line, 86.84 -> 91.87',
     [p.lines.length, p.lines[0].hours_before, p.lines[0].hours_after], [1, 86.84, 91.87])
  eq('C3 and it says it is a clear', [p.lines[0].clears_correction, p.lines[0].hours_typed], [true, null])
  eq('C4 a clear with no reason is refused before the request',
     buildPreview([row], { E043: { hours: '' } }).problems,
     ['Simarjyot Singh: clearing the correction needs a reason'])
}
// An empty box over a row with NO stored correction is "not mentioned", never a clear and never a zero.
eq('C5 an empty box on an uncorrected row produces nothing',
   buildPreview([E043], { E043: { hours: '' } }).lines.length, 0)

// ── D. a no-op never reaches the dialog ──────────────────────────────────────────────────────────
eq('D1 retyping the stored correction is not a change',
   buildPreview([{ ...E043, hours_approved: 86.84 }], { E043: { hours: '86.84' } }).lines.length, 0)
eq('D2 retyping the stored adjustment is not a change',
   buildPreview([E043], { E043: { adj: '-10.97' } }).lines.length, 0)
eq('D3 an untouched row is not a change',
   buildPreview([E043, PEER], { E100: { adj: '-4', adjReason: 'r' } }).lines.map(l => l.employee_id),
   ['E100'])
eq('D4 nothing typed at all is reported as empty, not confirmed',
   buildPreview([E043, PEER], {}).empty, true)

// ── E. the reason gates match the server's refusals ──────────────────────────────────────────────
eq('E1 a correction away from the computed figure needs a reason',
   buildPreview([E043], { E043: { hours: '86.84' } }).problems,
   ['Simarjyot Singh: the corrected hours need a reason'])
eq('E2 an adjustment needs a reason',
   buildPreview([PEER], { E100: { adj: '-4' } }).problems,
   ['Second Person: the adjustment needs a reason'])
eq('E3 negative hours are refused',
   buildPreview([E043], { E043: { hours: '-1', reason: 'r' } }).problems,
   ['Simarjyot Singh: hours cannot be negative'])
eq('E4 an adjustment below zero payable is refused',
   buildPreview([PEER], { E100: { adj: '-900', adjReason: 'r' } }).problems,
   ['Second Person: that adjustment would make payable hours negative'])
eq('E5 a non-numeric box is refused',
   buildPreview([E043], { E043: { hours: 'abc', reason: 'r' } }).problems,
   ['Simarjyot Singh: the hours box is not a number'])
// Setting the hours TO the computed figure is not a correction, so it needs no reason.
eq('E6 typing the computed figure itself needs no reason',
   buildPreview([{ ...E043, hours_approved: 50 }], { E043: { hours: '102.84' } }).problems, [])

// ── F. "what the new payroll will be", over several rows at once ─────────────────────────────────
{
  const p = buildPreview([E043, PEER], {
    E043: { hours: '86.84', reason: 'wrong clock out' },
    E100: { adj: '-4', adjReason: 'left early' },
  })
  eq('F1 two lines', p.lines.length, 2)
  eq('F2 hours before/after', [p.totals.hours_before, p.totals.hours_after], [171.87, 162.84])
  eq('F3 the payroll before/after is the sum of the lines',
     [p.totals.pay_before, p.totals.pay_after],
     [round2(91.87 * 17 + 80 * 20), round2(86.84 * 17 + 76 * 20)])
  eq('F4 the delta reconciles to after − before',
     p.totals.pay_delta, round2(p.totals.pay_after - p.totals.pay_before))
  // The selection narrows the save, and therefore the confirmation, to the same rows.
  const one = buildPreview([E043, PEER], {
    E043: { hours: '86.84', reason: 'r' }, E100: { adj: '-4', adjReason: 'r' },
  }, ['E100'])
  eq('F5 a selection narrows both the dialog and the write', one.lines.map(l => l.employee_id), ['E100'])
}

// ── G. the request carries exactly what was confirmed ────────────────────────────────────────────
{
  const p = buildPreview([E043, PEER], {
    E043: { hours: '86.84', reason: 'wrong clock out' },
    E100: { adj: '-4', adjReason: 'left early' },
  })
  eq('G1 save rows', previewToRows(p, 'save'), [
    { employee_id: 'E043', action: 'save', hours_approved: '86.84', reason: 'wrong clock out' },
    { employee_id: 'E100', action: 'save', adjustment_hours: '-4', adjustment_reason: 'left early' },
  ])
  eq('G2 the same lines approve in one call when asked to',
     previewToRows(p, 'approve').map(r => r.action), ['approve', 'approve'])
  const cleared = buildPreview([{ ...E043, hours_approved: 86.84 }],
                               { E043: { hours: '', reason: 'typed in error' } })
  eq('G3 a clear sends the empty string the server reads as "clear"',
     previewToRows(cleared, 'save'),
     [{ employee_id: 'E043', action: 'save', hours_approved: '', reason: 'typed in error' }])
  eq('G4 an untouched field is absent from the request, never sent as 0',
     Object.keys(previewToRows(p, 'save')[0]).sort(),
     ['action', 'employee_id', 'hours_approved', 'reason'])
}

// ── negative zero never reaches a payroll screen ─────────────────────────────────────────────────
eq('H1 round2 never yields -0', Object.is(round2(-0.001), 0), true)

console.log(`\nprove_hours_approval_preview: ${pass} passed, ${fails.length} failed`)
if (fails.length) {
  console.error('\n' + fails.map(f => '  ✗ ' + f).join('\n'))
  process.exit(1)
}
console.log('  ✓ the confirmation states the save that actually goes, and refuses the ones the server would')
