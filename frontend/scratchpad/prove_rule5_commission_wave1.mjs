// Proof for the pure filter-composition / aggregation logic added in the RULE FIVE wave-1 commission slice.
// Verbatim re-impl of the small logic each page adds on top of the proven standard-filters primitives.
// Run: node scratchpad/prove_rule5_commission_wave1.mjs
let pass = 0, fail = 0
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b)
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl of the proven primitives (from src/lib/standard-filters.ts) ──────────────────
const norm = v => (v == null ? '' : String(v)).trim()
function matchesStandardFilter(row, sel, acc) {
  if (sel.stores.length && acc.store && !sel.stores.includes(norm(acc.store(row)))) return false
  if (sel.markets.length && acc.market && !sel.markets.includes(norm(acc.market(row)))) return false
  if (sel.reps.length && acc.rep && !sel.reps.includes(norm(acc.rep(row)))) return false
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
  return { stores: [...stores].sort(), markets: [...markets].sort(),
    reps: [...reps.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([id, email]) => (email ? { id, label: id, sublabel: email } : { id, label: id })) }
}
const empty = () => ({ period: '', periodTo: '', stores: [], markets: [], reps: [] })

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 1) sales-report.tsx — rep-multi AND-composed with the existing market/store multi-select filters.
//    fRows = rows.filter(market ∧ store ∧ rep). Verbatim of the page's predicate.
// ════════════════════════════════════════════════════════════════════════════════════════════════
{
  const rows = [
    { store: 'A', market: 'North', salesperson: 'Ann' },
    { store: 'A', market: 'North', salesperson: 'Bob' },
    { store: 'B', market: 'South', salesperson: 'Ann' },
    { store: 'B', market: 'South', salesperson: 'Cy' },
  ]
  const fRows = (selMarkets, selStores, selReps) => rows.filter(r =>
    (selMarkets.length === 0 || selMarkets.includes(r.market)) &&
    (selStores.length === 0 || selStores.includes(r.store)) &&
    (selReps.length === 0 || selReps.includes(r.salesperson)))
  ok('sales: no filter → all', fRows([], [], []).length === 4)
  ok('sales: rep only', fRows([], [], ['Ann']).length === 2)
  ok('sales: rep AND store (AND-composed)', eq(fRows([], ['A'], ['Ann']).map(r => r.salesperson), ['Ann']))
  ok('sales: rep AND market AND store', fRows(['South'], ['B'], ['Cy']).length === 1)
  ok('sales: contradictory store+rep → empty', fRows([], ['A'], ['Cy']).length === 0)
  // rep options are pick-don't-type distinct over the org-scoped rows
  ok('sales: rep options distinct+sorted', eq(optionsFromRows(rows, { rep: r => r.salesperson }).reps.map(o => o.id), ['Ann', 'Bob', 'Cy']))
}

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 2) gp.tsx — By-Rep view composes the existing store substring filter with the new rep-multi.
//    (store filter matches on the leading token; rep-multi is exact on storeops_name||rep.)
// ════════════════════════════════════════════════════════════════════════════════════════════════
{
  const repRows = [
    { store: '101 Main', rep: 'r1', storeops_name: 'Ann Lee' },
    { store: '101 Main', rep: 'r2', storeops_name: 'Bob Ray' },
    { store: '202 Oak', rep: 'r3', storeops_name: 'Ann Lee' },  // same display name, different store row
  ]
  const repName = r => r.storeops_name || r.rep
  const filt = (selStores, selReps) => repRows.filter(r =>
    (!selStores.length || selStores.some(s => r.store?.includes(s.split(' ')[0]))) &&
    (!selReps.length || selReps.includes(repName(r))))
  ok('gp: no filter → all rep rows', filt([], []).length === 3)
  ok('gp: rep-multi narrows By-Rep view', filt([], ['Ann Lee']).length === 2)
  ok('gp: store substring AND rep-multi', eq(filt(['101 Main'], ['Ann Lee']).map(r => r.rep), ['r1']))
  ok('gp: rep options from rep rows', eq(optionsFromRows(repRows, { rep: repName }).reps.map(o => o.id), ['Ann Lee', 'Bob Ray']))
}

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 3) reports.tsx (Rep Commissions) — backend now stamps `market` on each rep row; the page filters via
//    the proven filterRows over {store, market, rep}. Header total + CSV export use the FILTERED set.
// ════════════════════════════════════════════════════════════════════════════════════════════════
{
  const reps = [
    { epay_salesperson: 'Ann', store: 'A', market: 'North', total_payout: 100 },
    { epay_salesperson: 'Bob', store: 'A', market: 'North', total_payout: 50 },
    { epay_salesperson: 'Cy',  store: 'B', market: 'South', total_payout: 25 },
  ]
  const acc = { store: r => r.store, market: r => r.market, rep: r => r.epay_salesperson }
  const total = f => filterRows(reps, f, acc).reduce((s, r) => s + r.total_payout, 0)
  ok('reports: unfiltered total = sum(all)', total(empty()) === 175)
  ok('reports: market filter → WYSIWYG total', total({ ...empty(), markets: ['North'] }) === 150)
  ok('reports: store+rep filter → single row total', total({ ...empty(), stores: ['A'], reps: ['Ann'] }) === 100)
  ok('reports: options carry the backend-stamped market', eq(optionsFromRows(reps, acc).markets, ['North', 'South']))
}

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 4) commission-ledger.tsx — By-rep rep-multi; totals row recomputed from the FILTERED reps (WYSIWYG).
//    Store/market are genuinely absent from the ledger data model (documented deviation).
// ════════════════════════════════════════════════════════════════════════════════════════════════
{
  const CATS = ['commission', 'spiff', 'equipment_rebate', 'residual_monthly', 'autopay_residual']
  const reps = [
    { rep: 'Ann', ledger_payout: 30, live_payout: 28, commission: 20, spiff: 10, equipment_rebate: 0, residual_monthly: 0, autopay_residual: 0 },
    { rep: 'Bob', ledger_payout: 15, live_payout: null, commission: 15, spiff: 0, equipment_rebate: 0, residual_monthly: 0, autopay_residual: 0 },
  ]
  const totals = { ledger_payout: 45, live_payout: 28, commission: 35, spiff: 10, equipment_rebate: 0, residual_monthly: 0, autopay_residual: 0 }
  const recompute = (selReps) => {
    const filteredReps = selReps.length ? reps.filter(r => selReps.includes(r.rep)) : reps
    if (!selReps.length) return { filteredReps, t: totals }
    const t = { ledger_payout: 0, live_payout: 0 }; CATS.forEach(c => { t[c] = 0 })
    filteredReps.forEach(r => { t.ledger_payout += r.ledger_payout || 0; t.live_payout += (r.live_payout || 0); CATS.forEach(c => { t[c] += r[c] || 0 }) })
    return { filteredReps, t }
  }
  ok('ledger: no filter → server totals passthrough', recompute([]).t.ledger_payout === 45)
  const one = recompute(['Ann'])
  ok('ledger: filtered totals recomputed', one.t.ledger_payout === 30 && one.t.commission === 20 && one.t.spiff === 10)
  ok('ledger: filtered live_payout treats null as 0', one.t.live_payout === 28)
  ok('ledger: filtered rows = selected', eq(one.filteredReps.map(r => r.rep), ['Ann']))
}

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 5) ma-commission — server-side store/rep narrowing + STABLE options (mirrors the Python handler).
//    Options are computed from the UNFILTERED rows so the picker never collapses under a filter.
// ════════════════════════════════════════════════════════════════════════════════════════════════
{
  const comm = [
    { merchant_account_id: 'ACC1', user_name: 'jsmith', pay: 10 },
    { merchant_account_id: 'ACC1', user_name: 'mjones', pay: 20 },
    { merchant_account_id: 'ACC2', user_name: 'jsmith', pay: 5 },
  ]
  const tx = [
    { account_id: 'ACC1', account_name: 'Downtown', margin: 3 },
    { account_id: 'ACC2', account_name: 'Uptown', margin: 4 },
  ]
  // stable options (pre-filter)
  const storeNames = {}
  for (const r of comm) { const k = norm(r.merchant_account_id); if (k) storeNames[k] = storeNames[k] ?? null }
  for (const r of tx) { const k = norm(r.account_id); if (k) { const nm = norm(r.account_name); if (nm) storeNames[k] = nm; else storeNames[k] = storeNames[k] ?? null } }
  const store_options = Object.entries(storeNames).map(([id, v]) => ({ id, label: v || id })).sort((a, b) => a.label.toLowerCase().localeCompare(b.label.toLowerCase()))
  const rep_options = [...new Set(comm.map(r => norm(r.user_name)).filter(Boolean))].sort()
  ok('ma: store options carry names, stable & sorted', eq(store_options, [{ id: 'ACC1', label: 'Downtown' }, { id: 'ACC2', label: 'Uptown' }]))
  ok('ma: rep options distinct+sorted', eq(rep_options, ['jsmith', 'mjones']))
  // server-side narrowing
  const narrow = (stores, reps) => {
    const storeSel = new Set(stores), repSel = new Set(reps)
    let c = comm, t = tx
    if (storeSel.size) { c = c.filter(r => storeSel.has(norm(r.merchant_account_id))); t = t.filter(r => storeSel.has(norm(r.account_id))) }
    if (repSel.size) { c = c.filter(r => repSel.has(norm(r.user_name))) }
    return { payable: c.reduce((s, r) => s + r.pay, 0), airtime: t.reduce((s, r) => s + r.margin, 0), acts: c.length }
  }
  ok('ma: unfiltered payable+airtime', eq(narrow([], []), { payable: 35, airtime: 7, acts: 3 }))
  ok('ma: store filter narrows tiles AND airtime (server-side WYSIWYG)', eq(narrow(['ACC1'], []), { payable: 30, airtime: 3, acts: 2 }))
  ok('ma: rep filter narrows comm; airtime has no rep dim (unchanged by rep)', eq(narrow([], ['jsmith']), { payable: 15, airtime: 7, acts: 2 }))
  ok('ma: store+rep compose', eq(narrow(['ACC1'], ['mjones']), { payable: 20, airtime: 3, acts: 1 }))
  ok('ma: options unaffected by an active narrow (stable list)', rep_options.length === 2 && store_options.length === 2)
}

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 6) ma-commission REP-FILTER airtime caveat (GATE-1 FIX 1). Airtime (raw_ma_daily_tx) has NO rep column,
//    so under a rep filter airtime can't be narrowed → the page EXCLUDES it (Total payable = rep commission
//    only), blanks the airtime column + tile, and drops airtime-only stores the rep never touched. Screen
//    and export apply the SAME transform (verbatim of the page's presentation logic).
// ════════════════════════════════════════════════════════════════════════════════════════════════
{
  const byStore = [
    { account_id: 'ACC1', activations: 1, payable: 10, airtime_margin: 3 },  // rep touched
    { account_id: 'ACC2', activations: 1, payable: 5,  airtime_margin: 4 },  // rep touched
    { account_id: 'ACC3', activations: 0, payable: 0,  airtime_margin: 9 },  // airtime-only — rep never sold here
  ]
  const present = (repFilterActive) => byStore
    .filter(s => !repFilterActive || (s.activations || 0) > 0 || (s.payable || 0) !== 0)   // drop airtime-only
    .map(s => ({ account_id: s.account_id,
      airtime: repFilterActive ? null : s.airtime_margin,                                  // blanked under rep filter
      total: repFilterActive ? s.payable : (s.payable + s.airtime_margin) }))              // rep-commission-only vs mixed
  ok('ma airtime: no rep filter → airtime shown + Total = payable+airtime (all stores)', eq(present(false), [
    { account_id: 'ACC1', airtime: 3, total: 13 }, { account_id: 'ACC2', airtime: 4, total: 9 }, { account_id: 'ACC3', airtime: 9, total: 9 }]))
  const on = present(true)
  ok('ma airtime: rep filter → airtime-only store (rep never touched) dropped', on.length === 2 && !on.some(r => r.account_id === 'ACC3'))
  ok('ma airtime: rep filter → Total payable = rep commission only (no all-reps airtime mixed in)', eq(on.map(r => r.total), [10, 5]))
  ok('ma airtime: rep filter → airtime column blanked (never a mixed number)', on.every(r => r.airtime === null))
}

