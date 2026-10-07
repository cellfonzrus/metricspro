// Proof harness — the P&L drill path: company → market → store → line (owner report 2026-10-07).
//
// Owner, verbatim: *"the details are missing and the details with the drop down does not tie any
// information which appears on the summary line, details should be same as on the p&l page, the
// expenses should be able to drill down, each line should be able to drill down to get to each level
// and finally down to the line level."*
//
// The BACKEND half is proven in `backend/harness_pl_drill_detail.py` (56 checks: drill detail is
// recorded at the store grain, so every scope and every filter explains its own number) and
// `backend/harness_labour_vocabulary.py` (99 checks: the labour double-count). This half proves the
// DISPLAY contract, which is where the same class would reappear in a different costume: a rung that
// adds up its own numbers instead of asking the server, or a market list the statement filter cannot
// bind.
//
// Asserts (no network, no DB, no React render — the drill logic is a pure .ts module):
//   A. drillFilter — the ROOT scope never changes as you descend; a market rides in `markets`; a
//      store rides in `stores` and REPLACES its market (one predicate per answer); re-drilling the
//      same kind replaces rather than stacks.
//   B. nextRung / drillRootLevels — company → market → store → line, and a STORE scope row starts at
//      the store, so it goes straight to its lines.
//   C. marketsForScope — the canonical union vocabulary, de-duplicated case-insensitively, never
//      silently shortened when the in-scope list is unknown.
//   D. allSections / SECTION_TITLE — ALL FOUR statement sections in the P&L page's order, empty ones
//      dropped (the old panel showed expenses only).
//   E. lineTieOut — a CHECK, never a source: it reports a line whose drill rows disagree and stays
//      silent for a line that carries no rows.
//   F. levelTotals — read straight off the statement, with the hub's own definition of `expenses`
//      dereferenced; absence is not zero.
//   G. The page wiring: every rung is the SAME `plQuery` read one filter deeper, the panel spells no
//      money URL of its own, a line's rows are rendered under it, and RULE TWO holds.
//
// Run:  node frontend/prove_pl_drill_path.mjs

import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const ACC = join(HERE, 'src/app/(platform)/accounts')
const HELPER = join(ACC, '_components/scopeFinancials.ts')
const PLSTMT = join(ACC, '_components/plStatement.ts')
const PAGE = join(ACC, 'page.tsx')

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}

const out = mkdtempSync(join(tmpdir(), 'drillpath-'))
writeFileSync(join(out, 'scopeFinancials.ts'), readFileSync(HELPER, 'utf8'))
execFileSync(join(HERE, 'node_modules/.bin/tsc'),
  [join(out, 'scopeFinancials.ts'), '--target', 'es2020', '--module', 'es2020', '--outDir', out, '--skipLibCheck'],
  { stdio: 'inherit', cwd: out })
const F = await import(pathToFileURL(join(out, 'scopeFinancials.js')).href)

