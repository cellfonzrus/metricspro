'use client'
// Reusable month-over-month trend chart, shared by every report that shows a number over time
// (Residual per Subscriber, Gross Profit, Expenses, …). Dual-axis: put a $-heavy overlay (e.g.
// commission) on the right axis and the primary metric on the left. Pass `data` (one row per period)
// and `series` describing each line/bar. Colors default to the shared palette.
//
// recharts (~8.8MB) is heavy, so the actual chart lives in ./TrendChart.impl and is pulled in on
// demand via next/dynamic (ssr:false). This wrapper keeps the light, dependency-free exports
// (TREND_COLORS, TrendSeries, TrendChartProps) that call sites and the impl both import — so nothing
// static in this file references recharts, and the recharts chunk stays out of every route bundle
// until a chart first renders. First open shows a brief placeholder box.
import dynamic from 'next/dynamic'

export const TREND_COLORS = ['#2e75b6', '#16a34a', '#dc2626', '#f59e0b', '#7c3aed', '#0891b2', '#db2777', '#65a30d', '#ea580c', '#4f46e5', '#0d9488', '#b91c1c']

export type TrendSeries = {
  key: string                       // dataKey in each `data` row
  name: string                      // legend/tooltip label
  color?: string
  axis?: 'left' | 'right'           // default 'left'
  type?: 'line' | 'bar'             // default 'line'
  money?: boolean                   // format tooltip/axis as $
  dashed?: boolean
  stack?: string                    // bars sharing a stack id stack (e.g. expense composition)
}

export type TrendChartProps = {
  data: any[]
  series: TrendSeries[]
  xKey?: string
  height?: number
  leftMoney?: boolean
  rightMoney?: boolean
  leftLabel?: string
  rightLabel?: string
  hint?: string
}

export const TrendChart = dynamic<TrendChartProps>(() => import('./TrendChart.impl'), {
  ssr: false,
  loading: () => (
    <div style={{ height: 320, display: 'flex', alignItems: 'center', justifyContent: 'center',
      color: 'var(--text3)', fontSize: 12 }}>Loading chart…</div>
  ),
})

export default TrendChart
