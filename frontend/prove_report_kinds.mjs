// Proof harness — THE REPORT-KIND REGISTRY, frontend twin (design §7, owner directives 2026-09-20).
//
// Like prove_carrier_scope.mjs, this does NOT re-implement the logic. It transpiles the REAL
// src/lib/carrier-scope.ts (reportKindsVisible — the one visibility function) and src/lib/report-kinds.ts
// (the pure selectors every upload surface calls: kindsForSurface / uploadTypesOf / labelForUploadType /
// suggestRuleFrom / globToRegex / withheldSummary) and src/app/(platform)/commcalc/_lib/uploadRoutes.ts
// (the Upload page's and wizard's route metadata) with the project's own TypeScript compiler, and drives
// them over the REAL house seed parsed out of database/migrations/1010_report_kind_registry.sql — the
// same rows the backend mirror is pinned to. The Python harness runs the same cases on the backend twin.
//
// Run:  node frontend/prove_report_kinds.mjs      (no network, no DB, no browser, no React)

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

const dir = mkdtempSync(join(tmpdir(), 'reportkinds-'))
// Transpile a real TS module. Runtime-only imports (react, the API client) are stripped: the pure
// functions under test never touch them; '@/lib/carrier-scope' is pointed at the transpiled twin.
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
const routes = await loadModule('src/app/(platform)/commcalc/_lib/uploadRoutes.ts', 'uploadRoutes.mjs')
const { reportKindsVisible, kindApplies, kindCapOverride, posSquash } = cs
const { kindsForSurface, uploadTypesOf, labelForUploadType, suggestRuleFrom, globToRegex, withheldSummary, showsInFor } = rk
for (const [n, f] of Object.entries({ reportKindsVisible, kindApplies, kindCapOverride, kindsForSurface, uploadTypesOf, labelForUploadType, suggestRuleFrom, globToRegex, withheldSummary }))
  must(typeof f === 'function', `${n} did not export a function`)

// ── THE REAL SEED, parsed out of the migration ───────────────────────────────────────────────────
// every migration that seeds HOUSE report kinds (backend report_kinds.SEED_MIGRATIONS): 1010 + 1012's sales_by_invoice
const SEEDS = ['1010_report_kind_registry.sql', '1012_sales_by_invoice.sql']
const body = SEEDS.map(n => readFileSync(join(HERE, '..', 'database', 'migrations', n), 'utf8').split('upload_types, sort_order, custom_sheet_label) VALUES')[1].split('ON CONFLICT')[0]).join('\n')
const COLS = ['org_id', 'key', 'label', 'what_in_it', 'recognisable_columns', 'source_hint', 'applies_to_pos', 'applies_to_carrier', 'defined_by',
  'statement_type', 'landing', 'layout', 'signature_fields', 'requires_columns', 'excludes_columns', 'upload_types', 'sort_order', 'custom_sheet_label']
const arr = s => s.startsWith('{') ? (s.slice(1, -1).match(/"((?:[^"\\]|\\.)*)"/g) || []).map(x => x.slice(1, -1)) : s
const ROWS = []
for (const line of body.trim().split('\n')) {
  const l = line.trim().replace(/,$/, '')
  if (!l.startsWith('(')) continue
  const vals = []
  for (const m of l.slice(1, -1).matchAll(/'((?:[^']|'')*)'|(\d+)|(NULL)/g)) vals.push(m[1] !== undefined ? m[1].replace(/''/g, "'") : m[2] !== undefined ? Number(m[2]) : null)
  const r = Object.fromEntries(COLS.map((c, i) => [c, vals[i]]))
  for (const c of ['recognisable_columns', 'applies_to_pos', 'applies_to_carrier', 'signature_fields', 'requires_columns', 'excludes_columns', 'upload_types']) r[c] = arr(r[c])
  r.is_active = true
  ROWS.push(r)
}
must(ROWS.length >= 20, `parsed only ${ROWS.length} seed rows`)
const B2B = new Set(ROWS.filter(r => r.applies_to_pos.includes('b2bsoft')).map(r => r.key))
const BOOST = new Set(ROWS.filter(r => r.applies_to_carrier.includes('boost')).map(r => r.key))
const TOTAL = new Set(ROWS.filter(r => r.applies_to_carrier.includes('total')).map(r => r.key))
const SPECIFIC = new Set([...B2B, ...BOOST, ...TOTAL])
const keys = rows => new Set(rows.map(r => r.key))
const inter = (a, b) => [...a].filter(x => b.has(x))
const SURFACES = ['intake', 'upload', 'wizard', 'email_imports', 'tiles']