const page = readFileSync(PAGE, 'utf8')
const plstmt = readFileSync(PLSTMT, 'utf8')
const codeOnly = s => s.replace(/\/\*[\s\S]*?\*\//g, ' ').split('\n').map(l => l.replace(/\/\/.*$/, '')).join('\n')

const CO = { kind: 'scope', key: 'company:11111111-1111-1111-1111-111111111111', label: 'Acme Holdings' }
const MK = { kind: 'market', key: 'PA', label: 'PA' }
const MK2 = { kind: 'market', key: 'NY', label: 'NY' }
const ST = { kind: 'store', key: '103 Fulton Ave', label: '103 Fulton Ave' }
const ST2 = { kind: 'store', key: '1115 Liberty Ave', label: '1115 Liberty Ave' }

console.log('\n── A. drillFilter: the scope is the root, the rung is the filter ──')
ck('the root alone filters nothing',
  JSON.stringify(F.drillFilter([CO])) === JSON.stringify({ scope: CO.key, stores: [], markets: [] }))
ck('a market rides in `markets`, under the SAME scope',
  JSON.stringify(F.drillFilter([CO, MK])) === JSON.stringify({ scope: CO.key, stores: [], markets: ['PA'] }))
ck('a store rides in `stores`',
  JSON.stringify(F.drillFilter([CO, MK, ST])) === JSON.stringify({ scope: CO.key, stores: ['103 Fulton Ave'], markets: [] }))
ck('…and REPLACES its market — one predicate per answer, never two',
  F.drillFilter([CO, MK, ST]).markets.length === 0)
ck('the scope NEVER changes as you descend (a company drill cannot leave the company)',
  [[CO], [CO, MK], [CO, MK, ST]].every(p => F.drillFilter(p).scope === CO.key))
ck('re-drilling a market replaces it rather than stacking two',
  JSON.stringify(F.drillFilter([CO, MK, MK2]).markets) === JSON.stringify(['NY']))
ck('re-drilling a store replaces it rather than stacking two',
  JSON.stringify(F.drillFilter([CO, MK, ST, ST2]).stores) === JSON.stringify(['1115 Liberty Ave']))
ck('a path with no root degrades to consolidated, never to a crash',
  F.drillFilter([]).scope === 'consolidated' && F.drillFilter([MK]).scope === 'consolidated')
ck('the filter is a pure function of the path',
  JSON.stringify(F.drillFilter([CO, MK])) === JSON.stringify(F.drillFilter([CO, MK])))

console.log('\n── B. the rungs: company → market → store → line ──')
ck('a company offers markets next', F.nextRung([CO]) === 'market')
ck('a market offers stores next', F.nextRung([CO, MK]) === 'store')
ck('a store is the last rung — below it are the lines', F.nextRung([CO, MK, ST]) === 'line')
ck('a store reached without a market still ends at the lines', F.nextRung([CO, ST]) === 'line')
ck('a COMPANY scope row starts at the company',
  F.drillRootLevels('company:abc', 'Acme').length === 1
  && F.drillRootLevels('company:abc', 'Acme')[0].kind === 'scope')
ck('a STORE scope row starts AT the store, so it goes straight to its lines',
  F.drillRootLevels('store:103 Fulton Ave', '103 Fulton Ave').length === 2
  && F.nextRung(F.drillRootLevels('store:103 Fulton Ave', '103 Fulton Ave')) === 'line')
ck('…and that root filters to exactly that store',
  JSON.stringify(F.drillFilter(F.drillRootLevels('store:103 Fulton Ave', 'x')).stores) === JSON.stringify(['103 Fulton Ave']))
ck('the consolidated scope drills by market too',
  F.nextRung(F.drillRootLevels('consolidated', 'Consolidated')) === 'market')

console.log('\n── C. the market vocabulary ──')
ck('every market of the canonical index is offered when nothing narrows it',
  JSON.stringify(F.marketsForScope(['PA', 'NY', 'NJ'], null)) === JSON.stringify(['PA', 'NY', 'NJ']))
ck('an EMPTY in-scope list is "not known yet" — the list is never silently shortened',
  F.marketsForScope(['PA', 'NY'], []).length === 2)
ck('a known in-scope list narrows it, case-insensitively',
  JSON.stringify(F.marketsForScope(['PA', 'NY', 'NJ'], ['pa', 'nj'])) === JSON.stringify(['PA', 'NJ']))
ck('duplicate spellings fold to one offer',
  JSON.stringify(F.marketsForScope(['PA', 'pa', ' PA '], null)) === JSON.stringify(['PA']))
ck('blank and missing entries are dropped',
  JSON.stringify(F.marketsForScope(['PA', '', '   ', null, undefined], null)) === JSON.stringify(['PA']))
ck('no markets at all yields no rung rather than a crash',
  F.marketsForScope(null, null).length === 0 && F.marketsForScope(undefined, ['x']).length === 0)

console.log('\n── D. ALL four sections, in the P&L page’s order ──')
const st = {
  sections: [
    { type: 'revenue', subtotal: 71924.68, lines: [{ key: 'device_rev', label: 'Device sales revenue', amount: 3812.45, detail: {} }] },
    { type: 'cogs', subtotal: 29329.14, lines: [{ key: 'device_cost', label: 'Device cost', amount: 28149.29, detail: {} }] },
    { type: 'opex', subtotal: 13585.14, lines: [
      { key: 'rep_comm', label: 'Rep commissions paid', amount: 235.98, detail: { 'Rep commission': 235.98 } },
      { key: 'wages', label: 'Gross Payroll', amount: 5929.16, detail: { 'Employee Salaries': 4479.16, 'Owner / Mgmt Salaries': 1450.0 } },
      { key: 'store_opex', label: 'Store operating expenses (rent / utilities / supplies)', amount: 7420.0, detail: { 'Rent / Lease': 4150.0, Electric: 600.0, 'Dm Salary': 850.0, 'Taxes / Accounting': 1820.0 } },
    ] },
    { type: 'other', subtotal: 0, lines: [] },
  ],
  gross_profit: 42595.54, net_operating_income: 29010.4, net_income: 29010.4,
}
ck('all four section types are known to the display', F.ALL_SECTIONS.length === 4)
ck('the order is the P&L page’s own',
  JSON.stringify([...F.ALL_SECTIONS]) === JSON.stringify(['revenue', 'cogs', 'opex', 'other']))
ck('an EMPTY section is dropped (no blank heading)',
  F.allSections(st).map(s => s.type).join(',') === 'revenue,cogs,opex')
ck('every non-empty section is rendered — not just the expenses (the owner’s complaint)',
  F.allSections(st).some(s => s.type === 'revenue') && F.allSections(st).some(s => s.type === 'cogs'))
ck('a missing / garbage statement yields no sections rather than a crash',
  F.allSections(null).length === 0 && F.allSections({}).length === 0 && F.allSections({ sections: null }).length === 0)
ck('every section has a human title, and they are the P&L’s words',
  F.ALL_SECTIONS.every(t => typeof F.SECTION_TITLE[t] === 'string' && F.SECTION_TITLE[t].length > 0)
  && F.SECTION_TITLE.opex === 'Operating Expenses' && F.SECTION_TITLE.cogs === 'Cost of Goods Sold')
ck('amounts are passed through untouched (nothing here rounds or re-sums)',
  F.allSections(st)[2].lines[1].amount === 5929.16)

console.log('\n── E. lineTieOut is a CHECK, never a source ──')
const wages = F.allSections(st)[2].lines[1]
ck('a line whose rows add up ties',
  F.lineTieOut(wages).checked && F.lineTieOut(wages).agree && F.lineTieOut(wages).detail === 5929.16)
ck('THE OWNER’S CASE — the salary line now drills to the rows behind it',
  Object.keys(wages.detail).length === 2 && wages.detail['Employee Salaries'] === 4479.16)
ck('a line with NO rows is not a failure, it is simply unchecked',
  F.lineTieOut({ amount: 10, detail: {} }).checked === false
  && F.lineTieOut({ amount: 10 }).checked === false && F.lineTieOut(null).checked === false)
ck('a line whose rows disagree is REPORTED, with the difference',
  (() => { const t = F.lineTieOut({ amount: 100, detail: { a: 60, b: 30 } }); return t.checked && !t.agree && t.delta === -10 })())
ck('…and the tie-out never substitutes its own total for the line',
  F.lineTieOut({ amount: 100, detail: { a: 60 } }).detail === 60)
ck('a cent of float noise does not cry wolf',
  F.lineTieOut({ amount: 0.3, detail: { a: 0.1, b: 0.2 } }).agree === true)
ck('a negative row (a correction) ties like any other',
  F.lineTieOut({ amount: 60, detail: { a: 100, b: -40 } }).agree === true)

console.log('\n── F. levelTotals: read, never recomputed; absence is not zero ──')
const t = F.levelTotals(st)
ck('revenue is the revenue section’s own subtotal', t.revenue === 71924.68)
ck('gross profit is the statement’s own', t.gross_profit === 42595.54)
ck('net income is the statement’s own', t.net_income === 29010.4)
ck('expenses uses the HUB’s definition, dereferenced (opex + other)', t.expenses === 13585.14)
ck('…and that definition is the one home, not a second list',
  JSON.stringify([...F.EXPENSE_SECTIONS]) === JSON.stringify(['opex', 'other']))
ck('gross profit − expenses == net income on a coherent level',
  Math.round((t.gross_profit - t.expenses - t.net_income) * 100) / 100 === 0)
ck('a level with NO statement is NOT reported (and not $0.00)',
  F.levelTotals(null).reported === false && F.levelTotals(undefined).reported === false)
ck('a level whose statement reports nothing is reported, at zero',
  F.levelTotals({ sections: [] }).reported === true && F.levelTotals({ sections: [] }).expenses === 0)

console.log('\n── G. the page wiring ──')
const panel = codeOnly(page.slice(page.indexOf('function ScopeDrillDown')))
const urls = (panel.match(/\/api\/v1\/[a-z0-9/_-]+/g) || []).filter(u => !u.startsWith('/api/v1/core/markets'))
ck('G1 every rung is the SAME canonical P&L read, one filter deeper',
  panel.includes('plQuery(') && (panel.match(/plQuery\(/g) || []).length >= 2)
ck('G2 the panel spells NO money URL of its own', urls.length === 0, urls)
ck('G3 the one URL it does build is the canonical market vocabulary index',
  panel.includes('/api/v1/core/markets'))
ck('G4 the P&L query has ONE frontend spelling, and it lives in plStatement',
  plstmt.includes('/api/v1/account/pl/') && !panel.includes('/api/v1/account/pl/'))
ck('G5 the breadcrumb is the way back up (a crumb truncates the path)',
  panel.includes('setPath(path.slice(0, i + 1))'))
ck('G6 a rung descends by APPENDING to the path, never by rewriting the scope',
  panel.includes('setPath([...path, c])'))
ck('G7 each line renders the rows behind it',
  panel.includes('l.detail') && panel.includes('lineTieOut('))
ck('G8 all four sections and the page’s own totals render',
  panel.includes('allSections(') && panel.includes('Net Operating Income') && panel.includes('Gross Profit'))
ck('G9 only the open rung and the open line fetch / expand (the panel stays lazy)',
  panel.includes('childTotals[') && panel.includes('openLines['))
ck('G10 a rung with no computed statement says "not reported", never $0.00',
  panel.includes('not reported'))
ck('G11 the panel derives no dollar — no arithmetic on money in its own body',
  !/\bamount\s*[-+]\s/.test(panel) && !panel.includes('.reduce((a'))
ck('G12 RULE TWO — no tenant / carrier / company / store name in the drill code',
  !/(?:boost|luxelink|nova\s*wave|cellfonz|t-?mobile|verizon)/i.test(panel + codeOnly(readFileSync(HELPER, 'utf8'))))
ck('G13 the drill helpers live in the EXISTING display home, not a sibling file',
  readFileSync(HELPER, 'utf8').includes('drillFilter') && page.includes("from './_components/scopeFinancials'"))

console.log('')
console.log(`prove_pl_drill_path: ${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
console.log('ALL CHECKS PASSED')
