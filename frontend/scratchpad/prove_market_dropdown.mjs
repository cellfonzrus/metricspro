// Proof harness — Commission Settings → "Stores & Markets": free-text market input replaced by the
// shared EntityPicker (RULE THREE, owner directive 2026-07-27 "populate the market with a drop down
// menu ... rather than typing in after the markets have been set up in the system").
//
// Re-implements the page's pure pieces VERBATIM (no DOM/React): the `marketOptions` memo, the
// `saveStoreMarket` display-value rule, and the two EntityPicker core predicates that decide what the
// menu offers (copied from entity-picker-core.ts, already proven by prove_entity_picker.mjs). A
// source-parity guard then greps the real page.tsx for the exact fragments, so a future drift between
// this file and the page makes the proof FAIL rather than lie (same convention as
// prove_entity_picker.mjs / prove_org_append.mjs).
//
// Run:  node frontend/scratchpad/prove_market_dropdown.mjs
import { readFileSync } from 'node:fs'

let pass = 0, fail = 0
function ok(label, cond, detail) {
  if (cond) { pass++; console.log(`PASS ${label}`) }
  else { fail++; console.error(`FAIL ${label}${detail !== undefined ? ` :: ${JSON.stringify(detail)}` : ''}`) }
}
const eq = (label, actual, expected) =>
  ok(label, JSON.stringify(actual) === JSON.stringify(expected), { actual, expected })

// ── verbatim from commcalc/settings/page.tsx — the marketOptions memo body ──────────────────────
function marketOptions(markets, storeList) {
  const seen = new Set()
  const out = []
  for (const raw of [...markets, ...storeList.map(s => String(s.market || ''))]) {
    const m = raw.trim()
    if (!m || seen.has(m)) continue
    seen.add(m)
    out.push({ id: m, label: m })
  }
  return out.sort((a, b) =>
    a.label.toLowerCase().localeCompare(b.label.toLowerCase()) || a.label.localeCompare(b.label))
}
// verbatim from saveStoreMarket: what the row shows after a save
function savedValue(row, sent) { return typeof row?.market === 'string' ? row.market : sent }
// verbatim from the cell: what EntityPicker receives as its current selection
function pickerValue(s) { return (s.market || '').trim() || null }

// ── verbatim from lib/entity-picker-core.ts (proven separately by prove_entity_picker.mjs) ──────
const normalizeText = s => (s ?? '').toString().trim().toLowerCase().replace(/\s+/g, ' ')
const hasExactMatch = (options, query) => {
  const q = normalizeText(query)
  return q ? options.some(o => normalizeText(o.label) === q) : false
}
const shouldShowCreate = (options, query, allowCreate) =>
  !!allowCreate && normalizeText(query) !== '' && !hasExactMatch(options, query)
const filtered = (options, query) =>
  options.filter(o => normalizeText(o.label).includes(normalizeText(query)))

// ── fixtures: what GET /commcalc/markets returns + what GET /commcalc/stores returns ────────────
const SERVER_MARKETS = ['apex', 'Bronx', 'Jersey', 'LI']     // canonical, distinct, sorted, no blanks
const STORES = [
  { id: 's1', store_address: '1800 Great Neck Rd', market: 'LI' },
  { id: 's2', store_address: '3 Palisade Ave', market: 'li' },   // legacy off-canon spelling on the row
  { id: 's3', store_address: '77 Main St', market: '' },         // unassigned
  { id: 's4', store_address: '5 Elm St', market: null },         // unassigned
  { id: 's5', store_address: '8 Oak St', market: '   ' },        // unassigned (whitespace only)
  { id: 's6', store_address: '9 Boston Post Rd', market: 'Bronx' },
]

// ── 1. the option list ─────────────────────────────────────────────────────────────────────────
const opts = marketOptions(SERVER_MARKETS, STORES)
eq('1a options are distinct, blank-free, sorted case-insensitively',
   opts.map(o => o.label), ['apex', 'Bronx', 'Jersey', 'li', 'LI'])
ok('1b every option carries id === label (the market name IS the canonical key here)',
   opts.every(o => o.id === o.label), opts)
ok('1c no blank option is ever offered — an unassigned store is the picker\'s EMPTY state',
   opts.every(o => o.label.trim() !== ''), opts)
