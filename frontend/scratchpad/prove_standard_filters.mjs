// Proof for the pure standard-filter logic (src/lib/standard-filters.ts). Verbatim re-impl.
// Run: node scratchpad/prove_standard_filters.mjs
let pass = 0, fail = 0
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b)
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl ────────────────────────────────────────────────────────────────────────────
const norm = v => (v == null ? '' : String(v)).trim()
function ymd(v) {
  const s = norm(v); if (!s) return ''
  const m = s.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/)
  if (m) return `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`
  const d = new Date(s); return isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10)
}
const emptyStandardFilter = (period = '') => ({ period, periodTo: '', stores: [], markets: [], reps: [] })
function isStandardFilterActive(sel, countPeriod = false) {
  return sel.stores.length > 0 || sel.markets.length > 0 || sel.reps.length > 0 || (countPeriod && (!!sel.period || !!sel.periodTo))
}
function periodOk(rowDate, sel) {
  const from = norm(sel.period), to = norm(sel.periodTo)
  if (!from && !to) return true
  const d = ymd(rowDate); if (!d) return false
  if (from && to) return d >= ymd(from) && d <= ymd(to)
  if (from && from.length === 7) return d.slice(0, 7) === from
  if (from && from.length >= 10) return d >= ymd(from)
  if (to) return d <= ymd(to)
  return true
}
function matchesStandardFilter(row, sel, acc) {
  if (sel.stores.length && acc.store && !sel.stores.includes(norm(acc.store(row)))) return false
  if (sel.markets.length && acc.market && !sel.markets.includes(norm(acc.market(row)))) return false
  if (sel.reps.length && acc.rep && !sel.reps.includes(norm(acc.rep(row)))) return false
  if (acc.date && (sel.period || sel.periodTo) && !periodOk(acc.date(row), sel)) return false
  return true
}
const filterRows = (rows, sel, acc) => rows.filter(r => matchesStandardFilter(r, sel, acc))
function optionsFromRows(rows, acc) {
  const stores = new Set(), markets = new Set(), reps = new Map()
  for (const r of rows) {
    if (acc.store) { const v = norm(acc.store(r)); if (v) stores.add(v) }
    if (acc.market) { const v = norm(acc.market(r)); if (v) markets.add(v) }
    if (acc.rep) { const v = norm(acc.rep(r)); if (v && !reps.has(v)) reps.set(v, norm(acc.repEmail?.(r))) }
  }
  return {
    stores: [...stores].sort(), markets: [...markets].sort(),
    reps: [...reps.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([id, email]) => (email ? { id, label: id, sublabel: email } : { id, label: id })),
  }
}

// ── data ──────────────────────────────────────────────────────────────────────────────────────
const rows = [
  { store: 'S1', market: 'North', rep: 'Ann Lee', email: 'ann@x.com', d: '2026-07-01' },
  { store: 'S2', market: 'South', rep: 'Bob Ray', email: 'bob@x.com', d: '2026-07-15' },
  { store: 'S1', market: 'North', rep: 'Ann Lee', email: 'ann@x.com', d: '2026-06-20' },
  { store: 'S3', market: 'South', rep: '', email: '', d: '' },
]
const acc = { store: r => r.store, market: r => r.market, rep: r => r.rep, date: r => r.d, repEmail: r => r.email }
const hrefs = a => a.map(r => r.store + r.d)

// 1) no filter → all rows
ok('empty filter passes all', filterRows(rows, emptyStandardFilter(), acc).length === 4)
// 2) store multi
ok('store filter', eq(hrefs(filterRows(rows, { ...emptyStandardFilter(), stores: ['S1'] }, acc)), ['S12026-07-01', 'S12026-06-20']))
// 3) market
ok('market filter', filterRows(rows, { ...emptyStandardFilter(), markets: ['South'] }, acc).length === 2)
// 4) rep
ok('rep filter', filterRows(rows, { ...emptyStandardFilter(), reps: ['Ann Lee'] }, acc).length === 2)
// 5) combined store+rep (AND semantics)
ok('store AND rep', filterRows(rows, { ...emptyStandardFilter(), stores: ['S1'], reps: ['Ann Lee'] }, acc).length === 2)
// 6) period month
ok('period month 2026-07', filterRows(rows, { period: '2026-07', periodTo: '', stores: [], markets: [], reps: [] }, acc).length === 2)
// 7) period range
ok('period range', filterRows(rows, { period: '2026-06-01', periodTo: '2026-07-05', stores: [], markets: [], reps: [] }, acc).length === 2)
// 8) period from-date onward (no `to`)
ok('period from-onward', filterRows(rows, { period: '2026-07-10', periodTo: '', stores: [], markets: [], reps: [] }, acc).length === 1)
// 9) a period filter excludes rows with NO date (can't prove membership)
ok('no-date row excluded by a period filter', !filterRows(rows, { period: '2026-07', periodTo: '', stores: [], markets: [], reps: [] }, acc).some(r => r.d === ''))
// 10) options derived from rows (distinct, sorted, reps disambiguated by email)
{
  const o = optionsFromRows(rows, acc)
  ok('option stores', eq(o.stores, ['S1', 'S2', 'S3']))
  ok('option markets', eq(o.markets, ['North', 'South']))
  ok('option reps disambiguated w/ email sublabel', eq(o.reps, [{ id: 'Ann Lee', label: 'Ann Lee', sublabel: 'ann@x.com' }, { id: 'Bob Ray', label: 'Bob Ray', sublabel: 'bob@x.com' }]))
  ok('blank rep dropped from options', !o.reps.some(r => r.id === ''))
}
// 11) active flags
ok('isActive false when empty', !isStandardFilterActive(emptyStandardFilter()))
ok('isActive true w/ store', isStandardFilterActive({ ...emptyStandardFilter(), stores: ['S1'] }))
ok('isActive period only counts in range mode', !isStandardFilterActive({ period: '2026-07', periodTo: '', stores: [], markets: [], reps: [] }) && isStandardFilterActive({ period: '2026-07-01', periodTo: '', stores: [], markets: [], reps: [] }, true))
// 12) accessor absent → that dimension ignored (surface without a store column isn't wrongly emptied)
ok('missing accessor ignored', filterRows(rows, { ...emptyStandardFilter(), stores: ['S1'] }, { rep: r => r.rep }).length === 4)

console.log(`\nstandard-filters: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
