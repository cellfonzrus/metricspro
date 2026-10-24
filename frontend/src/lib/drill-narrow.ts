// A PANEL NARROWED TO THE SCREEN'S OWN SELECTION — the one home (index §CORE-17.3d).
//
// THE PROBLEM, STATED ONCE SO NOBODY RE-DERIVES IT. Some reports fold their tiles in TypeScript over
// the rows the reader has filtered down to, while the server folds their drill-down panels over the
// whole range it was asked for. Under any filter the two describe DIFFERENT POPULATIONS, and a panel
// explaining a figure the screen is not showing is worse than no panel: it looks like proof of the
// wrong number. Three reports hit this — `/closing/tender-recon`, `/closing/tender-recon-3way` and
// `/closing/pickup` — and the first two were each about to ship their own excuse for it (§CORE-17.3b).
//
// THE FIX IS ONE FOLD, NOT ONE EXCUSE. The server serves each drill row with the `row_key` that
// addresses the report row it explains. A screen hands this function the SAME predicate it filters
// its table by, and gets back a panel whose rows, figure and tie-out are all over the selection. The
// tile then reads its number OFF THAT PANEL (`narrowed.figure.amount`) rather than reducing the rows
// itself — so the tile and its evidence are the same fold, by construction, and cannot drift.
//
// WHY THIS IS ALLOWED TO FOLD AT ALL, given the server is the authority. With NO narrowing applied it
// must be the identity: `narrowPanel(p, () => true).figure.amount === p.figure.amount`, to the cent,
// and the tie-out must come back saying the same thing. `frontend/prove_drill_narrow.mjs` asserts
// exactly that against real server payloads, which is what pins the two folds to agree; the arithmetic
// is a port of `core/report_drilldown.tie_out`, deliberately line-for-line.
//
// IT REFUSES RATHER THAN GUESS ON A CAPPED PANEL. The server caps a panel's rows (ROW_CAP) and serves
// the true count separately; the tie-out it sends was computed over ALL the rows, before the cap. So a
// narrowed figure over a capped list would be missing rows nobody can see, and this returns `null` for
// such a panel. A screen then offers no drill and says why — the same honesty the uncapped case gets,
// not a smaller number presented as the whole of one.
import type { DrillPanel, DrillRow } from '@/components/DrillDownPanel'

const cents = (n: unknown) => Math.round((Number(n) || 0) * 100)
const money = (n: number) =>
  '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** A drill row's key as one comparable string. Key order cannot matter, so the entries are sorted;
 *  blank values are already dropped server-side, so a key that is present means something. */
export function rowKeyOf(r: DrillRow): string {
  const k = r?.row_key
  if (!k) return ''
  return Object.keys(k).sort().map(n => `${n}=${String(k[n] ?? '')}`).join('\u0001')
}

/** The same string for a key the SCREEN builds, so a page can put its selection in a Set and test
 *  membership without knowing how the string is made. */
export function screenKey(parts: Record<string, string | number | null | undefined>): string {
  return Object.keys(parts).sort()
    .filter(n => parts[n] !== null && parts[n] !== undefined && parts[n] !== '')
    .map(n => `${n}=${String(parts[n])}`).join('\u0001')
}

/** THE ONLY CHECK A NARROWED PANEL CAN HONESTLY MAKE, and it is not the server's.
 *
 *  The server's tie-out is a real comparison: the figure comes from the REPORT and the rows come from
 *  the EVIDENCE, so they can disagree, and when they do that is a defect worth seeing. A narrowed
 *  figure is Σ(the selection's own rows), so comparing it against those same rows could only ever
 *  come back True — a check that cannot fail, printed as though it had passed. That would be the
 *  worst kind of reassurance.
 *
 *  So a narrowed tie-out states what IS true: the figure is the selection's own total, and whether
 *  every row in it carries a number. `ties` is **null** when one does not — a sum missing a row was
 *  never a comparison — and otherwise True with `by_construction` set, so a screen (and a reader) can
 *  tell a sum that was checked from a sum that was composed. The server's own verdict over the whole
 *  range is kept beside it as `whole_range`, because that one WAS a check. */
