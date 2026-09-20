'use client'
import { useCallback, useEffect, useState } from 'react'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// CARRIER EARNED vs EMPLOYEE PAID — per rep, per month (index §30).
//
// THE ONE THING THIS SCREEN MUST NOT DO IS ADD ITS TWO MONEY COLUMNS TOGETHER.
// Carrier earned is DEALER REVENUE (what the statement or the processor payment feed says we made on
// this rep's activations). Employee paid is a PAYROLL EXPENSE. They are two different ledgers on two
// different feeds, and a single combined figure would be meaningless. The gap between them is shown
// under its own explicit name and only for reps where BOTH sides were measured.
//
// AND IT MUST NOT PRINT $0.00 WHEN IT MEANS "NOT REPORTED". An un-uploaded statement and a carrier
// that genuinely paid nothing are different answers. Measured live on 2026-09-20 that distinction is
// worth $54,151.66 on one tenant and $86,501.79 on the other — collapsing it would have published a
// confident negative margin for a month whose feed simply had not landed. The backend returns null
// for an unknown earned figure and this page renders it as "not reported", never as a number.
//
// DUPLICATE CHECK: this is not a second commission report. /commcalc/commission-discrepancy answers
// "which sold activation was never paid" from the same engine (ma_recon) at the DEVICE grain. This
// rolls that same engine's output up to the REP and puts what we were paid beside what we paid out.
// /commcalc/commission-legs answers "what did we make by money stream" with no rep dimension at all.
//
// RULE TWO: no carrier, tenant, market or portal name appears here. Rep names, store names, market
// names and the feed-shape label are DATA, rendered from the payload.

type Row = {
  rep: string; rep_key: string; store: string; market: string
  activations_sold: number; activations_in_statement: number
  activations_without_device_key: number; open_no_rule: number
  carrier_earned: number | null; carrier_earned_state: string; carrier_earned_reason: string
  employee_paid: number | null; employee_paid_state: string
  difference: number | null; difference_state: string; difference_label: string
}
type Totals = {
  carrier_earned_reported: number; carrier_earned_reps_reported: number
  carrier_earned_reps_measured_zero: number; carrier_earned_reps_not_reported: number
  employee_paid_total: number; employee_paid_reps: number
  activations_sold: number; activations_in_statement: number
  activations_without_device_key: number; open_no_rule: number
  difference_over_measured_reps: number; difference_measured_reps: number
}

const card: React.CSSProperties = {
  border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px', background: 'var(--bg1)',
}

const STATE_LABEL: Record<string, string> = {
  reported: 'reported',
  measured_zero: 'measured zero',
  not_reported: 'not reported',
}

