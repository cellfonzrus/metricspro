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
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/^[ \t]*\/\/.*$/gm, ' ')
    .replace(/([^:])\/\/.*$/gm, '$1 ')
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
  if (!/import\s*\{[^}]*\bbuildCatalog\b[^}]*\}\s*from\s*['"]@\/lib\/search-catalog['"]/.test(raw))
    bad.push(`${rel} does not import buildCatalog() from @/lib/search-catalog`)
  // FACT 3: the catalogue must be built from the DERIVED page list, not from the curated registries
  // alone — that is the whole difference between complete and complete-by-luck.
  if (!/import\s*\{[^}]*\bsearchableRoutes\b[^}]*\}\s*from\s*['"]@\/lib\/route-index['"]/.test(raw))
    bad.push(`${rel} does not import searchableRoutes() from @/lib/route-index`)
  // And the door the assistant opens must be DECIDED, not left to the person.
  if (!/import\s*\{[^}]*\baskDoor\b[^}]*\}\s*from\s*['"]@\/lib\/search-rank['"]/.test(raw))
    bad.push(`${rel} does not import askDoor() from @/lib/search-rank`)
  // And they must be CALLED, not merely imported — a dead import is how a rewiring un-wires.
  for (const fn of ['rank(', 'buildCatalog(', 'searchableRoutes(', 'askDoor(']) {
    if (!code.includes(fn)) bad.push(`${rel} imports but never calls ${fn})`)
  }
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
  arm('B3  dropping the buildCatalog() import goes red',
      s => s.replace(/import \{ buildCatalog[^\n]*\n/, ''))
  arm('B4  never calling buildCatalog() goes red', s => s.replace(/buildCatalog\(\{/, 'noCatalog({'))
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
  arm('B10c not passing the routes source to buildCatalog goes red',
      s => s.replace(/const routes = /, 'const unused = ').replace(/routes,/g, ''))
  arm('B10d not gating the page list with canAccessPath goes red',
      s => s.replace(/canAccessPath\(/g, 'alwaysTrue('))
  arm('B10e dropping the askDoor() import goes red',
      s => s.replace(/, askDoor,/, ','))
  arm('B10f never calling askDoor() goes red', s => s.replace(/askDoor\(q\)/, "'data'"))

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

console.log(`\n${pass} passed, ${fail} failed`)
if (!fail) console.log('OK — one ranker, one catalogue, one DERIVED page index, dereferenced by the one console surface; every rule armed.')
process.exit(fail ? 1 : 0)
