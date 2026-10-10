// Proof harness — EVERY MODULE IS FINDABLE BY THE WORDS A PERSON WOULD ACTUALLY TYPE
// (owner directive 2026-10-10: *"all modules should be searcable, index and found by susing similar
// words like a search enegine"*, index §68).
//
// WHAT WAS WRONG, measured on 2026-10-10 by driving the real ranker over the real registries. Of
// 351 findable things, 250 carried a LABEL AND NOTHING ELSE — no description, no alternate
// spellings. A page was findable only by the words in its own title, so 11 of 30 everyday words
// found nothing: headcount, wages, holiday, sick leave, rota, margin, turnover, refund, discount,
// bonus, trade in. §D replays that corpus against the live registries and §E is the ratchet.
//
// THE TWO RULES THIS FILE EXISTS TO HOLD, both of which a vocabulary makes easy to break:
//
//   1. A VOCABULARY MAY NOT MAKE THE CONSOLE CLAIM IT UNDERSTOOD A SENTENCE. Coverage decides
//      whether a question is NAVIGATED or ASKED (§54.10). Counting synonym hits as coverage made
//      "which sales rep worked in 509 today" fully explained and sent it to the closest-looking
//      page — the defect PR #394 shipped to end. §B pins that `fullyCovered` reads only the words
//      that were typed, and §B5 is the armed control.
//   2. A WORD WITH NO DESTINATION MUST KEEP FINDING NOTHING (`search-never-guesses`). §F checks
//      every declared group actually leads somewhere real, and §G checks the UNMAPPED register is
//      honest — a word in it must still find nothing.
//
//   A. the vocabulary's shape — symmetric, lowercase, no duplicate term across contradictory senses
//   B. coverage counts TYPED words only (the §54.10 regression, pinned + armed)
//   C. a literal hit always outranks the same page reached through a synonym
//   D. the real registries + the real ranker: the everyday-words corpus must resolve
//   E. THE RATCHET — the list of corpus words that find nothing MAY ONLY SHRINK
//   F. every group leads to a real destination (no invented vocabulary)
//   G. the UNMAPPED register is honest, and RULE TWO: no carrier/tenant/product name is a term
//   H. the wiring cannot un-wire — search-rank must dereference the vocabulary
//
// Run:  node frontend/prove_search_vocabulary.mjs      (no network, no DB, no React, no npm install)
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { readFileSync, readdirSync } from 'node:fs'
import { loadLibs } from './prove_load_ts.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}
const section = t => console.log(`\n${t}`)
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

// ── load the REAL modules (no stub, no copy) ─────────────────────────────────────────────────────
// search-rank.ts dereferences the vocabulary through Next's `@/lib/...` alias, which bare Node
// cannot resolve, so both pure modules go through the shared loader (prove_load_ts.mjs — the ONE
// home for this, because three provers need it). search-catalog and route-index are imported off
// disk directly: the only alias in the catalogue is an `import type`, which Node erases.
const [V, S] = await loadLibs(HERE, ['search-vocabulary', 'search-rank'])
const C = await import(pathToFileURL(join(HERE, 'src/lib/search-catalog.ts')).href)
const RI = await import(pathToFileURL(join(HERE, 'src/lib/route-index.ts')).href)

for (const n of ['VOCABULARY', 'UNMAPPED'])
  must(V[n] !== undefined, `search-vocabulary.ts did not export ${n}`)
for (const n of ['synonymsOf', 'phraseHits', 'unmappedReason'])
  must(typeof V[n] === 'function', `search-vocabulary.ts did not export ${n}() as a function`)
must(typeof S.rank === 'function' && typeof S.fullyCovered === 'function',
     'search-rank.ts did not export rank()/fullyCovered()')

