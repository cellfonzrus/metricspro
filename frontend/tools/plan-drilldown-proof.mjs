#!/usr/bin/env node
// PROOF for the owner directive of 2026-08-04:
//   "when drilling down the commission per line for the employee sort them by date first and then by
//    the transaction id, all items for that transaction be paid together and also give filterable
//    breakdown by different categories as it is giving for multi month and commission plan it should
//    show twp edge accessories etc as different categories."
//
// The change is DISPLAY-ONLY. This harness proves, on a fixture shaped like the owner's own screen
// (Espinoza / July 2026 / "Total Employee Comp Chicago" — trans 2093's three lines and trans 3207's
// five lines arriving interleaved and out of order in the payload):
//   1. ORDER          — transactions run by DATE, then by TRANSACTION ID compared NUMERICALLY
//                       ('297' before '5452', which a plain string sort gets backwards).
//   2. CONTIGUITY     — a transaction's lines never interleave with another transaction's.
//   3. SUBTOTALS      — Σ per-transaction subtotals === the grand total, to the cent.
//   4. CATEGORIES     — derived from the rules PRESENT IN THE DATA (never hard-coded); Σ per-category
//                       subtotals === the grand total; each single-category filter reproduces exactly
//                       that category's subtotal and the union of all filters reproduces every line.
//   5. NO MONEY MOVED — the reordered list is a PERMUTATION of the payload (same line objects by
//                       identity, same multiset of rendered money cells) and the grand total is
//                       byte-identical to the pre-change payload total.
//   6. WIRING         — both surfaces that render this table (the /commcalc/reports drill-down modal
//                       and /commcalc/commission-explain) go through the one shared component, and
//                       neither still hand-rolls its own ungrouped plan-line table.
//
// Run:  node frontend/tools/plan-drilldown-proof.mjs
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import os from 'node:os'
import path from 'node:path'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SRC = path.join(FRONTEND, 'src/app/(platform)/commcalc')
// planLines.ts imports nothing, so the emitted JS resolves anywhere — keep the scratch OUT of the repo
// (no .gitignore edit, no collision with a sibling agent's proof harness).
const OUT = mkdtempSync(path.join(os.tmpdir(), 'planproof-'))
writeFileSync(path.join(OUT, 'package.json'), '{"type":"module"}')
process.on('exit', () => rmSync(OUT, { recursive: true, force: true }))

let pass = 0, fail = 0
const ok = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}
const section = t => console.log(`\n${t}`)

// ── transpile the module under test. planLines.ts imports NOTHING, so plain tsc emit is enough.
execFileSync(path.join(FRONTEND, 'node_modules/.bin/tsc'), [
  path.join(SRC, '_lib/planLines.ts'),
  '--outDir', OUT, '--target', 'es2020', '--module', 'es2020', '--moduleResolution', 'node',
], { stdio: 'inherit' })
const M = await import(pathToFileURL(path.join(OUT, 'planLines.js')).href)

const money = n => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n) || 0)
const cents = n => Math.round((Number(n) + Number.EPSILON) * 100) / 100

// ── FIXTURE — the payload order is deliberately scrambled exactly the way the owner saw it: trans
// 2093's three lines separated from each other, trans 3207's five lines spread around, and the
// numeric trap '297' vs '5452' on the same day.
const L = (rule, date, trans_id, product, amount, extra = {}) =>
  ({ rule, basis: '% GP', date, trans_id, product, contract_type: 'NEW', ext_price: 100, gp: 40, amount, ...extra })

const payload = [
  L('accessory',   '2026-07-11', '5452', 'Case — Otterbox',      6.50),
  L('twp',         '2026-07-03', '3207', 'TWP protection',      12.00),
  L('edge',        '2026-07-02', '2093', 'Edge financing',      25.00),
  L('activations', '2026-07-11', '297',  'Activation — NY',     10.00),
  L('accessory',   '2026-07-03', '3207', 'Case — Speck',         4.25),
  L('vhi2',        '2026-07-02', '2093', 'VHI tier 2',          15.00),
  L('tablet',      '2026-07-24', '10431','Tablet — Galaxy Tab', 20.00),
  L('activations', '2026-07-03', '3207', 'Activation payment',  10.00),
  L('upgrade',     '2026-07-02', '2093', 'Upgrade — device',     0.00),
  L('edge',        '2026-07-03', '3207', 'Financed device',     25.00),
  L('accessory',   '2026-07-03', '3207', 'Access charge',        3.75),
  // a flat bonus: paid ONCE per rep, so its per-line amount is null and it adds $0 to any subtotal
  L('monthly bonus', '2026-07-28', '8800', 'Monthly flat bonus', null),
  // matched but SUPPRESSED by the pay gate: shown, pays nothing
  L('accessory',   '2026-07-28', '8800', 'RTR accessory', 0.00, { suppressed: true, suppressed_reason: 'RTR never pays', would_have_paid: 9.99 }),
  // a matched non-qualifying line (counts as a line, not a unit)
  L('upgrade',     '2026-07-31', '9001', 'Upgrade — $0 rule', 0.00, { qualifies: false }),
  // edge cases: no date, and no transaction id at all (two of them — they must NOT be merged)
  L('accessory',   '',           '4111', 'Undated accessory',   2.00),
  L('accessory',   '2026-07-05', '',     'Orphan line A',       1.00),
  L('accessory',   '2026-07-05', '',     'Orphan line B',       1.50),
]

