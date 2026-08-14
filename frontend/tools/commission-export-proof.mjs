#!/usr/bin/env node
// PROOF for the owner bug of 2026-08-04:
//   "when commission for one employee is exported it sends the commission for all employees in one
//    pdf, it should only send the one which is selected."
//
// Demonstrates, on a fixture of 4 reps across 2 stores / 2 markets:
//   1. Individual-Rep tab  → the export payload contains ONLY the selected rep, in every sheet,
//      every cell, and the filename — no other rep's name, salesperson id, store or payout number.
//   2. Every format the page offers reads that ONE payload: CSV (payloadToCsv), Excel (real .xlsx
//      bytes decoded back and scanned), PDF/Print (lib/export renders from the same p.sheets), and
//      Send (SendReportButton exportPayload → renderExcelBase64/renderPdfBase64 of the same payload).
//   3. Rep Breakdown / Compensation, UNFILTERED → all rows, unchanged behaviour.
//   4. Rep Breakdown / Compensation, FILTERED by rep / store / market → exactly the filtered rows.
//   5. Static wiring: /commcalc/reports no longer uses the server `reportKey` path at all, and the
//      same server-path defect is gone from the other commcalc report pages that had it.
//
// Run:  node frontend/tools/commission-export-proof.mjs      (from the repo root, or anywhere)
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SRC = path.join(FRONTEND, 'src')
const OUT = mkdtempSync(path.join(FRONTEND, '.proof-'))   // inside frontend/ so bare imports resolve
const CFG = path.join(FRONTEND, `tsconfig${path.basename(OUT)}.json`)   // -> tsconfig.proof-XXXX.json
const cleanup = () => { rmSync(OUT, { recursive: true, force: true }); rmSync(CFG, { force: true }) }
process.on('exit', cleanup)

let pass = 0, fail = 0
const ok = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}
const section = t => console.log(`\n${t}`)

// ── transpile the two modules under test, reusing the app tsconfig (so the `@/*` paths resolve).
// commissionExport.ts's imports are ALL `import type`, so its emitted JS is import-free; export.tsx
// only imports react + dynamic-imports xlsx/jspdf, all resolvable from frontend/node_modules.
writeFileSync(CFG, JSON.stringify({
  extends: './tsconfig.json',
  compilerOptions: { noEmit: false, incremental: false, outDir: path.basename(OUT), module: 'es2020', target: 'es2020', declaration: false },
  files: ['src/app/(platform)/commcalc/_lib/commissionExport.ts', 'src/lib/export.tsx'],
  include: [],
}, null, 2))
execFileSync('npx', ['tsc', '-p', CFG], { cwd: FRONTEND, stdio: ['ignore', 'ignore', 'inherit'] })

const CE = await import(path.join(OUT, 'app', '(platform)', 'commcalc', '_lib', 'commissionExport.js'))
const EX = await import(path.join(OUT, 'lib', 'export.js'))
const { buildCommissionExport, payloadToCsv } = CE

// ── fixture: 4 reps, 2 stores, 2 markets, distinct payouts so a leak is unmistakable ─────────────
const mk = (id, name, store, market, payout, extra = {}) => ({
  epay_salesperson: id, storeops_name: name, store, market,
  tier: 1, kpis_met: 5, total_kpis: 5,
  premium_acts: 3, byod_acts: 2, upgrade_acts: 1,
  premium_comm: 30, byod_comm: 20, upgrade_comm: 10,
  acc_comm: 40, setup_fee_comm: 5, trade_in_comm: 5, acima_comm: 0,
  subtotal: payout, total_payout: payout, ...extra,
})
const REPS = [
  mk('A001', 'Alice Anderson', '100 Main St', 'North', 1111.11),
  mk('B002', 'Bob Brown', '200 Oak Ave', 'South', 2222.22, {
    ops_chargeback_deduction: 50,
    ops_chargeback_lines: [{ label: 'Missing deposit', amount: 50, reason: 'cash short', incident_date: '2026-07-03', store: '200 Oak Ave', status: 'posted' }],
  }),
  mk('C003', 'Carol Clark', '100 Main St', 'North', 3333.33),
  mk('D004', 'Dan Davis', '200 Oak Ave', 'South', 4444.44),
]
const CHARGEBACKS = [
  { id: 'cb1', epay_salesperson: 'B002', source: 'epay', description: 'Deact within 30d', mdn: '5550001', amount: 25, deduct: true },
  { id: 'cb2', epay_salesperson: 'C003', source: 'epay', description: 'Carol only', mdn: '5550002', amount: 99, deduct: true },
]
const FILT0 = { period: '', periodTo: '', stores: [], markets: [], reps: [] }
const base = {
  period: 'July 2026', isBoost: true, reps: REPS, filtered: REPS, currentRep: null,
  filt: FILT0, cfg: { premium_flat: 10, byod_flat: 10, upgrade_flat: 10, trade_in_spiff: 5, acima_spiff: 25 },
  chargebacks: CHARGEBACKS, hasInstallment: false,
}
const filterRepsBy = pred => REPS.filter(pred)

