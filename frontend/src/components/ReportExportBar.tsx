'use client'
// ReportExportBar — the RULE FOUR (§3c) export set (Excel · PDF · Print · Send email/WhatsApp) for report
// surfaces that keep their OWN rendering: interactive tables (inline row actions, expanders, clickable
// rows) and dashboard tiles that can't drop into <ReportShell>'s plain table. Give it the ExportColumn[]
// + the CURRENTLY-VISIBLE rows (what you see is what exports) and it renders the identical toolbar
// ReportShell provides, wired to the same lib/export renderers + the universal /notify/send-file path
// (org-scoped; the browser renders the file, the backend delivers it to reps by email/WhatsApp).
//
//   • Pure, read-only tabular report            → prefer <ReportShell columns rows/> (adds filter/group too).
//   • Interactive table / clickable list        → keep your table, add <ReportExportBar columns rows/>.
//   • Dashboard tiles (non-tabular)             → pass `sheets` = a one-sheet [{Metric, Value}] summary
//                                                  (optionally + a detail sheet). See the audit doctrine.
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

export type { ExportColumn } from '@/lib/export'

export function ReportExportBar({
  title, subtitle, filename, columns, rows, sheets, compact = true, right, style,
}: {
  title: string
  subtitle?: string
  filename?: string
  columns?: ExportColumn[]
  rows?: any[]                                                       // the CURRENTLY-FILTERED/VISIBLE rows
  sheets?: { name: string; columns: ExportColumn[]; rows: any[] }[]  // multi-sheet (e.g. tiles + a detail list)
  compact?: boolean
  right?: React.ReactNode
  style?: React.CSSProperties
}) {
  const buildPayload = (): ExportPayload => ({
    title, subtitle,
    filename: filename || title.replace(/[^\w]+/g, '_').toLowerCase(),
    sheets: sheets && sheets.length
      ? sheets.map(s => ({ name: s.name.slice(0, 28), columns: s.columns.filter(Boolean), rows: s.rows }))
      : [{ name: 'Report', columns: (columns || []).filter(Boolean), rows: rows || [] }],
  })
  return (
    <div style={{ display: 'inline-flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', ...style }}>
      {right}
      <ExportButtons payload={buildPayload} compact={compact} />
      {/* Universal Send: renders the file in-browser + delivers via /notify/send-file (email + WhatsApp). */}
      <SendReportButton exportPayload={buildPayload} title={title} compact={compact} />
    </div>
  )
}
export default ReportExportBar