function narrowedTieOut(
  amount: number, rows: DrillRow[], server: DrillPanel['tie_out'],
): NonNullable<DrillPanel['tie_out']> {
  const counted = rows.filter(r => r.in_figure)
  const unmeasured = counted.filter(r => !r.measured)
  const total = Math.round(counted.reduce((a, r) => a + (r.present ? Number(r.amount) || 0 : 0), 0) * 100) / 100
  const shownNotCounted = rows.filter(r => !r.in_figure).length
  let ties: boolean | null
  let words: string
  if (unmeasured.length) {
    ties = null
    words = `${unmeasured.length} of the ${counted.length} row(s) this figure counts has no recorded `
      + `number, so the rows cannot be added up to check the figure`
  } else {
    ties = true
    words = `this figure is the total of the ${counted.length} row(s) below for the selection you are `
      + `viewing — ${money(total)} — so it cannot disagree with them`
  }
  if (shownNotCounted) {
    words += `; ${shownNotCounted} further row(s) are shown for context and are deliberately not `
      + `counted in it`
  }
  if (server?.words) {
    words += `. Over the whole range the report asked for, ${server.words}`
  }
  return {
    figure: amount, rows_total: total, difference: 0, ties,
    counted_rows: counted.length, unmeasured_rows: unmeasured.length,
    shown_not_counted: shownNotCounted, words,
    by_construction: ties === true, whole_range: server || null,
  }
}

/** THE PANEL, RESTRICTED TO THE ROWS THE SCREEN IS SHOWING.
 *
 *  `keep` is the screen's own row predicate, given each drill row and its key string. Returns null
 *  when the panel cannot be narrowed honestly: there is no panel, or the server capped its rows (see
 *  the header). A row the server sent with no `row_key` cannot be matched to a report row, so it is
 *  KEPT — dropping it would silently shrink the figure, which is the failure this whole mechanism
 *  exists to prevent.
 *
 *  `figureLabel` lets a screen say what the narrowed figure is ("...for the 3 stores selected"); the
 *  panel's own label is used when it is omitted. */
export function narrowPanel(
  panel: DrillPanel | undefined | null,
  keep: (row: DrillRow, key: string) => boolean,
  figureLabel?: string,
): DrillPanel | null {
  if (!panel || !panel.rows) return null
  if (panel.capped) return null
  const rows = panel.rows.filter(r => {
    const k = rowKeyOf(r)
    return k === '' ? true : keep(r, k)
  })
  // NOTHING WAS DROPPED, SO NOTHING IS RECOMPUTED. The selection is the whole range, and the server's
  // figure, verdict and provenance roll-up are already the right answers for it — serving them
  // untouched is both exact and the reason the identity in `prove_drill_narrow.mjs` is a tautology
  // rather than two arithmetics hoping to agree.
  if (rows.length === panel.rows.length) {
    return figureLabel
      ? { ...panel, figure: { ...(panel.figure || {}), label: figureLabel } }
      : panel
  }
  const amount = Math.round(
    rows.reduce((a, r) => a + (r.in_figure && r.present ? Number(r.amount) || 0 : 0), 0) * 100) / 100
  return {
    ...panel,
    figure: { ...(panel.figure || {}), label: figureLabel || panel.figure?.label, amount },
    rows,
    rows_shown: rows.length,
    rows_total_count: rows.length,
    capped: false,
    tie_out: narrowedTieOut(amount, rows, panel.tie_out),
    sources: sourcesOf(rows),
    untraceable_origins: rows.reduce(
      (a, r) => a + (r.origins || []).filter(o => !o?.table).length, 0),
  }
}

/** The distinct tables the narrowed rows came from, with how many touched each — recomputed, because
 *  the server's list counts rows the selection no longer contains. */
function sourcesOf(rows: DrillRow[]): { table: string; rows: number }[] {
  const n: Record<string, number> = {}
  for (const r of rows) {
    for (const o of r.origins || []) {
      if (o?.table) n[o.table] = (n[o.table] || 0) + 1
    }
  }
  return Object.keys(n).sort((a, b) => n[b] - n[a] || a.localeCompare(b))
    .map(table => ({ table, rows: n[table] }))
}

/** Every panel narrowed the same way, so a screen narrows its whole strip in one call. */
export function narrowPanels(
  panels: Record<string, DrillPanel>,
  keep: (row: DrillRow, key: string) => boolean,
): Record<string, DrillPanel> {
  const out: Record<string, DrillPanel> = {}
  for (const k of Object.keys(panels || {})) {
    const p = narrowPanel(panels[k], keep)
    if (p) out[k] = p
  }
  return out
}

/** Does a tile's own number agree with its panel's, to the cent? The gate every screen on this
 *  mechanism passes to `useDrillTile`, so "the drill is offered only where it is honest" is also one
 *  rule rather than one per page. */
export function agreesToTheCent(panel: DrillPanel | undefined, tile: unknown): boolean {
  return !!panel && cents(panel.figure?.amount) === cents(tile)
}
