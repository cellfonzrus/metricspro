// ONE FOLD FOR A FIGURE AND ITS EVIDENCE — the lock on `src/lib/drill-narrow.ts` (index §CORE-17.3d).
//
// Some reports fold their tiles in TypeScript over the rows the reader filtered down to, while the
// server folds their drill-down panels over the whole range. Under any filter those are different
// populations, and a panel explaining a figure the screen is not showing looks like proof of the wrong
// number. `narrowPanel` closes that by restricting a panel to the screen's own selection, and the tile
// then reads its number OFF the narrowed panel rather than reducing the rows a second time.
//
// THAT MAKES A CLIENT-SIDE FOLD AUTHORITATIVE FOR WHAT THE SCREEN SHOWS, so this file exists to pin
// it to the server's. §A asserts the IDENTITY: with nothing filtered out, the narrowed figure and the
// whole narrowed tie-out must equal what `core/report_drilldown.panel` produced, to the cent and word
// for word. If the two folds ever drift, this fails rather than a manager's screen disagreeing with
// itself. The fixtures in `prove_drill_narrow_fixtures.json` are real panels emitted by the real
// server declarations; `backend/harness_drill_narrow_fixtures.py` fails the build if they go stale.
//
//   node frontend/prove_drill_narrow.mjs
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import ts from 'typescript'

const HERE = dirname(fileURLToPath(import.meta.url))
let pass = 0
const fails = []
const check = (name, ok, detail) => {
  if (ok) { pass++; console.log('  ok  ' + name) }
  else { fails.push(name); console.log('  FAIL ' + name + (detail === undefined ? '' : '  → ' + JSON.stringify(detail))) }
}

// The module under test, transpiled with the repo's own TypeScript and imported as a module. Its one
// import is a `type`-only one, which transpiles away, so nothing else is needed to load it.
const SRC = join(HERE, 'src', 'lib', 'drill-narrow.ts')
const src = readFileSync(SRC, 'utf8')
const js = ts.transpileModule(src, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  fileName: 'drill-narrow.ts',
}).outputText
const N = await import('data:text/javascript;base64,' + Buffer.from(js, 'utf8').toString('base64'))

const FIX = JSON.parse(readFileSync(join(HERE, 'prove_drill_narrow_fixtures.json'), 'utf8'))
const panels = FIX.panels
const names = Object.keys(panels)
const cents = n => Math.round((Number(n) || 0) * 100)
const KEEP_ALL = () => true

// A8 is the one that makes this section exact; A2–A7 are the regression guard on it, so that a future
// change which starts recomputing the unfiltered case has to come back here and agree field by field
// rather than quietly shipping a second arithmetic.
console.log('§A the identity — with nothing filtered out, the client fold IS the server fold')
check('A1 the fixtures are real server panels, not hand-written ones', names.length >= 5, names.length)
const wrongFigure = names.filter(k =>
  cents(N.narrowPanel(panels[k], KEEP_ALL).figure.amount) !== cents(panels[k].figure.amount))
check('A2 every figure comes back to the cent', wrongFigure.length === 0, wrongFigure)
const wrongTies = names.filter(k => {
  const a = N.narrowPanel(panels[k], KEEP_ALL).tie_out
  const b = panels[k].tie_out
  return a.ties !== b.ties
})
check('A3 and so does every tie-out VERDICT — a client fold that disagreed about whether the '
  + 'evidence adds up would be worse than none', wrongTies.length === 0, wrongTies)
const wrongWords = names.filter(k =>
  N.narrowPanel(panels[k], KEEP_ALL).tie_out.words !== panels[k].tie_out.words)
check('A4 word for word, including the money formatting — the sentence a manager reads is the same '
  + 'sentence whichever side folded it', wrongWords.length === 0, wrongWords)
const wrongCounts = names.filter(k => {
  const a = N.narrowPanel(panels[k], KEEP_ALL).tie_out
  const b = panels[k].tie_out
  return a.counted_rows !== b.counted_rows || a.unmeasured_rows !== b.unmeasured_rows
    || a.shown_not_counted !== b.shown_not_counted || cents(a.rows_total) !== cents(b.rows_total)
    || cents(a.difference) !== cents(b.difference)
})
check('A5 and every count and total inside it', wrongCounts.length === 0, wrongCounts)
const wrongSources = names.filter(k =>
  JSON.stringify(N.narrowPanel(panels[k], KEEP_ALL).sources) !== JSON.stringify(panels[k].sources))
