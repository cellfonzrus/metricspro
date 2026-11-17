// Proof harness — WHAT A DAY PAYS: the screen shows the engine's answer and never computes one
// (§COMMCALC-78), and the one home for "what does a payout measure" stays dereferenced.
//
// TWO DEFECTS THIS FILE EXISTS FOR.
//
// 1. A SECOND FOLD. `daily_incentive.compute_day` is the one home for what a store-day owes. A page
//    that adds its own lines into its own total is a second answer to the same question and the two
//    drift — silently, because both look right until a pool goes unallocated or a cap bites. So the
//    page renders what it is handed and the only arithmetic allowed in it is `.length`.
//
// 2. A PROMISE KEPT IN ONE PLACE AND LOST IN THE NEXT. The rules editor tells the admin that a pool
//    with no categories ticked is "a percentage of the whole day's gross revenue", and stored the
//    payout with basis 'amount' — which SUMS the named categories, so naming none summed nothing and
//    the rule paid $0.00 for a store that had traded all day. `daily_incentive.payout_measure_basis`
//    is now the one home for what a payout measures, and this fails the build if a caller stops
//    asking it or a second copy of the rule appears.
//
// No network, no DB, no browser, no React. Run:  node frontend/prove_daily_incentive_screen.mjs

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra !== undefined ? '  — ' + String(extra).slice(0, 300) : ''}`) }
}

const PAGE = readFileSync(join(HERE, 'src/app/(platform)/commcalc/daily-incentive/page.tsx'), 'utf8')
const ENGINE = readFileSync(join(ROOT, 'backend/app/modules/commcalc/daily_incentive.py'), 'utf8')
const ROUTER = readFileSync(join(ROOT, 'backend/app/modules/commcalc/router.py'), 'utf8')
const RBAC = readFileSync(join(HERE, 'src/lib/rbac.ts'), 'utf8')
const RINDEX = readFileSync(join(HERE, 'src/lib/route-index.ts'), 'utf8')

// The page's EXECUTABLE text: the comments carry the prose that explains each rule, and these
// scans are about what the CODE does.
const CODE = PAGE.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')

console.log('\n§A — the screen shows the engine\'s answer and computes none of its own')
ck('it reads the one endpoint', /commcalc\/daily-incentive\/\$\{encodeURIComponent\(date\)\}/.test(CODE))
ck('exactly one place asks for a day',
   (CODE.match(/commcalc\/daily-incentive\//g) || []).length === 1)
// The total, the per-person figures and each rule's paid amount are the ENGINE's own fields.
for (const f of ['total', 'by_employee', 'store_revenue', 'unallocated', 'top_earner']) {
  ck(`the page renders the engine's \`${f}\` rather than deriving it`,
     new RegExp(`d(\\?)?\\.${f}|\\b${f}\\b`).test(CODE))
}
// NO SECOND FOLD: no reduce, no += over money, no summing of lines into a total.
ck('no reduce() anywhere in the page', !/\.reduce\s*\(/.test(CODE))
ck('no running total is accumulated', !/\+=/.test(CODE))
ck('the only arithmetic over a list is .length',
   !/\.map\([^)]*\)\s*\.\s*(reduce|sum)/.test(CODE))
ck('NEGATIVE CONTROL: a page-side total would be caught',
   /\.reduce\s*\(/.test('const total = lines.reduce((a, l) => a + l.amount, 0)'))

console.log('\n§B — every rule is shown, including the ones that paid nothing')
ck('the page iterates the engine\'s full rule list', /\(d\?\.rules \|\| \[\]\)\.map/.test(CODE))
ck('it does NOT filter the rule list down to the payers',
   !/rules[^\n]*\.filter\(/.test(CODE))
ck('a rule that can never pay says so, from the engine\'s own verdict',
   /payout_measurable === false/.test(CODE) && /payout_note/.test(CODE))
ck('the verdict is READ, never re-derived from the numbers',
   !/payout_measurable\s*=\s*[^=]/.test(CODE) && !/metric_keys/.test(CODE))
ck('what the source carried but could not pay on is shown, not dropped',
   /metric_source_not_counted/.test(CODE))
ck('a pool nobody could share is shown and kept OUT of the total',
   /unallocated/.test(CODE) && /not in the total/.test(PAGE))

console.log('\n§C — which source answered is named, and its options come from the endpoint')
ck('the source options are rendered from the reply', /metric_source_options \|\| \[\]\)\.map/.test(CODE))
ck('the page spells no source value of its own',
   !/['"]declaration['"]/.test(CODE) && !/value=['"]feed['"]/.test(CODE))
ck('the answer always names its source', /metric_source_note/.test(CODE))
ck('an override says so, so a figure is never mistaken for the standing choice',
   /metric_source_overridden/.test(CODE))
ck('a business that has not been able to choose yet is told so',
   /metric_source_declaration_pending/.test(CODE))

console.log('\n§D — the one home for what a payout measures')
ck('payout_measure_basis exists and is spelled once',
   (ENGINE.match(/def payout_measure_basis\(/g) || []).length === 1)
ck('compute_day DEREFERENCES it rather than defaulting to a basis itself',
   /pbasis = payout_measure_basis\(/.test(ENGINE))
ck('payout_measurable asks the same home, so the two can never disagree',
   /b = payout_measure_basis\(basis, metric_keys\)/.test(ENGINE))
ck('no caller re-decides the fallback next to the money',
   (ENGINE.match(/or \("units" if kind == "per_unit" else "amount"\)/g) || []).length === 1)
ck('UNITS is NOT resolved to revenue — there is no day\'s units to fall back to',
   /if b == "amount" and not \[k for k in \(metric_keys or \[\]\) if _key\(k\)\]/.test(ENGINE))
ck('the endpoint still computes only and persists nothing',
   /COMPUTES ONLY/.test(ROUTER))

console.log('\n§E — the screen is reachable, and named once')
ck('it is in the menu', /href: '\/commcalc\/daily-incentive'/.test(RBAC))
ck('it is in the page index', /path: '\/commcalc\/daily-incentive'/.test(RINDEX))
ck('the page index does NOT carry a second label — the menu is the label authority',
   !/path: '\/commcalc\/daily-incentive', label:/.test(RINDEX))
ck('it is told apart from the carrier accrual screen of a similar name',
   /Daily Commission/.test(readFileSync(join(HERE, 'src/components/ScreenLink.tsx'), 'utf8')))

console.log('\n§F — RULE TWO: no tenant\'s words are on the screen')
const FORBIDDEN = ['nda', '2da', 'ups domestic', 'mailbox', 'overnight', 'notary', 'luxelink',
                   'cellfonz', 'boost', 'logistics', 'printing']
const spells = (text, word) =>
  new RegExp(`(?<![a-z0-9])${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![a-z0-9])`)
    .test(text.toLowerCase())
for (const w of FORBIDDEN) ck(`the page does not spell '${w}'`, !spells(PAGE, w))
ck('CONTROL: the word scan finds a real mention and is not fooled by "upsert"',
   spells('a notary service', 'notary') && !spells('upsert', 'ups'))

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
console.log('OK — the engine answers, the screen shows it, and nothing computes the answer twice.')
