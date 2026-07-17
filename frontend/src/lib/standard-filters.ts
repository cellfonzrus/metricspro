// Standard universal report filters (RULE FIVE, §3d) — the PURE logic behind <StandardFilterBar>.
// Core set: period (month OR date-range) · store(s) multi · market · rep/employee(s) multi. This module
// holds ONLY framework-free functions (no React) so it is unit-provable (scratchpad/prove_standard_filters.mjs)
// and reused identically by every report surface that adopts the bar. Filtering is client-side over the rows
// a page already loaded (which are already org-scoped by the caller's JWT) — what-you-see-is-what-exports.

export type StandardFilterValue = {
  /** 'YYYY-MM' (month mode) or 'YYYY-MM-DD' (range start / single day). '' = no period filter. */
  period?: string
  /** 'YYYY-MM-DD' range end (inclusive). Only meaningful in range mode. */
  periodTo?: string
  stores: string[]
  markets: string[]
  reps: string[]
}

export type FieldAccessors<T> = {
  store?: (r: T) => string | null | undefined
  market?: (r: T) => string | null | undefined
  rep?: (r: T) => string | null | undefined
  /** Returns a row date as anything Date-parseable / a YYYY-MM-DD-ish string, for the period filter. */
  date?: (r: T) => string | null | undefined
}

const norm = (v: any): string => (v == null ? '' : String(v)).trim()

/** A row date normalized to YYYY-MM-DD (best-effort; '' when unparseable). */
export function ymd(v: any): string {
  const s = norm(v)
  if (!s) return ''
  const m = s.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/)
  if (m) return `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`
  const d = new Date(s)
  return isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10)
}

export function emptyStandardFilter(period = ''): StandardFilterValue {
  return { period, periodTo: '', stores: [], markets: [], reps: [] }
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
  if (sel.stores.length && acc.store && !sel.stores.includes(norm(acc.store(row)))) return false
  if (sel.markets.length && acc.market && !sel.markets.includes(norm(acc.market(row)))) return false
  if (sel.reps.length && acc.rep && !sel.reps.includes(norm(acc.rep(row)))) return false
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
  const stores = new Set<string>(), markets = new Set<string>()
  const reps = new Map<string, string>()   // rep name → email (best-effort disambiguation)
  for (const r of rows) {
    if (acc.store) { const v = norm(acc.store(r)); if (v) stores.add(v) }
    if (acc.market) { const v = norm(acc.market(r)); if (v) markets.add(v) }
    if (acc.rep) { const v = norm(acc.rep(r)); if (v && !reps.has(v)) reps.set(v, norm(acc.repEmail?.(r))) }
  }
  return {
    stores: [...stores].sort(),
    markets: [...markets].sort(),
    reps: [...reps.entries()].sort((a, b) => a[0].localeCompare(b[0]))
      .map(([id, email]) => (email ? { id, label: id, sublabel: email } : { id, label: id })),
  }
}
