// Proof harness — the POS wizard's "Plans from your carrier data" card, the frontend's PURE logic (owner
// 2026-09-22: "we have enough plans in the system to bring over but it does not give an option to bring over").
//
// Like prove_line_class_step.mjs this does NOT re-implement anything. It transpiles the REAL
// src/app/(platform)/pos/onboarding/plan-sources-logic.ts (seedFromPreview / changedKeys / buildConfigBody /
// planLine / refusalOf) with the project's own TypeScript and drives it over the REAL preview payload the
// backend resolver produces (core/onboarding.preview_import over an in-memory tenant shaped like the measured
// one — harness_intake_fakes.FakeDB), dumped by python3 — so the seed is checked against the very proposal the
// server returns, not a hand-typed twin.
//
// Run:  node frontend/prove_plan_sources_card.mjs      (no network, no DB, no browser, no React; python3 for the engine)

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

const dir = mkdtempSync(join(tmpdir(), 'plansources-'))
async function loadModule(relPath, outName) {
  const src = readFileSync(join(HERE, relPath), 'utf8')
  const out = ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } })
  must(!(out.diagnostics || []).length, `${relPath} failed to transpile`)
  const modPath = join(dir, outName)
  writeFileSync(modPath, out.outputText, 'utf8')
  return import(pathToFileURL(modPath).href)
}
const L = await loadModule('src/app/(platform)/pos/onboarding/plan-sources-logic.ts', 'plan-sources-logic.mjs')
for (const n of ['seedFromPreview', 'changedKeys', 'buildConfigBody', 'planLine', 'refusalOf', 'parseWords', 'attestKey']) must(typeof L[n] === 'function', `${n} did not export a function`)

