'use client'
// Reusable month-over-month trend chart, shared by every report that shows a number over time
// (Residual per Subscriber, Gross Profit, Expenses, …). Dual-axis: put a $-heavy overlay (e.g.
// commission) on the right axis and the primary metric on the left. Pass `data` (one row per period)
// and `series` describing each line/bar. Colors default to the shared palette.
import {
  ResponsiveContainer, ComposedChart, Line, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import { fmt } from '@/lib/client'

export const TREND_COLORS = ['#2e75b6', '#16a34a', '#dc2626', '#f59e0b', '#7c3aed', '#0891b2', '#db2777', '#65a30d', '#ea580c', '#4f46e5', '#0d9488', '#b91c1c']

export type TrendSeries = {
  key: string                       // dataKey in each `data` row
  name: string                      // legend/tooltip label
  color?: string
  axis?: 'left' | 'right'           // default 'left'
  type?: 'line' | 'bar'             // default 'line'
  money?: boolean                   // format tooltip/axis as $
  dashed?: boolean
}

const usd0 = (v: number) => `$${Math.abs(v) >= 1000 ? (v / 1000).toFixed(0) + 'k' : Math.round(v)}`
const num0 = (v: number) => Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 })

export function TrendChart({
  data, series, xKey = 'name', height = 320, leftMoney = true, rightMoney = true, leftLabel, rightLabel, hint,
}: {
  data: any[]
  series: TrendSeries[]
  xKey?: string
  height?: number
  leftMoney?: boolean
  rightMoney?: boolean
  leftLabel?: string
  rightLabel?: string
  hint?: string
}) {
  const hasRight = series.some(s => s.axis === 'right')
  const moneyByKey: Record<string, boolean> = {}
  series.forEach(s => (moneyByKey[s.key] = !!s.money))
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 6, right: hasRight ? 8 : 4, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis dataKey={xKey} tick={{ fontSize: 11 }} stroke="var(--text3)" />
          <YAxis yAxisId="left" tick={{ fontSize: 11 }} stroke="var(--text3)"
            tickFormatter={(v: number) => leftMoney ? usd0(v) : num0(v)} />
          {hasRight && <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} stroke="#94a3b8"
            tickFormatter={(v: number) => rightMoney ? usd0(v) : num0(v)} />}
          <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} labelStyle={{ fontSize: 12 }}
            formatter={(v: any, n: any, p: any) => [moneyByKey[p?.dataKey] ? fmt(Number(v)) : Number(v).toLocaleString(), n]} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {series.map((s, i) => {
            const color = s.color || TREND_COLORS[i % TREND_COLORS.length]
            const axisId = s.axis === 'right' ? 'right' : 'left'
            if (s.type === 'bar') return <Bar key={s.key} yAxisId={axisId} dataKey={s.key} name={s.name} fill={color} radius={[3, 3, 0, 0]} />
            return <Line key={s.key} yAxisId={axisId} type="monotone" dataKey={s.key} name={s.name}
              stroke={color} strokeWidth={2} strokeDasharray={s.dashed ? '5 4' : undefined} dot={{ r: 2 }} />
          })}
        </ComposedChart>
      </ResponsiveContainer>
      {(leftLabel || rightLabel || hint) && (
        <div style={{ fontSize: 11, color: 'var(--text3)', padding: '2px 6px 4px' }}>
          {leftLabel && <>Left: {leftLabel}. </>}{hasRight && rightLabel && <>Right (dashed): {rightLabel}. </>}{hint}
        </div>
      )}
    </div>
  )
}

export default TrendChart
