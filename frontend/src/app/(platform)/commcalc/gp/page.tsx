'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'

interface StoreRow {
  store: string; store_code: string; market: string
  acc_gp: number; setup_gp: number; phone_sales: number; plan_gp: number; other_gp: number
  comm: number; reimb: number; mdf: number; chargeback: number; unmapped: number
  comp_comm: number; comp_reimb: number; comp_mdf: number
  mi: number; atu: number; total_rev: number
  rep_pay: number; exp_total: number; net_phone_cost: number
  net_profit: number; net_excl_mdf: number
}

interface ColDef { key: string; label: string; group: string; bold?: boolean; red?: boolean; highlight?: boolean }
const COLS: ColDef[] = [
  { key: 'acc_gp',       label: 'Acc GP',      group: 'Revenue' },
  { key: 'setup_gp',     label: 'Setup GP',    group: 'Revenue' },
  { key: 'phone_sales',  label: 'Phone Sales', group: 'Revenue' },
  { key: 'plan_gp',      label: 'Plan GP',     group: 'Revenue' },
  { key: 'other_gp',     label: 'Other',       group: 'Revenue' },
  { key: 'comm',         label: 'Commission',  group: 'Payments' },
  { key: 'reimb',        label: 'Re-imb',      group: 'Payments' },
  { key: 'mdf',          label: 'MDF',         group: 'Payments' },
  { key: 'comp_comm',    label: 'Comp Comm',   group: 'Payments' },
  { key: 'comp_reimb',   label: 'Comp Rebate', group: 'Payments' },
  { key: 'comp_mdf',     label: 'Comp MDF',    group: 'Payments' },
  { key: 'chargeback',   label: 'Chargebacks', group: 'Payments' },
  { key: 'mi',           label: 'MI',          group: 'Payments' },
  { key: 'atu',          label: 'ATU',         group: 'Payments' },
  { key: 'total_rev',    label: 'Total Rev',   group: 'Summary', bold: true },
  { key: 'rep_pay',      label: '−Rep Pay',    group: 'Deductions', red: true },
  { key: 'exp_total',    label: '−Expenses',   group: 'Deductions', red: true },
  { key: 'net_phone_cost', label: '−Phone Cost', group: 'Deductions', red: true },
  { key: 'net_profit',   label: 'Net Profit',  group: 'Summary', bold: true, highlight: true },
  { key: 'net_excl_mdf', label: 'Excl. MDF',  group: 'Summary' },
]

