// report-totals.ts — pure aggregation for a report TOTAL row. Shared by ReportShell's
// on-screen <tfoot> AND its export payload so the exported total is IDENTICAL to the total
// on screen (RULE FOUR — what you see is what you export). No React / no imports, so the
// aggregation math is unit-testable in plain Node.
//
// A column's aggregation is resolved once and used everywhere:
//   • money / numeric-count columns  → SUM of all the (filtered) rows given
//   • average / ratio / % columns    → recomputed MEAN (a SUM of ratios is meaningless);
//                                       an unweighted mean of the displayed values — a
//                                       WEIGHTED ratio needs the underlying bases, so a page
//                                       that wants that passes `agg` / a precomputed column
//   • text / date / anything else    → blank (the first blank column carries the label)
// A page can override any column with an explicit `agg` ('sum' | 'avg' | 'none').

export type Agg = 'sum' | 'avg' | 'none'

/**
 * A report row, shape unknown to this module on purpose: every value it reads comes through a
 * column's own `get`, so the rows themselves are never inspected here. Named once rather than
 * respelled at each signature.
 */
export type TotalsRow = any

// Minimal structural column shape — ExportColumn (lib/export) satisfies this, so ReportShell
// passes its own columns straight through without a cast.
export interface TotalsCol {
  header: string
  get: (row: any) => any
  money?: boolean
  align?: 'left' | 'right'
  type?: 'text' | 'money' | 'number' | 'date'
  agg?: Agg
}

export interface TotalCell {
  text: string             // formatted for on-screen / PDF / Print
  raw: string | number     // unformatted for Excel (real numbers stay numeric)
  isMoney: boolean
  agg: Agg
}

// Headers that name an average / ratio / percentage. `[^a-z]` boundaries keep these from
// matching mid-word (e.g. "rate" inside "Generated" / "Operator" must NOT trip it).
const AVG_RE = /(^|[^a-z])(avg|average|mean|rate|ratio|percent|pct|margin)([^a-z]|$)|%|\bper\b/i

export const isMoneyCol = (c: TotalsCol): boolean => !!c.money || c.type === 'money'

// Parse a genuine number, tolerating currency formatting ($ , % whitespace) but REJECTING
// anything with stray letters. Critically this must NOT read a code like "X-12" as -12 (which a
// naive strip-then-parse does), or the auto-sum guard would sum a text column. `Number('')` is 0,
// not NaN, so we also require at least one digit.
/** What a column's `get` can hand a formatter. Named once so the three money functions below do
 *  not each respell it as `any` — their whole job is to tell these cases apart. */
export type CellValue = string | number | null | undefined

export function toNumber(v: any): number | null {
  if (v == null || v === '') return null
  if (typeof v === 'number') return isFinite(v) ? v : null
  const cleaned = String(v).trim().replace(/[$,%\s]/g, '')
  if (!/^[-+]?(\d+\.?\d*|\.\d+)$/.test(cleaned)) return null
  const n = Number(cleaned)
  return isNaN(n) ? null : n
}

// ── WHAT A MONEY CELL SHOWS — THE ONE HOME, dereferenced by every table, total and export ───────
//
// OWNER REPORT 2026-11-09: the Cash Calendar's grid said "in: not measured" while the table under
// it said Expected in $0.00, Net $0.00 and TOTAL $0.00. A report whose entire purpose is to never
// show a figure for money nobody measured showed four of them, and the .xlsx would have carried
// them out of the building.
//
// THE CLASS, not the instance, and it is §19.18's again: *one question, six answers.* "How is a
// money value rendered" was re-implemented as `Number(v) || 0` in SIX places — ReportShell (cell
// and subtotal), DataGrid, lib/export (text and the raw .xlsx cell), and `moneyFmt` here. Every one
// of them turned a NON-NUMERIC value into a confident zero. `lib/export` fixed the ABSENT half on
// 2026-09-10, documented it, and deliberately left a non-numeric value rendering "$0.00 as before
// rather than silently vanishing" — the right answer was never vanishing, it was printing the word.
// The other five copies never got even that half.
//
// THE DISTINCTION THIS HOME KEEPS, and it is three states rather than two:
//   · a NUMBER (including a real 0) → currency. A measured nothing IS a figure.
//   · a WORD — non-empty and not numeric → ITSELF. Its author wrote it on purpose; "not measured",
//     "—", "not set" are claims about measurement, and $0.00 is a claim about money.
//   · ABSENT (null / '') → whatever the calling surface already decided, passed in as `absent`.
//     That half was ruled on 2026-09-10 (blank in an export, $0.00 on screen) and is NOT reopened
//     here: 665 of the app's 681 money columns rely on it.
//
// `toNumber` above is the one test of numeric-ness and is what makes the middle case decidable —
// it is regex-validated, so `Number('')`'s 0 and a stray `NaN` cannot sneak through. These three
// functions dereference it rather than re-deciding. Locked by `harness_column_render_lock.py`.

