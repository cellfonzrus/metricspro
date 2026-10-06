// Proof harness — the platform search console ranks on MEANING, and an unexplained question goes to
// the assistant instead of to a confident wrong page (owner directive 2026-10-05, index §54).
//
// THE REPORTED DEFECT, replayed as the first test in this file. Asked *"who is working in 509
// nostrand"* the old ⌘/ bar answered "Carrier Earned vs Employee Paid", because it scored one point
// per typed word found anywhere in a report's text: "who", "is" and "in" carried the whole ranking,
// nothing matched "nostrand" or "working", and ties broke alphabetically. §A below asserts that
// sentence produces NO report hit and routes to the assistant, with the store itself offered when
// the store is in the index.
//
// No stub and no copy: Node strips the erasable TypeScript of the REAL src/lib/search-rank.ts (which
// imports nothing) and drives its exported functions.
//
//   A. the reported question — the regression, and the negative control that proves §A is the rule
//      working rather than the ranker refusing everything
//   B. tokens(): a digit run is always a content token; a one-letter word is not
//   C. stopwords earn NOTHING on their own, and a pure-stopword query ranks nothing at all
//   D. field order: label exact > label prefix > label word > subtitle/alias > category > desc
//   E. coverage beats loudness — explaining the whole query outranks one loud partial match
//   F. looksLikeQuestion(): question marks, interrogatives, sentence shape; a page NAME is not one
//   G. intent(): empty / ask / navigate, including the asymmetry (a COVERED question navigates)
//   H. determinism and purity — stable order regardless of input order, nothing mutated
//
// Run:  node frontend/prove_search_rank.mjs      (no network, no DB, no React, no npm install)
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const S = await import(pathToFileURL(join(HERE, 'src/lib/search-rank.ts')).href)

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}
const section = t => console.log(`\n${t}`)

// A catalogue shaped like the real one: the reports whose descriptions carried the bad ranking, a
// couple of settings screens, and the store the owner was actually asking about.
const CATALOGUE = [
  { key: 'report:/commcalc/carrier-vs-pay', kind: 'report', href: '/commcalc/carrier-vs-pay',
    label: 'Carrier Earned vs Employee Paid', category: 'Commission',
    desc: 'What the carrier paid the store against what the employee is paid, so you can see who is in line and who is not' },
  { key: 'report:/commcalc/watchdog', kind: 'report', href: '/commcalc/watchdog',
    label: 'Management Watchdog', category: 'Management',
    desc: 'Every finding in one place — who is working on what, what is open, and which store it is in' },
  { key: 'report:/commcalc/sales-report', kind: 'report', href: '/commcalc/sales-report',
    label: 'Sales Report', category: 'Sales',
    desc: 'Units, revenue and gross profit per store, per salesperson, per day' },
  { key: 'page:/storeops/schedule', kind: 'page', href: '/storeops/schedule',
    label: 'Schedule', category: 'Workforce', aliases: ['Scheduling', 'Shifts'],
    desc: 'Who is scheduled to work, per store, per day' },
  { key: 'setting:/storeops/roles', kind: 'setting', href: '/storeops/roles',
    label: 'Roles & Access', category: 'Settings', aliases: ['Roles and Access', 'RBAC', 'permissions'],
    desc: 'Which role sees which page' },
  { key: 'store:B-1115', kind: 'store', href: '/commcalc/stores?store=B-1115',
    label: 'B-1115', subtitle: '509 Nostrand Ave, Brooklyn NY', category: 'Stores' },
  { key: 'store:B-1598', kind: 'store', href: '/commcalc/stores?store=B-1598',
    label: 'B-1598', subtitle: '1800 Great Neck Rd, Copiague NY', category: 'Stores' },
  { key: 'person:abid', kind: 'person', href: '/hr?tab=employees&q=Abid',
    label: 'Abid Khan', context: 'B-1115 · 509 Nostrand Ave', category: 'People' },
]
const BAD = 'who is working in 509 nostrand'