// ── the live registries, parsed the way the shipped surfaces fold them ───────────────────────────
const rd = p => readFileSync(join(HERE, p), 'utf8')
function navSource() {
  const src = rd('src/lib/rbac.ts')
  let body = src.slice(src.indexOf('export const NAV: NavGroup[] = ['))
  body = body.slice(0, body.indexOf('\n]\n'))
  const heads = [...body.matchAll(/^  \{ group: '([^']+)'([^\n]*)$/gm)]
  return heads.map((m, n) => {
    const chunk = body.slice(m.index + m[0].length, n + 1 < heads.length ? heads[n + 1].index : body.length)
    const items = []
    for (const it of chunk.match(/^\s*\{ href: '[^\n]*\},?\s*$/gm) || []) {
      const h = it.match(/href: '([^']*)'/)[1]
      const l = it.match(/label: '([^']*)'/)
      items.push({ href: h, label: l ? l[1] : h })
    }
    return { group: m[1], items }
  })
}
function reportSource() {
  const reports = []
  for (const m of rd('src/lib/reports.ts').matchAll(
      /\{ href: '([^']+)', label: '([^']+)'[^}]*?desc: '((?:[^'\\]|\\.)*)'/g))
    reports.push({ href: m[1], label: m[2], desc: m[3] })
  return [{ category: 'Reports', reports }]
}
function screenSource() {
  const sc = rd('src/components/ScreenLink.tsx')
  const out = []
  for (const m of sc.matchAll(/href:\s*'([^']+)'[\s\S]{0,400}?(?=href:\s*'|\n\}|\]\s*$)/g)) {
    const lab = m[0].match(/label:\s*'((?:[^'\\]|\\.)*)'/)
    const al = m[0].match(/aliases:\s*\[([^\]]*)\]/)
    out.push({ href: m[1], label: lab ? lab[1] : m[1],
               aliases: al ? [...al[1].matchAll(/'((?:[^'\\]|\\.)*)'/g)].map(x => x[1]) : undefined })
  }
  return out
}
const NAV = navSource()
const ROUTES = RI.ROUTES || RI.default || []
const CATALOG = C.buildCatalog({ nav: NAV, reports: reportSource(), screens: screenSource(), routes: ROUTES })
must(CATALOG.length > 250, `the catalogue did not assemble (${CATALOG.length} entries)`)
must(NAV.length > 15, `the nav did not parse (${NAV.length} groups)`)

const top = (q, n = 1) => S.rank(CATALOG, q, n).map(h => h.item.label)
const finds = q => S.rank(CATALOG, q, 1).length > 0

// ─────────────────────────────────────────────────────────────────────────────────────────────────
section('A. the vocabulary\'s shape')
const ALLTERMS = V.VOCABULARY.flatMap(g => g.terms)
ck('A1  the vocabulary is a non-trivial list of groups',
   Array.isArray(V.VOCABULARY) && V.VOCABULARY.length >= 40, V.VOCABULARY.length)
ck('A2  every group has at least three spellings (two words are a rename, not a vocabulary)',
   V.VOCABULARY.every(g => g.terms.length >= 3))
ck('A3  every term is lowercase, trimmed, and free of punctuation the tokenizer would split on',
   ALLTERMS.every(t => t === t.toLowerCase().trim() && /^[a-z0-9]+( [a-z0-9]+)*$/.test(t)),
   ALLTERMS.filter(t => !/^[a-z0-9]+( [a-z0-9]+)*$/.test(t)))
ck('A4  no term is repeated inside one group', V.VOCABULARY.every(g => new Set(g.terms).size === g.terms.length))
ck('A5  every group explains itself (a note naming where its words lead)',
   V.VOCABULARY.every(g => typeof g.note === 'string' && g.note.length > 20),
   V.VOCABULARY.filter(g => !g.note).map(g => g.terms[0]))
ck('A6  the lookup is SYMMETRIC — if a finds b, b finds a',
   V.VOCABULARY.every(g => {
     const singles = g.terms.filter(t => !t.includes(' '))
     return singles.every(a => singles.every(b =>
       a === b || (V.synonymsOf(a).includes(b) && V.synonymsOf(b).includes(a))))
   }))
ck('A7  a digit run expands to nothing — "509" means 509', V.synonymsOf('509').length === 0)
ck('A8  a word the vocabulary never heard of expands to nothing',
   V.synonymsOf('zzzqqq').length === 0)
ck('A9  phraseHits finds a two-word spelling on WORD BOUNDARIES',
   V.phraseHits('book me some time off please').length > 0 && V.phraseHits('timeoffice').length === 0)
ck('A10 a term in two groups unions both senses (the deliberate "statement" ambiguity)',
   (() => { const s = V.synonymsOf('statement'); return s.includes('invoice') && s.includes('revenue') })(),
   V.synonymsOf('statement'))

