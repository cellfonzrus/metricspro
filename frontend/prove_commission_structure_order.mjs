// Proof harness — THE TWO WAYS TO CALCULATE EMPLOYEE COMMISSION, in the owner's order (2026-09-20).
//
// Owner, verbatim: "for employe commissioin structure it shows 2 options… move the one from executive mtd on
// top and the other one at the bottom and show on top that there are 2 ways to calculate employee commission:
// from the reporting on Executive MTD to get paid flat or set up a customized commission payout using the
// following steps — this should be a platform wide fix not just from verizon".
//
// Like prove_pos_term_copy.mjs, this does NOT re-implement the logic. It transpiles the REAL
// src/app/(platform)/commcalc/_lib/commissionWays.ts (the ONE home of the header + the order) and
// src/lib/report-kinds.ts (feedsForScreen — where the data feeding Option 1 is uploaded, the inverse of
// showsInFor over the same GET /report-kinds payload) with the project's own TypeScript compiler, drives them
// over payloads shaped like the backend's, and then reads the two surfaces that present the choice as text to
// pin that (1) the header renders first, (2) Option 1 (Exec MTD flat) precedes Option 2 (custom steps),
// (3) every screen key the copy links resolves to a registered ScreenLink destination backed by a NAV href,
// (4) no vendor / carrier spelling is reachable, and (5) neither surface spells the header or an option title
// itself — a second copy is what would let the order drift. Negative controls prove each check can go red.
//
// Run:  node frontend/prove_commission_structure_order.mjs      (no network, no DB, no browser, no React)

import { readFileSync, writeFileSync, mkdtempSync, readdirSync, statSync } from 'node:fs'
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

const dir = mkdtempSync(join(tmpdir(), 'commways-'))
async function loadModule(relPath, outName, rewrites = {}) {
  let src = readFileSync(join(HERE, relPath), 'utf8')
  src = src.replace(/^import\s[^\n]*from\s+'react'\s*$/m, '')
  src = src.replace(/^import\s[^\n]*from\s+'@\/lib\/client'\s*$/m, '')
  src = src.replace(/^import\s+type\s[^\n]*from\s+'@\/components\/ScreenLink'\s*$/m, '')
  for (const [from, to] of Object.entries(rewrites)) src = src.replace(from, to)
  const js = ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
  const modPath = join(dir, outName)
  writeFileSync(modPath, js, 'utf8')
  return import(pathToFileURL(modPath).href)
}

const WAYS_PATH = 'src/app/(platform)/commcalc/_lib/commissionWays.ts'
const HEADER_PATH = 'src/app/(platform)/commcalc/_lib/CommissionWaysHeader.tsx'
const STRUCTURE_PATH = 'src/app/(platform)/commcalc/commission-structure/page.tsx'
const EDITOR_PATH = 'src/app/(platform)/commcalc/commission-plans/page.tsx'
const cw = await loadModule(WAYS_PATH, 'commissionWays.mjs')
await loadModule('src/lib/carrier-scope.ts', 'carrier-scope.mjs')   // report-kinds imports the transpiled twin
const rk = await loadModule('src/lib/report-kinds.ts', 'report-kinds.mjs', { "from '@/lib/carrier-scope'": "from './carrier-scope.mjs'" })
const { COMMISSION_WAYS, COMMISSION_WAYS_HEADER, EXEC_MTD_FLAT, CUSTOM_STEPS, wayForBasis, optionHeading } = cw
const { feedsForScreen, showsInFor } = rk
for (const [n, f] of Object.entries({ wayForBasis, optionHeading, feedsForScreen, showsInFor }))
  must(typeof f === 'function', `${n} did not export a function`)
must(Array.isArray(COMMISSION_WAYS), 'COMMISSION_WAYS is the ordered list')

