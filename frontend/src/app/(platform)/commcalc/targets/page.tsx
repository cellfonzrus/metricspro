'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID, fmt, fmtN, localToday } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

const CATS = [
  { key: 'activations', label: 'Activations', unit: 'count' },
  { key: 'upgrades', label: 'Upgrades', unit: 'count' },
  { key: 'byod', label: 'BYOD', unit: 'count' },
  { key: 'accessories', label: 'Accessories', unit: 'dollars' },
] as const

type CatKey = typeof CATS[number]['key']

function val(unit: string, n: number) {
  return unit === 'dollars' ? fmt(n || 0) : fmtN(n || 0, 1)
}

interface CatMetrics {
  unit: string; monthly: number; achieved_mtd: number; need: number
  base_today: number; today_target: number; pace: number; open_days_left: number
}
interface CalDay {
  date: string; hours: number; is_today: boolean; is_past: boolean
  cats: Record<string, { base: number; achieved: number | null }>
}
interface CalResp {
  period: string; scope: string; store_code: string; rep: string | null
  scheduled_hours_total: number; open_days_total: number; today: string
  has_schedule: boolean
  categories: Record<string, CatMetrics>; calendar: CalDay[]; reps: string[]
}

export default function DailyTargetsPage() {
  const { period } = usePeriod()
  const [summary, setSummary] = useState<any[]>([])
  const [loadingSum, setLoadingSum] = useState(true)
  const [storeCode, setStoreCode] = useState('')
  const [scope, setScope] = useState<'store' | 'rep'>('store')
  const [rep, setRep] = useState('')
  const [detail, setDetail] = useState<CalResp | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [calCat, setCalCat] = useState<CatKey>('activations')

  useEffect(() => { loadSummary() }, [period])
  useEffect(() => { if (storeCode) loadDetail() }, [storeCode, scope, rep, period])

  async function loadSummary() {
    setLoadingSum(true)
    try {
      const d = await api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/summary?org_id=${ORG_ID}&today=${localToday()}`)
      setSummary(d.stores || [])
      if (!storeCode && d.stores?.length) setStoreCode(d.stores[0].store_code)
    } catch (e) { console.error(e) }
    setLoadingSum(false)
  }

  async function loadDetail() {
    setLoadingDetail(true)
    try {
      const q = `scope=${scope}&store_code=${encodeURIComponent(storeCode)}${scope === 'rep' && rep ? `&rep=${encodeURIComponent(rep)}` : ''}&org_id=${ORG_ID}&today=${localToday()}`
      const d: CalResp = await api(`/api/v1/commcalc/targets/${encodeURIComponent(period)}/calendar?${q}`)
      setDetail(d)
      if (scope === 'rep' && !rep && d.reps?.length) setRep(d.reps[0])
    } catch (e) { console.error(e) }
    setLoadingDetail(false)
  }

  const th: React.CSSProperties = { textAlign: 'left', padding: '10px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }
  const td: React.CSSProperties = { padding: '8px 14px', fontSize: 13 }

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Daily Targets</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          {period} · Schedule-weighted daily targets with daily catch-up. Today's target rolls in any shortfall;
          pace spreads the remaining balance over the open days left.
        </p>
      </div>

      {/* ── All-stores summary ── */}
      <div className="card" style={{ padding: 0, marginBottom: 24, overflowX: 'auto' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600 }}>
          Store Summary — Activations
        </div>
        {loadingSum ? (
          <div style={{ textAlign: 'center', padding: 30, color: 'var(--text3)' }}>Loading…</div>
        ) : summary.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 30, color: 'var(--text3)' }}>
            No targets set. Add monthly targets in <strong>Target Settings</strong>.
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                {['Store', 'Monthly', 'Achieved', 'Need', "Today's Target", 'Pace/day', ''].map(h => <th key={h} style={th}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {summary.map((s, i) => {
                const a: CatMetrics = s.categories?.activations
                return (
                  <tr key={s.store_code} style={{ borderBottom: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                    <td style={td}>
                      <div style={{ fontWeight: 600 }}>{s.address || s.store_code}</div>
                      <div style={{ fontSize: 11, color: 'var(--text3)' }}>{s.store_code}{s.market ? ` · ${s.market}` : ''}</div>
                    </td>
                    <td style={td}>{fmtN(a?.monthly, 0)}</td>
                    <td style={td}>{fmtN(a?.achieved_mtd, 0)}</td>
                    <td style={{ ...td, color: a?.need > 0 ? '#b45309' : 'var(--green)' }}>{fmtN(a?.need, 0)}</td>
                    <td style={{ ...td, fontWeight: 700, color: 'var(--accent)' }}>{fmtN(a?.today_target, 1)}</td>
                    <td style={td}>{fmtN(a?.pace, 1)}</td>
                    <td style={td}>
                      <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }}
                        onClick={() => { setScope('store'); setRep(''); setStoreCode(s.store_code) }}>
                        View →
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Detail controls ── */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <select className="input" value={storeCode} onChange={e => { setStoreCode(e.target.value); setRep('') }} style={{ minWidth: 220 }}>
          {summary.map(s => <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}</option>)}
        </select>
        <div style={{ display: 'flex', gap: 4, background: 'var(--surface2)', padding: 4, borderRadius: 10 }}>
          {(['store', 'rep'] as const).map(sc => (
            <button key={sc} className="btn" onClick={() => setScope(sc)} style={{
              background: scope === sc ? 'white' : 'transparent',
              color: scope === sc ? 'var(--accent)' : 'var(--text2)', fontSize: 13,
              boxShadow: scope === sc ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
            }}>{sc === 'store' ? 'Store' : 'By Rep'}</button>
          ))}
        </div>
        {scope === 'rep' && (
          <select className="input" value={rep} onChange={e => setRep(e.target.value)} style={{ minWidth: 200 }}>
            <option value="">Select rep…</option>
            {detail?.reps?.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        )}
        {detail && <span style={{ fontSize: 12, color: 'var(--text3)' }}>
          {fmtN(detail.scheduled_hours_total, 0)}h scheduled · {detail.open_days_total} open days · today {detail.today}
        </span>}
      </div>

      {detail && !detail.has_schedule && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10, padding: '8px 14px', marginBottom: 16, fontSize: 13, color: '#92400e' }}>
          ⚠️ No StoreOps schedule loaded for this {scope === 'rep' ? 'rep' : 'store'} this period — targets can't be schedule-weighted, so the full remaining balance is shown under <strong>Pace</strong> and today's target is 0.
        </div>
      )}

      {loadingDetail ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>Loading…</div>
      ) : !detail ? null : (
        <>
          {/* Category tiles */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14, marginBottom: 24 }}>
            {CATS.map(c => {
              const m = detail.categories[c.key]
              if (!m) return null
              return (
                <div key={c.key} className="card">
                  <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>{c.label}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 6, fontSize: 13 }}>
                    <span style={{ color: 'var(--text2)' }}>Monthly target</span>
                    <span style={{ fontWeight: 600 }}>{val(m.unit, m.monthly)}</span>
                    <span style={{ color: 'var(--text2)' }}>Achieved so far</span>
                    <span style={{ fontWeight: 600 }}>{val(m.unit, m.achieved_mtd)}</span>
                    <span style={{ color: 'var(--text2)' }}>Need to achieve</span>
                    <span style={{ fontWeight: 600, color: m.need > 0 ? '#b45309' : 'var(--green)' }}>{val(m.unit, m.need)}</span>
                    <span style={{ color: 'var(--text2)', paddingTop: 6, borderTop: '1px solid var(--border)' }}>Today's target</span>
                    <span style={{ fontWeight: 700, color: 'var(--accent)', paddingTop: 6, borderTop: '1px solid var(--border)' }}>{val(m.unit, m.today_target)}</span>
                    <span style={{ color: 'var(--text2)' }}>Pace / open day</span>
                    <span style={{ fontWeight: 600 }}>{val(m.unit, m.pace)}</span>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Calendar */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontWeight: 600 }}>Target Calendar</div>
              <div style={{ display: 'flex', gap: 4 }}>
                {CATS.map(c => (
                  <button key={c.key} className="btn" onClick={() => setCalCat(c.key)} style={{
                    background: calCat === c.key ? 'var(--accent)' : 'transparent',
                    color: calCat === c.key ? 'white' : 'var(--text2)', fontSize: 12, padding: '4px 10px',
                  }}>{c.label}</button>
                ))}
              </div>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                  {['Date', 'Hours', 'Base target', 'Achieved'].map(h => <th key={h} style={th}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {detail.calendar.map(d => {
                  const cell = d.cats[calCat]
                  const unit = detail.categories[calCat]?.unit || 'count'
                  return (
                    <tr key={d.date} style={{
                      borderBottom: '1px solid var(--border)',
                      background: d.is_today ? '#eff6ff' : 'transparent',
                    }}>
                      <td style={{ ...td, fontWeight: d.is_today ? 700 : 400 }}>
                        {d.date}{d.is_today ? ' · today' : ''}
                      </td>
                      <td style={td}>{fmtN(d.hours, 1)}</td>
                      <td style={td}>{val(unit, cell?.base ?? 0)}</td>
                      <td style={{ ...td, color: cell?.achieved == null ? 'var(--text3)' : (cell.achieved >= (cell.base ?? 0) ? 'var(--green)' : '#b45309') }}>
                        {cell?.achieved == null ? '—' : val(unit, cell.achieved)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
