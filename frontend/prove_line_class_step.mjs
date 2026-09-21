// Proof harness — step 2.5a "What counts as an activation", the frontend's PURE logic (the second class,
// owner 2026-09-21: "the effective rule is not what the person confirmed").
//
// Like prove_report_kinds.mjs this does NOT re-implement anything. It transpiles the REAL
// src/app/(platform)/onboarding/intake/line-class-logic.ts (seedFromBlock / buildPutBody / refusalOf — what
// the step seeds its editable words from and the exact PUT body it sends) with the project's own TypeScript,
// and drives it over the REAL block the backend engine produces: `line_class.suggest_rules` /
// `suggest_metric_rules` over the measured-vocabulary fixture (harness_intake_fakes), dumped by python3 —
// so the seed is checked against the very proposal the router returns, not a hand-typed twin.
//
// Run:  node frontend/prove_line_class_step.mjs      (no network, no DB, no browser, no React; python3 for the engine)

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'

const HERE = dirname(fileURLToPath(import.meta.url))
const require_ = createRequire(import.meta.url)
const ts = require_('typescript')

let pass = 0, fail = 0
const ck = (label, cond, extra) => { if (cond) { pass++; console.log(`  ok  ${label}`) } else { fail++; console.error(`  XX  ${label}${extra !== undefined ? '  — ' + JSON.stringify(extra).slice(0, 400) : ''}`) } }
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

const dir = mkdtempSync(join(tmpdir(), 'lineclass-'))
async function loadModule(relPath, outName) {
  const src = readFileSync(join(HERE, relPath), 'utf8')
  const out = ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } })
  must(!(out.diagnostics || []).length, `${relPath} failed to transpile`)
  const modPath = join(dir, outName)
  writeFileSync(modPath, out.outputText, 'utf8')
  return import(pathToFileURL(modPath).href)
}
const L = await loadModule('src/app/(platform)/onboarding/intake/line-class-logic.ts', 'line-class-logic.mjs')
for (const n of ['seedFromBlock', 'buildPutBody', 'refusalOf', 'attestKey', 'parseWords']) must(typeof L[n] === 'function', `${n} did not export a function`)

// ── THE REAL BLOCK: the backend engine over the measured fixture, exactly as _intake_line_class_block shapes it ──
const PY = `
import json, sys
sys.path.insert(0, '.')
from harness_intake_fakes import measured_fixture_rows
from app.modules.commcalc import line_class as LC, exec_metric_defs as EMD
rows = measured_fixture_rows()
def block(raw):
    rules = LC.resolve_rules(raw)
    sug = LC.suggest_rules(rows, rules)
    met = LC.suggest_metric_rules(rows, EMD.resolve([], 'org', []), hints=rules['metric_hints'])
    refused = sug['refused']
    return {'step': '2.5a', 'classes': [{'key': c, 'label': LC.CLASS_LABELS[c]} for c in LC.CLASSES], 'candidate_fields': list(LC.CANDIDATE_FIELDS),
            'rules': {k: rules[k] for k in ('fields', 'tokens', 'exact', 'source', 'declared', 'house_fill')},
            'current': sug['current'], 'gate_open': LC.gate_open(sug['current']), 'gate_note': None,
            'refused': refused, 'rules_ok': not refused, 'refusal_note': LC.refusal_sentence(refused, sug['scanned']) if refused else None,
            'suggest': {k: sug[k] for k in ('scanned', 'distinct', 'per_class', 'too_broad', 'proposal', 'preview')}, 'metrics': met}
OWNER = {'fields': ['category'], 'tokens': {'activation': ['new activation', 'activation', 'add a line', 'new act', 'prepaid'], 'upgrade': ['upgrade'],
         'byod': ['customer provided', 'byod', 'customer owned'], 'port': ['port'], 'hardware_only': ['hardware only']}}
print(json.dumps({'house': block(None), 'owner': block(OWNER), 'hints': LC.HOUSE_HINTS, 'house_tokens': LC.HOUSE_TOKENS}))
`
const dump = JSON.parse(execFileSync('python3', ['-c', PY], { cwd: join(HERE, '..', 'backend'), encoding: 'utf8', maxBuffer: 64 << 20 }))
const HOUSE = dump.house, OWNER = dump.owner
must(HOUSE.gate_open === true && OWNER.refused.length === 1, 'the engine dump is not the expected shape')

console.log('\nA. THE SEED — from suggest.proposal only')
const s1 = L.seedFromBlock(HOUSE)
ck('an unmapped tenant (gate open): the editable words ARE the proposal — category "new activation" / "upgrade" / "customer provided, customer owned" / "hardware only"', s1.source === 'proposal'
  && s1.tokens.activation === 'new activation' && s1.tokens.upgrade === 'upgrade' && s1.tokens.byod === HOUSE.suggest.proposal.tokens.byod.join(', ') && s1.tokens.hardware_only === 'hardware only', s1.tokens)
ck('…the fields are the proposal\'s (contract type first, then the columns the words live in)', s1.fields.join(',') === HOUSE.suggest.proposal.fields.join(',') && s1.fields.includes('category'))
ck('…an undeclared class under a tenant field is EMPTY (port) — no house contract-type word rides in', s1.tokens.port === '' && HOUSE.suggest.proposal.tokens.port.length === 0)
const hintWords = Object.values(dump.hints).flat(), houseWords = Object.values(dump.house_tokens).flat()
const seededWords = Object.values(s1.tokens).flatMap(v => L.parseWords(v))
ck('…no seeded word is there BECAUSE it is a hint: every seeded word is in the proposal, and the bare "activation" / "port" hints are neither hints nor seeded', seededWords.every(w => Object.values(HOUSE.suggest.proposal.tokens).flat().includes(w))
  && !seededWords.includes('activation') && !seededWords.includes('port') && !hintWords.includes('activation') && !dump.hints.port.includes('port'), seededWords)
