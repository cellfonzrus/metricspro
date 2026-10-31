// Standard universal report filters (RULE FIVE, §3d) — the PURE logic behind <StandardFilterBar>.
// Core set: period (month OR date-range) · store(s) multi · market · rep/employee(s) multi. This module
// holds ONLY framework-free functions (no React) so it is unit-provable (scratchpad/prove_standard_filters.mjs)
// and reused identically by every report surface that adopts the bar. Filtering is client-side over the rows
// a page already loaded (which are already org-scoped by the caller's JWT) — what-you-see-is-what-exports.

// SORTING IS NOT RE-DERIVED HERE. `lib/table-sort` has been the one home for "how two cells compare"
// and "what a header click does next" since OWNER DIRECTIVE 2026-08-10; this module only gives that
// rule a place in the shared bar (index §CORE-28). A second comparator would drift from the headers.
import { nextSort, sortRows, type SortState } from './table-sort'

export type { SortState } from './table-sort'

export type StandardFilterValue = {
  /** 'YYYY-MM' (month mode) or 'YYYY-MM-DD' (range start / single day). '' = no period filter. */
  period?: string
  /** 'YYYY-MM-DD' range end (inclusive). Only meaningful in range mode. */
  periodTo?: string
  stores: string[]
  markets: string[]
  reps: string[]
  /** HOW THE ROWS ARE ORDERED (owner ask 2026-10-31, §CORE-28). One state for the whole page, so the
   *  bar's "Sort by" dropdown and a click on a sortable column header can never disagree about the
   *  order — they read and write this. `null`/absent = the order the report itself produced. */
  sort?: SortState
}

/** A SORTABLE FIELD, DECLARED BY THE PAGE. The bar is handed these; it has no list of its own and no
 *  default — a default list of column names is one report's (and in the worst case one tenant's)
 *  vocabulary answering for everybody, which is the class the owner directive of 2026-10-30 bans.
 *  `kind` only decides how the two directions are WORDED; the comparison itself is `table-sort`'s,
 *  which infers money/date/text from the cell. */
export type SortFieldKind = 'text' | 'number' | 'date'

/** A row, as a declaration sees it: a bag of named cells. DELIBERATELY NOT GENERIC over the page's
 *  own row interface — the declarations travel through the shared bar (which renders labels and never
 *  reads a cell), and a generic would make every adopter cast at that boundary. A getter therefore
 *  reads a named cell and returns it; it does no arithmetic, because comparing is `table-sort`'s job. */
export type SortRow = Record<string, unknown>

export type SortFieldDecl = {
  field: string
  label: string
  kind?: SortFieldKind
  get: (row: SortRow) => unknown
}

const DIR_WORDS: Record<SortFieldKind, [string, string]> = {
  text: ['A\u2013Z', 'Z\u2013A'],
  number: ['low \u2192 high', 'high \u2192 low'],
  date: ['oldest first', 'newest first'],
}

/** Reads one cell THROUGH the page's declaration. A field nobody declared has no cell — it returns
 *  undefined rather than reaching into the row, so a sort can never read a column the page did not
 *  offer (the same posture as the option lists: what you can filter by is what you were given). */
export function sortFieldCell<T>(fields: SortFieldDecl[], row: T, field: string): unknown {
  const f = (fields || []).find(x => x.field === field)
  return f ? f.get(row as SortRow) : undefined
}

/** The dropdown's options: every declared field, in both directions, worded for what it holds.
 *  EMPTY when the page declared nothing — nothing declared means nothing offered, and the bar then
 *  renders no sort control at all rather than falling back to somebody else's columns. */
export function sortSelectOptions(fields: SortFieldDecl[]): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = []
  for (const f of fields || []) {
    const [up, down] = DIR_WORDS[f.kind || 'text']
    out.push({ value: `${f.field}:asc`, label: `${f.label} (${up})` })
    out.push({ value: `${f.field}:desc`, label: `${f.label} (${down})` })
  }
  return out
}

/** The `<select>` value for a sort state; '' means "the report's own order". */
export function sortSelectValue(sort: SortState | undefined): string {
  return sort && sort.field ? `${sort.field}:${sort.dir}` : ''
}

