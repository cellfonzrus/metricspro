// Proof harness — THE CONNECTOR REGISTRY, frontend twin (mig 1014; owner 2026-09-21: "it says rq
// connection but refers to b2b reports").
//
// Like prove_report_kinds.mjs, this does NOT re-implement the logic. It transpiles the REAL
// src/lib/carrier-scope.ts (reportKindsVisible — the one visibility function, now generic over any
// scoped registry row), src/lib/report-kinds.ts and src/lib/connectors.ts (the connector hook's pure
// selectors: connectorFor / scopeState / neutralConnectorLine / connectorNotApplicableCopy /
// withheldConnectorsSummary) with the project's own TypeScript compiler, and drives them over the REAL
// house seed parsed out of database/migrations/1014_connector_registry.sql — the same rows the backend
// mirror is pinned to (harness_connector_scope_lock.py §A). backend/harness_connector_registry.py runs
// the same cases on the backend twin, and the lock pins the neutral line's words equal on both sides.
//
// Run:  node frontend/prove_connector_scope.mjs      (no network, no DB, no browser, no React)

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'

const HERE = dirname(fileURLToPath(import.meta.url))
const require_ = createRequire(import.meta.url)
const ts = require_('typescript')

let pass = 0, fail = 0
const ck = (label, cond, extra) => { if (cond) { pass++; console.log(`  ok  ${label}`) } else { fail++; console.error(`  XX  ${label}${extra !== undefined ? '  — ' + JSON.stringify(extra).slice(0, 300) : ''}`) } }
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

const dir = mkdtempSync(join(tmpdir(), 'connscope-'))
async function loadModule(relPath, outName, rewrites = {}) {
  let src = readFileSync(join(HERE, relPath), 'utf8')
  src = src.replace(/^import\s[^\n]*from\s+'react'\s*$/m, '')
  src = src.replace(/^import\s[^\n]*from\s+'@\/lib\/client'\s*$/m, '')
  for (const [from, to] of Object.entries(rewrites)) src = src.replace(from, to)
  const js = ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
  const modPath = join(dir, outName)
  writeFileSync(modPath, js, 'utf8')
  return import(pathToFileURL(modPath).href)
}

const cs = await loadModule('src/lib/carrier-scope.ts', 'carrier-scope.mjs')
const rk = await loadModule('src/lib/report-kinds.ts', 'report-kinds.mjs', { "from '@/lib/carrier-scope'": "from './carrier-scope.mjs'" })
const cn = await loadModule('src/lib/connectors.ts', 'connectors.mjs', { "from '@/lib/carrier-scope'": "from './carrier-scope.mjs'", "from '@/lib/report-kinds'": "from './report-kinds.mjs'" })
const { reportKindsVisible, kindCapOverride, posSquash } = cs
const { connectorFor, scopeState, neutralConnectorLine, connectorNotApplicableCopy, withheldConnectorsSummary, CONNECTOR_CAP_PREFIX } = cn
for (const [n, f] of Object.entries({ reportKindsVisible, kindCapOverride, connectorFor, scopeState, neutralConnectorLine, connectorNotApplicableCopy, withheldConnectorsSummary }))
  must(typeof f === 'function', `${n} did not export a function`)
must(CONNECTOR_CAP_PREFIX === 'connector:', 'the connector cap namespace')

// ── THE REAL SEED, parsed out of the migration ───────────────────────────────────────────────────
const body = readFileSync(join(HERE, '..', 'database', 'migrations', '1014_connector_registry.sql'), 'utf8').split('sort_order, notes) VALUES')[1].split('ON CONFLICT')[0]
const COLS = ['org_id', 'key', 'aliases', 'label', 'host', 'kind', 'applies_to_pos', 'applies_to_carrier', 'defined_by', 'sort_order', 'notes']
const arr = s => typeof s === 'string' && s.startsWith('{') ? (s.slice(1, -1).match(/"((?:[^"\\]|\\.)*)"/g) || []).map(x => x.slice(1, -1)) : s
const ROWS = []
for (const line of body.trim().split('\n')) {
  const l = line.trim().replace(/,$/, '')
  if (!l.startsWith('(')) continue
  const vals = []
  for (const m of l.slice(1, -1).matchAll(/'((?:[^']|'')*)'|(\d+)|(NULL)/g)) vals.push(m[1] !== undefined ? m[1].replace(/''/g, "'") : m[2] !== undefined ? Number(m[2]) : null)
  const r = Object.fromEntries(COLS.map((c, i) => [c, vals[i]]))
  for (const c of ['aliases', 'applies_to_pos', 'applies_to_carrier']) r[c] = arr(r[c])
  r.is_active = true
  ROWS.push(r)
}
must(ROWS.length >= 8, `parsed only ${ROWS.length} seed rows`)
const keys = rows => new Set(rows.map(r => r.key))
const VZ = { pos: ['rq'], pos_source: 'report_term', carriers: ['verizon'], reasons: [] }
const HOUSE = { pos: ['b2bsoft'], pos_source: 'report_term:boost', carriers: ['boost', 'total'], reasons: [] }
const TOTAL_ONLY = { pos: ['b2bsoft'], pos_source: 'report_term', carriers: ['total'], reasons: [] }
const UNKNOWN = { pos: [], pos_source: 'unknown', carriers: [], reasons: ['no POS declared', 'no carrier declared'] }

