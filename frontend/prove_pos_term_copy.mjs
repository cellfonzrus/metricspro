// Proof harness — THE POS NAME IN PAGE COPY (owner 2026-09-20: "if we have declared there is no b2b in
// verizon why does this message show — it should customize the message based on what POS is being used").
//
// Like prove_report_kinds.mjs, this does NOT re-implement the logic. It transpiles the REAL
// src/lib/report-labels.ts (pickPosTerm / neutralTerm — the ONE way copy names the tenant's POS),
// src/lib/report-kinds.ts (feedKindFor / feedApplies / notApplicableCopy — the applies?/loaded? two-step)
// and src/app/(platform)/commcalc/sales-recon/copy.ts (the Sales Feed Recon page's copy) with the project's
// own TypeScript compiler, and drives them over payloads shaped exactly like GET /report-labels and
// GET /report-kinds. The last section proves no vendor spelling is reachable from the measured pages'
// copy, using the vocabulary the CI guard DERIVES from the seeds (one derivation, asked for over the wire).
//
// Run:  node frontend/prove_pos_term_copy.mjs      (no network, no DB, no browser, no React)

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')
const require_ = createRequire(import.meta.url)
const ts = require_('typescript')

let pass = 0, fail = 0
const ck = (label, cond, extra) => { if (cond) { pass++; console.log(`  ok  ${label}`) } else { fail++; console.error(`  XX  ${label}${extra !== undefined ? '  — ' + JSON.stringify(extra).slice(0, 300) : ''}`) } }
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

const dir = mkdtempSync(join(tmpdir(), 'posterm-'))
async function loadModule(relPath, outName, rewrites = {}) {
  let src = readFileSync(join(HERE, relPath), 'utf8')
  src = src.replace(/^import\s[^\n]*from\s+'react'\s*$/m, '')
  src = src.replace(/^import\s[^\n]*from\s+'@\/lib\/client'\s*$/m, '')
  src = src.replace(/^import\s[^\n]*from\s+'@\/lib\/auth-context'\s*$/m, '')
  for (const [from, to] of Object.entries(rewrites)) src = src.replace(from, to)
  const js = ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
  const modPath = join(dir, outName)
  writeFileSync(modPath, js, 'utf8')
  return import(pathToFileURL(modPath).href)
}

const rl = await loadModule('src/lib/report-labels.ts', 'report-labels.mjs')
const cs = await loadModule('src/lib/carrier-scope.ts', 'carrier-scope.mjs')
const rk = await loadModule('src/lib/report-kinds.ts', 'report-kinds.mjs', { "from '@/lib/carrier-scope'": "from './carrier-scope.mjs'" })
const copy = await loadModule('src/app/(platform)/commcalc/sales-recon/copy.ts', 'sales-recon-copy.mjs', { "from '@/lib/report-kinds'": "from './report-kinds.mjs'" })
const { pickPosTerm, neutralTerm, POS_TERM_KEY } = rl
const { feedKindFor, feedApplies, notApplicableCopy } = rk
const { salesReconEmptyState, salesReconTabs, dailyFeedLabel, DAILY_FEED_ROUTE } = copy
for (const [n, f] of Object.entries({ pickPosTerm, neutralTerm, feedKindFor, feedApplies, notApplicableCopy, salesReconEmptyState, salesReconTabs, dailyFeedLabel }))
  must(typeof f === 'function', `${n} did not export a function`)
must(POS_TERM_KEY === 'pos_system', 'the term key is the mig-953 pos_system term')

// ── payloads shaped like GET /commcalc/report-labels (build_payload): terms carry ONLY preset/override keys
const EDITABLE = [{ key: 'processor', default: 'payment processor' }, { key: 'pos_system', default: 'POS' }]
const verizonRQ = { carriers: ['verizon'], default_carrier: 'verizon', columns: {}, banners: {}, editable_terms: EDITABLE,
  terms: { verizon: { pos_system: 'RQ' }, _: {} }, overrides: { columns: {}, banners: {}, terms: {} }, presets: {}, editable_columns: [], banner_keys: [] }
const boostB2B = { ...verizonRQ, carriers: ['boost'], default_carrier: 'boost', terms: { boost: { pos_system: 'b2bsoft' }, _: {} } }
const undeclared = { ...verizonRQ, carriers: ['cricket'], default_carrier: 'cricket', terms: { cricket: {}, _: {} } }
// build_payload folds the org's overrides into EVERY carrier map and into '_' (the no-carrier fallback)
const tenantOverride = { ...undeclared, terms: { cricket: { pos_system: 'Lightspeed' }, _: { pos_system: 'Lightspeed' } } }
const noCarrierRow = { ...undeclared, carriers: [], default_carrier: '', terms: { _: { pos_system: 'Lightspeed' } } }