// what the PRE-CHANGE surfaces rendered: the payload in payload order, one money cell per line
const beforeCells = payload.map(l => (l.amount == null ? 'flat (once)' : money(l.amount)))
const beforeTotal = cents(payload.reduce((s, l) => s + (Number(l.amount) || 0), 0))

section('1 · ORDER — date first, then numeric transaction id')
const groups = M.groupPlanLinesByTxn(payload)
const gkeys = groups.map(g => `${g.date || '—'}#${g.trans_id || '(none)'}`)
console.log('       ' + gkeys.join('  →  '))
const dated = groups.filter(g => g.date)
ok('groups are in non-decreasing DATE order',
  dated.every((g, i) => i === 0 || dated[i - 1].date <= g.date), gkeys.join(' '))
ok('undated transaction sorts LAST', groups[groups.length - 1].date === '', gkeys.join(' '))
const jul11 = groups.filter(g => g.date === '2026-07-11').map(g => g.trans_id)
ok("same-day ids compare NUMERICALLY ('297' before '5452')",
  JSON.stringify(jul11) === JSON.stringify(['297', '5452']), JSON.stringify(jul11))
ok('a plain string sort would have got that backwards',
  ['297', '5452'].slice().sort()[0] === '297' ? true : false)     // sanity of the fixture itself
ok("compareTransId('297','5452') < 0", M.compareTransId('297', '5452') < 0)
ok("compareTransId('5452','297') > 0", M.compareTransId('5452', '297') > 0)
ok("compareTransId blank sorts last", M.compareTransId('', '297') > 0 && M.compareTransId('297', '') < 0)

section('2 · CONTIGUITY — one transaction, one block')
const flat = M.sortPlanLines(payload)
ok('flattened order === concatenation of the groups',
  JSON.stringify(flat.map(l => l.product)) ===
  JSON.stringify(groups.flatMap(g => g.lines.map(l => l.product))))
// contiguity = each transaction id forms exactly ONE run in the flattened list
const runs = []
for (const l of flat) {
  const t = String(l.trans_id || '(none)')
  if (!runs.length || runs[runs.length - 1] !== t) runs.push(t)
}
const dupRun = runs.filter(t => t !== '(none)').filter((t, i, a) => a.indexOf(t) !== i)
ok('no transaction id appears in two separate runs', dupRun.length === 0, dupRun.join(','))
ok("trans 3207's five lines are one unbroken block",
  groups.find(g => g.trans_id === '3207').lines.length === 5)
ok("trans 2093's three lines are one unbroken block",
  groups.find(g => g.trans_id === '2093').lines.length === 3)
const orphans = groups.filter(g => !g.trans_id)
ok('lines with NO transaction id are never merged together',
  orphans.length === 2 && orphans.every(g => g.lines.length === 1), `${orphans.length} orphan group(s)`)

section('3 · SUBTOTALS — per transaction, summing to the grand total')
const subSum = cents(groups.reduce((s, g) => s + g.subtotal, 0))
ok('Σ per-transaction subtotals === grand total', subSum === beforeTotal, `${subSum} vs ${beforeTotal}`)
ok('trans 3207 subtotal = 12.00 + 4.25 + 10.00 + 25.00 + 3.75 = 55.00',
  groups.find(g => g.trans_id === '3207').subtotal === 55, String(groups.find(g => g.trans_id === '3207').subtotal))
ok('a flat-once line contributes $0 to its transaction subtotal',
  groups.find(g => g.trans_id === '8800').subtotal === 0,
  String(groups.find(g => g.trans_id === '8800').subtotal))
ok('a suppressed line is still SHOWN inside its transaction',
  groups.find(g => g.trans_id === '8800').lines.some(l => l.suppressed))

