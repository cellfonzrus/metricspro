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
const { financingVendorLabel, atuActiveCarry, textCarrier, presetVisibleForCarrier, vendorServesCarrier } = cs
for (const [n, f] of Object.entries({ defaultActiveCarrier, carrierOKActive, carrierCode }))
  must(typeof f === 'function', `${n} did not export a function from rbac.ts`)
for (const [n, f] of Object.entries({ financingVendorLabel, atuActiveCarry, presetVisibleForCarrier, vendorServesCarrier }))
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

console.log(`\n${fail === 0 ? 'PASS' : 'FAIL'} — ${pass} ok, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