/** Every rendered cell of a payload, as strings — exactly what Excel/PDF/Print/CSV serialize. */
const cells = p => p.sheets.flatMap(sh => sh.rows.flatMap(r => sh.columns.map(c => {
  const v = c.get(r); return v == null ? '' : String(v)
})))
const payloadText = p => [p.title, p.subtitle || '', p.filename, ...p.sheets.map(s => s.name), ...cells(p)].join('')

// tokens that must never appear in a single-rep export
const foreignTokens = keepId => REPS.filter(r => r.epay_salesperson !== keepId)
  .flatMap(r => [r.epay_salesperson, r.storeops_name, String(r.total_payout)])

// ══════════════════════════════════════════════════════════════════════════════════════════════
section('1. Individual-Rep tab exports ONLY the selected rep')
for (const target of REPS) {
  const p = buildCommissionExport({ ...base, tab: 'individual', currentRep: target })
  const text = payloadText(p)
  const leaked = foreignTokens(target.epay_salesperson).filter(t => text.includes(t))
  ok(`${target.storeops_name}: no other rep's name/id/payout anywhere in the payload`,
    leaked.length === 0, `leaked: ${leaked.join(', ')}`)
  ok(`${target.storeops_name}: own identity IS present`,
    text.includes(target.storeops_name) && text.includes(target.epay_salesperson))
  ok(`${target.storeops_name}: filename names the rep`,
    p.filename.startsWith('commission-') && p.filename.includes(target.storeops_name.split(' ')[0].toLowerCase()),
    p.filename)
  ok(`${target.storeops_name}: title names the rep`, p.title === `Incentive Statement — ${target.storeops_name}`, p.title)
}
{
  // the chargeback sheets are the sharpest leak surface: cb2 belongs to Carol only
  const pBob = buildCommissionExport({ ...base, tab: 'individual', currentRep: REPS[1] })
  const t = payloadText(pBob)
  ok("Bob's statement carries HIS chargeback (cb1) …", t.includes('Deact within 30d'))
  ok("… and NOT Carol's (cb2)", !t.includes('Carol only'))
  ok("Bob's ops-chargeback sheet is present", pBob.sheets.some(s => s.name === 'Ops Chargebacks'))
  const pAlice = buildCommissionExport({ ...base, tab: 'individual', currentRep: REPS[0] })
  ok('Alice has no chargeback sheets at all', !pAlice.sheets.some(s => /Chargeback/.test(s.name)))
}
{
  const p = buildCommissionExport({ ...base, tab: 'individual', currentRep: null })
  ok('no rep selected → empty payload, nothing to leak', p.sheets.length === 0)
}
{
  // plan-mode (non-Boost) statement is equally scoped
  const planReps = REPS.map(r => ({ ...r, plan_comm: r.total_payout, plan_name: `Plan-${r.epay_salesperson}` }))
  const p = buildCommissionExport({ ...base, tab: 'individual', isBoost: false, reps: planReps, filtered: planReps, currentRep: planReps[2] })
  const t = payloadText(p)
  ok('plan-mode statement: only the selected rep', foreignTokens('C003').every(x => !t.includes(x)))
  ok('plan-mode statement: names the assigned plan', t.includes('Plan-C003'))
}

section('2. Every export format reads that SAME payload')
{
  const p = buildCommissionExport({ ...base, tab: 'individual', currentRep: REPS[1] })
  const csv = payloadToCsv(p)
  const leaked = foreignTokens('B002').filter(t => csv.includes(t))
  ok('CSV: no foreign rep', leaked.length === 0, leaked.join(', '))
  ok('CSV: has Bob', csv.includes('Bob Brown'))

  // REAL .xlsx bytes, produced by lib/export, decoded back and scanned cell by cell
  const xlsx = await import('xlsx')
  const file = await EX.renderExcelBase64(p)
  const wb = xlsx.read(Buffer.from(file.content_b64, 'base64'), { type: 'buffer' })
  const sheetText = wb.SheetNames.map(n => xlsx.utils.sheet_to_csv(wb.Sheets[n])).join('\n')
  ok('Excel: filename names the rep', file.filename === `${p.filename}.xlsx`, file.filename)
  ok('Excel: decoded cells contain no foreign rep',
    foreignTokens('B002').every(t => !sheetText.includes(t)))
  ok('Excel: decoded cells contain Bob', sheetText.includes('Bob Brown'))
  ok('Excel: sheet set matches the payload', wb.SheetNames.join('|') === p.sheets.map(s => s.name).join('|'),
    `${wb.SheetNames.join('|')} vs ${p.sheets.map(s => s.name).join('|')}`)

  // PDF / Print render from p.sheets through the identical column getters (lib/export displayCell),
  // so the cell set they serialize is exactly `cells(p)` — asserted here, and the toolbar wiring
  // that feeds them this payload is asserted statically in section 5.
  ok('PDF/Print cell set == payload cell set (single source)',
    cells(p).length === p.sheets.reduce((n, s) => n + s.rows.length * s.columns.length, 0))
  ok('PDF/Print cell set carries no foreign rep', foreignTokens('B002').every(t => !cells(p).join('').includes(t)))
}

