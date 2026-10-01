// Proof harness — the carrier-scoping compliance lens. Built 2026-08-22 for the carrier-scoping rewrite:
// "no screen may reveal the org runs BOTH Boost and Total". The active-carrier lens shows one carrier at
// a time; single-carrier tenants are unchanged.
//
// Like prove_nav_no_reload.mjs, this does NOT re-implement the logic. It transpiles the REAL pure
// helpers from src/lib/rbac.ts and src/lib/carrier-scope.ts with the project's own TypeScript compiler
// and executes them. If a function is renamed/removed the harness fails loudly.
//
// Run:  node frontend/prove_carrier_scope.mjs      (no network, no DB, no browser, no React)

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'

const HERE = dirname(fileURLToPath(import.meta.url))
const require_ = createRequire(import.meta.url)
const ts = require_('typescript')

let pass = 0, fail = 0
const ck = (label, cond) => { if (cond) { pass++; console.log(`  ok  ${label}`) } else { fail++; console.error(`  XX  ${label}`) } }
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

// Transpile a whole pure-TS module (no runtime imports at module scope) and import it.
async function loadModule(relPath) {
  const src = readFileSync(join(HERE, relPath), 'utf8')
  const js = ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
  const dir = mkdtempSync(join(tmpdir(), 'carrierproof-'))
  const modPath = join(dir, 'm.mjs')
  writeFileSync(modPath, js, 'utf8')
  return import(pathToFileURL(modPath).href)
}

const rbac = await loadModule('src/lib/rbac.ts')
const cs = await loadModule('src/lib/carrier-scope.ts')

const { defaultActiveCarrier, carrierOKActive, carrierCode, NAV_CARRIERS } = rbac
const { financingVendorLabel, atuActiveCarry, textCarrier, presetVisibleForCarrier, vendorServesCarrier,
        posSquash, posVisible, reportKindsVisible, KIND_PROVENANCE } = cs
for (const [n, f] of Object.entries({ defaultActiveCarrier, carrierOKActive, carrierCode }))
  must(typeof f === 'function', `${n} did not export a function from rbac.ts`)
for (const [n, f] of Object.entries({ financingVendorLabel, atuActiveCarry, presetVisibleForCarrier, vendorServesCarrier,
                                      posSquash, posVisible, reportKindsVisible }))
  must(typeof f === 'function', `${n} did not export a function from carrier-scope.ts`)

// Fixtures.
const DUAL = [{ name: 'Boost', code: 'boost', is_default: true }, { name: 'Total Wireless', code: 'total' }]
const DUAL_TOTAL_DEFAULT = [{ name: 'Boost', code: 'boost' }, { name: 'Total Wireless', code: 'total', is_default: true }]
const BOOST_ONLY = [{ name: 'Boost', code: 'boost' }]
const TOTAL_ONLY = [{ name: 'Total Wireless', code: 'total' }]
const NONE = []

console.log('\nA. default active-carrier resolution')
ck('dual tenant → is_default carrier (boost)', defaultActiveCarrier(DUAL) === 'boost')
ck('dual tenant → is_default carrier (total)', defaultActiveCarrier(DUAL_TOTAL_DEFAULT) === 'total')
ck('single carrier (boost) → that carrier', defaultActiveCarrier(BOOST_ONLY) === 'boost')
ck('single carrier (total) → that carrier', defaultActiveCarrier(TOTAL_ONLY) === 'total')
ck('no carriers → boost', defaultActiveCarrier(NONE) === 'boost')
ck('no carriers (undefined) → boost', defaultActiveCarrier(undefined) === 'boost')
ck('carrierCode derives total from "Total Wireless" name', carrierCode({ name: 'Total Wireless' }) === 'total')

// Representative cluster hrefs from NAV_CARRIERS.
const BOOST_HREF = '/commcalc/kpi'          // NAV_CARRIERS → ['boost']
const TOTAL_HREF = '/commcalc/ma-commission' // NAV_CARRIERS → ['total']
const GENERIC_HREF = '/commcalc/dashboard'   // not in NAV_CARRIERS → always shown
must(Array.isArray(NAV_CARRIERS[BOOST_HREF]) && NAV_CARRIERS[BOOST_HREF][0] === 'boost', 'kpi should be a boost-cluster href')
must(Array.isArray(NAV_CARRIERS[TOTAL_HREF]) && NAV_CARRIERS[TOTAL_HREF][0] === 'total', 'ma-commission should be a total-cluster href')

console.log('\nB. carrierOKActive gating — dual tenant sees ONLY the active carrier cluster')
ck('active=boost shows the Boost cluster', carrierOKActive(BOOST_HREF, 'boost', {}) === true)
ck('active=boost HIDES the Total cluster', carrierOKActive(TOTAL_HREF, 'boost', {}) === false)
ck('active=total shows the Total cluster', carrierOKActive(TOTAL_HREF, 'total', {}) === true)
ck('active=total HIDES the Boost cluster', carrierOKActive(BOOST_HREF, 'total', {}) === false)
ck('generic href shows under either carrier', carrierOKActive(GENERIC_HREF, 'boost', {}) === true && carrierOKActive(GENERIC_HREF, 'total', {}) === true)
ck('admin override caps["carrier:href"]=true still wins over the lens',
  carrierOKActive(TOTAL_HREF, 'boost', { ['carrier:' + TOTAL_HREF]: true }) === true)
