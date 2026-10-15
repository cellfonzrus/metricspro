// REP INCENTIVE — A MONTH RANGE ON ONE PAGE (owner 2026-09-25): "the range for multiple months should be
// there to display the commission for teh months selected in different rowqn on one page".
//
// The rows come from GET /commcalc/commissions-range, which LOOPS the single-month report's own handler
// (get_commissions) over the months account/_period.month_range enumerates — so every rep row here IS the
// row that month shows alone. This module is PURE and DISPLAY-ONLY: it groups those rows by month for the
// table and sums the ones on screen (the single-month page sums its filtered rows the same way). It
// imports nothing, so frontend/tools/rep-incentive-range-proof.mjs transpiles and proves it directly.

/** The widest range the page asks for — the backend's rep_incentive_range.MAX_MONTHS (12). */
export const RANGE_MAX_MONTHS = 12

export type RangeRow = {
  range_month: string
  // WHETHER THIS ROW'S NAMED PAY SLICES ARE FIGURES AT ALL (§COMMCALC-09) — the server's verdict,
  // named here so a range table reads it rather than treating a never-computed subtotal as $0.00.
  payout_slice_basis?: string | null
  payout_slice_reason?: string | null
  [k: string]: any
}

export const RANGE_MONEY = ['subtotal', 'total_payout', 'final_payout'] as const
export const RANGE_COUNTS = ['premium_acts', 'byod_acts', 'upgrade_acts'] as const

const num = (v: any): number => { const n = Number(v); return Number.isFinite(n) ? n : 0 }
const cents = (n: number): number => Math.round((n + Number.EPSILON) * 100) / 100

export type MonthBlock = { month: string; rows: RangeRow[]; totals: Record<string, number> }

/** The NAMED SLICES among RANGE_MONEY — the fields a plan-mode row only carries when the engine that
 *  wrote it itemised the pay (§COMMCALC-09). `total_payout` and `final_payout` are not among them:
 *  every writer has always written those, so they sum unconditionally and a month's total stays exact. */
const RANGE_SLICES: readonly string[] = ['subtotal']

/** Does this row's breakdown exist at all? READS the server's stamped verdict
 *  (`commcalc/payout_slice_evidence`, the one home) — the test is not re-derived here, and a payload
 *  without the stamp reads as recorded so an older server changes nothing. */
const sliceRecorded = (r: RangeRow): boolean =>
  String(r?.payout_slice_basis ?? 'recorded') === 'recorded'

function totalsOf(rows: RangeRow[]): Record<string, number> {
  const t: Record<string, number> = { reps: rows.length }
  for (const k of RANGE_MONEY) {
    // A never-computed slice is NOT added in as zero: a window covering a month calculated before the
    // breakdown existed used to print a Subtotal short by that month and say nothing.
    const src = RANGE_SLICES.includes(k) ? rows.filter(sliceRecorded) : rows
    t[k] = cents(src.reduce((s, r) => s + num(r[k]), 0))
    if (RANGE_SLICES.includes(k)) t[`${k}_rows_not_recorded`] = rows.length - src.length
  }
  for (const k of RANGE_COUNTS) t[k] = rows.reduce((s, r) => s + Math.trunc(num(r[k])), 0)
  return t
}

/** One block per month in `months` order (a month with no rows still shows, empty), each with the
 *  rows the backend returned for it — order kept — and their sums; plus the grand total. PURE. */
export function monthBlocks(rows: RangeRow[], months: string[]): { blocks: MonthBlock[]; grand: Record<string, number> } {
  const by = new Map<string, RangeRow[]>()
  for (const m of months || []) by.set(m, [])
  for (const r of rows || []) {
    const m = String(r?.range_month ?? '')
    if (!by.has(m)) by.set(m, [])
    by.get(m)!.push(r)
  }
  const blocks = Array.from(by.entries()).map(([month, rs]) => ({ month, rows: rs, totals: totalsOf(rs) }))
  return { blocks, grand: totalsOf(blocks.flatMap(b => b.rows)) }
}

/** 'YYYY-MM' of today's month minus `back` months — the default window's bounds. PURE given `today`. */
export function monthInput(today: Date, back = 0): string {
  const d = new Date(today.getFullYear(), today.getMonth() - back, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

/** How many months [from, to] spans (inclusive); 0 when either bound is not 'YYYY-MM' or reversed. */
export function spanMonths(from: string, to: string): number {
  const p = (s: string) => { const m = /^(\d{4})-(\d{2})$/.exec(String(s || '')); return m ? [Number(m[1]), Number(m[2])] : null }
  const a = p(from), b = p(to)
  if (!a || !b) return 0
  const n = (b[0] - a[0]) * 12 + (b[1] - a[1]) + 1
  return n > 0 ? n : 0
}
