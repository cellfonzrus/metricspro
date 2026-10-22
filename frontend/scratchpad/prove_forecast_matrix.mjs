// Proof for the pure forecast pivot (src/lib/forecast-matrix.ts) and for the WIRING that rests on it.
// Verbatim re-impl, same convention as prove_standard_filters.mjs.
// Run: node scratchpad/prove_forecast_matrix.mjs
let pass = 0, fail = 0
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl ────────────────────────────────────────────────────────────────────────────
const norm = v => (v == null ? '' : String(v)).trim()
const num = v => { const n = Number(v); return Number.isFinite(n) ? n : 0 }
const UNASSIGNED = '(unassigned)'
function buildForecastMatrix(rows, metric = 'recommend_order') {
  const cells = new Map(), storeMeta = new Map(), storeTot = new Map(), modelTot = new Map()
  let grand = 0
  for (const r of rows || []) {
    const model = norm(r.device_model); if (!model) continue
    const key = norm(r.store_key) || norm(r.store) || UNASSIGNED
    if (!storeMeta.has(key)) storeMeta.set(key, { key, label: norm(r.store) || UNASSIGNED })
    const v = num(r[metric])
    cells.set(`${key}\u0000${model}`, (cells.get(`${key}\u0000${model}`) || 0) + v)
    storeTot.set(key, (storeTot.get(key) || 0) + v)
    modelTot.set(model, (modelTot.get(model) || 0) + v)
    grand += v
  }
  const models = [...modelTot.keys()].sort((a, b) => (modelTot.get(b) - modelTot.get(a)) || a.localeCompare(b))
  const stores = [...storeMeta.values()].sort((a, b) => {
    if ((a.label === UNASSIGNED) !== (b.label === UNASSIGNED)) return a.label === UNASSIGNED ? 1 : -1
    return (storeTot.get(b.key) - storeTot.get(a.key)) || a.label.localeCompare(b.label)
  })
  return {
    models, stores,
    cell: (k, m) => cells.get(`${norm(k)}\u0000${norm(m)}`) || 0,
    storeTotal: k => storeTot.get(norm(k)) || 0,
    modelTotal: m => modelTot.get(norm(m)) || 0,
    grandTotal: grand, empty: grand === 0,
  }
}
function matrixExportRows(m) {
  return m.stores.map(s => {
    const row = { __store: s.label, __total: m.storeTotal(s.key) }
    for (const model of m.models) row[model] = m.cell(s.key, model)
    return row
  })
}
function matrixExportColumns(m) {
  return [{ header: 'Store', field: 'store', role: 'store', get: r => r.__store },
    ...m.models.map(model => ({ header: model, field: model, get: r => r[model] ?? 0 })),
    { header: 'Total', field: '__total', get: r => r.__total }]
}

const row = (store, model, order, extra = {}) =>
  ({ store, store_key: store, device_model: model, recommend_order: order, units: 0, on_hand: 0, projected_demand: 0, ...extra })

// ── §A the pivot itself ─────────────────────────────────────────────────────────────────────────
console.log('\n§A  stores down the side, models across the top')
const BASIC = [row('A St', 'iPhone 15', 4), row('A St', 'Moto G', 2), row('B Ave', 'iPhone 15', 1)]
let m = buildForecastMatrix(BASIC)
ok('A1 one column per model', JSON.stringify(m.models) === JSON.stringify(['iPhone 15', 'Moto G']))
ok('A2 one row per store', m.stores.map(s => s.label).join('|') === 'A St|B Ave')
ok('A3 a cell is that store’s units of that model', m.cell('A St', 'iPhone 15') === 4 && m.cell('B Ave', 'Moto G') === 0)
ok('A4 a store total adds its own row', m.storeTotal('A St') === 6)
ok('A5 a model total adds its own column', m.modelTotal('iPhone 15') === 5)
ok('A6 the grand total is both, and they agree',
  m.grandTotal === 7 && m.stores.reduce((t, s) => t + m.storeTotal(s.key), 0) === m.grandTotal
  && m.models.reduce((t, x) => t + m.modelTotal(x), 0) === m.grandTotal)

console.log('\n§B  ordering is by size, so what to order most of reads first')
m = buildForecastMatrix([row('A', 'Zeta', 1), row('A', 'Alpha', 9), row('B', 'Alpha', 3)])
ok('B1 the biggest model column is first', m.models[0] === 'Alpha')
ok('B2 the biggest store row is first', m.stores[0].label === 'A')
m = buildForecastMatrix([row('A', 'Tie', 2), row('B', 'Other', 2)])
ok('B3 a tie breaks on name, so the order is stable between render and export',
  JSON.stringify(m.models) === JSON.stringify(['Other', 'Tie']))

console.log('\n§C  nothing is quietly dropped')
m = buildForecastMatrix([row('A', 'X', 3), row('', 'X', 5), row(null, 'X', 2)])
ok('C1 a row with no store becomes the (unassigned) bucket, never a disappearance',
  m.stores.some(s => s.label === UNASSIGNED) && m.cell(UNASSIGNED, 'X') === 7)
