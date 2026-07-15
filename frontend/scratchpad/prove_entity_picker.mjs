// Proof harness for EntityPicker's pure logic (RULE THREE — pick, don't type).
// Re-implements the functions from src/lib/entity-picker-core.ts VERBATIM (no DOM/React), drives the
// required case table, then a source-parity guard greps the .ts for the exact bodies (same convention
// as prove_org_append.mjs). Run:  node frontend/scratchpad/prove_entity_picker.mjs
import { readFileSync } from 'node:fs'

// ── verbatim from entity-picker-core.ts ─────────────────────────────────────────────────────────────
function normalizeText(s) {
  return (s ?? '').toString().trim().toLowerCase().replace(/\s+/g, ' ')
}
function matchesQuery(haystack, query) {
  const q = normalizeText(query)
  if (!q) return true
  return normalizeText(haystack).includes(q)
}
function computeDisplays(options) {
  const counts = {}
  for (const o of options) {
    const k = normalizeText(o.label)
    counts[k] = (counts[k] || 0) + 1
  }
  const out = {}
  for (const o of options) {
    const k = normalizeText(o.label)
    out[o.id] = counts[k] > 1 && o.sublabel ? `${o.label} — ${o.sublabel}` : o.label
  }
  return out
}
function hasExactMatch(options, query) {
  const q = normalizeText(query)
  if (!q) return false
  return options.some(o => normalizeText(o.label) === q)
}
function shouldShowCreate(options, query, allowCreate) {
  return !!allowCreate && normalizeText(query) !== '' && !hasExactMatch(options, query)
}
function bigrams(s) {
  const b = []
  for (let i = 0; i < s.length - 1; i++) b.push(s.slice(i, i + 2))
  return b
}
function similarity(a, b) {
  const na = normalizeText(a), nb = normalizeText(b)
  if (na === nb) return 1
  const ba = bigrams(na), bb = bigrams(nb)
  if (ba.length === 0 || bb.length === 0) return 0
  const bag = {}
  for (const g of ba) bag[g] = (bag[g] || 0) + 1
  let inter = 0
  for (const g of bb) if (bag[g] > 0) { inter++; bag[g]-- }
  return (2 * inter) / (ba.length + bb.length)
}
function closest(options, query, limit = 5) {
  const q = normalizeText(query)
  if (!q) return []
  return options
    .map(o => ({ o, s: similarity(o.label, query) }))
    .filter(x => x.s > 0)
    .sort((a, b) => b.s - a.s || normalizeText(a.o.label).localeCompare(normalizeText(b.o.label)))
    .slice(0, limit)
    .map(x => x.o)
}
function buildMenu(options, query, allowCreate) {
  const displays = computeDisplays(options)
  const filtered = options.filter(o => matchesQuery(o.label, query) || (o.sublabel ? matchesQuery(o.sublabel, query) : false))
  const rows = []
  for (const o of filtered) rows.push({ kind: 'option', id: o.id, display: displays[o.id], option: o })
  const showCreate = shouldShowCreate(options, query, allowCreate)
  if (filtered.length === 0 && !showCreate) {
    rows.push({ kind: 'empty', message: normalizeText(query) ? 'No match' : 'No options' })
    for (const o of closest(options, query)) rows.push({ kind: 'suggest', id: o.id, display: displays[o.id], option: o })
  }
  if (showCreate) rows.push({ kind: 'create', value: query.trim() })
  return rows
}
function resolveRow(row) {
  if (row.kind === 'create') return { create: true, value: row.value }
  if (row.kind === 'option' || row.kind === 'suggest') return { create: false, id: row.id, option: row.option }
  return null
}
// ── multi-select helpers (verbatim from entity-picker-core.ts) ───────────────────────────────────────
function excludeSelected(options, selectedIds) {
  const chosen = new Set(selectedIds)
  return options.filter(o => !chosen.has(o.id))
}
function buildMenuMulti(options, query, allowCreate, selectedIds) {
  const remaining = excludeSelected(options, selectedIds)
  const displays = computeDisplays(options)
  const filtered = remaining.filter(o => matchesQuery(o.label, query) || (o.sublabel ? matchesQuery(o.sublabel, query) : false))
  const rows = []
  for (const o of filtered) rows.push({ kind: 'option', id: o.id, display: displays[o.id], option: o })
  const showCreate = shouldShowCreate(options, query, allowCreate)
  if (filtered.length === 0 && !showCreate) {
    rows.push({ kind: 'empty', message: normalizeText(query) ? 'No match' : 'No options' })
    for (const o of closest(remaining, query)) rows.push({ kind: 'suggest', id: o.id, display: displays[o.id], option: o })
  }
  if (showCreate) rows.push({ kind: 'create', value: query.trim() })
  return rows
}
function addSelection(selectedIds, id) {
  return selectedIds.includes(id) ? selectedIds : [...selectedIds, id]
}
function removeSelection(selectedIds, id) {
  return selectedIds.filter(x => x !== id)
}
function selectedChips(options, selectedIds) {
  const displays = computeDisplays(options)
  return selectedIds.map(id => ({ id, display: displays[id] ?? id }))
}

