// Proof harness — the Account hub's EXPENSES column + the per-scope drill-down (owner request
// 2026-09-21: *"in teh finance accout module add expenses column also and let each store be drilled
// down to get more details"*).
//
// The backend half is proven in `backend/harness_account_expenses_one_home.py` (the ONE home,
// `account/analysis.pl_totals`, and the identity gross_profit − expenses == net_income). This half
// proves the DISPLAY contract, which is where the same defect would reappear in a different costume:
// a column that quietly computes its own number, or prints $0.00 for a figure nobody measured.
//
// Asserts (no network, no DB, no React render — the display logic is a pure .ts module):
//   A. scopeExpenses — a number is REPORTED, absent/null/NaN is NOT, and a measured zero is reported.
//   B. The hub column READS `row.expenses`. It never nets gross_profit − net_income, never sums a
//      source table, and holds no arithmetic of its own: proven by feeding a row whose `expenses`
//      contradicts GP − NI and checking the cell still shows `expenses` (the number the P&L shows),
//      while the identity checker RAISES ITS HAND about the contradiction instead of hiding it.
//   C. The drill-down reuses the canonical read: `scopeStatementPath` is `/api/v1/account/pl/…` —
//      the SAME endpoint /accounts/pl fetches — with the row's own scope key and the org id, and
//      "see everything" links land on the existing P&L / Balance Sheet pages for that scope. No new
//      endpoint, no fourth place store financials are computed. Scope keys are URL-encoded (store
//      addresses carry spaces, commas and '#').
//   D. expenseSections / statementExpenseSubtotal read the stored snapshot: only the expense
//      sections, in EXPENSE_SECTIONS order, empty ones dropped, amounts untouched.
//   E. The TIE-OUT is a check, not a source — when the panel's detail disagrees with the column it
//      reports the difference; it never substitutes its own total.
//   F. The page wiring: the header row carries Expenses between Gross Profit and Net Income, the
//      export column list matches the visible table, an unmeasured cell exports BLANK (not $0.00),
//      every scope row is drillable through ONE mechanism, and nothing branches on a tenant,
//      company or store NAME (RULE TWO).
//
// Run:  node frontend/prove_accounts_expenses_column.mjs

import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const ACC = join(HERE, 'src/app/(platform)/accounts')
const HELPER = join(ACC, '_components/scopeFinancials.ts')
const PAGE = join(ACC, 'page.tsx')

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}

// ── transpile the pure display module (types only; no bundler, no stub) ─────────────────────────
const out = mkdtempSync(join(tmpdir(), 'accexp-'))
writeFileSync(join(out, 'scopeFinancials.ts'), readFileSync(HELPER, 'utf8'))
execFileSync(join(HERE, 'node_modules/.bin/tsc'),
  [join(out, 'scopeFinancials.ts'), '--target', 'es2020', '--module', 'es2020', '--outDir', out, '--skipLibCheck'],
  { stdio: 'inherit', cwd: out })
const F = await import(pathToFileURL(join(out, 'scopeFinancials.js')).href)

