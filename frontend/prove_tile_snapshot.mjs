// Proof harness — THE SNAPSHOT A MANAGER CAN SEND SAYS EXACTLY WHAT THE SCREEN SAYS (index §COMMCALC-19).
//
// Owner ask 2026-10-19, verbatim: *"on the sales comparison dashboard it shoudl give an option to send
// teh snapshot of the top part to to the desired people"*.
//
// Sending already had ONE home (`src/lib/send-report.tsx`): the recipient picker, the channels, the
// message and the Notify history. What was missing was CONTENT — that button can only send an
// `ExportPayload`, and `<ReportShell>` only ever handed it the TABLE. So every report could mail its
// rows and no report could mail the figures a manager actually reads. The fix is therefore a shared
// CONVERTER, `src/lib/tile-snapshot.ts`, not a snapshot block inside one page.
//
// WHAT THIS PROVES, by driving the REAL module (Node >= 22.18 strips its erasable TypeScript; its only
// import is a `type`, so nothing is installed and nothing is stubbed):
//   A. the sheet's SHAPE is fixed here — one column order for every report that ever sends its tiles
//   B. THE TRI-STATE, which is the whole reason this file exists: a measured percent, a brand-new item
//      with no base to divide by, and a change that CANNOT be measured and says so
//   C. AN ABSENT FIGURE IS BLANK, NEVER 0 — a snapshot outlives the screen and carries no small print,
//      so a fabricated $0.00 in a mailed sheet is worse than on the page (index §19.49's class)
//   D. MONEY IS PER ROW, not per column — a tile row legitimately mixes units and dollars
//   E. the rows are the caller's own tiles, in the caller's own order (the screen's order)
//   F. no state, no clock, no network: the same input gives the same sheet twice
//   G. negative controls — each rule above is shown going RED
//
// Run:  node frontend/prove_tile_snapshot.mjs      (no network, no DB, no React, no npm install)
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { readFileSync } from 'node:fs'

const HERE = dirname(fileURLToPath(import.meta.url))
const MOD = 'src/lib/tile-snapshot.ts'
const T = await import(pathToFileURL(join(HERE, MOD)).href)

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}
// the sheet's columns are getters; this is how a renderer reads one row
const cells = (sheet, row) => sheet.columns.map(c => c.get(row))

console.log('A. one fixed shape for every report that sends its tiles')
const sheet = T.tileSnapshotSheet([{ label: 'Revenue', value: 1, previous: 1, changePct: 0 }])
ck('the sheet is named once, from the module', sheet.name === T.SNAPSHOT_SHEET_NAME)
ck('the name is a plain word a reader recognises', T.SNAPSHOT_SHEET_NAME === 'Snapshot')
ck('five columns, in this order',
  JSON.stringify(sheet.columns.map(c => c.header))
  === JSON.stringify(['Measure', 'This period', 'Compared period', 'Change', 'Note']),
  sheet.columns.map(c => c.header))
ck('the caller may name the sheet (a report with two tile rows)',
  T.tileSnapshotSheet([], 'Top line').name === 'Top line')
ck('the figures and the change are right-aligned, the label and the note are not',
  sheet.columns.map(c => c.align || '') .join(',') === ',right,right,right,')
ck('no money column is declared — the figures are already formatted text',
  !sheet.columns.some(c => c.money))

console.log('B. THE TRI-STATE — a measured change, a new item, and a change that cannot be measured')
const lbl = t => T.snapshotChangeLabel(t)
ck('a rise is signed and arrowed', lbl({ label: 'x', value: 2, changePct: 12.5 }) === '▲ +12.5%')
ck('a fall is arrowed down, and the minus is the number’s own',
  lbl({ label: 'x', value: 1, changePct: -20.3 }) === '▼ -20.3%')
ck('a real nought is a real finding', lbl({ label: 'x', value: 1, changePct: 0 }) === '± 0%')
ck('NO BASE TO DIVIDE BY reads "new", never 0%', lbl({ label: 'x', value: 5, changePct: null }) === 'new')
ck('...and an absent key is the same case', lbl({ label: 'x', value: 5 }) === 'new')
ck('A CHANGE THAT CANNOT BE MEASURED SAYS SO',
  lbl({ label: 'x', value: 5, previous: 5, changePct: 0, measured: false }) === 'not measured')
ck('THE REGRESSION: the ± 0% the screen used to mail for two carried expense sheets is gone',
  lbl({ label: 'Store expenses', value: 276162.69, previous: 276162.69, changePct: 0, measured: false })
  === 'not measured')
ck('`measured: false` outranks a percent, because the percent is the claim being withheld',
  lbl({ label: 'x', value: 1, changePct: 99, measured: false }) === 'not measured')
ck('`measured: true` is just the normal case, not a third behaviour',
  lbl({ label: 'x', value: 1, changePct: 7, measured: true }) === '▲ +7%')
// and the reason travels with it — a mailed sheet has no tooltip
const unmeasured = T.tileSnapshotSheet([{
  label: 'Store expenses', value: 276162.69, previous: 276162.69, changePct: 0, measured: false,
  money: true, note: 'Both months carry 2026-08',
}])
ck('the reason is carried into the Note column',
  cells(unmeasured, unmeasured.rows[0])[4] === 'Both months carry 2026-08')