ok('C2 …and it sorts LAST, so it never heads the order sheet', m.stores[m.stores.length - 1].label === UNASSIGNED)
ok('C3 …and it is still inside the grand total', m.grandTotal === 10)
m = buildForecastMatrix([row('A', '', 4), row('A', 'X', 1)])
ok('C4 a row with no MODEL names no column to order under and is left out', m.grandTotal === 1 && m.models.length === 1)
ok('C5 an all-zero grid says "nothing to order" rather than rendering empty',
  buildForecastMatrix([row('A', 'X', 0)]).empty === true && buildForecastMatrix([row('A', 'X', 0)]).stores.length === 1)
ok('C6 no rows at all is empty with no columns', buildForecastMatrix([]).empty === true && buildForecastMatrix([]).models.length === 0)
ok('C7 an unreadable figure is 0, never NaN in a cell the buyer reads',
  buildForecastMatrix([row('A', 'X', 'n/a')]).cell('A', 'X') === 0)

// ── §D STORE IDENTITY — the defect this grid must not re-introduce ──────────────────────────────
console.log('\n§D  one physical store is ONE row, whatever the feed calls it')
const TWO_SPELLINGS = [
  { store: '5619 N Broad St', store_key: '5619 N Broad Street', device_model: 'X', recommend_order: 3 },
  { store: '5619 N Broad Street', store_key: '5619 N Broad Street', device_model: 'X', recommend_order: 4 },
]
m = buildForecastMatrix(TWO_SPELLINGS)
ok('D1 two spellings sharing a canonical key are ONE row', m.stores.length === 1)
ok('D2 …and their orders ADD, instead of being split across two rows that each look too small',
  m.cell('5619 N Broad Street', 'X') === 7)
ok('D3 …shown under a spelling a human recognises', m.stores[0].label === '5619 N Broad St')
const RAW_ONLY = [{ store: 'Only Spelling', device_model: 'X', recommend_order: 2 }]
ok('D4 a row with no canonical key falls back to its own spelling and still appears',
  buildForecastMatrix(RAW_ONLY).cell('Only Spelling', 'X') === 2)

// ── §E the export is the SAME pivot ─────────────────────────────────────────────────────────────
console.log('\n§E  what-you-see-is-what-exports')
m = buildForecastMatrix(BASIC)
const xr = matrixExportRows(m), xc = matrixExportColumns(m)
ok('E1 one export row per grid row', xr.length === m.stores.length)
ok('E2 one export column per model, plus Store and Total', xc.length === m.models.length + 2)
ok('E3 every exported cell equals the cell on screen',
  m.stores.every(s => m.models.every((mod, i) => xc[i + 1].get(xr[m.stores.indexOf(s)]) === m.cell(s.key, mod))))
ok('E4 the exported row total equals the grid row total',
  xr.every((r, i) => r.__total === m.storeTotal(m.stores[i].key)))
ok('E5 the exported totals add back to the grand total',
  xr.reduce((t, r) => t + r.__total, 0) === m.grandTotal)
ok('E6 a store with none of a model exports 0, not blank', matrixExportRows(m)[1]['Moto G'] === 0)

// ── §F metrics: the grid answers the question it was switched to ────────────────────────────────
console.log('\n§F  the metric switch pivots the same rows, never a second fetch')
const MULTI = [row('A', 'X', 3, { units: 10, on_hand: 7, projected_demand: 10 })]
ok('F1 order', buildForecastMatrix(MULTI, 'recommend_order').grandTotal === 3)
ok('F2 projected', buildForecastMatrix(MULTI, 'projected_demand').grandTotal === 10)
ok('F3 sold', buildForecastMatrix(MULTI, 'units').grandTotal === 10)
ok('F4 on hand', buildForecastMatrix(MULTI, 'on_hand').grandTotal === 7)

// ── §G WIRING — the lock that fails the build if the screen un-wires from the one homes ─────────
console.log('\n§G  the screen dereferences the one homes (it cannot un-wire silently)')
{
  const fs = await import('node:fs')
  const page = fs.readFileSync(new URL('../src/app/(platform)/commcalc/payables/page.tsx', import.meta.url), 'utf8')
  ok('G1 the grid is built by the shared pivot, not pivoted inline',
    page.includes('buildForecastMatrix('))
  ok('G2 the export reads the SAME pivot', page.includes('matrixExportRows(') && page.includes('matrixExportColumns('))
  ok('G3 the store filter compares the canonical store KEY, not the feed’s spelling (owner 2026-10-22)',
    page.includes('r.store_key'))
  ok('G4 the filter bar carries the date RANGE', page.includes('periodMode="range"'))
  ok('G5 the date range is sent to the SERVER, which is where the forecast window is applied',
    page.includes("qs.set('start'") && page.includes("qs.set('end'"))
  const lib = fs.readFileSync(new URL('../src/lib/forecast-matrix.ts', import.meta.url), 'utf8')
  ok('G6 the pivot states no store-spelling rule of its own', !/squash|street|alias|store_mapping/i.test(lib.replace(/\/\/.*$/gm, '')))
}

console.log(`\nforecast-matrix: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