/** TRUE where a value was said on purpose and is not a number — a measurement claim, not a figure.
 *  Absent is deliberately FALSE: "nobody supplied this" is a different fact from "this was not
 *  measured", and only the latter may override a money column's formatting. */
export function isUnmeasured(v: CellValue): boolean {
  return v != null && v !== '' && toNumber(v) == null
}

/** What a money cell SHOWS. `absent` is the calling surface's own ruling for null/'' — '' in an
 *  export, '$0.00' on screen — so this home formats money without relitigating that. */
export function moneyText(v: CellValue, opts?: { absent?: string }): string {
  if (v == null || v === '') return opts?.absent ?? '$0.00'
  if (isUnmeasured(v)) return String(v)
  return moneyFmt(toNumber(v) ?? 0)
}

/** What a money value lands in a SPREADSHEET cell as: a number where it is one, the word where it
 *  is a word, an empty cell where it is absent. A file outlives the screen and carries no note, so
 *  a word must survive the download rather than becoming a 0 somebody later sums. */
export function moneyRaw(v: CellValue): string | number {
  if (v == null || v === '') return ''
  const n = toNumber(v)
  return n == null ? String(v) : n
}

const moneyFmt = (n: any) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n) || 0)
const numberFmt = (n: number) =>
  Number.isInteger(n) ? n.toLocaleString('en-US') : n.toLocaleString('en-US', { maximumFractionDigits: 2 })

// Decide how ONE column aggregates in the TOTAL row.
export function resolveAgg(col: TotalsCol, rows: TotalsRow[]): Agg {
  if (col.agg) return col.agg                         // explicit page override always wins
  if (AVG_RE.test(col.header || '')) return 'avg'     // ratio/%/rate → mean, never a sum
  // A MONEY COLUMN SUMS ONLY WHERE NOTHING IN IT REFUSED TO BE MEASURED (owner report 2026-11-09).
  // This used to return 'sum' unconditionally, and the sum then read every "not measured" cell as
  // 0 — so four unmeasured days totalled $0.00, which is the one figure this whole class of report
  // exists not to print. A PARTIAL measurement refuses too: a total over the days that answered is
  // not a total over the days, and labelling it one is the §CORE-17.3h defect (a figure no set of
  // rows adds up to). Absent cells still sum, per the 2026-09-10 ruling left standing above.
  // This is the same rule the numeric-count branch below already applied; money was the exception.
  if (isMoneyCol(col)) {
    return rows.some(r => isUnmeasured(col.get(r))) ? 'none' : 'sum'
  }
  if (col.type === 'text' || col.type === 'date') return 'none'
  // numeric-count columns (type:number, or right-aligned) → sum, but ONLY when every present
  // value is actually numeric (guards a right-aligned text column from being summed to 0).
  if (col.type === 'number' || col.align === 'right') {
    const present = rows.map(r => col.get(r)).filter(v => v != null && v !== '')
    if (present.length === 0) return 'none'
    return present.every(v => toNumber(v) != null) ? 'sum' : 'none'
  }
  return 'none'
}