section('A. THE REPORTED QUESTION — "who is working in 509 nostrand"')
const hitsBad = S.rank(CATALOGUE, BAD)
const labels = hitsBad.map(h => h.item.label)
ck('A1 "Carrier Earned vs Employee Paid" is NOT returned at all (the reported defect)',
   !labels.includes('Carrier Earned vs Employee Paid'), labels)
// "Management Watchdog" does match a content word ("working") in its own description, so it is a
// legitimate — weak — hit. What the defect was is that it OUTRANKED the thing actually asked about.
ck('A2 "Management Watchdog" no longer leads; the store and the person both beat it',
   labels.indexOf('Management Watchdog') > 1 || !labels.includes('Management Watchdog'), labels)
ck('A3 the store at that address IS the top hit', hitsBad[0]?.item.key === 'store:B-1115', labels)
ck('A4 …found by its ADDRESS, not its code', hitsBad[0]?.matched.includes('nostrand'), hitsBad[0])
ck('A5 the digit token did the work too', hitsBad[0]?.matched.includes('509'), hitsBad[0])
ck('A6 no hit is accounted for by a stopword',
   hitsBad.every(h => h.matched.every(t => !S.STOPWORDS.has(t))), hitsBad)
ck('A7 it is recognised as a QUESTION', S.looksLikeQuestion(BAD))
ck('A8 and routes to the assistant, because no hit explains the whole thing',
   S.intent(BAD, hitsBad) === 'ask', S.intent(BAD, hitsBad))
ck('A9 the person at that store is offered too (the question is about people)',
   labels.includes('Abid Khan'), labels)

// NEGATIVE CONTROL — §A must be the rule working, not the ranker having been made to refuse things.
const hitsCarrier = S.rank(CATALOGUE, 'carrier earned vs employee paid')
ck('A10-ARMED asked for that report BY NAME, it is the top hit',
   hitsCarrier[0]?.item.label === 'Carrier Earned vs Employee Paid', hitsCarrier.map(h => h.item.label))
ck('A11-ARMED …and that is a navigation, not a question',
   S.intent('carrier earned vs employee paid', hitsCarrier) === 'navigate')
const hitsSched = S.rank(CATALOGUE, 'who is working today')
ck('A12 "who is working today" finds the Schedule page — "working" reaches "work"/"scheduled"',
   hitsSched.some(h => h.item.label === 'Schedule'), hitsSched.map(h => h.item.label))

section('A2. identity vs borrowed context, and shared stems')
const PLACE = [
  { key: 'store:B-1115', kind: 'store', href: '/s', label: 'B-1115', subtitle: '509 Nostrand Ave' },
  { key: 'person:abid', kind: 'person', href: '/p', label: 'Abid Khan', context: 'B-1115 · 509 Nostrand Ave' },
]
const place = S.rank(PLACE, '509 nostrand')
ck('A2a the STORE at an address outranks the person who works there',
   place[0]?.item.kind === 'store', place)
ck('A2b …and the person is still offered, not hidden', place.length === 2, place)
ck('A2c-ARMED the distinction is the `context` field doing it, not the alphabet: moved to `subtitle`, '
   + 'the person (alphabetically first) wins',
   S.rank([PLACE[0], { ...PLACE[1], subtitle: PLACE[1].context, context: undefined }],
          '509 nostrand')[0]?.item.kind === 'person')
// shared stems: long enough to be the same word, never short enough to be a different one
const stem = (label, q) => S.rank([{ key: 'k', kind: 'page', href: '/x', label }], q).length > 0
ck('A2d "working" reaches "work"', stem('Work schedule', 'working'))
ck('A2e "scheduled" reaches "schedule"', stem('Schedule', 'scheduled'))
// The loose fallback is a WORD PREFIX, not a substring — so a half-typed name works…
ck('A2f "pay" finds Payroll (a prefix is what half a typed name means)', stem('Payroll', 'pay'))
ck('A2f2-ARMED …but a token never matches the MIDDLE of a word: "ate" is not Estimate',
   !stem('Estimate', 'ate'))