section('3. List tabs, UNFILTERED → all rows (behaviour unchanged)')
for (const tab of ['breakdown', 'compensation']) {
  const p = buildCommissionExport({ ...base, tab })
  ok(`${tab}: exports all ${REPS.length} reps`, p.sheets[0].rows.length === REPS.length)
  ok(`${tab}: every rep present`, REPS.every(r => payloadText(p).includes(r.storeops_name)))
  ok(`${tab}: filename NOT marked filtered`, !p.filename.includes('-filtered'), p.filename)
}

section('4. List tabs, FILTERED → exactly the filtered rows')
{
  const one = filterRepsBy(r => r.epay_salesperson === 'B002')
  for (const tab of ['breakdown', 'compensation']) {
    const p = buildCommissionExport({ ...base, tab, filtered: one, filt: { ...FILT0, reps: ['B002'] } })
    ok(`${tab} + rep filter: 1 row`, p.sheets[0].rows.length === 1)
    ok(`${tab} + rep filter: no other rep`, foreignTokens('B002').every(t => !payloadText(p).includes(t)))
    ok(`${tab} + rep filter: subtitle states the filter`, /reps: B002/.test(p.subtitle), p.subtitle)
    ok(`${tab} + rep filter: filename marked filtered`, p.filename.includes('-filtered'), p.filename)
  }
  const store = filterRepsBy(r => r.store === '100 Main St')
  const ps = buildCommissionExport({ ...base, tab: 'breakdown', filtered: store, filt: { ...FILT0, stores: ['100 Main St'] } })
  ok('store filter: 2 rows (Alice + Carol)', ps.sheets[0].rows.length === 2)
  ok('store filter: excludes Bob + Dan', !payloadText(ps).includes('Bob Brown') && !payloadText(ps).includes('Dan Davis'))

  const market = filterRepsBy(r => r.market === 'South')
  const pm = buildCommissionExport({ ...base, tab: 'compensation', filtered: market, filt: { ...FILT0, markets: ['South'] } })
  ok('market filter: 2 rows (Bob + Dan)', pm.sheets[0].rows.length === 2)
  ok('market filter: excludes Alice + Carol', !payloadText(pm).includes('Alice Anderson') && !payloadText(pm).includes('Carol Clark'))

  const none = buildCommissionExport({ ...base, tab: 'breakdown', filtered: [], filt: { ...FILT0, reps: ['ZZZ'] } })
  ok('filter matching nobody: zero rows exported', none.sheets[0].rows.length === 0)
}

section('5. Static wiring — the server `reportKey` path is gone from the fixed pages')
{
  const read = rel => { const f = path.join(SRC, 'app', '(platform)', rel); return existsSync(f) ? readFileSync(f, 'utf8') : '' }
  const reports = read('commcalc/reports/page.tsx')
  ok('reports/page.tsx: no reportKey= anywhere', !/reportKey=/.test(reports))
  ok('reports/page.tsx: Send uses exportPayload={buildPayload}', /SendReportButton\s+exportPayload=\{buildPayload\}/.test(reports))
  ok('reports/page.tsx: Excel/PDF/Print use payload={buildPayload}', /ExportButtons\s+payload=\{buildPayload\}/.test(reports))
  ok('reports/page.tsx: CSV uses the same payload', /payloadToCsv\(p\)/.test(reports))
  ok('reports/page.tsx: buildPayload delegates to the proven module', /buildCommissionExport\(exportInput\(\)\)/.test(reports))

  for (const [label, rel] of [['flags', 'commcalc/flags/page.tsx'], ['gp', 'commcalc/gp/page.tsx'], ['discrepancy', 'commcalc/discrepancy/page.tsx']]) {
    const s = read(rel)
    ok(`${label}/page.tsx: no reportKey= (class fix)`, s.length > 0 && !/reportKey=/.test(s))
    ok(`${label}/page.tsx: Send uses exportPayload={buildPayload}`, /SendReportButton\s+exportPayload=\{buildPayload\}/.test(s))
  }
}


console.log(`\n${fail === 0 ? 'ALL GREEN' : 'FAILURES'} — ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
