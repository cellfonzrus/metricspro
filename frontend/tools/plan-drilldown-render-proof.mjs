#!/usr/bin/env node
// RENDER proof for the 2026-08-04 plan-drilldown directive — the companion to
// plan-drilldown-proof.mjs (which proves the pure ordering/grouping/category maths).
//
// This one renders the ACTUAL component the two pages mount (_lib/PlanLineBreakdown.tsx) with
// react-dom/server and reads the produced HTML, so the proof is about what a human sees, not about a
// helper function in isolation:
//   • the transaction group headers appear in date → numeric-trans-id order;
//   • each group carries its own "Subtotal paid on this transaction" cell and they add to the total;
//   • every line of a transaction sits between that transaction's header and the next one;
//   • the per-category breakdown table renders one row per rule present in the data, defaulting to
//     ALL categories with the totals visible;
//   • the grand total on screen equals the payload total, to the cent.
//
// Run:  node frontend/tools/plan-drilldown-render-proof.mjs
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, rmSync, readFileSync, writeFileSync, symlinkSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import os from 'node:os'
import path from 'node:path'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const LIB = path.join(FRONTEND, 'src/app/(platform)/commcalc/_lib')
const OUT = mkdtempSync(path.join(os.tmpdir(), 'planrender-'))
process.on('exit', () => rmSync(OUT, { recursive: true, force: true }))
// keep the scratch out of the repo, but let it resolve react/jsx-runtime from the app's own install
symlinkSync(path.join(FRONTEND, 'node_modules'), path.join(OUT, 'node_modules'))
writeFileSync(path.join(OUT, 'package.json'), '{"type":"module"}')

let pass = 0, fail = 0
const ok = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}
const section = t => console.log(`\n${t}`)

// The component sources are used VERBATIM; only the app's `@/lib/client` path alias is repointed at a
// shim carrying the SAME one-line formatter client.ts exports (asserted against client.ts itself).
const clientSrc = readFileSync(path.join(FRONTEND, 'src/lib/client.ts'), 'utf8')
ok('shim matches the real fmt in lib/client.ts',
  /export const fmt = \(n: number\) =>\s*\n\s*new Intl\.NumberFormat\('en-US', \{ style: 'currency', currency: 'USD' \}\)\.format\(n \|\| 0\)/
    .test(clientSrc))
mkdirSync(path.join(OUT, 'src'))
writeFileSync(path.join(OUT, 'src/lib-client.ts'),
  "export const fmt = (n: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n || 0)\n")
writeFileSync(path.join(OUT, 'src/planLines.ts'), readFileSync(path.join(LIB, 'planLines.ts'), 'utf8'))
writeFileSync(path.join(OUT, 'src/PlanLineBreakdown.tsx'),
  readFileSync(path.join(LIB, 'PlanLineBreakdown.tsx'), 'utf8').replace("'@/lib/client'", "'./lib-client'"))

execFileSync(path.join(FRONTEND, 'node_modules/.bin/tsc'), [
  path.join(OUT, 'src/planLines.ts'), path.join(OUT, 'src/lib-client.ts'), path.join(OUT, 'src/PlanLineBreakdown.tsx'),
  '--outDir', path.join(OUT, 'js'), '--target', 'es2020', '--module', 'es2020',
  '--moduleResolution', 'node', '--jsx', 'react-jsx', '--esModuleInterop', '--skipLibCheck',
], { stdio: 'inherit' })

// node's ESM loader needs explicit extensions on relative specifiers (tsc emits them bare)
for (const f of ['PlanLineBreakdown.js', 'planLines.js', 'lib-client.js']) {
  const p = path.join(OUT, 'js', f)
  writeFileSync(p, readFileSync(p, 'utf8').replace(/from '(\.\/[^']+?)'/g, (m, s) => `from '${s}.js'`))
}

const React = (await import('react')).default
const { renderToStaticMarkup } = await import('react-dom/server.node')
const PlanLineBreakdown = (await import(pathToFileURL(path.join(OUT, 'js/PlanLineBreakdown.js')).href)).default

const money = n => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n) || 0)
const L = (rule, date, trans_id, product, amount, extra = {}) =>
  ({ rule, basis: '% GP', date, trans_id, product, contract_type: 'NEW', ext_price: 100, gp: 40, amount, ...extra })

// same scrambled payload the owner was looking at (2093 / 3207 split apart, 297 vs 5452 same day)
const payload = [
  L('accessory',   '2026-07-11', '5452', 'Case — Otterbox',      6.50),
  L('twp',         '2026-07-03', '3207', 'TWP protection',      12.00),
  L('edge',        '2026-07-02', '2093', 'Edge financing',      25.00),
  L('activations', '2026-07-11', '297',  'Activation — NY',     10.00),
  L('accessory',   '2026-07-03', '3207', 'Case — Speck',         4.25),
  L('vhi2',        '2026-07-02', '2093', 'VHI tier 2',          15.00),
  L('activations', '2026-07-03', '3207', 'Activation payment',  10.00),
  L('upgrade',     '2026-07-02', '2093', 'Upgrade — device',     0.00),
  L('edge',        '2026-07-03', '3207', 'Financed device',     25.00),
  L('accessory',   '2026-07-03', '3207', 'Access charge',        3.75),
]
const total = payload.reduce((s, l) => s + (Number(l.amount) || 0), 0)

const html = renderToStaticMarkup(React.createElement(PlanLineBreakdown, { rows: payload, compact: true }))