console.log('\nA. the seed parsed')
ck(`the seed carries B2B-, boost- and total-applies-to kinds (${B2B.size}/${BOOST.size}/${TOTAL.size})`, B2B.size >= 3 && BOOST.size >= 3 && TOTAL.size >= 3)
ck('the eleven layman cards are present', ['sales_imei_phone', 'sales_cost_price', 'commission_statement', 'residual_statement', 'inventory_on_hand', 'inventory_aging', 'bill_payments_pos', 'bill_payments_carrier', 'x_report', 'merchant_settlement', 'something_else'].every(k => ROWS.some(r => r.key === k)))

console.log('\nB. THE VERIZON CASE — POS RQ, carrier verizon: no B2B kind on ANY surface')
const VZ = { pos: ['rq'], carriers: ['verizon'], reasons: [] }
const vz = reportKindsVisible(ROWS, VZ, {})
ck('no B2B / boost / total kind is visible', inter(keys(vz), SPECIFIC).length === 0, inter(keys(vz), SPECIFIC))
for (const s of SURFACES) ck(`surface '${s}': no B2B / boost / total kind`, inter(keys(kindsForSurface(vz, s)), SPECIFIC).length === 0, inter(keys(kindsForSurface(vz, s)), SPECIFIC))
ck('the POS-agnostic layman cards are offered', ['sales_imei_phone', 'sales_cost_price', 'commission_statement', 'residual_statement', 'inventory_on_hand', 'inventory_aging', 'x_report', 'merchant_settlement', 'something_else'].every(k => keys(vz).has(k)))
ck('each with provenance "house default"', vz.every(r => r.provenance === 'house default'))
ck('the declaration is squashed like posSquash: "RQ" and "rq" are one POS', keys(reportKindsVisible(ROWS, { pos: ['RQ'], carriers: ['Verizon'] }, {})).size === keys(vz).size)
// the page-level selectors the Upload page / wizard call
const vzTypes = uploadTypesOf(vz)
const vzTiles = Object.values(routes.PERIOD_ROUTES).filter(t => vzTypes.includes(t.id)).map(t => t.id)
const vzModules = Object.values(routes.MODULE_ROUTES).filter(t => vzTypes.includes(t.id)).map(t => t.id)
const vzLinks = Object.values(routes.LINK_ROUTES).filter(t => vzTypes.includes(t.id)).map(t => t.id)
ck('UPLOAD PAGE tiles for Verizon carry no boost / total / B2B route', !['payment_detail', 'mi_report', 'comp_report', 'ma_commission', 'ma_daily_tx', 'ma_fulfillment'].some(x => vzTiles.includes(x)) && vzTiles.length > 0, vzTiles)
ck('… module tiles: no distributor / asset-ledger route', !['vip_workbook', 'asset_ledger'].some(x => vzModules.includes(x)), vzModules)
ck('… link tiles: no b2b inventory recon', vzLinks.length === 0, vzLinks)
ck('… custom-sheet tiles (the POS email reports of #234): none', kindsForSurface(vz, 'upload').filter(k => k.custom_sheet_label).length === 0)
ck('WIZARD steps for Verizon = only routes a visible kind names', kindsForSurface(vz, 'wizard').every(k => k.upload_types.every(u => vzTypes.includes(u))) && !kindsForSurface(vz, 'wizard').some(k => SPECIFIC.has(k.key)))
ck('EMAIL IMPORTS "Routes to" list for Verizon: no boost / total / B2B route', !['ma_commission', 'ma_daily_tx', 'ma_fulfillment', 'payment_detail'].some(x => vzTypes.includes(x)))
ck('INTAKE 2.0 cards for Verizon: the layman cards, no B2B custom sheet', kindsForSurface(vz, 'intake').length >= 9 && !kindsForSurface(vz, 'intake').some(k => B2B.has(k.key)))