const page = readFileSync(PAGE, 'utf8')
const helper = readFileSync(HELPER, 'utf8')
const codeOnly = s => s.replace(/\/\*[\s\S]*?\*\//g, ' ').split('\n').map(l => l.replace(/\/\/.*$/, '')).join('\n')

console.log('\n── A. reported vs not reported ──')
ck('a number is reported', F.scopeExpenses({ scope_key: 'store:A', expenses: 1234.56 }).reported === true)
ck('…and carries the amount', F.scopeExpenses({ scope_key: 'store:A', expenses: 1234.56 }).amount === 1234.56)
ck('a MEASURED zero is reported', F.scopeExpenses({ scope_key: 'store:A', expenses: 0 }).reported === true)
ck('…and is zero, not absent', F.scopeExpenses({ scope_key: 'store:A', expenses: 0 }).amount === 0)
ck('an ABSENT key is not reported', F.scopeExpenses({ scope_key: 'store:A' }).reported === false)
ck('null is not reported', F.scopeExpenses({ scope_key: 'store:A', expenses: null }).reported === false)
ck('NaN / Infinity are not reported',
  F.scopeExpenses({ scope_key: 'x', expenses: NaN }).reported === false
  && F.scopeExpenses({ scope_key: 'x', expenses: Infinity }).reported === false)
ck('a missing row is not reported', F.scopeExpenses(undefined).reported === false && F.scopeExpenses(null).reported === false)
ck('a negative total (net reversals) is still reported',
  F.scopeExpenses({ scope_key: 'x', expenses: -12.5 }).reported === true
  && F.scopeExpenses({ scope_key: 'x', expenses: -12.5 }).amount === -12.5)

console.log('\n── B. the column is READ, never recomputed ──')
const honest = { scope_key: 'store:1 S 60th street', gross_profit: 1000, expenses: 471.68, net_income: 528.32 }
ck('the cell shows the statement figure', F.scopeExpenses(honest).amount === 471.68)
ck('…which is NOT GP − NI arithmetic by coincidence only', Math.round((honest.gross_profit - honest.net_income) * 100) / 100 === 471.68)
ck('identity holds on an honest row', F.marginIdentity(honest).checked && F.marginIdentity(honest).agree)
// A contradictory row: if the column were computed as GP − NI it would show 600 and nobody would
// ever learn the snapshot was inconsistent. It shows the statement's 471.68 and RAISES ITS HAND.
const contradicted = { ...honest, net_income: 400 }
ck('a contradictory snapshot still shows the STATEMENT number', F.scopeExpenses(contradicted).amount === 471.68)
ck('…and the identity check reports the discrepancy rather than hiding it',
  F.marginIdentity(contradicted).checked && F.marginIdentity(contradicted).agree === false
  && F.marginIdentity(contradicted).delta === 128.32, F.marginIdentity(contradicted))
ck('identity is not checked when there is nothing to check',
  F.marginIdentity({ scope_key: 'x' }).checked === false && F.marginIdentity({ scope_key: 'x' }).agree === true)
ck('a cent of float noise does not cry wolf',
  F.marginIdentity({ scope_key: 'x', gross_profit: 100.0, expenses: 33.33, net_income: 66.67 }).agree === true)

console.log('\n── C. the drill-down REUSES the canonical read ──')
const path = F.scopeStatementPath('August 2026', 'store:117 E Burnside Ave', '00000000-0000-0000-0000-000000000001')
ck('it is the P&L read the /accounts/pl page uses', path.startsWith('/api/v1/account/pl/'))
ck('period is encoded', path.includes('August%202026'))
ck('scope is encoded (addresses carry spaces, commas, #)',
  F.scopeStatementPath('P', 'store:1115 Liberty Ave Brooklyn, NY 11208', 'O').includes('store%3A1115%20Liberty%20Ave%20Brooklyn%2C%20NY%2011208'))
ck('the read is org-scoped', path.includes('org_id=00000000-0000-0000-0000-000000000001'))
ck('NO store/market filter is applied — the stored snapshot is what the P&L page shows unfiltered',
  !path.includes('stores=') && !path.includes('markets='))
ck('"see everything" lands on the existing P&L page for that scope',
  F.scopeDetailHref('store:103 Fulton Ave') === '/accounts/pl?scope=store%3A103%20Fulton%20Ave')
ck('…and on the existing Balance Sheet page',
  F.scopeBalanceSheetHref('company:abc') === '/accounts/balance-sheet?scope=company%3Aabc')
ck('store scopes are recognised without naming a store',
  F.isStoreScope('store:X') && !F.isStoreScope('company:X') && !F.isStoreScope('consolidated'))
ck('the store address is recoverable from the key',
  F.scopeAddress('store:117 E Burnside Ave') === '117 E Burnside Ave' && F.scopeAddress('consolidated') === 'consolidated')

console.log('\n── D. the panel renders the stored snapshot ──')
const st = {
  sections: [
    { type: 'revenue', subtotal: 20000, lines: [{ key: 'device_rev', label: 'Device sales revenue', amount: 20000 }] },
    { type: 'cogs', subtotal: 14000, lines: [{ key: 'device_cost', label: 'Device cost', amount: 14000 }] },
    { type: 'opex', subtotal: 18292.12, lines: [
      { key: 'rep_comm', label: 'Rep commissions paid', amount: 235.98 },
      { key: 'wages', label: 'Wages / hourly payroll', amount: 4471 },
      { key: 'chargebacks', label: 'Chargebacks / clawbacks', amount: 0, note: 'no chargeback file for this period' },
      { key: 'store_opex', label: 'Store operating expenses', amount: 13585.14, detail: { Rent: 9000, Utilities: 4585.14 } },
    ] },
    { type: 'other', subtotal: 0, lines: [] },
  ],
  gross_profit: 6000, net_operating_income: -12292.12, net_income: -12292.12,
}
const secs = F.expenseSections(st)
ck('only the expense sections are rendered', secs.length === 1 && secs[0].type === 'opex')
ck('an EMPTY expense section is dropped, not shown as a zero heading', !secs.some(s => (s.lines || []).length === 0))
ck('line amounts are untouched', secs[0].lines[3].amount === 13585.14)
ck('per-line stored detail survives for the drill-down', secs[0].lines[3].detail.Rent === 9000)
ck('a declared-zero NOTE survives (ruling K3(b): a $0 with a reason is not a measured $0)',
  secs[0].lines[2].note === 'no chargeback file for this period')
ck('sections come back in EXPENSE_SECTIONS order', JSON.stringify(F.EXPENSE_SECTIONS) === JSON.stringify(['opex', 'other']))
ck('an empty/absent statement yields no sections and no crash',
  F.expenseSections(null).length === 0 && F.expenseSections({}).length === 0)
ck('the subtotal sums the SECTION SUBTOTALS the snapshot stored', F.statementExpenseSubtotal(st) === 18292.12)
ck('…including a non-empty Other section',
  F.statementExpenseSubtotal({ sections: [{ type: 'opex', subtotal: 10 }, { type: 'other', subtotal: 2.5 }] }) === 12.5)
ck('…and ignores revenue/COGS entirely',
  F.statementExpenseSubtotal({ sections: [{ type: 'revenue', subtotal: 999 }, { type: 'cogs', subtotal: 999 }] }) === 0)

console.log('\n── E. the tie-out reports, it does not substitute ──')
const agreeing = F.expenseTieOut({ reported: true, amount: 18292.12 }, st)
ck('agreement is stated', agreeing.checked && agreeing.agree && agreeing.delta === 0)
const off = F.expenseTieOut({ reported: true, amount: 18000 }, st)
ck('a disagreement is CHECKED and reported', off.checked && off.agree === false)
ck('…with the exact difference, both numbers preserved', off.delta === 292.12 && off.detail === 18292.12, off)
ck('nothing to tie out when the column is unmeasured',
  F.expenseTieOut({ reported: false, amount: 0 }, st).checked === false)
ck('nothing to tie out before the statement arrives',
  F.expenseTieOut({ reported: true, amount: 5 }, null).checked === false)

console.log('\n── F. the page wiring ──')
const header = page.slice(page.indexOf('<thead>'), page.indexOf('</thead>'))
const cols = [...header.matchAll(/>\s*(Scope|Revenue|Gross Profit|Expenses|Net Income|Assets|Bal\.)\s*</g)].map(m => m[1])
ck('Expenses sits between Gross Profit and Net Income',
  JSON.stringify(cols) === JSON.stringify(['Scope', 'Revenue', 'Gross Profit', 'Expenses', 'Net Income', 'Assets', 'Bal.']), cols)
const exportCols = [...page.matchAll(/\{ header: '([^']+)', get:/g)].map(m => m[1])
ck('the export carries the same columns in the same order',
  JSON.stringify(exportCols.slice(0, 6)) === JSON.stringify(['Scope', 'Revenue', 'Gross Profit', 'Expenses', 'Net Income', 'Assets']), exportCols)
ck('an unmeasured cell exports BLANK, never $0.00',
  page.includes('scopeExpenses(r).reported ? r.expenses : null'))
ck('the screen renders "not reported" for an unmeasured cell', page.includes('not reported'))
ck('the cell is the shared helper, not an inline formula', page.includes('<ExpenseCell row={s} />'))
ck('EVERY scope row drills through ONE mechanism (no per-table, per-tenant variant)',
  (page.match(/<ScopeDrillDown /g) || []).length === 1
  && (page.match(/setOpenKey\(/g) || []).length === 1)
ck('only the OPEN row fetches (the panel is lazy)',
  page.includes('{open && (') && page.includes('<ScopeDrillDown row={s} period={period} />'))
ck('the panel builds no URL of its own — it calls the shared path helper',
  !codeOnly(page.slice(page.indexOf('function ScopeDrillDown'))).includes('/api/v1/'))
ck('an uncomputed scope says so instead of showing zeros',
  page.includes("setState('uncomputed')") && page.includes('not the same as $0.00'))
ck('RULE TWO — no tenant / carrier / company / store name in the added code',
  !/(?:boost|luxelink|nova\s*wave|cellfonz|t-?mobile|verizon)/i.test(
    codeOnly(page.slice(page.indexOf('function ExpenseCell('))) + codeOnly(helper)))
ck('the helper reads no source table and no second expenses feed',
  !codeOnly(helper).includes('store_expenses') && !codeOnly(helper).includes('/expenses')
  && !codeOnly(helper).includes('commcalc'))
// The one place GP and NI are subtracted is `marginIdentity` — the CHECK. The cell itself must not
// be reachable from that arithmetic: scopeExpenses' body names only the row's own `expenses`.
const scopeExpensesBody = codeOnly(helper).slice(codeOnly(helper).indexOf('export function scopeExpenses'))
  .split('\n}')[0]
ck('scopeExpenses reads row.expenses and nothing else',
  scopeExpensesBody.includes('row?.expenses')
  && !scopeExpensesBody.includes('gross_profit') && !scopeExpensesBody.includes('net_income'))
ck('the helper documents the ONE home it dereferences', helper.includes('analysis.pl_totals'))

console.log()
console.log(`prove_accounts_expenses_column: ${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
console.log('ALL CHECKS PASSED')