/** '' / 'field' / 'field:asc' / 'field:desc' -> a sort state. An unrecognised direction reads as
 *  ascending rather than throwing: the control is a view, and a bad value must not blank a report. */
export function parseSortSelect(v: string | undefined): SortState {
  const s = norm(v)
  if (!s) return null
  const [field, dir] = s.split(':')
  if (!field) return null
  return { field, dir: dir === 'desc' ? 'desc' : 'asc' }
}

/** True when the rows are being REORDERED. Deliberately NOT part of `isStandardFilterActive`: a sort
 *  hides nothing, and a page that counts it as a filter would tell the user rows were left out. */
export function isStandardSortActive(sel: StandardFilterValue): boolean {
  return !!(sel?.sort && sel.sort.field)
}

/** A header click, applied to the shared value: asc -> desc -> off on the same column, ascending on a
 *  new one. The transition is `table-sort.nextSort` — the headers' own rule, not a second copy. */
export function toggleStandardSort(sel: StandardFilterValue, field: string): StandardFilterValue {
  return { ...sel, sort: nextSort(sel?.sort ?? null, field) }
}

/** THE ORDERED VIEW. Rows already filtered, ordered by `sel.sort` through the page's declaration.
 *  Returns the SAME array reference when there is nothing to do (no sort, nothing declared, or a
 *  field this page does not declare), so a report's own order is the default and cheap. */
export function sortStandard<T>(rows: T[], sel: StandardFilterValue, fields: SortFieldDecl[]): T[] {
  const sort = sel?.sort ?? null
  if (!sort || !sort.field) return rows
  if (!(fields || []).some(f => f.field === sort.field)) return rows
  return sortRows(rows, sort, (row: T, field: string) => sortFieldCell(fields, row, field))
}

export type FieldAccessors<T> = {
  store?: (r: T) => string | null | undefined
  market?: (r: T) => string | null | undefined
  rep?: (r: T) => string | null | undefined
  /** Returns a row date as anything Date-parseable / a YYYY-MM-DD-ish string, for the period filter. */
  date?: (r: T) => string | null | undefined
}

const norm = (v: any): string => (v == null ? '' : String(v)).trim()

/** Case-insensitive comparison key (OWNER DIRECTIVE 2026-07-18 "do a case insensitive match"). Trim +
 *  lowercase. Used ONLY for equality/dedup — DISPLAY labels always keep their original (first-seen) casing.
 *  Consequence: case-variant duplicates of one store/rep collapse to a single option, and picking it now
 *  matches every casing of that value (previously the other variant's rows were silently excluded). For
 *  data whose casing is already consistent this is a no-op (fold(x) vs fold(y) === x vs y). */
const foldKey = (v: any): string => norm(v).toLowerCase()

/** A row date normalized to YYYY-MM-DD (best-effort; '' when unparseable). */
export function ymd(v: any): string {
  const s = norm(v)
  if (!s) return ''
  const m = s.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/)
  if (m) return `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`
  const d = new Date(s)
  return isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10)
}

/** The value a MONTH DROPDOWN should show for the current selection, given the month options it is
 *  rendering. One home because the bar previously decided this inline with two contradictory rules: it
 *  selected `period` only when it was seven characters long (ISO 'YYYY-MM'), while also appending an
 *  option for a `period` the list does not carry — so a surface whose months are spelled 'August 2026'
 *  (a carrier statement's own spelling, which this platform stores as a period) rendered its dropdown
 *  with NOTHING selected even though the month was chosen and the report below it was that month's.
 *
 *  The rule: show whatever is selected. '' means "no month chosen" and is the only empty case. ISO
 *  periods are unaffected — `monthSelectValue('2026-08', [...])` is '2026-08' either way — so this is
 *  a no-op for every adopter whose months are ISO and a fix for every adopter whose months are not. */
export function monthSelectValue(period: string | undefined): string {
  return norm(period)
}

/** True when a chosen month is NOT one of the dropdown's options and the dropdown must therefore carry
 *  an option of its own for it — otherwise the browser has nothing to select and the control renders
 *  blank over a report that is showing that month. */
export function monthNeedsOwnOption(period: string | undefined, periods?: string[]): boolean {
  const p = norm(period)
  return !!p && !(periods || []).includes(p)
}