// ── harness ─────────────────────────────────────────────────────────────────────────────────────────
let pass = 0, fail = 0
function check(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want)
  const ok = g === w
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `\n        got:  ${g}\n        want: ${w}`}`)
  ok ? pass++ : fail++
}
function ok(name, cond) {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}`)
  cond ? pass++ : fail++
}
const ids = rows => rows.filter(r => r.kind === 'option').map(r => r.id)
const kinds = rows => rows.map(r => r.kind)

// datasets
const STATES = [
  { id: 'IL', label: 'Illinois (IL)' }, { id: 'IN', label: 'Indiana (IN)' },
  { id: 'CA', label: 'California (CA)' }, { id: 'NY', label: 'New York (NY)' },
]
const PEOPLE = [
  { id: 'e1', label: 'John Smith', sublabel: 'john.s@shop.com' },
  { id: 'e2', label: 'John Smith', sublabel: 'jsmith@shop.com' },
  { id: 'e3', label: 'Maria Cruz', sublabel: 'maria@shop.com' },
]
const STORES = [
  { id: 's1', label: 'Aurora Main' }, { id: 's2', label: 'Naperville' }, { id: 's3', label: 'Joliet' },
]

console.log('── A. filter: case/whitespace-insensitive contains-match ──')
check('type "illinois" → Illinois only (case-insensitive)', ids(buildMenu(STATES, 'illinois', false)), ['IL'])
check('type "IL" (code) → Illinois only', ids(buildMenu(STATES, 'IL', false)), ['IL'])
check('type "  new   york " (whitespace-insensitive) → New York only', ids(buildMenu(STATES, '  new   york ', false)), ['NY'])
check('type "in" (contains) → Illinois + Indiana', ids(buildMenu(STATES, 'in', false)).sort(), ['IL', 'IN'])
check('empty query → full list', ids(buildMenu(STATES, '', false)), ['IL', 'IN', 'CA', 'NY'])
check('filter by email substring → the right person', ids(buildMenu(PEOPLE, 'jsmith@', false)), ['e2'])

console.log('\n── B. same-name disambiguation: sublabel appended to BOTH ──')
{
  const d = computeDisplays(PEOPLE)
  check('both John Smiths get email appended', [d.e1, d.e2], ['John Smith — john.s@shop.com', 'John Smith — jsmith@shop.com'])
  check('unique name stays bare (no email)', d.e3, 'Maria Cruz')
}
{
  // disambiguation is computed over the FULL set even when only one collision is filtered in
  const rows = buildMenu(PEOPLE, 'john.s@', false)   // filters to e1 only, but e1 is ambiguous by name
  check('ambiguous name still shows email when only one is visible', rows.filter(r => r.kind === 'option').map(r => r.display), ['John Smith — john.s@shop.com'])
}
{
  // no sublabel available → cannot disambiguate, label stays bare (documented limitation)
  const dupNoEmail = [{ id: 'x1', label: 'Aurora' }, { id: 'x2', label: 'Aurora' }]
  const d = computeDisplays(dupNoEmail)
  check('duplicate label w/o sublabel → left bare (best effort)', [d.x1, d.x2], ['Aurora', 'Aurora'])
}

