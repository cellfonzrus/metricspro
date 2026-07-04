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
  const [drill, setDrill] = useState<any>(null)          // the clicked (store, rep, day) cell
  const [detail, setDetail] = useState<any>(null)        // its transactions
  const [drillBusy, setDrillBusy] = useState(false)
  const [openTxn, setOpenTxn] = useState<Record<string, boolean>>({})

  function openDrill(r: any) {
    setDrill(r); setDetail(null); setOpenTxn({}); setDrillBusy(true)
    const qs = new URLSearchParams({ period, store: r.store || '', salesperson: r.salesperson || '', date: r.trans_date || '' })
    api(`/api/v1/commcalc/sales-report/detail?${qs.toString()}`)
      .then(setDetail).catch(e => setDetail({ transactions: [], error: String(e?.message || e) }))
      .finally(() => setDrillBusy(false))
  }

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
          onRowClick={openDrill}
        />
      )}

      {!loading && rows.length > 0 && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>💡 Click any row to see the individual transactions behind it.</div>}

      {/* Transaction drill-down */}
      {drill && (
        <div onClick={() => setDrill(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div onClick={e => e.stopPropagation()} className="card" style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 'min(820px,97vw)', maxHeight: '88vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 8 }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{drill.store} · {drill.salesperson}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{drill.trans_date} · {detail?.txn_count ?? drill.txns} transaction{(detail?.txn_count ?? drill.txns) === 1 ? '' : 's'}</div>
              </div>
              <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={() => setDrill(null)}>✕</button>
            </div>
            {drillBusy ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>Loading transactions…</div>
            ) : detail?.error ? (
              <div style={{ padding: 20, color: '#dc2626', fontSize: 13 }}>❌ {detail.error}</div>
            ) : (detail?.transactions || []).length === 0 ? (
              <div style={{ padding: 20, color: 'var(--text3)', fontSize: 13 }}>No transaction detail found for this cell.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {detail.transactions.map((t: any) => {
                  const open = !!openTxn[t.trans_id]
                  return (
                    <div key={t.trans_id} className="card" style={{ padding: 0, border: '1px solid var(--border)', borderRadius: 8 }}>
                      <div onClick={() => setOpenTxn(o => ({ ...o, [t.trans_id]: !o[t.trans_id] }))}
                        style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '9px 12px', cursor: 'pointer', flexWrap: 'wrap' }}>
                        <span style={{ color: 'var(--text3)', width: 12 }}>{open ? '▾' : '▸'}</span>
                        <span style={{ fontFamily: 'monospace', fontSize: 12 }}>#{t.trans_id}</span>
                        {t.customer && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{t.customer}</span>}
                        <span style={{ fontSize: 11, color: 'var(--text3)' }}>{t.line_count} line{t.line_count === 1 ? '' : 's'}</span>
                        <div style={{ flex: 1 }} />
                        <span style={{ fontSize: 12, color: 'var(--text3)' }}>GP {fmt(t.gp)}</span>
                        <span style={{ fontSize: 14, fontWeight: 700 }}>{fmt(t.total)}</span>
                      </div>
                      {open && (
                        <div style={{ borderTop: '1px solid var(--border)', overflowX: 'auto' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                            <thead><tr style={{ background: 'var(--surface2)' }}>
                              {['Department', 'Category', 'Contract', 'Product', 'MDN', 'Serial', 'Price', 'GP'].map(h =>
                                <th key={h} style={{ textAlign: h === 'Price' || h === 'GP' ? 'right' : 'left', padding: '5px 8px', fontSize: 10, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
                            </tr></thead>
                            <tbody>
                              {t.lines.map((l: any, i: number) => (
                                <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                                  <td style={{ padding: '5px 8px' }}>{l.department || '—'}</td>
                                  <td style={{ padding: '5px 8px' }}>{l.category || '—'}</td>
                                  <td style={{ padding: '5px 8px' }}>{l.contract_type || '—'}</td>
                                  <td style={{ padding: '5px 8px' }}>{l.product || '—'}{l.sku ? <span style={{ color: 'var(--text3)' }}> · {l.sku}</span> : ''}</td>
                                  <td style={{ padding: '5px 8px' }}>{l.mdn || '—'}</td>
                                  <td style={{ padding: '5px 8px', fontFamily: 'monospace', fontSize: 11 }}>{l.serial || '—'}</td>
                                  <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(l.ext_price)}</td>
                                  <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(l.gp)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