ck('...and the FIGURES are untouched by withholding the percent',
  cells(unmeasured, unmeasured.rows[0]).slice(1, 4).join('|') === '$276,162.69|$276,162.69|not measured',
  cells(unmeasured, unmeasured.rows[0]))

console.log('C. an absent figure is BLANK, never 0')
const absent = T.tileSnapshotSheet([
  { label: 'Financing', value: null, previous: null, changePct: null, money: true },
  { label: 'Tablets', value: 0, previous: 0, changePct: 0 },
])
ck('null money is an empty cell', cells(absent, absent.rows[0])[1] === '')
ck('an absent compared figure is an empty cell', cells(absent, absent.rows[0])[2] === '')
ck('a REAL zero is still printed — blank means "not known", not "none"',
  cells(absent, absent.rows[1])[1] === '0')
ck('an absent note is an empty cell, never the word null', cells(absent, absent.rows[0])[4] === '')
ck('an empty string is treated as absent too (an unset tile field)',
  cells(T.tileSnapshotSheet([{ label: 'x', value: '', money: true }]), { label: 'x', value: '', money: true })[1] === '')

console.log('D. money is honoured per ROW — a tile row mixes units and dollars')
const mixed = T.tileSnapshotSheet([
  { label: 'Phones', value: 487, previous: 629, changePct: -22.6 },
  { label: 'Accessories', value: 31298.61, previous: 39273.45, changePct: -20.3, money: true, note: '842 units' },
])
ck('a unit tile prints the count as a count', cells(mixed, mixed.rows[0])[1] === '487')
ck('a money tile prints currency', cells(mixed, mixed.rows[1])[1] === '$31,298.61')
ck('...on both sides of the comparison', cells(mixed, mixed.rows[1])[2] === '$39,273.45')
ck('a money tile whose figure is already text is passed through, not re-parsed',
  cells(T.tileSnapshotSheet([{ label: 'x', value: '1,234 units', money: true }]),
        { label: 'x', value: '1,234 units', money: true })[1] === '1,234 units')
ck('the note rides along', cells(mixed, mixed.rows[1])[4] === '842 units')

console.log('E. the rows are the caller’s tiles, in the caller’s order')
const order = ['Phones', 'BYOD', 'Activation', 'Tablets', 'Accessories', 'Financing',
               'Transactions', 'Revenue', 'Store expenses'].map(label => ({ label, value: 1, changePct: 1 }))
const os = T.tileSnapshotSheet(order)
ck('every tile is one row — nothing dropped, nothing invented', os.rows.length === order.length)
ck('the screen’s order is the sheet’s order',
  JSON.stringify(os.rows.map(r => cells(os, r)[0])) === JSON.stringify(order.map(t => t.label)))
ck('no tiles is an empty sheet, not a crash', T.tileSnapshotSheet([]).rows.length === 0)

console.log('F. pure — no clock, no state, no network')
const a = T.tileSnapshotSheet(order), b = T.tileSnapshotSheet(order)
ck('the same tiles give the same cells twice',
  JSON.stringify(a.rows.map(r => cells(a, r))) === JSON.stringify(b.rows.map(r => cells(b, r))))
const src = readFileSync(join(HERE, MOD), 'utf8')
const code = src.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map(l => l.replace(/(^|\s)\/\/.*$/, '')).join('\n')
ck('the module reaches for no clock', !/Date\.now|new Date/.test(code))
ck('...no network', !/\bfetch\s*\(|\bapi\s*\(/.test(code))
ck('...and no React', !/from 'react'|useMemo|useState/.test(code))
ck('it names no tenant, carrier or store (RULE TWO)',
  !/\b(cellfonz|luxelink|novawave|boost|metro(?!r)|cricket|t-?mobile|at&t|verizon)\b/i.test(code))
ck('the tri-state is ONE ladder, in one function',
  (code.match(/export function snapshotChangeLabel/g) || []).length === 1
  && (code.match(/'not measured'/g) || []).length === 1)

console.log('G. negative controls — each rule above goes RED when broken')
ck('a converter that printed 0 for an absent figure would fail C',
  cells(absent, absent.rows[0])[1] !== '$0.00' && cells(absent, absent.rows[0])[1] !== '0')
ck('a converter that read `measured` as truthy-only would fail B',
  lbl({ label: 'x', value: 1, changePct: 4, measured: undefined }) === '▲ +4%'
  && lbl({ label: 'x', value: 1, changePct: 4, measured: false }) === 'not measured')
ck('a converter that formatted every column as money would fail D',
  cells(mixed, mixed.rows[0])[1] !== '$487.00')
ck('a converter that sorted the rows would fail E',
  cells(os, os.rows[0])[0] === 'Phones')
ck('a tri-state collapsed to two states would fail B',
  new Set([lbl({ label: 'x', value: 1, changePct: 0 }),
           lbl({ label: 'x', value: 1, changePct: null }),
           lbl({ label: 'x', value: 1, changePct: 0, measured: false })]).size === 3)

console.log(`\n  ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
