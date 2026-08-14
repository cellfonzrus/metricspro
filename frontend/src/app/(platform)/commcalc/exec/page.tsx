'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

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

  // Store-leaderboard export columns (money flagged so Excel/PDF/subtotals format correctly).
  const storeCols: ExportColumn[] = [
    { header: 'Store', field: 'store', role: 'store', get: (r: any) => r.store },
    { header: 'Market', field: 'market', get: (r: any) => r.market || '' },
    { header: 'Reps', field: 'reps', get: (r: any) => r.reps },
    { header: 'Paid', field: 'paid', money: true, get: (r: any) => r.paid },
    { header: 'At risk', field: 'at_risk', money: true, get: (r: any) => r.at_risk },
    { header: 'Chargebacks', field: 'chargebacks', money: true, get: (r: any) => r.chargebacks },
    { header: 'Flags', field: 'flags', get: (r: any) => r.flags || 0 },
    { header: 'On table', field: 'on_table', money: true, get: (r: any) => r.on_table },
  ]
  // Tiles doctrine: a dashboard with a detail table exports the table PLUS a one-page {Metric,Value} summary.
  const summaryRows = [
    ...(data?.pl ? [{ k: 'Revenue', v: fmt(data.pl.revenue) }, { k: 'Gross profit', v: fmt(data.pl.gross_profit) }, { k: 'Net income', v: fmt(data.pl.net_income) }] : []),
    { k: 'Commissions paid', v: fmt(t.commissions_paid) },
    { k: 'Commission at risk', v: fmt(t.commission_at_risk) },
    { k: 'Chargebacks deducted', v: fmt(t.chargebacks_deducted) },
    { k: 'Total left on table', v: fmt(t.money_on_table) },
    { k: 'Reps', v: String(t.reps || 0) },
    { k: 'Open chargebacks', v: String(t.open_chargebacks || 0) },
  ]
  const summaryCols: ExportColumn[] = [{ header: 'Metric', get: (r: any) => r.k }, { header: 'Value', get: (r: any) => r.v }]
  const exportSheets = [
    { name: 'Summary', columns: summaryCols, rows: summaryRows },
    { name: 'Store leaderboard', columns: storeCols, rows: stores },
  ]

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📊 Owner Overview — {period}</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Business health at a glance — commissions, money at risk, chargebacks, flags, and the store leaderboard.</p>
        {/* WHY THERE IS NO DATE RANGE HERE (owner asked 2026-08-11 for a date-range filter on
            "Executive MTD / Owner Overview"): every tile on this page is MONTH-GRAINED at the source —
            commissions paid / at-risk / chargebacks come from the per-rep incentive run, and the three
            P&L tiles from that month's account_statements snapshot. Neither exists at day grain, so a
            date range here could only ever relabel a whole-month number as a week's. Executive MTD is
            built from individual sales lines, which is why the range lives there. */}
        <p style={{ color: 'var(--text3)', fontSize: 12.5, margin: '6px 0 0' }}>
          Monthly by nature — commissions and the P&amp;L are computed per month. For a day-level slice
          (any From → To inside the month), use <Link href="/commcalc/exec/mtd">Executive MTD →</Link>
        </p>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          {data?.pl && (data.pl.revenue != null || data.pl.net_income != null) && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12, marginBottom: 12 }}>
              <Tile label="Revenue" value={fmt(data.pl.revenue)} />
              <Tile label="Gross profit" value={fmt(data.pl.gross_profit)} tone={(data.pl.gross_profit ?? 0) < 0 ? '#b42318' : '#16794a'} />
              <Tile label="Net income" value={fmt(data.pl.net_income)} tone={(data.pl.net_income ?? 0) < 0 ? '#b42318' : '#16794a'} sub={<Link href="/accounts/pl" style={{ fontSize: 11 }}>P&L →</Link>} />
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12, marginBottom: 18 }}>
            <Tile label="Commissions paid" value={fmt(t.commissions_paid)} />
            <Tile label="Commission at risk" value={fmt(t.commission_at_risk)} tone="#b45309" sub={`${t.below_tier || 0} reps below tier`} />
            <Tile label="Chargebacks deducted" value={fmt(t.chargebacks_deducted)} tone="#b42318" />
            <Tile label="Total left on table" value={fmt(t.money_on_table)} tone="#b42318" />
            <Tile label="Reps" value={`${t.reps || 0}`} />
            <Tile label="Open chargebacks" value={`${t.open_chargebacks || 0}`} tone="#b42318" sub="in the bucket" />
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 10, flexWrap: 'wrap' }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>Store leaderboard</div>
            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <ReportExportBar title={`Owner Overview ${period}`} filename={`owner_overview_${String(period).replace(/\s+/g, '_')}`} sheets={exportSheets} />
              <Link href="/commcalc/exec/mtd" style={{ fontSize: 12 }}>MTD sales summary →</Link>
              <Link href="/commcalc/coaching" style={{ fontSize: 12 }}>Rep coaching →</Link>
            </div>
          </div>
          {stores.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No incentive data for {period}. (Run the incentive calc.)</div>
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

const Tile = ({ label, value, sub, tone }: { label: string; value: string; sub?: React.ReactNode; tone?: string }) => (
  <div className="card" style={{ padding: 14 }}>
    <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: tone || 'var(--text)' }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
)
