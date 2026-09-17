// ── Date-range helpers for dashboard filters ─────────────────────────────────────────────────────
// A DateRange is an inclusive [from, to] pair of local YYYY-MM-DD strings. The dashboards offer quick
// presets (Today / This week / This month) plus a custom range; endpoints that filter server-side
// (exec-mtd) take from/to directly, and the ones we filter client-side (sales) compare each row's
// date against the range. Everything is local-time to match how a store thinks about "today".

import { currentPeriod, todayISO } from './period'

export type DateRange = { from: string; to: string }
export type RangePreset = 'today' | 'week' | 'month' | 'custom'

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}
function ymd(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/** Shift a YYYY-MM-DD by n days (local). */
export function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split('-').map((x) => parseInt(x, 10))
  const date = new Date(y, (m || 1) - 1, d || 1)
  date.setDate(date.getDate() + n)
  return ymd(date)
}

/** The YYYY-MM month a date falls in. */
export function monthOf(iso: string): string {
  return iso.slice(0, 7)
}

export function rangeToday(): DateRange {
  const t = todayISO()
  return { from: t, to: t }
}

/** Rolling 7 days ending today (keeps it inside a single month most of the time; callers that key on
 *  a month clamp with `monthOf(to)`). */
export function rangeWeek(): DateRange {
  const t = todayISO()
  return { from: addDays(t, -6), to: t }
}

/** Month-to-date: first of the current month through today. */
export function rangeMonth(): DateRange {
  const t = todayISO()
  return { from: `${currentPeriod()}-01`, to: t }
}

export function presetRange(p: RangePreset): DateRange {
  return p === 'today' ? rangeToday() : p === 'week' ? rangeWeek() : rangeMonth()
}

/** "Sep 17" / "Sep 1 – 17" / "Sep 1 – Oct 3" — a compact human label for a range. */
export function rangeLabel(r: DateRange): string {
  const fmt = (iso: string, withMonth: boolean) => {
    const [y, m, d] = iso.split('-').map((x) => parseInt(x, 10))
    const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][(m || 1) - 1]
    return withMonth ? `${mon} ${d}` : String(d)
  }
  if (r.from === r.to) return fmt(r.from, true)
  const sameMonth = monthOf(r.from) === monthOf(r.to)
  return `${fmt(r.from, true)} – ${fmt(r.to, !sameMonth)}`
}

/** Is a row's date (YYYY-MM-DD, possibly with time) inside the inclusive range? Undated → excluded. */
export function inRange(dateIso: unknown, r: DateRange): boolean {
  const s = typeof dateIso === 'string' ? dateIso.slice(0, 10) : ''
  return !!s && s >= r.from && s <= r.to
}