ck('A2g-ARMED "in" does NOT reach "inventory"', !stem('Inventory', 'in'))
ck('A2h "50" narrows to 509 Nostrand (a digit prefix)', stem('509 Nostrand Ave', '50'))
ck('A2h2-ARMED but "509" does NOT match the store at 1509 — the defect a substring test would cause',
   !stem('1509 Flatbush Ave', '509'))
ck('A2i an exact word still beats a stem',
   S.rank([{ key: 'a', kind: 'page', href: '/a', label: 'Working' },
           { key: 'b', kind: 'page', href: '/b', label: 'Work' }], 'working')[0]?.item.key === 'a')

section('B. tokens — a number is a word')
ck('B1 digits of any length survive', S.tokens('509 b 1115 x').includes('509'))
ck('B2 a single digit survives', S.tokens('store 5').includes('5'))
ck('B3 a single LETTER does not', !S.tokens('store b').includes('b'))
ck('B4 punctuation and case are gone',
   JSON.stringify(S.tokens('B-1115, Nostrand!')) === JSON.stringify(['b', '1115', 'nostrand'])
   || JSON.stringify(S.tokens('B-1115, Nostrand!')) === JSON.stringify(['1115', 'nostrand']),
   S.tokens('B-1115, Nostrand!'))
ck('B5 an empty or junk query yields nothing', S.tokens('').length === 0 && S.tokens('  ?? ').length === 0)

section('C. stopwords rank nothing')
ck('C1 contentTokens drops them',
   JSON.stringify(S.contentTokens(BAD)) === JSON.stringify(['working', '509', 'nostrand']),
   S.contentTokens(BAD))
for (const q of ['who is it', 'what is the', 'how much', 'can i see this']) {
  ck(`C2 a pure-stopword query ranks NOTHING: ${JSON.stringify(q)}`, S.rank(CATALOGUE, q).length === 0)
  ck(`C3 …and is reported as empty, not as a bad search: ${JSON.stringify(q)}`,
     S.intent(q, S.rank(CATALOGUE, q)) === 'empty')
}
ck('C4-ARMED the SAME query with one content word does rank',
   S.rank(CATALOGUE, 'who is on the schedule').length > 0)

section('D. field order')
const one = (label, extra) => [{ key: 'k', kind: 'report', href: '/x', label, ...extra }]
const sc = (label, extra, q) => S.rank(one(label, extra), q)[0]?.score ?? 0
const exact = sc('schedule', {}, 'schedule')
const prefix = sc('schedule report', {}, 'schedule')
const word = sc('the schedule report', {}, 'schedule')
const sub = sc('B-1115', { subtitle: 'schedule' }, 'schedule')
const alias = sc('B-1115', { aliases: ['schedule'] }, 'schedule')
const cat = sc('B-1115', { category: 'schedule' }, 'schedule')
const desc = sc('B-1115', { desc: 'schedule' }, 'schedule')
ck('D1 label exact > label prefix', exact > prefix, { exact, prefix })
ck('D2 label prefix > label word', prefix > word, { prefix, word })
ck('D3 label word > subtitle and alias', word > sub && word > alias, { word, sub, alias })
ck('D4 subtitle and alias > category', sub > cat && alias > cat, { sub, alias, cat })
ck('D5 category > description', cat > desc, { cat, desc })
ck('D6 description still counts for something', desc > 0, { desc })
ck('D7 an unmatched item scores nothing at all', S.rank(one('nothing here'), 'schedule').length === 0)

section('E. coverage beats loudness')
const COV = [
  { key: 'loud', kind: 'report', href: '/a', label: 'Sales' },                      // exact on one token
  { key: 'both', kind: 'report', href: '/b', label: 'Sales by store', desc: 'x' },  // both tokens
]
const covHits = S.rank(COV, 'sales store')
ck('E1 the hit explaining BOTH words wins over an exact match on one',
   covHits[0]?.item.key === 'both', covHits)
