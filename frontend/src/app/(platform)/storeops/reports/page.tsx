'use client'
import { useState, useEffect } from 'react'
import { api, fmt } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'

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
  const withMkt = rows.map(r => ({ ...r, market: mktOf[r.store] || '' }))

  // per-store rollup (all rows for the month; ReportShell handles any further filtering)
  const byStore = Object.values(withMkt.reduce((acc: any, r) => {
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

  const tot = withMkt.reduce((t, r) => ({
    sh: t.sh + (r.scheduled_hours || 0), ah: t.ah + (r.actual_hours || 0),
    sp: t.sp + (r.scheduled_pay || 0), ap: t.ap + (r.actual_pay || 0),
  }), { sh: 0, ah: 0, sp: 0, ap: 0 })

  const monthName = new Date(month + '-01T00:00:00').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

  const empCols: ExportColumn[] = [
    { header: 'Employee', get: r => r.name, role: 'rep' },
    { header: 'Store', get: r => r.store, role: 'store' },
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
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Market', get: r => r.market },
    { header: 'Employees', get: r => r.employees, align: 'right' },
    { header: 'Sched Hrs', get: r => Math.round(r.scheduled_hours * 10) / 10, align: 'right' },
    { header: 'Actual Hrs', get: r => Math.round(r.actual_hours * 10) / 10, align: 'right' },
    { header: 'Sched Pay', get: r => r.scheduled_pay, money: true },
    { header: 'Actual Pay', get: r => r.actual_pay, money: true },
  ]

  const Tile = ({ label, value }: { label: string; value: string }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 130 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{value}</div>
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📋 StoreOps Reports — Hours & Payroll</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Scheduled vs actual hours and pay, per employee and per store. {monthName}.</p>
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
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
        <Tile label="Scheduled Hrs" value={tot.sh.toFixed(1)} />
        <Tile label="Actual Hrs" value={tot.ah.toFixed(1)} />
        <Tile label="Scheduled Pay" value={fmt(tot.sp)} />
        <Tile label="Actual Pay" value={fmt(tot.ap)} />
        <Tile label="Employees" value={String(withMkt.length)} />
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : withMkt.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No hours for {monthName}. Payroll is computed from entered shifts — add shifts in the Schedule for this month.
        </div>
      ) : (
        <ReportShell
          title={`StoreOps Hours & Payroll — ${monthName}`}
          subtitle={view === 'employee' ? 'By employee' : 'By store'}
          filename={`storeops-payroll-${month}`}
          columns={view === 'employee' ? empCols : storeCols}
          rows={view === 'employee' ? withMkt : (byStore as any[])}
        />
      )}
    </div>
  )
}