console.log('\n── C. create affordance: only when allowCreate && no exact match ──')
ok('allowCreate=false → never a create row', !buildMenu(STORES, 'Elgin', false).some(r => r.kind === 'create'))
ok('allowCreate=true + no match → create row present', buildMenu(STORES, 'Elgin', true).some(r => r.kind === 'create'))
ok('allowCreate=true + EXACT match → NO create row (no dup)', !buildMenu(STORES, 'Aurora Main', true).some(r => r.kind === 'create'))
ok('exact match is case/space-insensitive → "  aurora   main " suppresses create', !buildMenu(STORES, '  aurora   main ', true).some(r => r.kind === 'create'))
ok('allowCreate=true + partial match → still offers create alongside matches', (() => {
  const rows = buildMenu(STORES, 'Aur', true)          // matches "Aurora Main" but not exactly
  return rows.some(r => r.kind === 'option' && r.id === 's1') && rows.some(r => r.kind === 'create')
})())
ok('allowCreate=true + empty query → NO create row', !buildMenu(STORES, '', true).some(r => r.kind === 'create'))
check('create row carries the TRIMMED typed value', buildMenu(STORES, '  Elgin  ', true).find(r => r.kind === 'create'), { kind: 'create', value: 'Elgin' })

console.log('\n── D. no-match with create OFF → empty + closest suggestions (never emits unmatched) ──')
{
  const rows = buildMenu(STORES, 'Aroira', false)      // typo of Aurora, no contains-match, no create
  check('kinds = empty header then suggest rows', kinds(rows)[0], 'empty')
  ok('suggests the closest existing store (Aurora Main)', rows.some(r => r.kind === 'suggest' && r.id === 's1'))
  ok('NO create row when allowCreate is false', !rows.some(r => r.kind === 'create'))
  ok('NO option row emitted for the unmatched string', !rows.some(r => r.kind === 'option'))
}
{
  const rows = buildMenu(STORES, 'zzzzzz', false)      // nothing close at all
  check('totally unrelated query → just the empty header', kinds(rows), ['empty'])
}

console.log('\n── E. emit contract: resolveRow returns ID for a pick, {create,value} for create ──')
{
  const optRow = buildMenu(STATES, 'illinois', false).find(r => r.kind === 'option')
  check('picking an option resolves to its ID (not the label)', resolveRow(optRow), { create: false, id: 'IL', option: { id: 'IL', label: 'Illinois (IL)' } })
  ok('resolved value is the canonical id, never the display string', resolveRow(optRow).id === 'IL')
}
{
  const createRow = buildMenu(STORES, 'Elgin', true).find(r => r.kind === 'create')
  check('picking create resolves to {create:true, value}', resolveRow(createRow), { create: true, value: 'Elgin' })
}
{
  const sugg = buildMenu(STORES, 'Aroira', false).find(r => r.kind === 'suggest')
  ok('a suggestion also resolves to its canonical id', resolveRow(sugg).id === 's1' && resolveRow(sugg).create === false)
}
ok('empty header row is NOT selectable (resolveRow → null)', resolveRow({ kind: 'empty', message: 'No match' }) === null)

