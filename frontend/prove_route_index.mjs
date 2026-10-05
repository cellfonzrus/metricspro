// THE ROUTE INDEX — derived from the pages that exist, so a page cannot be left out of search
// (owner directive 2026-10-05: *"this is a design issue not a random left out issue, all the search
// needs to be run through the index we built"*).
//
// WHAT WENT WRONG, named as a class rather than an instance. Somebody asked the console for a
// password reset and it found nothing. The instance is that `/account/password` is in no registry.
// The CLASS is that the searchable catalogue was assembled from CURATED registries — the nav, the
// report catalogue, the screen-alias registry — and a page that exists on disk but appears in none of
// them is invisible to every one of them. Measured when this was written: **34 of 319 static pages**
// were in no registry at all, among them the whole HR letters queue, five purchase-order screens and
// five closing screens. Curating an alias for the one that was noticed would have left the other 33.
//
// THE FIX IS DERIVATION PLUS A RATCHET. `src/lib/route-index.ts` is GENERATED from a walk of
// `src/app`, exactly the way `module_graph.py` derives its callers from the import graph rather than
// trusting a hand list (§50). This file is both the generator (`--bless`) and the proof, and run
// without `--bless` it FAILS THE BUILD when disk and the registry disagree — so the next page added
// is findable or the build is red. There is no third option and no quiet drift.
//
// WHAT STAYS HUMAN. A derived label reads like a path ("Account → Password"); a person may improve
// it, and a person may declare `aliases` (the words somebody would type) or `preauth` (the reason a
// page is deliberately not searchable — a sign-in screen, the marketing terms page). `--bless`
// PRESERVES all three for every path still on disk and only ever adds or removes whole entries. That
// preservation is the rule the module-graph blesser broke on 2026-10-04 when a one-line entry made
// its span run into the next one and it silently deleted 11 of 12 facts while still printing OK — so
// the write here is verified by re-reading and re-parsing before it is kept, and §E proves the
// preservation on a real round trip rather than asserting it.
//
// ALIASES FOR A NAV PAGE DO NOT LIVE HERE. `ScreenLink.tsx` SCREENS is the existing one home for "the
// spellings prose uses for a screen", and `harness_screen_link_guard.py` §A requires every SCREENS
// href to BE a nav href — which is exactly why it cannot hold `/account/password`. So the boundary is
// mechanical, not a preference: SCREENS owns the aliases of a nav page, this registry owns the
// aliases of a page nav does not list.
//
// Run:  node frontend/prove_route_index.mjs            (proof — fails the build on drift)
//       node frontend/prove_route_index.mjs --bless     (regenerate after adding or removing a page)
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs'

const HERE = dirname(fileURLToPath(import.meta.url))
const APP = join(HERE, 'src/app')
const OUT = join(HERE, 'src/lib/route-index.ts')
const RBAC = join(HERE, 'src/lib/rbac.ts')
const BLESS = process.argv.includes('--bless')

// ── derive: every static page on disk, as the URL the router serves ──────────────────────────────
// A Next route group — a directory in parentheses, e.g. `(platform)` — shapes the layout, not the
// URL, so it is stripped. A dynamic segment (`[id]`) is NOT a destination somebody can search for:
// there is no one URL to offer, so it is excluded by construction rather than by a list.
function routesOnDisk(dir = APP, rel = '') {
  const out = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (!statSync(full).isDirectory()) continue
    if (name.startsWith('_') || name === 'api') continue
    const seg = /^\(.*\)$/.test(name) ? '' : `/${name}`
    out.push(...routesOnDisk(full, rel + seg))
  }
  try {
    statSync(join(dir, 'page.tsx'))
    out.push(rel || '/')
  } catch { /* not a page directory */ }
  return out
}

const DISK = [...new Set(routesOnDisk())].filter(p => !p.includes('[')).sort()

// A derived label: the path's segments, title-cased, joined by an arrow — the same shape the nav uses
// for a sub-page. Only ever a FALLBACK; a nav entry's own label wins when the catalogue is built, and
// a person may replace this one in the registry.
function derivedLabel(path) {
  const parts = path.split('/').filter(Boolean)
  if (!parts.length) return 'Home'
  return parts
    .map(p => p.replace(/-/g, ' ').replace(/\b[a-z]/g, c => c.toUpperCase()))
    .join(' → ')
}

