'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'

function ymd(d: Date) {
  const y = d.getFullYear(), m = String(d.getMonth()+1).padStart(2,'0'), day = String(d.getDate()).padStart(2,'0')
  return `${y}-${m}-${day}`
}
function upcomingThursday(from = new Date()) {
  const d = new Date(from); const diff = (4 - d.getDay() + 7) % 7
  d.setDate(d.getDate() + diff); return d
}
function shiftThursday(iso: string, weeks: number) {
  const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + weeks*7); return ymd(d)
}
function pretty(iso: string) {
  return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { weekday:'short', month:'short', day:'numeric', year:'numeric' })
}

type Bucket = { count: number; owed: number }
type Report = {
  thursday: string
  due_this_week: { sold: Bucket; aging: Bucket; total: Bucket }
  by_store: { store: string; market: string; sold_count: number; sold_owed: number; aging_count: number; aging_owed: number; total_owed: number }[]
  upcoming: { thursday: string; sold_owed: number; aging_owed: number; total_owed: number; count: number }[]
  rows: any[]
  total_due_rows: number
}

function Kpi({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="card" style={{ padding: '18px 22px' }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color: color || 'var(--text1)' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

export default function OwedWeeklyPage() {
  const [thursday, setThursday] = useState(ymd(upcomingThursday()))
  const [market, setMarket] = useState('')
  const [store, setStore] = useState('')
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{store:string;market:string}[]>([])
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api(`/api/v1/asset/filter-options?org_id=${ORG_ID}`)
      .then((d:any) => { setMarkets(d.markets || []); setStores(d.stores || []) })
      .catch(console.error)
  }, [])

  useEffect(() => { load() }, [thursday, market, store])

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID, thursday })
      if (market) qs.set('market', market)
      if (store) qs.set('store', store)
      const d = await api(`/api/v1/asset/owed-weekly?${qs.toString()}`)
      setReport(d)
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  const visibleStores = market ? stores.filter(s => s.market === market) : stores
  const selStyle = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <a href="/commcalc/asset" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Asset Ledger</a>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>Weekly Owed to VIP</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          What VIP collects each Thursday — phones sold (billed the following Thursday) and aged inventory past 60 days.
        </p>
      </div>

      {/* Controls */}
      <div className="card" style={{ padding: 14, marginBottom: 20, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn" onClick={() => setThursday(shiftThursday(thursday, -1))}>◀ Prev</button>
        <div style={{ fontWeight: 700, fontSize: 15, minWidth: 200, textAlign: 'center' }}>{pretty(thursday)}</div>
        <button className="btn" onClick={() => setThursday(shiftThursday(thursday, 1))}>Next ▶</button>
        <button className="btn" onClick={() => setThursday(ymd(upcomingThursday()))}>This week</button>
        <div style={{ flex: 1 }} />
        <select style={selStyle} value={market} onChange={e => { setMarket(e.target.value); setStore('') }}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select style={selStyle} value={store} onChange={e => setStore(e.target.value)}>
          <option value="">All stores</option>
          {visibleStores.map(s => <option key={s.store} value={s.store}>{s.store}</option>)}
        </select>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>Loading…</div>
      ) : !report ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>No data.</div>
      ) : (
        <>
          {/* KPI cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 24 }}>
            <Kpi label="Total Due This Thursday" value={fmt(report.due_this_week.total.owed)} sub={`${report.due_this_week.total.count.toLocaleString()} devices`} color="var(--accent)" />
            <Kpi label="Sold Phones" value={fmt(report.due_this_week.sold.owed)} sub={`${report.due_this_week.sold.count.toLocaleString()} sold — billed this week`} color="#059669" />
            <Kpi label="Aged > 60 Days (Never Sold)" value={fmt(report.due_this_week.aging.owed)} sub={`${report.due_this_week.aging.count.toLocaleString()} devices past due date`} color="#d97706" />
          </div>

          {/* By store */}
          <div className="card" style={{ padding: 0, marginBottom: 24 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              🏬 By Store — due {pretty(report.thursday)}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Store','Market','Sold #','Sold Owed','Aged #','Aged Owed','Total Owed'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.by_store.length === 0 ? (
                    <tr><td colSpan={7} style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>Nothing bills on this Thursday for the current filter.</td></tr>
                  ) : report.by_store.map((s, i) => (
                    <tr key={s.store} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 500 }}>{s.store}</td>
                      <td style={{ padding: '9px 14px', fontSize: 12, color: 'var(--text2)' }}>{s.market || '—'}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13 }}>{s.sold_count}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13, color: '#059669' }}>{fmt(s.sold_owed)}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13 }}>{s.aging_count}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13, color: '#d97706' }}>{fmt(s.aging_owed)}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 700 }}>{fmt(s.total_owed)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Upcoming Thursdays */}
          <div className="card" style={{ padding: 0, marginBottom: 24 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              📅 Upcoming Thursdays
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--surface2)' }}>
                  {['Thursday','Devices','Sold Owed','Aged Owed','Total'].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.upcoming.length === 0 ? (
                  <tr><td colSpan={5} style={{ padding: 20, textAlign: 'center', color: 'var(--text3)' }}>No upcoming billings in range.</td></tr>
                ) : report.upcoming.map((u, i) => (
                  <tr key={u.thursday}
                      onClick={() => setThursday(u.thursday)}
                      style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)', cursor: 'pointer' }}>
                    <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 500 }}>{pretty(u.thursday)}</td>
                    <td style={{ padding: '9px 14px', fontSize: 13 }}>{u.count.toLocaleString()}</td>
                    <td style={{ padding: '9px 14px', fontSize: 13, color: '#059669' }}>{fmt(u.sold_owed)}</td>
                    <td style={{ padding: '9px 14px', fontSize: 13, color: '#d97706' }}>{fmt(u.aging_owed)}</td>
                    <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 700 }}>{fmt(u.total_owed)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Device rows */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              📱 Devices billing this Thursday <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>({report.rows.length.toLocaleString()} of {report.total_due_rows.toLocaleString()})</span>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Store','Device','IMEI/ESN','Phone','Contract','Path','Sold','Due','Owed'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.rows.map((r, i) => (
                    <tr key={r.id} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.store || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.device_model || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 11, fontFamily: 'monospace' }}>{r.esn_imei || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.phone_number || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.contract_type || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 11 }}>
                        <span style={{ background: r.bill_path === 'aging' ? '#fef3c7' : '#d1fae5', color: r.bill_path === 'aging' ? '#92400e' : '#065f46', borderRadius: 5, padding: '1px 7px', fontWeight: 600 }}>{r.bill_path}</span>
                      </td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.date_sold || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.due_date || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 600 }}>{fmt(r.owed_to_vip || 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}