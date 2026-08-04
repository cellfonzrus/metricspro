// Proof for the pure market->store cascade logic (closing/_lib/market-store-cascade.ts). Verbatim re-impl.
// Run: node scratchpad/prove_market_store_cascade.mjs
let pass = 0, fail = 0
const ok = (name, cond) => { if (cond) pass++; else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl ────────────────────────────────────────────────────────────────────────────
const NO_MARKET_ID = '(no market)'
const norm = v => (v == null ? '' : String(v)).trim()
const foldKey = v => norm(v).toLowerCase()

function marketsFromStores(stores) {
  const m = new Map(); let hasUnmapped = false
  for (const s of stores) {
    const v = norm(s.market)
    if (v) { const k = foldKey(v); if (!m.has(k)) m.set(k, v) }
    else hasUnmapped = true
  }
  const out = [...m.values()].sort().map(v => ({ id: v, label: v }))
  if (hasUnmapped) out.push({ id: NO_MARKET_ID, label: NO_MARKET_ID })
  return out
}
function cascadeStores(stores, selectedMarkets) {
  if (!selectedMarkets.length) return stores
  const wanted = new Set(selectedMarkets.map(foldKey))
  return stores.filter(s => {
    const v = norm(s.market)
    if (!v) return wanted.has(foldKey(NO_MARKET_ID))
    return wanted.has(foldKey(v))
  })
}
function pruneSelectedStores(stores, selectedMarkets, selectedStoreIds) {
  if (!selectedMarkets.length || !selectedStoreIds.length) return selectedStoreIds
  const known = new Set(stores.map(s => s.id))
  const visible = new Set(cascadeStores(stores, selectedMarkets).map(s => s.id))
  const pruned = selectedStoreIds.filter(id => !known.has(id) || visible.has(id))
  return pruned.length === selectedStoreIds.length ? selectedStoreIds : pruned
}
function resolveStoreCodes(stores, selectedMarkets, selectedStoreIds) {
  if (selectedStoreIds.length) return selectedStoreIds
  if (selectedMarkets.length) return cascadeStores(stores, selectedMarkets).map(s => s.id)
  return []
}

// ── fixtures ────────────────────────────────────────────────────────────────────────────────────
const stores = [
  { id: 'S1', label: 'Main St', market: 'NY Metro' },
  { id: 'S2', label: 'Elm St', market: 'NY Metro' },
  { id: 'S3', label: 'Oak Ave', market: 'NJ' },
  { id: 'S4', label: 'Pine Rd', market: '' },       // unmapped
  { id: 'S5', label: 'Birch Blvd', market: null },  // unmapped
]

// marketsFromStores
const mkts = marketsFromStores(stores)
ok('markets: real markets sorted + dedup', JSON.stringify(mkts.map(m => m.id)) === JSON.stringify(['NJ', 'NY Metro', NO_MARKET_ID]))
ok('markets: no (no market) bucket when every store is mapped', marketsFromStores(stores.slice(0, 3)).every(m => m.id !== NO_MARKET_ID))

// cascadeStores
ok('cascade: no market selected -> every store, unfiltered', cascadeStores(stores, []).length === 5)
ok('cascade: one real market -> only its stores', cascadeStores(stores, ['NY Metro']).map(s => s.id).join(',') === 'S1,S2')
ok('cascade: case/whitespace-insensitive market match', cascadeStores(stores, [' ny metro ']).length === 2)
ok('cascade: two markets -> union', cascadeStores(stores, ['NY Metro', 'NJ']).length === 3)
ok('cascade: unmapped stores hidden when a REAL market is selected', !cascadeStores(stores, ['NJ']).some(s => s.id === 'S4' || s.id === 'S5'))
ok('cascade: (no market) bucket selected -> only unmapped stores', cascadeStores(stores, [NO_MARKET_ID]).map(s => s.id).sort().join(',') === 'S4,S5')
ok('cascade: real market + (no market) both selected -> union of both', cascadeStores(stores, ['NJ', NO_MARKET_ID]).length === 3)

// pruneSelectedStores
{
  const sel = ['S3']
  ok('prune: no markets selected -> selection untouched (identity, same array ref)', pruneSelectedStores(stores, [], sel) === sel)
}
{
  const sel = ['S1', 'S3']
  const pruned = pruneSelectedStores(stores, ['NY Metro'], sel)
  ok('prune: drops a selected store outside the newly-narrowed market', pruned.join(',') === 'S1')
}
{
  const sel = ['S1', 'S2']
  const pruned = pruneSelectedStores(stores, ['NY Metro'], sel)
  ok('prune: leaves selection untouched when everything still visible (same array, no needless re-render)', pruned === sel)
}
ok('prune: an id this picker has no info about is never dropped', pruneSelectedStores(stores, ['NY Metro'], ['UNKNOWN_ID']).includes('UNKNOWN_ID'))

// resolveStoreCodes (the "what does the backend get" contract, owner Q2)
ok('resolve: no market, no store -> empty (no store-scoping)', resolveStoreCodes(stores, [], []).length === 0)
ok('resolve: market picked, no explicit store -> the WHOLE market expands', resolveStoreCodes(stores, ['NY Metro'], []).join(',') === 'S1,S2')
ok('resolve: explicit store wins over market expansion', resolveStoreCodes(stores, ['NY Metro'], ['S1']).join(',') === 'S1')
ok('resolve: explicit store outside any market selection still respected (no market picked at all)', resolveStoreCodes(stores, [], ['S3']).join(',') === 'S3')

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