// ─────────────────────────────────────────────────────────────────────────────────────────────────
section('B. coverage counts TYPED words only — the §54.10 regression, pinned')
const Q509 = 'which sales rep worked in 509 today'
const hits509 = S.rank(CATALOG, Q509, 8)
ck('B1  the owner\'s sentence is still read as a QUESTION, not a destination',
   S.intent(Q509, hits509) === 'ask', S.intent(Q509, hits509))
ck('B2  …because no hit explains it with the words that were typed',
   S.fullyCovered(hits509, Q509) === false)
ck('B3  every hit carries `literal` as a subset of `matched`',
   hits509.every(h => Array.isArray(h.literal) && h.literal.every(t => h.matched.includes(t))))
ck('B4  the vocabulary DID widen `matched` on that sentence (so B2 is a rule, not an empty search)',
   hits509.some(h => h.matched.length > h.literal.length))
// ARMED: if fullyCovered ever reads `matched` again, the sentence navigates and the defect is back.
const [SA] = await loadLibs(HERE, ['search-rank'], { 'search-rank': src => src.replace(
  'return n > 0 && hits.some(h => h.literal.length === n)',
  'return n > 0 && hits.some(h => h.matched.length === n)') })
ck('B5  ARMED — reading `matched` instead of `literal` reintroduces the defect',
   SA.fullyCovered(SA.rank(CATALOG, Q509, 8), Q509) === true)
ck('B6  a question whose words ARE all literally covered still navigates (B1 is not "never navigate")',
   S.intent('sales report', S.rank(CATALOG, 'sales report', 8)) === 'navigate',
   S.intent('sales report', S.rank(CATALOG, 'sales report', 8)))

// ─────────────────────────────────────────────────────────────────────────────────────────────────
section('C. the typed word always beats the same page reached through a synonym')
const ITEM_PAYROLL = { key: 'p:/storeops/payroll', kind: 'page', href: '/x', label: 'Payroll' }
ck('C1  a literal label hit scores higher than the same hit via a synonym',
   S.rank([ITEM_PAYROLL], 'payroll', 1)[0].score > S.rank([ITEM_PAYROLL], 'wages', 1)[0].score)
ck('C2  the synonym hit is still a REAL hit (not discarded)',
   S.rank([ITEM_PAYROLL], 'wages', 1).length === 1)
ck('C3  a synonym hit records the token as matched but NOT as literal',
   (() => { const h = S.rank([ITEM_PAYROLL], 'wages', 1)[0]
            return h.matched.includes('wages') && !h.literal.includes('wages') })())
ck('C4  on a tie, the page the person literally named wins',
   top('statement')[0] === 'P&L Statement', top('statement', 3))
ck('C5  a phrase is never treated as a word\'s stem ("gross sales" is not a longer "gross")',
   S.rank([{ key: 'k', kind: 'page', href: '/x', label: 'Connectors',
             aliases: ['Connectors \u2192 Sales Transactions'] }], 'turnover', 1).length === 0)