console.log('\n— pickPosTerm: the declared term, else the registry\'s neutral noun —')
ck('Verizon tenant (house preset RQ) → pos "RQ", declared', (() => { const t = pickPosTerm(verizonRQ, 'verizon'); return t.pos === 'RQ' && t.posDeclared === true })(), pickPosTerm(verizonRQ, 'verizon'))
ck('Boost tenant (house preset b2bsoft) → the preset label, declared', (() => { const t = pickPosTerm(boostB2B, 'boost'); return t.pos === 'b2bsoft' && t.posDeclared })())
ck('undeclared (carrier with no preset, no override) → the NEUTRAL noun from editable_terms, not declared', (() => { const t = pickPosTerm(undeclared, 'cricket'); return t.pos === 'POS' && t.posDeclared === false })(), pickPosTerm(undeclared, 'cricket'))
ck('a tenant override on a carrier with no preset → declared with the override', (() => { const t = pickPosTerm(tenantOverride, 'cricket'); return t.pos === 'Lightspeed' && t.posDeclared })())
ck('a tenant with no carrier row yet → the "_" map still applies the override', (() => { const t = pickPosTerm(noCarrierRow, ''); return t.pos === 'Lightspeed' && t.posDeclared })())
ck('no payload at all (fetch failed) → the last-resort neutral noun, posLoaded false', (() => { const t = pickPosTerm(null, 'verizon', false); return t.pos === 'POS' && !t.posDeclared && t.posLoaded === false })())
ck('inactive lens falls back to the default carrier\'s map (pickTermMap ladder)', pickPosTerm(verizonRQ, '').pos === 'RQ')
ck('neutralTerm reads the registry default and never a vendor', neutralTerm(verizonRQ, 'pos_system', 'x') === 'POS' && neutralTerm({ ...verizonRQ, editable_terms: [] }, 'pos_system', 'POS') === 'POS')
ck('a blank preset value is not a declaration', !pickPosTerm({ ...verizonRQ, terms: { verizon: { pos_system: '  ' }, _: {} } }, 'verizon').posDeclared)

// ── the registry side: the REAL house seed, parsed out of mig 1010 (same parser as prove_report_kinds)
// every migration that seeds HOUSE report kinds (backend report_kinds.SEED_MIGRATIONS): 1010 + 1012's sales_by_invoice
const SEEDS = ['1010_report_kind_registry.sql', '1012_sales_by_invoice.sql']
const body = SEEDS.map(n => readFileSync(join(ROOT, 'database', 'migrations', n), 'utf8').split('upload_types, sort_order, custom_sheet_label) VALUES')[1].split('ON CONFLICT')[0]).join('\n')
const COLS = ['org_id', 'key', 'label', 'what_in_it', 'recognisable_columns', 'source_hint', 'applies_to_pos', 'applies_to_carrier', 'defined_by',
  'statement_type', 'landing', 'layout', 'signature_fields', 'requires_columns', 'excludes_columns', 'upload_types', 'sort_order', 'custom_sheet_label']
const arr = s => s.startsWith('{') ? (s.slice(1, -1).match(/"((?:[^"\\]|\\.)*)"/g) || []).map(x => x.slice(1, -1)) : s
const ROWS = []
for (const line of body.trim().split('\n')) {
  const l = line.trim().replace(/,$/, '')
  if (!l.startsWith('(')) continue
  const vals = []
  for (const m of l.slice(1, -1).matchAll(/'((?:[^']|'')*)'|(\d+)|(NULL)/g)) vals.push(m[1] !== undefined ? m[1].replace(/''/g, "'") : m[2] !== undefined ? Number(m[2]) : null)
  const r = {}; COLS.forEach((c, i) => { r[c] = typeof vals[i] === 'string' && (vals[i].startsWith('{')) ? arr(vals[i]) : vals[i] })
  r.is_active = true; ROWS.push(r)
}
must(ROWS.length > 10, 'parsed the house seed')
const visibleFor = (pos, carriers) => cs.reportKindsVisible(ROWS, { pos, carriers, source: 'test', reasons: [] }, {})
const rqVisible = visibleFor(['rq'], ['verizon'])
const b2bVisible = visibleFor(['b2bsoft'], ['boost'])

