#!/usr/bin/env node
// Proof harness — Borrowed Money store-picker registry fix (agent/asset/export-filter-fix,
// 2026-08-04, second task on the same branch).
//
// Owner report (verbatim): "i added cellular services as a store but it does not appear in the
// store list in the borrowed lending store list." Root cause: GET /asset/filter-options' `stores`
// field is derived ENTIRELY from asset_ledger rows, so a store with zero financed devices (exactly
// a store borrowing money to buy its FIRST inventory) can never appear in the Borrowed-Money
// borrower/lender pickers. Fix: additive `registry_stores` field (commcalc.store_mapping, the full
// tenant roster) + a client-side union on the ONE page that needs it (Borrowed Money's create form).
//
// This harness re-implements (in plain JS, verbatim logic) the two pure pieces touched:
//   [A] backend `_registry_stores` dedup/filter/sort (backend/app/modules/asset/router.py)
//   [B] frontend `pickerGroups` union/dedup (borrowed/page.tsx)
// plus STATIC source checks that the change is additive-only (existing keys untouched, no OTHER
// asset report page picked up `registry_stores`, org_id scoping present).

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { pyFunctionBody, pyReturnDict, pyDictKeys, pyStripStrings } from './_proof-source.mjs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(__dirname, '..', '..')
let n = 0
function ok(label) { n++; console.log(`  ok ${n}. ${label}`) }

console.log('=== asset borrowed-money registry-store proof ===\n')

// ── [0] STATIC — backend is additive-only, org-scoped ───────────────────────────────────────────
console.log('[0] backend: additive-only, org_id query-param scoped')
{
  const routerSrc = fs.readFileSync(path.join(REPO, 'backend/app/modules/asset/router.py'), 'utf8')

  // The existing response KEYS are still returned. Read through tools/_proof-source.mjs, which
  // finds the endpoint's return dict by what it IS. This check used to match
  // `/return \{"markets": sorted\(markets\)…/` and went red when the §13c canonical market
  // vocabulary union moved that `sorted()` into a variable — a false alarm about a value's
  // spelling, reported as "return statement found". The invariant was never "the first value is
  // written sorted(markets)"; it is "these keys are all still there". So that is what is asserted.
  const endpoint = pyFunctionBody(routerSrc, 'get_filter_options')
  assert.ok(endpoint, 'asset router defines the get_filter_options endpoint')
  const rb = pyReturnDict(endpoint)
  assert.ok(rb, `get_filter_options returns a dict literal (got: ${String(rb)})`)
  const keys = pyDictKeys(rb)
  for (const key of ['markets', 'stores', 'store_groups', 'no_market_count', 'no_market_value']) {
    assert.ok(keys.includes(key),
      `existing key "${key}" still returned for old consumers — returns [${keys.join(', ')}]`)
  }
  assert.ok(keys.includes('registry_stores'),
    `additive key "registry_stores" present — returns [${keys.join(', ')}]`)
  ok(`GET /filter-options: existing keys untouched, registry_stores is a pure addition (${keys.length} keys)`)

  // _registry_stores itself: org_id scoped read, is_active filtered, never raises into the caller.
  // Body read by INDENTATION, not by a `\n\n\n` terminator — the second instance of the same
  // brittleness, which would have broken on a change to the blank lines after the function.
  const fn = pyFunctionBody(routerSrc, '_registry_stores')
  assert.ok(fn, '_registry_stores function found')
  assert.ok(/\.eq\("org_id", org_id\)/.test(fn), 'store_mapping read is .eq("org_id", org_id)-scoped')
  assert.ok(/is_active.*is False/.test(fn) || /is_active.*False/.test(fn), 'inactive store_mapping rows excluded')

  // THE INVARIANT, not its spelling: nothing may escape this function, because the caller does NOT
  // wrap it — `registry_stores = _registry_stores(client, org_id)` is a bare call, so a raise here
  // would 500 the whole ledger-derived response every existing report filter depends on.
  // This was asserted as `/except Exception:\s*\n\s*return \[\]/` and went red when the function
  // was rewritten to degrade with `rows = []` and carry on into the storeops overlay — the SAME
  // promise, kept better. A spelling is not an invariant.
  const fnCode = pyStripStrings(fn)   // so the docstring's prose is not read as code
  assert.ok(/except\s+Exception/.test(fnCode), '_registry_stores catches its lookup failures')
  assert.ok(!/\braise\b/.test(fnCode),
    '_registry_stores never raises into its caller — the caller does not wrap it, so a raise here ' +
    '500s the whole /filter-options response')
  ok('_registry_stores is org-scoped, excludes inactive rows, and no exception escapes it')
}
console.log()