console.log('\nA. the seed parsed')
ck('the POS-portal connector is POS-scoped, the MA / processor / distributor portals carrier-scoped, the rest any', (() => {
  const b = ROWS.find(r => r.key === 'b2bsoft'); const v = ROWS.find(r => r.key === 'vidapay'); const e = ROWS.find(r => r.key === 'epay')
  return b.applies_to_pos.includes('b2bsoft') && b.aliases.includes('b2b') && v.applies_to_carrier.includes('total') && v.aliases.includes('total_access') && e.applies_to_carrier.includes('boost')
    && ROWS.filter(r => !r.applies_to_pos.length && !r.applies_to_carrier.length).length >= 6
})())

console.log('\nB. THE VERIZON CASE — POS RQ, carrier verizon: the one visibility function, connector namespace')
const vis = reportKindsVisible(ROWS, VZ, {}, CONNECTOR_CAP_PREFIX)
ck('the POS-portal connector is NOT visible', !keys(vis).has('b2bsoft'), [...keys(vis)])
ck('nor the boost / total portals', !['epay', 'vip', 'vidapay'].some(k => keys(vis).has(k)))
ck('the any-scope connectors are (dlar, the three merchant portals, mailbox, ftp, closing sheet)', ['dlar', 'payanywhere', 'transfirst', 'businesstrack', 'mailbox', 'ftp', 'google_closing'].every(k => keys(vis).has(k)))
ck('connectorFor resolves by key AND by alias, case-insensitively, over the VISIBLE set only', connectorFor(vis, 'b2b') === null && connectorFor(reportKindsVisible(ROWS, HOUSE, {}, CONNECTOR_CAP_PREFIX), 'B2B')?.key === 'b2bsoft' && connectorFor(reportKindsVisible(ROWS, HOUSE, {}, CONNECTOR_CAP_PREFIX), 'total_access')?.key === 'vidapay')
ck('the visible rows carry provenance (house default)', vis.every(r => r.provenance === 'house default'))
ck('the same function with the KIND namespace ignores a connector cap (namespaces do not bleed)', reportKindsVisible(ROWS, VZ, { 'connector:b2bsoft': true }).some(r => r.key === 'b2bsoft') === false)
ck('a super-admin `connector:b2bsoft` show cap widens and is recorded', reportKindsVisible(ROWS, VZ, { 'connector:b2bsoft': true }, CONNECTOR_CAP_PREFIX).find(r => r.key === 'b2bsoft')?.provenance === 'widened by super-admin')
ck('a hide cap withholds an any-scope connector', !keys(reportKindsVisible(ROWS, VZ, { 'connector:dlar': false }, CONNECTOR_CAP_PREFIX)).has('dlar'))
ck('kindCapOverride reads the connector namespace when asked, and the kind namespace by default', kindCapOverride({ 'connector:x': true }, 'x', 'connector:') === true && kindCapOverride({ 'connector:x': true }, 'x') === null)

console.log('\nC. the house org, a Total-only tenant, an unknown declaration')
ck('the house org (POS b2bsoft, boost + total) sees EVERY connector', keys(reportKindsVisible(ROWS, HOUSE, {}, CONNECTOR_CAP_PREFIX)).size === ROWS.length)
const tvis = keys(reportKindsVisible(ROWS, TOTAL_ONLY, {}, CONNECTOR_CAP_PREFIX))
ck('a Total-only tenant on POS b2bsoft: the MA portal + the POS portal, NOT the boost portals', tvis.has('vidapay') && tvis.has('b2bsoft') && !tvis.has('epay') && !tvis.has('vip'))
const uvis = keys(reportKindsVisible(ROWS, UNKNOWN, {}, CONNECTOR_CAP_PREFIX))
ck('an UNKNOWN declaration shows LESS — no POS- or carrier-scoped connector, the any-scope ones still', !uvis.has('b2bsoft') && !uvis.has('vidapay') && uvis.has('mailbox'))
ck('the report-kind path is untouched: reportKindsVisible with its defaults still sorts by sort_order and keeps provenance', (() => {
  const rows = [{ key: 'b', sort_order: 2, landing: 'x' }, { key: 'a', sort_order: 1, landing: 'x' }]
  const out = reportKindsVisible(rows, VZ)
  return out.map(r => r.key).join() === 'a,b' && out[0].provenance === 'house default'
})())

