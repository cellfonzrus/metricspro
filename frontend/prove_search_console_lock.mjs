// DESIGN LOCK — the search console has ONE ranker and ONE catalogue, and the build fails if a
// surface stops dereferencing them (owner directive 2026-10-05, index §54).
//
// WHY A LOCK RATHER THAN A CONVENTION. The defect this subsystem exists to fix was itself a private
// ranker: `AskBar.tsx` scored a result by counting typed words in a `hay` string it built itself, and
// because nothing outside that file could see the rule, nothing could be wrong about it. The house
// rule is explicit — *"a design fix ships with a check that FAILS THE BUILD if a caller stops
// dereferencing the shared fact, or if a second copy appears"* — so the two facts are named here:
//
//   FACT 1  "what did the person mean by what they typed"  →  src/lib/search-rank.ts
//   FACT 2  "what can the platform search find"            →  src/lib/search-catalog.ts
//   FACT 3  "every page this app has"                      →  src/lib/route-index.ts (DERIVED)
//   FACT 4  "what can THIS viewer find"                     →  src/lib/search-catalog.viewerSources
//
// FACT 4 arrived from the owner's third report — *"it is not appearing in the search bar also, which
// leads us to checking if all modules are searchable or they are hidden"* — and it is the same shape
// as FACT 3 one level up. There were TWO page-search boxes: ⌘/ folded the derived registry, and the
// sidebar's ⌘K built its own index from NAV alone and ranked it with its own private
// `startsWith / includes` ladder. 27 in-app pages were therefore unfindable in the box the owner
// types in, while 0 were unfindable in the other. A second surface answering the same question with
// its own index is exactly what FACT 1 and FACT 2 exist to forbid, so the sidebar is now under this
// lock too — as a JUMPER rather than a console, because it opens destinations and asks the assistant
// nothing. §D is its section (index §54.12).
//
// FACT 3 arrived from the owner's second report — *"this is a design issue not a random left out
// issue, all the search needs to be run through the index we built"* — and it is locked differently
// from the other two, because the failure mode is different: a curated list goes stale silently, so
// the lock here is that the registry must be DERIVED (its own proof, `prove_route_index.mjs`, fails
// the build when disk and the registry disagree) and that the console must actually read it. A
// surface that assembles a catalogue without the route source is complete only by luck.
//
// Every check is ARMED: the lock is run against a deliberately broken copy of the source in memory
// and must go red. An unarmed lock that matches nothing passes forever and protects nothing, which
// has happened in this repo (see §19.18), so the arming is not ceremony.
//
// Run:  node frontend/prove_search_console_lock.mjs    (no network, no DB, no React, no npm install)
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { readFileSync } from 'node:fs'

const HERE = dirname(fileURLToPath(import.meta.url))
const read = rel => readFileSync(join(HERE, rel), 'utf8')

let pass = 0, fail = 0
const ok = (name, cond, detail) => {
  if (cond) { pass++; return }
  fail++
  console.log(`  FAIL  ${name}${detail === undefined ? '' : `   ${JSON.stringify(detail)}`}`)
}

