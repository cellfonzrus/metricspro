'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'right', padding: '7px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const thL: React.CSSProperties = { ...th, textAlign: 'left' }
const td: React.CSSProperties = { textAlign: 'right', padding: '7px 10px', borderTop: '1px solid var(--border)', fontSize: 13, whiteSpace: 'nowrap' }
const tdL: React.CSSProperties = { ...td, textAlign: 'left' }

const thisMonth = () => localToday().slice(0, 7)

export default function ClosingDashboard() {
  const { user, permissions } = useAuth()
  const [period, setPeriod] = useState(thisMonth())
  const [market, setMarket] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'store' | 'rep'>('store')

  useEffect(() => { if (user?.market && permissions?.scope === 'market') setMarket(user.market) }, [user, permissions])

  const load = useCallback(() => {
    if (!period) return
    setLoading(true)
    api(`/api/v1/closing/rollup?period=${period}${market ? `&market=${encodeURIComponent(market)}` : ''}`)
      .then(setData).catch(console.error).finally(() => setLoading(false))
  }, [period, market])
  useEffect(() => { load() }, [load])

  const t = data?.totals || {}
  const byStore: any[] = data?.by_store || []
  const byRep: any[] = data?.by_rep || []
  const markets = Array.from(new Set(byStore.map(s => s.market).filter(Boolean))).sort()
  const cashTotal = (r: any) => (r.store_cash || 0) + (r.epay_cash || 0)
  const cardTotal = (r: any) => (r.store_cc || 0) + (r.epay_cc || 0)
  const cov = data ? `${data.verified_keys}/${data.submitted_keys}` : '—'

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Daily Closing</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Month-to-date closing summaries by store and by rep, with DM verification coverage.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Link href="/closing/submit" className="btn btn-primary" style={{ fontSize: 13 }}>➕ Submit closing</Link>
          <Link href="/closing/verify" className="btn btn-secondary" style={{ fontSize: 13 }}>✅ DM verify</Link>
          <Link href="/closing/count-config" className="btn btn-secondary" style={{ fontSize: 13 }}>🔢 Count fields</Link>
        </div>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <input type="month" style={sel} value={period} onChange={e => setPeriod(e.target.value)} />
        <select style={sel} value={market} onChange={e => setMarket(e.target.value)}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m as string} value={m as string}>{m as string}</option>)}
        </select>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          {/* Summary tiles */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 12, marginBottom: 18 }}>
            <Tile label="Cash collected" value={fmt(cashTotal(t))} />
            <Tile label="Credit collected" value={fmt(cardTotal(t))} />
            <Tile label="Accessory sales" value={fmt(t.acc_sale)} />
            <Tile label="Other (Zelle/CashApp)" value={fmt(t.other_account)} />
            <Tile label="Activations" value={`${(t.new_line_count || 0) + (t.postpaid_count || 0)}`} sub={`${t.new_line_count || 0} new · ${t.postpaid_count || 0} postpaid`} />
            <Tile label="Upgrades" value={`${t.upgrade_count || 0}`} />
            <Tile label="Rep submissions" value={`${t.rows || 0}`} sub={`${t.days || 0} day(s)`} />
            <Tile label="DM verified" value={cov} sub="store-days verified" />
          </div>

          {/* Tabs */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
            {(['store', 'rep'] as const).map(x => (
              <button key={x} className={`btn ${tab === x ? 'btn-primary' : 'btn-secondary'}`} style={{ fontSize: 13 }} onClick={() => setTab(x)}>
                {x === 'store' ? '🏬 By store' : '🧑 By rep'}
              </button>
            ))}
          </div>

          {tab === 'store' ? (
            <Table
              head={['Store', 'Market', 'Days', 'Cash', 'Credit', 'Accessory', 'Other', 'Upg', 'New', 'Post']}
              rows={byStore}
              empty="No closing rows for this month yet."
              render={(r: any) => [
                <span style={{ fontWeight: 600 }}>{r.store_address || r.store_name || '—'}</span>,
                r.market || '—', r.days, fmt(cashTotal(r)), fmt(cardTotal(r)), fmt(r.acc_sale), fmt(r.other_account),
                r.upgrade_count, r.new_line_count, r.postpaid_count,
              ]}
            />
          ) : (
            <Table
              head={['Rep', 'Store', 'Days', 'Cash', 'Credit', 'Accessory', 'Other', 'Upg', 'New', 'Post']}
              rows={byRep}
              empty="No rep submissions for this month yet."
              render={(r: any) => [
                <span style={{ fontWeight: 600 }}>{r.employee_name || '—'}</span>,
                r.store_address || '—', r.days, fmt(cashTotal(r)), fmt(cardTotal(r)), fmt(r.acc_sale), fmt(r.other_account),
                r.upgrade_count, r.new_line_count, r.postpaid_count,
              ]}
            />
          )}
        </>
      )}
    </div>
  )
}

const Tile = ({ label, value, sub }: { label: string; value: string; sub?: string }) => (
  <div className="card" style={{ padding: 14 }}>
    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
)

function Table({ head, rows, render, empty }: { head: string[]; rows: any[]; render: (r: any) => React.ReactNode[]; empty: string }) {
  if (!rows.length) return <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>{empty}</div>
  return (
    <div className="card table-wrapper" style={{ padding: 0 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr style={{ background: 'var(--surface2)' }}>
          {head.map((h, i) => <th key={h} style={i < 2 ? thL : th}>{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri}>{render(r).map((c, i) => <td key={i} style={i < 2 ? tdL : td}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
