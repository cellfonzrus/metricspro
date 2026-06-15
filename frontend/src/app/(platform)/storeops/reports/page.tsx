'use client'
import { useState, useEffect } from 'react'
import { api, fmt } from '@/lib/client'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'

const cell: React.CSSProperties = { padding: '8px 10px', borderBottom: '1px solid var(--border)' }
const num: React.CSSProperties = { ...cell, textAlign: 'right' }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

function thisMonth() {
  // Avoid timezone surprises: derive YYYY-MM from an ISO string slice.
  return new Date().toISOString().slice(0, 7)
}

export default function StoreOpsReportsPage() {
  const [month, setMonth] = useState(thisMonth())
  const [rows, setRows] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<'employee' | 'store'>('employee')
  const [market, setMarket] = useState('')
  const [storeF, setStoreF] = useState('')

  function load() {
    setLoading(true)
    Promise.all([
      api(`/api/v1/storeops/payroll?month=${month}`).catch(() => []),
      api('/api/v1/storeops/stores').catch(() => []),
    ]).then(([p, s]) => { setRows(p || []); setStores(s || []) })
      .catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [month])

  // store_code -> market
  const mktOf: Record<string, string> = {}
  stores.forEach(s => { if (s.store_code) mktOf[s.store_code] = s.market || '' })
  const markets = Array.from(new Set(stores.map(s => s.market).filter(Boolean))).sort()
  const storeCodes = Array.from(new Set(rows.map(r => r.store).filter(Boolean))).sort()

  const withMkt = rows.map(r => ({ ...r, market: mktOf[r.store] || '' }))
  const filtered = withMkt.filter(r => (!market || r.market === market) && (!storeF || r.store === storeF))

  // per-store rollup
  const byStore = Object.values(filtered.reduce((acc: any, r) => {
    const k = r.store || '—'
    if (!acc[k]) acc[k] = { store: k, market: r.market, employees: 0, scheduled_hours: 0, actual_hours: 0, scheduled_pay: 0, actual_pay: 0, shifts: 0 }
    acc[k].employees += 1
    acc[k].scheduled_hours += r.scheduled_hours || 0
    acc[k].actual_hours += r.actual_hours || 0
    acc[k].scheduled_pay += r.scheduled_pay || 0
    acc[k].actual_pay += r.actual_pay || 0
    acc[k].shifts += r.shifts || 0
    return acc
  }, {})).sort((a: any, b: any) => b.actual_pay - a.actual_pay)

  const tot = filtered.reduce((t, r) => ({
    sh: t.sh + (r.scheduled_hours || 0), ah: t.ah + (r.actual_hours || 0),
    sp: t.sp + (r.scheduled_pay || 0), ap: t.ap + (r.actual_pay || 0),
  }), { sh: 0, ah: 0, sp: 0, ap: 0 })

  const monthName = new Date(month + '-01T00:00:00').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

  function buildPayload(): ExportPayload {
    const empCols: ExportColumn[] = [
      { header: 'Employee', get: r => r.name },
      { header: 'Store', get: r => r.store },
      { header: 'Market', get: r => r.market },
      { header: 'Pay $/hr', get: r => r.pay_rate, money: true },
      { header: 'Sched Hrs', get: r => Math.round((r.scheduled_hours || 0) * 10) / 10, align: 'right' },
      { header: 'Actual Hrs', get: r => Math.round((r.actual_hours || 0) * 10) / 10, align: 'right' },
      { header: 'Hrs Var', get: r => Math.round(((r.actual_hours || 0) - (r.scheduled_hours || 0)) * 10) / 10, align: 'right' },
      { header: 'Sched Pay', get: r => r.scheduled_pay, money: true },
      { header: 'Actual Pay', get: r => r.actual_pay, money: true },
      { header: 'Shifts', get: r => r.shifts, align: 'right' },
    ]
    const storeCols: ExportColumn[] = [
      { header: 'Store', get: r => r.store },
      { header: 'Market', get: r => r.market },
      { header: 'Employees', get: r => r.employees, align: 'right' },
      { header: 'Sched Hrs', get: r => Math.round(r.scheduled_hours * 10) / 10, align: 'right' },
      { header: 'Actual Hrs', get: r => Math.round(r.actual_hours * 10) / 10, align: 'right' },
      { header: 'Sched Pay', get: r => r.scheduled_pay, money: true },
      { header: 'Actual Pay', get: r => r.actual_pay, money: true },
    ]
    const flt = [market || null, storeF || null].filter(Boolean).join(' · ') || 'All stores'
    return {
      title: 'StoreOps Hours & Payroll',
      subtitle: `${monthName} · ${flt}`,
      filename: `storeops-payroll-${month}`,
      sheets: [
        { name: 'By Employee', rows: filtered, columns: empCols },
        { name: 'By Store', rows: byStore as any[], columns: storeCols },
      ],
    }
  }

  const Tile = ({ label, value }: { label: string; value: string }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 130 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{value}</div>
    </div>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📋 StoreOps Reports — Hours & Payroll</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Scheduled vs actual hours and pay, per employee and per store. {monthName}.</p>
        </div>
        {!loading && filtered.length > 0 && <ExportButtons payload={buildPayload} />}
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <input type="month" style={sel} value={month} onChange={e => setMonth(e.target.value)} />
        <div style={{ display: 'flex', background: 'var(--surface2)', padding: 3, borderRadius: 8, gap: 3 }}>
          {(['employee', 'store'] as const).map(v => (
            <button key={v} onClick={() => setView(v)} className="btn" style={{ background: view === v ? 'white' : 'transparent', color: view === v ? 'var(--accent)' : 'var(--text2)', fontSize: 13, boxShadow: view === v ? '0 1px 3px rgba(0,0,0,0.1)' : 'none' }}>
              {v === 'employee' ? '👤 By Employee' : '🏪 By Store'}
            </button>
          ))}
        </div>
        <select style={sel} value={market} onChange={e => setMarket(e.target.value)}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select style={sel} value={storeF} onChange={e => setStoreF(e.target.value)}>
          <option value="">All stores</option>
          {storeCodes.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        {(market || storeF) && <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => { setMarket(''); setStoreF('') }}>✕ Clear</button>}
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
        <Tile label="Scheduled Hrs" value={tot.sh.toFixed(1)} />
        <Tile label="Actual Hrs" value={tot.ah.toFixed(1)} />
        <Tile label="Scheduled Pay" value={fmt(tot.sp)} />
        <Tile label="Actual Pay" value={fmt(tot.ap)} />
        <Tile label="Employees" value={String(filtered.length)} />
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : filtered.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No hours for {monthName}. Payroll is computed from entered shifts — add shifts in the Schedule for this month.
        </div>
      ) : view === 'employee' ? (
        <div className="table-wrapper">
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Employee', 'Store', 'Market', 'Pay $/hr', 'Sched Hrs', 'Actual Hrs', 'Hrs Var', 'Sched Pay', 'Actual Pay', 'Shifts'].map((h, i) =>
                <th key={h} style={{ textAlign: i < 3 ? 'left' : 'right', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {filtered.sort((a, b) => (b.actual_pay || 0) - (a.actual_pay || 0)).map((r, i) => {
                const hv = (r.actual_hours || 0) - (r.scheduled_hours || 0)
                return (
                  <tr key={i}>
                    <td style={{ ...cell, fontWeight: 500 }}>{r.name}</td>
                    <td style={cell}>{r.store}</td>
                    <td style={{ ...cell, color: 'var(--text3)', fontSize: 12 }}>{r.market || '—'}</td>
                    <td style={num}>{fmt(r.pay_rate)}</td>
                    <td style={num}>{(r.scheduled_hours || 0).toFixed(1)}</td>
                    <td style={num}>{(r.actual_hours || 0).toFixed(1)}</td>
                    <td style={{ ...num, color: hv < 0 ? 'var(--red)' : hv > 0 ? 'var(--amber)' : 'var(--text3)' }}>{hv > 0 ? '+' : ''}{hv.toFixed(1)}</td>
                    <td style={num}>{fmt(r.scheduled_pay)}</td>
                    <td style={{ ...num, fontWeight: 600 }}>{fmt(r.actual_pay)}</td>
                    <td style={num}>{r.shifts}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="table-wrapper">
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Store', 'Market', 'Employees', 'Sched Hrs', 'Actual Hrs', 'Sched Pay', 'Actual Pay'].map((h, i) =>
                <th key={h} style={{ textAlign: i < 2 ? 'left' : 'right', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {(byStore as any[]).map((r, i) => (
                <tr key={i}>
                  <td style={{ ...cell, fontWeight: 500 }}>{r.store}</td>
                  <td style={{ ...cell, color: 'var(--text3)', fontSize: 12 }}>{r.market || '—'}</td>
                  <td style={num}>{r.employees}</td>
                  <td style={num}>{r.scheduled_hours.toFixed(1)}</td>
                  <td style={num}>{r.actual_hours.toFixed(1)}</td>
                  <td style={num}>{fmt(r.scheduled_pay)}</td>
                  <td style={{ ...num, fontWeight: 600 }}>{fmt(r.actual_pay)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
