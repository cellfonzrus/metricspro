// Tenant pay-period range helpers (2026-07-25, owner: "the date range by default should be as
// defined in the payroll time period"). A per-tenant pay-period config ALREADY EXISTS (migration
// 085: storeops.tenants.pay_period_type/work_week_start_dow/payday_*, resolved server-side by
// backend/app/modules/core/router.py `pay_period_for` + `_next_periods`, exposed read-only via the
// existing `GET /api/v1/core/tenant-settings` endpoint as `{settings, preview: [currentPeriod, ...]}`).
//
// This module deliberately does NOT reimplement that boundary math (work_week_start_dow anchoring,
// the biweekly_anchor alignment, payday resolution) — core/** is a shared file no module agent may
// edit, and duplicating server logic client-side is exactly the kind of drift the AGENT_CONTRACT
// warns about. It only:
//   1. reads the tenant's CURRENT pay period straight off `preview[0]` (server-computed, verbatim).
//   2. steps prev/next by the period's fixed length (7 days weekly, 14 biweekly) — safe WITHOUT
//      re-deriving the anchor, because once one period boundary is server-verified, every
//      subsequent period on that same grid is contiguous and exactly `length` days from the last
//      (pay_period_for's own biweekly_anchor snapping only ever runs once, at period 0 — see
//      backend/harness_pay_period_stepping.py for the differential proof against the real
//      pay_period_for()/`_next_periods()` for a spread of configs/reference dates).
import { addDays } from '@/lib/client'

export type PayPeriodSettings = {
  work_week_start_dow?: number
  pay_period_type?: string      // 'weekly' | 'biweekly' (anything else treated as weekly, same as core's default)
  payday_dow?: number
  payday_weeks_after?: number
}

export type PayPeriod = { start: string; end: string; payday?: string }

export type TenantSettingsResponse = {
  settings?: PayPeriodSettings
  preview?: PayPeriod[]
}

/** Period length in days for a tenant's pay_period_type ('biweekly' -> 14, everything else -> 7,
 *  matching core's own `length = 14 if pay_period_type == "biweekly" else 7`). */
export function periodLengthDays(settings?: PayPeriodSettings | null): number {
  return settings?.pay_period_type === 'biweekly' ? 14 : 7
}

/** Pull {settings, period} (the CURRENT pay period) out of a GET /api/v1/core/tenant-settings
 *  response. Returns null on any unexpected shape (pre-migration tenant, network hiccup, etc.) so
 *  callers can degrade to a plain calendar-month default instead of crashing on a malformed range. */
export function currentPeriodFromSettingsResponse(r: TenantSettingsResponse | null | undefined):
  { settings: PayPeriodSettings; period: PayPeriod } | null {
  if (!r || !r.settings || !Array.isArray(r.preview) || !r.preview[0]) return null
  const period = r.preview[0]
  if (!period.start || !period.end) return null
  return { settings: r.settings, period }
}

/** Shift an already-server-computed {start,end} by exactly one period length in either direction.
 *  Local-date-safe (addDays operates on 'YYYY-MM-DD' strings, never `new Date(iso)` parsing). */
export function stepPeriod(period: { start: string; end: string },
                            settings: PayPeriodSettings | null | undefined, dir: 1 | -1): { start: string; end: string } {
  const len = periodLengthDays(settings)
  return { start: addDays(period.start, dir * len), end: addDays(period.end, dir * len) }
}

/** Calendar-month {start,end} (inclusive), offset in whole months from today — the fallback default
 *  when no tenant pay-period config is resolvable yet, and the "This month"/"Last month" presets.
 *  Built from numeric Date field math (never `new Date("YYYY-MM-DD")` string parsing), so it's immune
 *  to the JS UTC-parse off-by-one pitfall. */
export function monthRange(offsetMonths = 0): { start: string; end: string } {
  const pad = (n: number) => String(n).padStart(2, '0')
  const now = new Date()
  const y = now.getFullYear(), m = now.getMonth() + offsetMonths
  const first = new Date(y, m, 1)
  const last = new Date(y, m + 1, 0)   // day 0 of next month = last day of this month
  const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  return { start: fmt(first), end: fmt(last) }
}

/** Human label for a {start,end} range, e.g. "Jul 10 – Jul 23, 2026" or "July 2026" when it happens
 *  to be exactly a calendar month (so the common case still reads the way the old month picker did). */
export function rangeLabel(start: string, end: string): string {
  if (!start || !end) return ''
  const s = new Date(start + 'T00:00:00'), e = new Date(end + 'T00:00:00')
  const isFullMonth = s.getDate() === 1 && s.getMonth() === e.getMonth() && s.getFullYear() === e.getFullYear()
    && e.getDate() === new Date(e.getFullYear(), e.getMonth() + 1, 0).getDate()
  if (isFullMonth) return s.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
  const sameYear = s.getFullYear() === e.getFullYear()
  const left = s.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: sameYear ? undefined : 'numeric' })
  const right = e.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  return `${left} – ${right}`
}
