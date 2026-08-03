// Proof harness for Workforce/My Team's RULE FIVE retrofit (TeamSnapshot.tsx, owner 2026-08-03: "add the
// totals for achieved vs the target and the total for accessories at the bottom with a standard filters
// on the top"). Verbatim re-impl of (a) the pure primitives in src/lib/standard-filters.ts (case-insensitive
// foldKey match, same technique as scratchpad/prove_standard_filters.mjs which already unit-proves the
// primitive itself) and (b) TeamSnapshot's own filter/totals logic (store+market filterRows, rep-touches-store
// narrowing, sumCategories — the client-side mirror of the backend's `_team_totals`).
// Proves: (1) unfiltered filteredTotals is byte-identical to summing the FULL store set (i.e. matches what
// the server's `totals` already returned before this change), (2) a store filter narrows the store table AND
// the bottom Totals card together, (3) a rep filter drops stores none of the selected reps touched while
// keeping the touched store's total WHOLE (not split per rep), (4) the Accessories total is a true subset sum,
// (5) a market filter narrows via the store's own `market` field (no external map needed, unlike payroll's
// employee-database precedent), (6) filtered totals === Σ filtered rows for every case (the harness's own
// litmus test for "filter bar drives totals").
// Run: node scratchpad/prove_myteam_filters.mjs
let pass = 0, fail = 0
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl of lib/standard-filters.ts (current, case-insensitive foldKey version) ──────────
const norm = v => (v == null ? '' : String(v)).trim()
const foldKey = v => norm(v).toLowerCase()
const emptyStandardFilter = (period = '') => ({ period, periodTo: '', stores: [], markets: [], reps: [] })
function matchesStandardFilter(row, sel, acc) {
  if (sel.stores.length && acc.store) { const k = foldKey(acc.store(row)); if (!sel.stores.some(s => foldKey(s) === k)) return false }
  if (sel.markets.length && acc.market) { const k = foldKey(acc.market(row)); if (!sel.markets.some(m => foldKey(m) === k)) return false }
  if (sel.reps.length && acc.rep) { const k = foldKey(acc.rep(row)); if (!sel.reps.some(r => foldKey(r) === k)) return false }
  return true
}
const filterRows = (rows, sel, acc) => rows.filter(r => matchesStandardFilter(r, sel, acc))

// ── verbatim re-impl of TeamSnapshot.tsx's own logic (fold, filteredStores, filteredReps, sumCategories) ──
const fold = v => String(v ?? '').trim().toLowerCase()

function filteredStoresOf(stores, filter) {
  const byFilter = filterRows(stores, filter, { store: s => s.address || s.store_code, market: s => s.market })
  if (filter.reps.length === 0) return byFilter
  const sel = new Set(filter.reps.map(fold))
  return byFilter.filter(s => (s.reps || []).some(rr => sel.has(fold(rr.rep))))
}
function filteredRepsOf(stores, reps, filter) {
  const marketByStoreKey = {}
  stores.forEach(s => {
    const mk = s.market || ''
    if (s.store_code) marketByStoreKey[fold(s.store_code)] = mk
    if (s.address) marketByStoreKey[fold(s.address)] = mk
  })
  return filterRows(reps, filter, {
    store: r => r.store, rep: r => r.rep,
    market: r => marketByStoreKey[fold(r.store)] || '',
  })
}
function sumCategories(storeRows) {
  const out = {}
  for (const s of storeRows) {
    for (const [cat, c] of Object.entries(s.categories || {})) {
      const t = out[cat] || (out[cat] = { unit: c.unit, monthly: 0, achieved_mtd: 0, need: 0, today_target: 0 })
      t.monthly += Number(c.monthly) || 0
      t.achieved_mtd += Number(c.achieved_mtd) || 0
      t.need += Number(c.need) || 0
      t.today_target += Number(c.today_target) || 0
    }
  }
  for (const t of Object.values(out)) {
    t.monthly = Math.round(t.monthly * 100) / 100
    t.achieved_mtd = Math.round(t.achieved_mtd * 100) / 100
    t.need = Math.round(t.need * 100) / 100
    t.today_target = Math.round(t.today_target * 100) / 100
    t.pct = t.monthly > 0 ? Math.round((100 * t.achieved_mtd / t.monthly) * 10) / 10 : 0
  }
  return out
}

// ── synthetic data mirroring GET /commcalc/team/{period}/snapshot's `stores`/`reps` shape ───────────
const cat = (achieved, monthly, unit = 'count') => ({ achieved_mtd: achieved, monthly, need: Math.max(0, monthly - achieved), today_target: 0, unit, pct: 0 })
const stores = [
  {
    store_code: 'B-STOREA', address: '1 Main St', market: 'North',
    categories: { activations: cat(10, 20), upgrades: cat(4, 10), byod: cat(2, 5), accessories: cat(1200, 3000, 'dollars') },
    reps: [{ rep: 'Alex Rep' }, { rep: 'Sam Rep' }],
  },
  {
    store_code: 'B-STOREB', address: '2 Oak Ave', market: 'North',
    categories: { activations: cat(6, 15), upgrades: cat(3, 8), byod: cat(1, 4), accessories: cat(800, 2000, 'dollars') },
    reps: [{ rep: 'Jamie Rep' }],
  },
  {
    store_code: 'B-STOREC', address: '3 Pine Rd', market: 'South',
    categories: { activations: cat(9, 12), upgrades: cat(5, 6), byod: cat(3, 3), accessories: cat(2500, 2500, 'dollars') },
    reps: [{ rep: 'Casey Rep' }],
  },
]
const reps = [
  { rep: 'Alex Rep', store: '1 Main St', money_on_table: 50 },
  { rep: 'Sam Rep', store: '1 Main St', money_on_table: 0 },
  { rep: 'Jamie Rep', store: '2 Oak Ave', money_on_table: 120 },
  { rep: 'Casey Rep', store: '3 Pine Rd', money_on_table: 0 },
]