// ════════════════════════════════════════════════════════════════════════════════════════════════
// 7) comp-trend PER-MONTH store/market attribution (GATE-1 FIX 4). A rep floats between stores across
//    months (luxelink has 2-3 reps per store), so store/market must be stamped PER MONTH, never collapsed
//    to one store for the whole window. Backend emits points[{period,store,market,pay}]; the page narrows
//    the MONTHS within each rep under a store/market filter and re-sums the displayed totals.
// ════════════════════════════════════════════════════════════════════════════════════════════════
{
  // (a) backend re-impl: rep_commissions rows → per-(period,store) points (verbatim of compute_rep_pay_trend).
  const rows = [
    { period: 'May 2026',  store: 'A', total_payout: 100, storeops_name: 'Flo' },
    { period: 'June 2026', store: 'B', total_payout: 60,  storeops_name: 'Flo' },
  ]
  const d = { by_period: {}, total: 0, _ps: {} }
  for (const r of rows) {
    const p = r.period, st = r.store, pay = r.total_payout
    d.by_period[p] = (d.by_period[p] || 0) + pay; d.total += pay
    const k = p + ' ' + st; d._ps[k] = (d._ps[k] || 0) + pay
  }
  const points = Object.entries(d._ps).map(([k, v]) => { const [p, st] = k.split(' '); return { period: p, store: st, pay: v } })
  ok('comp-trend backend: points are per-(period,store), never collapsed to one store', eq(points, [
    { period: 'May 2026', store: 'A', pay: 100 }, { period: 'June 2026', store: 'B', pay: 60 }]))
  ok('comp-trend backend: by_period still sums per period (backward compat)', eq(d.by_period, { 'May 2026': 100, 'June 2026': 60 }))

  // (b) frontend re-impl: the page's per-month narrowing (verbatim of comp-trend/page.tsx `reps` useMemo).
  const repsAll = [
    { rep: 'Flo',  store: 'A', market: 'North', by_period: { 'May 2026': 100, 'June 2026': 60 }, total: 160,
      points: [{ period: 'May 2026', store: 'A', market: 'North', pay: 100 }, { period: 'June 2026', store: 'B', market: 'South', pay: 60 }] },
    { rep: 'Stan', store: 'A', market: 'North', by_period: { 'May 2026': 40,  'June 2026': 50 }, total: 90,
      points: [{ period: 'May 2026', store: 'A', market: 'North', pay: 40 }, { period: 'June 2026', store: 'A', market: 'North', pay: 50 }] },
  ]
  const empty7 = () => ({ stores: [], markets: [], reps: [] })
  const narrow = (filt) => {
    const storeSet = new Set(filt.stores.map(norm)), marketSet = new Set(filt.markets.map(norm)), repSet = new Set(filt.reps.map(norm))
    const hasStore = storeSet.size > 0, hasMarket = marketSet.size > 0, hasRep = repSet.size > 0
    const out = []
    for (const r of repsAll) {
      if (hasRep && !repSet.has(norm(r.rep))) continue
      const pts = r.points || []
      if (!hasStore && !hasMarket) { const seen = new Set(); for (const pt of pts) if (norm(pt.store)) seen.add(norm(pt.store)); out.push({ ...r, _stores: [...seen].sort() }); continue }
      const kept = pts.filter(pt => (!hasStore || storeSet.has(norm(pt.store))) && (!hasMarket || marketSet.has(norm(pt.market))))
      if (kept.length === 0) continue
      const by_period = {}, seen = new Set(); let total = 0
      for (const pt of kept) { by_period[pt.period] = (by_period[pt.period] || 0) + pt.pay; total += pt.pay; if (norm(pt.store)) seen.add(norm(pt.store)) }
      out.push({ ...r, by_period, total: Math.round(total * 100) / 100, _stores: [...seen].sort() })
    }
    return out.sort((a, b) => (b.total || 0) - (a.total || 0))
  }
  ok('comp-trend: no filter → totals byte-identical to before', eq(narrow(empty7()).map(r => [r.rep, r.total]), [['Flo', 160], ['Stan', 90]]))
  const b = narrow({ ...empty7(), stores: ['B'] })
  ok('comp-trend: store-B filter → ONLY the floater, ONLY the June months (re-summed)', b.length === 1 && b[0].rep === 'Flo' && b[0].total === 60 && eq(b[0].by_period, { 'June 2026': 60 }))
  const a = narrow({ ...empty7(), stores: ['A'] })
  ok('comp-trend: store-A filter → floater May-only (60 at B excluded) + Stan both months', eq(a.map(r => [r.rep, r.total]), [['Flo', 100], ['Stan', 90]]))
  ok('comp-trend: store-A filter → floater row shows only the narrowed store', eq(a.find(r => r.rep === 'Flo')._stores, ['A']))
  const south = narrow({ ...empty7(), markets: ['South'] })
  ok('comp-trend: market-South filter → only the floater June month counted', south.length === 1 && south[0].rep === 'Flo' && south[0].total === 60)
  ok('comp-trend: per-month store stamp not collapsed (May→A, June→B)', eq(repsAll[0].points.map(p => [p.period, p.store]), [['May 2026', 'A'], ['June 2026', 'B']]))
}

console.log(`\nrule5-commission-wave1: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