export default function GPReportPage() {
  const [period] = useState('April 2026')
  const [view, setView] = useState<'store'|'rep'>('store')
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selStores, setSelStores] = useState<string[]>([])
  const [data, setData] = useState<any>({})
  const [loading, setLoading] = useState(true)
  const [markets, setMarkets] = useState<string[]>([])

  useEffect(() => {
    setLoading(true)
    api(`/api/v1/commcalc/gp/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(d => {
        setData(d || {})
        if (d?.store_rows) {
          const mkts = [...new Set(d.store_rows.map((r: StoreRow) => r.market).filter(Boolean))].sort() as string[]
          setMarkets(mkts)
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [period])

  const allRows: StoreRow[] = data.store_rows || []
  const rows: StoreRow[] = allRows.filter(r => {
    if (selMarkets.length && !selMarkets.includes(r.market)) return false
    if (selStores.length && !selStores.includes(r.store)) return false
    return true
  })
  const repRows: any[] = (data.rep_rows || [])
  const totals: any = {}
  COLS.forEach(c2 => { totals[c2.key] = rows.reduce((s, r) => s + ((r as any)[c2.key] || 0), 0) })

  function Cell({ val, col }: { val: number; col: ColDef }) {
    const color = col.highlight
      ? val >= 0 ? 'var(--green)' : 'var(--red)'
      : col.red ? 'var(--red)' : undefined
    return (
      <td style={{ textAlign: 'right', fontWeight: col.bold ? 700 : 400, color, fontSize: 12, padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
        {fmt(val)}
      </td>
    )
  }

  function exportCSV() {
    const head = ['Store', 'Market', ...COLS.map(c => c.label)].join(',')
    const csvRows = rows.map(r => [
      `"${r.store}"`, `"${r.market}"`,
      ...COLS.map(c => r[c.key as keyof StoreRow]?.toString() || '0'),
    ].join(','))
    const a = document.createElement('a')
    a.href = 'data:text/csv,' + encodeURIComponent([head, ...csvRows].join('\n'))
    a.download = `gp-report-${period.replace(' ', '-')}.csv`
    a.click()
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Gross Profit Report</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · {rows.length} stores · Net: <strong style={{ color: totals.net_profit >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmt(totals.net_profit || 0)}</strong>
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
            {markets.map(m => (
              <button key={m} onClick={() => setSelMarkets(s => s.includes(m) ? s.filter(x => x !== m) : [...s, m])}
                className="btn" style={{
                  fontSize: 12, padding: '4px 10px',
                  background: selMarkets.includes(m) ? 'var(--accent)' : 'var(--surface2)',
                  color: selMarkets.includes(m) ? 'white' : 'var(--text2)',
                }}>
                {m}
              </button>
            ))}
            {(selMarkets.length > 0 || selStores.length > 0) && (
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }}
                onClick={() => { setSelMarkets([]); setSelStores([]) }}>✕ Clear</button>
            )}
          </div>
          <select className="select" value="" onChange={e => {
            const v = e.target.value
            if (v && !selStores.includes(v)) setSelStores(s => [...s, v])
          }}>
            <option value="">+ Add store filter</option>
            {allRows.map(r => <option key={r.store} value={r.store}>{r.store.substring(0, 40)}</option>)}
          </select>
          <div style={{ display: 'flex', background: 'var(--surface2)', padding: 3, borderRadius: 8, gap: 3 }}>
            {(['store', 'rep'] as const).map(v => (
              <button key={v} onClick={() => setView(v)} className="btn" style={{
                background: view === v ? 'white' : 'transparent',
                color: view === v ? 'var(--accent)' : 'var(--text2)',
                fontSize: 13, boxShadow: view === v ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
              }}>
                {v === 'store' ? '🏪 By Store' : '👤 By Rep'}
              </button>
            ))}
          </div>
          <button className="btn btn-secondary" onClick={exportCSV}>📥 CSV</button>
        </div>
      </div>

      {/* Summary cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        {[
          { label: 'Total Revenue', val: totals.total_rev, icon: '💰' },
          { label: 'Rep Commissions', val: totals.rep_pay, icon: '👥', red: true },
          { label: 'Store Expenses', val: totals.exp_total, icon: '🏪', red: true },
          { label: 'Net Profit', val: totals.net_profit, icon: '📊', highlight: true },
        ].map(({ label, val, icon, red, highlight }) => (
          <div key={label} className="card">
            <div style={{ fontSize: 22 }}>{icon}</div>
            <div style={{ fontSize: 20, fontWeight: 700, marginTop: 8,
              color: highlight ? (val >= 0 ? 'var(--green)' : 'var(--red)') : red ? 'var(--red)' : 'var(--accent)' }}>
              {fmt(val || 0)}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Rep view */}
      {!loading && view === 'rep' && (
        <div className="table-wrapper" style={{ marginBottom: 20 }}>
          <table>
            <thead>
              <tr>
                <th>Rep</th><th>Store</th>
                <th style={{ textAlign: 'right' }}>Acc GP</th>
                <th style={{ textAlign: 'right' }}>Setup GP</th>
                <th style={{ textAlign: 'right' }}>Phone Sales</th>
                <th style={{ textAlign: 'right' }}>Plan GP</th>
                <th style={{ textAlign: 'right' }}>Comm Earned</th>
              </tr>
            </thead>
            <tbody>
              {repRows
                .filter((r: any) => !selStores.length || selStores.some(s => r.store?.includes(s.split(' ')[0])))
                .map((r: any, i: number) => (
                <tr key={i}>
                  <td style={{ fontWeight: 500 }}>{r.storeops_name || r.rep}</td>
                  <td style={{ fontSize: 12, color: 'var(--text3)' }}>{r.store?.substring(0, 30)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.acc_gp)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.setup_gp)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.phone_sales)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.plan_gp)}</td>
                  <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>{fmt(r.comm_earned)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Full GP table */}
      {view === 'store' && loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : view === 'store' && (
        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 320px)', background: 'white', border: '1px solid var(--border)', borderRadius: 12 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1600 }}>
            <thead>
              <tr>
                <th style={{ padding: '12px 14px', color: 'white', fontSize: 12, fontWeight: 700, letterSpacing: '0.03em', textAlign: 'left', position: 'sticky', left: 0, top: 0, zIndex: 3, background: '#1e3a5f', width: 200 }}>
                  STORE
                </th>
                {COLS.map(c => (
                  <th key={c.key} style={{ padding: '12px 10px', color: 'white', fontSize: 12, fontWeight: 700, letterSpacing: '0.03em', textAlign: 'right',
                    position: 'sticky', top: 0, zIndex: 2, background: '#1e3a5f', whiteSpace: 'nowrap',
                    borderLeft: ['comm', 'total_rev', 'rep_pay', 'net_profit'].includes(c.key) ? '2px solid rgba(255,255,255,0.25)' : undefined }}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} style={{ background: i % 2 === 1 ? '#fafbfc' : 'white' }}>
                  <td style={{ padding: '8px 14px', fontWeight: 500, fontSize: 12, position: 'sticky', left: 0, background: i % 2 === 1 ? '#fafbfc' : 'white', borderBottom: '1px solid var(--border)' }}>
                    <div>{r.store?.substring(0, 30)}</div>
                    <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 999, background: '#dbeafe', color: '#1e40af', fontWeight: 600 }}>
                      {r.market || '—'}
                    </span>
                  </td>
                  {COLS.map(c => <Cell key={c.key} val={(r as any)[c.key] || 0} col={c} />)}
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={COLS.length + 1} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  No data — upload files and run calculation
                </td></tr>
              )}
            </tbody>
            {rows.length > 0 && (
              <tfoot>
                <tr style={{ background: '#1e3a5f', fontWeight: 700 }}>
                  <td style={{ padding: '10px 14px', color: 'white', fontSize: 12, position: 'sticky', left: 0, background: '#1e3a5f' }}>
                    TOTAL ({rows.length} stores)
                  </td>
                  {COLS.map(c => {
                    const val = (totals as any)[c.key] || 0
                    const color = c.highlight ? (val >= 0 ? '#86efac' : '#fca5a5') : c.red ? '#fca5a5' : 'white'
                    return (
                      <td key={c.key} style={{ padding: '10px 10px', textAlign: 'right', color, fontSize: 12,
                        borderLeft: ['comm', 'total_rev', 'rep_pay', 'net_profit'].includes(c.key) ? '2px solid rgba(255,255,255,0.2)' : undefined }}>
                        {fmt(val)}
                      </td>
                    )
                  })}
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </div>
  )
}
