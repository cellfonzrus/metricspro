'use client'
// Shared rendering for ONE sweep-history row (commcalc.email_processed / commcalc.ftp_processed),
// so the Email-Imports and FTP-Imports pages can never disagree about what a status means.
//
// The backend records five statuses (see `_sweep_ingest_outcome` in commcalc/router.py):
//   ok       rows landed. A `detail` on an ok row means a REAL ingest WITH A CAVEAT (a price-guard
//            partial, a device-only Inventory-Aging ingest, an X-Report with unmapped tender labels) —
//            amber, never a clean green tick.
//   skipped  0 rows for a NAMED, FIXABLE reason (price-guard refusal, 0-store parse, X-Report zero
//            reason). Auto-retries every sweep, so it self-heals once the source is corrected.
//   empty    0 rows, the file was read and carried nothing ingestable. Terminal — it stops being
//            re-pulled, so the row is the ONLY record that it arrived.
//   ignored  the report has no importer (register it under Data Imports → Custom Reports).
//   error    the ingest raised. Auto-retries.
// 'download_failed' only ever appears in a live run payload, never in the journal.
//
// Before this, both pages had only ok/skipped/else — so an `empty` or `ignored` file rendered as a red
// ✕ with no rows and (on the FTP page) no explanation at all.

export type SweepStatus = 'ok' | 'skipped' | 'empty' | 'ignored' | 'error' | 'download_failed' | string

export interface SweepRow {
  status?: SweepStatus | null
  rows_saved?: number | null
  detail?: string | null
  skipped?: string | null
}

const AMBER = '#b45309'
const GREEN = '#16794a'
const RED = '#dc2626'
const MUTED = 'var(--text3)'

/** Colour + glyph + retry semantics for a status. Pure — safe to unit-test. */
export function sweepTone(row: SweepRow): { color: string; glyph: string; retries: boolean } {
  const st = String(row.status || '')
  if (st === 'ok') return { color: (row.detail ? AMBER : GREEN), glyph: (row.detail ? '⚠' : '✓'), retries: false }
  if (st === 'skipped') return { color: AMBER, glyph: '⚠', retries: true }
  if (st === 'empty') return { color: AMBER, glyph: '∅', retries: false }
  if (st === 'ignored') return { color: MUTED, glyph: '–', retries: false }
  return { color: RED, glyph: '✕', retries: true }   // error / download_failed / unknown
}

/** The sentence for a history row. Every non-ok status must show a reason — a 0-row outcome with no
 *  explanation is exactly the defect this whole package removes, so fall back to naming the status. */
export function sweepText(row: SweepRow): string {
  const st = String(row.status || '')
  const n = Number(row.rows_saved || 0)
  const detail = (row.detail || '').trim()
  if (st === 'ok') return detail ? `${n.toLocaleString()} rows — ${detail}` : `${n.toLocaleString()} rows`
  if (st === 'skipped') {
    return `0 rows — ${detail || 'refused: fuller data already stored for that day (price guard)'} · will retry next sweep`
  }
  if (st === 'empty') {
    return `0 rows — ${detail || 'the file was read but carried no ingestable rows'} · nothing was overwritten`
  }
  if (st === 'ignored') {
    return detail || 'no importer for this report — register it under Data Imports → Custom Reports'
  }
  if (st === 'download_failed') return `download failed — ${detail || 'no error recorded'} · will retry next sweep`
  return `${detail || 'no error recorded'} · will retry next sweep`
}

/** One table cell's worth of honest status. */
export function SweepStatusCell({ row }: { row: SweepRow }) {
  const { color, glyph } = sweepTone(row)
  const text = sweepText(row)
  return <span style={{ color }} title={row.detail || ''}>{glyph} {text}</span>
}

/** Roll a live run payload's `files[]` into one honest sentence for the page banner. */
export function summarizeSweepRun(r: any): string {
  const files: SweepRow[] = (r?.files || []) as SweepRow[]
  const by = (st: string) => files.filter(f => String(f.status || '') === st)
  const guard = by('skipped').filter(f => String(f.skipped || '').startsWith('price_guard'))
  const other = by('skipped').filter(f => !String(f.skipped || '').startsWith('price_guard'))
  const bits: string[] = []
  if (guard.length) bits.push(`⚠️ ${guard.length} refused by the price guard (fuller data already stored — existing dollars kept)`)
  if (other.length) bits.push(`⚠️ ${other.length} saved 0 rows: ${other.slice(0, 2).map(f => sweepText(f)).join(' · ')}`)
  if (by('empty').length) bits.push(`⚠️ ${by('empty').length} file(s) read but carried no ingestable rows`)
  if (by('ignored').length) bits.push(`${by('ignored').length} ignored (no importer for that report)`)
  if (by('error').length) bits.push(`❌ ${by('error').length} errored: ${by('error').slice(0, 2).map(f => f.detail).join(' · ')}`)
  if (by('download_failed').length) bits.push(`❌ ${by('download_failed').length} download failure(s)`)
  if (r?.retried) bits.push(`${r.retried} retried after a previous failure`)
  if (r?.journal_failures) bits.push(`❌ ${r.journal_failures} history row(s) could not be recorded — those files will be re-processed next run`)
  return bits.join(' · ')
}
