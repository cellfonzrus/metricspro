#!/usr/bin/env node
// PROOF for the Google-rating surfaces added to the commission module (owner directive 2026-08-06,
// "surface an employee's Google store rating(s) wherever the employee appears").
//
// The three properties that matter, none of which a screenshot can demonstrate:
//   1. NO MONEY MOVES, NO EXPORT CHANGES WITHOUT DATA — with no ratings on screen, every export payload
//      /commcalc/reports builds is byte-identical to the pre-change one (same sheets, same columns, same
//      cells). The rating column exists only when the page actually rendered chips.
//   2. THE PAY-PRIVACY RULE STILL HOLDS — the Individual-Rep statement carries the SELECTED rep's rating
//      and no other rep's, exactly like every other cell on that tab.
//   3. THE CONSUMER DEGRADES SILENTLY — every call into mod-people's (possibly not-yet-deployed)
//      google-reviews endpoints is caught, logs nothing, and carries the /api/v1 prefix + org scope;
//      no commission page imports mod-people's own GoogleReviewsCard.
//
// Run:  node frontend/tools/google-ratings-proof.mjs
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SRC = path.join(FRONTEND, 'src')
const OUT = mkdtempSync(path.join(FRONTEND, '.grproof-'))
const CFG = path.join(FRONTEND, `tsconfig${path.basename(OUT)}.json`)
const cleanup = () => { rmSync(OUT, { recursive: true, force: true }); rmSync(CFG, { force: true }) }
process.on('exit', cleanup)

let pass = 0, fail = 0
const ok = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}
const section = t => console.log(`\n${t}`)
const read = p => readFileSync(path.join(SRC, p), 'utf8')

writeFileSync(CFG, JSON.stringify({
  extends: './tsconfig.json',
  compilerOptions: { noEmit: false, incremental: false, outDir: path.basename(OUT), module: 'es2020', target: 'es2020', declaration: false },
  files: ['src/app/(platform)/commcalc/_lib/commissionExport.ts'],
  include: [],
}, null, 2))
execFileSync('npx', ['tsc', '-p', CFG], { cwd: FRONTEND, stdio: ['ignore', 'ignore', 'inherit'] })
const { buildCommissionExport } = await import(path.join(OUT, 'app', '(platform)', 'commcalc', '_lib', 'commissionExport.js'))

// ── fixture: 3 reps, distinct ratings so a leak is unmistakable ───────────────────────────────────
const mk = (id, name, store, market, payout) => ({
  epay_salesperson: id, storeops_name: name, store, market,
  tier: 1, kpis_met: 5, total_kpis: 5, premium_acts: 3, byod_acts: 2, upgrade_acts: 1,
  premium_comm: 30, byod_comm: 20, upgrade_comm: 10, acc_comm: 40, setup_fee_comm: 5,
  trade_in_comm: 5, acima_comm: 0, subtotal: payout, total_payout: payout,
})
const REPS = [
  mk('A001', 'Alice Anderson', '100 Main St', 'North', 1111.11),
  mk('B002', 'Bob Brown', '200 Oak Ave', 'South', 2222.22),
  mk('C003', 'Carol Clark', '100 Main St', 'North', 3333.33),
]
const RATINGS = {
  'Alice Anderson': 'S100 4.9/4.7',
  'Bob Brown': 'S200 4.2/4.7 (below)',
  'Carol Clark': 'S100 4.9/4.7',
}
const FILT0 = { period: '', periodTo: '', stores: [], markets: [], reps: [] }
const base = {
  period: 'July 2026', isBoost: true, reps: REPS, filtered: REPS, currentRep: null, filt: FILT0,
  cfg: { premium_flat: 10, byod_flat: 10, upgrade_flat: 10, trade_in_spiff: 5, acima_spiff: 25 },
  chargebacks: [], hasInstallment: false,
}
const cells = p => p.sheets.flatMap(sh => sh.rows.flatMap(r => sh.columns.map(c => {
  const v = c.get(r); return v == null ? '' : String(v)
})))
const shape = p => JSON.stringify({
  title: p.title, subtitle: p.subtitle, filename: p.filename,
  sheets: p.sheets.map(s => ({ name: s.name, headers: s.columns.map(c => c.header), cells: cells({ sheets: [s] }) })),
})

section('1. No ratings on screen ⇒ the export payload is unchanged, on every tab')
for (const tab of ['breakdown', 'compensation', 'individual']) {
  const input = { ...base, tab, currentRep: tab === 'individual' ? REPS[0] : null }
  const without = buildCommissionExport(input)
  const undef = buildCommissionExport({ ...input, ratingByRep: undefined })
  const empty = buildCommissionExport({ ...input, ratingByRep: {} })
  ok(`${tab}: undefined ratingByRep ≡ field absent`, shape(without) === shape(undef))
  ok(`${tab}: EMPTY ratingByRep ≡ field absent (no phantom column)`, shape(without) === shape(empty))
  ok(`${tab}: no 'Google rating' header anywhere`,
    !without.sheets.some(s => s.columns.some(c => c.header === 'Google rating')))
}