check('A6 the provenance roll-up too — same tables, same row counts, same order',
  wrongSources.length === 0, wrongSources)
const wrongUntraceable = names.filter(k =>
  N.narrowPanel(panels[k], KEEP_ALL).untraceable_origins !== panels[k].untraceable_origins)
check('A7 and an untraceable origin stays counted — narrowing must not tidy a provenance gap away',
  wrongUntraceable.length === 0, wrongUntraceable)
check('A8 and the identity is EXACT because nothing is recomputed when nothing was dropped — the '
  + 'server\'s own panel is handed straight back, so the two folds cannot be two arithmetics '
  + 'hoping to agree',
  names.every(k => N.narrowPanel(panels[k], KEEP_ALL) === panels[k]))
check('A9 a relabelled panel is still the server\'s numbers, only its title changed', (() => {
  const p = N.narrowPanel(panels[names[0]], KEEP_ALL, 'for the stores you picked')
  return p.figure.label === 'for the stores you picked'
    && cents(p.figure.amount) === cents(panels[names[0]].figure.amount)
    && p.tie_out === panels[names[0]].tie_out
})())

console.log('\n§B narrowing — the panel follows the screen')
const withRows = names.filter(k => (panels[k].rows || []).length > 1)
check('B1 there are fixtures with several rows to narrow', withRows.length >= 3, withRows.length)
const one = withRows[0]
const firstKey = N.rowKeyOf(panels[one].rows[0])
const narrowed = N.narrowPanel(panels[one], (_r, k) => k === firstKey)
check('B2 keeping one row leaves one row', narrowed.rows.length === 1)
check('B3 and the figure is that row\'s own contribution, not the whole range\'s',
  cents(narrowed.figure.amount)
  === cents(panels[one].rows[0].in_figure && panels[one].rows[0].present ? panels[one].rows[0].amount : 0))
check('B4 the narrowed figure still TIES to the narrowed rows — the whole point, since the tile is '
  + 'read off it', narrowed.tie_out.ties !== false)
check('B5 dropping every row gives $0.00 over no rows, which ties',
  cents(N.narrowPanel(panels[one], () => false).figure.amount) === 0
  && N.narrowPanel(panels[one], () => false).rows.length === 0)
check('B6 narrowPanels narrows the whole strip and keeps the keys',
  Object.keys(N.narrowPanels(panels, KEEP_ALL)).sort().join() === names.sort().join())

console.log('\n§C the keys line up')
check('C1 every row the server sent carries the key of the report row it explains',
  names.every(k => (panels[k].rows || []).every(r => r.row_key && Object.keys(r.row_key).length)))
check('C2 a key the SCREEN builds from the same fields matches the row\'s, whatever the field order',
  names.every(k => (panels[k].rows || []).every(r => {
    const f = Object.keys(r.row_key)
    const fwd = {}; const rev = {}
    for (const n of f) fwd[n] = r.row_key[n]
    for (const n of [...f].reverse()) rev[n] = r.row_key[n]
    return N.screenKey(fwd) === N.rowKeyOf(r) && N.screenKey(rev) === N.rowKeyOf(r)
  })))
check('C3 a blank key is empty, never the string "undefined"',
  N.screenKey({ a: null, b: undefined, c: '' }) === '' && N.rowKeyOf({}) === '')

console.log('\n§D it REFUSES rather than guess')
check('D1 a capped panel cannot be narrowed — the rows beyond the cap are not on the client, so a '
  + 'narrowed figure would be missing rows nobody can see',
  N.narrowPanel({ ...panels[one], capped: true }, KEEP_ALL) === null)
check('D2 so does a panel that is absent, or carries no rows at all',
  N.narrowPanel(undefined, KEEP_ALL) === null && N.narrowPanel(null, KEEP_ALL) === null
  && N.narrowPanel({ figure: { amount: 1 } }, KEEP_ALL) === null)
check('D3 narrowPanels leaves a refused panel OUT, so a screen offers no link for it rather than a '
  + 'link that opens a short answer',
  !(one in N.narrowPanels({ ...panels, [one]: { ...panels[one], capped: true } }, KEEP_ALL)))
