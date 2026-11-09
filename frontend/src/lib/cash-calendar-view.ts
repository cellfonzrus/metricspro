// CASH CALENDAR — the two pure display facts the page dereferences (§COMMCALC-56).
//
// WHY THESE ARE NOT IN THE PAGE. Both are rules that must not drift from the backend home that owns
// them, and both are arithmetic a screenshot cannot prove:
//
//  · CASH_CAL_BASIS_WORD mirrors `payables/receipt_schedule.BASIS_WORDS`. A sentence per cadence
//    typed into the page would be a second copy of the backend's vocabulary (§19.18), and a basis the
//    page has never seen would then render as nothing at all. Here, an unknown basis falls through to
//    ITSELF — the reader sees the raw key rather than an empty cell that implies no rhythm.
//  · cashCalWeeks turns a flat run of day rows into calendar WEEKS beginning on Monday, padding the
//    first and last week with nulls so the grid has seven columns. The off-by-one this prevents is
//    the one that silently shifts every date by a day: JavaScript's getDay() is Sunday-indexed while
//    the weekday the backend declares (and the header this page draws) is Monday-indexed.
//
// Pure, no React, no fetch — unit-tested in cash-calendar-view.test.ts.

/** A day cell the calendar can draw. A `null` is a pad cell before the first or after the last day. */
export type CashCalDay = { date: string } & Record<string, unknown>

/** The basis a figure was dated by, in words. Mirrors receipt_schedule.BASIS_WORDS exactly; an
 *  unknown key falls through to itself rather than vanishing. */
export const CASH_CAL_BASIS_WORD: Record<string, string> = {
  statement_field: "the carrier's own statement date",
  weekly_declared: "the carrier's declared pay weekday",
  semi_monthly_declared: "the carrier's declared pay days of the month",
  monthly_declared: "the carrier's declared pay day of the month",
  net_days_declared: "the carrier's declared days-after-earning terms",
  cadence_undeclared: 'not measured — no payout cycle declared',
  day_undeclared: 'not measured — the pay day is not declared',
  no_date: 'not measured — the row carries no date',
  mixed: 'more than one payout cycle',
}

/** Monday = 0 … Sunday = 6, for an ISO date string. Date.getDay() is Sunday-indexed, which is the
 *  off-by-one that would shift every cell one column left. */
export function mondayIndex(iso: string): number {
  const d = new Date(`${String(iso).slice(0, 10)}T00:00:00Z`)
  if (isNaN(d.getTime())) return 0
  return (d.getUTCDay() + 6) % 7
}

/** The day rows laid out as calendar weeks of exactly seven cells, Monday first.
 *
 *  Pads the leading and trailing week with nulls so the grid never reflows, and never reorders or
 *  drops a day: every row handed in appears exactly once, on its own weekday column. A gap in the
 *  middle of the run (which the backend does not produce, because it emits every day in the span) is
 *  left as a pad rather than being closed up, so a missing day reads as missing. */
export function cashCalWeeks<T extends CashCalDay>(days: T[]): Array<Array<T | null>> {
  // A row with no USABLE date is left out rather than placed on Monday: a cell drawn on the wrong
  // day is money attributed to a day it does not belong to, which is worse than a cell missing.
  const rows = (days || []).filter(
    d => d && typeof d.date === 'string' && /^\d{4}-\d{2}-\d{2}/.test(d.date))
  if (!rows.length) return []
  const weeks: Array<Array<T | null>> = []
  let week: Array<T | null> = new Array(7).fill(null)
  let lastIdx = -1
  for (const d of rows) {
    const idx = mondayIndex(d.date)
    // A new week starts whenever the weekday column goes backwards or repeats.
    if (idx <= lastIdx && lastIdx !== -1) {
      weeks.push(week)
      week = new Array(7).fill(null)
    }
    week[idx] = d
    lastIdx = idx
  }
  weeks.push(week)
  return weeks
}
