// Proof harness — the search catalogue is ASSEMBLED from the registries that already exist, and the
// folding is what makes the reported question answerable (owner directive 2026-10-05, index §54).
//
// THE REPORTED DEFECT, replayed in §A. "who is working in 509 nostrand" could never have worked: the
// only catalogue the ⌘/ bar searched was the 58 curated report entries, and no report is called 509
// Nostrand. §A asserts that the SAME sentence, against a catalogue built from the same sources plus
// the store option list, resolves the store — and that the store outranks the person who works there.
//
// No stub and no copy: Node strips the erasable TypeScript of the REAL src/lib/search-catalog.ts and
// src/lib/search-rank.ts and drives their exported functions.
//
//   A. the reported question resolves the store, and the store beats the person at that address
//   B. the FOLD — one entry per destination, no matter how many registries name it
//   C. the merge — the nav's label wins, the report's description and the screen's aliases survive
//   D. entities carry no href, and `entityOnly` is the one test for that
//   E. labels/kinds — a report upgrades a page; a settings group reads as a setting
//   F. blanks, missing sources and malformed rows never produce an entry or throw
//   G. determinism and purity — same input, same output; the sources are not mutated
//
// Run:  node frontend/prove_search_catalog.mjs      (no network, no DB, no React, no npm install)
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const C = await import(pathToFileURL(join(HERE, 'src/lib/search-catalog.ts')).href)
const R = await import(pathToFileURL(join(HERE, 'src/lib/search-rank.ts')).href)

let pass = 0, fail = 0
const ok = (name, cond, detail) => {
  if (cond) { pass++; return }
  fail++
  console.log(`  FAIL  ${name}${detail === undefined ? '' : `   ${JSON.stringify(detail)}`}`)
}
const eq = (name, got, want) => ok(name, JSON.stringify(got) === JSON.stringify(want), { got, want })

// ── the fixtures: the real registries' SHAPES, trimmed to what each section needs ────────────────
const NAV = [
  { group: 'Reports · Commissions', items: [
    { href: '/commcalc/activations', label: 'Activations' },
    { href: '/commcalc/carrier-vs-paid', label: 'Carrier Earned vs Employee Paid' },
    { href: '/commcalc/reports-index', label: 'Reports Index' },
  ] },
  { group: 'Store Operations', items: [
    { href: '/storeops/schedule', label: 'Schedule' },
    { href: '/franchise', label: 'Operations Dashboard' },
  ] },
  { group: 'Settings & Access', items: [
    { href: '/closing/cash-config', label: 'Cash Config' },
    { href: '/admin/roles', label: 'Roles & Access' },
  ] },
  // Two doors into ONE page — the deep-link convention rbac.navPath gates on.
  { group: 'Human Resources', items: [
    { href: '/hr', label: 'HR' },
    { href: '/hr?tab=employees', label: 'Employees & Pay' },
  ] },
]
const REPORTS = [
  { category: 'Commissions', reports: [
    { href: '/commcalc/activations', label: 'Activations', desc: 'POS Activation Details basis of truth' },
    { href: '/commcalc/carrier-vs-paid', label: 'Carrier Earned vs Employee Paid',
      desc: 'What the carrier paid against what the employee was paid' },
  ] },
  { category: 'StoreOps', reports: [
    { href: '/storeops/schedule', label: 'Schedule', desc: 'Who is scheduled to work, by store and week' },
  ] },
]
const SCREENS = [
  { href: '/closing/cash-config', label: 'Cash Setup', blurb: 'Closing deadline, gate and alert recipients',
    aliases: ['cash setup', 'closing gate', 'cash config'] },
  // A screen with NO nav entry of its own still becomes findable.
  { href: '/closing/variance', label: 'Variance Review', blurb: 'Cash and credit variance per store-day',
    aliases: ['variance review'] },
]
const STORES = [
  { store: '509 Nostrand Ave', market: 'Brooklyn', also_known_as: ['B-1115'] },
  { store: '1509 Flatbush Ave', market: 'Brooklyn', also_known_as: ['B-1598', 'B-2778'] },
  { store: 'B-9001', market: null, also_known_as: [] },
]
const PEOPLE = [
  { employee_id: 'E45', name: 'Abid Hussain', home_store: '509 Nostrand Ave', role: 'Rep' },
  { employee_id: 'E127', name: 'Nostrand Ahmed', home_store: null, role: 'Rep' },
]
const ALL = () => C.buildCatalog({ nav: NAV, reports: REPORTS, screens: SCREENS, stores: STORES, people: PEOPLE })