check('D4 a row with NO key is KEPT, never dropped — dropping it would silently shrink the figure, '
  + 'which is the failure this whole mechanism exists to prevent',
  N.narrowPanel({ figure: { amount: 5 }, rows: [{ amount: 5, present: true, in_figure: true, measured: true }] },
    () => false).rows.length === 1)
check('D5 agreesToTheCent is false for an absent panel and true only on a cent-exact match',
  N.agreesToTheCent(undefined, 0) === false
  && N.agreesToTheCent({ figure: { amount: 10.01 } }, 10.01) === true
  && N.agreesToTheCent({ figure: { amount: 10.01 } }, 10.02) === false)

console.log('\n§Z negative controls — the checks above can FAIL')
// §Z drives the RECOMPUTE path, which only runs once a row has actually been dropped. A panel built
// here from two rows and narrowed to one is the smallest thing that does.
const two = (a, b, figure) => ({
  figure: { label: 'x', amount: figure, unit: 'money' },
  rows: [a, b], rows_shown: 2, rows_total_count: 2, capped: false,
  tie_out: { ties: true, words: 'the 2 row(s) behind this figure add up to it exactly' },
  sources: [], untraceable_origins: 0,
})
const row = (key, amount, extra) => ({
  label: 'r' + key, amount, present: amount !== null, in_figure: true, measured: amount !== null,
  origins: [{ table: 'daily_closing', keys: { a: key } }], row_key: { a: key }, ...extra,
})
const keepA = (_r, k) => k === N.screenKey({ a: 1 })

check('Z1 a planted cent on the kept row moves the narrowed figure — the recompute is real',
  cents(N.narrowPanel(two(row(1, 4.01), row(2, 3), 7), keepA).figure.amount) === 401)
check('Z2 a kept row with no number makes the verdict null, never true — a sum missing a row was '
  + 'never a comparison, and a composed sum must not claim otherwise',
  N.narrowPanel(two(row(1, null), row(2, 3), 7), keepA).tie_out.ties === null)
check('Z3 a narrowed verdict NEVER claims a check it could not make: it is flagged by_construction '
  + 'and says the figure is the selection\'s own total',
  (() => {
    const p = N.narrowPanel(two(row(1, 4), row(2, 3), 7), keepA)
    return p.tie_out.ties === true && p.tie_out.by_construction === true
      && p.tie_out.words.includes('cannot disagree')
      && !p.tie_out.words.startsWith('the 1 row(s) behind this figure add up to it exactly')
  })())
check('Z4 and the SERVER\'s verdict over the whole range is kept beside it, because that one was a '
  + 'check', (() => {
    const p = N.narrowPanel(two(row(1, 4), row(2, 3), 7), keepA)
    return p.tie_out.whole_range?.ties === true
      && p.tie_out.words.includes('Over the whole range the report asked for')
  })())
check('Z5 a row the figure excludes is kept as context and NOT counted', (() => {
  const p = N.narrowPanel({
    ...two(row(1, 4), row(2, 3), 7),
    rows: [row(1, 4), row(2, 99, { in_figure: false }), row(3, 50)],
  }, (_r, k) => k !== N.screenKey({ a: 3 }))
  return cents(p.figure.amount) === 400 && p.rows.length === 2 && p.tie_out.shown_not_counted === 1
})())
check('Z6 the provenance roll-up is recomputed, so it counts the selection\'s rows and not the '
  + 'range\'s', (() => {
    const p = N.narrowPanel(two(row(1, 4), row(2, 3), 7), keepA)
    return p.sources.length === 1 && p.sources[0].table === 'daily_closing' && p.sources[0].rows === 1
  })())
check('Z7 an untraceable origin on a kept row stays counted', (() => {
  const p = N.narrowPanel({
    ...two(row(1, 4), row(2, 3), 7),
    rows: [{ ...row(1, 4), origins: [{ table: null, keys: { a: 1 } }] }, row(2, 3)],
  }, keepA)
  return p.untraceable_origins === 1 && p.sources.length === 0
})())

console.log(`\n${pass} passed, ${fails.length} failed`)
if (fails.length) { for (const f of fails) console.log('  - ' + f); process.exit(1) }
console.log('OK — the client fold is the server fold, and it refuses where it cannot be.')