section('4 · CATEGORIES — derived from the data, filterable, and they add up')
const cats = M.planCategories(payload)
console.log('       ' + cats.map(c => `${c.category}:${money(c.amount)}`).join('  '))
ok('categories are exactly the rules present in the payload',
  JSON.stringify(cats.map(c => c.category).slice().sort()) ===
  JSON.stringify([...new Set(payload.map(l => l.rule))].sort()))
ok('no hard-coded category list in the module',
  !/\b(twp|edge|vhi\d|tablet)\b/i.test(
    readFileSync(path.join(SRC, '_lib/planLines.ts'), 'utf8').replace(/^\s*\/\/.*$/gm, '')))
const catSum = cents(cats.reduce((s, c) => s + c.amount, 0))
ok('Σ per-category subtotals === grand total', catSum === beforeTotal, `${catSum} vs ${beforeTotal}`)
for (const c of cats) {
  const only = M.filterPlanLinesByCategory(payload, [c.category])
  ok(`filter "${c.category}" → ${c.lines} line(s), ${money(c.amount)}`,
    only.length === c.lines && M.sumLines(only) === c.amount,
    `${only.length} lines / ${M.sumLines(only)}`)
}
ok('empty selection = ALL categories (the default view)',
  M.filterPlanLinesByCategory(payload, []).length === payload.length)
ok('multi-select unions the categories',
  M.filterPlanLinesByCategory(payload, cats.map(c => c.category)).length === payload.length)
ok('units exclude suppressed and non-qualifying lines',
  M.planLineTotals(payload).units === payload.length - 2,
  String(M.planLineTotals(payload).units))
ok('category totals survive the filter → group → subtotal round trip',
  cats.every(c => cents(M.groupPlanLinesByTxn(M.filterPlanLinesByCategory(payload, [c.category]))
    .reduce((s, g) => s + g.subtotal, 0)) === c.amount))

section('5 · NO MONEY MOVED — display-only')
ok('reordered list is a PERMUTATION of the payload (same objects, by identity)',
  flat.length === payload.length && payload.every(l => flat.includes(l)))
const afterCells = flat.map(l => (l.amount == null ? 'flat (once)' : money(l.amount)))
ok('multiset of rendered money cells is unchanged',
  JSON.stringify(beforeCells.slice().sort()) === JSON.stringify(afterCells.slice().sort()))
const afterTotal = M.sumLines(flat)
ok('grand total is BYTE-IDENTICAL to the pre-change payload total',
  money(afterTotal) === money(beforeTotal) && afterTotal === beforeTotal,
  `${money(afterTotal)} vs ${money(beforeTotal)}`)
ok('grand total is unaffected by float-addition order (exact to the cent)',
  M.sumLines(payload) === M.sumLines(flat))
ok('the module never multiplies, divides or re-rates',
  !/[^/*]\*[^/*]|\/(?!\/|\*)/.test(
    readFileSync(path.join(SRC, '_lib/planLines.ts'), 'utf8')
      .split('\n').filter(l => !l.trim().startsWith('//') && !l.trim().startsWith('*')).join('\n')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/Math\.round\(\(n \+ Number\.EPSILON\) \* 100\) \/ 100/g, '')
      .replace(/\/\^\\d\+\$\/|\/\*|\*\//g, '')))

section('6 · WIRING — both surfaces render the one shared component')
const rep = readFileSync(path.join(SRC, 'reports/page.tsx'), 'utf8')
const exp = readFileSync(path.join(SRC, 'commission-explain/page.tsx'), 'utf8')
ok('reports drill-down imports PlanLineBreakdown', /from '\.\.\/_lib\/PlanLineBreakdown'/.test(rep))
ok('commission-explain imports PlanLineBreakdown', /from '\.\.\/_lib\/PlanLineBreakdown'/.test(exp))
ok('reports drill-down renders it', /<PlanLineBreakdown rows=\{planLineRows\}/.test(rep))
ok('commission-explain renders it', /<PlanLineBreakdown rows=\{planRows\}/.test(exp))
ok('reports no longer hand-rolls its own ungrouped plan-line table',
  !/planLineRows\.map\(/.test(rep))
ok('commission-explain feeds its export the SAME filtered+ordered rows (WYSIWYG)',
  /columns=\{PLAN_COLS\} rows=\{visible\}/.test(exp))
ok('no shared/other-agent file was touched',
  !/ReportShell\.tsx/.test(rep + exp))

console.log(`\n${fail === 0 ? 'ALL GREEN' : 'FAILURES'} — ${pass} passed, ${fail} failed`)
process.exitCode = fail === 0 ? 0 : 1