console.log('\n— feedKindFor / feedApplies: step 1 of applies?/loaded? —')
ck('the daily feed is found by its upload route key (daily_sales → sales_imei_phone)', feedKindFor(rqVisible, DAILY_FEED_ROUTE)?.key === 'sales_imei_phone')
ck('a kind is found by its registry key too', feedKindFor(b2bVisible, 'pos_activation_details')?.key === 'pos_activation_details')
ck('the seed as shipped: sales_imei_phone applies to ANY POS, so RQ has a daily-feed kind (branch 2 today)', !!feedKindFor(rqVisible, DAILY_FEED_ROUTE))
ck('the seed as shipped: Activation Details is a b2bsoft row — NOT defined for RQ', feedKindFor(rqVisible, 'pos_activation_details') === null && !!feedKindFor(b2bVisible, 'pos_activation_details'))
ck('feedApplies: not loaded → checking', feedApplies(false, null, null) === 'checking')
ck('feedApplies: fetch error → unknown (never hide a page on a lookup failure)', feedApplies(true, 'boom', null) === 'unknown')
ck('feedApplies: loaded, no kind → not_defined', feedApplies(true, null, null) === 'not_defined')
ck('feedApplies: loaded, kind → defined', feedApplies(true, null, rqVisible[0]) === 'defined')

console.log('\n— notApplicableCopy: the one sentence, with the tenant\'s term —')
const naRQ = notApplicableCopy({ pos: 'RQ', posDeclared: true, feedNoun: 'daily feed', kindNoun: 'daily-feed', purpose: 'reconcile the monthly file against' })
ck('declared RQ, no daily kind → the owner\'s sentence, verbatim',
  naRQ === 'You declared RQ as your POS. No daily-feed report kind is defined for RQ yet, so there is nothing to reconcile the monthly file against. When RQ exports a daily feed, add it under Onboarding → Intake and this page will use it.', naRQ)
const naNone = notApplicableCopy({ pos: 'POS', posDeclared: false, feedNoun: 'daily feed', purpose: 'reconcile the monthly file against' })
ck('undeclared → says no POS is declared and where to declare it; never "You declared POS"', /^No POS is declared/.test(naNone) && /Implementation wizard/.test(naNone) && !/You declared/.test(naNone), naNone)

console.log('\n— Sales Feed Recon empty state: the four tenant states —')
const st = (applies, hasFeed, pos, posDeclared) => salesReconEmptyState({ applies, hasFeed, pos, posDeclared, period: 'August 2026' })
const e1 = st('not_defined', false, 'RQ', true)
ck('(declared RQ, no daily kind) → not_applicable with the term, never an upload invitation', e1?.kind === 'not_applicable' && e1.text === naRQ && !/daily_sales|FTP/.test(e1.text), e1)
const e2 = st('defined', false, 'RQ', true)
ck('(declared RQ, daily kind visible, nothing loaded) → the original message with RQ substituted',
  e2?.kind === 'not_loaded' && e2.text.startsWith('No daily RQ feed loaded for August 2026 yet. Once the daily feed lands (via FTP Auto-Import or a manual “daily_sales” upload)'), e2)
ck('(declared b2bsoft, feed loaded) → no banner', st('defined', true, 'b2bsoft', true) === null)
const e4 = st('defined', false, 'POS', false)
ck('(undeclared, nothing loaded) → the neutral noun: "No daily POS feed loaded …"', e4?.kind === 'not_loaded' && e4.text.startsWith('No daily POS feed loaded for August 2026 yet.'), e4)
ck('registry still loading → "checking", not a verdict', st('checking', false, 'RQ', true)?.kind === 'checking')
ck('registry unknown (fetch failed) → falls through to the loaded? step (never claims "not defined")', st('unknown', false, 'RQ', true)?.kind === 'not_loaded' && st('unknown', true, 'RQ', true) === null)
ck('the daily-feed tile and drill title carry the term', dailyFeedLabel('RQ') === 'Daily RQ feed' && salesReconTabs('RQ')[0].blurb.startsWith('In the daily RQ feed'))
ck('the undeclared tab blurb reads with the neutral noun', salesReconTabs('POS')[0].blurb.startsWith('In the daily POS feed'))