section('RENDERED HTML — transaction groups')
const headers = [...html.matchAll(/Trans (?:<!-- -->)?([^<]*)<\/b>/g)].map(m => m[1].trim())
console.log('       group headers in DOM order: ' + headers.join(' → '))
ok('groups render in date → numeric trans-id order',
  JSON.stringify(headers) === JSON.stringify(['2093', '3207', '297', '5452']), headers.join(','))
const subs = [...html.matchAll(/title="Subtotal paid on this transaction">([^<]*)</g)].map(m => m[1])
console.log('       subtotals in DOM order:     ' + subs.join(' → '))
ok('every group renders a subtotal', subs.length === headers.length, `${subs.length} vs ${headers.length}`)
ok('subtotals are 2093=$40.00, 3207=$55.00, 297=$10.00, 5452=$6.50',
  JSON.stringify(subs) === JSON.stringify(['$40.00', '$55.00', '$10.00', '$6.50']), subs.join(','))
const subSum = subs.reduce((s, v) => s + Number(v.replace(/[^0-9.-]/g, '')), 0)
ok('Σ rendered subtotals === payload total', Math.abs(subSum - total) < 0.005, `${subSum} vs ${total}`)

section('RENDERED HTML — contiguity')
// each product name must fall between its own transaction's header and the next header
const blocks = html.split(/🧾 /).slice(1)
ok('one block per transaction', blocks.length === 4, String(blocks.length))
const belongs = (block, id) => payload.filter(l => l.trans_id === id).every(l => block.includes(l.product))
ok("trans 2093's 3 products all sit inside block 1", belongs(blocks[0], '2093'))
ok("trans 3207's 5 products all sit inside block 2", belongs(blocks[1], '3207'))
ok('no product from another transaction leaks into block 1',
  payload.filter(l => l.trans_id !== '2093').every(l => !blocks[0].includes(l.product)))
ok('no product from another transaction leaks into block 2',
  payload.filter(l => l.trans_id !== '3207').every(l => !blocks[1].includes(l.product)))

section('RENDERED HTML — category breakdown (default = ALL, totals visible)')
for (const c of [...new Set(payload.map(l => l.rule))])
  ok(`category row rendered: ${c}`, html.includes(`☐ ${c}`) || html.includes(`☐ <!-- -->${c}`))
ok('default view selects no chip → "All ·" chip is the active one', html.includes('All ·'))
ok('breakdown footer row "All categories" is visible', html.includes('All categories'))
ok(`grand total ${money(total)} appears on screen`, html.includes(money(total)))
ok('no "No lines in the selected category" empty state on the default view',
  !html.includes('No lines in the selected category'))

section('RENDERED HTML — dual membership reads clearly (2026-08-04)')
// The accessory inside an edge-financed sale: paying under `accessory`, ⛔ under `edge`. Payload order
// puts the ⛔ row FIRST, the way it led on screen when the owner read it as "not paid".
const ACC = 'Case — Otterbox XL'
const dual = [
  L('edge',      '2026-07-09', '7777', ACC, 0.00,
    { suppressed: true, suppressed_reason: 'device already paid on this transaction', would_have_paid: 6.50 }),
  L('edge',      '2026-07-09', '7777', 'Financed device — A15', 25.00),
  L('accessory', '2026-07-09', '7777', ACC, 6.50),
]
const dualTotal = dual.reduce((s, l) => s + (Number(l.amount) || 0), 0)
const dhtml = renderToStaticMarkup(React.createElement(PlanLineBreakdown, { rows: dual, compact: true }))
// everything a human can actually READ — tooltips stripped, so a title-only answer cannot pass
const visible = dhtml.replace(/title="[^"]*"/g, '').replace(/<[^>]+>/g, ' ').replace(/<!-- -->/g, '')

ok('the ⛔ reason is VISIBLE text, not only a tooltip',
  /device already paid on this transaction/.test(visible))
ok('the ⛔ names the rule that suppressed it (not "an accessory suppression")',
  /edge\s*—\s*device already paid on this transaction/.test(visible))
ok('the ⛔ row says where the line DID get paid',
  /paid under\s*accessory/.test(visible) && /\$6\.50/.test(visible))
ok('the paying row says the same line also matched the other rule',
  /also matched\s*edge/.test(visible))
ok('the cross-reference calls them the same line',
  /same line/.test(visible))
const pPay = dhtml.indexOf('$6.50'), pSup = dhtml.indexOf('device already paid')
ok('the PAYING row renders BEFORE its ⛔ twin', pPay > -1 && pSup > -1 && pPay < pSup,
  `${pPay} < ${pSup}`)
ok('both rows of the same sale line still render (nothing is hidden)',
  (dhtml.split(ACC).length - 1) >= 2, String(dhtml.split(ACC).length - 1))
ok(`grand total ${money(dualTotal)} still on screen`, dhtml.includes(money(dualTotal)))
ok('transaction subtotal is still the sum of the paid rows',
  [...dhtml.matchAll(/title="Subtotal paid on this transaction">([^<]*)</g)]
    .map(m => m[1]).join(',') === money(dualTotal), money(dualTotal))
// a payload with NO dual membership must gain no cross-reference chrome at all
ok('single-membership payload renders NO cross-reference lines',
  !/same line/.test(html.replace(/title="[^"]*"/g, '')) && !/also matched/.test(html.replace(/title="[^"]*"/g, '')))

console.log(`\n${fail === 0 ? 'ALL GREEN' : 'FAILURES'} — ${pass} passed, ${fail} failed`)
// react-dom's scheduler keeps a MessageChannel alive, so exit explicitly rather than hanging
rmSync(OUT, { recursive: true, force: true })
process.exit(fail === 0 ? 0 : 1)