console.log('\n── G. MULTI-select: exclude chosen · emit string[] · chips · create guard ──')
{
  // dropdown EXCLUDES already-selected options
  const rows = buildMenuMulti(STATES, '', false, ['IL', 'CA'])
  check('menu excludes the already-selected ids', ids(rows), ['IN', 'NY'])
}
check('filtering still works within the remaining options', ids(buildMenuMulti(STATES, 'in', false, ['IL'])), ['IN'])
{
  // add is idempotent + order-preserving; remove is order-preserving
  check('addSelection appends a new id', addSelection(['IL'], 'CA'), ['IL', 'CA'])
  check('addSelection is idempotent (no dup)', addSelection(['IL', 'CA'], 'IL'), ['IL', 'CA'])
  check('removeSelection drops the id, keeps order', removeSelection(['IL', 'CA', 'NY'], 'CA'), ['IL', 'NY'])
}
{
  // chips: disambiguated display, selection order; an off-roster id survives as the raw id
  const chips = selectedChips(PEOPLE, ['e2', 'e3'])
  check('chips carry the disambiguated display in selection order', chips, [
    { id: 'e2', display: 'John Smith — jsmith@shop.com' }, { id: 'e3', display: 'Maria Cruz' },
  ])
  check('an off-roster selected id is kept as the raw id (never vanishes)', selectedChips(STORES, ['s1', 'ghost']), [
    { id: 's1', display: 'Aurora Main' }, { id: 'ghost', display: 'ghost' },
  ])
}
{
  // create-affordance guard uses the FULL set: an exact match of an ALREADY-selected value never re-offers create
  ok('multi + allowCreate + exact match of a SELECTED value → NO create row (no dup)',
    !buildMenuMulti(STORES, 'Aurora Main', true, ['s1']).some(r => r.kind === 'create'))
  ok('multi + allowCreate + genuinely new value → create row present',
    buildMenuMulti(STORES, 'Elgin', true, ['s1']).some(r => r.kind === 'create'))
  ok('multi + allowCreate=false + no match → no create, closest suggestion instead',
    (() => { const r = buildMenuMulti(STORES, 'Aroira', false, ['s3']); return !r.some(x => x.kind === 'create') && r.some(x => x.kind === 'suggest' && x.id === 's1') })())
}
{
  // emitting: commit maps a picked option-row id into the array via addSelection (emit is string[])
  const row = buildMenuMulti(STATES, 'new york', false, ['IL']).find(r => r.kind === 'option')
  const emitted = addSelection(['IL'], resolveRow(row).id)
  check('picking an option in multi emits the id APPENDED to the array', emitted, ['IL', 'NY'])
}
ok('all-selected → menu is just the empty header (nothing left to pick)',
  kinds(buildMenuMulti(STORES, '', false, ['s1', 's2', 's3'])).join() === 'empty')

// ── source-parity guard: the bodies above must exist verbatim in entity-picker-core.ts ──────────────
console.log('\n── F. source-parity: entity-picker-core.ts matches this harness ──')
const src = readFileSync(new URL('../src/lib/entity-picker-core.ts', import.meta.url), 'utf8')
const need = [
  'return (s ?? \'\').toString().trim().toLowerCase().replace(/\\s+/g, \' \')',
  'return normalizeText(haystack).includes(q)',
  'out[o.id] = counts[k] > 1 && o.sublabel ? `${o.label} — ${o.sublabel}` : o.label',
  'return options.some(o => normalizeText(o.label) === q)',
  'return !!allowCreate && normalizeText(query) !== \'\' && !hasExactMatch(options, query)',
  'const filtered = options.filter(o => matchesQuery(o.label, query) || (o.sublabel ? matchesQuery(o.sublabel, query) : false))',
  'if (showCreate) rows.push({ kind: \'create\', value: query.trim() })',
  'if (row.kind === \'create\') return { create: true, value: row.value }',
  // multi-select parity
  'const chosen = new Set(selectedIds)',
  'const remaining = excludeSelected(options, selectedIds)',
  'return selectedIds.includes(id) ? selectedIds : [...selectedIds, id]',
  'return selectedIds.filter(x => x !== id)',
  'return selectedIds.map(id => ({ id, display: displays[id] ?? id }))',
]
for (const s of need) {
  const found = src.includes(s)
  console.log(`${found ? 'PASS' : 'FAIL'}  core contains \`${s.slice(0, 52)}…\``)
  found ? pass++ : fail++
}
// the US_STATES canonical-first-adopter export exists and stores the code as the id
ok('US_STATES exports Illinois as {id:\'IL\', label:\'Illinois (IL)\'}', src.includes("{ id: 'IL', label: 'Illinois (IL)' }"))

console.log(`\n${fail === 0 ? '✅' : '❌'}  ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
