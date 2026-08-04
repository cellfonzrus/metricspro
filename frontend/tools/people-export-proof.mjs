#!/usr/bin/env node
// PROOF for the 2026-08-04 people-domain export-privacy audit (same class as the commission-domain
// bug: "when commission for one employee is exported it sends the commission for all employees" —
// docs/handoffs/commission.md @ 1c2f4f44).
//
// Audited every <SendReportButton> call site under storeops/**, hr/**, employee/**:
//   - storeops/schedule/page.tsx   — used the server `reportKey="storeops_schedule"` path, which only
//     forwarded week_start + store_code. Filtering by MARKET ALONE (no single store picked) was
//     silently dropped server-side, so Send emailed/WhatsApp'd every store's schedule org-wide
//     instead of the filtered grid on screen. FIXED — extracted the row-building logic into a pure
//     module (storeops/lib/scheduleExport.ts) that both the local Excel/PDF/Print buttons AND
//     SendReportButton now render from, so there is no second query to forget a filter.
//   - storeops/visits/page.tsx, storeops/visits/[id]/page.tsx, hr/page.tsx — already used the
//     WYSIWYG `exportPayload` path. Verified sound, untouched.
//   - Every other report surface in the tree (storeops/payroll, salary-advances,
//     payroll-change-log, storeops/reports, hr/employee-database, hr/letters/sent) renders through
//     <ReportShell>, which is exportPayload-only by construction (components/ReportShell.tsx:253)
//     and receives an already-filtered `rows` prop — verified sound, untouched.
//
// This script:
//   1. Exercises the extracted buildScheduleExport() on a fixture of 2 markets / 3 stores / 4
//      employees: unfiltered vs market-filtered vs store-filtered vs employee-view, asserting no
//      foreign store/employee/shift token appears in a filtered payload — in every cell, the CSV
//      string, AND real decoded .xlsx bytes.
//   2. Statically re-asserts the class fix: zero `reportKey=` usages left anywhere under
//      storeops/**, hr/**, employee/**, and that the schedule page's Send/Export both route through
//      the one buildPayload() that delegates to the proven module.
//
// Run:  node frontend/tools/people-export-proof.mjs      (from the repo root, or anywhere)
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync, writeFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SRC = path.join(FRONTEND, 'src')
const OUT = mkdtempSync(path.join(FRONTEND, '.proof-'))
const CFG = path.join(FRONTEND, `tsconfig${path.basename(OUT)}.json`)
const cleanup = () => { rmSync(OUT, { recursive: true, force: true }); rmSync(CFG, { force: true }) }
process.on('exit', cleanup)

let pass = 0, fail = 0
const ok = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}
const section = t => console.log(`\n${t}`)

// ── transpile the pure module + the shared export renderer ──────────────────────────────────────
writeFileSync(CFG, JSON.stringify({
  extends: './tsconfig.json',
  compilerOptions: { noEmit: false, incremental: false, outDir: path.basename(OUT), module: 'es2020', target: 'es2020', declaration: false },
  files: ['src/app/(platform)/storeops/lib/scheduleExport.ts', 'src/lib/export.tsx'],
  include: [],
}, null, 2))
execFileSync('npx', ['tsc', '-p', CFG], { cwd: FRONTEND, stdio: ['ignore', 'ignore', 'inherit'] })

const SE = await import(path.join(OUT, 'app', '(platform)', 'storeops', 'lib', 'scheduleExport.js'))
const EX = await import(path.join(OUT, 'lib', 'export.js'))
const { buildScheduleExport } = SE

// ── fixture: 2 markets, 3 stores, 4 employees, a full work week ─────────────────────────────────
const WEEK = ['2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07', '2026-08-08', '2026-08-09']
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const weekDates = WEEK.map((iso, i) => ({ iso, dow: DOW[i], md: `8/${3 + i}` }))