// ── A. the ONE list: header text, two ways, Option 1 = Exec MTD flat, Option 2 = custom steps ─────────
console.log('\n— A. the header and the order, read from the one module —')
ck('the header is the owner\'s sentence', COMMISSION_WAYS_HEADER === 'There are 2 ways to calculate employee commission', COMMISSION_WAYS_HEADER)
ck('exactly two ways', COMMISSION_WAYS.length === 2)
ck('Option 1 (first) pays flat from the Executive MTD reporting — basis exec_mtd, reads the exec_mtd screen',
  COMMISSION_WAYS[0].n === 1 && COMMISSION_WAYS[0].key === 'exec_mtd_flat' && COMMISSION_WAYS[0].basis === 'exec_mtd'
  && COMMISSION_WAYS[0].reads === 'exec_mtd' && /^Pay flat from Executive MTD reporting$/.test(COMMISSION_WAYS[0].title), COMMISSION_WAYS[0])
ck('Option 2 (second) is the customised payout "using the following steps" — basis rules, reads no report',
  COMMISSION_WAYS[1].n === 2 && COMMISSION_WAYS[1].key === 'custom_steps' && COMMISSION_WAYS[1].basis === 'rules'
  && COMMISSION_WAYS[1].reads === null && /customised commission payout using the following steps/.test(COMMISSION_WAYS[1].title), COMMISSION_WAYS[1])
ck('EXEC_MTD_FLAT / CUSTOM_STEPS are the same objects as the list (no second copy)', EXEC_MTD_FLAT === COMMISSION_WAYS[0] && CUSTOM_STEPS === COMMISSION_WAYS[1])
ck('each way carries one layman sentence and its own page anchor', COMMISSION_WAYS.every(w => w.layman.length > 40 && /^option-[12]-/.test(w.anchor)) && EXEC_MTD_FLAT.anchor !== CUSTOM_STEPS.anchor)
ck('the two bases are exactly the mig-298 CHECK constraint values (rules | exec_mtd)', (() => {
  const sql = readFileSync(join(ROOT, 'database', 'migrations', '298_commission_plan_exec_mtd_basis.sql'), 'utf8')
  const m = sql.match(/CHECK \(commission_basis IN \(([^)]*)\)\)/)
  const allowed = m ? [...m[1].matchAll(/'([^']+)'/g)].map(x => x[1]).sort() : []
  return JSON.stringify(allowed) === JSON.stringify(COMMISSION_WAYS.map(w => w.basis).sort())
})())
ck('optionHeading spells "Option N — <title>"', optionHeading(EXEC_MTD_FLAT) === 'Option 1 — Pay flat from Executive MTD reporting' && optionHeading(CUSTOM_STEPS).startsWith('Option 2 — Set up a customised'))

console.log('\n— wayForBasis: READS a plan\'s persisted basis; never a third answer —')
ck("'exec_mtd' → Option 1", wayForBasis('exec_mtd') === EXEC_MTD_FLAT)
ck("case / whitespace tolerant ('  EXEC_MTD ')", wayForBasis('  EXEC_MTD ') === EXEC_MTD_FLAT)
ck("'rules' → Option 2", wayForBasis('rules') === CUSTOM_STEPS)
ck('NULL / undefined / \'\' → Option 2 (the mig-298 column default)', wayForBasis(null) === CUSTOM_STEPS && wayForBasis(undefined) === CUSTOM_STEPS && wayForBasis('') === CUSTOM_STEPS)
ck('an unknown value → Option 2 (what the backend collapses it to), never a crash', wayForBasis('something_else') === CUSTOM_STEPS)

