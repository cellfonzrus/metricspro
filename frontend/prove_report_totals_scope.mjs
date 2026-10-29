#!/usr/bin/env node
// PROOF + GUARD: a total on screen names the ROWS IT IS OVER, and one module does the arithmetic.
//
// OWNER ASK 2026-10-29, verbatim: *"need the total below the hours on the time clock when filtered
// and without filter"*.
//
// THE INSTANCE is the Hours column on Time Clock. THE CLASS is that every total in this product is a
// total of whatever rows a filter left visible, and none of them said so — a filter left on by
// accident read exactly like a quiet fortnight. So the fix is `computeTotalScope` in
// `src/lib/report-totals.ts`, the module that ALREADY owned total arithmetic for `ReportShell`'s
// footer, extended rather than copied; and the two surfaces that draw a footer dereference it.
//
// WHAT IT PINS:
//   A  computeTotalScope exists in the module that already owned total arithmetic (no sibling)
//   B  the pair is correct: visible total, loaded total, and the counts of each
//   C  `narrowed` is false when nothing is cut — the renderers draw one figure then, not two
//   D  THE DEFECT THIS EXISTS TO STOP: a filter leaving zero rows must not make the two figures
//      different aggregations of one column (an armed control proves the guard is load-bearing)
//   E  a ratio / percent column is still a MEAN and a text column is still blank, on BOTH figures
//   F  the Time Clock screen reads every total off the one home — no hand-rolled `.reduce(` sum
//   G  ReportShell's footer dereferences it too, so every opted-in report inherits the pair
//
// Run: node prove_report_totals_scope.mjs        (from frontend/)
import { readFileSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadLibs } from './prove_load_ts.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const TIMECLOCK = join(HERE, 'src/app/(platform)/storeops/timeclock/page.tsx')
const SHELL = join(HERE, 'src/components/ReportShell.tsx')
const TOTALS = join(HERE, 'src/lib/report-totals.ts')

const [T] = await loadLibs(HERE, ['report-totals'])

let pass = 0
const fails = []
const ck = (label, cond, extra) => {
  if (cond) { pass++; return }
  fails.push(`${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`)
}
const codeOnly = s => s.replace(/\/\*[\s\S]*?\*\//g, ' ')
  .split('\n').map(l => l.replace(/\/\/.*$/, '')).join('\n')

// One shape used throughout: a punch board, two stores, one filtered view of it.
const COLS = [
  { header: 'Employee', type: 'text', get: r => r.who },
  { header: 'Date', type: 'date', get: r => r.day },
  { header: 'Hours', type: 'number', get: r => r.hours },
  { header: 'Pay', type: 'money', get: r => r.pay },
]
const LOADED = [
  { who: 'A', day: '2026-10-05', hours: 8, pay: 136, store: 'B-1' },
  { who: 'B', day: '2026-10-05', hours: 7.5, pay: 127.5, store: 'B-1' },
  { who: 'C', day: '2026-10-06', hours: 4.25, pay: 72.25, store: 'B-2' },
]
const SHOWN = LOADED.filter(r => r.store === 'B-1')

console.log('\n── A. it lives in the module that already owned total arithmetic ──')
ck('computeTotalScope is exported from lib/report-totals', typeof T.computeTotalScope === 'function')
ck('…and computeTotalRow, the function it is built on, is still exported unchanged',
   typeof T.computeTotalRow === 'function')
const totalsSrc = codeOnly(readFileSync(TOTALS, 'utf8'))
ck('…and it REUSES computeTotalRow rather than re-deriving the sum — one arithmetic, two populations',
   (totalsSrc.match(/computeTotalRow\(/g) || []).length >= 2)
ck('no sibling totals module was created', !existsSync(join(HERE, 'src/lib/report-totals-scope.ts')))

console.log('\n── B. the pair is right ──')
{
  const s = T.computeTotalScope(COLS, SHOWN, LOADED)
  ck('the visible Hours total is the sum of the VISIBLE rows', s.cells[2].raw === 15.5, s.cells[2].raw)
  ck('…and the second figure is the sum of every LOADED row', s.cells[2].allRaw === 19.75, s.cells[2].allRaw)
  ck('the money column carries both too, formatted as money',
     s.cells[3].raw === 263.5 && s.cells[3].allRaw === 335.75 && /\$/.test(s.cells[3].allText),
     [s.cells[3].text, s.cells[3].allText])
  ck('shown / loaded are the row counts, not the column count', s.shown === 2 && s.loaded === 3)
  ck('narrowed is true when a filter is cutting rows', s.narrowed === true)
  ck('the label lands in the first non-aggregating column', s.cells[0].text === 'TOTAL', s.cells[0].text)
  ck('…and a caller can name it', T.computeTotalScope(COLS, SHOWN, LOADED, 'ALL PUNCHES').cells[0].text === 'ALL PUNCHES')
}

console.log('\n── C. with nothing cut, there is only one figure to show ──')
{
  const s = T.computeTotalScope(COLS, LOADED, LOADED)
  ck('narrowed is FALSE when the visible set is the loaded set', s.narrowed === false)
  ck('…and the two figures agree, so a renderer gated on `narrowed` draws one number',
     s.cells[2].raw === s.cells[2].allRaw && s.cells[2].text === s.cells[2].allText)
}

console.log('\n── D. the defect: a filter down to zero rows must not change the AGGREGATION ──')
// resolveAgg reads the rows it is given to decide whether a numeric-looking column is summable. Over
// an EMPTY visible set it resolves 'none' — so without a shared decision the visible cell blanks
// while the figure beside it, labelled the same, still sums. That is the bug the `aggs` argument
// exists for, and it is proved by ARMING it: strip the argument and watch the defect return.
{
  const s = T.computeTotalScope(COLS, [], LOADED)
  ck('the visible Hours total over an empty filter is 0.00, not blank', s.cells[2].raw === 0, s.cells[2])
  ck('…and it still reports the same aggregation as the figure beside it',
     s.cells[2].agg === 'sum', s.cells[2].agg)
  ck('…whose value is the whole loaded set', s.cells[2].allRaw === 19.75)
  ck('shown is 0 and the row counts still differ', s.shown === 0 && s.loaded === 3 && s.narrowed === true)
}
{
  // ARMED CONTROL — the real source, with the shared decision removed on its way through the loader.
  const [Broken] = await loadLibs(HERE, ['report-totals'], {
    'report-totals': src => src.replace(
      'const here = computeTotalRow(cols, shown, firstLabel, aggs)',
      'const here = computeTotalRow(cols, shown, firstLabel)'),
  })
  const b = Broken.computeTotalScope(COLS, [], LOADED)
  ck('control: WITHOUT the shared decision the empty-filter cell really does go blank — so the '
     + 'argument is load-bearing, not decoration',
     b.cells[2].text === '' && b.cells[2].agg === 'none', [b.cells[2].text, b.cells[2].agg])
}

console.log('\n── E. a ratio stays a mean, a text column stays blank — on BOTH figures ──')
{
  const cols = [
    { header: 'Store', type: 'text', get: r => r.store },
    { header: 'Hours', type: 'number', get: r => r.hours },
    { header: 'Face Match %', type: 'number', get: r => r.pct },
  ]
  const loaded = [{ store: 'B-1', hours: 8, pct: 90 }, { store: 'B-1', hours: 2, pct: 70 },
                  { store: 'B-2', hours: 5, pct: 50 }]
  const s = T.computeTotalScope(cols, loaded.filter(r => r.store === 'B-1'), loaded)
  ck('a % column is a MEAN of the visible rows, never their sum', s.cells[2].raw === 80, s.cells[2].raw)
  ck('…and a MEAN of the loaded rows in the second figure', s.cells[2].allRaw === 70, s.cells[2].allRaw)
  ck('…and both say so', s.cells[2].agg === 'avg')
  ck('a text column is blank in both figures and carries the label',
     s.cells[0].text === 'TOTAL' && s.cells[0].allText === 'TOTAL' && s.cells[0].agg === 'none')
}

console.log('\n── F. the Time Clock screen dereferences the one home ──')
const tc = codeOnly(readFileSync(TIMECLOCK, 'utf8'))
ck('it imports computeTotalScope', /import\s*\{[^}]*computeTotalScope[^}]*\}\s*from\s*'@\/lib\/report-totals'/.test(tc))
ck('the punches table has a footer', /<tfoot>/.test(tc))
ck('…and so does the manual-hours table beside it — the sibling on the same screen',
   (tc.match(/<tfoot>/g) || []).length >= 2, (tc.match(/<tfoot>/g) || []).length)
