// ── Number / money formatting ──────────────────────────────────────────────────────────────────
// Dashboards show a lot of dollar totals and counts; keep the rendering in one place so every KPI
// card reads the same. Null/NaN degrade to an em dash rather than "$NaN".

export function money(n: unknown, opts: { cents?: boolean } = {}): string {
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  const frac = opts.cents ? 2 : 0
  return `$${v.toLocaleString('en-US', { minimumFractionDigits: frac, maximumFractionDigits: frac })}`
}

// Compact money for tight KPI tiles: $1.2M / $34.5K / $980.
export function moneyShort(n: unknown): string {
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}K`
  return `${sign}$${abs.toFixed(0)}`
}

export function count(n: unknown): string {
  const v = Number(n)
  return Number.isFinite(v) ? v.toLocaleString('en-US') : '—'
}

export function pct(n: unknown, digits = 0): string {
  const v = Number(n)
  return Number.isFinite(v) ? `${v.toFixed(digits)}%` : '—'
}