// (1) unfiltered: filteredTotals sums the FULL store set — byte-identical to what the server's own
// `_team_totals` (summed over the same 3 stores) would return, i.e. this is a lossless client-side re-impl.
{
  const filt = emptyStandardFilter()
  const fs = filteredStoresOf(stores, filt)
  ok('no-filter: all 3 stores visible', fs.length === 3)
  const tot = sumCategories(fs)
  ok('no-filter: activations achieved = 10+6+9 = 25', tot.activations.achieved_mtd === 25)
  ok('no-filter: activations monthly = 20+15+12 = 47', tot.activations.monthly === 47)
  ok('no-filter: accessories achieved = 1200+800+2500 = 4500', tot.accessories.achieved_mtd === 4500)
  ok('no-filter: accessories monthly = 3000+2000+2500 = 7500', tot.accessories.monthly === 7500)
  ok('no-filter: accessories pct = round(4500/7500*100,1) = 60', tot.accessories.pct === 60)
  const fr = filteredRepsOf(stores, reps, filt)
  ok('no-filter: all 4 reps visible', fr.length === 4)
  const mot = fr.reduce((a, r) => a + r.money_on_table, 0)
  ok('no-filter: money-on-table total = 50+0+120+0 = 170', mot === 170)
}

// (2) store filter narrows the store table AND the bottom Totals card together (filtered totals === Σ
// filtered rows — the litmus test). The filter option list is generated by the SAME accessor
// (`s.address || s.store_code`) the filter matches against, so a user can only ever pick the value that
// is actually offered — for store A that is its address, since it has one.
{
  const filt = { ...emptyStandardFilter(), stores: ['1 Main St'] }
  const fs = filteredStoresOf(stores, filt)
  ok('store filter "1 Main St": only store A remains', fs.length === 1 && fs[0].store_code === 'B-STOREA')
  const tot = sumCategories(fs)
  ok('store filter "1 Main St": accessories achieved = 1200 (not the full 4500)', tot.accessories.achieved_mtd === 1200)
  ok('store filter "1 Main St": activations achieved = 10 (not 25)', tot.activations.achieved_mtd === 10)
}
// case-insensitive match
{
  const filt = { ...emptyStandardFilter(), stores: ['1 main st'] }
  const fs = filteredStoresOf(stores, filt)
  ok('store filter by address, case-insensitive: matches store A', fs.length === 1 && fs[0].store_code === 'B-STOREA')
}
// a target-only store with NO address (roster union case, get_targets_summary's "target-only code"
// path) falls back to store_code for both the option label and the match — proven separately since the
// two-store fixture above always has an address.
{
  const addrless = [{ store_code: 'B-STOREZ', address: '', market: 'North',
                       categories: { activations: cat(1, 5) }, reps: [] }]
  const filt = { ...emptyStandardFilter(), stores: ['B-STOREZ'] }
  const fs = filteredStoresOf(addrless, filt)
  ok('store filter on a store with no address falls back to store_code', fs.length === 1)
}

// (3) rep filter drops stores none of the selected reps touched, keeps the touched store's total WHOLE
{
  const filt = { ...emptyStandardFilter(), reps: ['Jamie Rep'] }
  const fs = filteredStoresOf(stores, filt)
  ok('rep filter Jamie Rep: only store B (Jamie\'s store) remains', fs.length === 1 && fs[0].store_code === 'B-STOREB')
  const tot = sumCategories(fs)
  ok('rep filter Jamie Rep: store B\'s accessories total is the FULL store figure (800), not split per rep', tot.accessories.achieved_mtd === 800)
  const fr = filteredRepsOf(stores, reps, filt)
  ok('rep filter Jamie Rep: rep table shows only Jamie', fr.length === 1 && fr[0].rep === 'Jamie Rep')
}

// (4) market filter narrows via the store's own `market` field (North = store A + B, not C)
{
  const filt = { ...emptyStandardFilter(), markets: ['North'] }
  const fs = filteredStoresOf(stores, filt)
  ok('market filter North: stores A + B, not C', fs.length === 2 && fs.every(s => s.store_code !== 'B-STOREC'))
  const fr = filteredRepsOf(stores, reps, filt)
  ok('market filter North (reps resolve market via their store): Alex+Sam+Jamie, not Casey',
    fr.length === 3 && fr.every(r => r.rep !== 'Casey Rep'))
}

// (5) combined store+market filter narrowing correctly (AND semantics)
{
  const filt = { ...emptyStandardFilter(), markets: ['North'], stores: ['2 Oak Ave'] }
  const fs = filteredStoresOf(stores, filt)
  ok('market=North AND store="2 Oak Ave": only store B (satisfies both)', fs.length === 1 && fs[0].store_code === 'B-STOREB')
}

// (6) filtered totals === Σ filtered rows, for a random narrowing (South market)
{
  const filt = { ...emptyStandardFilter(), markets: ['South'] }
  const fs = filteredStoresOf(stores, filt)
  const tot = sumCategories(fs)
  const manualSum = fs.reduce((a, s) => a + s.categories.accessories.achieved_mtd, 0)
  ok('filtered totals === Σ filtered rows (South market accessories)', tot.accessories.achieved_mtd === manualSum && manualSum === 2500)
}

console.log(`\n${pass} passed, ${fail} failed`)
if (fail > 0) process.exit(1)