section('2. Ratings present ⇒ ONE extra display column, money columns untouched')
{
  const b0 = buildCommissionExport({ ...base, tab: 'breakdown' })
  const b1 = buildCommissionExport({ ...base, tab: 'breakdown', ratingByRep: RATINGS })
  const h0 = b0.sheets[0].columns.map(c => c.header), h1 = b1.sheets[0].columns.map(c => c.header)
  ok('breakdown: exactly one column added', h1.length === h0.length + 1)
  ok('breakdown: it is the LAST column and is named Google rating', h1[h1.length - 1] === 'Google rating')
  ok('breakdown: every pre-existing column is unchanged, in order', JSON.stringify(h1.slice(0, h0.length)) === JSON.stringify(h0))
  ok('breakdown: each rep gets THEIR own rating',
    REPS.every(r => b1.sheets[0].columns[h1.length - 1].get(r) === RATINGS[r.storeops_name]))
  const c1 = buildCommissionExport({ ...base, tab: 'compensation', ratingByRep: RATINGS })
  ok('compensation: Google rating column present, last', c1.sheets[0].columns.map(c => c.header).pop() === 'Google rating')
  const money0 = cells(b0).filter(v => v.includes('.')), money1 = cells(b1).filter(v => v.includes('.'))
  ok('breakdown: no money cell changed', JSON.stringify(money0.filter(v => !Object.values(RATINGS).some(t => t.includes(v)))) ===
    JSON.stringify(money1.filter(v => !Object.values(RATINGS).some(t => t.includes(v)))))
}

section("3. Individual-Rep statement carries ONLY that rep's rating")
for (const target of REPS) {
  const p = buildCommissionExport({ ...base, tab: 'individual', currentRep: target, ratingByRep: RATINGS })
  const text = cells(p).join('|')
  ok(`${target.storeops_name}: own rating present`, text.includes(RATINGS[target.storeops_name]))
  const foreign = REPS.filter(r => r.epay_salesperson !== target.epay_salesperson)
    .map(r => RATINGS[r.storeops_name])
    .filter(t => t !== RATINGS[target.storeops_name] && text.includes(t))
  ok(`${target.storeops_name}: no other rep's rating leaked`, foreign.length === 0, foreign.join(', '))
}
{
  const p = buildCommissionExport({ ...base, tab: 'individual', currentRep: REPS[0], ratingByRep: { 'Somebody Else': 'S999 1.0/4.7' } })
  ok('a rating for a rep NOT on screen never appears', !cells(p).join('|').includes('S999'))
}