ck('…the house contract-type words (" aal", "idv", "port-in" …) never appear in a category-field seed', !seededWords.some(w => houseWords.includes(w) && w !== 'upgrade'), seededWords)
const s2 = L.seedFromBlock(OWNER)
ck('THE OWNER\'S SAVED ROW (bare "activation" inside every path): the block says refused; the seed is the person\'s words MINUS the refused one plus this file\'s hits — "prepaid" kept, "activation" gone',
  OWNER.rules_ok === false && s2.tokens.activation === 'new activation, add a line, new act, prepaid' && s2.tokens.byod === 'customer provided, byod, customer owned', s2.tokens)
ck('…the seed never comes from block.rules (the rules in force still carry the bare word; the seed does not)', OWNER.rules.tokens.activation.includes('activation') && !L.parseWords(s2.tokens.activation).includes('activation'))
ck('…the block hands the step no hint list at all', !('hints' in OWNER.rules) && !('metric_hints' in OWNER.rules) && !('hints' in OWNER))
ck('the phones proposal is ticked by default ("use") because the bucket matched 0 and the file carries the words; bill_payment (no proposal) is not', s1.useMetric.phones === true && s1.useMetric.bill_payment === false, s1.useMetric)

console.log('\nB. THE PUT BODY — what the save sends')
const body = L.buildPutBody({ instanceKey: 'sales:mypos:sales', who: 'pat', fields: s1.fields, tokens: s1.tokens, useMetric: s1.useMetric, buckets: HOUSE.metrics.buckets, noAct: '', broadOk: [] })
ck('ticking "use" on the phones proposal puts metric_rules.phones = the engine\'s proposal (category_contains smartphone / basic phone …) in the body', !!body.metric_rules && !!body.metric_rules.phones
  && (body.metric_rules.phones.category_contains || []).includes('smartphone') && (body.metric_rules.phones.category_contains || []).includes('basic phone'), body.metric_rules)
const withProposal = Object.values(HOUSE.metrics.buckets).filter(m => m.proposal).map(m => m.bucket).sort()
ck('…and exactly the buckets that carry a proposal (phones + accessory over this fixture) — a bucket without one (bill_payment, protect, activation_fee) sends nothing', Object.keys(body.metric_rules).sort().join(',') === withProposal.join(',') && !('bill_payment' in body.metric_rules), Object.keys(body.metric_rules))
ck('the words are parsed per class, lowercased, trimmed; an empty box is [] (this type is not counted)', body.tokens.activation.join('|') === 'new activation' && Array.isArray(body.tokens.port) && body.tokens.port.length === 0 && body.fields.includes('category'))
ck('instance_key / by / no_activations null / broad_ok null ride along', body.instance_key === 'sales:mypos:sales' && body.by === 'pat' && body.no_activations === null && body.broad_ok === null)
const unticked = L.buildPutBody({ instanceKey: 'k', who: 'pat', fields: s1.fields, tokens: s1.tokens, useMetric: { ...s1.useMetric, phones: false }, buckets: HOUSE.metrics.buckets, noAct: '', broadOk: [] })
const none = L.buildPutBody({ instanceKey: 'k', who: 'pat', fields: s1.fields, tokens: s1.tokens, useMetric: {}, buckets: HOUSE.metrics.buckets, noAct: '', broadOk: [] })
ck('unticking phones drops phones from the body (the other ticked bucket stays); nothing ticked → metric_rules null (nothing written for the buckets)', unticked.metric_rules !== null && !('phones' in unticked.metric_rules) && none.metric_rules === null)
const attested = L.buildPutBody({ instanceKey: 'k', who: 'pat', fields: ['category'], tokens: { ...s2.tokens, activation: 'new activation, Activation' }, useMetric: {}, buckets: HOUSE.metrics.buckets, noAct: '  ', broadOk: [L.attestKey('activation', 'Activation')] })
ck('keeping a refused word "anyway" sends broad_ok = ["activation:activation"] (the attestation by name the backend records); the word itself rides in tokens lowercased', attested.broad_ok.join() === 'activation:activation' && attested.tokens.activation.includes('activation') && attested.no_activations === null)

console.log('\nC. THE REFUSAL — the save\'s 400 detail rendered as one shape')
const detail = { message: 'Not saved — "activation" under new activation (99% of the lines) …', refused: OWNER.refused, basis: 'landed', rows: 898, attest_with: 'broad_ok' }
const r = L.refusalOf(detail, 'fallback')
ck('a structured refusal (message + refused[] naming class / word / share) is rendered; the word and its share are there', !!r && r.refused[0].token === 'activation' && r.refused[0].ratio >= 0.8 && r.basis === 'landed' && r.rows === 898 && r.message.startsWith('Not saved'))
ck('a plain-string error is NOT a refusal (the step shows the message instead)', L.refusalOf('permission denied', 'x') === null && L.refusalOf({ message: 'x', refused: [] }, 'x') === null && L.refusalOf(undefined, 'x') === null)

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
console.log('OK — the step seeds from the proposal only, the body carries the ticked metric rule, a refusal names its evidence.')