console.log('\nC. the siblings')
const b2 = reportKindsVisible(ROWS, { pos: ['B2B Soft'], carriers: ['boost'] }, {})
ck('a B2B / boost tenant sees the B2B kinds and the boost kinds ("B2B Soft" squashes to the code)', [...B2B, ...BOOST].every(k => keys(b2).has(k)))
ck('… and not the total-only kinds', inter(keys(b2), TOTAL).length === 0)
ck('… its Upload page tiles include the boost routes and the custom sheets', uploadTypesOf(b2).includes('payment_detail') && kindsForSurface(b2, 'upload').some(k => k.custom_sheet_label))
const both = reportKindsVisible(ROWS, { pos: ['rq', 'b2bsoft'], carriers: ['verizon', 'boost'] }, {})
ck('a tenant with both POS sees both', [...B2B].every(k => keys(both).has(k)) && keys(both).has('sales_imei_phone'))
ck('a kind with POS AND carrier applies-to needs both to match', keys(b2).has('pos_inventory_recon') && !keys(reportKindsVisible(ROWS, { pos: ['b2bsoft'], carriers: ['verizon'] }, {})).has('pos_inventory_recon'))
const tenantRow = { key: 'verizon_device_payment_report', label: 'Verizon Device Payment Report', landing: 'other', applies_to_pos: [], applies_to_carrier: ['verizon'], defined_by: 'tenant', defined_by_org: 'x', sort_order: 500, is_active: true }
ck('a carrier-specific kind nobody defined is ABSENT', !keys(vz).has(tenantRow.key))
const nx = reportKindsVisible([...ROWS, tenantRow], VZ, {})
ck('once one tenant confirmed it (a house row defined_by tenant) the next tenant on that carrier sees it, provenance "defined by a tenant on this carrier"', nx.find(r => r.key === tenantRow.key)?.provenance === 'defined by a tenant on this carrier')
ck('… and a tenant on another carrier does not', !keys(reportKindsVisible([...ROWS, tenantRow], { pos: ['b2bsoft'], carriers: ['boost'] }, {})).has(tenantRow.key))
const wid = reportKindsVisible(ROWS, VZ, { 'kind:pos_activation_details': true })
ck('SUPER-ADMIN WIDENING (cap kind:<key> = show) shows a gated-out kind, recorded "widened by super-admin"', wid.find(r => r.key === 'pos_activation_details')?.provenance === 'widened by super-admin')
ck('a hide override hides one the rule would show', !keys(reportKindsVisible(ROWS, VZ, { 'kind:x_report': false })).has('x_report'))
ck('a null override is auto', !keys(reportKindsVisible(ROWS, VZ, { 'kind:pos_activation_details': null })).has('pos_activation_details'))
ck('a carrier: or pos: override never reaches a kind (separate namespaces)', !keys(reportKindsVisible(ROWS, VZ, { 'pos:pos_activation_details': true, 'carrier:/x': true })).has('pos_activation_details'))
const unk = reportKindsVisible(ROWS, { pos: [], carriers: [], reasons: ['no POS declared', 'no carrier declared'] }, {})
ck('UNKNOWN declaration shows LESS: specific kinds hidden, agnostic ones shown', inter(keys(unk), SPECIFIC).length === 0 && keys(unk).has('sales_imei_phone'))
ck('a null declaration is treated as unknown (never as "any")', inter(keys(reportKindsVisible(ROWS, null, undefined)), SPECIFIC).length === 0)
ck('an inactive row never shows', !keys(reportKindsVisible(ROWS.map(r => r.key === 'x_report' ? { ...r, is_active: false } : r), VZ, {})).has('x_report'))
ck('a backend-supplied provenance is kept ("your override")', reportKindsVisible([{ ...ROWS[0], provenance: 'your override' }], VZ, {})[0].provenance === 'your override')
ck('rows come back in registry order', vz.every((r, i) => i === 0 || (vz[i - 1].sort_order ?? 100) <= (r.sort_order ?? 100)))
ck('kindApplies: empty applies-to = any', kindApplies({ applies_to_pos: [], applies_to_carrier: [] }, { pos: [], carriers: [] }) === true)
ck('kindCapOverride: show/hide/auto', kindCapOverride({ 'kind:a': true }, 'a') === true && kindCapOverride({ 'kind:a': false }, 'a') === false && kindCapOverride({}, 'a') === null)

