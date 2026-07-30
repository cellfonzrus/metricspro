'use client'
// Shared "when did this report last receive data?" line (owner 2026-07-29: "show there for each report
// when the last set of data was uploaded").
//
// Source of truth: GET /api/v1/commcalc/upload/last — which folds commcalc.upload_trace (mig 202: EVERY
// ingest path, incl. the hourly email sweep, the portal sweeps and the feed→raw_sales promotion) with the
// older commcalc.upload_log into ONE newest-per-report record. Read-only; nothing here writes.
//
// Two lines, deliberately separate, because they answer different questions:
//   • "Last upload: …"        — the newest ingest that actually LANDED rows (with its period / day span).
//   • "Newer attempt … saved no rows" — only when a LATER attempt was refused/parsed nothing, so a tile
//     can never imply fresh data arrived when the last file was rejected (the green-zero defect class).
import { useCallback, useEffect, useState } from 'react'
import { ORG_ID, api } from '@/lib/client'

export interface LastUploadAttempt {
  at: string
  rows_saved: number | null
  status?: string | null
  skipped?: string | null
  source?: string | null
  source_label?: string | null
  origin?: string | null
  filename?: string | null
}

export interface LastUpload {
  key: string
  last_at: string | null
  rows_saved: number | null
  rows_in?: number | null
  target_table?: string | null
  status?: string | null
  origin?: string | null
  source?: string | null
  source_label?: string | null
  filename?: string | null
  period?: string | null
  periods?: Record<string, number> | null
  span?: [string, string] | null
  days?: number | null
  note?: string | null
  latest_attempt?: LastUploadAttempt | null
}

/** ISO UTC (from the API) → the BROWSER's locale + timezone. */
export function fmtStamp(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return String(iso)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** 'YYYY-MM-DD' → 'Jul 14' WITHOUT `new Date("YYYY-MM-DD")` (which parses as UTC and renders the
 *  previous day west of Greenwich — the documented off-by-one in this codebase). */
export function fmtDay(ymd?: string | null): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(ymd || ''))
  if (!m) return String(ymd || '')
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  return isNaN(d.getTime()) ? String(ymd) : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** The "which data" half of the line: a period label, a day span, or nothing.
 *  A day span is the MORE precise statement, so it wins over a period label — but when the same load
 *  touched more than one period (a multi-month historical MA import) the period count is appended, or
 *  the tile would silently imply one month. */
export function coverageText(rec: LastUpload): string {
  const nPeriods = Object.keys(rec.periods || {}).length
  if (rec.span && rec.span[0]) {
    const [a, b] = rec.span
    const span = a === b ? fmtDay(a) : `${fmtDay(a)} – ${fmtDay(b)}`
    const days = rec.days && rec.days > 1 ? `${span} (${rec.days} days)` : span
    return nPeriods > 1 ? `${days} · ${nPeriods} periods` : days
  }
  if (rec.period) return rec.period
  const keys = Object.keys(rec.periods || {})
  if (keys.length > 1) return `${keys.length} periods: ${keys.slice(0, 3).join(', ')}${keys.length > 3 ? '…' : ''}`
  if (keys.length === 1) return keys[0]
  return ''
}

/** Fetch the newest-per-report record for a fixed set of report keys. `keys` is joined, so pass a stable
 *  list (or memoize it) — the effect re-runs when the joined string changes, not on every render. */
export function useLastUploads(keys: string[]) {
  const [last, setLast] = useState<Record<string, LastUpload>>({})
  const [loaded, setLoaded] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  const csv = keys.filter(Boolean).join(',')

  const reload = useCallback(async () => {
    try {
      // api() needs the explicit /api/v1 prefix (a bare path 404s in the app while passing a curl check)
      // and rewrites org_id to the signed-in tenant, so a tenant sees ITS OWN ingest history.
      const d: any = await api(`/api/v1/commcalc/upload/last?types=${encodeURIComponent(csv)}&org_id=${ORG_ID}`)
      setLast(d?.reports || {})
      setHint(d?.hint || null)
    } catch {
      setLast({}); setHint(null)      // best-effort: the line just doesn't render
    } finally {
      setLoaded(true)
    }
  }, [csv])

  useEffect(() => { reload() }, [reload])
  return { last, loaded, hint, reload }
}

const muted: React.CSSProperties = { fontSize: 12, color: 'var(--text3)', marginTop: 8 }

/**
 * One tile's last-upload line. `loaded` avoids flashing "No data uploaded yet" before the fetch lands.
 * `tracked={false}` renders NOTHING — used for uploads whose endpoint writes no ingest journal (the
 * asset-ledger + daily-closing module uploads), where "no upload recorded" would be a lie rather than
 * a fact.
 */
export function LastUploadLine({ rec, loaded = true, tracked = true }:
  { rec?: LastUpload | null; loaded?: boolean; tracked?: boolean }) {
  if (!tracked || !loaded) return null
  const attempt = rec?.latest_attempt || null
  return (
    <>
      {rec?.last_at ? (
        <div style={{ ...muted, color: '#15803d' }}>
          📅 Last upload: {fmtStamp(rec.last_at)}
          {rec.rows_saved != null ? ` · ${Number(rec.rows_saved).toLocaleString()} rows` : ''}
          {coverageText(rec) ? ` · ${coverageText(rec)}` : ''}
          {rec.source_label ? ` · via ${rec.source_label}` : ''}
        </div>
      ) : (
        <div style={muted}>📅 No data uploaded yet</div>
      )}
      {attempt && (
        <div style={{ ...muted, marginTop: 4, color: '#b45309' }}>
          ⚠️ Newer attempt {fmtStamp(attempt.at)} saved no rows{attempt.skipped ? ` (${attempt.skipped.replace(/_/g, ' ')})` : ''}
          {attempt.source_label ? ` · via ${attempt.source_label}` : ''} — see “Where are my rows?”.
        </div>
      )}
    </>
  )
}