// Build the full TOTAL row (one cell per column) over the given rows. `firstLabel` lands in
// the first NON-aggregated column (typically the leading label column).
//
// `aggs` pre-resolves how each column aggregates instead of deciding from `rows`. Only
// `computeTotalScope` passes it, and it has to: `resolveAgg` reads the rows it is given to decide
// whether a numeric-looking column is really summable, so a filter that leaves ZERO rows resolves
// 'none' and blanks the filtered figure while the unfiltered one beside it still sums. Two figures
// labelled the same aggregation must BE the same aggregation, so the decision is made once over the
// superset and handed to both.
export function computeTotalRow(
  cols: TotalsCol[], rows: TotalsRow[], firstLabel = 'TOTAL', aggs?: Agg[],
): TotalCell[] {
  const resolved = aggs && aggs.length === cols.length ? aggs : cols.map(c => resolveAgg(c, rows))
  const labelIdx = resolved.findIndex(a => a === 'none')   // -1 if every column aggregates
  return cols.map((col, i) => {
    const isMoney = isMoneyCol(col)
    const agg = resolved[i]
    if (agg === 'sum') {
      const total = rows.reduce((s, r) => s + (toNumber(col.get(r)) ?? 0), 0)
      return { text: isMoney ? moneyText(total) : numberFmt(total), raw: total, isMoney, agg }
    }
    if (agg === 'avg') {
      const vals = rows.map(r => toNumber(col.get(r))).filter((v): v is number => v != null)
      if (!vals.length) return { text: '', raw: '', isMoney, agg }
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length
      return { text: isMoney ? moneyText(mean) : numberFmt(mean), raw: mean, isMoney, agg }
    }
    if (i === labelIdx) return { text: firstLabel, raw: firstLabel, isMoney, agg }
    return { text: '', raw: '', isMoney, agg }
  })
}

// ── A total says nothing until you know WHICH ROWS it is over (owner ask 2026-10-29) ────────────
//
// OWNER ASK, verbatim: *"need the total below the hours on the time clock when filtered and without
// filter"*.
//
// THE CLASS, not the instance. The instance is the Hours column on Time Clock. The class is that
// EVERY total in this product is a total of the rows a filter happened to leave visible, and none of
// them says so. A reviewer narrows to one store, reads 88.20 h, and cannot tell from the screen
// whether that is the whole fortnight or a tenth of it — so a filter left on by accident reads
// exactly like a quiet month. Asking for "filtered and without filter" is asking a total to name its
// own population, and the answer belongs here, in the one home for total arithmetic, not in one
// screen's markup.
//
// WHAT "WITHOUT FILTER" HONESTLY MEANS. The widest population this side can see: every row the page
// LOADED. A page's server query is itself a narrowing — Time Clock fetches one date range — so this
// is "everything in range", never "everything ever", and a caller must label it that way. Claiming
// otherwise would make the second figure the same kind of unsourced number as the first.
//
// `narrowed` is the whole point of the pair. When no filter is cutting anything the two figures are
// identical, and a renderer that prints both anyway teaches the reader to stop looking at either.

export interface TotalScopeCell extends TotalCell {
  /** The same aggregation over every row the page LOADED, not only the visible ones. */
  allText: string
  allRaw: string | number
}

export interface TotalScope {
  /** One cell per column, each carrying the visible figure AND the loaded-set figure. */
  cells: TotalScopeCell[]
  /** Rows the filters left visible. */
  shown: number
  /** Rows the page loaded. */
  loaded: number
  /** True when a filter is actually cutting rows — the only case where the second figure says anything. */
  narrowed: boolean
}

/**
 * The TOTAL row for `shownRows`, with the same aggregation over `loadedRows` beside it.
 *
 * `loadedRows` must be the SUPERSET `shownRows` was filtered out of. The aggregation decision is
 * taken once over that superset and applied to both figures — see `computeTotalRow`'s `aggs`.
 */
export function computeTotalScope(
  cols: TotalsCol[], shownRows: TotalsRow[], loadedRows: TotalsRow[], firstLabel = 'TOTAL',
): TotalScope {
  const shown = shownRows || []
  const loaded = loadedRows || []
  // Resolved over the superset so the two figures can never be different aggregations of one column.
  const aggs = cols.map(c => resolveAgg(c, loaded))
  const here = computeTotalRow(cols, shown, firstLabel, aggs)
  const all = computeTotalRow(cols, loaded, firstLabel, aggs)
  return {
    cells: here.map((cell, i) => ({ ...cell, allText: all[i].text, allRaw: all[i].raw })),
    shown: shown.length,
    loaded: loaded.length,
    narrowed: shown.length !== loaded.length,
  }
}
