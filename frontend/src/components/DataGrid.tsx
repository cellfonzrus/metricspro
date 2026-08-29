'use client'
// DataGrid — a high-density data grid (owner 2026-08-29 modernization track). Built on TanStack Table
// (headless) so it gets robust column PINNING, resizing and sorting state, but it consumes the app's
// EXISTING `ExportColumn[]` shape — the very columns a report already builds for export — so a page adopts
// it without redefining its columns. What it adds over a plain <table>:
//   • a STICKY header ribbon that stays put while the body scrolls;
//   • a PINNED first column (the store / rep label) that stays visible while a wide table scrolls sideways;
//   • click-to-SORT on every column and drag-to-RESIZE column widths;
//   • an optional pinned TOTALS footer row.
// Cells render via each column's optional `render` (for report-specific number formatting) and fall back to
// a money/number/text default otherwise — DISPLAY ONLY; the numbers still come from the column's `get`.
import { useMemo, useState, type CSSProperties } from 'react'
import {
  useReactTable, getCoreRowModel, getSortedRowModel,
  type ColumnDef, type SortingState, type ColumnPinningState, type Column,
} from '@tanstack/react-table'
import type { ExportColumn } from '@/lib/export'

const money = (n: any) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n) || 0)
const isRight = (c: ExportColumn) => !!c.money || c.type === 'money' || c.type === 'number' || c.align === 'right'
const colId = (c: ExportColumn) => c.field || c.header

// Default cell formatting when a column has no `render`: money → currency, number → localized int, else text.
function defaultCell(c: ExportColumn, row: any) {
  const v = c.get(row)
  if (v == null || v === '') return c.money ? money(0) : ''
  if (c.money || c.type === 'money') return money(v)
  if (c.type === 'number') { const n = Number(v); return isNaN(n) ? String(v) : n.toLocaleString() }
  return String(v)
}

// Left offset + sticky styling for a pinned column, so the header and every body/footer cell in that
// column line up and stay opaque over the scrolling content.
function pinnedStyle(col: Column<any, unknown>, bg: string, z: number): CSSProperties | undefined {
  if (col.getIsPinned() !== 'left') return undefined
  return { position: 'sticky', left: col.getStart('left'), zIndex: z, background: bg,
    boxShadow: 'inset -1px 0 0 var(--border)' }
}

export default function DataGrid({
  columns, rows, totalRow, totalLabel = 'TOTAL', pinFirst = true, maxHeight = '70vh', onRowClick,
}: {
  columns: ExportColumn[]
  rows: any[]
  totalRow?: any                     // rendered as a pinned footer row (uses the same columns); omit to hide
  totalLabel?: string
  pinFirst?: boolean                 // keep the first column visible while scrolling sideways (default on)
  maxHeight?: string
  onRowClick?: (row: any) => void
}) {
  const cols = useMemo(() => columns.filter(Boolean), [columns])

  const defs = useMemo<ColumnDef<any>[]>(() => cols.map((c) => ({
    id: colId(c),
    accessorFn: (row: any) => c.get(row),
    header: c.header,
    enableSorting: true,
    meta: { col: c },
    // Numbers sort numerically; everything else alphabetically.
    sortingFn: (isRight(c) && c.type !== 'text')
      ? (a, b, id) => (Number(a.getValue(id)) || 0) - (Number(b.getValue(id)) || 0)
      : 'alphanumeric',
  })), [cols])

  const [sorting, setSorting] = useState<SortingState>([])
  const pinning: ColumnPinningState = useMemo(
    () => (pinFirst && cols.length ? { left: [colId(cols[0])], right: [] } : { left: [], right: [] }),
    [pinFirst, cols])

  const table = useReactTable({
    data: rows, columns: defs,
    state: { sorting, columnPinning: pinning },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  })

  const headBg = 'var(--surface2)'
  const th: CSSProperties = { padding: '9px 12px', fontSize: 11, fontWeight: 650, color: 'var(--text2)',
    textTransform: 'uppercase', letterSpacing: '0.04em', whiteSpace: 'nowrap', textAlign: 'left',
    position: 'sticky', top: 0, zIndex: 2, background: headBg, boxShadow: 'inset 0 -1px 0 var(--border)',
    userSelect: 'none' }
  const td: CSSProperties = { padding: '8px 12px', fontSize: 12.5, borderTop: '1px solid var(--border)',
    whiteSpace: 'nowrap', background: 'var(--surface)' }

  const leaf = table.getVisibleLeafColumns()

  return (
    <div className="table-wrapper" style={{ maxHeight, overflow: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, tableLayout: 'fixed' }}>
        <colgroup>{leaf.map((c) => <col key={c.id} style={{ width: c.getSize() }} />)}</colgroup>
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => {
                const c: ExportColumn = (h.column.columnDef.meta as any).col
                const sorted = h.column.getIsSorted()
                const pin = pinnedStyle(h.column, headBg, 3)
                return (
                  <th key={h.id} title={c.tip}
                    style={{ ...th, textAlign: isRight(c) ? 'right' : 'left',
                      ...(pin ? { ...pin, zIndex: (pin.zIndex as number) + 1 } : {}) }}>
                    <span onClick={h.column.getToggleSortingHandler()}
                      style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      {c.header}
                      <span style={{ color: sorted ? 'var(--accent2)' : 'var(--text3)', fontSize: 9 }}>
                        {sorted === 'asc' ? '▲' : sorted === 'desc' ? '▼' : '↕'}
                      </span>
                    </span>
                    {/* drag-to-resize handle on the right edge */}
                    <span onMouseDown={h.getResizeHandler()} onTouchStart={h.getResizeHandler()}
                      onClick={(e) => e.stopPropagation()} aria-hidden
                      style={{ position: 'absolute', right: 0, top: 0, height: '100%', width: 6,
                        cursor: 'col-resize', touchAction: 'none' }} />
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((r) => (
            <tr key={r.id} onClick={onRowClick ? () => onRowClick(r.original) : undefined}
              style={onRowClick ? { cursor: 'pointer' } : undefined}>
              {r.getVisibleCells().map((cell) => {
                const c: ExportColumn = (cell.column.columnDef.meta as any).col
                const pin = pinnedStyle(cell.column, 'var(--surface)', 1)
                const first = cell.column.getIsPinned() === 'left'
                return (
                  <td key={cell.id}
                    style={{ ...td, textAlign: isRight(c) ? 'right' : 'left',
                      ...(first ? { fontWeight: 600 } : {}), ...(pin || {}) }}>
                    {c.render ? c.render(r.original) : defaultCell(c, r.original)}
                  </td>
                )
              })}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td style={{ ...td, textAlign: 'center', color: 'var(--text3)', padding: 24 }} colSpan={leaf.length}>
              No rows.
            </td></tr>
          )}
        </tbody>
        {totalRow && rows.length > 0 && (
          <tfoot>
            <tr>
              {leaf.map((column, i) => {
                const c: ExportColumn = (column.columnDef.meta as any).col
                const pin = pinnedStyle(column, headBg, 2)
                return (
                  <td key={column.id}
                    style={{ ...td, fontWeight: 700, background: headBg, position: 'sticky', bottom: 0, zIndex: 1,
                      borderTop: '2px solid var(--border2)', textAlign: isRight(c) ? 'right' : 'left',
                      ...(pin ? { ...pin, zIndex: 3, bottom: 0 } : {}) }}>
                    {i === 0 ? totalLabel : (c.render ? c.render(totalRow) : defaultCell(c, totalRow))}
                  </td>
                )
              })}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  )
}