// ── THE REAL PAYLOAD: the backend resolver over the measured-shape tenant, exactly as preview_import shapes it ──
const PY = `
import json, sys
sys.path.insert(0, '.')
from harness_intake_fakes import FakeDB
from app.modules.core import onboarding as ob, plan_sources as ps
ORG = 'f4f1c16e-0000-4000-8000-000000000001'
db = FakeDB()
db.declared['product_mrc'] = ['id', 'org_id', 'plan_pattern', 'mrc', 'carrier_id', 'classification', 'is_active']
db.declared['raw_mi'] = ['id', 'org_id', 'period', 'period_year', 'period_month', 'customer_plan', 'base_mrc', 'commissionable_mrc']
db.declared['commission_ledger'] = ['id', 'org_id', 'period', 'source_report', 'product_name', 'order_type', 'category', 'trans_date']
db.declared['pos_settings'] = ['id', 'org_id', 'store_code', 'key', 'value', 'updated_at']
db.seed('carrier', [{'org_id': ORG, 'name': 'Carrier A'}])
P = '>> Activations (Price Sheet) >> Carrier A >> '
def lines(cat, prod, n, d='2026-08-05'):
    return [{'org_id': ORG, 'period': 'August 2026', 'department': 'Activations (Price Sheet)', 'category': cat, 'product_desc': prod, 'trans_date': d} for _ in range(n)]
db.seed('raw_sales', lines(P + 'Rate Plans', 'iPhone Rate Plan (DPA)', 263) + lines(P + 'Rate Plans', 'New Activation Rate Plan', 56, '2026-07-02')
        + lines(P + 'Rate Plans', 'Smart Phone Rate Plan (DPA)', 30) + lines(P + 'Rate Plan Rebates', 'DPA Upgrade iPhone (Rate Plan Rebate)', 159)
        + lines(P + 'Rate Plan Rebates', 'DPA New Act iPhone (Rate Plan Rebate)', 104) + lines(P + 'Rate Plan Rebates', 'New Activation (Rate Plan Rebate)', 53)
        + lines(P + 'SmartPhones >> Maker', 'Phone model X', 2000) + lines('>> Accessories >> Cases', 'Case', 2050))
db.seed('commission_ledger', [{'org_id': ORG, 'period': 'July 2026', 'source_report': 's', 'product_name': 'Unlimited Plus Price Plan - New', 'order_type': 'Activation', 'category': 'commission', 'trans_date': '2026-07-10'}] * 90
        + [{'org_id': ORG, 'period': 'July 2026', 'source_report': 's', 'product_name': 'Device Bonus', 'order_type': 'Spiff', 'category': 'spiff', 'trans_date': '2026-07-11'}] * 10)
ob.sb = lambda: db
house = ob.preview_import(ob.PLAN_SOURCE_KEY, ORG)
after = ob.save_plan_sources(db, ORG, {'sources': {'sales_lines': {'enabled': True, 'include': ['rate plan'], 'exclude': ['rebate']}}}, actor='owner')
try:
    ob.save_plan_sources(db, ORG, {'sources': {'statement_lines': {'enabled': True, 'include': ['plan']}}})
    refusal = None
except Exception as e:
    refusal = {'detail': e.detail}
print(json.dumps({'house': house, 'after': after, 'refusal': refusal, 'hints': ps.HOUSE_INCLUDE_HINTS, 'keys': ps.source_keys()}))
`
const out = execFileSync('python3', ['-c', PY], { cwd: join(HERE, '..', 'backend'), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
const R = JSON.parse(out.trim().split('\n').pop())
const house = R.house, after = R.after

console.log('\n── the seed comes from the PROPOSAL, over the real payload ──')
const seed = L.seedFromPreview(house)
ck('every source in the payload gets an edit slot — none named in the logic file', Object.keys(seed.edits).length === house.sources.length && R.keys.every(k => k in seed.edits), Object.keys(seed.edits))
ck('the mig-074 pair is ticked (house = today), the line-level sources are not', seed.edits.catalogue.enabled && seed.edits.subscribers.enabled && !seed.edits.sales_lines.enabled && !seed.edits.statement_lines.enabled)
ck('the sales export\'s words are seeded from suggest.proposal — "rate plan", exclude "rebate"', seed.edits.sales_lines.include === 'rate plan' && seed.edits.sales_lines.exclude === 'rebate', seed.edits.sales_lines)
ck('a hint word that is NOT a hit in this file is never seeded (no hint list reaches the card)', !R.hints.filter(h => h !== 'rate plan').some(h => seed.edits.sales_lines.include.split(', ').includes(h)))
ck('the card carries no hint list: the payload has none', !('hints' in house) && !house.sources.some(s => 'hints' in s))
ck('the seed is marked as coming from the proposal', seed.source === 'proposal')

console.log('\n── the body the save sends ──')
const base = seed.edits
ck('nothing changed → nothing sent (a fresh proposal is not a change the person made)', L.changedKeys(house, seed.edits, base).length === 0 && JSON.stringify(L.buildConfigBody(house, seed.edits, base, [])) === JSON.stringify({ sources: {} }))
const edits = { ...seed.edits, sales_lines: { ...seed.edits.sales_lines, enabled: true } }
ck('ticking the sales export sends ONLY that source, with its proposed words parsed', JSON.stringify(L.buildConfigBody(house, edits, base, [])) === JSON.stringify({ sources: { sales_lines: { enabled: true, include: ['rate plan'], exclude: ['rebate'] } } }), L.buildConfigBody(house, edits, base, []))
const edits2 = { ...edits, catalogue: { ...edits.catalogue, enabled: false }, statement_lines: { ...edits.statement_lines, include: 'Price Plan, price plan , unlimited' } }
const body2 = L.buildConfigBody(house, edits2, base, ['statement_lines:plan'])
ck('a house source switched off sends {enabled:false} only (no words for a non-line source)', JSON.stringify(body2.sources.catalogue) === JSON.stringify({ enabled: false }))
ck('words are parsed lower-cased, trimmed, de-duplicated, order kept', JSON.stringify(body2.sources.statement_lines.include) === JSON.stringify(['price plan', 'unlimited']), body2.sources.statement_lines)
ck('an edited-but-unticked source is still sent (the words are saved for when it is ticked)', body2.sources.statement_lines.enabled === false)
ck('the attested keys ride along as broad_ok', JSON.stringify(body2.broad_ok) === JSON.stringify(['statement_lines:plan']))
ck('attestKey spells <source>:<word> lower-cased', L.attestKey('statement_lines', ' Plan ') === 'statement_lines:plan')

console.log('\n── after the person confirms: the card re-seeds from the SAVED preview ──')
const seed2 = L.seedFromPreview(after)
ck('the saved words are now the seed (declared words first)', seed2.edits.sales_lines.enabled && seed2.edits.sales_lines.include === 'rate plan' && seed2.edits.sales_lines.exclude === 'rebate', seed2.edits.sales_lines)
const salesAfter = after.sources.find(s => s.key === 'sales_lines')
ck('and right after a save the seed equals the rules in force — nothing dirty', L.changedKeys(after, seed2.edits, seed2.edits).length === 0 && seed2.edits.sales_lines.include === salesAfter.include.join(', ') && seed2.edits.sales_lines.exclude === salesAfter.exclude.join(', '), salesAfter)
ck('Bring over N is the resolver\'s count — 3 plans, none a rebate', after.count === 3 && after.sample.every(p => !/rebate/i.test(p.plan_name)), after.sample.map(p => p.plan_name))
const labelOf = Object.fromEntries(after.sources.map(s => [s.key, s.label]))
const line0 = L.planLine(after.sample[0], after.mrc_next, labelOf)
ck('a candidate line shows the name, the missing charge with WHERE to price it, and its provenance', line0.startsWith('iPhone Rate Plan (DPA)') && line0.includes('no charge on file') && line0.includes(after.mrc_next) && line0.includes('263 line(s) in Your sales export (line level)'), line0)
ck('a priced plan renders its charge, not the pointer', L.planLine({ plan_name: 'X', monthly_fee: 55, subscribers: 12, source: 'subscribers' }, after.mrc_next, labelOf) === 'X — $55/mo (12 subscribers, Your subscriber report)')

console.log('\n── the refusal ──')
const r = L.refusalOf(R.refusal)
ck('a refused save is rendered from the structured detail: the message and the attest keys', r && r.keys.length === 1 && r.keys[0] === 'statement_lines:plan' && /names 90%/.test(r.message), r)
ck('an ordinary error is not a refusal', L.refusalOf({ detail: 'sign in to change onboarding' }) === null && L.refusalOf(null) === null)

console.log(`\n${fail ? 'FAIL' : 'OK'} — ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
