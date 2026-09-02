'use client'
// recharts-using implementation of <TrendChart>. Loaded on demand by ./TrendChart via next/dynamic,
// so this module (and its ~8.8MB recharts dependency) is code-split into its own chunk. The public
// exports (TREND_COLORS, TrendSeries, TrendChartProps) live in ./TrendChart — a dependency-free file
// — which this imports; ./TrendChart only pulls THIS module in through a lazy import(), so there is
// no static import cycle.
import {
  ResponsiveContainer, ComposedChart, Line, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import { fmt } from '@/lib/client'
import { TREND_COLORS, type TrendChartProps } from './TrendChart'

const usd0 = (v: number) => `$${Math.abs(v) >= 1000 ? (v / 1000).toFixed(0) + 'k' : Math.round(v)}`
const num0 = (v: number) => Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 })

export default function TrendChartImpl({
  data, series, xKey = 'name', height = 320, leftMoney = true, rightMoney = true, leftLabel, rightLabel, hint,
}: TrendChartProps) {
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
            if (s.type === 'bar') return <Bar key={s.key} yAxisId={axisId} dataKey={s.key} name={s.name} fill={color}
              stackId={s.stack} radius={s.stack ? undefined : [3, 3, 0, 0]} />
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