section('4. The consumer degrades silently and stays inside its own module')
{
  const gr = read('app/(platform)/commcalc/_lib/googleRatings.tsx')
  // every backtick path string that names the people endpoints must be rooted at /api/v1 — a bare
  // /storeops/... passes a curl-against-backend check and 404s inside the app (client.ts api()).
  const paths = (gr.match(/`[^`]*storeops\/google-reviews[^`]*`/g) || [])
  ok('every google-reviews path carries the /api/v1 prefix (curl-verified ≠ UI-verified)',
    paths.length >= 2 && paths.every(p => p.startsWith('`/api/v1/storeops/google-reviews')), paths.join(' '))
  ok('the batch summary endpoint is called, not one-per-row', gr.includes('google-reviews/employee-summary'))
  ok('the per-employee detail endpoint is called', /google-reviews\/employee\/\$\{/.test(gr))
  ok('org_id rides on every call (orgParam/orgQ)', /orgParam\(\)|orgQ\(\)/.test(gr))
  ok('zero console output in the whole module (no spam when the endpoint 404s)', !/console\./.test(gr))
  ok('every await of the people endpoints is wrapped in try/catch', (gr.match(/try \{[\s\S]{0,240}?api\(/g) || []).length >= 2)
  ok('chips render NOTHING with no data (no placeholder row/gap)', /if \(!rows\.length\) return null/.test(gr))
  ok('detail panel renders NOTHING with no data', /if \(!stores \|\| !stores\.length\) return null/.test(gr))
  ok('ambiguous name → refuses to guess a person', /byKey\.set\(k, null\)/.test(gr))
}
{
  const consumers = [
    'app/(platform)/commcalc/page.tsx',
    'app/(platform)/commcalc/reports/page.tsx',
    'app/(platform)/commcalc/productivity/page.tsx',
    'app/(platform)/commcalc/commission-explain/page.tsx',
    'app/(platform)/commcalc/coaching/page.tsx',
  ]
  for (const f of consumers) {
    const src = read(f)
    ok(`${path.basename(path.dirname(f))}: uses the commcalc-owned module`, /_lib\/googleRatings/.test(src))
    ok(`${path.basename(path.dirname(f))}: does NOT import mod-people's GoogleReviewsCard`, !/GoogleReviewsCard/.test(src))
    // a page may MENTION the endpoints in a comment; what it may not do is call them itself — every
    // fetch goes through the one module so the degrade/caching rules can't be forgotten on one page.
    ok(`${path.basename(path.dirname(f))}: no direct google-reviews fetch on the page`,
      !/api\([^)]*google-reviews/.test(src))
  }
}

section('5. RENDER proof — the chips actually produce (and withhold) markup')
{
  // Transpile the module + lib/client to CommonJS so it can be REQUIRED and server-rendered here.
  // '@/lib/client' is mapped to the emitted client.js by a resolver hook (node cannot read tsconfig paths).
  const CFG2 = path.join(FRONTEND, `tsconfig${path.basename(OUT)}-r.json`)
  process.on('exit', () => rmSync(CFG2, { force: true }))
  writeFileSync(CFG2, JSON.stringify({
    extends: './tsconfig.json',
    compilerOptions: {
      noEmit: false, incremental: false, module: 'commonjs', moduleResolution: 'node',
      target: 'es2020', jsx: 'react-jsx', declaration: false, outDir: path.basename(OUT) + '/r',
    },
    files: ['src/lib/client.ts', 'src/app/(platform)/commcalc/_lib/googleRatings.tsx'],
    include: [],
  }, null, 2))
  execFileSync('npx', ['tsc', '-p', CFG2], { cwd: FRONTEND, stdio: ['ignore', 'ignore', 'inherit'] })

  process.env.NEXT_PUBLIC_SUPABASE_URL ||= 'https://proof.invalid'
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||= 'proof-anon-key'
  const { createRequire } = await import('node:module')
  const Module = (await import('node:module')).default
  const require = createRequire(path.join(FRONTEND, 'proof.cjs'))
  const R = path.join(OUT, 'r')
  const orig = Module._resolveFilename
  Module._resolveFilename = function (req, ...rest) {
    if (req === '@/lib/client') return path.join(R, 'lib', 'client.js')
    return orig.call(this, req, ...rest)
  }
  const React = require('react')
  const { renderToStaticMarkup } = require('react-dom/server')
  const GR = require(path.join(R, 'app', '(platform)', 'commcalc', '_lib', 'googleRatings.js'))
  const html = (el) => renderToStaticMarkup(el)

  ok('no data → renders literally nothing', html(React.createElement(GR.GoogleRatingChips, { list: [] })) === '')
  ok('undefined list → renders literally nothing', html(React.createElement(GR.GoogleRatingChips, {})) === '')
  const below = html(React.createElement(GR.GoogleRatingChips, {
    list: [{ store_code: 'S123', rating: 4.6, target: 4.7, review_count: 128, status: 'below' }] }))
  ok('below target → chip shows store, rating/target and the warn colour',
    below.includes('S123') && below.includes('4.6/4.7') && below.includes('var(--amber)'), below.slice(0, 160))
  ok('below target → tooltip says BELOW target in plain English', /BELOW target/.test(below))
  const above = html(React.createElement(GR.GoogleRatingChips, {
    list: [{ store_code: 'S200', rating: 4.9, target: 4.7, status: 'above' }] }))
  ok('at/above target → ok colour', above.includes('var(--green)') && above.includes('S200'))
  const unknown = html(React.createElement(GR.GoogleRatingChips, {
    list: [{ store_code: 'S300', rating: null, target: 4.7, status: 'unknown' }] }))
  ok('not rated yet → muted chip with an em dash, never a fake 0.0',
    unknown.includes('var(--text3)') && unknown.includes('—/4.7'), unknown.slice(0, 160))
  const multi = html(React.createElement(GR.GoogleRatingChips, {
    list: [{ store_code: 'S1', rating: 4.8, target: 4.7, status: 'above' },
           { store_code: 'S2', rating: 4.1, target: 4.7, status: 'below' }] }))
  ok('an employee at TWO stores gets TWO chips', multi.includes('S1') && multi.includes('S2'))
  ok('detail panel with nothing loaded renders nothing (no empty card, no layout gap)',
    html(React.createElement(GR.GoogleRatingDetail, { repName: 'Nobody Here' })) === '')
  ok('export cell text matches the chip text', GR.ratingsText([{ store_code: 'S123', rating: 4.6, target: 4.7, status: 'below' }]) === 'S123 4.6/4.7 (below)')
  ok('export cell for no data is empty (never "undefined")', GR.ratingsText([]) === '' && GR.ratingsText(undefined) === '')
  ok('"LAST, FIRST" and "First Last" resolve to the SAME person key',
    GR.repNameKey('ALI, MOHAMMAD KHALID') === GR.repNameKey('Mohammad Khalid Ali'))
  Module._resolveFilename = orig
}

console.log(`\n${fail === 0 ? 'ALL GREEN' : 'FAILURES'} — ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