ck('admin override caps["carrier:href"]=false still hides',
  carrierOKActive(BOOST_HREF, 'boost', { ['carrier:' + BOOST_HREF]: false }) === false)

console.log('\nC. single-carrier tenant is UNCHANGED (active = its only carrier)')
// A Boost-only tenant's active carrier is 'boost': Boost cluster shows, Total cluster hidden — identical
// to the old tenant-set carrierOK for that tenant. Same for Total-only.
ck('boost-only: Boost cluster shows', carrierOKActive(BOOST_HREF, defaultActiveCarrier(BOOST_ONLY), {}) === true)
ck('boost-only: Total cluster hidden', carrierOKActive(TOTAL_HREF, defaultActiveCarrier(BOOST_ONLY), {}) === false)
ck('total-only: Total cluster shows', carrierOKActive(TOTAL_HREF, defaultActiveCarrier(TOTAL_ONLY), {}) === true)
ck('total-only: Boost cluster hidden', carrierOKActive(BOOST_HREF, defaultActiveCarrier(TOTAL_ONLY), {}) === false)

console.log('\nD. ATU figure selection — active carrier carry, NEVER the combined')
const MONEY = { boost_carry_monthly: 100, total_carry_monthly: 40, carry_monthly: 140 }
ck('active=boost → boost carry (100)', atuActiveCarry(MONEY, 'boost') === 100)
ck('active=total → total carry (40)', atuActiveCarry(MONEY, 'total') === 40)
ck('never returns the combined carry_monthly (140)',
  atuActiveCarry(MONEY, 'boost') !== MONEY.carry_monthly && atuActiveCarry(MONEY, 'total') !== MONEY.carry_monthly)
ck('missing money → 0', atuActiveCarry(undefined, 'boost') === 0)

console.log('\nE. generic financing relabel — NEVER emits ACIMA / TW / Edge')
const LEAK = /acima|edge|\btw\b|total\s*wireless|vidapay/i
ck('acima key → neutral "Lease-to-own"', financingVendorLabel('acima', 'ACIMA lease-to-own') === 'Lease-to-own')
ck('edge key → neutral "Carrier financing"', financingVendorLabel('edge', 'Edge financing') === 'Carrier financing')
ck('tw key → neutral "Carrier financing"', financingVendorLabel('tw', 'TW Financing') === 'Carrier financing')
ck('custom label naming a brand collapses to "Financing"', financingVendorLabel('x', 'ACIMA special') === 'Financing')
ck('neutral custom label passes through', financingVendorLabel('affirm', 'Affirm') === 'Affirm')
for (const [k, raw] of [['acima', 'ACIMA lease-to-own'], ['edge', 'Edge financing'], ['tw', 'TW EDGE'], ['acima', 'acima'], ['edge', 'Total Wireless EDGE']]) {
  const out = financingVendorLabel(k, raw)
  ck(`relabel(${k},"${raw}")="${out}" contains no carrier/brand leak`, !LEAK.test(out))
}

console.log('\nF. vendor filtering + preset visibility helpers')
ck('vendor with no carriers = any carrier (shown under boost)', vendorServesCarrier([], 'boost') === true)
ck('Total-assigned vendor hidden under boost', vendorServesCarrier([{ carrier_name: 'Total' }], 'boost') === false)
ck('Total-assigned vendor shown under total', vendorServesCarrier([{ carrier_name: 'Total' }], 'total') === true)
ck('"Total Wireless default" preset hidden when active=boost', presetVisibleForCarrier('Total Wireless default', 'boost', true) === false)
ck('"Total Wireless default" preset shown when active=total', presetVisibleForCarrier('Total Wireless default', 'total', true) === true)
ck('carrier-neutral preset always shown', presetVisibleForCarrier('DM Standard Plan', 'boost', true) === true)
ck('single-carrier tenant: every preset shown (multi=false)', presetVisibleForCarrier('Total Wireless default', 'boost', false) === true)
ck('textCarrier detects total from "Total Wireless"', textCarrier('Total Wireless default') === 'total')

// ── WHICH POS DOES THIS TENANT RUN (owner bug report 2026-09-13) ────────────────────────────────
// "since we declared that the pos is not b2b anymore it is rq that shoud not give the option for b2b
// any more … since the pos was never updated at the ground level it did not propogate to the other
// attched modules." The upload page's email-reports block was the ONE tile block with no visibility
// filter at all, so a tenant that had declared a different POS was still offered another POS's exports.
console.log('\n— POS gate (owner 2026-09-13) —')

// The spellings that actually occur: the mig-953 house preset writes 'b2bsoft', the mig-1004 preset
// writes 'RQ', and an operator editing the term by hand types 'B2B Soft'. A gate that treated those
// as different systems would hide a tenant's own imports.
ck('posSquash folds case and punctuation', posSquash('B2B Soft') === 'b2bsoft')
ck('  …and matches the house preset spelling', posSquash('b2bsoft') === posSquash('B2B Soft'))
ck('  …and tolerates surrounding space', posSquash('  RQ ') === 'rq')
ck('  …null/undefined squash to empty', posSquash(null) === '' && posSquash(undefined) === '')