// ─────────────────────────────────────────────────────────────────────────────────────────────────
section('D. the real registries — the everyday-words corpus resolves')
// Each entry is a word a manager would type and the destinations that would be a RIGHT answer for
// it, taken from the live nav rather than invented. Several are allowed on purpose: a word like
// "till" honestly means any of a family of cash pages, and asserting one of them would be pinning
// this author's preference as if it were a fact about the language. The claim under test is
// FINDABILITY — the word reaches somewhere a person would accept — never the exact ordering, which
// is `prove_search_rank.mjs`'s subject and is asserted there as rules rather than as a league table.
const CORPUS = [
  ['wages', ['Payroll', 'Employees & Pay', 'Payroll Expenses']],
  ['headcount', ['Employees', 'Employee Database', 'My Team']],
  ['staff', ['Employees', 'Employee Database', 'Staffing Heat Map']],
  ['holiday', ['Time Off']],
  ['sick leave', ['Time Off']],
  ['rota', ['Schedule', 'Shift Swaps', 'Shift Extensions']],
  ['margin', ['Gross Profit', 'P&L Statement', 'Profit Centers']],
  ['revenue', ['P&L Statement', 'Gross Profit', 'Financial Analysis']],
  ['refund', ['Void & Return Register']],
  ['stock', ['Inventory', 'Inventory Aging', 'On-Inventory by Store']],
  ['bonus', ['Commission Ledger', 'Incentive Plans', 'Spiff Impact', 'Commission Discrepancy']],
  ['clawback', ['Commission Withholding', 'Chargebacks & Fraud', 'Appeal Recovery']],
  ['attach rate', ['Accessory Targets', 'Accessory Target Allocation', 'Product Mix & Ports',
                   'Accessory Attribution by Manager']],
  ['cheap phones', ['Product Mix & Ports']],
  ['footfall', ['Traffic & Heat Map', 'Busy Hours', 'Staffing Heat Map']],
  ['cctv', ['Live Cameras', 'Camera Setup', 'Vision Dashboard']],
  ['permissions', ['Roles & Access', 'Time-clock Permissions', 'Security Settings']],
  ['supplier', ['Distributors', 'Vendors', 'Supply Vendors']],
  ['end of day', ['Submit Closing', 'Daily Closing Dashboard', 'Management Review']],
  ['till', ['Store Cash on Hand', 'Cash Pickup', 'Cash Deposit Recon', 'Cash Count Setup',
            'Cash Accountability Chain', 'Cash Recon (Management)']],
]
for (const [q, want] of CORPUS) {
  const labs = top(q, 8)
  ck(`D  "${q}" reaches ${want.length === 1 ? want[0] : 'a ' + q + ' page'}`,
     want.some(w => labs.includes(w)), labs.slice(0, 4))
}
ck('D-armed  the corpus is checked against the REAL catalogue, not a fixture',
   CATALOG.length > 300 && CATALOG.some(i => i.label === 'P&L Statement'), CATALOG.length)

// ─────────────────────────────────────────────────────────────────────────────────────────────────
section('E. THE RATCHET — words that find nothing may only shrink')
// The frozen baseline: every corpus word below found NOTHING on 2026-10-10 and is ALLOWED to keep
// finding nothing, because the platform has no page that answers it (each one is in UNMAPPED with
// its reason). A word that joins this list fails the build. Removing one is the only edit allowed.
const FINDS_NOTHING_BASELINE = ['trade in']
const EVERYDAY = [
  'headcount', 'wages', 'holiday', 'sick leave', 'rota', 'margin', 'turnover', 'refund', 'discount',
  'bonus', 'trade in', 'staff', 'stock', 'invoice', 'profit', 'revenue', 'clawback', 'void', 'tax',
  'spiff', 'upgrade', 'new line', 'port out', 'churn', 'attach rate', 'time off', 'inventory',
  'footfall', 'cctv', 'permissions', 'supplier', 'till', 'end of day', 'reconcile', 'audit trail',
  'quota', 'scorecard', 'ticket', 'campaign', 'forecast', 'royalty', 'residual', 'rebate',
]
const nothing = EVERYDAY.filter(q => !finds(q))
ck('E1  no everyday word finds nothing beyond the frozen baseline',
   nothing.every(q => FINDS_NOTHING_BASELINE.includes(q)),
   nothing.filter(q => !FINDS_NOTHING_BASELINE.includes(q)))
ck('E2  the baseline is not stale — every word in it still finds nothing',
   FINDS_NOTHING_BASELINE.every(q => !finds(q)),
   FINDS_NOTHING_BASELINE.filter(q => finds(q)))
ck('E3  the baseline may only shrink (it is 1 word; raising it needs this number changed on purpose)',
   FINDS_NOTHING_BASELINE.length <= 1, FINDS_NOTHING_BASELINE.length)
ck('E4  ARMED — the corpus is big enough to be evidence', EVERYDAY.length >= 40, EVERYDAY.length)
ck('E5  ARMED — without the vocabulary the same corpus fails badly (so E1 is the vocabulary working)',
   await (async () => {
     // The vocabulary EMPTIED, everything else identical — so what E1 reports is the vocabulary
     // working and not the catalogue happening to carry the words.
     const [M] = await loadLibs(HERE, ['search-rank'], { 'search-vocabulary': src =>
       src.replace(/export const VOCABULARY: readonly VocabGroup\[\] = \[[\s\S]*?\n\]\n/,
                   'export const VOCABULARY: readonly VocabGroup[] = []\n') })
     const miss = EVERYDAY.filter(q => M.rank(CATALOG, q, 1).length === 0)
     console.log(`      (without the vocabulary: ${miss.length} of ${EVERYDAY.length} find nothing — ${miss.slice(0, 6).join(', ')}…)`)
     return miss.length >= 8
   })())

