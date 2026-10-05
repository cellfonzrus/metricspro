'use client'
// DataAnswer — renders what the in-app data assistant RETURNED: its pivot tables and its charts
// (index §52). ONE home for that rendering, so the Ask-AI panel and the AskBar overlay show an answer
// identically.
//
// WHY THERE IS NO ARITHMETIC IN THIS FILE, deliberately. The backend returns a pivot already carrying
// its row totals and its column footer (`core/data_qa_compute.pivot`, proved by
// backend/harness_data_qa_compute.py), and a chart already reduced to `{labels, series}`. This
// component only FORMATS. If it started totalling a column itself, the screen could disagree with the
// sentence above it — which is the whole defect the backend's single-arithmetic-home exists to stop.
//
// A NULL CELL IS NOT A ZERO. The backend distinguishes "not reported" (null) from "zero", because a
// store with no sales feed is not a store that sold nothing. A null renders as "—"; it never becomes
// $0.00 and never becomes a zero-height bar.
//
// CHARTS REUSE <TrendChart> (recharts, code-split behind next/dynamic) rather than adding a second
// charting path to the app — the duplicate-check gate. A pie has no TrendChart form, so it renders as
// a share table, which carries the same information without a second chart dependency.
import { useMemo } from 'react'
import { fmt } from '@/lib/client'
import { TrendChart, TREND_COLORS, type TrendSeries } from '@/components/TrendChart'
import { SortableTh, useTableSort } from '@/components/SortableTh'

export type DataChart = {
  kind: 'bar' | 'horizontal_bar' | 'line' | 'pie'
  title?: string
  label_col?: string
  labels: string[]
  series: { name: string; values: (number | null)[] }[]
  truncated?: boolean
}

// A cell as the backend sends it: a number, a label, or NULL for "not reported" (never 0 — see below).
export type Cell = number | string | null

export type DataTable = {
  title?: string
  columns: string[]
  rows: Record<string, Cell>[]
  totals: Record<string, Cell>
  measure?: string
  agg?: string
  row_by?: string[]
  col_by?: string
  row_count?: number
}

export type DataSource = {
  question: string; label: string
  parameters?: Record<string, string | string[]>
  row_count?: number
}

// A measure whose name reads like money is formatted as money. This is a DISPLAY guess and nothing
// else depends on it: a wrong guess changes a dollar sign, never a figure.
const MONEYISH = /revenue|gp|gross|profit|price|amount|payout|commission|cost|sales\b|\$/i
const cell = (v: Cell | undefined, money: boolean) => {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v !== 'number') return String(v)
  return money ? fmt(v) : v.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

// A cell read for the shared sorter. Module scope so the memo inside useTableSort is stable.
const pivotCell = (row: Record<string, Cell>, field: string) => row[field]