ck('the Hours total in the footer is READ off the scope, not summed in the markup',
   /scope\.cells\[HOURS_I\]/.test(tc))
ck('the badges at the top read the SAME object, so the two can never disagree',
   /const totalHours = Number\(scope\.cells\[HOURS_I\]\.raw\)/.test(tc))
ck('the unfiltered figure is drawn only where a filter is cutting rows',
   /scope\.narrowed/.test(tc) && /allText/.test(tc))
// The defect this page had: a sum written into the page. None may come back.
const reduces = (tc.match(/\.reduce\(\s*\(\s*s\s*,/g) || []).length
ck('no hand-rolled total survives on the page — the two it had are gone', reduces === 0, reduces)
ck('the header ribbon and the footer come from ONE column list, so a column cannot gain a heading '
   + 'without gaining a total slot',
   /screenCols\.slice\(0, SCREEN_COL_COUNT\)\.map/.test(tc))

console.log('\n── G. every report that opts into a total inherits the pair ──')
const shell = codeOnly(readFileSync(SHELL, 'utf8'))
ck('ReportShell imports computeTotalScope', /computeTotalScope/.test(shell))
ck('…and builds its footer from it', /computeTotalScope\(cols, filtered, rows\)/.test(shell))
ck('…drawing the second figure only when a filter is cutting rows',
   /totalScope\.narrowed/.test(shell))
ck('…and never on a column that does not aggregate — a blank label has nothing to compare',
   /agg !== 'none'/.test(shell))
ck('the export path still uses computeTotalRow, so what you see is still what you export (RULE FOUR)',
   /computeTotalRow\(cols, rs\)/.test(shell))

console.log('\n── Z. negative controls ──')
{
  ck('control: a sum over no rows at all is 0, and the pair is not "narrowed"',
     T.computeTotalScope(COLS, [], []).narrowed === false)
  ck('control: the detector for a hand-rolled sum really matches one',
     /\.reduce\(\s*\(\s*s\s*,/.test('const t = rows.reduce((s, r) => s + r.hours, 0)'))
  ck('control: a null row list does not throw', T.computeTotalScope(COLS, null, null).loaded === 0)
}

console.log(`\nprove_report_totals_scope: ${pass} passed, ${fails.length} failed`)
if (fails.length) {
  console.error('\n' + fails.map(f => '  XX  ' + f).join('\n'))
  console.error('\n  A total that does not name its population is the defect this guard exists to\n'
    + '  stop: a filter left on by accident reads exactly like a quiet period. Totals come from\n'
    + '  lib/report-totals.computeTotalScope — never from a .reduce() in a page.')
  process.exit(1)
}
console.log('  ok  one module totals, and every total on screen names the rows it is over')