console.log('\nD. the email-imports selectors — rules from the registry, never a list in the page')
const RULES = [{ pattern: '*Sales*Transaction*Details*', upload_type: 'daily_sales' }, { pattern: '*Inventory*Aging*', upload_type: 'inventory_aging' }, { pattern: '*X-Report*', upload_type: 'x_report' }]
ck('globToRegex matches the way the sweep does (case-insensitive, * = anything)', globToRegex('*Sales*Transaction*Details*').test('B2B Sales Transaction Details 2026-09-01.xlsx') && !globToRegex('*Inventory*Aging*').test('sales.xlsx'))
ck('suggestRuleFrom: a name a registry rule matches → that rule', JSON.stringify(suggestRuleFrom('Inventory Aging Report.xlsx', RULES)) === JSON.stringify({ pattern: '*Inventory*Aging*', upload_type: 'inventory_aging', matched: true }))
ck('suggestRuleFrom: an unknown name → a token glob and NO upload type (the person picks; no guessed default)', (() => { const s = suggestRuleFrom('Weekly Bonus Summary.csv', RULES); return s.matched === false && s.upload_type === '' && s.pattern === '*Weekly*Bonus*Summary*' })())
ck('suggestRuleFrom with NO rules (an undeclared POS) never invents a type', suggestRuleFrom('Sales Transaction Details.xlsx', []).upload_type === '')
ck('labelForUploadType: the registry label for a route key, null when no visible kind names it', labelForUploadType(vz, 'x_report') === 'Cash register / X-report' && labelForUploadType(vz, 'payment_detail') === null)
ck('uploadTypesOf keeps registry order and dedupes', uploadTypesOf(b2).indexOf('daily_sales') < uploadTypesOf(b2).indexOf('payment_detail') && new Set(uploadTypesOf(b2)).size === uploadTypesOf(b2).length)
const ws = withheldSummary({ hidden: [{ key: 'a', label: 'A', why: 'x' }, { key: 'b', label: 'B', why: 'y' }], declaration: { pos: ['rq'], carriers: ['verizon'], reasons: [] } })
ck('withheldSummary says what is withheld and what was declared', /2 report kinds not offered — you declared POS rq · carrier verizon/.test(ws), ws)
ck('withheldSummary is empty when nothing is withheld', withheldSummary({ hidden: [], declaration: { pos: ['rq'], carriers: ['verizon'], reasons: [] } }) === '' && withheldSummary(null) === '')

console.log('\nH. WHERE AN UPLOAD SHOWS UP — the one selector over rows the backend decorated (landing identity, 2026-09-20)')
const decorated = vz.map(r => ({ ...r, shows_in: r.key === 'sales_imei_phone'
  ? { table: 'raw_sales', consumers: [{ screen: 'exec_mtd', label: 'Executive MTD', needs: ['department'], gate: true }], note: null }
  : r.key === 'x_report' ? { table: 'pos_tender_summary', consumers: [{ screen: 'closing_recon', label: 'Closing Reconciliation', needs: [], gate: false }], note: null } : undefined }))
ck('showsInFor by registry key → that row\'s consumers', showsInFor(decorated, 'sales_imei_phone')?.consumers?.[0]?.screen === 'exec_mtd')
ck('showsInFor by the upload route key a tile posts through (x_report) → the same row', showsInFor(decorated, 'x_report')?.table === 'pos_tender_summary')
ck('showsInFor for an undecorated / unknown key → null (never a guessed list)', showsInFor(decorated, 'commission_statement') === null && showsInFor(decorated, 'nope') === null)

console.log(`\n${fail ? 'FAIL' : 'PASS'} — ${pass} ok, ${fail} failed`)
process.exit(fail ? 1 : 0)
