// Proof for the pure standard-filter logic (src/lib/standard-filters.ts). Verbatim re-impl.
// Run: node scratchpad/prove_standard_filters.mjs
let pass = 0, fail = 0
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b)
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl ────────────────────────────────────────────────────────────────────────────
const norm = v => (v == null ? '' : String(v)).trim()
const foldKey = v => norm(v).toLowerCase()   // OWNER 2026-07-18 case-insensitive key (display keeps orig casing)
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
  if (sel.stores.length && acc.store) { const k = foldKey(acc.store(row)); if (!sel.stores.some(s => foldKey(s) === k)) return false }
  if (sel.markets.length && acc.market) { const k = foldKey(acc.market(row)); if (!sel.markets.some(m => foldKey(m) === k)) return false }
  if (sel.reps.length && acc.rep) { const k = foldKey(acc.rep(row)); if (!sel.reps.some(r => foldKey(r) === k)) return false }
  if (acc.date && (sel.period || sel.periodTo) && !periodOk(acc.date(row), sel)) return false
  return true
}
const filterRows = (rows, sel, acc) => rows.filter(r => matchesStandardFilter(r, sel, acc))
function optionsFromRows(rows, acc) {
  const stores = new Map(), markets = new Map(), reps = new Map()
  for (const r of rows) {
    if (acc.store) { const v = norm(acc.store(r)); if (v) { const k = foldKey(v); if (!stores.has(k)) stores.set(k, v) } }
    if (acc.market) { const v = norm(acc.market(r)); if (v) { const k = foldKey(v); if (!markets.has(k)) markets.set(k, v) } }
    if (acc.rep) { const v = norm(acc.rep(r)); if (v) { const k = foldKey(v); if (!reps.has(k)) reps.set(k, { label: v, email: norm(acc.repEmail?.(r)) }) } }
  }
  return {
    stores: [...stores.values()].sort(), markets: [...markets.values()].sort(),
    reps: [...reps.values()].sort((a, b) => a.label.localeCompare(b.label)).map(({ label, email }) => (email ? { id: label, label, sublabel: email } : { id: label, label })),
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

// ── OWNER DIRECTIVE 2026-07-18 "do a case insensitive match" (NEW) ──────────────────────────────
const cvRows = [
  { store: 'Main St', market: 'North', rep: 'Ann Lee', email: 'ann@x.com', d: '2026-07-01' },
  { store: 'MAIN ST', market: 'north', rep: 'ANN LEE', email: 'ann2@x.com', d: '2026-07-02' },
  { store: 'main st', market: 'North', rep: 'ann lee', email: '', d: '2026-07-03' },
  { store: 'Elm Ave', market: 'South', rep: 'Bob Ray', email: 'bob@x.com', d: '2026-07-04' },
]
const cvAcc = { store: r => r.store, market: r => r.market, rep: r => r.rep, date: r => r.d, repEmail: r => r.email }

// (a) variant dedupe — case-variants collapse to ONE option, first-seen casing kept as the label
{
  const o = optionsFromRows(cvRows, cvAcc)
  ok('fold: store variants collapse to one option (first-seen casing)', eq(o.stores, ['Elm Ave', 'Main St']))
  ok('fold: market variants collapse (North/north → one)', eq(o.markets, ['North', 'South']))
  ok('fold: rep variants collapse to one option, first-seen name + first-seen email',
    eq(o.reps, [{ id: 'Ann Lee', label: 'Ann Lee', sublabel: 'ann@x.com' }, { id: 'Bob Ray', label: 'Bob Ray', sublabel: 'bob@x.com' }]))
}
// (b) case-insensitive match — selecting ONE casing now INCLUDES all variant rows (were silently excluded)
ok('fold: select "Main St" matches all 3 casings', filterRows(cvRows, { ...emptyStandardFilter(), stores: ['Main St'] }, cvAcc).length === 3)
ok('fold: select lowercase "main st" also matches all 3', filterRows(cvRows, { ...emptyStandardFilter(), stores: ['main st'] }, cvAcc).length === 3)
ok('fold: rep "ANN LEE" matches all 3 Ann rows', filterRows(cvRows, { ...emptyStandardFilter(), reps: ['ANN LEE'] }, cvAcc).length === 3)
ok('fold: market "NORTH" matches North + north (3 rows)', filterRows(cvRows, { ...emptyStandardFilter(), markets: ['NORTH'] }, cvAcc).length === 3)
// (c) selected-value round-trip — option picked from the deduped list, and a pre-fix stored casing, both match
{
  const o = optionsFromRows(cvRows, cvAcc)
  const pickedStore = o.stores.find(s => foldKey(s) === foldKey('main st'))   // 'Main St'
  ok('fold: option value picked from the deduped list matches every variant row', filterRows(cvRows, { ...emptyStandardFilter(), stores: [pickedStore] }, cvAcc).length === 3)
  ok('fold: a PRE-FIX stored selection ("MAIN ST") still round-trips after dedupe', filterRows(cvRows, { ...emptyStandardFilter(), stores: ['MAIN ST'] }, cvAcc).length === 3)
}
// (d) consistent-casing invariant — ZERO behavior change vs pre-fold on the original `rows`
{
  const o = optionsFromRows(rows, acc)
  ok('fold: consistent-case options identical (no dupes introduced/removed)', eq(o.stores, ['S1', 'S2', 'S3']) && eq(o.markets, ['North', 'South']))
  ok('fold: consistent-case store filter unchanged (S1 → 2 rows)', filterRows(rows, { ...emptyStandardFilter(), stores: ['S1'] }, acc).length === 2)
  ok('fold: consistent-case non-existent selection still excludes all', filterRows(rows, { ...emptyStandardFilter(), stores: ['S9'] }, acc).length === 0)
}
// (e) AND semantics preserved under folding
ok('fold: store+rep AND, both case-variant → still matches', filterRows(cvRows, { ...emptyStandardFilter(), stores: ['MAIN ST'], reps: ['ann lee'] }, cvAcc).length === 3)

console.log(`\nstandard-filters: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
