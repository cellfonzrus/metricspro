'use client'
import { useState, useEffect, useMemo } from 'react'
import Link from 'next/link'
import { api, fmt, ORG_ID } from '@/lib/client'
import { TrendChart, TREND_COLORS } from '@/components/TrendChart'
import { RestrictedReport, useReportGrant, isForbidden, ACCOUNT_TRENDS_GRANT } from '../_components/ReportGate'

// Financial Analysis — the roadmap Phase 3–5 hub (owner directive 2026-09-02: "bars charts,
// projections … with a probable company valuation"). ONE MATH PATH: every figure comes from the
// three backend endpoints that read the STORED statement snapshots (GET /account/analysis,
// /account/projection, /account/valuation) — this page computes NOTHING itself, so it can never
// disagree with the P&L / Balance Sheet pages.
//
// PERMISSIONS: page-level = the DEFAULT-CLOSED 'account_trends' grant (same as the Trends hub —
// backend enforces on /analysis + /projection). The valuation section rides its OWN stricter
// 'company_valuation' grant: a 403 there hides the section behind a lock chip, never the page.
const MONTH_OPTS = [6, 12, 24]
const shortPeriod = (p: string) => {
  const m = String(p || '').match(/^([A-Za-z]+)\s+(\d{4})$/)
  return m ? `${m[1].slice(0, 3)} '${m[2].slice(2)}` : p
}
const card: React.CSSProperties = { padding: '14px 12px 8px', marginBottom: 0 }
const cardTitle: React.CSSProperties = { fontSize: 13, fontWeight: 600, marginBottom: 8, paddingLeft: 6 }
const r2 = (n: any) => Math.round((Number(n) || 0) * 100) / 100