export function emptyStandardFilter(period = ''): StandardFilterValue {
  return { period, periodTo: '', stores: [], markets: [], reps: [], sort: null }
}

/** True when any of the four core filters is narrowing the result. Period alone counts only in range mode
 *  (in month mode `period` usually drives the server query, not a client narrowing) — callers pass
 *  `countPeriod` to include it. */
export function isStandardFilterActive(sel: StandardFilterValue, countPeriod = false): boolean {
  return (
    sel.stores.length > 0 || sel.markets.length > 0 || sel.reps.length > 0 ||
    (countPeriod && (!!sel.period || !!sel.periodTo))
  )
}

function periodOk(rowDate: string, sel: StandardFilterValue): boolean {
  const from = norm(sel.period)
  const to = norm(sel.periodTo)
  if (!from && !to) return true
  const d = ymd(rowDate)
  if (!d) return false                                   // a period filter is set but the row has no date → excluded
  if (from && to) return d >= ymd(from) && d <= ymd(to)  // inclusive range
  if (from && from.length === 7) return d.slice(0, 7) === from   // month match
  if (from && from.length >= 10) return d >= ymd(from)   // from a start date onward
  if (to) return d <= ymd(to)
  return true
}

export function matchesStandardFilter<T>(row: T, sel: StandardFilterValue, acc: FieldAccessors<T>): boolean {
  // Case-insensitive membership (foldKey): a selection stored with any casing matches a row of any casing,
  // so a case-variant of a selected store/rep is INCLUDED (was silently excluded), and a pre-fix selection
  // held in component state (original casing) still round-trips after option dedupe.
  if (sel.stores.length && acc.store) { const k = foldKey(acc.store(row)); if (!sel.stores.some(s => foldKey(s) === k)) return false }
  if (sel.markets.length && acc.market) { const k = foldKey(acc.market(row)); if (!sel.markets.some(m => foldKey(m) === k)) return false }
  if (sel.reps.length && acc.rep) { const k = foldKey(acc.rep(row)); if (!sel.reps.some(r => foldKey(r) === k)) return false }
  if (acc.date && (sel.period || sel.periodTo) && !periodOk(acc.date(row) as string, sel)) return false
  return true
}

export function filterRows<T>(rows: T[], sel: StandardFilterValue, acc: FieldAccessors<T>): T[] {
  return rows.filter(r => matchesStandardFilter(r, sel, acc))
}

/** Distinct, sorted option lists derived from the ALREADY-org-scoped loaded rows — a pick-don't-type
 *  source for pages with no dedicated roster endpoint (the options are exactly the values present, so a
 *  filter can never reference data outside the tenant). Reps carry an email sublabel when available. */
export function optionsFromRows<T>(
  rows: T[], acc: FieldAccessors<T> & { repEmail?: (r: T) => string | null | undefined },
): { stores: string[]; markets: string[]; reps: { id: string; label: string; sublabel?: string }[] } {
  // foldKey → first-seen ORIGINAL-casing display value. Dedup is case-insensitive (one "Store A"/"store a"
  // option, not two) but the label shown is whatever casing appeared first in the rows.
  const stores = new Map<string, string>(), markets = new Map<string, string>()
  const reps = new Map<string, { label: string; email: string }>()  // foldKey → first-seen name + email
  for (const r of rows) {
    if (acc.store) { const v = norm(acc.store(r)); if (v) { const k = foldKey(v); if (!stores.has(k)) stores.set(k, v) } }
    if (acc.market) { const v = norm(acc.market(r)); if (v) { const k = foldKey(v); if (!markets.has(k)) markets.set(k, v) } }
    if (acc.rep) { const v = norm(acc.rep(r)); if (v) { const k = foldKey(v); if (!reps.has(k)) reps.set(k, { label: v, email: norm(acc.repEmail?.(r)) }) } }
  }
  return {
    stores: [...stores.values()].sort(),
    markets: [...markets.values()].sort(),
    reps: [...reps.values()].sort((a, b) => a.label.localeCompare(b.label))
      .map(({ label, email }) => (email ? { id: label, label, sublabel: email } : { id: label, label })),
  }
}