// ── B. every screen key the copy links is a registered ScreenLink destination backed by a NAV href ──
console.log('\n— B. links resolve to registered screen keys, and those keys to NAV hrefs —')
const screenSrc = readFileSync(join(HERE, 'src/components/ScreenLink.tsx'), 'utf8')
const screensBody = screenSrc.split('export const SCREENS')[1].split('\n}\n')[0]
const SCREENS = {}
for (const m of screensBody.matchAll(/^\s{2}(\w+): \{[\s\S]*?href: '([^']+)'/gm)) SCREENS[m[1]] = m[2]
must(Object.keys(SCREENS).length >= 30, `parsed only ${Object.keys(SCREENS).length} SCREENS entries`)
const rbacSrc = readFileSync(join(HERE, 'src/lib/rbac.ts'), 'utf8')
const NAV_HREFS = new Set([...rbacSrc.matchAll(/href: '([^']+)'/g)].map(m => m[1]))
const navBacked = k => k in SCREENS && NAV_HREFS.has(SCREENS[k].split('#')[0])
ck('Option 1\'s `reads` (exec_mtd) is a registered screen with a NAV href', navBacked(EXEC_MTD_FLAT.reads) && SCREENS.exec_mtd === '/commcalc/exec/mtd')
ck('the two surfaces that present the choice are registered screens (commission_structure, incentive_plans) with NAV hrefs',
  navBacked('commission_structure') && navBacked('incentive_plans') && SCREENS.commission_structure === '/commcalc/commission-structure' && SCREENS.incentive_plans === '/commcalc/commission-plans')
ck('the "define one under …" fallback (onboarding_intake) is registered', navBacked('onboarding_intake'))
// the `where` screens the backend can emit for an upload (landing_identity.where_to_upload) — read from its source, not listed here
const liSrc = readFileSync(join(ROOT, 'backend', 'app', 'modules', 'commcalc', 'landing_identity.py'), 'utf8')
const whereFn = liSrc.split('def where_to_upload(')[1].split('\ndef ')[0]
const whereScreens = [...new Set([...whereFn.matchAll(/"screen": "(\w+)"/g)].map(m => m[1]))]
ck(`every \`where.screen\` the backend can emit resolves in SCREENS with a NAV href (${whereScreens.join(', ')})`, whereScreens.length >= 2 && whereScreens.every(navBacked), whereScreens.filter(k => !navBacked(k)))
const headerSrcRaw = readFileSync(join(HERE, HEADER_PATH), 'utf8')
const headerSrc = headerSrcRaw.split('\n').filter(l => { const t = l.trim(); return !(t.startsWith('//') || t.startsWith('*') || t.startsWith('/*') || t.startsWith('{/*')) }).join('\n')
const linked = [...headerSrc.matchAll(/<ScreenLink to="(\w+)"/g)].map(m => m[1])
ck(`every literal <ScreenLink to=…> in the header component is a registered, NAV-backed key (${linked.join(', ')})`, linked.length >= 2 && linked.every(navBacked), linked.filter(k => !navBacked(k)))

// ── C. feedsForScreen: where the data feeding Option 1 is uploaded — the inverse of showsInFor ──────
console.log('\n— C. feedsForScreen over a payload shaped like GET /report-kinds (shows_in + where per row) —')
const row = (key, label, consumers, where, extra = {}) => ({ key, label, landing: 'sales', layout: key, upload_types: [], applies_to_pos: [], applies_to_carrier: [], is_active: true,
  shows_in: consumers === null ? null : { table: consumers.length ? 'raw_sales' : null, consumers: consumers.map(s => ({ screen: s, label: s, needs: [], gate: false })), note: null },
  where, ...extra })
const ROWS = [
  row('sales_imei_phone', 'Sales report with IMEI and phone number', ['exec_mtd', 'sales_report', 'gp_report'], { screen: 'onboarding_intake', label: 'Onboarding — Commission Intake', upload_types: [] }),
  row('inventory_on_hand', 'Inventory on hand', ['inventory_recon'], { screen: 'upload_files', label: 'Upload Files', upload_types: ['inventory'] }),
  row('residual_statement', 'Residual statement', null, { screen: 'onboarding_intake', label: 'Onboarding — Commission Intake', upload_types: [] }),
  row('pos_activation_details', 'Activation details', ['exec_mtd'], { screen: 'upload_files', label: 'Upload Files', upload_types: ['activation_details'] }),
  row('x_report', 'X-report', [], { screen: 'onboarding_intake', label: 'Onboarding — Commission Intake', upload_types: [] }),
]
const feeds = feedsForScreen(ROWS, 'exec_mtd')
ck('the kinds whose shows_in names exec_mtd, in registry order, each with its upload page', feeds.map(f => f.key).join(',') === 'sales_imei_phone,pos_activation_details'
  && feeds[0].where.screen === 'onboarding_intake' && feeds[1].where.screen === 'upload_files', feeds)
ck('a kind with no shows_in, no consumers, or other consumers is not a feed', !feeds.some(f => ['residual_statement', 'x_report', 'inventory_on_hand'].includes(f.key)))
ck('a screen nothing feeds → [] (the component then says so and points at the intake)', feedsForScreen(ROWS, 'kpi').length === 0 && feedsForScreen([], 'exec_mtd').length === 0)
ck('a row with no `where` yields where: null (the label still renders, no invented page)', feedsForScreen([row('k', 'K', ['exec_mtd'], undefined)], 'exec_mtd')[0].where === null)
ck('it IS the inverse of showsInFor over the same rows (row ∈ feeds ⇔ showsInFor(row).consumers names the screen)',
  ROWS.every(r => (showsInFor(ROWS, r.key)?.consumers || []).some(c => c.screen === 'exec_mtd') === feeds.some(f => f.key === r.key)))
ck('the hook exposes feedsFor beside showsIn (one fetch, one visible set)', /const feedsFor = useCallback\(\(screen: string\): FeedForScreen\[\] => feedsForScreen\(visible, screen\)/.test(readFileSync(join(HERE, 'src/lib/report-kinds.ts'), 'utf8')))

// ── D. the two surfaces: header first, Option 1 above Option 2 above the steps; no second copy ─────
console.log('\n— D. the Employee Commission Structure page: header first, Option 1 above Option 2 above steps 1–6 —')
// Comments are not copy: every "spells it itself" check runs over the source with comment lines removed.
const stripComments = src => src.split('\n').filter(l => { const s = l.trim(); return !(s.startsWith('//') || s.startsWith('*') || s.startsWith('/*') || s.startsWith('{/*')) }).join('\n')
const page = readFileSync(join(HERE, STRUCTURE_PATH), 'utf8')
const pageCode = stripComments(page)
const idx = (src, needle) => { const i = src.indexOf(needle); must(i >= 0, `"${needle}" not found`); return i }
const pageOrder = src => {
  const at = {
    header: src.indexOf('<CommissionWaysHeader'),
    option1: src.indexOf('id={EXEC_MTD_FLAT.anchor}'),
    option1Compute: src.indexOf('📈 Commission from Exec MTD'),
    option2: src.indexOf('id={CUSTOM_STEPS.anchor}'),
    step1: src.indexOf('{/* STEP 1'),
    step6: src.indexOf('{/* STEP 6'),
    apply: src.indexOf('{/* APPLY'),
  }
  const seq = ['header', 'option1', 'option1Compute', 'option2', 'step1', 'step6', 'apply']
  const ok = seq.every((k, i) => at[k] >= 0 && (i === 0 || at[k] > at[seq[i - 1]]))
  return { ok, at }
}
const po = pageOrder(page)
ck('header → Option 1 (Exec MTD flat, with its compute) → Option 2 → Step 1 … Step 6 → Apply, in that order', po.ok, po.at)
const returnAt = idx(page, '  return (\n    <div style={{ maxWidth: 960 }}>')
ck('the header is the first card after the page title (nothing renders between the intro and it)',
  po.at.header > returnAt && !/className="card"/.test(page.slice(returnAt, po.at.header)))
ck('the page renders the option headings THROUGH optionHeading(EXEC_MTD_FLAT / CUSTOM_STEPS), not its own words',
  page.includes('{optionHeading(EXEC_MTD_FLAT)}') && page.includes('{optionHeading(CUSTOM_STEPS)}'))
ck('the page never spells the header sentence or an option title (the module is the only home)',
  !pageCode.includes(COMMISSION_WAYS_HEADER) && !COMMISSION_WAYS.some(w => pageCode.includes(w.title)))
ck('the selected plan\'s persisted basis is READ into the header and never written by this page',
  /<CommissionWaysHeader selectedBasis=\{selected\?\.commission_basis\} planName=\{selected\?\.name\} \/>/.test(page)
  && !/commission_basis:\s/.test(page) && !/commission-mtd\/[^'`]*save/.test(page))
ck('ONE plan select implementation (PlanPicker) mounted in Option 1 and in Step 1, bound to the same state',
  (page.match(/<PlanPicker plans=\{plans\} value=\{selId\} onChange=\{setSelId\}/g) || []).length === 2 && (page.match(/<select className="input" value=\{selId\}/g) || []).length === 0
  && po.at.option1 < idx(page, '<PlanPicker plans={plans} value={selId} onChange={setSelId} disabled={loading} />') && (page.match(/function PlanPicker\(/g) || []).length === 1)
ck('the compute behind Option 1 is the SAME read-only endpoint as before (GET /commission-mtd/{period}, plan_id + rates + acc_pct)',
  /api\(`\/api\/v1\/commcalc\/commission-mtd\/\$\{encodeURIComponent\(period\)\}\$\{q\}`\)/.test(page) && page.includes('&rates=${encodeURIComponent(rateStr)}&acc_pct='))
ck('steps 1–6 are all still there, numbered, below Option 2', [1, 2, 3, 4, 5, 6].every(n => { const i = page.indexOf(`<span style={sectionNum}>${n}</span>`); return i > po.at.option2 }))

console.log('\n— the Incentive Plans editor (the other surface that presents the choice) mounts the SAME header —')
const editor = readFileSync(join(HERE, EDITOR_PATH), 'utf8')
ck('the editor imports and mounts CommissionWaysHeader (compact) with the draft plan\'s persisted basis, above the plan form',
  /import CommissionWaysHeader from '\.\.\/_lib\/CommissionWaysHeader'/.test(editor)
  && /<CommissionWaysHeader compact selectedBasis=\{draft\?\.commission_basis\} planName=\{draft\?\.name\}/.test(editor)
  && editor.indexOf('<CommissionWaysHeader') < editor.indexOf('{/* editor'))
ck('its Option 1 anchor is the "Make Executive MTD this plan\'s commission basis" box and its Option 2 anchor is the rules form',
  editor.includes("anchors={{ exec_mtd_flat: '#exec-mtd-basis', custom_steps: '#plan-rules' }}")
  && editor.includes('id="exec-mtd-basis"') && editor.includes('id="plan-rules"')
  && editor.indexOf('id="exec-mtd-basis"') < editor.indexOf('Make Executive MTD this plan’s commission basis'))
ck('the editor still owns the persisted choice exactly as before (the checkbox writes commission_basis; the header does not)',
  editor.includes("commission_basis: e.target.checked ? 'exec_mtd' : 'rules'") && !headerSrc.includes('commission_basis:'))
ck('the editor never spells the header sentence either', !editor.includes(COMMISSION_WAYS_HEADER))

console.log('\n— the header component dereferences the module (it is not a second copy) —')
ck('CommissionWaysHeader maps COMMISSION_WAYS in list order and prints COMMISSION_WAYS_HEADER; it spells neither',
  headerSrc.includes('{COMMISSION_WAYS.map(w => (') && headerSrc.includes('{COMMISSION_WAYS_HEADER}')
  && !headerSrc.includes(COMMISSION_WAYS_HEADER) && !COMMISSION_WAYS.some(w => headerSrc.includes(w.title)))
ck('the upload pages feeding Option 1 come from useReportKinds().feedsFor(<the way\'s reads key>) — no list, no href',
  headerSrc.includes('const { loaded, error, feedsFor } = useReportKinds()') && headerSrc.includes('feedsFor(screen)') && headerSrc.includes('const screen = EXEC_MTD_FLAT.reads')
  && !/href: '\//.test(headerSrc) && !/\/commcalc\//.test(headerSrc))
ck('the header links the Executive MTD and each feed\'s upload page through ScreenLink (self-gated), never a raw href',
  headerSrc.includes('<ScreenLink to={screen} />') && headerSrc.includes('<ScreenLink to={f.where.screen}>') && !headerSrc.includes('<Link href'))
// ONE home across the whole frontend source
const walk = (d, out = []) => { for (const n of readdirSync(d)) { const p = join(d, n); if (statSync(p).isDirectory()) walk(p, out); else if (/\.(tsx?|mjs|js)$/.test(n)) out.push(p) } return out }
const holders = walk(join(HERE, 'src')).filter(p => stripComments(readFileSync(p, 'utf8')).includes(COMMISSION_WAYS_HEADER)).map(p => p.slice(HERE.length + 1))
ck('exactly ONE file under src/ carries the header sentence in code (the module; comments excluded)', holders.length === 1 && holders[0] === WAYS_PATH, holders)
const titleHolders = walk(join(HERE, 'src')).filter(p => { const c = stripComments(readFileSync(p, 'utf8')); return COMMISSION_WAYS.some(w => c.includes(w.title)) }).map(p => p.slice(HERE.length + 1))
ck('exactly ONE file under src/ carries an option title in code', titleHolders.length === 1 && titleHolders[0] === WAYS_PATH, titleHolders)

// ── E. no vendor / carrier spelling reachable — the CI guard's DERIVED vocabulary, plus the guard itself ─
console.log('\n— E. platform-wide: no vendor / carrier spelling in the new copy —')
let vocab
try {
  vocab = JSON.parse(execFileSync('python3', [join(ROOT, 'backend', 'harness_carrier_vocab_guard.py'), '--print-pos-vocab'], { encoding: 'utf8' }))
} catch (e) { must(false, 'the guard must print its derived vocabulary: ' + e.message) }
const posRx = new RegExp(vocab.regex, 'i')
for (const f of [WAYS_PATH, HEADER_PATH]) ck(`no POS vendor spelling in ${f.split('/').pop()} (code)`, !stripComments(readFileSync(join(HERE, f), 'utf8')).match(posRx))
const carrierRx = /verizon|boost|cricket|total\s+wireless|vidapay|t-?cetra|luxelink|cellfonz|\bvip\b|\bacima\b|\bepay\b/i
ck('no carrier or tenant name in the module or the header component (the owner\'s "platform wide, not just from verizon")',
  ![WAYS_PATH, HEADER_PATH].some(f => carrierRx.test(stripComments(readFileSync(join(HERE, f), 'utf8')))))
ck('the copy the module ships (header, titles, sentences) names no carrier, POS or tenant',
  ![COMMISSION_WAYS_HEADER, ...COMMISSION_WAYS.flatMap(w => [w.title, w.layman])].some(t => carrierRx.test(t) || posRx.test(t)))
let guardOk = true
try { execFileSync('python3', [join(ROOT, 'backend', 'harness_carrier_vocab_guard.py')], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }) } catch { guardOk = false }
ck('backend/harness_carrier_vocab_guard.py (the CI guard: carrier + POS axes over all of frontend/src) is green', guardOk)

// ── F. negative controls — every ordering check can go red ──────────────────────────────────────────
console.log('\n— F. negative controls —')
const reversed = [...COMMISSION_WAYS].reverse()
ck('NEG a reversed list → Option 1 is no longer the Exec MTD flat way', !(reversed[0].key === 'exec_mtd_flat' && reversed[0].n === 1))
const swapped = page.replace('{/* STEP 1', '{/* STEP_1_MOVED').replace('id={EXEC_MTD_FLAT.anchor}', 'id={MOVED}')
ck('NEG the Option 1 card removed from its place → the page-order check goes red', !pageOrder(swapped).ok)
const secondCopy = pageCode.replace('{optionHeading(EXEC_MTD_FLAT)}', `Option 1 — ${EXEC_MTD_FLAT.title}`)
ck('NEG a page that spells the option title itself (in code, not a comment) → the one-home check goes red', secondCopy.includes(EXEC_MTD_FLAT.title) && !pageCode.includes(EXEC_MTD_FLAT.title))
const belowOption2 = page.replace('<CommissionWaysHeader', '<Elsewhere').replace('{/* STEP 6', '<CommissionWaysHeader />\n      {/* STEP 6')
ck('NEG the header mounted below the steps → red', !pageOrder(belowOption2).ok)
ck('NEG a `where.screen` the registry does not know → not NAV-backed', !navBacked('no_such_screen'))

console.log(`\n${fail ? 'FAIL' : 'PASS'} — ${pass} ok, ${fail} failed`)
process.exit(fail ? 1 : 0)