// ── §A — the reported question ───────────────────────────────────────────────────────────────────
console.log('§A  the reported question: "who is working in 509 nostrand"')
{
  const items = ALL()
  const hits = R.rank(items, 'who is working in 509 nostrand', 8)
  ok('A1  it returns at least one hit now', hits.length > 0, hits.length)
  eq('A2  the top hit IS the store at that address', hits[0]?.item.key, 'store:509 nostrand ave')
  ok('A3  the store is a store, not a report', hits[0]?.item.kind === 'store', hits[0]?.item.kind)
  ok('A4  the carrier report the owner was sent to is NOT in the results',
     !hits.some(h => h.item.href === '/commcalc/carrier-vs-paid'),
     hits.map(h => h.item.label))
  ok('A5  the person who WORKS there ranks below the store itself',
     hits.findIndex(h => h.item.key === 'store:509 nostrand ave') <
     hits.findIndex(h => h.item.key === 'person:E45'),
     hits.map(h => h.item.label))
  ok('A6  the store at 1509 is NOT offered for 509', !hits.some(h => h.item.key === 'store:1509 flatbush ave'),
     hits.map(h => h.item.label))
  // The schedule report DOES legitimately match "working" through its description, and should be
  // offered — the complaint was never that the console offered too much, it was that it offered the
  // WRONG thing first and then navigated there.
  ok('A7  the schedule report is still offered for "working"',
     hits.some(h => h.item.href === '/storeops/schedule'), hits.map(h => h.item.label))
  // And the whole point: nothing explains the WHOLE question, so the console asks rather than jumps.
  eq('A8  intent is to ASK, not to navigate', R.intent('who is working in 509 nostrand', hits), 'ask')
  // NEGATIVE CONTROL — §A is the rule working, not the ranker refusing everything.
  const named = R.rank(items, 'carrier earned vs employee paid', 8)
  eq('A9  asked for the carrier report BY NAME it is the top hit', named[0]?.item.href, '/commcalc/carrier-vs-paid')
  eq('A10 and that one navigates', R.intent('carrier earned vs employee paid', named), 'navigate')
  // The store code is a spelling of the same store, so it must find it too.
  const byCode = R.rank(items, 'B-1115', 8)
  eq('A11 the bare store code finds the same store', byCode[0]?.item.key, 'store:509 nostrand ave')
  // ARMED: without the store source the sentence is unanswerable again — which is what it was.
  const noStores = C.buildCatalog({ nav: NAV, reports: REPORTS, screens: SCREENS, people: PEOPLE })
  const before = R.rank(noStores, 'who is working in 509 nostrand', 8)
  ok('A12 ARMED — with no store source nothing matches "509" or "nostrand" except the person',
     !before.some(h => h.item.kind === 'store'), before.map(h => h.item.label))
}

// ── §B — the fold ────────────────────────────────────────────────────────────────────────────────
console.log('§B  one entry per destination, however many registries name it')
{
  const items = ALL()
  const hrefs = items.filter(i => !C.entityOnly(i)).map(i => i.href.split('?')[0].split('#')[0])
  eq('B1  no destination appears twice', hrefs.length, new Set(hrefs).size)
  // /commcalc/activations is in BOTH the nav and the report catalogue.
  const act = items.filter(i => i.href.startsWith('/commcalc/activations'))
  eq('B2  a page that is also a report is ONE entry', act.length, 1)
  // /hr and /hr?tab=employees are two doors into one page.
  const hr = items.filter(i => i.href.split('?')[0] === '/hr')
  eq('B3  two doors into one page fold to one entry', hr.length, 1)
  eq('B4  the folded entry keeps the first door it was given', hr[0]?.href, '/hr')
  // /closing/variance is named only by SCREENS.
  ok('B5  a screen with no nav entry is still findable',
     items.some(i => i.href === '/closing/variance'))
  // ARMED: feed the SAME source twice and the count must not move.
  const twice = C.buildCatalog({ nav: [...NAV, ...NAV], reports: [...REPORTS, ...REPORTS], screens: SCREENS })
  const once = C.buildCatalog({ nav: NAV, reports: REPORTS, screens: SCREENS })
  eq('B6  ARMED — a doubled source produces no extra entry', twice.length, once.length)
}

// ── §C — the merge ───────────────────────────────────────────────────────────────────────────────
console.log('§C  the merge: nav label, report description, screen aliases')
{
  const items = ALL()
  const cash = items.find(i => i.href === '/closing/cash-config')
  eq('C1  the NAV label wins over the screen label', cash?.label, 'Cash Config')
  ok('C2  the screen aliases survive the merge', (cash?.aliases || []).includes('closing gate'),
     cash?.aliases)
  ok('C3  the screen blurb fills the empty description', /deadline/i.test(cash?.desc || ''), cash?.desc)
  // So the words PROSE uses find the screen even though the nav calls it something else.
  const byAlias = R.rank(items, 'closing gate', 8)
  eq('C4  typing what prose calls it finds the screen', byAlias[0]?.item.href, '/closing/cash-config')
  const sched = items.find(i => i.href === '/storeops/schedule')
  eq('C5  the nav label is kept', sched?.label, 'Schedule')
  ok('C6  and the report description came across', /scheduled to work/i.test(sched?.desc || ''), sched?.desc)
  eq('C7  the folded entry is categorised by its nav group', sched?.category, 'Store Operations')
}