const STORES = [
  { store_code: 'S100', market: 'North' },
  { store_code: 'S200', market: 'North' },
  { store_code: 'S300', market: 'South' },
]
const EMPS = [
  { name: 'Alice Anderson', home_store: 'S100', market: 'North' },
  { name: 'Bob Brown', home_store: 'S200', market: 'North' },
  { name: 'Carol Clark', home_store: 'S300', market: 'South' },
  { name: 'Dan Davis', home_store: 'S300', market: 'South' },
]
const SHIFTS = [
  { employee_name: 'Alice Anderson', store_code: 'S100', shift_date: '2026-08-03', start_time: '09:00', end_time: '17:00', scheduled_hours: 8 },
  { employee_name: 'Bob Brown', store_code: 'S200', shift_date: '2026-08-04', start_time: '10:00', end_time: '18:00', scheduled_hours: 8 },
  { employee_name: 'Carol Clark', store_code: 'S300', shift_date: '2026-08-05', start_time: '08:00', end_time: '16:00', scheduled_hours: 8 },
  { employee_name: 'Dan Davis', store_code: 'S300', shift_date: '2026-08-06', start_time: '12:00', end_time: '20:00', scheduled_hours: 8 },
]

const byMarket = m => STORES.filter(s => s.market === m).map(s => ({ store_code: s.store_code }))
const empsByMarket = m => EMPS.filter(e => e.market === m).map(e => ({ name: e.name }))
const allStores = STORES.map(s => ({ store_code: s.store_code }))
const allEmps = EMPS.map(e => ({ name: e.name }))

const cells = p => p.sheets.flatMap(sh => sh.rows.flatMap(r => sh.columns.map(c => {
  const v = c.get(r); return v == null ? '' : String(v)
})))
const payloadText = p => [p.title, p.subtitle || '', p.filename, ...p.sheets.map(s => s.name), ...cells(p)].join('')

const NORTH_TOKENS = ['S100', 'S200', 'Alice Anderson', 'Bob Brown']
const SOUTH_TOKENS = ['S300', 'Carol Clark', 'Dan Davis']

const base = { weekStartLabel: 'August 3, 2026', weekStartIso: '2026-08-03', weekDates, shifts: SHIFTS }

// ══════════════════════════════════════════════════════════════════════════════════════════════
section('1. UNFILTERED (view=store) — all stores, all markets present (behaviour unchanged)')
{
  const p = buildScheduleExport({ ...base, view: 'store', filterMarkets: [], filteredStores: allStores, filteredEmps: allEmps })
  ok('3 rows (one per store)', p.sheets[0].rows.length === 3, p.sheets[0].rows.length)
  ok('both markets present', [...NORTH_TOKENS, ...SOUTH_TOKENS].every(t => payloadText(p).includes(t)))
  ok('subtitle carries no market filter', p.subtitle === 'By store', p.subtitle)
}

section('2. MARKET filter (view=store) — the exact bug: North selected, South must NOT leak')
{
  const p = buildScheduleExport({ ...base, view: 'store', filterMarkets: ['North'], filteredStores: byMarket('North'), filteredEmps: empsByMarket('North') })
  const t = payloadText(p)
  ok('2 rows (S100, S200 only)', p.sheets[0].rows.length === 2, p.sheets[0].rows.length)
  ok('North stores/employees present', NORTH_TOKENS.every(x => t.includes(x)))
  const leaked = SOUTH_TOKENS.filter(x => t.includes(x))
  ok('South store/employees NOT present anywhere in the payload', leaked.length === 0, `leaked: ${leaked.join(', ')}`)
  ok('subtitle states the market filter', p.subtitle.includes('North'), p.subtitle)

  // real .xlsx bytes — decode back and scan cell-by-cell
  const xlsx = await import('xlsx')
  const file = await EX.renderExcelBase64(p)
  const wb = xlsx.read(Buffer.from(file.content_b64, 'base64'), { type: 'buffer' })
  const sheetText = wb.SheetNames.map(n => xlsx.utils.sheet_to_csv(wb.Sheets[n])).join('\n')
  ok('Excel: decoded cells carry North data', NORTH_TOKENS.every(x => sheetText.includes(x)))
  ok('Excel: decoded cells carry NO South data', SOUTH_TOKENS.every(x => !sheetText.includes(x)))
}

