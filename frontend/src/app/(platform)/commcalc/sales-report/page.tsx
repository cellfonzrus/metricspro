'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'

// The sales actually done across all stores, from the imported Sales Transaction Details
// (raw_sales, falling back to the daily email feed). One row per store + rep + day; ReportShell
// adds the rep/store/date/month filters, add-your-own filter, group-by, export and send.
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

function thisMonth() { return new Date().toISOString().slice(0, 7) }

export default function SalesReportPage() {
  const [period, setPeriod] = useState(thisMonth())
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    api(`/api/v1/commcalc/sales-report?period=${encodeURIComponent(period)}`)
      .then(setData).catch(e => setData({ rows: [], totals: {}, error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const t = data?.totals || {}
  // Distinct months available across both sales tables (for the picker).
  const months = Array.from(new Set((data?.periods || []).map((p: string) => {
    const s = String(p)
    if (/^\d{4}-\d{2}/.test(s)) return s.slice(0, 7)
    const d = new Date(s + ' 1'); return isNaN(d.getTime()) ? null : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  }).filter(Boolean))).sort().reverse() as string[]

  const cols: ExportColumn[] = [
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Rep', get: r => r.salesperson, role: 'rep' },
    { header: 'Date', get: r => r.trans_date, type: 'date' },
    { header: 'Txns', get: r => r.txns, align: 'right' },
    { header: 'Activations', get: r => r.activations, align: 'right' },
    { header: 'Upgrades', get: r => r.upgrades, align: 'right' },
    { header: 'Accessory $', get: r => r.accessory_rev, money: true },
    { header: 'Revenue $', get: r => r.revenue, money: true },
    { header: 'GP $', get: r => r.gp, money: true },
  ]

  const Tile = ({ label, value }: { label: string; value: string }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 120 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{value}</div>
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Sales Report</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Sales done across all stores, from the imported Sales Transaction Details. Filter by rep, store, date or
          month, add your own filter, group by any column, then export or send to a rep.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Month{' '}
          {months.length > 0
            ? <select style={sel} value={period.length === 7 ? period : ''} onChange={e => setPeriod(e.target.value)}>
                {months.map(m => <option key={m} value={m}>{m}</option>)}
                {!months.includes(period) && <option value={period}>{period}</option>}
              </select>
            : <input type="month" style={sel} value={period.length === 7 ? period : thisMonth()} onChange={e => setPeriod(e.target.value)} />}
        </label>
        {data?.source === 'daily_sales_feed' && <span style={{ fontSize: 11, color: '#b45309' }}>source: daily email feed (raw_sales not promoted yet — enable ‘auto’ on Connectors)</span>}
        {data?.error && <span style={{ fontSize: 12, color: '#dc2626' }}>❌ {data.error}</span>}
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
        <Tile label="Revenue" value={fmt(t.revenue || 0)} />
        <Tile label="Gross Profit" value={fmt(t.gp || 0)} />
        <Tile label="Accessory $" value={fmt(t.accessory_rev || 0)} />
        <Tile label="Transactions" value={String(t.txns || 0)} />
        <Tile label="Activations" value={String(t.activations || 0)} />
        <Tile label="Upgrades" value={String(t.upgrades || 0)} />
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No sales for {period}. Sales come from the imported Sales Transaction Details — check the month, or that the
          daily feed / monthly upload has loaded on the Imports pages.
        </div>
      ) : (
        <ReportShell
          title={`Sales Report — ${period}`}
          subtitle="All stores · from Sales Transaction Details"
          filename={`sales-report-${period.replace(/\s+/g, '-')}`}
          columns={cols}
          rows={rows}
        />
      )}
    </div>
  )
}
