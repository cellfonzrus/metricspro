// Click-a-header SORTING for report tables (OWNER DIRECTIVE 2026-08-10: "need the sort function by
// clicking on the header for all reports"). Framework-free so it is unit-provable
// (scratchpad/prove_table_sort.mjs) and shared identically by <ReportShell>, the hand-rolled report
// tables, and any future surface — one sort rule fleet-wide, not a per-page reimplementation.
//
// DESIGN NOTES
//  • Sorting is a VIEW concern: it reorders the rows a page already has, never re-queries. A page that
//    paginates server-side must sort server-side too — `sortRows` cannot see rows it wasn't given.
//  • A TOTAL/subtotal row must never be dragged into the middle of the table by a sort. Pages append
//    totals AFTER sorting (see `sortRows` callers) — this module only ever sees detail rows.
//  • Mixed-type columns are the norm here (a money column holding null for "not configured", a count
//    column holding '—'). `compareValues` puts EMPTIES LAST in both directions on purpose: "no answer"
//    is not "zero", and flipping the direction should not fill the top of the table with blanks.
//  • Numbers compare numerically even when they arrive as formatted strings ('$1,234.50', '12.5%'),
//    because several report tables carry pre-formatted cells; dates compare chronologically via the
//    same YYYY-MM-DD normalization `standard-filters` uses. Everything else is a locale string compare.

export type SortDir = 'asc' | 'desc'
export type SortState = { field: string; dir: SortDir } | null

const isNil = (v: any) => v === null || v === undefined
const s = (v: any) => String(v).trim()

/** '' / null / undefined / '—' / '-' / 'n/a' all count as EMPTY (sorted last, both directions). */
export function isEmptyCell(v: any): boolean {
  if (isNil(v)) return true
  const t = s(v).toLowerCase()
  return t === '' || t === '—' || t === '-' || t === '–' || t === 'n/a' || t === 'na'
}

/** A number when the value IS one, or is a formatted one ('$1,234.50', '(1,234.50)', '12.5%', '1,234').
 *  Returns null for anything else — including a string that merely STARTS with digits ('104-08 Lefferts
 *  Blvd' is a store name, not the number 104), which is why the whole-string match is anchored. */
export function asNumber(v: any): number | null {
  if (typeof v === 'number') return isFinite(v) ? v : null
  if (isNil(v)) return null
  let t = s(v)
  if (!t) return null
  let neg = false
  if (/^\(.*\)$/.test(t)) { neg = true; t = t.slice(1, -1).trim() }       // (1,234.50) = negative
  t = t.replace(/[$,\s]/g, '').replace(/%$/, '')
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(t)) return null
  const n = Number(t)
  if (!isFinite(n)) return null
  return neg ? -n : n
}

/** A YYYY-MM-DD key when the value looks like a date, else null. Mirrors `standard-filters.ymd`'s rule
 *  but deliberately does NOT fall back to `new Date(...)`: that parses '5' and 'August' into dates and
 *  would silently sort a plain text column chronologically. */
export function asDateKey(v: any): string | null {
  if (isNil(v)) return null
  const t = s(v)
  let m = t.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/)
  if (m) return `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`
  m = t.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})/)                        // US M/D/YYYY
  if (m) return `${m[3]}-${m[1].padStart(2, '0')}-${m[2].padStart(2, '0')}`
  return null
}

/** The shared cell comparator. EMPTIES LAST regardless of direction (see the header note). */
export function compareValues(a: any, b: any, dir: SortDir = 'asc'): number {
  const ea = isEmptyCell(a), eb = isEmptyCell(b)
  if (ea && eb) return 0
  if (ea) return 1                       // empty sinks, in BOTH directions
  if (eb) return -1
  const sign = dir === 'desc' ? -1 : 1
  const na = asNumber(a), nb = asNumber(b)
  if (na !== null && nb !== null) return na === nb ? 0 : (na < nb ? -1 : 1) * sign
  const da = asDateKey(a), db = asDateKey(b)
  if (da !== null && db !== null) return da === db ? 0 : (da < db ? -1 : 1) * sign
  return s(a).localeCompare(s(b), undefined, { numeric: true, sensitivity: 'base' }) * sign
}

/** Stable sort of `rows` by `sort.field`, reading each cell through `get`. `sort === null` (or a field
 *  with no accessor) returns the ORIGINAL array reference — the report's own default order, untouched.
 *  Never mutates the input. */
export function sortRows<T>(rows: T[], sort: SortState, get: (row: T, field: string) => any): T[] {
  if (!sort || !sort.field || !rows || rows.length < 2) return rows
  return rows
    .map((row, i) => ({ row, i }))
    .sort((x, y) => {
      const c = compareValues(get(x.row, sort.field), get(y.row, sort.field), sort.dir)
      return c !== 0 ? c : x.i - y.i          // stable: ties keep the report's own order
    })
    .map(x => x.row)
}

/** Header-click transition: a NEW column starts ascending; the SAME column toggles asc → desc → off
 *  (back to the report's own default order). Three states, so a sort is always undoable without a reload. */
export function nextSort(current: SortState, field: string): SortState {
  if (!current || current.field !== field) return { field, dir: 'asc' }
  if (current.dir === 'asc') return { field, dir: 'desc' }
  return null
}

/** The affordance drawn in a header cell: '▲' / '▼' when sorted, a dimmed '↕' otherwise. */
export function sortIndicator(sort: SortState, field: string): string {
  if (!sort || sort.field !== field) return '↕'
  return sort.dir === 'asc' ? '▲' : '▼'
}