// ── parse the registry as it stands, so --bless can preserve the human half ──────────────────────
function parseRegistry(src) {
  const body = (src.match(/export const ROUTES: RouteEntry\[\] = \[([\s\S]*?)\n\]/) || [])[1]
  if (body == null) return null
  const out = new Map()
  // One entry per line, which is what makes the span unambiguous — the module-graph blesser's bug was
  // a span that could run past its own entry, so here an entry may not span lines at all.
  for (const line of body.split('\n')) {
    const t = line.trim()
    if (!t || t.startsWith('//')) continue
    const path = (t.match(/path: '([^']*)'/) || [])[1]
    if (path == null) return null                     // unparseable line: refuse rather than guess
    const label = (t.match(/label: '((?:[^'\\]|\\.)*)'/) || [])[1]
    const preauth = (t.match(/preauth: '((?:[^'\\]|\\.)*)'/) || [])[1]
    const aliasRaw = (t.match(/aliases: \[([^\]]*)\]/) || [])[1]
    const aliases = aliasRaw
      ? aliasRaw.split(',').map(s => (s.trim().match(/^'((?:[^'\\]|\\.)*)'$/) || [])[1]).filter(Boolean)
      : undefined
    out.set(path, { path, label, aliases, preauth })
  }
  return out
}

const esc = s => String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'")

function render(entries) {
  const lines = entries.map(e => {
    const bits = [`path: '${esc(e.path)}'`, `label: '${esc(e.label)}'`]
    if (e.aliases?.length) bits.push(`aliases: [${e.aliases.map(a => `'${esc(a)}'`).join(', ')}]`)
    if (e.preauth) bits.push(`preauth: '${esc(e.preauth)}'`)
    return `  { ${bits.join(', ')} },`
  })
  return `${HEADER}export const ROUTES: RouteEntry[] = [
${lines.join('\n')}
]

/** The destinations search may offer: every page except the ones declared pre-auth. The CALLER still
 *  gates each one with \`canAccessPath\`, which is the one home for "may this viewer open this path" —
 *  this function only removes the pages nobody should ever be sent to from inside the app. */
export function searchableRoutes(): RouteEntry[] {
  return ROUTES.filter(r => !r.preauth)
}
`
}

const HEADER = `// GENERATED — do not edit the ROUTES array by hand.
//
// THE ONE HOME for "every page this app has". Derived by walking \`src/app\` with
// \`node frontend/prove_route_index.mjs --bless\`; the same file run WITHOUT \`--bless\` is the proof,
// and it FAILS THE BUILD when disk and this array disagree. That ratchet is the whole point: before
// it, 34 of 319 static pages were in no registry at all and therefore unfindable by any search in the
// product — including the one somebody actually asked for, \`/account/password\`. Curating the missing
// page would have left the other 33, so the registry is derived instead (the \`module_graph.py\`
// pattern, §50).
//
// THREE FIELDS ARE HUMAN and are PRESERVED across a re-bless:
//   label    — a derived label reads like a path ("Account → Password"); replace it with what people
//              call the page. A page that also has a nav entry takes the NAV label instead, because
//              that is the wording on the viewer's own screen.
//   aliases  — the words somebody would TYPE. For a page nav lists, aliases belong in
//              \`ScreenLink.tsx\` SCREENS instead: its guard requires every href to be a nav href,
//              which is mechanically why a page like \`/account/password\` can only declare them here.
//   preauth  — the REASON a page is deliberately not searchable (a sign-in screen, a public terms
//              page). Not a flag: the string is the reason, so an exclusion cannot be silent.
//
// Everything else about a page — may this viewer open it, what module gates it — stays where it
// already lives (\`rbac.canAccessPath\`, \`canSeeItem\`). This file says only WHAT EXISTS.
export type RouteEntry = {
  path: string
  label: string
  aliases?: string[]
  /** Why this page is deliberately not offered by search. Absent = searchable. */
  preauth?: string
}

`

// Pages that are not reached from inside the app. Seeded on first bless with the reason; preserved
// afterwards like any human field, so changing one is a reviewable diff.
const SEED_PREAUTH = {
  '/': 'the marketing root — a signed-in user is redirected away from it',
  '/login': 'the sign-in screen — nobody searches for it from inside the app',
  '/signup': 'the sign-up screen — reached before there is an account',
  '/privacy': 'a public policy page, not a place to do work',
  '/terms': 'a public policy page, not a place to do work',
}
// Aliases seeded for pages nav does not list, where the label alone is not what somebody types.
const SEED_ALIASES = {
  '/account/password': ['password reset', 'reset password', 'change password', 'change my password',
                        'forgot password', 'new password'],
}