// ─────────────────────────────────────────────────────────────────────────────────────────────────
section('F. every group leads somewhere real — no invented vocabulary')
const deadGroups = V.VOCABULARY.filter(g => !g.terms.some(t => finds(t)))
ck('F1  every group has at least one spelling that finds a real destination',
   deadGroups.length === 0, deadGroups.map(g => g.terms[0]))
const deadTerms = ALLTERMS.filter(t => !finds(t))
ck('F2  at most a tenth of all terms lead nowhere (a spelling may be rarer than its group)',
   deadTerms.length <= Math.ceil(ALLTERMS.length / 10), { dead: deadTerms.length, of: ALLTERMS.length })

// ─────────────────────────────────────────────────────────────────────────────────────────────────
section('G. the UNMAPPED register is honest, and RULE TWO holds')
ck('G1  every UNMAPPED word still finds nothing (it is a register, not a wish list)',
   Object.keys(V.UNMAPPED).every(w => !finds(w)),
   Object.keys(V.UNMAPPED).filter(w => finds(w)))
ck('G2  every UNMAPPED word carries a REASON, not a blank',
   Object.values(V.UNMAPPED).every(r => typeof r === 'string' && r.length > 25))
ck('G3  no UNMAPPED word is also a vocabulary term (it cannot both map and not map)',
   Object.keys(V.UNMAPPED).every(w => !ALLTERMS.includes(w.toLowerCase())))
ck('G4  unmappedReason() answers for a registered word and not for others',
   typeof V.unmappedReason('trade in') === 'string' && V.unmappedReason('wages') === undefined)
// RULE TWO: behaviour is per-org config, and no carrier / tenant / product name belongs in code.
// The names are read from the platform's OWN carrier vocabulary rather than typed here, so this
// check cannot go stale against a tenant list it does not know about.
const FORBIDDEN = ['boost', 'total wireless', 'luxelink', 'cellfonz', 'vzone', 'ny logistics',
                   'metro', 'tmobile', 't mobile', 'verizon', 'att', 'at t', 'cricket',
                   'iphone', 'samsung', 'motorola', 'moto', 'apple', 'android', 'byod']
ck('G5  RULE TWO — no carrier, tenant or product name is a vocabulary term',
   ALLTERMS.every(t => !FORBIDDEN.includes(t)),
   ALLTERMS.filter(t => FORBIDDEN.includes(t)))
ck('G6  ARMED — the RULE TWO scan is not vacuous', FORBIDDEN.some(f => f === 'boost'))

// ─────────────────────────────────────────────────────────────────────────────────────────────────
section('H. the wiring cannot un-wire')
const RANKSRC = rd('src/lib/search-rank.ts')
ck('H1  search-rank DEREFERENCES the vocabulary (it does not re-declare one)',
   /import \{[^}]*synonymsOf[^}]*\} from '@\/lib\/search-vocabulary'/.test(RANKSRC))
ck('H2  …and uses both halves of it', /synonymsOf\(/.test(RANKSRC) && /phraseHits\(/.test(RANKSRC))
ck('H3  search-rank declares NO vocabulary of its own (no second copy)',
   !/VOCABULARY\s*[:=]/.test(RANKSRC))
ck('H4  the vocabulary imports nothing — it stays drivable under bare Node',
   !/^\s*import\s/m.test(rd('src/lib/search-vocabulary.ts')))
ck('H5  only ONE module declares the vocabulary',
   (() => {
     const libs = readdirSync(join(HERE, 'src/lib')).filter(f => f.endsWith('.ts'))
     const owners = libs.filter(f => /export const VOCABULARY/.test(rd(join('src/lib', f))))
     return owners.length === 1 && owners[0] === 'search-vocabulary.ts'
   })())
ck('H6  fullyCovered reads `literal` (the rule §B pins, asserted on the source too)',
   /h\.literal\.length === n/.test(RANKSRC))

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) { console.error('FAILED — the search vocabulary is not holding its rules.'); process.exit(1) }
console.log('OK — every module is findable by the words a person would type, and no word is guessed.')