section('3. STORE filter (view=employee) — single store, single market\'s employees only')
{
  const p = buildScheduleExport({ ...base, view: 'employee', filterMarkets: [], filteredStores: [{ store_code: 'S300' }], filteredEmps: EMPS.filter(e => e.home_store === 'S300').map(e => ({ name: e.name })) })
  const t = payloadText(p)
  ok('2 rows (Carol, Dan — S300 only)', p.sheets[0].rows.length === 2, p.sheets[0].rows.length)
  ok('no North employee leaks in', !t.includes('Alice Anderson') && !t.includes('Bob Brown'))
}

section('4. Filter matching nobody → zero rows exported (never falls back to "everyone")')
{
  const p = buildScheduleExport({ ...base, view: 'store', filterMarkets: ['Nonexistent'], filteredStores: [], filteredEmps: [] })
  ok('zero rows', p.sheets[0].rows.length === 0)
}

section('5. Static wiring — no `reportKey=` remains anywhere in the people tree; schedule page single-sources its export')
{
  const walk = dir => {
    const out = []
    for (const e of readdirSync(dir)) {
      const fp = path.join(dir, e)
      const st = statSync(fp)
      if (st.isDirectory()) out.push(...walk(fp))
      else if (/\.tsx?$/.test(e)) out.push(fp)
    }
    return out
  }
  const PLATFORM = path.join(SRC, 'app', '(platform)')
  const peopleFiles = [
    ...walk(path.join(PLATFORM, 'storeops')),
    ...walk(path.join(PLATFORM, 'hr')),
    ...walk(path.join(PLATFORM, 'employee')),
  ]
  ok(`scanned ${peopleFiles.length} files under storeops/**, hr/**, employee/**`, peopleFiles.length > 20, peopleFiles.length)
  // match live JSX wiring (`<SendReportButton reportKey=...>`), not prose in comments that
  // documents the fix (e.g. this file's own header, and the schedule page's before/after note)
  const offenders = peopleFiles.filter(f => /SendReportButton\s+reportKey\s*=/.test(readFileSync(f, 'utf8')))
  ok('zero `<SendReportButton reportKey=…>` usages left in the people tree', offenders.length === 0, offenders.join(', '))

  const schedule = readFileSync(path.join(PLATFORM, 'storeops', 'schedule', 'page.tsx'), 'utf8')
  ok('schedule: ExportButtons uses payload={buildPayload}', /ExportButtons\s+payload=\{buildPayload\}/.test(schedule))
  ok('schedule: SendReportButton uses exportPayload={buildPayload}', /SendReportButton\s+exportPayload=\{buildPayload\}/.test(schedule))
  ok('schedule: buildPayload delegates to the proven module', /return buildScheduleExport\(\{/.test(schedule))

  // the two people pages that were already correct — re-confirm unchanged
  for (const [label, rel] of [['visits', ['storeops', 'visits', 'page.tsx']], ['visit detail', ['storeops', 'visits', '[id]', 'page.tsx']], ['hr', ['hr', 'page.tsx']]]) {
    const s = readFileSync(path.join(PLATFORM, ...rel), 'utf8')
    ok(`${label}: SendReportButton uses exportPayload=`, /SendReportButton\s+exportPayload=/.test(s))
  }

  // every ReportShell-based report page is exportPayload-only by construction — spot-check the
  // shared component itself carries no reportKey path (it must not, so no page behind it can regress)
  const shell = readFileSync(path.join(SRC, 'components', 'ReportShell.tsx'), 'utf8')
  ok('ReportShell.tsx: SendReportButton uses exportPayload (never reportKey)', /SendReportButton\s+exportPayload=\{buildPayload\}/.test(shell))
  ok('ReportShell.tsx: no reportKey= anywhere', !/reportKey=/.test(shell))
}

console.log(`\n${fail === 0 ? 'ALL GREEN' : 'FAILURES'} — ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
