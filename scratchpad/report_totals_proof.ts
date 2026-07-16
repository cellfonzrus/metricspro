// Offline proof for the Sales-Report TOTAL row math + the export-injection mechanic.
// Run: node --experimental-strip-types scratchpad/report_totals_proof.ts   (from repo root)
// Kept OUT of frontend/ so the app's tsc (which globs **/*.ts) never compiles this proof.
// Pure — imports only the no-dependency helper frontend/src/lib/report-totals.ts.
import { computeTotalRow, resolveAgg, toNumber, type TotalsCol, type TotalCell } from '../frontend/src/lib/report-totals.ts'

let pass = 0, fail = 0
const eq = (name: string, got: any, want: any) => {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  if (g === w) { pass++; console.log(`  ok  ${name}`) }
  else { fail++; console.log(`FAIL  ${name}\n        got  ${g}\n        want ${w}`) }
}
const ok = (name: string, cond: boolean) => { if (cond) { pass++; console.log(`  ok  ${name}`) } else { fail++; console.log(`FAIL  ${name}`) } }

// ---- The EXACT sales-report column set (v2: Swaps added between Upgrades and Accessory $) ----
// idx: 0 Store 1 Market 2 Rep 3 Date 4 Txns 5 Activations 6 BYOD 7 Upgrades 8 Swaps 9 Acc$ 10 Rev$ 11 GP$
const salesCols: TotalsCol[] = [
  { header: 'Store', get: r => r.store },
  { header: 'Market', get: r => r.market || '—' },
  { header: 'Rep', get: r => r.salesperson },
  { header: 'Date', get: r => r.trans_date, type: 'date' },
  { header: 'Txns', get: r => r.txns, align: 'right' },
  { header: 'Activations', get: r => r.activations, align: 'right' },
  { header: 'BYOD', get: r => r.byod, align: 'right' },
  { header: 'Upgrades', get: r => r.upgrades, align: 'right' },
  { header: 'Swaps', get: r => r.swaps, align: 'right' },
  { header: 'Accessory $', get: r => r.accessory_rev, money: true },
  { header: 'Revenue $', get: r => r.revenue, money: true },
  { header: 'GP $', get: r => r.gp, money: true },
]
const salesRows = [
  { store: 'Store A', market: 'North', salesperson: 'Jane', trans_date: '2026-07-01', txns: 3, activations: 2, byod: 1, upgrades: 0, swaps: 1, accessory_rev: 40.5, revenue: 500, gp: 120 },
  { store: 'Store A', market: 'North', salesperson: 'Bob', trans_date: '2026-07-02', txns: 2, activations: 1, byod: 0, upgrades: 1, swaps: 0, accessory_rev: 10, revenue: 300.25, gp: 80 },
  { store: 'Store B', market: 'South', salesperson: 'Amy', trans_date: '2026-07-01', txns: 5, activations: 4, byod: 2, upgrades: 1, swaps: 2, accessory_rev: 305.64, revenue: 999.75, gp: 250.5 },
]

console.log('\n== A. Sales-report grand total (all rows) ==')
const A = computeTotalRow(salesCols, salesRows)
eq('Store col = "TOTAL" label', A[0].text, 'TOTAL')
eq('Market blank', A[1].text, '')
eq('Rep blank', A[2].text, '')
eq('Date blank', A[3].text, '')
eq('Txns summed (3+2+5=10)', A[4].raw, 10)
eq('Txns text formatted', A[4].text, '10')
eq('Activations summed (2+1+4=7)', A[5].raw, 7)
eq('BYOD summed (1+0+2=3)', A[6].raw, 3)
eq('Upgrades summed (0+1+1=2)', A[7].raw, 2)
eq('Swaps summed (1+0+2=3)', A[8].raw, 3)
eq('Swaps text formatted', A[8].text, '3')
eq('Accessory$ summed raw', Math.round(A[9].raw as number * 100) / 100, 356.14)
eq('Accessory$ money text', A[9].text, '$356.14')
eq('Revenue$ summed raw', Math.round(A[10].raw as number * 100) / 100, 1800)
eq('Revenue$ money text', A[10].text, '$1,800.00')
eq('GP$ summed raw (120+80+250.5)', A[11].raw, 450.5)
eq('GP$ money text', A[11].text, '$450.50')
ok('count cols are agg=sum (incl. Swaps)', A[4].agg === 'sum' && A[7].agg === 'sum' && A[8].agg === 'sum')
ok('money cols are agg=sum', A[9].agg === 'sum' && A[11].agg === 'sum')
ok('text/date cols are agg=none', A[1].agg === 'none' && A[3].agg === 'none')

console.log('\n== B. Money raw stays numeric (Excel), text is formatted (screen/PDF) ==')
ok('money raw is a number type', typeof A[10].raw === 'number')
ok('label raw is the string', A[0].raw === 'TOTAL')
ok('blank raw is empty string', A[1].raw === '')

console.log('\n== C. Averages/ratios recompute a MEAN, never a sum ==')
const ratioCols: TotalsCol[] = [
  { header: 'Store', get: r => r.store },
  { header: 'Close Rate %', get: r => r.rate, align: 'right' },
  { header: 'GP Margin', get: r => r.margin, align: 'right' },
  { header: 'Avg Ticket $', get: r => r.avg, money: true },
  { header: 'Revenue $', get: r => r.rev, money: true },
]
const ratioRows = [
  { store: 'A', rate: 50, margin: 0.2, avg: 100, rev: 1000 },
  { store: 'B', rate: 70, margin: 0.4, avg: 200, rev: 3000 },
]
const C = computeTotalRow(ratioCols, ratioRows)
eq('Close Rate % → mean (50,70)=60', C[1].raw, 60)
eq('Close Rate % agg=avg', C[1].agg, 'avg')
eq('GP Margin → mean (0.2,0.4)=0.3', Math.round((C[2].raw as number) * 100) / 100, 0.3)
eq('GP Margin text (2dp)', C[2].text, '0.3')
eq('Avg Ticket $ (money+"avg" header) → mean=150', C[3].raw, 150)
eq('Avg Ticket $ formatted as money', C[3].text, '$150.00')
eq('Revenue $ still summed=4000', C[4].raw, 4000)

