'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// Staffing heat map: store-local weekday × hour demand → staff required, vs scheduled & actual heads.
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const VIEWS = [
  { k: 'required', label: 'Staff required', kind: 'seq' as const, get: (c: any) => c.required },
  { k: 'txn', label: 'Transaction demand', kind: 'seq' as const, get: (c: any) => c.txn },
  { k: 'scheduled', label: 'Scheduled heads', kind: 'seq' as const, get: (c: any) => c.scheduled },
  { k: 'actual', label: 'Actual heads', kind: 'seq' as const, get: (c: any) => c.actual },
  { k: 'gap', label: 'Coverage gap (sched − required)', kind: 'div' as const, get: (c: any) => c.gap },
]
const hourLabel = (h: number) => (h === 0 ? '12a' : h < 12 ? `${h}a` : h === 12 ? '12p' : `${h - 12}p`)

export default function StaffingHeatmapPage() {
  const { period } = usePeriod()
  const [stores, setStores] = useState<any[]>([])
  const [storeCode, setStoreCode] = useState('')
  const [capacity, setCapacity] = useState(12)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [view, setView] = useState('required')
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
      const r: any = await api(`/api/v1/storeops/staffing-heatmap?store_code=${encodeURIComponent(storeCode)}&period=${encodeURIComponent(period)}&capacity=${capacity}&org_id=${ORG_ID}`)
      setData(r)
      if (!r.has_demand) setMsg('No transaction-time data yet for this store/period — demand & required fill in as new time-stamped sales upload (historical rows are date-only). Scheduled & actual are shown now.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)); setData(null) } finally { setLoading(false) }
  }
  useEffect(() => { if (storeCode) load() }, [storeCode, period]) // eslint-disable-line

  const active = VIEWS.find(v => v.k === view)!
  const cells: any[] = data?.grid || []
  const cellAt = (wd: number, hr: number) => cells.find(c => c.weekday === wd && c.hour === hr)
  const maxAbs = Math.max(1, ...cells.map(c => Math.abs(active.get(c))))

  function bg(c: any) {
    const v = active.get(c)
    if (active.kind === 'div') {
      if (!v) return 'transparent'
      const t = Math.min(1, Math.abs(v) / maxAbs)
      return v < 0 ? `rgba(220,38,38,${0.15 + 0.65 * t})` : `rgba(22,163,74,${0.15 + 0.6 * t})`
    }
    if (!v) return 'transparent'
    return `rgba(37,99,235,${0.12 + 0.72 * Math.min(1, v / maxAbs)})`
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Staffing Heat Map</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          {period} · store-local hours · transaction demand → staff required, vs scheduled & actual heads.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <select className="select" value={storeCode} onChange={e => setStoreCode(e.target.value)}>
          {stores.length === 0 && <option value="">(no stores)</option>}
          {stores.map((s: any) => <option key={s.store_code || s.id} value={s.store_code}>{s.store_code}{s.address ? ` · ${String(s.address).substring(0, 30)}` : ''}</option>)}
        </select>
        <label style={{ fontSize: 13, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 6 }}>
          Capacity
          <input type="number" min={1} value={capacity} onChange={e => setCapacity(Number(e.target.value) || 1)} style={{ width: 64 }} title="Transactions one employee handles per hour" />
          <span style={{ color: 'var(--text3)' }}>txns/employee-hr</span>
        </label>
        <button className="btn" disabled={loading} onClick={load}>{loading ? '…' : 'Refresh'}</button>
        <div style={{ display: 'flex', background: 'var(--surface2)', padding: 3, borderRadius: 8, gap: 3, marginLeft: 'auto' }}>
          {VIEWS.map(v => (
            <button key={v.k} onClick={() => setView(v.k)} className="btn" style={{
              fontSize: 12, background: view === v.k ? 'white' : 'transparent',
              color: view === v.k ? 'var(--accent)' : 'var(--text2)', boxShadow: view === v.k ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
            }}>{v.label}</button>
          ))}
        </div>
      </div>

      {msg && <div style={{ fontSize: 12.5, color: 'var(--text2)', background: 'var(--surface2)', borderRadius: 8, padding: '8px 12px', marginBottom: 12 }}>{msg}</div>}

      {data && (
        <div className="card" style={{ padding: 14, overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr>
                <th style={{ padding: '2px 6px', textAlign: 'left', position: 'sticky', left: 0, background: 'var(--surface)' }}></th>
                {HOURS.map(h => <th key={h} style={{ padding: '2px 3px', fontWeight: 500, color: 'var(--text3)', minWidth: 26 }}>{hourLabel(h)}</th>)}
              </tr>
            </thead>
            <tbody>
              {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((wl, wd) => (
                <tr key={wd}>
                  <td style={{ padding: '2px 8px 2px 2px', fontWeight: 600, color: 'var(--text2)', position: 'sticky', left: 0, background: 'var(--surface)' }}>{wl}</td>
                  {HOURS.map(h => {
                    const c = cellAt(wd, h)
                    if (!c) return <td key={h} />
                    const v = active.get(c)
                    return (
                      <td key={h} title={`${wl} ${hourLabel(h)} — demand ${c.txn}/day, required ${c.required}, scheduled ${c.scheduled}, actual ${c.actual}, gap ${c.gap}`}
                        style={{ background: bg(c), textAlign: 'center', height: 26, minWidth: 26, border: '1px solid var(--border)', color: 'var(--text)', cursor: 'default' }}>
                        {v ? (Number.isInteger(v) ? v : v.toFixed(1)) : ''}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ display: 'flex', gap: 16, marginTop: 10, fontSize: 11.5, color: 'var(--text3)', flexWrap: 'wrap' }}>
            <span>Timezone: <b>{data.timezone}</b></span>
            <span>Capacity: <b>{data.capacity}</b> txns/employee-hr</span>
            {active.kind === 'div' && <span><span style={{ color: '#dc2626' }}>■</span> understaffed vs demand · <span style={{ color: '#16a34a' }}>■</span> overstaffed</span>}
          </div>
        </div>
      )}
    </div>
  )
}
