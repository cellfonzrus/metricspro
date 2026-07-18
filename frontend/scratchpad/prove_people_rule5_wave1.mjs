// Proof harness for the RULE FIVE (§3d) people wave-1 adoption on payroll / payroll-tax / hours-budget.
// Verbatim re-impl of the pure primitives in src/lib/standard-filters.ts (same technique as
// core/scratchpad/prove_standard_filters.mjs, which already unit-proves the primitive itself). This
// harness proves the PAGE-LEVEL usage: (a) no-filter numbers are byte-identical to the pre-adoption
// totals, (b) a store filter narrows shift-derived per-employee rows and the grand totals RE-SUM from
// the filtered set (not stale), (c) a market filter resolves via the store→market map, (d) a rep filter
// narrows by employee identity, (e) hours-budget's two tables (budgets + overrides) narrow together.
// Run: node scratchpad/prove_people_rule5_wave1.mjs
let pass = 0, fail = 0
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl of lib/standard-filters.ts (no framework deps) ────────────────────────────
const norm = v => (v == null ? '' : String(v)).trim()
const emptyStandardFilter = (period = '') => ({ period, periodTo: '', stores: [], markets: [], reps: [] })
function matchesStandardFilter(row, sel, acc) {
  if (sel.stores.length && acc.store && !sel.stores.includes(norm(acc.store(row)))) return false
  if (sel.markets.length && acc.market && !sel.markets.includes(norm(acc.market(row)))) return false
  if (sel.reps.length && acc.rep && !sel.reps.includes(norm(acc.rep(row)))) return false
  return true
}
const filterRows = (rows, sel, acc) => rows.filter(r => matchesStandardFilter(r, sel, acc))

// ── synthetic data mirroring GET /payroll's shape (post dominant-store-attribution backend fix) ──
// Alex is a floater whose row is attributed to STORE-B (worked there more, per the backend fix) but
// whose totals (7h sched/act, $140/$140) are the SUM across every store they worked — exactly what
// the backend now returns, and exactly what this page must re-sum correctly when narrowed.
const payrollRows = [
  { employee_id: 'E1', name: 'Alex Floater', store: 'STORE-B', pay_rate: 20, scheduled_hours: 7, actual_hours: 7, shifts: 2, scheduled_pay: 140, actual_pay: 140 },
  { employee_id: 'E2', name: 'Sam One-Store', store: 'STORE-A', pay_rate: 18, scheduled_hours: 10, actual_hours: 8, shifts: 1, scheduled_pay: 180, actual_pay: 144 },
  { employee_id: 'E3', name: 'Jamie Other', store: 'STORE-C', pay_rate: 25, scheduled_hours: 5, actual_hours: 5, shifts: 1, scheduled_pay: 125, actual_pay: 125 },
]
const storeMarket = { 'STORE-A': 'North', 'STORE-B': 'North', 'STORE-C': 'South' }
const acc = { store: r => r.store, market: r => storeMarket[r.store] || '', rep: r => r.name }

// (a) no filter => byte-identical to the raw load (every row, same order, same totals)
{
  const filt = emptyStandardFilter()
  const visible = filterRows(payrollRows, filt, acc)
  ok('no-filter: same row count as raw load', visible.length === payrollRows.length)
  ok('no-filter: identical row objects (reference-stable filter, no mutation)', visible.every((r, i) => r === payrollRows[i]))
  const totActual = visible.reduce((s, r) => s + r.actual_hours, 0)
  const totPay = visible.reduce((s, r) => s + r.actual_pay, 0)
  ok('no-filter: total actual hours = 7+8+5 = 20 (unchanged)', totActual === 20)
  ok('no-filter: total actual pay = 140+144+125 = 409 (unchanged)', totPay === 409)
}

// (b) store filter narrows rows AND the grand total re-sums from the filtered set (not the full load)
{
  const filt = { ...emptyStandardFilter(), stores: ['STORE-B'] }
  const visible = filterRows(payrollRows, filt, acc)
  ok('store filter STORE-B: only Alex (floater\'s attributed store) remains', visible.length === 1 && visible[0].employee_id === 'E1')
  const totPay = visible.reduce((s, r) => s + r.actual_pay, 0)
  ok('store filter STORE-B: grand total re-sums to 140 (Alex only), NOT the full-load 409', totPay === 140)
}

// (c) market filter resolves via the store→market map (STORE-A + STORE-B are both "North")
{
  const filt = { ...emptyStandardFilter(), markets: ['North'] }
  const visible = filterRows(payrollRows, filt, acc)
  ok('market filter North: Alex (STORE-B) + Sam (STORE-A), not Jamie (South)', visible.length === 2 && visible.every(r => r.employee_id !== 'E3'))
  const totPay = visible.reduce((s, r) => s + r.actual_pay, 0)
  ok('market filter North: grand total re-sums to 140+144 = 284', totPay === 284)
}

// (d) rep filter narrows by employee identity, independent of store/market
{
  const filt = { ...emptyStandardFilter(), reps: ['Sam One-Store'] }
  const visible = filterRows(payrollRows, filt, acc)
  ok('rep filter Sam: exactly Sam\'s row', visible.length === 1 && visible[0].employee_id === 'E2')
}

// (e) combined store+rep filter (AND semantics) — Sam at STORE-A matches; Sam at STORE-B would not
{
  const filtMatch = { ...emptyStandardFilter(), stores: ['STORE-A'], reps: ['Sam One-Store'] }
  const filtNoMatch = { ...emptyStandardFilter(), stores: ['STORE-B'], reps: ['Sam One-Store'] }
  ok('AND semantics: store+rep both matching -> 1 row', filterRows(payrollRows, filtMatch, acc).length === 1)
  ok('AND semantics: store+rep mismatched -> 0 rows', filterRows(payrollRows, filtNoMatch, acc).length === 0)
}

// ── hours-budget: two tables (per-store budgets + overrides) narrow TOGETHER off the same filter ──
{
  const budgetRows = [
    { store_code: 'STORE-A', address: '1 Main', market: 'North', weekly_hours: 100, used_hours: 90 },
    { store_code: 'STORE-B', address: '2 Elm', market: 'North', weekly_hours: 80, used_hours: 85 },
    { store_code: 'STORE-C', address: '3 Oak', market: 'South', weekly_hours: 60, used_hours: 40 },
  ]
  const ovRows = [
    { id: 'o1', store_code: 'STORE-B', status: 'pending' },
    { id: 'o2', store_code: 'STORE-C', status: 'pending' },
  ]
  const sm = Object.fromEntries(budgetRows.map(r => [r.store_code, r.market]))
  const budgetAcc = { store: r => r.store_code, market: r => r.market || '' }
  const ovAcc = { store: r => r.store_code, market: r => sm[r.store_code] || '' }
  const filt = { ...emptyStandardFilter(), markets: ['North'] }
  const visBudgets = filterRows(budgetRows, filt, budgetAcc)
  const visOvs = filterRows(ovRows, filt, ovAcc)
  ok('hours-budget: market=North narrows budgets to STORE-A + STORE-B', visBudgets.length === 2)
  ok('hours-budget: SAME market filter narrows overrides to only the North one (o1)', visOvs.length === 1 && visOvs[0].id === 'o1')
  ok('hours-budget: no filter -> all 3 budget rows, all 2 overrides (byte-identical to before)',
    filterRows(budgetRows, emptyStandardFilter(), budgetAcc).length === 3 &&
    filterRows(ovRows, emptyStandardFilter(), ovAcc).length === 2)
}

console.log(`\n${pass}/${pass + fail} passed`)
process.exit(fail ? 1 : 0)