ck('E2 and coverage is reported honestly',
   covHits[0]?.matched.length === 2 && covHits[1]?.matched.length === 1, covHits)
ck('E3 fullyCovered sees it', S.fullyCovered(covHits, 'sales store'))
ck('E4 …and does not see a partial-only result',
   !S.fullyCovered(S.rank([COV[0]], 'sales store'), 'sales store'))

section('F. looksLikeQuestion')
for (const q of ['who is working in 509 nostrand', 'what is my commission', 'how do i add a store',
                 'which store is best?', 'sales report?', 'can i see payroll',
                 'is 509 nostrand open', 'who pulled my numbers down today']) {
  ck(`F1 a question: ${JSON.stringify(q)}`, S.looksLikeQuestion(q), q)
}
for (const q of ['sales report', 'carrier earned vs employee paid', 'roles', 'B-1115',
                 '509 nostrand', 'schedule', 'p&l', 'commission statement']) {
  ck(`F2 NOT a question: ${JSON.stringify(q)}`, !S.looksLikeQuestion(q), q)
}
ck('F3 nothing typed is not a question', !S.looksLikeQuestion('') && !S.looksLikeQuestion('   '))

section('G. intent')
ck('G1 empty on nothing typed', S.intent('', []) === 'empty')
ck('G2 empty on stopwords only', S.intent('who is it', []) === 'empty')
ck('G3 ask when a question is unexplained', S.intent(BAD, hitsBad) === 'ask')
ck('G4 ask when nothing was found at all', S.intent('zzzz quux', []) === 'ask')
ck('G5 navigate when a name was typed', S.intent('sales report', S.rank(CATALOGUE, 'sales report')) === 'navigate')
// The asymmetry, stated in the module and asserted here: a question whose words ARE a page's name
// opens the page. "Schedule" literally answers "who is working" only if the label says so.
const NAMED = [{ key: 'k', kind: 'report', href: '/x', label: 'Who is working today' }]
ck('G6 a question a result fully explains NAVIGATES rather than asking',
   S.intent('who is working today', S.rank(NAMED, 'who is working today')) === 'navigate',
   S.rank(NAMED, 'who is working today'))

section('H. determinism and purity')
const before = JSON.stringify(CATALOGUE)
const a = S.rank(CATALOGUE, BAD)
const b = S.rank([...CATALOGUE].reverse(), BAD)
ck('H1 the order does not depend on the catalogue order',
   JSON.stringify(a.map(h => h.item.key)) === JSON.stringify(b.map(h => h.item.key)),
   [a.map(h => h.item.key), b.map(h => h.item.key)])
ck('H2 ranking mutates nothing', JSON.stringify(CATALOGUE) === before)
ck('H3 the same query twice gives the same answer',
   JSON.stringify(S.rank(CATALOGUE, BAD)) === JSON.stringify(a))
ck('H4 the limit is honoured', S.rank(CATALOGUE, 'store', 2).length <= 2)
ck('H5 a zero limit returns nothing', S.rank(CATALOGUE, 'store', 0).length === 0)
ck('H6 a null-ish catalogue does not throw', S.rank([], 'x').length === 0)

