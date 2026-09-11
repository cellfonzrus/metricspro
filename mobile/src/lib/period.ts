// ── Reporting period helpers ─────────────────────────────────────────────────────────────────────
// The platform's report endpoints (exec-mtd, sales-report, account/overview, gp, commissions) key on
// a period string. The canonical form the web app and these endpoints accept is "YYYY-MM"; some also
// accept a month name. We standardise on "YYYY-MM" and derive a human label for display.

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December']

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}

/** Current calendar month in the device's local time, as YYYY-MM. */
export function currentPeriod(d = new Date()): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}`
}

/** The month before `period` (YYYY-MM → YYYY-MM). */
export function prevPeriod(period: string): string {
  const [y, m] = period.split('-').map((x) => parseInt(x, 10))
  if (!y || !m) return currentPeriod()
  const date = new Date(y, m - 1, 1)
  date.setMonth(date.getMonth() - 1)
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}`
}

/** The month after `period`, capped at the current month (never navigate into the future). */
export function nextPeriod(period: string): string {
  const [y, m] = period.split('-').map((x) => parseInt(x, 10))
  if (!y || !m) return currentPeriod()
  const date = new Date(y, m - 1, 1)
  date.setMonth(date.getMonth() + 1)
  const cand = `${date.getFullYear()}-${pad2(date.getMonth() + 1)}`
  return cand > currentPeriod() ? currentPeriod() : cand
}

export function isCurrentPeriod(period: string): boolean {
  return period === currentPeriod()
}

/** "2026-09" → "September 2026". Falls back to the raw string if it isn't YYYY-MM. */
export function periodLabel(period: string): string {
  const [y, m] = period.split('-').map((x) => parseInt(x, 10))
  if (!y || !m || m < 1 || m > 12) return period
  return `${MONTHS[m - 1]} ${y}`
}

/** Today as YYYY-MM-DD (local) — the MTD cut-off the exec-mtd endpoint wants. */
export function todayISO(d = new Date()): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/**
 * Parse free text from the ask bar into a period (YYYY-MM), or null if no date is named.
 * Mirrors the web AskBar's resolvePeriod: "this month", "last month", a month name (optional year),
 * or an explicit YYYY-MM. Anything else → null (caller defaults to the current period).
 */
export function parsePeriod(text: string): string | null {
  const t = (text || '').toLowerCase()
  if (/\bthis month\b/.test(t)) return currentPeriod()
  if (/\b(last|previous|prior) month\b/.test(t)) return prevPeriod(currentPeriod())
  const explicit = t.match(/\b(20\d{2})[-/](0?[1-9]|1[0-2])\b/)
  if (explicit) return `${explicit[1]}-${pad2(parseInt(explicit[2], 10))}`
  for (let i = 0; i < MONTHS.length; i++) {
    const name = MONTHS[i].toLowerCase()
    if (t.includes(name) || t.includes(name.slice(0, 3))) {
      const yr = t.match(/\b(20\d{2})\b/)
      const year = yr ? parseInt(yr[1], 10) : new Date().getFullYear()
      return `${year}-${pad2(i + 1)}`
    }
  }
  return null
}
