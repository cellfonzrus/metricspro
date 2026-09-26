// P&L over a MONTH RANGE — the export payload (owner 2026-09-26: "also need the p&L report to be exported
// for multiple months … all these need to be platform wide"). Index §4c.
//
// DISPLAY / EXPORT ONLY. Every number comes from `GET /account/pl-range`, which loops THE single-month P&L
// read (`router.pl_single_month` — the same read this page shows) over the months `_period.month_range`
// lists; the grid AND its export layout (Section · Line · one column per month · Total, the drill rows, the
// page's line order) are laid out once, by `account/pl_range.export_sheet`, and travel with the response.
// This file only maps those {header, key, money} columns onto ExportColumn getters and adds the
// self-describing cover sheet. It adds up nothing and derives no line — the lock
// `backend/harness_pl_range_lock.py` fails the build if it starts to.
import type { ExportColumn, ExportPayload, ExportSheet } from '@/lib/export'
import { statementInfoSheet, statementSubtitle, type StatementMeta } from './statementExport'

export const PL_RANGE_ENDPOINT = '/api/v1/account/pl-range'

export type PlRangeServerSheet = {
  name: string
  columns: { header: string; key: string; money?: boolean }[]
  rows: Record<string, any>[]
}
export type PlRangeResponse = {
  period_from: string
  period_to: string
  scope: string
  scope_label?: string | null
  months: string[]
  computed_months: string[]
  missing_months: string[]
  month_status: { period: string; computed: boolean; stale: boolean; computed_at?: string | null; newest_ingest_at?: string | null }[]
  sheets: PlRangeServerSheet[]
}

/** The query string for the range read — the SAME scope / store / market parameters `/account/pl/{period}`
 *  takes (pipe-separated, as the page sends them). */
export function plRangeQuery(from: string, to: string, scope: string, stores: string[], markets: string[], orgId: string): string {
  const q = new URLSearchParams({ period_from: from, period_to: to, scope, org_id: orgId })
  if (stores.length > 0 || markets.length > 0) {
    q.set('stores', stores.join('|'))
    q.set('markets', markets.join('|'))
  }
  return `${PL_RANGE_ENDPOINT}?${q.toString()}`
}

/** A server sheet as an ExportSheet: each column reads its own key off the row, nothing more. */
export function toExportSheet(s: PlRangeServerSheet): ExportSheet {
  const columns: ExportColumn[] = (s.columns || []).map(c => ({
    header: c.header, money: !!c.money, get: (r: any) => (r == null ? null : r[c.key]),
  }))
  return { name: s.name, columns, rows: s.rows || [] }
}

export function plRangeMeta(d: PlRangeResponse, scopeFallback: string): StatementMeta {
  const stale = (d.month_status || []).filter(m => m.stale).map(m => m.period)
  const stamps = (d.month_status || []).map(m => m.computed_at).filter(Boolean) as string[]
  const ingests = (d.month_status || []).map(m => m.newest_ingest_at).filter(Boolean) as string[]
  return {
    reportName: 'Profit & Loss by month',
    scopeLabel: d.scope_label || scopeFallback,
    period: `${d.period_from} → ${d.period_to}`,
    basis: 'Cash basis',
    computed: (d.computed_months || []).length > 0,
    computedAt: stamps.sort().slice(-1)[0] || null,
    newestIngestAt: ingests.sort().slice(-1)[0] || null,
    stale: stale.length > 0,
    extra: [
      ['Months', String((d.months || []).length)],
      ['Months computed', (d.computed_months || []).join(', ') || '—'],
      ['Months NOT computed (blank columns, not in the Total)', (d.missing_months || []).join(', ') || 'none'],
      ['Stale months', stale.join(', ') || 'none'],
      ['Total column', 'the sum of the month columns of the same row (blank months excluded)'],
    ],
  }
}

export function plRangePayload(d: PlRangeResponse, scopeFallback: string, filtered: boolean): ExportPayload {
  const meta = plRangeMeta(d, scopeFallback)
  const slug = (filtered ? 'filtered' : (d.scope || scopeFallback)).replace(/[^a-z0-9]+/gi, '-')
  return {
    title: `Profit & Loss by month — ${meta.scopeLabel}`,
    subtitle: statementSubtitle(meta),
    filename: `pl-${slug}-${d.period_from}-to-${d.period_to}`.replace(/\s+/g, '-'),
    sheets: [statementInfoSheet(meta), ...(d.sheets || []).map(toExportSheet)],
  }
}
