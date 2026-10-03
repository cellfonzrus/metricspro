// Proof harness — the finance scope picker shows a NAME, never a company id (index §13b.1).
//
// Owner report 2026-10-03: *"the app shows the company id not the name of the company in the settings to
// choose the company to work in."* The company dropdown shared by the Accounts hub, the P&L, the Balance
// Sheet and the Cash Flow labelled each scope `scope_label || scope_key`, and `scope_key` for a company
// scope is the literal string `company:<uuid>`. A missing or stale stored label therefore rendered the
// company's UUID as its name.
//
// The backend now resolves the name ONCE (`account/coa.scope_display_label`, proved by
// `backend/harness_scope_label.py`) and stamps it as `scope_display`. This harness proves the FRONTEND
// half: `accounts/_components/scopeFinancials.ts` is the only reader of that fact, and nothing it
// returns can be a raw key.
//
// No stub, no copy: Node >= 22.18 strips the erasable TypeScript of the REAL module (it imports nothing)
// and this drives its exported functions.
//
//   A. scopeDisplay returns the resolved name for a scope row
//   B. THE REGRESSION: a company row whose stored label is null/stale still renders a NAME — never the
//      `company:<uuid>` key, in any of the shapes a response can arrive in
//   C. statementScopeDisplay falls back from the statement read to the picker list, never to the key
//   D. scopeDisplayOr takes a caller's already-resolved name, and still never a key
//   E. no output of any function, for any input, contains a uuid or a `family:` key
//
// Run:  node frontend/prove_scope_label.mjs      (no network, no DB, no React, no npm install)
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { readFileSync } from 'node:fs'

const HERE = dirname(fileURLToPath(import.meta.url))
const MOD = 'src/app/(platform)/accounts/_components/scopeFinancials.ts'
const S = await import(pathToFileURL(join(HERE, MOD)).href)

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}

const CID = '9b22c0d8-1111-4444-8888-aaaaaaaaaaaa'
const KEY = `company:${CID}`
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i
const KEYISH = /^(company|store|profit_center):/

console.log('A. scopeDisplay reads the resolved name')
ck('a company row renders its name',
  S.scopeDisplay({ scope_key: KEY, scope_label: null, scope_display: 'First Entity LLC' }) === 'First Entity LLC')
ck('consolidated renders its own words',
  S.scopeDisplay({ scope_key: 'consolidated', scope_display: 'Consolidated (all companies)' }) === 'Consolidated (all companies)')
ck('a store row renders its address',
  S.scopeDisplay({ scope_key: 'store:123 MAIN ST', scope_display: '123 MAIN ST' }) === '123 MAIN ST')

console.log('B. THE REGRESSION — a null or stale stored label never shows the id')
const regressions = [
  ['stored label null, backend resolved it', { scope_key: KEY, scope_label: null, scope_display: 'First Entity LLC' }, 'First Entity LLC'],
  ['stored label STALE, backend resolved it', { scope_key: KEY, scope_label: 'Old Name LLC', scope_display: 'First Entity LLC' }, 'First Entity LLC'],
  ['stored label IS the key, backend resolved it', { scope_key: KEY, scope_label: KEY, scope_display: 'First Entity LLC' }, 'First Entity LLC'],
]
for (const [why, row, want] of regressions) ck(why, S.scopeDisplay(row) === want, S.scopeDisplay(row))
const degraded = [
  ['no scope_display at all (an older cached response)', { scope_key: KEY, scope_label: null }],
  ['scope_display blank', { scope_key: KEY, scope_label: KEY, scope_display: '   ' }],
  ['scope_display null', { scope_key: KEY, scope_display: null }],
  ['only a key', { scope_key: KEY }],
  ['nothing at all', {}],
  ['null row', null],
  ['undefined row', undefined],
]
for (const [why, row] of degraded) {
  const got = S.scopeDisplay(row)
  ck(`degraded: ${why} ⇒ a word, not the key`, got.length > 0 && !UUID.test(got) && !KEYISH.test(got), got)
}

console.log('C. statementScopeDisplay — read, then picker list, never the key')
const scopes = [
  { scope_key: 'consolidated', scope_display: 'Consolidated (all companies)' },
  { scope_key: KEY, scope_display: 'First Entity LLC' },
]
ck('the statement read wins when it carries the name',
  S.statementScopeDisplay({ scope: KEY, scope_display: 'First Entity LLC' }, scopes, KEY) === 'First Entity LLC')
ck('falls back to the picker row for the SELECTED key (read not loaded yet)',
  S.statementScopeDisplay(null, scopes, KEY) === 'First Entity LLC')
ck('a stale read with no name still uses the picker row',
  S.statementScopeDisplay({ scope: KEY, scope_label: 'Old Name LLC' }, scopes, KEY) === 'First Entity LLC')
for (const [why, data, list, key] of [
  ['selected key is in neither', null, scopes, 'company:deadbeef-0000-0000-0000-000000000000'],
  ['empty list, empty read', null, [], KEY],
  ['null list', null, null, KEY],
  ['no selection', null, scopes, ''],
]) {
  const got = S.statementScopeDisplay(data, list, key)
  ck(`degraded: ${why} ⇒ a word, not the key`, got.length > 0 && !UUID.test(got) && !KEYISH.test(got), got)
}

console.log('D. scopeDisplayOr — a caller fallback that is itself a name')
ck('the resolved name wins over the caller fallback',
  S.scopeDisplayOr({ scope_display: 'First Entity LLC' }, 'Consolidated (all companies)') === 'First Entity LLC')
ck('the caller fallback is used when the response carries none',
  S.scopeDisplayOr({ scope: KEY }, 'First Entity LLC') === 'First Entity LLC')
const blankBoth = S.scopeDisplayOr({}, '')
ck('both blank ⇒ a word, not a key', blankBoth.length > 0 && !KEYISH.test(blankBoth), blankBoth)

console.log('E. no function can emit a uuid or a raw key, for any input')
const inputs = [null, undefined, {}, { scope_key: KEY }, { scope: KEY }, { scope_display: '' },
  { scope_key: 'store:123 MAIN ST' }, { scope_key: 'profit_center:NORTH' },
  { scope_key: KEY, scope_label: KEY }]
const leaks = []
for (const row of inputs) {
  for (const [name, got] of [
    ['scopeDisplay', S.scopeDisplay(row)],
    ['scopeDisplayOr', S.scopeDisplayOr(row, '')],
    ['statementScopeDisplay', S.statementScopeDisplay(row, [row], String(row?.scope_key || ''))],
  ]) {
    if (UUID.test(got) || KEYISH.test(got) || !String(got).trim()) leaks.push([name, row, got])
  }
}
ck('no leak across the whole input matrix', leaks.length === 0, leaks)

console.log('F. the module is the ONLY place the rule lives')
const src = readFileSync(join(HERE, MOD), 'utf8')
const code = src.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map(l => l.replace(/(^|\s)\/\/.*$/, '')).join('\n')
ck('no `scope_label ||` fallback in the module code', !/scope_label\s*(\|\||\?\?)/.test(code))
ck('no `|| scope_key` fallback in the module code', !/\|\|\s*\w*\.?scope_key\b/.test(code))
ck('a single named floor constant', (code.match(/const SCOPE_UNKNOWN\s*=/g) || []).length === 1)
ck('it names no tenant, company or carrier',
  !/\b(cellfonz|luxelink|novawave|boost|metro|cricket|t-?mobile|at&t|verizon)\b/i.test(src))

console.log(`\n  ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