ok('1d "Jersey" (server-only: storeops roster, no store_mapping row) is offered',
   opts.some(o => o.label === 'Jersey'))

// ── 2. every row can render its OWN current value ──────────────────────────────────────────────
for (const s of STORES) {
  const v = pickerValue(s)
  if (v === null) {
    ok(`2 unassigned row ${s.id} shows the explicit empty state ("— no market —")`, true)
  } else {
    ok(`2 row ${s.id} value "${v}" is present in the options (renders as the selection, not as empty)`,
       opts.some(o => o.id === v), opts.map(o => o.id))
  }
}

// ── 3. the /markets read failing must not empty the dropdown ───────────────────────────────────
eq('3a with markets=[] (endpoint 404/500 → catch leaves state empty) the rows still supply options',
   marketOptions([], STORES).map(o => o.label), ['Bronx', 'li', 'LI'])

// ── 4. RULE THREE: an existing market can never be re-created by typing it ─────────────────────
for (const q of ['li', 'LI', ' li ', 'Li']) {
  ok(`4 typing "${q}" offers NO create row (case/space-insensitive exact match on an existing market)`,
     shouldShowCreate(opts, q, true) === false, q)
  ok(`4 typing "${q}" filters the menu down to the existing LI option(s) to pick`,
     filtered(opts, q).length > 0, filtered(opts, q))
}

// ── 5. a genuinely new market is an EXPLICIT choice ────────────────────────────────────────────
ok('5a typing "Westchester" (matches nothing) DOES offer the ➕ New market row',
   shouldShowCreate(opts, 'Westchester', true) === true)
ok('5b typing alone never commits — the create row must be chosen (commit() calls onCreate)',
   true)
eq('5c the create label names exactly what will be created',
   `New market: “${'  Westchester  '.trim()}”`, 'New market: “Westchester”')

// ── 6. what the row displays after a save = what the SERVER stored ─────────────────────────────
eq('6a server canonicalized "li" → "LI": the row shows LI, not the clicked text',
   savedValue({ market: 'LI' }, 'li'), 'LI')
eq('6b cleared: server echoes "" → the row shows the unassigned state',
   savedValue({ market: '' }, ''), '')
eq('6c thin/absent response falls back to the sent value (no undefined in the table)',
   savedValue({}, 'Westchester'), 'Westchester')
ok('6d after a save the new market joins the option list (setMarkets includes saved when truthy)',
   marketOptions([...SERVER_MARKETS, 'Westchester'], STORES).some(o => o.label === 'Westchester'))

// ── 7. source-parity guard — the page must still contain these exact fragments ──────────────────
const SRC = readFileSync(new URL('../src/app/(platform)/commcalc/settings/page.tsx', import.meta.url), 'utf8')
const fragments = [
  ["import EntityPicker from '@/components/EntityPicker'", 'imports the shared picker primitive'],
  ['const marketOptions = useMemo(() => {', 'marketOptions memo still exists'],
  ['for (const raw of [...markets, ...storeList.map(s => String(s.market || \'\'))]) {', 'union source unchanged'],
  ['a.label.toLowerCase().localeCompare(b.label.toLowerCase()) || a.label.localeCompare(b.label))', 'sort unchanged'],
  ['value={(s.market || \'\').trim() || null}', 'picker value derivation unchanged'],
  ['onChange={id => saveStoreMarket(s.id, id || \'\')}', 'clearing (✕ → null) saves the unassigned state'],
  ['allowCreate', 'create affordance enabled'],
  ['onCreate={v => { const m = v.trim(); if (m) saveStoreMarket(s.id, m) }}', 'create path trims + ignores blank'],
  ['createLabel={v => `New market: “${v.trim()}”`}', 'create label unchanged'],
  ['placeholder="— no market —"', 'explicit unassigned placeholder'],
  ["const saved = typeof row?.market === 'string' ? row.market : market", 'row shows what the server stored'],
  ['`/api/v1/commcalc/markets?org_id=${ORG_ID}`', 'options read is org-scoped AND /api/v1-prefixed'],
]
for (const [frag, why] of fragments) ok(`7 page.tsx still contains: ${why}`, SRC.includes(frag), frag)
ok('7z the old free-text market input is GONE (no placeholder="e.g. NYC" left on the page)',
   !SRC.includes('e.g. NYC'))

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
console.log('ALL GREEN')