function PivotTable({ table }: { table: DataTable }) {
  const money = MONEYISH.test(table.measure || '')
  const side = table.row_by?.length ? table.row_by : ['']
  // Click-a-header sorting through the ONE comparison home (@/lib/table-sort), like every other report
  // table. Null seeds it with the backend's own order, and the totals row lives in <tfoot>, so a sort
  // can never drag it into the middle.
  const { sorted, sort, toggle } = useTableSort<Record<string, Cell>>(table.rows || [], pivotCell)
  return (
    <div style={{ marginTop: 10, border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
      {(table.title || table.measure) && (
        <div style={{ padding: '7px 10px', background: 'var(--surface2)', fontSize: 11.5, fontWeight: 650,
          color: 'var(--text3)' }}>
          {table.title || `${table.agg || 'sum'} of ${table.measure}`}
          {table.col_by ? ` · by ${table.col_by}` : ''}
        </div>
      )}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
          <thead>
            <tr style={{ background: 'var(--surface2)' }}>
              {side.map(c => (
                <SortableTh key={`h-${c}`} field={c} sort={sort} onSort={toggle} disabled={!c}
                  style={{ textAlign: 'left', padding: '6px 9px', whiteSpace: 'nowrap',
                    borderBottom: '1px solid var(--border)' }}>{c || ''}</SortableTh>
              ))}
              {table.columns.map(c => (
                <SortableTh key={`h2-${c}`} field={c} sort={sort} onSort={toggle}
                  style={{ textAlign: 'right', padding: '6px 9px', whiteSpace: 'nowrap',
                    borderBottom: '1px solid var(--border)' }}>{c || '—'}</SortableTh>
              ))}
              <SortableTh field="_total" sort={sort} onSort={toggle}
                style={{ textAlign: 'right', padding: '6px 9px',
                  borderBottom: '1px solid var(--border)' }}>Total</SortableTh>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i}>
                {side.map(c => (
                  <td key={`c-${c}`} style={{ padding: '5px 9px', whiteSpace: 'nowrap',
                    borderBottom: '1px solid var(--border)' }}>{c ? (r[c] ?? '—') : ''}</td>
                ))}
                {table.columns.map(c => (
                  <td key={`v-${c}`} style={{ padding: '5px 9px', textAlign: 'right',
                    fontVariantNumeric: 'tabular-nums', borderBottom: '1px solid var(--border)' }}>
                    {cell(r[c], money)}
                  </td>
                ))}
                <td style={{ padding: '5px 9px', textAlign: 'right', fontWeight: 650,
                  fontVariantNumeric: 'tabular-nums', borderBottom: '1px solid var(--border)' }}>
                  {cell(r._total, money)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ background: 'var(--surface2)', fontWeight: 650 }}>
              {side.map((c, i) => <td key={`f-${c}`} style={{ padding: '6px 9px' }}>{i === 0 ? 'Total' : ''}</td>)}
              {table.columns.map(c => (
                <td key={`ft-${c}`} style={{ padding: '6px 9px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                  {cell(table.totals?.[c], money)}
                </td>
              ))}
              <td style={{ padding: '6px 9px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                {cell(table.totals?._total, money)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      {typeof table.row_count === 'number' && table.row_count > sorted.length && (
        <div style={{ padding: '5px 10px', fontSize: 11, color: 'var(--text3)' }}>
          Showing {sorted.length} of {table.row_count} rows.
        </div>
      )}
    </div>
  )
}

function ShareTable({ chart }: { chart: DataChart }) {
  // A pie, rendered as the share table it is. The values are the backend's; the percentage is of the
  // shown values only, and says so, rather than implying it is a share of the whole company.
  const s = chart.series[0]
  const total = (s?.values || []).reduce<number>((a, v) => a + (typeof v === 'number' ? v : 0), 0)
  const money = MONEYISH.test(s?.name || '')
  return (
    <div style={{ marginTop: 10, border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ padding: '7px 10px', background: 'var(--surface2)', fontSize: 11.5, fontWeight: 650,
        color: 'var(--text3)' }}>{chart.title || s?.name} · share</div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
        <tbody>
          {chart.labels.map((l, i) => {
            const v = s?.values?.[i]
            const pct = typeof v === 'number' && total ? (v / total) * 100 : null
            return (
              <tr key={l + i}>
                <td style={{ padding: '5px 9px', borderBottom: '1px solid var(--border)' }}>{l || '—'}</td>
                <td style={{ padding: '5px 9px', textAlign: 'right', fontVariantNumeric: 'tabular-nums',
                  borderBottom: '1px solid var(--border)' }}>{cell(v, money)}</td>
                <td style={{ padding: '5px 9px', textAlign: 'right', width: 64, color: 'var(--text3)',
                  borderBottom: '1px solid var(--border)' }}>{pct == null ? '—' : `${pct.toFixed(1)}%`}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div style={{ padding: '5px 10px', fontSize: 11, color: 'var(--text3)' }}>
        Percentages are of the rows shown.{chart.truncated ? ' The list was shortened.' : ''}
      </div>
    </div>
  )
}

function Chart({ chart }: { chart: DataChart }) {
  const { data, series } = useMemo(() => {
    const rows = chart.labels.map((l, i) => {
      const row: Record<string, Cell> = { name: l || '—' }
      chart.series.forEach(s => { row[s.name] = s.values[i] })   // null stays null: recharts draws a gap
      return row
    })
    const ser: TrendSeries[] = chart.series.map((s, i) => ({
      key: s.name, name: s.name, color: TREND_COLORS[i % TREND_COLORS.length],
      type: chart.kind === 'line' ? 'line' : 'bar', money: MONEYISH.test(s.name),
    }))
    return { data: rows, series: ser }
  }, [chart])
  if (chart.kind === 'pie') return <ShareTable chart={chart} />
  const anyMoney = chart.series.some(s => MONEYISH.test(s.name))
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 11.5, fontWeight: 650, color: 'var(--text3)', marginBottom: 2 }}>{chart.title}</div>
      <TrendChart data={data} series={series} height={240} leftMoney={anyMoney}
        hint={chart.truncated ? 'The chart shows the first rows only.' : undefined} />
    </div>
  )
}

export default function DataAnswer({ charts, tables, sources }: {
  charts?: DataChart[]; tables?: DataTable[]; sources?: DataSource[]
}) {
  const hasAny = (charts?.length || 0) + (tables?.length || 0) + (sources?.length || 0) > 0
  if (!hasAny) return null
  return (
    <div>
      {(tables || []).map((t, i) => <PivotTable key={`t${i}`} table={t} />)}
      {(charts || []).map((c, i) => <Chart key={`c${i}`} chart={c} />)}
      {/* WHERE THE FIGURES CAME FROM. The assistant runs the platform's own reports, so naming them is
          not a footnote — it is how a user checks a number against the screen that owns it. */}
      {(sources || []).length > 0 && (
        <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text3)' }}>
          Read from {(sources || []).map(s => {
            const p = Object.entries(s.parameters || {})
              .map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(', ') : v}`).join(', ')
            return `${s.label}${p ? ` (${p})` : ''}`
          }).join(' · ')}
        </div>
      )}
    </div>
  )
}