ck('THE REGRESSION: a b2bsoft block is HIDDEN from a tenant running RQ',
   posVisible('b2bsoft', 'RQ') === false)
ck('  …and hidden however the operator spelled the POS', posVisible('b2bsoft', 'rq') === false)
ck('a b2bsoft block is SHOWN to a tenant running b2bsoft', posVisible('b2bsoft', 'b2bsoft') === true)
ck('  …including the hand-typed spelling', posVisible('b2bsoft', 'B2B Soft') === true)

ck('NEVER HIDES when the tenant POS is unresolved (a failed lookup withholds nothing)',
   posVisible('b2bsoft', '') === true)
ck('  …same for null and undefined', posVisible('b2bsoft', null) === true && posVisible('b2bsoft', undefined) === true)
ck('a POS-AGNOSTIC surface (no tag) is always shown', posVisible('', 'RQ') === true)
ck('  …and with a null tag', posVisible(null, 'RQ') === true)
ck('both unknown ⇒ shown (never a blank page)', posVisible('', '') === true)

// The neutral noun is the term resolver's DISPLAY fallback; it must never be passed in as a POS name
// or every tagged block would hide. The page passes term('pos_system', '') for exactly this reason.
ck("the neutral noun 'POS' is not treated as a POS that matches b2bsoft",
   posVisible('b2bsoft', 'POS') === false)

// ── THE RE-GRANT — a gate with no way out is a support ticket (owner directive 2026-09-13) ───────
// "if they need them then the super admin should have a full role permission exclusiveluy for super
// admin to assign to the new or existing tenants which have been gated out due to carrier or pos
// settings."
//
// The requirement is unchanged; the LEVEL it is met at moved. It used to be a POS *surface* gate
// (`posOK` + `POS_GATED_SURFACES` over `pos:<surface>` caps). Since 2026-09-20 a tenant's POS gates
// individual report-kind ROWS instead, and on 2026-10-01 the surface-level pair was removed once it
// had no caller left — a dead override ladder is worse than none, because its own comment claimed a
// stored `pos:` row was still honoured when nothing read it (index §45).
//
// So the re-grant is proven HERE, on the live mechanism: the `kind:<key>` cap through
// reportKindsVisible, same ui_label_override store, same endpoint, same /admin/labels screen.
console.log('\n— the re-grant, at the kind level (owner 2026-09-13) —')
{
  const K = 'x_report'
  const rows = [{ key: K, is_active: true, applies_to_pos: ['b2bsoft'], applies_to_carrier: [] }]
  const FOREIGN = { pos: ['RQ'], carriers: [] }      // a tenant whose POS the row does not serve
  const OWN     = { pos: ['B2B Soft'], carriers: [] } // …and one whose POS it does (spelled loosely)
  const keys = (out) => out.map(r => r.key)

  // No override ⇒ the gate decides, exactly as the surface gate used to.
  ck('no override ⇒ the POS gate still hides a row the tenant does not run',
     keys(reportKindsVisible(rows, FOREIGN, {})).length === 0)
  ck('no override ⇒ the gate still shows a row the tenant does run',
     keys(reportKindsVisible(rows, OWN, {}))[0] === K)
  ck('an undefined caps map is tolerated',
     keys(reportKindsVisible(rows, OWN, undefined))[0] === K)

  // THE RE-GRANT: the owner's actual ask.
  ck('THE RE-GRANT: an override of show re-opens a row the POS gate had hidden',
     keys(reportKindsVisible(rows, FOREIGN, { ['kind:' + K]: true }))[0] === K)
  ck('…and it is RECORDED as widened, so nobody mistakes it for the tenant\'s own setting',
     reportKindsVisible(rows, FOREIGN, { ['kind:' + K]: true })[0].provenance === KIND_PROVENANCE.widened)
  ck('an override of hide closes a row the gate would have shown',
     keys(reportKindsVisible(rows, OWN, { ['kind:' + K]: false })).length === 0)
  ck('a null override means AUTO — fall through to the gate, not hide',
     keys(reportKindsVisible(rows, FOREIGN, { ['kind:' + K]: null })).length === 0
     && keys(reportKindsVisible(rows, OWN, { ['kind:' + K]: null }))[0] === K)
  ck('an override for a DIFFERENT kind does not leak across',
     keys(reportKindsVisible(rows, FOREIGN, { 'kind:something_else': true })).length === 0)
  ck('a carrier override never reaches the POS gate (separate namespaces)',
     keys(reportKindsVisible(rows, FOREIGN, { 'carrier:/commcalc/upload': true })).length === 0)
  ck('an inactive row is never re-granted by an override — off means off',
     keys(reportKindsVisible([{ ...rows[0], is_active: false }], OWN, { ['kind:' + K]: true })).length === 0)
}

console.log(`\n${fail === 0 ? 'PASS' : 'FAIL'} — ${pass} ok, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
