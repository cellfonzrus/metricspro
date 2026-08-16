'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// Productivity lens: rank reps by output/hour and show how well their hours fit the store's busy hours,
// with positive coaching/scheduling recommendations. Human-in-the-loop — recommends, never acts.
const REC_STYLE: Record<string, { icon: string; color: string }> = {
  recognize: { icon: '🌟', color: 'var(--green)' },
  schedule: { icon: '📅', color: 'var(--accent)' },
  coach: { icon: '🎓', color: '#b45309' },
}

export default function ProductivityInsightsPage() {
  const { period } = usePeriod()
  const [stores, setStores] = useState<any[]>([])
  const [storeCode, setStoreCode] = useState('')
  const [metric, setMetric] = useState('acc')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    api('/api/v1/storeops/stores').then((r: any) => {
      const list = Array.isArray(r) ? r : []
      setStores(list)
      if (!storeCode && list[0]?.store_code) setStoreCode(list[0].store_code)
    }).catch(() => {})
  }, [])

  async function load() {
    if (!storeCode) { setMsg('Pick a store.'); return }
    setLoading(true); setMsg('')
    try {
      const r: any = await api(`/api/v1/commcalc/productivity-insights/${encodeURIComponent(period)}?store_code=${encodeURIComponent(storeCode)}&metric=${metric}&org_id=${ORG_ID}`)
      setData(r)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)); setData(null) } finally { setLoading(false) }
  }
  useEffect(() => { if (storeCode) load() }, [storeCode, period, metric]) // eslint-disable-line

  const reps: any[] = data?.reps || []
  const recs: any[] = data?.recommendations || []
  const fmtHr = (h: number) => (h === 0 ? '12a' : h < 12 ? `${h}a` : h === 12 ? '12p' : `${h - 12}p`)

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Productivity Insights</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          {period} · output per hour + how well each rep's hours fit the store's busy hours. Suggestions only — you decide.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <select className="select" value={storeCode} onChange={e => setStoreCode(e.target.value)}>
          {stores.length === 0 && <option value="">(no stores)</option>}
          {stores.map((s: any) => <option key={s.store_code || s.id} value={s.store_code}>{s.store_code}{s.address ? ` · ${String(s.address).substring(0, 30)}` : ''}</option>)}
        </select>
        <div style={{ display: 'flex', background: 'var(--surface2)', padding: 3, borderRadius: 8, gap: 3 }}>
          {[['acc', 'Accessory $/hr'], ['boxes', 'Boxes/hr']].map(([k, l]) => (
            <button key={k} onClick={() => setMetric(k)} className="btn" style={{
              fontSize: 12.5, background: metric === k ? 'white' : 'transparent',
              color: metric === k ? 'var(--accent)' : 'var(--text2)', boxShadow: metric === k ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
            }}>{l}</button>
          ))}
        </div>
        <button className="btn" disabled={loading} onClick={load}>{loading ? '…' : 'Refresh'}</button>
      </div>

      {msg && <div style={{ fontSize: 12.5, color: 'var(--text2)', background: 'var(--surface2)', borderRadius: 8, padding: '8px 12px', marginBottom: 12 }}>{msg}</div>}

      {data && (
        <>
          {recs.length > 0 && (
            <div className="card" style={{ padding: 14, marginBottom: 14 }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>Recommendations</div>
              {recs.map((rec, i) => {
                const st = REC_STYLE[rec.kind] || { icon: '•', color: 'var(--text2)' }
                return (
                  <div key={i} style={{ display: 'flex', gap: 8, padding: '6px 0', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                    <span style={{ fontSize: 16 }}>{st.icon}</span>
                    <span style={{ fontSize: 13, color: 'var(--text)' }}><b style={{ color: st.color }}>{rec.kind}</b> — {rec.text}</span>
                  </div>
                )
              })}
            </div>
          )}

          <div className="card" style={{ padding: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
              <div style={{ fontWeight: 700, fontSize: 14 }}>Reps by {data.metric}</div>
              <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
                Peak hours ({data.peak_basis}): {(data.peak_hours || []).map((h: number) => fmtHr(h)).join(', ') || '—'} · {data.timezone}
              </div>
            </div>
            <div style={{ overflowX: 'auto', marginTop: 10 }}>
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead><tr>{['#', 'Rep', data.metric, 'Hours', 'Peak hrs', 'Peak share'].map(h => <th key={h} style={{ textAlign: h === 'Rep' ? 'left' : 'right', padding: '4px 12px 8px 0', color: 'var(--text3)' }}>{h}</th>)}</tr></thead>
                <tbody>
                  {reps.map((r, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right', color: 'var(--text3)' }}>{r.output_rank}</td>
                      <td style={{ padding: '5px 12px 5px 0', fontWeight: 500 }}>{r.name}</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right', fontWeight: 600 }}>{r.output_per_hour == null ? '—' : (metric === 'acc' ? '$' : '') + r.output_per_hour}</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>{r.hours}</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>{r.peak_hours}</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>
                        {r.peak_share == null ? '—' : (
                          <span style={{ color: r.peak_share >= 0.5 ? 'var(--green)' : 'var(--text2)' }}>{Math.round(r.peak_share * 100)}%</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {reps.length === 0 && <tr><td colSpan={6} style={{ padding: 12, color: 'var(--text3)' }}>No rep data for this store/period.</td></tr>}
                </tbody>
              </table>
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8 }}>
              Peak share = % of a rep's scheduled hours that fall in the store's busy hours. {!data.has_demand && 'Busy hours are a staffing proxy until time-stamped sales accumulate.'}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