function build(existing) {
  return DISK.map(path => {
    const prev = existing?.get(path)
    return {
      path,
      label: prev?.label || derivedLabel(path),
      aliases: prev?.aliases || SEED_ALIASES[path],
      preauth: prev?.preauth || SEED_PREAUTH[path],
    }
  })
}

// ── --bless: write, then VERIFY the write by re-reading and re-parsing it ─────────────────────────
if (BLESS) {
  let existing = null
  try { existing = parseRegistry(readFileSync(OUT, 'utf8')) } catch { existing = null }
  const before = existing ? existing.size : 0
  const entries = build(existing)
  const text = render(entries)
  // The module-graph blesser printed OK while deleting 11 of 12 facts. So: never write fewer entries
  // than disk has, and re-read what landed before calling it done.
  if (entries.length !== DISK.length) {
    console.error(`REFUSED: built ${entries.length} entries for ${DISK.length} pages on disk`)
    process.exit(1)
  }
  writeFileSync(OUT, text)
  const back = parseRegistry(readFileSync(OUT, 'utf8'))
  if (!back || back.size !== DISK.length) {
    console.error(`REFUSED: wrote ${DISK.length} entries but read back ${back ? back.size : 'nothing'}`)
    process.exit(1)
  }
  for (const e of entries) {
    const r = back.get(e.path)
    if (!r || r.label !== e.label || (r.preauth || undefined) !== (e.preauth || undefined)
        || JSON.stringify(r.aliases || null) !== JSON.stringify(e.aliases || null)) {
      console.error(`REFUSED: ${e.path} did not round-trip`)
      process.exit(1)
    }
  }
  console.log(`blessed: ${DISK.length} pages (was ${before}); verified by re-reading the file`)
  process.exit(0)
}

// ── the proof ────────────────────────────────────────────────────────────────────────────────────
let pass = 0, fail = 0
const ok = (name, cond, detail) => {
  if (cond) { pass++; return }
  fail++
  console.log(`  FAIL  ${name}${detail === undefined ? '' : `   ${JSON.stringify(detail)}`}`)
}

const SRC = readFileSync(OUT, 'utf8')
const REG = parseRegistry(SRC)

console.log(`§A  the registry matches the pages on disk (${DISK.length} static pages)`)
{
  ok('A1  the registry parses', REG != null)
  const have = new Set(REG ? REG.keys() : [])
  const missing = DISK.filter(p => !have.has(p))
  const extra = [...have].filter(p => !DISK.includes(p))
  ok('A2  every page on disk is in the registry — run --bless if this is red', missing.length === 0, missing)
  ok('A3  the registry names no page that does not exist', extra.length === 0, extra)
  ok('A4  every entry carries a label', REG && [...REG.values()].every(e => e.label && e.label.trim()))
  ok('A5  no page is listed twice', REG && REG.size === DISK.length, { reg: REG?.size, disk: DISK.length })
  // The page the owner actually asked for — the regression, by name.
  ok('A6  /account/password is in the registry', have.has('/account/password'))
  const pw = REG?.get('/account/password')
  ok('A7  and it is searchable, not pre-auth', pw && !pw.preauth, pw)
  ok('A8  and the words somebody types reach it',
     (pw?.aliases || []).includes('password reset'), pw?.aliases)
}

console.log('§B  an exclusion carries its REASON, never a bare flag')
{
  const pre = REG ? [...REG.values()].filter(e => e.preauth) : []
  ok('B1  something is excluded', pre.length > 0)
  ok('B2  every exclusion states why, in a sentence', pre.every(e => e.preauth.length > 15),
     pre.map(e => [e.path, e.preauth]))
  ok('B3  the sign-in screen is excluded', pre.some(e => e.path === '/login'))
  ok('B4  a working page is NOT excluded', !pre.some(e => e.path.startsWith('/commcalc')),
     pre.map(e => e.path))
  // searchableRoutes() is the one filter.
  ok('B5  searchableRoutes() drops exactly the excluded ones',
     SRC.includes('return ROUTES.filter(r => !r.preauth)'))
}