// ── §I — askDoor(): which assistant answers an unexplained question ──────────────────────────────
// The reported case, by name. "someone wanted a password reset" belongs to the how-to door; routing
// it to the data door is how it got no answer at all.
section('§I  askDoor(): the how-to door for doing, the data door for figures')
{
  const howto = [
    'someone wanted a password reset',
    'how do i reset a password',
    'reset password for abid',
    'how do i add a store',
    'where is the closing deadline set',
    'can i turn on the digest',
    'how do i invite a new user',
    'change my password',
    'how do i upload the commission ledger',
  ]
  for (const q of howto) ck(`I1  howto: "${q}"`, S.askDoor(q) === 'howto', S.askDoor(q))
  const data = [
    'which store was best last month',
    'how much revenue did we make',
    'how many activations in august',
    'who is my best sales person',
    'what is my commission',
    'total payout for september',
    'who is pulling me down',
  ]
  for (const q of data) ck(`I2  data: "${q}"`, S.askDoor(q) === 'data', S.askDoor(q))
  // The deliberate exception: "how much" / "how many" open with "how" and are still quantities.
  ck('I3  "how much" is a quantity, not a how-to', S.askDoor('how much did store b-1115 make') === 'data')
  ck('I4  "how many" likewise', S.askDoor('how many phones did we sell') === 'data')
  // ARMED: a quantity word beats a doing word, because a misroute to the data door still returns a
  // number while a misroute to the how-to door returns nothing.
  ck('I5  ARMED — a quantity word wins over a doing verb',
     S.askDoor('how much did we add in revenue') === 'data', S.askDoor('how much did we add in revenue'))
  ck('I6  the safe default for a question with neither signal is the data door',
     S.askDoor('nostrand august') === 'data')
  ck('I7  empty is the safe default too', S.askDoor('') === 'data')
  ck('I8  it is pure — same answer twice', S.askDoor('reset a password') === S.askDoor('reset a password'))
}

// ── §J — submitAction(): a guess is never delivered as an answer ─────────────────────────────────
// THE SECOND REPORT, by name. Asked "which sales rep worked in 509 today" the console judged the
// question unexplained (intent -> 'ask'), found the assistant not switched on for the tenant, and
// then navigated to the Sales Report anyway; "who worked in 509 today" landed on Pay period &
// work-week the same way. intent() was right both times — the fall-through in the surface was the
// defect. An unexplained question with no reachable assistant is a "no", not a navigation.
section('§J  submitAction(): an unexplained question never navigates to the closest-looking page')
{
  const REPORTED = ['which sales rep worked in 509 today', 'who worked in 509 today']
  for (const q of REPORTED) {
    const hits = S.rank(CATALOGUE, q, 8)
    const mode = S.intent(q, hits)
    ck(`J1  "${q}" still reads as an unexplained question`, mode === 'ask', mode)
    // There IS a destination (that is how it went wrong), and it is still not taken.
    const hasDest = hits.some(h => h.item.href)
    ck(`J2  "${q}" does have a closest-looking destination`, hasDest)
    ck(`J3  "${q}" with the assistant OFF does not navigate`,
       S.submitAction(mode, { canAsk: false, hasDest }) === 'unanswered',
       S.submitAction(mode, { canAsk: false, hasDest }))
    ck(`J4  "${q}" with the assistant ON goes to the assistant`,
       S.submitAction(mode, { canAsk: true, hasDest }) === 'ask')
  }
  // The other outcomes are unchanged, so the fix narrows nothing it should not.
  ck('J5  a covered query still navigates',
     S.submitAction('navigate', { hasDest: true }) === 'navigate')
  ck('J6  a deterministic figure wins over everything, assistant or no assistant',
     S.submitAction('ask', { hasFigure: true, canAsk: false, hasDest: true }) === 'figure')
  ck('J7  an empty box does nothing', S.submitAction('empty', { hasDest: true }) === 'unanswered')
  ck('J8  navigate with nothing openable is honest, not a silent no-op',
     S.submitAction('navigate', { hasDest: false }) === 'unanswered')
  // ARMED: the rule is what makes J3 true, not the fixture. With the assistant reachable the same
  // inputs produce a different answer, so J3 cannot be passing because nothing ever navigates.
  ck('J9  ARMED — the same inputs navigate when the question IS explained',
     S.submitAction('navigate', { canAsk: false, hasDest: true }) === 'navigate')
  ck('J10 it is pure — same answer twice',
     S.submitAction('ask', { canAsk: false, hasDest: true }) === S.submitAction('ask', { canAsk: false, hasDest: true }))
}

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
console.log('OK — search ranks on meaning; an unexplained question goes to the assistant.')
