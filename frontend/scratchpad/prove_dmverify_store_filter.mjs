// Proof for the Gate-1 B1 fix (2026-07-28) on SubmissionsTable.tsx's store-filter combinator.
// Verbatim re-impl of the pure logic (same convention as prove_standard_filters.mjs, referenced by
// src/lib/standard-filters.ts's own top comment). Run: node scratchpad/prove_dmverify_store_filter.mjs
//
// The bug: a PARENT-supplied `storeOptions` list is canonical (id = store_code, from GET
// /closing/stores), a different value-space than this component's own self-sourced options
// (id = store_address, from optionsFromRows). Routing ALL filtering (including store) through the
// shared `filterRows`'s address-keyed accessor meant a canonical store_code selection compared
// against a row's store_address and matched NOTHING — every store selection silently emptied the
// tab and every export. Section A below reproduces that exact failure against the UNCHANGED shared
// `filterRows`; Section B proves the fix (a store-specific combinator, value-space aware, with the
// backend's own "an unresolved store is never dropped" bypass in canonical mode).
let pass = 0, fail = 0
const ok = (name, cond) => { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl of src/lib/standard-filters.ts (the SHARED file, unedited by this package) ──
const norm = v => (v == null ? '' : String(v)).trim()
const foldKey = v => norm(v).toLowerCase()
function matchesStandardFilter(row, sel, acc) {
  if (sel.stores.length && acc.store) { const k = foldKey(acc.store(row)); if (!sel.stores.some(s => foldKey(s) === k)) return false }
  if (sel.markets.length && acc.market) { const k = foldKey(acc.market(row)); if (!sel.markets.some(m => foldKey(m) === k)) return false }
  if (sel.reps.length && acc.rep) { const k = foldKey(acc.rep(row)); if (!sel.reps.some(r => foldKey(r) === k)) return false }
  return true
}
const filterRows = (rows, sel, acc) => rows.filter(r => matchesStandardFilter(r, sel, acc))

// ── verbatim re-impl of SubmissionsTable.tsx's NEW `rows` combinator (post-fold) ──────────────────
function filterSubmissionRows(rawRows, filt, usingCanonicalStores) {
  const acc = { store: r => r.store_address, market: r => r.market, rep: r => r.employee_name }
  const afterMarketRep = filterRows(rawRows, filt, { market: acc.market, rep: acc.rep })
  if (!filt.stores.length) return afterMarketRep
  const wanted = new Set(filt.stores.map(s => s.trim().toLowerCase()))
  return afterMarketRep.filter(r => {
    if (usingCanonicalStores) {
      const code = String(r.store_code || '').trim()
      if (!code) return true
      return wanted.has(code.toLowerCase())
    }
    return wanted.has(String(r.store_address || '').trim().toLowerCase())
  })
}

const emptyFilt = () => ({ period: '', periodTo: '', stores: [], markets: [], reps: [] })

const rows = [
  { id: 's1a', store_code: 'S1', store_address: '1 Main St', market: 'Texas', employee_name: 'Jane Rep' },
  { id: 's1b', store_code: 'S1', store_address: '1 Main St', market: 'Texas', employee_name: 'John Rep' },
  { id: 's2', store_code: 'S2', store_address: '2 Oak Ave', market: 'Ohio', employee_name: 'Mo Rep' },
  { id: 'unresolved', store_code: null, store_address: 'Unmapped Store', market: '(no market)', employee_name: 'Ghost Rep' },
]

// ── A. Reproduce the ORIGINAL bug: canonical (store_code) selection through the UNCHANGED shared
//    filterRows with an address-keyed accessor — must match nothing. ────────────────────────────
{
  const filt = { ...emptyFilt(), stores: ['S1'] }   // a store_code, as a canonical picker would emit
  const broken = filterRows(rows, filt, { store: r => r.store_address, market: r => r.market, rep: r => r.employee_name })
  ok('A1. reproduces the original bug — address-keyed accessor + a code selection matches ZERO rows',
     broken.length === 0)
}

// ── B. The fix — canonical mode compares store_code, bypasses the unresolved row ──────────────────
{
  const filt = { ...emptyFilt(), stores: ['S1'] }
  const fixed = filterSubmissionRows(rows, filt, /* usingCanonicalStores */ true)
  const ids = fixed.map(r => r.id).sort()
  ok('B1. canonical mode: stores=[S1] -> both S1 rows + the unresolved row (never dropped)',
     JSON.stringify(ids) === JSON.stringify(['s1a', 's1b', 'unresolved']))
}
{
  const filt = { ...emptyFilt(), stores: ['S2'] }
  const fixed = filterSubmissionRows(rows, filt, true)
  const ids = fixed.map(r => r.id).sort()
  ok('B2. canonical mode: stores=[S2] -> S2 + the unresolved row, S1 rows dropped',
     JSON.stringify(ids) === JSON.stringify(['s2', 'unresolved']))
}
{
  // A real (non-canonical) store id that just isn't selected must still be excluded — only a TRUE
  // unresolved row (no store_code) bypasses the filter.
  const filt = { ...emptyFilt(), stores: ['S1', 'S2'] }
  const fixed = filterSubmissionRows(rows, filt, true)
  ok('B3. canonical mode: every resolved store selected -> unresolved row still included, nothing else lost',
     fixed.length === rows.length)
}

// ── C. Standalone mode (no parent props) — unchanged retail-ops-13 behavior: options/filter values
//    are store_address strings (self-sourced via optionsFromRows), matched as before. ─────────────
{
  const filt = { ...emptyFilt(), stores: ['1 Main St'] }
  const std = filterSubmissionRows(rows, filt, /* usingCanonicalStores */ false)
  const ids = std.map(r => r.id).sort()
  ok('C1. standalone mode: an address selection matches the same rows it always did',
     JSON.stringify(ids) === JSON.stringify(['s1a', 's1b']))
}

// ── D. Market/rep still delegate to the real, unedited shared filterRows ───────────────────────────
{
  const filt = { ...emptyFilt(), markets: ['Ohio'] }
  const out = filterSubmissionRows(rows, filt, true)
  ok('D1. market filter unaffected by the store-filter fold', out.map(r => r.id).join(',') === 's2')
}
{
  const filt = { ...emptyFilt(), reps: ['Jane Rep'] }
  const out = filterSubmissionRows(rows, filt, true)
  ok('D2. rep filter unaffected by the store-filter fold', out.map(r => r.id).join(',') === 's1a')
}

console.log(`\n${pass}/${pass + fail} checks passed`)
if (fail) process.exit(1)