export default function FinancialAnalysisPage() {
  const [months, setMonths] = useState(12)
  const [ana, setAna] = useState<any>(null)
  const [proj, setProj] = useState<any>(null)
  const [val, setVal] = useState<any>(null)
  const [valLocked, setValLocked] = useState(false)
  const [loading, setLoading] = useState(true)
  const { granted, ready } = useReportGrant(ACCOUNT_TRENDS_GRANT)
  const [denied, setDenied] = useState(false)

  useEffect(() => {
    if (!ready) return
    if (!granted) { setLoading(false); setAna(null); setProj(null); setVal(null); return }
    setLoading(true); setDenied(false)
    let forbidden = false
    Promise.all([
      api(`/api/v1/account/analysis?months=${months}&org_id=${ORG_ID}`).catch(e => { if (isForbidden(e)) forbidden = true; return null }),
      api(`/api/v1/account/projection?months=24&org_id=${ORG_ID}`).catch(() => null),
      // valuation has its OWN stricter grant — a 403 locks only this section
      api(`/api/v1/account/valuation?org_id=${ORG_ID}`).catch(e => { if (isForbidden(e)) setValLocked(true); return null }),
    ]).then(([a, p, v]: any) => {
      if (forbidden) { setDenied(true); setAna(null); setProj(null); setVal(null); return }
      setAna(a); setProj(p); setVal(v)
    }).finally(() => setLoading(false))
  }, [months, granted, ready])

  const monthly: any[] = ana?.monthly || []

  // ── P&L trend + projection overlay (dashed keys continue from the last actual) ────────────────
  const plTrend = useMemo(() => {
    const rows: any[] = monthly.map(m => ({ name: shortPeriod(m.period), revenue: m.revenue, gross_profit: m.gross_profit, net_income: m.net_income }))
    const series: any[] = proj?.computed ? proj.series : []
    if (rows.length && series.length) {
      const last = rows[rows.length - 1]
      Object.assign(last, { revenue_p: last.revenue, gross_profit_p: last.gross_profit, net_income_p: last.net_income })  // bridge point
      series.forEach((s: any) => rows.push({ name: shortPeriod(s.period), revenue_p: s.revenue, gross_profit_p: s.gross_profit, net_income_p: s.net_income }))
    }
    return rows
  }, [monthly, proj])

  const marginTrend = useMemo(() => monthly.map(m => ({ name: shortPeriod(m.period), gross_margin: m.gross_margin_pct, net_margin: m.net_margin_pct })), [monthly])

  // ── expense composition: top 6 lines by latest amount, rest bucketed as "Other" ───────────────
  const expense = useMemo(() => {
    const comp: any[] = ana?.expense_composition_latest || []
    const top = comp.slice(0, 6).map((c: any) => c.key)
    const labels: Record<string, string> = ana?.expense_lines || {}
    const rows = (ana?.expense_breakdown || []).map((r: any) => {
      const out: any = { name: shortPeriod(r.period) }
      let other = 0
      Object.entries(r).forEach(([k, v]: any) => {
        if (k === 'period') return
        if (top.includes(k)) out[k] = r2(v); else other += Number(v) || 0
      })
      if (other) out.__other = r2(other)
      return out
    })
    const series = top.map((k, i) => ({ key: k, name: labels[k] || k, type: 'bar' as const, stack: 'opex', money: true, color: TREND_COLORS[i % TREND_COLORS.length] }))
    if (rows.some((r: any) => r.__other != null)) series.push({ key: '__other', name: 'Other', type: 'bar' as const, stack: 'opex', money: true, color: '#94a3b8' })
    return { rows, series }
  }, [ana])

  // ── latest-month comparisons ──────────────────────────────────────────────────────────────────
  const companyCompare = useMemo(() => (ana?.companies || []).map((c: any) => {
    const last = c.series[c.series.length - 1] || {}
    return { name: String(c.label).slice(0, 22), revenue: last.revenue, gross_profit: last.gross_profit, net_income: last.net_income }
  }), [ana])
  const storeCompare = useMemo(() => (ana?.stores || [])
    .map((s: any) => { const last = s.series[s.series.length - 1] || {}; return { name: String(s.label).slice(0, 18), revenue: last.revenue, net_income: last.net_income } })
    .sort((a: any, b: any) => (b.revenue || 0) - (a.revenue || 0)).slice(0, 12), [ana])

  const cashTrend = useMemo(() => monthly.map(m => ({ name: shortPeriod(m.period), cash: m.cash, assets: m.assets, liabilities: m.liabilities })), [monthly])

  if (ready && (!granted || denied)) {
    return <RestrictedReport title="Financial Analysis" grantKey={ACCOUNT_TRENDS_GRANT}
      subtitle="Trends, margins, expense composition, projections and valuation." />
  }

  const latest = monthly[monthly.length - 1]
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📊 Financial Analysis</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, margin: '4px 0 0' }}>
            Trends, margins, expense composition, projections and valuation — all read from the computed statements (never a second math path).
          </p>
        </div>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Range&nbsp;
          <select className="select" value={months} onChange={e => setMonths(+e.target.value)}>
            {MONTH_OPTS.map(m => <option key={m} value={m}>Last {m} months</option>)}
          </select>
        </label>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !ana?.computed ? (
        <div className="card" style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
          No computed statements yet — compute a period on the <Link href="/accounts">Accounts dashboard</Link> (or wait for the auto-recompute sweep).
        </div>
      ) : (
        <>
          {/* headline tiles */}
          {latest && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginBottom: 14 }}>
              {[['Revenue', fmt(latest.revenue)], ['Gross profit', fmt(latest.gross_profit)],
                ['Net income', fmt(latest.net_income)],
                ['Gross margin', latest.gross_margin_pct == null ? '—' : `${latest.gross_margin_pct}%`],
                ['Net margin', latest.net_margin_pct == null ? '—' : `${latest.net_margin_pct}%`],
                ['Cash & equivalents', fmt(latest.cash)]].map(([l, v]) => (
                  <div key={l as string} className="card" style={{ padding: '10px 12px' }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase' }}>{l} · {shortPeriod(latest.period)}</div>
                    <div style={{ fontSize: 18, fontWeight: 700, marginTop: 2 }}>{v}</div>
                  </div>
                ))}
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(440px, 1fr))', gap: 16 }}>
            <div className="card" style={card}>
              <div style={cardTitle}>📈 Revenue · Gross profit · Net income {proj?.computed && <span style={{ fontWeight: 400, color: 'var(--text3)' }}>· dashed = projected ({proj.method})</span>}</div>
              <TrendChart data={plTrend} height={280} series={[
                { key: 'revenue', name: 'Revenue', color: '#2e75b6', money: true },
                { key: 'gross_profit', name: 'Gross profit', color: '#0891b2', money: true },
                { key: 'net_income', name: 'Net income', color: '#16a34a', money: true },
                { key: 'revenue_p', name: 'Revenue (proj)', color: '#2e75b6', money: true, dashed: true },
                { key: 'gross_profit_p', name: 'GP (proj)', color: '#0891b2', money: true, dashed: true },
                { key: 'net_income_p', name: 'NI (proj)', color: '#16a34a', money: true, dashed: true },
              ]} leftLabel="monthly $ from computed statements; dashed = deterministic projection" />
            </div>
            <div className="card" style={card}>
              <div style={cardTitle}>🧾 Expense composition (OPEX)</div>
              <TrendChart data={expense.rows} height={280} series={expense.series as any}
                leftLabel="operating expenses by P&L line (stacked)" />
            </div>
            <div className="card" style={card}>
              <div style={cardTitle}>📐 Margin trend</div>
              <TrendChart data={marginTrend} height={260} leftMoney={false} series={[
                { key: 'gross_margin', name: 'Gross margin %', color: '#7c3aed' },
                { key: 'net_margin', name: 'Net margin %', color: '#16a34a' },
              ]} leftLabel="% of revenue (gaps = no revenue that month)" />
            </div>
            <div className="card" style={card}>
              <div style={cardTitle}>💵 Cash & balance-sheet trend</div>
              <TrendChart data={cashTrend} height={260} series={[
                { key: 'cash', name: 'Cash & equivalents', color: '#16a34a', money: true },
                { key: 'assets', name: 'Total assets', color: '#2e75b6', money: true, dashed: true },
                { key: 'liabilities', name: 'Total liabilities', color: '#dc2626', money: true, dashed: true },
              ]} leftLabel="point-in-time balances per computed month" />
            </div>
            {companyCompare.length > 1 && (
              <div className="card" style={card}>
                <div style={cardTitle}>🏢 Company comparison <span style={{ fontWeight: 400, color: 'var(--text3)' }}>· latest month</span></div>
                <TrendChart data={companyCompare} height={260} series={[
                  { key: 'revenue', name: 'Revenue', type: 'bar', money: true, color: '#2e75b6' },
                  { key: 'gross_profit', name: 'Gross profit', type: 'bar', money: true, color: '#0891b2' },
                  { key: 'net_income', name: 'Net income', type: 'bar', money: true, color: '#16a34a' },
                ]} leftLabel="per company statement scope" />
              </div>
            )}
            {storeCompare.length > 1 && (
              <div className="card" style={card}>
                <div style={cardTitle}>🏪 Store comparison <span style={{ fontWeight: 400, color: 'var(--text3)' }}>· latest month · top {storeCompare.length} by revenue</span></div>
                <TrendChart data={storeCompare} height={260} series={[
                  { key: 'revenue', name: 'Revenue', type: 'bar', money: true, color: '#2e75b6' },
                  { key: 'net_income', name: 'Net income', type: 'bar', money: true, color: '#16a34a' },
                ]} leftLabel="per store statement scope" />
              </div>
            )}
          </div>

          {/* ── projection detail ─────────────────────────────────────────────────────────────── */}
          {proj?.computed && (
            <div className="card" style={{ padding: 16, marginTop: 16 }}>
              <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>🔭 Projection — next {proj.horizon_months} month(s) <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}>method: {proj.method} · deterministic, display-only</span></div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', minWidth: 560 }}>
                  <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                    <th style={{ textAlign: 'left', padding: '6px 10px' }}>Month</th>
                    {['Revenue', 'COGS', 'OPEX', 'Gross profit', 'Net income'].map(h => <th key={h} style={{ textAlign: 'right', padding: '6px 10px' }}>{h}</th>)}
                  </tr></thead>
                  <tbody>{proj.series.map((s: any) => (
                    <tr key={s.period} style={{ borderTop: '1px solid var(--border)', fontSize: 13 }}>
                      <td style={{ padding: '5px 10px' }}>{s.period} <span style={{ fontSize: 10, color: '#92400e', background: '#fef3c7', padding: '1px 5px', borderRadius: 999 }}>projected</span></td>
                      {[s.revenue, s.cogs, s.opex, s.gross_profit, s.net_income].map((v, i) => <td key={i} style={{ padding: '5px 10px', textAlign: 'right' }}>{fmt(v)}</td>)}
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              {proj.cash_runway && (
                <div style={{ fontSize: 13, marginTop: 10 }}>
                  <b>Cash runway:</b>{' '}
                  {proj.cash_runway.months != null
                    ? <>~{proj.cash_runway.months} months ({fmt(proj.cash_runway.cash)} cash ÷ {fmt(Math.abs(proj.cash_runway.avg_projected_net_income))}/mo projected burn)</>
                    : <span style={{ color: 'var(--text2)' }}>{proj.cash_runway.reason || 'n/a'}</span>}
                </div>
              )}
              <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
                {(proj.assumptions || []).map((a: string, i: number) => <div key={i}>· {a}</div>)}
              </div>
            </div>
          )}

          {/* ── valuation (own stricter grant) ─────────────────────────────────────────────────── */}
          {valLocked ? (
            <div className="card" style={{ padding: 14, marginTop: 16, fontSize: 13, color: 'var(--text2)' }}>
              🔒 <b>Company valuation</b> is restricted — it needs the <code>company_valuation</code> data permission on your role (admin-only by default).
            </div>
          ) : val?.computed && (
            <div className="card" style={{ padding: 16, marginTop: 16 }}>
              <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>🏷️ Probable company valuation <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text3)' }}>as of {val.as_of}</span></div>
              <div style={{ display: 'flex', gap: 18, alignItems: 'baseline', flexWrap: 'wrap', margin: '8px 0 4px' }}>
                <div><span style={{ fontSize: 12, color: 'var(--text3)' }}>Low </span><span style={{ fontSize: 20, fontWeight: 700 }}>{fmt(val.summary.low)}</span></div>
                <div><span style={{ fontSize: 12, color: 'var(--text3)' }}>Mid </span><span style={{ fontSize: 24, fontWeight: 800, color: 'var(--accent)' }}>{fmt(val.summary.mid)}</span></div>
                <div><span style={{ fontSize: 12, color: 'var(--text3)' }}>High </span><span style={{ fontSize: 20, fontWeight: 700 }}>{fmt(val.summary.high)}</span></div>
                {val.summary.asset_floor_applied && <span style={{ fontSize: 11, color: '#92400e', background: '#fef3c7', padding: '2px 7px', borderRadius: 999 }}>asset floor lifted the low end</span>}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>{val.summary.basis}</div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', minWidth: 620 }}>
                  <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                    <th style={{ textAlign: 'left', padding: '6px 10px' }}>Method</th>
                    <th style={{ textAlign: 'right', padding: '6px 10px' }}>Basis</th>
                    <th style={{ textAlign: 'left', padding: '6px 10px' }}>Assumption</th>
                    <th style={{ textAlign: 'right', padding: '6px 10px' }}>Low</th>
                    <th style={{ textAlign: 'right', padding: '6px 10px' }}>Mid</th>
                    <th style={{ textAlign: 'right', padding: '6px 10px' }}>High</th>
                  </tr></thead>
                  <tbody>{val.methods.map((m: any) => (
                    <tr key={m.key} style={{ borderTop: '1px solid var(--border)', fontSize: 13, opacity: m.meaningful === false ? 0.55 : 1 }}>
                      <td style={{ padding: '5px 10px' }}>{m.label}{m.note && <div style={{ fontSize: 11, color: 'var(--text3)', maxWidth: 340 }}>{m.note}</div>}</td>
                      <td style={{ padding: '5px 10px', textAlign: 'right' }}>{m.basis_value != null ? fmt(m.basis_value) : '—'}</td>
                      <td style={{ padding: '5px 10px', fontSize: 12, color: 'var(--text2)' }}>
                        {m.multiple_range ? `× ${m.multiple_range[0]}–${m.multiple_range[1]} (${m.source})`
                          : m.discount_rate_range ? `${Math.round(m.discount_rate_range[0] * 100)}–${Math.round(m.discount_rate_range[1] * 100)}% rate · ${m.terminal_multiple_range[0]}–${m.terminal_multiple_range[1]}× terminal (${m.source})`
                            : m.source || ''}
                      </td>
                      {[m.low, m.mid, m.high].map((v, i) => <td key={i} style={{ padding: '5px 10px', textAlign: 'right' }}>{v == null ? '—' : fmt(v)}</td>)}
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
                {(val.assumptions || []).map((a: string, i: number) => <div key={i}>· {a}</div>)}
              </div>
              <div style={{ fontSize: 12, marginTop: 10, padding: '8px 10px', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, color: '#92400e' }}>
                ⚠️ {val.disclaimer}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