console.log('\n== D. Right-aligned NON-numeric column is NOT summed (blank/none) ==')
const mixedCols: TotalsCol[] = [
  { header: 'Store', get: r => r.store },
  { header: 'Code', get: r => r.code, align: 'right' },     // right-aligned but text
  { header: 'Qty', get: r => r.qty, align: 'right' },
]
const mixedRows = [{ store: 'A', code: 'X-12', qty: 4 }, { store: 'B', code: 'Y-9', qty: 6 }]
const D = computeTotalRow(mixedCols, mixedRows)
eq('non-numeric right-aligned Code → none/blank', [D[1].agg, D[1].text], ['none', ''])
eq('Qty still summed=10', D[2].raw, 10)

console.log('\n== E. Explicit per-column agg override wins ==')
const ovCols: TotalsCol[] = [
  { header: 'Store', get: r => r.store },
  { header: 'Widgets', get: r => r.w, align: 'right', agg: 'none' },   // force no sum
  { header: 'Score', get: r => r.s, align: 'right', agg: 'avg' },      // force mean
]
const ovRows = [{ store: 'A', w: 3, s: 10 }, { store: 'B', w: 7, s: 20 }]
const E = computeTotalRow(ovCols, ovRows)
eq('agg:none override → blank', [E[1].agg, E[1].text], ['none', ''])
eq('agg:avg override → mean=15', E[2].raw, 15)

console.log('\n== F. Label lands in the first NON-aggregated column ==')
const f1 = computeTotalRow([{ header: 'Amt', get: r => r.a, money: true }, { header: 'Name', get: r => r.n }], [{ a: 5, n: 'x' }])
eq('all-summable-first → label in first text col', [f1[0].text, f1[1].text], ['$5.00', 'TOTAL'])

console.log('\n== G. Empty / degenerate inputs never throw ==')
// Over zero rows a count column can't be confirmed numeric → blank; money still sums to $0.00;
// the label still lands. (ReportShell separately skips the export total row when there are 0 rows.)
ok('empty rows → label + $0.00 money + blank counts (incl. Swaps)', (() => { const g = computeTotalRow(salesCols, []); return g[0].raw === 'TOTAL' && g[4].text === '' && g[8].text === '' && g[9].text === '$0.00' })())
eq('toNumber strips currency', toNumber('$1,234.50'), 1234.5)
eq('toNumber blank → null', toNumber(''), null)
eq('toNumber non-numeric → null', toNumber('abc'), null)
eq('toNumber code "X-12" → null (NOT -12)', toNumber('X-12'), null)
eq('toNumber "50%" → 50', toNumber('50%'), 50)
eq('toNumber "1-2" → null', toNumber('1-2'), null)

console.log('\n== H. Export injection mechanic (mirrors ReportShell.sheetWithTotal) ==')
// Reproduce EXACTLY what ReportShell does to put the total into an export sheet, and prove the
// exported cell values equal the on-screen totals (what you see == what you export).
function sheetWithTotal(cols: TotalsCol[], rs: any[]) {
  const cells = computeTotalRow(cols, rs)
  const TOTAL_ROW = '__rsTotal'
  const wrapped = cols.map((c, i) => ({ ...c, get: (row: any) => (row && (row as any)[TOTAL_ROW]) ? (row as any)[TOTAL_ROW][i].raw : c.get(row) }))
  return { columns: wrapped, rows: [...rs, { [TOTAL_ROW]: cells }] }
}
// export.tsx rawCell(): money → Number(v)||0 ; else v ?? ''
const rawCell = (c: any, row: any) => { const v = c.get(row); return c.money ? (Number(v) || 0) : (v == null ? '' : v) }
const sheet = sheetWithTotal(salesCols, salesRows)
const lastRow = sheet.rows[sheet.rows.length - 1]
ok('export sheet has exactly rows+1', sheet.rows.length === salesRows.length + 1)
eq('export TOTAL Store cell', rawCell(sheet.columns[0], lastRow), 'TOTAL')
eq('export TOTAL Txns cell = 10 (real number)', rawCell(sheet.columns[4], lastRow), 10)
eq('export TOTAL Swaps cell = 3 (real number)', rawCell(sheet.columns[8], lastRow), 3)
eq('export TOTAL Revenue$ cell = 1800 (real number)', rawCell(sheet.columns[10], lastRow), 1800)
eq('export normal row still reads through', rawCell(sheet.columns[0], salesRows[0]), 'Store A')
ok('export money total number matches on-screen raw', rawCell(sheet.columns[11], lastRow) === A[11].raw)

console.log('\n== I. Grouped export → per-sheet subtotal == what that group shows ==')
const north = salesRows.filter(r => r.market === 'North')
const gTot = computeTotalRow(salesCols, north)
eq('group North Txns subtotal (3+2)', gTot[4].raw, 5)
eq('group North Swaps subtotal (1+0)', gTot[8].raw, 1)
eq('group North Revenue$ subtotal (500+300.25)', Math.round(gTot[10].raw as number * 100) / 100, 800.25)

console.log(`\n==== ${pass} passed, ${fail} failed ====`)
if (fail) process.exit(1)