// ── no vendor spelling reachable from the measured pages' copy — the CI guard's DERIVED vocabulary
console.log('\n— the 30 measured files + this PR\'s new modules: no vendor spelling in code (guard-derived vocabulary) —')
const MEASURED = [
  'accounts/balance-sheet', 'accounts/inventory', 'closing/_lib/SubmissionsTable.tsx', 'closing/cash-config', 'closing/count-config',
  'closing/management', 'closing', 'closing/pickup', 'closing/readiness', 'closing/recon', 'closing/tender-config',
  'commcalc/_lib/uploadGuard.tsx', 'commcalc/activations', 'commcalc/asset/inventory-recon', 'commcalc/commission-discrepancy',
  'commcalc/connectors', 'commcalc/device-history/DeviceHistoryLookup.tsx', 'commcalc/email-imports', 'commcalc/exec/mtd',
  'commcalc/expenses', 'commcalc/ftp-imports', 'commcalc/imei-rebates', 'commcalc/imei-recon', 'commcalc/implementation',
  'commcalc/sales-derive', 'commcalc/sales-recon', 'commcalc/sales-report', 'commcalc/settings', 'commcalc/upload', 'crm/settings',
]
let vocab
try {
  vocab = JSON.parse(execFileSync('python3', [join(ROOT, 'backend', 'harness_carrier_vocab_guard.py'), '--print-pos-vocab'], { encoding: 'utf8' }))
} catch (e) { must(false, 'the guard must print its derived vocabulary: ' + e.message) }
ck('the guard derives the seeds\' vendor spellings (b2bsoft / b2b soft / b2b / rq) — nothing listed twice', ['b2bsoft', 'b2b soft', 'b2b', 'rq'].every(k => k in vocab.spellings), vocab.spellings)
const rx = new RegExp(vocab.regex, 'i')
// DATA identifiers (field names / connector ids / API paths) are not copy; the guard's own allow set names them.
const ALLOWED_FILES = new Set(['commcalc/upload', 'commcalc/connectors', 'commcalc/expenses'])
const stripComments = src => src.split('\n').filter(l => { const s = l.trim(); return !(s.startsWith('//') || s.startsWith('*') || s.startsWith('/*') || s.startsWith('{/*')) }).join('\n')
const copyOnly = src => stripComments(src)
  .replace(/\/api\/v1\/[^'"`\s]*/g, '')                       // API paths (route data)
  .replace(/[a-zA-Z_$][\w$]*\??\.[\w$?.]+/g, '')                // member chains: data.b2b_loaded, g.b2b?.cash
  .replace(/\b[\w$]*_[\w$]*\b/g, '')                            // snake_case identifiers: b2b_loaded, b2b_acc_gp
  .replace(/(^|[{,;(]\s*)[\w$]+\??:/gm, '$1')                   // object / type keys: { asset: number; b2b: number }
let leaks = []
for (const m of MEASURED) {
  const rel = m.endsWith('.tsx') ? m : `${m}/page.tsx`
  const src = readFileSync(join(HERE, 'src', 'app', '(platform)', rel), 'utf8')
  const hit = copyOnly(src).match(rx)
  if (hit && !ALLOWED_FILES.has(m)) leaks.push(`${rel}: ${hit[0]}`)
}
ck(`no vendor spelling reachable from the ${MEASURED.length} measured pages' copy (3 data-id files excused by the guard)`, leaks.length === 0, leaks)
for (const f of ['src/lib/report-labels.ts', 'src/lib/report-kinds.ts', 'src/app/(platform)/commcalc/sales-recon/copy.ts', 'src/lib/reports.ts'])
  ck(`no vendor spelling in ${f} (code)`, !copyOnly(readFileSync(join(HERE, f), 'utf8')).match(rx))
ck('the uploadGuard reason takes the term as a parameter and defaults to the neutral noun', /readUploadOutcome\(r: any, unit = 'row\(s\)', pos = 'POS'\)/.test(readFileSync(join(HERE, 'src/app/(platform)/commcalc/_lib/uploadGuard.tsx'), 'utf8')))
const hookSrc = readFileSync(join(HERE, 'src/lib/report-labels.ts'), 'utf8')
ck('useReportLabels returns pos/posDeclared and usePosTerm is a wrapper over it (one fetch, one resolution)', /\.\.\.posTerm\s*\}/.test(hookSrc) && /export function usePosTerm\(\): PosTerm \{\s*const \{ pos, posDeclared, posLoaded \} = useReportLabels\(\)/.test(hookSrc))
const users = MEASURED.filter(m => { const rel = m.endsWith('.tsx') ? m : `${m}/page.tsx`; return /usePosTerm\(|useReportLabels\(/.test(readFileSync(join(HERE, 'src', 'app', '(platform)', rel), 'utf8')) })
ck(`every measured page whose copy names the POS reads the term (${users.length} pages call usePosTerm/useReportLabels)`, users.length >= 22, users)

console.log(`\n${fail ? 'FAIL' : 'PASS'} — ${pass} ok, ${fail} failed`)
process.exit(fail ? 1 : 0)