function Tile({ label, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: 'earned' | 'paid' | 'gap' | 'plain'
}) {
  const color = tone === 'earned' ? '#15803d' : tone === 'paid' ? '#b45309'
    : tone === 'gap' ? 'var(--text1)' : 'var(--text1)'
  return (
    <div style={card}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: .3 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

/** An earned cell. NEVER renders a number when the state is not_reported. */
function Earned({ r }: { r: Row }) {
  if (r.carrier_earned === null || r.carrier_earned_state === 'not_reported') {
    return <span style={{ color: 'var(--text3)', fontStyle: 'italic' }} title={r.carrier_earned_reason}>not reported</span>
  }
  const zero = r.carrier_earned_state === 'measured_zero'
  return (
    <span style={{ color: zero ? 'var(--text3)' : '#15803d', fontWeight: zero ? 400 : 600 }}
          title={r.carrier_earned_reason}>
      {fmt(r.carrier_earned)}{zero && <span style={{ fontSize: 11 }}> (measured)</span>}
    </span>
  )
}

export default function CarrierVsPayPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [market, setMarket] = useState('')

  const load = useCallback(() => {
    if (!period) return
    setLoading(true); setErr('')
    const q = market ? `?market=${encodeURIComponent(market)}` : ''
    api(`/api/v1/commcalc/carrier-vs-pay/${encodeURIComponent(period)}${q}`)
      .then((r: any) => setData(r))
      .catch((e: any) => setErr(e?.message || String(e)))
      .finally(() => setLoading(false))
  }, [period, market])
  useEffect(() => { load() }, [load])

  const t: Totals | null = data?.totals || null
  const meta = data?.meta || {}
  const rows: Row[] = data?.rows || []
  const markets = Array.from(new Set(rows.map(r => r.market).filter(Boolean))).sort()
  const notReported = t?.carrier_earned_reps_not_reported || 0

  return (
    <div style={{ padding: 24, maxWidth: 1280 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Carrier Earned vs Employee Paid</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
        Per rep, for one month: what the carrier says it paid us on that rep&rsquo;s activations,
        against what we paid that rep.
      </p>

      {/* The posture banner. The whole risk of this screen is a reader treating the two columns as
          one ledger, so this is the sentence that stops them. */}
      <div style={{ ...card, marginTop: 14, background: '#f0f9ff', borderColor: '#bae6fd' }}>
        <strong style={{ fontSize: 13 }}>Two ledgers, side by side. Never added together.</strong>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
          {meta.difference_note || (
            <>Carrier earned is dealer <strong>revenue</strong>; employee paid is a payroll{' '}
            <strong>expense</strong>. This report never sums them and never nets them.</>
          )}{' '}
          Nothing on this screen is booked anywhere &mdash; it reads the commission engines and the
          calculated pay, and writes nothing.
        </div>
      </div>

      {/* Absence is stated, not inferred. */}
      {meta.carrier_side && (
        <div style={{ ...card, marginTop: 12, background: '#fffbeb', borderColor: '#fcd34d' }}>
          <strong style={{ fontSize: 13 }}>The carrier side is not reported for this month.</strong>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>{meta.carrier_side}</div>
        </div>
      )}
      {!meta.carrier_side && notReported > 0 && (
        <div style={{ ...card, marginTop: 12, background: '#fffbeb', borderColor: '#fcd34d' }}>
          <strong style={{ fontSize: 13 }}>
            {notReported} rep(s) have no measured carrier figure.
          </strong>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
            They are shown as <em>not reported</em>, never as $0.00 earned, and they are excluded from
            the difference total below. A zero and a missing upload are different answers.
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', margin: '14px 0 0', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
          Market
          <select value={market} onChange={e => setMarket(e.target.value)}
                  style={{ padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)' }}>
            <option value="">All</option>
            {markets.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <button className="btn btn-secondary" onClick={load}>&#8635; Refresh</button>
        <a className="btn btn-secondary" href="/commcalc/commission-discrepancy">Pay Discrepancy (same engine, device grain) &#8599;</a>
      </div>

      {loading && <div style={{ marginTop: 16, color: 'var(--text2)' }}>Loading&hellip;</div>}
      {err && <div style={{ marginTop: 16, color: '#dc2626', fontSize: 13 }}>&#10060; {err}</div>}

      {t && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 12, marginTop: 16 }}>
            <Tile label="Carrier earned (dealer revenue)" value={fmt(t.carrier_earned_reported)} tone="earned"
                  sub={`${t.carrier_earned_reps_reported} rep(s) reported · ${t.carrier_earned_reps_measured_zero} measured zero · ${t.carrier_earned_reps_not_reported} not reported`} />
            <Tile label="Employee paid (payroll expense)" value={fmt(t.employee_paid_total)} tone="paid"
                  sub={`${t.employee_paid_reps} rep(s) calculated`} />
            <Tile label="Earned minus paid" value={fmt(t.difference_over_measured_reps)} tone="gap"
                  sub={`over ${t.difference_measured_reps} rep(s) where BOTH sides were measured`} />
            <Tile label="Activations sold" value={t.activations_sold.toLocaleString()}
                  sub={`${t.activations_in_statement.toLocaleString()} found in the carrier feed · ${t.open_no_rule.toLocaleString()} unpaid with no business rule`} />
          </div>

          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
            Basis: {meta.earned_shape === 'preattributed_rep'
              ? 'the processor payment feed, already aggregated per rep.'
              : 'the per-device carrier statement, attributed device → sale → rep.'}
            {Array.isArray(meta.earnings_columns) && meta.earnings_columns.length > 0 && (
              <> Columns summed ({meta.earnings_columns_source}): {meta.earnings_columns.join(', ')}.</>
            )}
          </div>

          <div style={{ marginTop: 20, overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text3)' }}>
                  <th style={{ padding: '6px 8px' }}>Rep</th>
                  <th style={{ padding: '6px 8px' }}>Store</th>
                  <th style={{ padding: '6px 8px' }}>Market</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Activations</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>In carrier feed</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Carrier earned</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Employee paid</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Earned &minus; paid</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.rep_key} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 8px' }}>{r.rep || r.rep_key}</td>
                    <td style={{ padding: '6px 8px', color: 'var(--text2)' }}>{r.store || '—'}</td>
                    <td style={{ padding: '6px 8px', color: 'var(--text2)' }}>{r.market || '—'}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{r.activations_sold.toLocaleString()}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text2)' }}>
                      {r.activations_in_statement.toLocaleString()}
                    </td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}><Earned r={r} /></td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                      {r.employee_paid === null
                        ? <span style={{ color: 'var(--text3)', fontStyle: 'italic' }}>not calculated</span>
                        : <span style={{ color: '#b45309' }}>{fmt(r.employee_paid)}</span>}
                    </td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>
                      {r.difference === null
                        ? <span style={{ color: 'var(--text3)', fontStyle: 'italic', fontWeight: 400 }}
                                title={`${STATE_LABEL[r.carrier_earned_state] || r.carrier_earned_state} — no margin is computed unless both sides are measured`}>—</span>
                        : fmt(r.difference)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {rows.length === 0 && !loading && (
            <div style={{ marginTop: 16, color: 'var(--text2)', fontSize: 13 }}>
              No reps for this month and filter.
            </div>
          )}
        </>
      )}
    </div>
  )
}
