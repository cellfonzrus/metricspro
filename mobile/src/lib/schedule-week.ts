/**
 * The work week a schedule is paged by — THIS TENANT'S week, never "Monday because we assumed so".
 *
 * WHY THIS IS NOT `daterange.rangeWeek()`. That one is a ROLLING seven days ending today, which is
 * the right shape for a dashboard look-back and the wrong shape for a schedule: a rota is read as a
 * calendar week you step forward and back through, and the two must not be confused.
 *
 * WHY `startDow` IS A REQUIRED ARGUMENT. `storeops.tenants.work_week_start_dow` is a tenant's own
 * declaration (0 = Monday across the product; one tenant here runs Thursday = 3), and the owner
 * directive of 2026-10-30 is explicit that a caller which has not asked the org must not be able to
 * answer. A default parameter of 0 would let a screen render the wrong week silently and look right
 * while doing it. The ONE place a fallback is chosen is where the setting is fetched
 * (`GET /core/tenant-settings` → `settings.work_week_start_dow`), stated once, the same source and
 * the same fallback the web schedule page uses — so there is one convention, not two.
 *
 * The date arithmetic is `daterange.addDays` and `period.todayISO`; this module adds none of its own.
 */

import { addDays } from './daterange'
import { todayISO } from './period'

export type Week = { start: string; end: string }

/** 0 = Monday … 6 = Sunday — the product's convention, stated here so a reader need not guess. */
export const MONDAY = 0

/** A date's day-of-week on the product's 0=Monday scale (JS gives 0=Sunday). */
export function dowOf(iso: string): number {
  const [y, m, d] = iso.split('-').map((x) => parseInt(x, 10))
  const js = new Date(y, (m || 1) - 1, d || 1).getDay()
  return js === 0 ? 6 : js - 1
}

/** A declared `work_week_start_dow` as a usable 0–6, or Monday when it is not a readable number.
 *  Junk resolves to the HOUSE POLICY, never to today — a schedule whose week starts "now" is
 *  unreadable, and a policy is exactly what a house default is right for. */
export function startDowOf(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(value)
  if (value === null || value === undefined || value === '' || !Number.isFinite(n)) return MONDAY
  return ((Math.trunc(n) % 7) + 7) % 7
}

/** The week (inclusive [start, end]) containing `iso`, for a tenant whose week starts `startDow`. */
export function weekOf(startDow: number, iso?: string): Week {
  const day = iso || todayISO()
  const delta = (dowOf(day) - startDowOf(startDow) + 7) % 7
  const start = addDays(day, -delta)
  return { start, end: addDays(start, 6) }
}

/** The next / previous week. `n` may be any integer, so a caller can jump. */
export function shiftWeek(week: Week, n: number): Week {
  const start = addDays(week.start, 7 * n)
  return { start, end: addDays(start, 6) }
}

/** The seven dates of a week, in order — what a day-by-day schedule list renders over. */
export function daysOf(week: Week): string[] {
  return Array.from({ length: 7 }, (_, i) => addDays(week.start, i))
}

/** Is this the week containing today, for this tenant? Drives the "This week" affordance. */
export function isCurrentWeek(startDow: number, week: Week): boolean {
  return weekOf(startDow).start === week.start
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DAYS_FROM_MONDAY = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/** "Mon" … "Sun" for a date — on the product's scale, so it cannot disagree with `dowOf`. */
export function dayLabel(iso: string): string {
  return DAYS_FROM_MONDAY[dowOf(iso)]
}

/** "Nov 3 – 9" / "Oct 29 – Nov 4" — a week's label, month repeated only when it changes. */
export function weekLabel(week: Week): string {
  const parts = (iso: string) => {
    const [, m, d] = iso.split('-').map((x) => parseInt(x, 10))
    return { mon: MONTHS[(m || 1) - 1], day: d || 1 }
  }
  const a = parts(week.start)
  const b = parts(week.end)
  return a.mon === b.mon ? `${a.mon} ${a.day} – ${b.day}` : `${a.mon} ${a.day} – ${b.mon} ${b.day}`
}

/** "7:00a – 3:30p" from two stored times, or null when the shift carries no times at all.
 *  A shift with no times is a real row (an all-day or unspecified assignment) and must render as
 *  itself rather than as "12:00a – 12:00a". */
export function timeRange(start: unknown, end: unknown): string | null {
  const one = (v: unknown): string | null => {
    const s = typeof v === 'string' ? v.trim() : ''
    const m = /^(\d{1,2}):(\d{2})/.exec(s)
    if (!m) return null
    const h = parseInt(m[1], 10)
    const min = m[2]
    if (!Number.isFinite(h) || h > 23) return null
    const suffix = h < 12 ? 'a' : 'p'
    const h12 = h % 12 === 0 ? 12 : h % 12
    return min === '00' ? `${h12}${suffix}` : `${h12}:${min}${suffix}`
  }
  const a = one(start)
  const b = one(end)
  if (!a && !b) return null
  if (a && b) return `${a} – ${b}`
  return a || b
}