// ── [1] STATIC — scope discipline: no OTHER asset report page widened its picker ──────────────────
console.log('[1] scope: registry_stores consumed ONLY by the Borrowed Money create-form pickers')
{
  const assetDir = path.join(REPO, 'frontend/src/app/(platform)/commcalc/asset')
  const untouchedReportPages = [
    'page.tsx', 'aging/page.tsx', 'charges/rma/page.tsx', 'charges/[group]/page.tsx',
    'owed-weekly/page.tsx', 'on-inventory/page.tsx', 'oninv-3way-recon/page.tsx',
  ]
  for (const rel of untouchedReportPages) {
    const p = path.join(assetDir, rel)
    if (!fs.existsSync(p)) continue
    const src = fs.readFileSync(p, 'utf8')
    assert.ok(!src.includes('registry_stores'), `${rel}: report-filter store dropdown stays ledger-derived only`)
  }
  ok('no report-filter dropdown (aging/rma/charges/owed-weekly/on-inventory/3-way-recon) reads registry_stores')

  const borrowedSrc = fs.readFileSync(path.join(assetDir, 'borrowed/page.tsx'), 'utf8')
  assert.ok(borrowedSrc.includes('registry_stores'), 'borrowed/page.tsx reads registry_stores')
  assert.ok(/pickerGroups\.withAssets/.test(borrowedSrc) && /pickerGroups\.registryOnly/.test(borrowedSrc),
    'borrowed/page.tsx builds the union pickerGroups and uses both parts')
  // The report FILTER dropdown (fStore) is explicitly left ledger-derived, per scope carve-out.
  const filterSelectBlock = borrowedSrc.match(/value=\{fStore\}[\s\S]{0,200}/)[0]
  assert.ok(!filterSelectBlock.includes('registryOnly'), 'the fStore REPORT filter dropdown is untouched (still ledger-derived `stores`)')
  ok('borrowed/page.tsx: create-form pickers unioned; report filter dropdown deliberately left as-is')
}
console.log()

// ── [2] LOGIC — backend dedup/filter/sort (verbatim re-implementation of _registry_stores) ────────
console.log('[2] _registry_stores logic — dedupe, is_active filter, sort')
function registryStoresLogic(rows) {
  const out = []
  const seen = new Set()
  for (const r of rows) {
    if (r.is_active === false) continue
    const addr = (r.store_address || '').trim()
    if (!addr || seen.has(addr.toLowerCase())) continue
    seen.add(addr.toLowerCase())
    out.push({ store: addr, market: r.market ?? null })
  }
  out.sort((a, b) => a.store.localeCompare(b.store))
  return out
}
{
  const rows = [
    { store_address: 'Cellular Services', market: null, is_active: true },   // the owner's new store
    { store_address: 'Tri-State #1', market: 'Tri-State', is_active: true },
    { store_address: 'Closed Store', market: 'NJ', is_active: false },        // must be excluded
    { store_address: 'tri-state #1', market: 'Tri-State', is_active: true },  // case-dupe, must dedupe
  ]
  const out = registryStoresLogic(rows)
  assert.deepEqual(out.map(s => s.store), ['Cellular Services', 'Tri-State #1'], 'sorted, deduped, inactive excluded')
  assert.equal(out.find(s => s.store === 'Cellular Services').market, null, 'a store with no market saves/returns null (not synthesized)')
  ok('a brand-new, no-market, no-ledger-rows store ("Cellular Services") comes back from the registry query')
}
console.log()

// ── [3] LOGIC — frontend picker union (verbatim re-implementation of pickerGroups) ─────────────────
console.log('[3] pickerGroups union — ledger stores ∪ registry-only stores, ledger market wins')
function pickerGroupsLogic(stores, registryStores) {
  const seen = new Set(stores.map(s => s.store.trim().toLowerCase()))
  const registryOnly = registryStores.filter(s => s.store && !seen.has(s.store.trim().toLowerCase()))
  const all = [...stores, ...registryOnly].sort((a, b) => a.store.localeCompare(b.store))
  return { withAssets: stores, registryOnly, byName: new Map(all.map(s => [s.store, s])) }
}
{
  const stores = [{ store: 'Tri-State #1', market: 'Tri-State' }]           // ledger-derived (has assets)
  const registryStores = [
    { store: 'Cellular Services', market: null },                          // registry-only (the bug case)
    { store: 'Tri-State #1', market: 'Tri-State' },                        // also in ledger -> must NOT duplicate
  ]
  const g = pickerGroupsLogic(stores, registryStores)
  assert.equal(g.registryOnly.length, 1, 'exactly the ONE genuinely-new store is registry-only')
  assert.equal(g.registryOnly[0].store, 'Cellular Services', 'Cellular Services appears in the picker')
  assert.ok(!g.registryOnly.some(s => s.store === 'Tri-State #1'), 'a store already in `stores` is not duplicated into registryOnly')
  assert.equal(g.byName.get('Tri-State #1').market, 'Tri-State', 'a dual-listed store keeps its real ledger market')
  assert.equal(g.byName.get('Cellular Services').market, null, 'a registry-only store with no store_mapping market resolves to null (create endpoint tolerates it)')
  ok('union: registry-only store surfaces exactly once; ledger market wins for a dual-listed store; no-market resolves to null')
}
console.log()

console.log(`\n=== ${n}/${n} assertions green ===`)
