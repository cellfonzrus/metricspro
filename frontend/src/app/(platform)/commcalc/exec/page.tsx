'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// Owner / exec single-pane — headline business health + store leaderboard for the period.
const th: React.CSSProperties = { textAlign: 'right', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const thL: React.CSSProperties = { ...th, textAlign: 'left' }
const td: React.CSSProperties = { textAlign: 'right', padding: '8px 10px', borderTop: '1px solid var(--border)', fontSize: 13, whiteSpace: 'nowrap' }
const tdL: React.CSSProperties = { ...td, textAlign: 'left' }

export default function ExecOverviewPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    if (!period) return
    setLoading(true)
    api(`/api/v1/commcalc/exec-overview/${encodeURIComponent(period)}`).then(setData).catch(console.error).finally(() => setLoading(false))
  }, [period])
  useEffect(() => { load() }, [load])

  const t = data?.tiles || {}
  const stores: any[] = data?.stores || []

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📊 Owner Overview — {period}</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Business health at a glance — commissions, money at risk, chargebacks, flags, and the store leaderboard.</p>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12, marginBottom: 18 }}>
            <Tile label="Commissions paid" value={fmt(t.commissions_paid)} />
            <Tile label="Commission at risk" value={fmt(t.commission_at_risk)} tone="#b45309" sub={`${t.below_tier || 0} reps below tier`} />
            <Tile label="Chargebacks deducted" value={fmt(t.chargebacks_deducted)} tone="#b42318" />
            <Tile label="Total left on table" value={fmt(t.money_on_table)} tone="#b42318" />
            <Tile label="Reps" value={`${t.reps || 0}`} />
            <Tile label="Open chargebacks" value={`${t.open_chargebacks || 0}`} tone="#b42318" sub="in the bucket" />
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>Store leaderboard</div>
            <Link href="/commcalc/coaching" style={{ fontSize: 12 }}>Rep coaching →</Link>
          </div>
          {stores.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No commission data for {period}. (Run the commission calc.)</div>
          ) : (
            <div className="card table-wrapper" style={{ padding: 0 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Store', 'Market', 'Reps', 'Paid', 'At risk', 'Chargebacks', 'Flags', 'On table'].map((h, i) =>
                    <th key={h} style={i < 2 ? thL : th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {stores.map((s, i) => (
                    <tr key={i}>
                      <td style={{ ...tdL, fontWeight: 600 }}>{s.store}</td>
                      <td style={tdL}>{s.market || '—'}</td>
                      <td style={td}>{s.reps}</td>
                      <td style={td}>{fmt(s.paid)}</td>
                      <td style={{ ...td, color: s.at_risk > 0 ? '#b45309' : 'var(--text3)' }}>{s.at_risk > 0 ? fmt(s.at_risk) : '—'}</td>
                      <td style={{ ...td, color: s.chargebacks > 0 ? '#b42318' : 'var(--text3)' }}>{s.chargebacks > 0 ? fmt(s.chargebacks) : '—'}</td>
                      <td style={{ ...td, color: s.flags > 0 ? '#b42318' : 'var(--text3)' }}>{s.flags || '—'}</td>
                      <td style={{ ...td, fontWeight: 700, color: s.on_table > 0 ? '#b42318' : 'var(--text3)' }}>{fmt(s.on_table)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

const Tile = ({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) => (
  <div className="card" style={{ padding: 14 }}>
    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: tone || 'var(--text)' }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
)