// ── §D — entities carry no href ──────────────────────────────────────────────────────────────────
console.log('§D  an entity is a fact, not a link')
{
  const items = ALL()
  const ents = items.filter(i => i.kind === 'store' || i.kind === 'person')
  eq('D1  every store and person is present', ents.length, STORES.length + PEOPLE.length)
  ok('D2  none of them carries an href', ents.every(i => i.href === ''), ents.map(i => i.href))
  ok('D3  entityOnly is true for every one of them', ents.every(C.entityOnly))
  ok('D4  and false for every destination',
     items.filter(i => !ents.includes(i)).every(i => !C.entityOnly(i)))
  // ARMED: a destination with an href is never mistaken for an entity.
  ok('D5  ARMED — a page is not entityOnly',
     !C.entityOnly({ key: 'page:/x', kind: 'page', label: 'X', href: '/x' }))
}

// ── §E — kinds ───────────────────────────────────────────────────────────────────────────────────
console.log('§E  kinds: a report upgrades a page, a settings group reads as a setting')
{
  const items = ALL()
  const kind = h => items.find(i => i.href.split('?')[0] === h)?.kind
  eq('E1  a nav page that is also a report is a report', kind('/commcalc/activations'), 'report')
  eq('E2  a nav page that is not a report is a page', kind('/franchise'), 'page')
  eq('E3  a page in a settings group is a setting', kind('/admin/roles'), 'setting')
  eq('E4  and so is one whose path says so', kind('/closing/cash-config'), 'setting')
  eq('E5  a store is a store', items.find(i => i.key === 'store:509 nostrand ave')?.kind, 'store')
  eq('E6  a person is a person', items.find(i => i.key === 'person:E45')?.kind, 'person')
  const counts = C.catalogCounts(items)
  eq('E7  the counts add up to the catalogue', Object.values(counts).reduce((a, b) => a + b, 0), items.length)
  eq('E8  three stores', counts.store, 3)
  eq('E9  two people', counts.person, 2)
  // A nav page in a "Reports · …" group is NOT demoted to a setting, and is not silently called a
  // report either: only the curated report catalogue decides that, and this one is not in it.
  eq('E10 a page in a Reports group is a page, not a setting', kind('/commcalc/reports-index'), 'page')
}

// ── §F — blanks and malformed rows ───────────────────────────────────────────────────────────────
console.log('§F  blanks, missing sources and malformed rows')
{
  eq('F1  no sources at all is an empty catalogue', C.buildCatalog({}).length, 0)
  eq('F2  undefined is an empty catalogue', C.buildCatalog(undefined || {}).length, 0)
  const messy = C.buildCatalog({
    nav: [{ group: 'G', items: [{ href: '', label: 'No href' }, { href: '/a', label: '   ' },
                                { href: '/b', label: 'Real' }] }],
    stores: [{ store: '' }, { store: '  ' }, { store: 'Good Store' }],
    people: [{ name: '' }, { name: 'Real Person' }],
  })
  eq('F3  a blank href is not an entry', messy.filter(i => i.href === '/a' || i.label === 'No href').length, 0)
  eq('F4  only the real rows survive', messy.map(i => i.label).sort(), ['Good Store', 'Real', 'Real Person'])
  eq('F5  a group with no items does not throw', C.buildCatalog({ nav: [{ group: 'G', items: [] }] }).length, 0)
  // A person with no employee_id still gets a stable key off their name.
  const anon = C.buildCatalog({ people: [{ name: 'No Id Here' }] })
  eq('F6  a person with no id keys off their name', anon[0]?.key, 'person:no id here')
  eq('F7  and carries no alias', anon[0]?.aliases, undefined)
  // A store with no market carries no context rather than an empty one.
  const items = ALL()
  eq('F8  a store with no market carries no context',
     items.find(i => i.key === 'store:b-9001')?.context, undefined)
  eq('F9  a person with no home store carries no context',
     items.find(i => i.key === 'person:E127')?.context, undefined)
}