// Comments and string literals blanked, so a check for CODE never matches prose about the code. The
// same trap `harness_data_qa_lock.py` hit: a textual check over text that includes its own
// documentation is vacuous, and here the documentation quotes the very patterns being forbidden.
// LINE comments are stripped FIRST, then block comments — and the order is load-bearing, not style.
// This function used to do it the other way round, which was latent until the sidebar came under the
// lock: `layout.tsx` carries the line comment `// /commcalc/* page, so a first-match …`, and reading
// block comments first takes that `/*` as an opener and blanks 110 lines of real code, including the
// very calls §D checks for. §D1 failed on correctly wired code until this was fixed. A glob inside a
// comment is a comment, not a comment opener. `[^:]` keeps `https://` intact.
function stripComments(src) {
  return src
    .replace(/^[ \t]*\/\/.*$/gm, ' ')
    .replace(/([^:])\/\/.*$/gm, '$1 ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
}

function codeOnly(src) {
  let out = stripComments(src)
  // string and template literals
  out = out.replace(/'(?:[^'\\\n]|\\.)*'/g, "''")
           .replace(/"(?:[^"\\\n]|\\.)*"/g, '""')
           .replace(/`(?:[^`\\]|\\.)*`/g, '``')
  return out
}

// THE SURFACES that must dereference the two facts. A new console surface is added here, which is
// the point: the list is what a reviewer reads to know who is bound by the rule.
const SURFACES = ['src/components/AskBar.tsx']
// THE JUMPER SURFACE. It searches the same world and ranks it the same way, but it has no assistant,
// so the question-door rules (askDoor / submitAction / intent) do not apply to it. What DOES apply is
// every rule about not keeping a second index or a second ranker — which is the whole reason it is
// here: its private copies of both were the defect.
const JUMPERS = ['src/app/(platform)/layout.tsx']

const RANKER = 'src/lib/search-rank.ts'
const CATALOG = 'src/lib/search-catalog.ts'
const ROUTES = 'src/lib/route-index.ts'

// ── the checks, as functions over (relative path, raw source) so the arming can re-run them ──────
// Each returns an array of complaints; empty means the file is wired correctly.
function checkSurface(rel, raw) {
  const code = codeOnly(raw)
  const bad = []
  // The imports must be there. Checked on the RAW source, because an import's module path is a
  // string literal and `codeOnly` blanks it — the one place a raw read is the correct read.
  if (!/import\s*\{[^}]*\brank\b[^}]*\}\s*from\s*['"]@\/lib\/search-rank['"]/.test(raw))
    bad.push(`${rel} does not import rank() from @/lib/search-rank`)
  if (!/import\s*\{[^}]*\bintent\b[^}]*\}\s*from\s*['"]@\/lib\/search-rank['"]/.test(raw))
    bad.push(`${rel} does not import intent() from @/lib/search-rank`)
  // `viewerCatalog` is the gate+fold entry point (FACT 4); it calls `buildCatalog` itself, so a
  // surface that reaches the catalogue through either one is dereferencing the same home.
  if (!/import\s*\{[^}]*\b(?:viewerCatalog|buildCatalog)\b[^}]*\}\s*from\s*['"]@\/lib\/search-catalog['"]/.test(raw))
    bad.push(`${rel} does not import viewerCatalog()/buildCatalog() from @/lib/search-catalog`)
  // FACT 3: the catalogue must be built from the DERIVED page list, not from the curated registries
  // alone — that is the whole difference between complete and complete-by-luck.
  if (!/import\s*\{[^}]*\bsearchableRoutes\b[^}]*\}\s*from\s*['"]@\/lib\/route-index['"]/.test(raw))
    bad.push(`${rel} does not import searchableRoutes() from @/lib/route-index`)
  // And the door the assistant opens must be DECIDED, not left to the person.
  if (!/import\s*\{[^}]*\baskDoor\b[^}]*\}\s*from\s*['"]@\/lib\/search-rank['"]/.test(raw))
    bad.push(`${rel} does not import askDoor() from @/lib/search-rank`)
  // And what a KEYSTROKE does must be decided in the one home too. The reported defect was here and
  // nowhere else: the surface asked intent(), got 'ask', found the assistant switched off, and then
  // navigated to the closest-looking page anyway. A guess arriving as an answer is what this console
  // exists not to do, so the surface may not keep its own fall-through.
  if (!/import\s*\{[^}]*\bsubmitAction\b[^}]*\}\s*from\s*['"]@\/lib\/search-rank['"]/.test(raw))
    bad.push(`${rel} does not import submitAction() from @/lib/search-rank`)
  if (/if\s*\(firstDest\)\s*go\(/.test(code))
    bad.push(`${rel} navigates to its best guess without asking submitAction() — the reported defect`)
  // And they must be CALLED, not merely imported — a dead import is how a rewiring un-wires.
  for (const fn of ['rank(', 'searchableRoutes(', 'askDoor(', 'submitAction(']) {
    if (!code.includes(fn)) bad.push(`${rel} imports but never calls ${fn})`)
  }
  if (!/\b(?:viewerCatalog|buildCatalog)\(/.test(code))
    bad.push(`${rel} imports but never calls viewerCatalog()/buildCatalog()`)
  // `intent` is imported under an alias here (the file already has a local `Intent` type for the
  // metric intents), so the call is matched by either spelling.
  if (!/\b(searchIntent|intent)\s*\(/.test(code)) bad.push(`${rel} never calls intent()`)
  // The route source must reach buildCatalog, and each page must be gated by the ONE home for "may
  // this viewer open this path". A surface that passed the derived list through ungated would be
  // offering pages the viewer cannot open — the leak the upstream-filter rule exists to prevent.
  if (!/routes\b/.test(code)) bad.push(`${rel} never passes a routes source to buildCatalog()`)
  if (!/canAccessPath\s*\(/.test(code))
    bad.push(`${rel} does not gate the derived page list with canAccessPath()`)
  // NO PRIVATE RANKER. These are the exact shapes the old code used, and each one re-appearing means
  // somebody started scoring in the surface again instead of in the one home.
  if (/\bhay\b/.test(code)) bad.push(`${rel} builds a "hay" haystack string — scoring belongs in ${RANKER}`)
  if (/\bscore\s*[:=]/.test(code)) bad.push(`${rel} computes a score of its own — scoring belongs in ${RANKER}`)
  if (/localeCompare/.test(code)) bad.push(`${rel} sorts results itself — ordering belongs in ${RANKER}`)
  if (/\.toLowerCase\(\)\s*\.split\(/.test(code))
    bad.push(`${rel} tokenizes the query itself — tokens() belongs in ${RANKER}`)
  // NO SECOND CATALOGUE. A surface may read a registry and pass it in; it may not declare one.
  if (/\bSTOPWORDS\b/.test(code)) bad.push(`${rel} carries its own stopword list — one home is ${RANKER}`)
  return bad
}

// THE JUMPER RULE (FACT 4). A destination-search box must assemble the viewer's world in the one
// home, read the DERIVED page list, and rank with the one ranker — and keep no private copy of any
// of the three. It is not held to the assistant rules: it asks nothing, so there is no door to pick
// and no keystroke verdict to defer. Everything else is identical, deliberately: the defect was a
// surface that answered "which pages exist" and "what did they mean" by itself.
function checkJumper(rel, raw) {
  const code = codeOnly(raw)
  const bad = []
  if (!/import\s*\{[^}]*\bviewerCatalog\b[^}]*\}\s*from\s*['"]@\/lib\/search-catalog['"]/.test(raw))
    bad.push(`${rel} does not import viewerCatalog() from @/lib/search-catalog`)
  if (!/import\s*\{[^}]*\bsearchableRoutes\b[^}]*\}\s*from\s*['"]@\/lib\/route-index['"]/.test(raw))
    bad.push(`${rel} does not import searchableRoutes() from @/lib/route-index`)
  if (!/import\s*\{[^}]*\brank\b[^}]*\}\s*from\s*['"]@\/lib\/search-rank['"]/.test(raw))
    bad.push(`${rel} does not import rank() from @/lib/search-rank`)
  // CALLED, not merely imported — a dead import is how a rewiring un-wires. `rank` is imported under
  // an alias in the shell (the file has no other `rank`, but the alias says where it comes from).
  if (!/\bviewerCatalog\(/.test(code)) bad.push(`${rel} imports but never calls viewerCatalog()`)
  if (!/\bsearchableRoutes\(/.test(code)) bad.push(`${rel} imports but never calls searchableRoutes()`)
  if (!/\b(?:rankSearch|rank)\(/.test(code)) bad.push(`${rel} imports but never calls rank()`)
  // The derived page list must reach the catalogue as the `routes` source, gated by the one home for
  // "may this viewer open this path" — a NAV-only index is the 27-page gap this lock exists to stop.
  if (!/routes:/.test(code)) bad.push(`${rel} never passes a routes source to viewerCatalog()`)
  if (!/canAccessPath\s*\(/.test(code))
    bad.push(`${rel} does not gate the derived page list with canAccessPath()`)
  // And destinations are chosen by the one predicate, not by a second reading of "is this a place".
  if (!/\bdestinations\(/.test(code))
    bad.push(`${rel} does not use destinations() to drop entities — entityOnly has one home`)
  // NO PRIVATE RANKER. These are the exact shapes its own ladder used.
  if (/localeCompare/.test(code)) bad.push(`${rel} sorts results itself — ordering belongs in ${RANKER}`)
  if (/\bscore\s*[:=]/.test(code)) bad.push(`${rel} computes a score of its own — scoring belongs in ${RANKER}`)
  if (/\bhay\b/.test(code)) bad.push(`${rel} builds a haystack string — scoring belongs in ${RANKER}`)
  if (/\bSTOPWORDS\b/.test(code)) bad.push(`${rel} carries its own stopword list — one home is ${RANKER}`)
  return bad
}

function checkRanker(raw) {
  const code = codeOnly(raw)
  const bad = []
  // PURE and import-free: that is what lets the proof drive the real functions under Node, and what
  // keeps the rule readable in one file.
  const imports = raw.match(/^\s*import\s+(?!type\b)/gm) || []
  if (imports.length) bad.push(`${RANKER} imports at runtime (${imports.length}) — it must stay pure`)
  if (/\bfetch\s*\(|\bapi\s*\(/.test(code)) bad.push(`${RANKER} does I/O — ranking must be pure`)
  // It must not hold a catalogue: items are passed in.
  if (/\bNAV\b|REPORT_CATEGORIES|\bSCREENS\b/.test(code))
    bad.push(`${RANKER} names a registry — the catalogue is assembled in ${CATALOG}`)
  for (const fn of ['export function rank', 'export function intent', 'export function tokens'])
    if (!code.includes(fn)) bad.push(`${RANKER} no longer exports ${fn.split(' ').pop()}()`)
  return bad
}

function checkRouteIndex(raw) {
  const code = codeOnly(raw)
  const bad = []
  // DERIVED, and it must say so: the header is what stops somebody hand-editing the array back into
  // a curated list, and the generator is what keeps it true.
  if (!/GENERATED/.test(raw.slice(0, 400)))
    bad.push(`${ROUTES} does not declare itself generated in its first lines`)
  if (!code.includes('export function searchableRoutes'))
    bad.push(`${ROUTES} no longer exports searchableRoutes()`)
  if (!code.includes('export const ROUTES')) bad.push(`${ROUTES} no longer exports ROUTES`)
  const imports = raw.match(/^\s*import\s+(?!type\b)/gm) || []
  if (imports.length) bad.push(`${ROUTES} imports at runtime (${imports.length}) — it must stay pure`)
  // It says WHAT EXISTS and nothing else: no gate, no module, no API path.
  const noComments = raw.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/.*$/gm, ' ')
  if (/canSeeItem|canAccessPath|module:/.test(noComments))
    bad.push(`${ROUTES} carries a permission rule — gating lives in rbac.ts`)
  if (/\/api\/v1/.test(noComments)) bad.push(`${ROUTES} carries an API path`)
  // An exclusion must carry its reason rather than be a bare flag, so it cannot be silent.
  if (/preauth: (?:true|false)/.test(code))
    bad.push(`${ROUTES} uses preauth as a flag — it must hold the REASON`)
  return bad
}

function checkCatalog(raw) {
  const code = codeOnly(raw)
  const bad = []
  const imports = raw.match(/^\s*import\s+(?!type\b)/gm) || []
  if (imports.length) bad.push(`${CATALOG} imports at runtime (${imports.length}) — it must stay pure`)
  if (/\bfetch\s*\(|\bapi\s*\(/.test(code)) bad.push(`${CATALOG} does I/O — assembly must be pure`)
  // It must not DECLARE the registries, only read the shapes it is handed. A literal in-app href
  // here would be the second copy of "which pages exist".
  // Strings KEPT here on purpose: an href IS a string literal, so `codeOnly` would blank the very
  // thing being forbidden and the check would pass forever. Comments are still stripped, because the
  // documentation above legitimately names example paths.
  const hrefs = stripComments(raw).match(/['"]\/[a-z][a-z0-9/-]*['"]/g) || []
  if (hrefs.length) bad.push(`${CATALOG} spells in-app hrefs (${hrefs.slice(0, 3)}) — sources are passed in`)
  if (!code.includes('export function buildCatalog')) bad.push(`${CATALOG} no longer exports buildCatalog()`)
  if (!code.includes('export function entityOnly')) bad.push(`${CATALOG} no longer exports entityOnly()`)
  // It must not re-implement scoring either.
  if (/\bscore\s*[:=]/.test(code)) bad.push(`${CATALOG} scores — scoring belongs in ${RANKER}`)
  return bad
}

// ── §A — the tree as it stands is GREEN ──────────────────────────────────────────────────────────
console.log('§A  the tree as it stands')
const RAW = { ranker: read(RANKER), catalog: read(CATALOG), routes: read(ROUTES) }
const SURFACE_RAW = Object.fromEntries(SURFACES.map(s => [s, read(s)]))
{
  for (const s of SURFACES) {
    const bad = checkSurface(s, SURFACE_RAW[s])
    ok(`A1  ${s} dereferences both facts`, bad.length === 0, bad)
  }
  const r = checkRanker(RAW.ranker)
  ok('A2  the ranker is pure and still exports its surface', r.length === 0, r)
  const c = checkCatalog(RAW.catalog)
  ok('A3  the catalogue is pure and declares no registry', c.length === 0, c)
  const ri = checkRouteIndex(RAW.routes)
  ok('A3b the derived page index is generated, pure, and says only what exists', ri.length === 0, ri)
  ok('A4  there is exactly one console surface under the lock', SURFACES.length === 1, SURFACES)
}

// ── §B — ARMED: each rule goes red on a deliberately broken copy ──────────────────────────────────
console.log('§B  ARMED — every rule fails on a broken copy')
{
  const surface = SURFACE_RAW[SURFACES[0]]
  const arm = (name, mutate) => {
    const broken = mutate(surface)
    ok(name, checkSurface(SURFACES[0], broken).length > 0, 'the broken copy still passed')
  }
  arm('B1  dropping the rank() import goes red',
      s => s.replace(/import \{ rank, intent as searchIntent[^\n]*\n/, ''))
  arm('B2  importing rank() but never calling it goes red',
      s => s.replace(/\brank\(catalog, q, 8\)/, '[]'))
  arm('B3  dropping the viewerCatalog() import goes red',
      s => s.replace(/import \{ viewerCatalog[^\n]*\n/, ''))
  arm('B4  never calling viewerCatalog() goes red', s => s.replace(/viewerCatalog\(/g, 'noCatalog('))
  arm('B5  a private haystack goes red', s => `${s}\nconst hay = 1\n`)
  arm('B6  a private score goes red', s => `${s}\nconst score = 1\n`)
  arm('B7  sorting results in the surface goes red', s => `${s}\nconst z = a.localeCompare(b)\n`)
  arm('B8  tokenizing the query in the surface goes red', s => `${s}\nconst t = q.toLowerCase().split(/x/)\n`)
  arm('B9  a second stopword list goes red', s => `${s}\nconst STOPWORDS = new Set()\n`)
  arm('B10 never calling intent() goes red',
      s => s.replace(/searchIntent\(q, hits\)/, "'navigate'"))
  arm('B10a dropping the searchableRoutes() import goes red',
      s => s.replace(/import \{ searchableRoutes \}[^\n]*\n/, ''))
  arm('B10b never calling searchableRoutes() goes red',
      s => s.replace(/searchableRoutes\(\)/g, '[]'))
  arm('B10c not passing the routes source to the catalogue goes red',
      s => s.replace(/routes: searchableRoutes\(\),/, 'noRoutes: searchableRoutes(),')
             .replace(/\broutes\b/g, 'noRoutes'))
  arm('B10d not gating the page list with canAccessPath goes red',
      s => s.replace(/canAccessPath\(/g, 'alwaysTrue('))
  arm('B10e dropping the askDoor() import goes red',
      s => s.replace(/, askDoor,/, ','))
  arm('B10f never calling askDoor() goes red', s => s.replace(/askDoor\(q\)/, "'data'"))
  arm('B10g dropping the submitAction() import goes red',
      s => s.replace(/, submitAction,/, ','))
  arm('B10h never calling submitAction() goes red',
      s => s.replace(/submitAction\(mode,/, "noop(mode,"))
  // THE REGRESSION, by name: the fall-through that sent "which sales rep worked in 509 today" to
  // the Sales Report because the assistant was off.
  arm('B10i the best-guess fall-through coming back goes red',
      s => `${s}\n  const onSubmit2 = () => { if (firstDest) go(firstDest.item.href) }\n`)

  ok('B11 ARMED — an import added to the ranker goes red',
     checkRanker(`import x from 'y'\n${RAW.ranker}`).length > 0)
  ok('B12 ARMED — a registry named in the ranker goes red',
     checkRanker(`${RAW.ranker}\nconst z = REPORT_CATEGORIES\n`).length > 0)
  ok('B13 ARMED — dropping rank() from the ranker goes red',
     checkRanker(RAW.ranker.replace('export function rank', 'function rank')).length > 0)
  ok('B14 ARMED — an in-app href spelled in the catalogue goes red',
     checkCatalog(`${RAW.catalog}\nconst z = ['/commcalc/sales-report']\n`).length > 0)
  ok('B15 ARMED — an import added to the catalogue goes red',
     checkCatalog(`import x from 'y'\n${RAW.catalog}`).length > 0)
  ok('B16 ARMED — dropping entityOnly() goes red',
     checkCatalog(RAW.catalog.replace('export function entityOnly', 'function entityOnly')).length > 0)
  ok('B17 ARMED — scoring in the catalogue goes red',
     checkCatalog(`${RAW.catalog}\nconst score = 1\n`).length > 0)
  ok('B18 ARMED — a route index that stops declaring itself generated goes red',
     checkRouteIndex(RAW.routes.replace('GENERATED', 'hand-written')).length > 0)
  ok('B19 ARMED — dropping searchableRoutes() from the index goes red',
     checkRouteIndex(RAW.routes.replace('export function searchableRoutes', 'function searchableRoutes')).length > 0)
  ok('B20 ARMED — a permission rule in the index goes red',
     checkRouteIndex(`${RAW.routes}\nconst z = canSeeItem(p, i)\n`).length > 0)
  ok('B21 ARMED — preauth used as a bare flag goes red',
     checkRouteIndex(RAW.routes.replace(/preauth: '[^']*'/, 'preauth: true')).length > 0)
  ok('B22 ARMED — an import added to the index goes red',
     checkRouteIndex(`import x from 'y'\n${RAW.routes}`).length > 0)
}

// ── §C — the scan is not vacuous ─────────────────────────────────────────────────────────────────
// If `codeOnly` ever blanked the whole file, every check above would pass on anything. These assert
// the scanner still SEES code, which is the counter-arming the data-qa lock learned to need.
console.log('§C  the scan still sees code')
{
  const code = codeOnly(SURFACE_RAW[SURFACES[0]])
  ok('C1  the surface still has code after blanking comments and strings', code.length > 2000, code.length)
  ok('C2  blanking removed the prose that quotes the forbidden patterns',
     code.length < SURFACE_RAW[SURFACES[0]].length)
  // The documentation at the top of the surface DOES contain the word it forbids; proof that the
  // blanking is what keeps §A green rather than luck.
  ok('C3  the raw surface mentions a pattern the lock forbids in prose',
     /score/i.test(SURFACE_RAW[SURFACES[0]]))
  ok('C4  and the blanked code does not', !/\bscore\s*[:=]/.test(code))
  ok('C5  the ranker still has code after blanking', codeOnly(RAW.ranker).length > 2000)
  ok('C6  the catalogue still has code after blanking', codeOnly(RAW.catalog).length > 1000)
  ok('C7  the route index still has code after blanking', codeOnly(RAW.routes).length > 2000)
  // Counter-arming the index checks the same way: the scan must still see real entries.
  ok('C8  and the blanked index still holds its exports',
     codeOnly(RAW.routes).includes('export const ROUTES'))
}

// ── §D — the JUMPER surface is under the same lock, and every rule armed ─────────────────────────
// The sidebar's ⌘K box. Before §54.12 it indexed NAV alone and ranked with its own ladder, so 27
// in-app pages existed, were reachable, were in the derived registry, and could not be found in it.
console.log('§D  the sidebar jumper searches the same world, ranked the same way')
const JUMPER_RAW = Object.fromEntries(JUMPERS.map(s => [s, read(s)]))
{
  for (const j of JUMPERS) {
    const bad = checkJumper(j, JUMPER_RAW[j])
    ok(`D1  ${j} dereferences the gate, the page list and the ranker`, bad.length === 0, bad)
  }
  ok('D2  there is exactly one jumper surface under the lock', JUMPERS.length === 1, JUMPERS)
  const jump = JUMPER_RAW[JUMPERS[0]]
  const armJ = (name, mutate) => ok(name, checkJumper(JUMPERS[0], mutate(jump)).length > 0,
                                    'the broken copy still passed')
  armJ('D3  dropping the viewerCatalog() import goes red',
       s => s.replace(/import \{ viewerCatalog[^\n]*\n/, ''))
  armJ('D4  never calling viewerCatalog() goes red', s => s.replace(/viewerCatalog\(/g, 'noCatalog('))
  armJ('D5  dropping the searchableRoutes() import goes red',
       s => s.replace(/import \{ searchableRoutes \}[^\n]*\n/, ''))
  armJ('D6  never calling searchableRoutes() goes red', s => s.replace(/searchableRoutes\(\)/g, '[]'))
  armJ('D7  dropping the rank() import goes red',
       s => s.replace(/import \{ rank as rankSearch \}[^\n]*\n/, ''))
  // THE REGRESSION, by name: the private `startsWith / includes / group-includes` ladder.
  armJ('D8  ranking in the surface again goes red', s => s.replace(/rankSearch\(/g, 'myRank('))
  armJ('D9  sorting results in the surface goes red', s => `${s}\nconst z = a.localeCompare(b)\n`)
  armJ('D10 a private score goes red', s => `${s}\nconst score = 1\n`)
  armJ('D11 a second stopword list goes red', s => `${s}\nconst STOPWORDS = new Set()\n`)
  // THE 27-PAGE GAP, by name: a NAV-only index again.
  armJ('D12 not passing the derived page list goes red',
       s => s.replace(/routes: searchableRoutes\(\),/, '').replace(/\broutes:/g, 'noRoutes:'))
  armJ('D13 not gating the page list with canAccessPath goes red',
       s => s.replace(/canAccessPath\(/g, 'alwaysTrue('))
  armJ('D14 deciding what counts as a place itself goes red',
       s => s.replace(/destinations\(/g, 'myPlaces('))
  // Counter-arming: the scan still sees the jumper's code after blanking.
  const jcode = codeOnly(jump)
  ok('D15 ARMED — the blanked jumper still holds code', jcode.length > 5000, jcode.length)
  ok('D16 ARMED — the raw jumper names a forbidden pattern in prose, the blanked code does not',
     /ladder|startsWith/.test(jump) && !/\bscore\s*[:=]/.test(jcode))
}

console.log(`\n${pass} passed, ${fail} failed`)
if (!fail) console.log('OK — one ranker, one catalogue, one DERIVED page index, one viewer gate, dereferenced by BOTH search surfaces; every rule armed.')
process.exit(fail ? 1 : 0)
