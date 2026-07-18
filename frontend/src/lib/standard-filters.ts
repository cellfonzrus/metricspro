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