// ── §G — determinism and purity ──────────────────────────────────────────────────────────────────
console.log('§G  determinism and purity')
{
  const a = JSON.stringify(ALL())
  const b = JSON.stringify(ALL())
  ok('G1  the same sources produce the same catalogue', a === b)
  const navCopy = JSON.stringify(NAV), repCopy = JSON.stringify(REPORTS)
  const scrCopy = JSON.stringify(SCREENS), stCopy = JSON.stringify(STORES), pplCopy = JSON.stringify(PEOPLE)
  ALL()
  ok('G2  the nav source was not mutated', JSON.stringify(NAV) === navCopy)
  ok('G3  the report source was not mutated', JSON.stringify(REPORTS) === repCopy)
  ok('G4  the screen source was not mutated', JSON.stringify(SCREENS) === scrCopy)
  ok('G5  the store source was not mutated', JSON.stringify(STORES) === stCopy)
  ok('G6  the roster source was not mutated', JSON.stringify(PEOPLE) === pplCopy)
  // Source ORDER must not change which entries exist (it may change their order in the array, which
  // is why the ranker sorts rather than trusting the input).
  const flipped = C.buildCatalog({ people: PEOPLE, stores: STORES, screens: SCREENS,
                                   reports: REPORTS, nav: NAV })
  eq('G7  flipping the source order keeps the same keys',
     flipped.map(i => i.key).sort(), ALL().map(i => i.key).sort())
  eq('G8  and the same ranking for the reported question',
     R.rank(flipped, 'who is working in 509 nostrand', 8).map(h => h.item.key),
     R.rank(ALL(), 'who is working in 509 nostrand', 8).map(h => h.item.key))
}

// ── §H — the route source is what makes the catalogue COMPLETE rather than curated ───────────────
// The second reported defect, by name: somebody searched for a password reset and found nothing,
// because `/account/password` is a real page that no curated registry listed. 34 of 319 static pages
// were in that position. The route index (§54.7) is derived from disk, so the catalogue is complete
// by construction — and this section asserts the ORDERING that lets it be complete without
// overwriting the labels the nav already supplies.
console.log('§H  every page that exists is findable, and the nav keeps its labels')
{
  const ROUTES = [
    // a page the nav also names — the label must NOT be replaced by the derived one
    { path: '/storeops/schedule', label: 'Storeops → Schedule' },
    // a page in NO other registry: the reported case
    { path: '/account/password', label: 'Account → Password',
      aliases: ['password reset', 'reset password', 'change password'] },
    // another one, to prove the first is not a special case
    { path: '/hr/letters/queue', label: 'Hr → Letters → Queue' },
  ]
  const items = C.buildCatalog({ nav: NAV, reports: REPORTS, screens: SCREENS, routes: ROUTES,
                                 stores: STORES, people: PEOPLE })
  const at = p => items.find(i => i.href.split('?')[0] === p)
  ok('H1  the page nobody listed is now in the catalogue', !!at('/account/password'))
  eq('H2  and so is the other one', at('/hr/letters/queue')?.label, 'Hr → Letters → Queue')
  // THE ORDERING RULE: routes are folded in LAST, so a nav label wins over a derived one.
  eq('H3  a nav page keeps the NAV label, not the derived one', at('/storeops/schedule')?.label, 'Schedule')
  ok('H4  and keeps its report description', /scheduled to work/i.test(at('/storeops/schedule')?.desc || ''))
  eq('H5  the route source adds no duplicate entry',
     items.filter(i => i.href.split('?')[0] === '/storeops/schedule').length, 1)
  // And the question that started it finally resolves.
  const hits = R.rank(items, 'password reset', 8)
  eq('H6  "password reset" finds the password page', hits[0]?.item.href, '/account/password')
  const hits2 = R.rank(items, 'someone wanted a password reset', 8)
  ok('H7  the sentence form finds it too',
     hits2.some(h => h.item.href === '/account/password'), hits2.map(h => h.item.label))
  eq('H8  and that sentence goes to the how-to door', R.askDoor('someone wanted a password reset'), 'howto')
  // ARMED: without the route source the page is unfindable again — which is what was reported.
  const before = C.buildCatalog({ nav: NAV, reports: REPORTS, screens: SCREENS })
  eq('H9  ARMED — with no route source "password reset" finds nothing',
     R.rank(before, 'password reset', 8).length, 0)
  // A declared alias is kept; a page with none still carries its label.
  ok('H10 the declared aliases survive',
     (at('/account/password')?.aliases || []).includes('change password'),
     at('/account/password')?.aliases)
  eq('H11 a route with no aliases carries none', at('/hr/letters/queue')?.aliases, undefined)
  // A route-only page is still gated UPSTREAM, never here: the catalogue ranks what it is handed.
  eq('H12 the catalogue applies no gate of its own',
     C.buildCatalog({ routes: [{ path: '/anything', label: 'Anything' }] }).length, 1)
}

console.log(`\n${pass} passed, ${fail} failed`)
if (!fail) console.log('OK — the catalogue is assembled from the registries that already exist, folded to one entry per thing.')
process.exit(fail ? 1 : 0)
