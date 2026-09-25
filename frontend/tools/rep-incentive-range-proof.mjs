#!/usr/bin/env node
// PROOF — the Rep Incentive MONTH RANGE view (owner 2026-09-25) groups the backend's rows by month and sums
// only what is on screen; it never re-rates, drops or reorders a month's rows. The backend half (every month
// == that month viewed alone) is backend/harness_rep_incentive_range.py.
//
// Run:  node frontend/tools/rep-incentive-range-proof.mjs
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import os from 'node:os'
import path from 'node:path'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SRC = path.join(FRONTEND, 'src/app/(platform)/commcalc')
const OUT = mkdtempSync(path.join(os.tmpdir(), 'rangeproof-'))
writeFileSync(path.join(OUT, 'package.json'), '{"type":"module"}')
process.on('exit', () => rmSync(OUT, { recursive: true, force: true }))
let pass = 0, fail = 0
const ok = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) } else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}
execFileSync(path.join(FRONTEND, 'node_modules/.bin/tsc'), [
  path.join(SRC, '_lib/repIncentiveRange.ts'), '--outDir', OUT, '--target', 'es2020', '--module', 'es2020',
  '--moduleResolution', 'node'], { stdio: 'inherit' })
const M = await import(pathToFileURL(path.join(OUT, 'repIncentiveRange.js')).href)

// the backend payload shape (rows carry range_month; month_totals are the backend's own sums)
const months = ['June 2026', 'July 2026', 'August 2026']
const rows = [
  { range_month: 'July 2026', epay_salesperson: 'Rep A', subtotal: 715, total_payout: 715, final_payout: 715, premium_acts: 37, byod_acts: 6, upgrade_acts: 57 },
  { range_month: 'July 2026', epay_salesperson: 'Rep B', subtotal: 320, total_payout: 320, final_payout: 295, premium_acts: 12, byod_acts: 10, upgrade_acts: 20 },
  { range_month: 'August 2026', epay_salesperson: 'Rep A', subtotal: 365, total_payout: 365, final_payout: 365, premium_acts: 18, byod_acts: 4, upgrade_acts: 29 },
  { range_month: 'August 2026', epay_salesperson: 'Rep C', subtotal: 125.1, total_payout: 125.1, final_payout: 115.05, premium_acts: 7, byod_acts: 1, upgrade_acts: 9 },
]
const backendTotals = { 'June 2026': 0, 'July 2026': 1035, 'August 2026': 490.1 }
const v = M.monthBlocks(rows, months)
ok('one block per month, in the backend\'s month order (the empty month kept)', v.blocks.map(b => b.month).join('|') === months.join('|'))
ok('each block holds exactly that month\'s rows, order kept (the same objects)',
  v.blocks.every(b => b.rows.every(r => r.range_month === b.month)) && v.blocks[1].rows[0] === rows[0] && v.blocks[1].rows[1] === rows[1])
ok('unfiltered, every month subtotal == the backend month total',
  v.blocks.every(b => b.totals.total_payout === backendTotals[b.month]))
ok('grand total = Σ blocks ($1,525.10 payout / $1,490.05 final)', v.grand.total_payout === 1525.1 && v.grand.final_payout === 1490.05)
ok('counts sum (PA 74 / BA 21 / UA 115)', v.grand.premium_acts === 74 && v.grand.byod_acts === 21 && v.grand.upgrade_acts === 115)
const onlyA = M.monthBlocks(rows.filter(r => r.epay_salesperson === 'Rep A'), months)
ok('a filter sums only what is on screen (Rep A: $1,080.00)', onlyA.grand.total_payout === 1080)
ok('spanMonths: Jun–Aug = 3; reversed = 0; junk = 0; the cap is 12',
  M.spanMonths('2026-06', '2026-08') === 3 && M.spanMonths('2026-08', '2026-06') === 0 && M.spanMonths('x', '2026-06') === 0 && M.RANGE_MAX_MONTHS === 12)
ok('monthInput: the default window ends this month', M.monthInput(new Date(2026, 8, 25), 0) === '2026-09' && M.monthInput(new Date(2026, 0, 5), 2) === '2025-11')
const page = readFileSync(path.join(SRC, 'reports/page.tsx'), 'utf8')
ok('the page reads /commissions-range (the looped per-month handler) and groups through monthBlocks',
  page.includes('/api/v1/commcalc/commissions-range?') && page.includes('monthBlocks('))
ok('the page never sums a pay component itself beyond the helper (no second rate / tier arithmetic in the range tab)',
  !/tab === 'range'[\s\S]{0,4000}?\* *(?:r\.)?tier/.test(page))
console.log(`\n${fail === 0 ? 'ALL GREEN' : 'FAILURES'} — ${pass} passed, ${fail} failed`)
process.exitCode = fail === 0 ? 0 : 1
