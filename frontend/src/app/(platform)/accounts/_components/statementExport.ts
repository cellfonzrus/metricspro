// Shared builders for the finance statement exports (RULE FOUR §3c). They keep the "self-describing"
// header block + info sheet IDENTICAL across the P&L and Balance Sheet, so a printed / emailed /
// Excel statement always carries: which company or store scope, which period, the accounting basis
// (cash vs point-in-time), when it was computed, how fresh the underlying data is, and whether it is
// stale. DISPLAY / EXPORT ONLY — nothing here reads or alters a statement number; the figures come
// straight from the already-computed snapshot payloads the pages fetch.
import type { ExportColumn, ExportSheet } from '@/lib/export'

export type StatementMeta = {
  reportName: string                 // "Profit & Loss" / "Balance Sheet"
  scopeLabel: string                 // e.g. "Consolidated (all companies)" or a store address
  period: string                     // the ACTIVE period ("June 2026")
  basis: string                      // "Cash basis" / "Point-in-time"
  computed?: boolean
  computedAt?: string | null         // account_statements.computed_at (ISO)
  newestIngestAt?: string | null     // newest relevant upload feeding this period (ISO)
  stale?: boolean                    // newest ingest newer than the snapshot
  extra?: [string, string][]         // statement-specific rows (e.g. Balanced / Imbalance on the BS)
}

// Local timestamp string; leaves a non-parseable value as-is; "—" when absent.
export function fmtStamp(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d.getTime()) ? String(iso) : d.toLocaleString()
}

export function statementStatus(m: StatementMeta): string {
  if (!m.computed) return 'Never computed'
  if (m.stale) return 'STALE — data newer than statement'
  return 'Current'
}

// The self-describing subtitle rendered atop the PDF / Print / Send (the visible "header" of a
// printed statement). Excel drops the subtitle, which is why we also ship the Info sheet below.
export function statementSubtitle(m: StatementMeta): string {
  const bits = [m.period, m.basis, m.scopeLabel]
  if (m.computedAt) bits.push(`computed ${fmtStamp(m.computedAt)}`)
  if (m.stale) bits.push('⚠ STALE — data newer than statement')
  return bits.filter(Boolean).join(' · ')
}

// A one-sheet {Field, Value} cover so the EXCEL workbook is self-describing too (Excel keeps only
// column headers + rows, not the title/subtitle). Rendered first in the workbook / PDF.
export function statementInfoSheet(m: StatementMeta): ExportSheet {
  const rows: { k: string; v: string }[] = [
    { k: 'Report', v: m.reportName },
    { k: 'Company / scope', v: m.scopeLabel },
    { k: 'Period', v: m.period },
    { k: 'Basis', v: m.basis },
    { k: 'Computed at', v: fmtStamp(m.computedAt) },
    { k: 'Data current as of', v: fmtStamp(m.newestIngestAt) },
    { k: 'Status', v: statementStatus(m) },
    ...(m.extra || []).map(([k, v]) => ({ k, v })),
  ]
  const columns: ExportColumn[] = [
    { header: 'Field', get: (r: any) => r.k },
    { header: 'Value', get: (r: any) => r.v },
  ]
  return { name: 'Statement Info', columns, rows }
}