console.log('\nD. scopeState — the applies?/loaded? step over ONE connector\'s backend-computed scope')
ck("'checking' until the config has loaded", scopeState({ applies: false }, false) === 'checking')
ck("'unknown' for an older backend / a failed read (never hides the surface)", scopeState(undefined, true) === 'unknown' && scopeState(null, true) === 'unknown' && scopeState({}, true) === 'unknown')
ck("'not_defined' when the registry withholds it; 'defined' when it applies", scopeState({ applies: false, registered: true, key: 'b2bsoft', label: 'x', host: null, kind: 'portal', why: 'w', miss: 'pos' }, true) === 'not_defined' && scopeState({ applies: true, registered: false, key: 'rq', label: null, host: null, kind: null, why: '' }, true) === 'defined')

console.log('\nE. THE NEUTRAL LINE — the one sentence, with the term, never a vendor')
const declared = neutralConnectorLine({ pos: 'RQ', posDeclared: true })
ck('declared: "No reports-portal connection is defined for RQ yet — when RQ has a reports portal, it can be added under Connectors."', declared === 'No reports-portal connection is defined for RQ yet — when RQ has a reports portal, it can be added under Connectors.', declared)
ck('undeclared: points at the Implementation wizard', neutralConnectorLine({ pos: 'POS', posDeclared: false }).includes('Declare your POS in the Implementation wizard'))
ck('no vendor spelling in either sentence', !/b2b|wsreports/i.test(declared + neutralConnectorLine({ pos: 'POS', posDeclared: false })))
const posScope = { applies: false, registered: true, key: 'b2bsoft', label: 'B2B Soft wsreports', host: 'wsreports.b2bsoft.com', kind: 'portal', why: 'applies to POS b2bsoft — you declared rq', miss: 'pos' }
ck("connectorNotApplicableCopy: a POS miss → the neutral line (the owner's case)", connectorNotApplicableCopy({ scope: posScope, pos: 'RQ', posDeclared: true }) === declared)
const carScope = { ...posScope, key: 'epay', why: 'applies to carrier boost — you declared verizon', miss: 'carrier' }
const carCopy = connectorNotApplicableCopy({ scope: carScope, pos: 'RQ', posDeclared: true })
ck('… a carrier miss → the registry\'s reason, and never a vendor from this file', carCopy.includes('applies to carrier boost — you declared verizon') && carCopy.startsWith('This connector is not offered to this tenant'))
ck('… the registry label / host never leak into the not-applicable copy (the connector is not theirs to name)', !carCopy.includes('wsreports') && !connectorNotApplicableCopy({ scope: posScope, pos: 'RQ', posDeclared: true }).includes('wsreports'))

console.log('\nF. what the surfaces print about what is withheld')
const payload = { declaration: VZ, hidden: [{ key: 'b2bsoft', label: 'x', why: 'w' }, { key: 'epay', label: 'y', why: 'w' }], connectors: vis, caps: {}, registry_ready: true, migration: '1014', all_keys: [], pos: { term: 'RQ', declared: true }, neutral_line: declared }
ck('withheldConnectorsSummary names the count and the declaration', withheldConnectorsSummary(payload) === '2 connectors not offered — you declared POS rq · carrier verizon.', withheldConnectorsSummary(payload))
ck('… and is empty when nothing is withheld', withheldConnectorsSummary({ ...payload, hidden: [] }) === '' && withheldConnectorsSummary(null) === '')

console.log('\nG. no vendor spelling reachable from the connector surfaces\' own copy (the guard\'s derived vocabulary)')
import { execFileSync } from 'node:child_process'
let vocab = null
try { vocab = JSON.parse(execFileSync('python3', [join(HERE, '..', 'backend', 'harness_carrier_vocab_guard.py'), '--print-pos-vocab'], { encoding: 'utf8' })) } catch (e) { vocab = null }
if (vocab) {
  const rx = new RegExp(vocab.regex, 'i')
  const files = ['src/lib/connectors.ts', 'src/app/(platform)/accounts/inventory/page.tsx', 'src/app/(platform)/commcalc/dlar/sweep/page.tsx', 'src/app/(platform)/commcalc/epay/sweep/page.tsx', 'src/app/(platform)/commcalc/vip/sweep/page.tsx', 'src/app/(platform)/commcalc/upload/wizard/page.tsx']
  for (const f of files) {
    // the guard's posture: single-line string literals that carry whitespace and are not a path (a route
    // like /commcalc/b2b/sweep/config is a data key, excused there too)
    const lines = readFileSync(join(HERE, f), 'utf8').split('\n').filter(l => !/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(l))
    const strings = lines.flatMap(l => [...l.matchAll(/(['"`])((?:\\.|(?!\1)[^\\\n])*)\1/g)].map(m => m[2])).filter(s => / /.test(s) && !/\//.test(s))
    ck(`${f}: no POS vendor spelling in a display string`, !strings.some(s => rx.test(s)), strings.filter(s => rx.test(s)).slice(0, 3))
  }
} else {
  console.log('  (skipped — python3 / the guard is not available here; CI runs the guard itself)')
}

console.log(`\n${fail ? 'FAIL' : 'PASS'} — ${pass} ok, ${fail} failed`)
process.exit(fail ? 1 : 0)
