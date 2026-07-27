'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { useAuth } from '@/lib/auth-context'
import { carrierMode } from '@/lib/rbac'

interface RepRow {
  epay_salesperson: string
  store: string
  tier: number
  kpis_met: number
  total_payout: number
  subtotal: number
  premium_acts: number
  byod_acts: number
  upgrade_acts: number
  acima_comm: number
}

export default function CommCalcDashboard() {
  const { period } = usePeriod()
  const { carriers } = useAuth()
  const isBoost = carrierMode(carriers) === 'boost'   // non-Boost carriers pay via plans, not KPI tiers
  const [reps, setReps] = useState<RepRow[]>([])
  const [loading, setLoading] = useState(true)
  const [calcStatus, setCalcStatus] = useState<string>('')
  const [calcError, setCalcError] = useState<string>('')
  const [calcNotices, setCalcNotices] = useState<any[]>([])   // mig 247 — what the calc did NOT pay

  useEffect(() => {
    loadData()
  }, [period])

  async function loadData() {
    setLoading(true)
    try {
      const enc = encodeURIComponent(period)
      const [comms, status] = await Promise.all([
        api(`/api/v1/commcalc/commissions/${enc}?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/calc-status/${enc}?org_id=${ORG_ID}`),
      ])
      setReps(comms || [])
      setCalcStatus(status?.calc_status || 'not_run')
      // Surface a REFUSED / error calc (e.g. the R1 unconfigured-tenant guard) instead of a silent
      // "0 reps" — save_errors carries the actionable message.
      const errs = status?.save_errors
      setCalcError(status?.calc_status === 'error'
        ? (Array.isArray(errs) ? errs.join(' ') : (errs || 'Calculation failed.')) : '')
      // WHAT THIS CALCULATION DID NOT PAY (mig 247). A successful run that deliberately skipped
      // activations — an unticked device category, an activation we could not classify, one with no
      // identifiable rate-plan line — used to have NO channel at all: save_errors is only rendered on a
      // failed calc. The owner reads this panel after every recalculation.
      setCalcNotices(Array.isArray(status?.calc_notices) ? status.calc_notices : [])
    } catch (e) {
      console.error(e)
    }
    setLoading(false)
  }

  const totalPayout = reps.reduce((s, r) => s + (r.total_payout || 0), 0)
  const totalActs   = reps.reduce((s, r) => s + (r.premium_acts || 0) + (r.byod_acts || 0), 0)
  const totalUpgrades = reps.reduce((s, r) => s + (r.upgrade_acts || 0), 0)
  const tierCounts = { 100: 0, 75: 0, 50: 0 } as Record<number, number>
  reps.forEach(r => {
    const t = Math.round((r.tier || 0.5) * 100)
    tierCounts[t] = (tierCounts[t] || 0) + 1
  })

  const KPI_CARDS = [
    { label: 'Total Commission Payout', value: fmt(totalPayout), color: 'var(--accent)', icon: '💰' },
    { label: 'Total Activations', value: totalActs.toString(), color: 'var(--green)', icon: '📱' },
    { label: 'Total Upgrades', value: totalUpgrades.toString(), color: '#7c3aed', icon: '🔄' },
    { label: 'Reps Calculated', value: reps.length.toString(), color: 'var(--amber)', icon: '👥' },
  ]

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>CommCalc Dashboard</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · {reps.length} reps
            {calcStatus === 'done' && <span style={{ color: 'var(--green)', marginLeft: 8 }}>✓ Calculated</span>}
            {calcStatus === 'running' && <span style={{ color: 'var(--amber)', marginLeft: 8 }}>⏳ Running...</span>}
            {calcStatus === 'error' && <span style={{ color: 'var(--red)', marginLeft: 8 }}>⚠ Calculation refused</span>}
            {calcStatus === 'not_run' && <span style={{ color: 'var(--text3)', marginLeft: 8 }}>Not calculated yet</span>}
          </p>
        </div>
        <button
          onClick={async () => {
            setCalcStatus('running')
            try {
              await api(`/api/v1/commcalc/calculate/${encodeURIComponent(period)}?org_id=${ORG_ID}`, { method: 'POST' })
              setTimeout(loadData, 2000)
            } catch (e: any) { alert(e.message) }
          }}
          className="btn btn-primary"
        >
          ⚡ Run Calculation
        </button>
      </div>

      {/* WHAT THIS CALCULATION DID NOT PAY (mig 247) — excluded device categories, unclassifiable
          activations, unresolved monthly charges, duplicate device-months. Shown after a SUCCESSFUL
          run: a deliberate non-payment must be as visible as a failure. */}
      {calcNotices.length > 0 && (
        <div className="card" style={{ borderLeft: '4px solid var(--amber)', background: 'var(--surface2)', marginBottom: 24 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>What this calculation did not pay</div>
          <ul style={{ margin: '0 0 6px 18px', padding: 0, fontSize: 13, color: 'var(--text2)', lineHeight: 1.6 }}>
            {calcNotices.map((n: any, i: number) => (
              <li key={i}>
                {n.message}
                {n.by_rep && Object.keys(n.by_rep).length > 0 && (
                  <span style={{ color: 'var(--text3)' }}>
                    {' '}({Object.entries(n.by_rep).map(([r, v]: any) => `${r} ${fmt(v)}`).join(' · ')})
                  </span>
                )}
              </li>
            ))}
          </ul>
          <a href="/commcalc/plan-installments#categories" style={{ color: 'var(--accent)', fontSize: 13 }}>
            Review qualifying device categories →
          </a>
        </div>
      )}

      {/* Calc refused / error banner — surfaces the R1 unconfigured-tenant guard with a fix link */}
      {calcError && (
        <div className="card" style={{ borderLeft: '4px solid var(--red)', background: 'var(--surface2)', marginBottom: 24 }}>
          <div style={{ fontWeight: 700, color: 'var(--red)', marginBottom: 6 }}>⚠ Commission calculation refused — last good snapshot kept</div>
          <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.6 }}>{calcError}</div>
          {/REFUSED|no commission source|plan mode/i.test(calcError) && (
            <div style={{ marginTop: 10, display: 'flex', gap: 12, alignItems: 'center' }}>
              <a href="/commcalc/commission-plans" className="btn btn-primary" style={{ textDecoration: 'none' }}>
                Configure Commission Plans →
              </a>
              <a href="/commcalc/plan-installments" style={{ color: 'var(--accent)', fontSize: 13 }}>
                Multi-month schedules & pay settings
              </a>
            </div>
          )}
        </div>
      )}

      {/* KPI Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }}>
        {KPI_CARDS.map(({ label, value, color, icon }) => (
          <div key={label} className="card" style={{ borderTop: `3px solid ${color}` }}>
            <div style={{ fontSize: 24 }}>{icon}</div>
            <div style={{ fontSize: 22, fontWeight: 700, color, marginTop: 8 }}>{value}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Tier Distribution + Top Reps */}
      <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16, marginBottom: 24 }}>
        <div className="card">
          <div style={{ fontWeight: 600, marginBottom: 16 }}>{isBoost ? 'Tier Distribution' : 'Payout Basis'}</div>
          {!isBoost && (
            <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.6 }}>
              Reps on this carrier are paid from their assigned <b>Commission Plan</b> — the Boost KPI‑tier
              multiplier does not apply. Manage pay under{' '}
              <a href="/commcalc/payout-plans" style={{ color: 'var(--accent)' }}>Commission Payout Plans</a>.
            </div>
          )}
          {isBoost && [{pct: 100, color: '#16a34a'}, {pct: 75, color: '#d97706'}, {pct: 50, color: '#dc2626'}].map(({ pct, color }) => (
            <div key={pct} style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color }}>{pct}% Tier</span>
                <span style={{ fontSize: 13, color: 'var(--text2)' }}>{tierCounts[pct] || 0} reps</span>
              </div>
              <div style={{ background: 'var(--surface2)', borderRadius: 4, height: 8, overflow: 'hidden' }}>
                <div style={{
                  background: color, height: '100%', borderRadius: 4,
                  width: reps.length > 0 ? `${((tierCounts[pct] || 0) / reps.length) * 100}%` : '0%',
                  transition: 'width 0.5s',
                }} />
              </div>
            </div>
          ))}
        </div>

        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: '16px 20px', fontWeight: 600, borderBottom: '1px solid var(--border)' }}>
            Top Earners
          </div>
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
              <div className="spinner" />
            </div>
          ) : (
            <div className="table-wrapper" style={{ border: 'none', borderRadius: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>Rep</th>
                    <th>Store</th>
                    <th>{isBoost ? 'Tier' : 'Basis'}</th>
                    <th>PA</th>
                    <th>BA</th>
                    <th>UA</th>
                    <th style={{ textAlign: 'right' }}>Payout</th>
                  </tr>
                </thead>
                <tbody>
                  {reps.slice(0, 10).map((r, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 500 }}>{r.epay_salesperson}</td>
                      <td style={{ color: 'var(--text3)', fontSize: 12 }}>
                        {r.store?.substring(0, 25)}{r.store?.length > 25 ? '…' : ''}
                      </td>
                      <td>
                        {isBoost ? (
                          <span className={`badge ${r.tier >= 1 ? 'badge-green' : r.tier >= 0.75 ? 'badge-amber' : 'badge-red'}`}>
                            {Math.round((r.tier || 0) * 100)}%
                          </span>
                        ) : (
                          <span className="badge" style={{ background: 'var(--surface2)', color: 'var(--text2)' }}>Plan</span>
                        )}
                      </td>
                      <td>{r.premium_acts || 0}</td>
                      <td>{r.byod_acts || 0}</td>
                      <td>{r.upgrade_acts || 0}</td>
                      <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>
                        {fmt(r.total_payout || 0)}
                      </td>
                    </tr>
                  ))}
                  {reps.length === 0 && !loading && (
                    <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text3)', padding: 32 }}>
                      No data — run calculation first
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