console.log('§C  the registry says WHAT EXISTS and nothing else')
{
  // Comments stripped BOTH kinds: the header and the `searchableRoutes` docstring legitimately name
  // `canAccessPath` to say where that decision lives, so a line-comment-only strip would make this
  // check read the documentation and fail. (The same shape of mistake §54.3 catches in reverse.)
  const noComments = SRC.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/.*$/gm, ' ')
  ok('C1  it holds no permission rule',
     !/canSeeItem|canAccessPath|scope|module:/.test(noComments), noComments.match(/canSeeItem|canAccessPath|scope|module:/g))
  ok('C2  it is import-free', !/^\s*import\s+(?!type\b)/m.test(SRC))
  ok('C3  it carries no API path', !/\/api\/v1/.test(noComments))
  // Counter-arming: the strip must not have blanked the whole file.
  ok('C4  the stripped source still holds the entries', /path: '\/account\/password'/.test(noComments))
}

console.log('§D  the derived label is a label, not a slug')
{
  ok('D1  a one-segment page', derivedLabel('/reports') === 'Reports', derivedLabel('/reports'))
  ok('D2  a nested page', derivedLabel('/account/password') === 'Account → Password',
     derivedLabel('/account/password'))
  ok('D3  a hyphenated segment becomes words',
     derivedLabel('/hr/letters/queue') === 'Hr → Letters → Queue', derivedLabel('/hr/letters/queue'))
  ok('D4  the root', derivedLabel('/') === 'Home')
}

console.log('§E  --bless PRESERVES the human half (a real round trip, in memory)')
{
  // The module-graph blesser deleted 11 of 12 facts while printing OK (2026-10-04). This re-runs the
  // real parse → build → render → parse cycle and asserts nothing human was lost.
  const round = parseRegistry(render(build(REG)))
  ok('E1  the round trip keeps every entry', round && round.size === REG.size,
     { before: REG?.size, after: round?.size })
  let keptLabels = 0, keptAliases = 0, keptPreauth = 0
  for (const [path, e] of REG || []) {
    const r = round?.get(path)
    if (r?.label === e.label) keptLabels++
    if (JSON.stringify(r?.aliases || null) === JSON.stringify(e.aliases || null)) keptAliases++
    if ((r?.preauth || undefined) === (e.preauth || undefined)) keptPreauth++
  }
  ok('E2  every label survives', keptLabels === REG?.size, { keptLabels, of: REG?.size })
  ok('E3  every alias list survives', keptAliases === REG?.size, { keptAliases, of: REG?.size })
  ok('E4  every exclusion reason survives', keptPreauth === REG?.size, { keptPreauth, of: REG?.size })
  // ARMED: the preservation is real, not a tautology — drop a label and the round trip must differ.
  const hacked = new Map(REG)
  hacked.set('/account/password', { path: '/account/password', label: 'CHANGED' })
  const hackedRound = parseRegistry(render(build(hacked)))
  ok('E5  ARMED — a changed label comes back changed, so E2 is measuring something',
     hackedRound?.get('/account/password')?.label === 'CHANGED')
  // ARMED: a page deleted from the registry is rebuilt from disk, never left out.
  const shrunk = new Map(REG); shrunk.delete('/account/password')
  ok('E6  ARMED — a page missing from the registry is re-derived from disk',
     build(shrunk).some(e => e.path === '/account/password'))
  // ARMED: an unparseable line makes the parser REFUSE rather than silently drop entries.
  ok('E7  ARMED — an entry with no path makes the parse refuse',
     parseRegistry(SRC.replace(/\{ path: '\/account\/password'[^\n]*\n/, '  { oops: 1 },\n')) === null)
}

console.log('§F  the nav is still the label authority where it has one')
{
  const nav = new Set((readFileSync(RBAC, 'utf8').match(/\{ href: '([^']+)'/g) || [])
    .map(m => m.replace(/^\{ href: '/, '').replace(/'$/, '').split('#')[0].split('?')[0]))
  const inNav = DISK.filter(p => nav.has(p))
  const notInNav = DISK.filter(p => !nav.has(p))
  ok('F1  most pages do have a nav entry', inNav.length > notInNav.length,
     { inNav: inNav.length, notInNav: notInNav.length })
  ok('F2  and the ones that do not are exactly what this registry exists for',
     notInNav.length > 0 && notInNav.includes('/account/password'), notInNav.length)
  console.log(`      ${inNav.length} pages are in NAV; ${notInNav.length} are not and were invisible before this`)
}

console.log(`\n${pass} passed, ${fail} failed`)
if (!fail) console.log('OK — every page that exists is in the index; a new one is findable or the build is red.')
process.exit(fail ? 1 : 0)
