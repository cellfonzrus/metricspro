'use client'
import { useState, useEffect, useRef } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { useAuth } from '@/lib/auth-context'
import { carrierMode, payoutRefused } from '@/lib/rbac'
import { buttonState, isError, isRunning, settleStatus, nextPollDelay, POLL_GIVE_UP_MS } from '@/lib/job-run'
import StatTile from '@/components/StatTile'
import { GoogleRatingChips, useGoogleRatings } from './_lib/googleRatings'

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

// Badge colour per job-run phase. The PHASES are owned by job-run.ts; only their colour is local.
const BADGE_COLOR: Record<string, string> = {
  idle: 'var(--text3)',
  running: 'var(--amber)',
  overdue: 'var(--amber)',
  done: 'var(--green)',
  error: 'var(--red)',
}

export default function CommCalcDashboard() {
  const { period } = usePeriod()
  const { carriers, permissions } = useAuth()
  const isBoost = carrierMode(carriers) === 'boost'   // non-Boost carriers pay via plans, not KPI tiers
  const [reps, setReps] = useState<RepRow[]>([])
  const [loading, setLoading] = useState(true)
  const [calcStatus, setCalcStatus] = useState<string>('')
  const [calcError, setCalcError] = useState<string>('')
  // CALC WARNINGS (mig 243): a calc can SUCCEED and still leave real activations paid by nothing —
  // plan rules have no exclusivity and the multi-month engine has its own trigger, so a rule that stops
  // matching does not hand its lines to anything else. Read-only; never blocks anything.
  const [calcWarn, setCalcWarn] = useState<any>(null)
  const [calcNotices, setCalcNotices] = useState<any[]>([])   // mig 247 — what the calc did NOT pay
  // WHEN THIS PAGE STARTED A RUN (owner report 2026-10-06, index §6q). `job-run.ts` is the ONE home
  // for what the control shows; this ref is only the local fact it needs — the epoch ms of OUR press,
  // so a status read that PREDATES the press cannot downgrade it back to "not calculated yet". That
  // downgrade is what made the button look pressable mid-run and earned the server's 409.
  const pressedAt = useRef<number | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // Whether the run we started has outlived job-run's poll schedule. A BOOLEAN rather than a live
  // elapsed-ms figure on purpose: reading the clock during render makes the render impure, and the
  // only thing the control needs to know is whether we have stopped following the run.
  const [overdue, setOverdue] = useState(false)

  useEffect(() => {
    // A new month is a different job: forget our press so the new period's real status is shown.
    // `pressedAt` is a ref, so this costs no render; `overdue` is cleared by the press itself.
    pressedAt.current = null
    loadData()
  }, [period])

  // FOLLOW THE RUN TO ITS END (owner report 2026-10-06). The page used to ask the server once, two
  // seconds after the press, and never again — against a job that takes about a minute. The schedule
  // lives in job-run.POLL_SCHEDULE (quick at first, then backing off, then stopping) so no surface
  // invents its own cadence and none polls forever.
  useEffect(() => {
    if (pressedAt.current === null || !isRunning(calcStatus)) return
    let alive = true
    // The clock is read HERE, in the effect, never during render.
    const delay = nextPollDelay(Date.now() - pressedAt.current)
    if (delay === null) return
    const t = setTimeout(() => {
      if (!alive) return
      if (pressedAt.current !== null && nextPollDelay(Date.now() - pressedAt.current) === null) setOverdue(true)
      loadData()
    }, delay)
    return () => { alive = false; clearTimeout(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [calcStatus, overdue])

  async function loadData() {
    setLoading(true)
    try {
      const enc = encodeURIComponent(period)
      const [comms, status] = await Promise.all([
        api(`/api/v1/commcalc/commissions/${enc}?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/calc-status/${enc}?org_id=${ORG_ID}`),
      ])
      setReps(comms || [])
      // ONE rule for reconciling our press with the server's word (job-run.settleStatus): a read
      // that is not strictly later than the press may not contradict it.
      setCalcStatus(settleStatus(status?.calc_status, { pressedAt: pressedAt.current, readAt: Date.now() }))
      // Surface a REFUSED / error calc (e.g. the R1 unconfigured-tenant guard) instead of a silent
      // "0 reps" — save_errors carries the actionable message.
      const errs = status?.save_errors
      setCalcError(isError(status?.calc_status)
        ? (Array.isArray(errs) ? errs.join(' ') : (errs || 'Calculation failed.')) : '')
      // pre-mig-243 the column doesn't exist, so fall back to computing them live
      const w = status?.calc_warnings
      if (w && (w.counts?.unpaid_activations || w.counts?.unassigned_reps)) setCalcWarn(w)
      else {
        try {
          const live = await api(`/api/v1/commcalc/commission-plans/pay-warnings?period=${enc}&org_id=${ORG_ID}`)
          setCalcWarn((live?.counts?.unpaid_activations || live?.counts?.unassigned_reps) ? live : null)
        } catch { setCalcWarn(null) }
      }
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

  // Google store rating chips for the Top Earners list (owner 2026-08-06) — ONE batched call for the
  // ten rows shown, display-only, and completely invisible until the Google Reviews endpoints exist.
  const topReps = reps.slice(0, 10)
  const { ratingsFor: googleFor } = useGoogleRatings(
    topReps.map(r => (r as any).storeops_name || r.epay_salesperson))

  // The control's whole appearance, from the ONE home. `idleLabel` is this page's own wording for
  // the action; everything else (label while running, disabled, badge) is derived there so no second
  // surface can spell "running" differently.
  const btn = buttonState({
    status: calcStatus,
    idleLabel: '⚡ Run Calculation',
    elapsedMs: overdue ? POLL_GIVE_UP_MS + 1 : null,
    submitting,
  })

  const totalPayout = reps.reduce((s, r) => s + (r.total_payout || 0), 0)
  const totalActs   = reps.reduce((s, r) => s + (r.premium_acts || 0) + (r.byod_acts || 0), 0)
  const totalUpgrades = reps.reduce((s, r) => s + (r.upgrade_acts || 0), 0)
  const tierCounts = { 100: 0, 75: 0, 50: 0 } as Record<number, number>
  reps.forEach(r => {
    const t = Math.round((r.tier || 0.5) * 100)
    tierCounts[t] = (tierCounts[t] || 0) + 1
  })

  const KPI_CARDS = [
    { label: 'Total Incentive Payout', value: fmt(totalPayout), color: 'var(--accent)', icon: '💰', hero: true },
    { label: 'Total Activations', value: totalActs.toLocaleString(), color: 'var(--green)', icon: '📱' },
    { label: 'Total Upgrades', value: totalUpgrades.toLocaleString(), color: '#7c3aed', icon: '🔄' },
    { label: 'Reps Calculated', value: reps.length.toLocaleString(), color: 'var(--amber)', icon: '👥' },
  ]

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>CommCalc Dashboard</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · {reps.length} reps
            {/* ONE source for the badge AND the button (job-run.buttonState), so the two can never
                disagree — the reported defect was exactly that disagreement. */}
            <span style={{ marginLeft: 8, color: BADGE_COLOR[btn.phase] }}>
              {btn.phase === 'error' ? '⚠ Calculation refused' : btn.badge}
            </span>
          </p>
        </div>
        <button
          disabled={btn.disabled}
          title={btn.disabled ? btn.badge : undefined}
          onClick={async () => {
            // A PRESS IS A REQUEST, and the job is running until the SERVER says it is not. We mark
            // the press FIRST so settleStatus can protect it from a status read that predates it.
            if (btn.disabled) return
            pressedAt.current = Date.now()
            setOverdue(false)
            setSubmitting(true)
            setCalcStatus('running')
            try {
              await api(`/api/v1/commcalc/calculate/${encodeURIComponent(period)}?org_id=${ORG_ID}`, { method: 'POST' })
            } catch (e: any) {
              // A 409 here means a calculation for this month is ALREADY running (single-flight guard).
              // The button being disabled while we know a run is in flight means this is now reachable
              // only when ANOTHER tab or the landing hook started one — so keep 'running' and let the
              // poll follow that run, rather than alerting and forgetting it.
              const busy = /already running/i.test(String(e?.message || ''))
              if (!busy) { pressedAt.current = null; alert(e.message) }
            } finally {
              // Only the SUBMIT is over here. Clearing a busy flag in `finally` as if the JOB were
              // over is the class of defect this page was fixed for — see job-run.ts.
              setSubmitting(false)
            }
            loadData()
          }}
          className="btn btn-primary"
        >
          {btn.label}
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
          <div style={{ fontWeight: 700, color: 'var(--red)', marginBottom: 6 }}>⚠ Incentive calculation refused — last good snapshot kept</div>
          <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.6 }}>{calcError}</div>
          {/REFUSED|no commission source|plan mode/i.test(calcError) && (
            <div style={{ marginTop: 10, display: 'flex', gap: 12, alignItems: 'center' }}>
              <a href="/commcalc/commission-plans" className="btn btn-primary" style={{ textDecoration: 'none' }}>
                Configure Incentive Plans →
              </a>
              <a href="/commcalc/plan-installments" style={{ color: 'var(--accent)', fontSize: 13 }}>
                Multi-month schedules & pay settings
              </a>
            </div>
          )}
        </div>
      )}

      {/* Unpaid-activation warnings — a SUCCESSFUL calc that still left activations paying $0 */}
      {calcWarn && (
        <div className="card" style={{ borderLeft: '4px solid var(--amber)', background: 'var(--surface2)', marginBottom: 24 }}>
          <div style={{ fontWeight: 700, color: '#b45309', marginBottom: 6 }}>
            ⚠ Calculated — but {calcWarn.counts?.unpaid_activations || 0} transaction(s) paid nothing
            {calcWarn.counts?.unassigned_reps ? ` and ${calcWarn.counts.unassigned_reps} seller(s) have no plan attached` : ''}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.6 }}>
            These activations matched no Incentive-Plan rule and no multi-month schedule trigger, so no
            configured source pays them. That is usually a rule keyed on the wrong field (an item-description
            keyword instead of the tender / contract type), or a missing multi-month trigger.
          </div>
          <ul style={{ fontSize: 12, color: 'var(--text2)', margin: '8px 0 0', paddingLeft: 18, lineHeight: 1.7 }}>
            {(calcWarn.unpaid_activations || []).slice(0, 6).map((g: any, i: number) => (
              <li key={i}>
                <b>{g.rep}</b> · trans {g.trans_id} · {g.date} · {g.activations} activation(s) ·{' '}
                {(g.samples || []).map((s: any) => s.product).filter(Boolean).slice(0, 2).join(' | ') || '—'}
              </li>
            ))}
            {(calcWarn.unassigned_reps || []).slice(0, 3).map((u: any, i: number) => (
              <li key={`u${i}`}>{u.rep} — {u.reason}</li>
            ))}
          </ul>
          <div style={{ marginTop: 10, display: 'flex', gap: 12, alignItems: 'center' }}>
            <a href="/commcalc/commission-plans" style={{ color: 'var(--accent)', fontSize: 13 }}>Review Incentive Plans →</a>
            <a href="/commcalc/plan-installments" style={{ color: 'var(--accent)', fontSize: 13 }}>Multi-month schedules →</a>
            {/* a carrier surface (index §6m) — offered only to a viewer the server serves it to */}
            {!payoutRefused(permissions, '/commcalc/commission-explain') && (
              <a href="/commcalc/commission-explain" style={{ color: 'var(--accent)', fontSize: 13 }}>Explain a rep's pay →</a>
            )}
            <a href="/commcalc/accessory-cost-audit" style={{ color: 'var(--accent)', fontSize: 13 }}>Accessory cost audit →</a>
          </div>
        </div>
      )}

      {/* KPI tiles — bento layout: the payout headline reads as the hero, the counts support it. */}
      <div className="stat-grid" style={{ marginBottom: 24 }}>
        {KPI_CARDS.map(({ label, value, color, icon, hero }) => (
          <StatTile key={label} label={label} value={value} icon={icon} accent={color} hero={hero} />
        ))}
      </div>

      {/* Tier Distribution + Top Reps */}
      <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16, marginBottom: 24 }}>
        <div className="card">
          <div style={{ fontWeight: 600, marginBottom: 16 }}>{isBoost ? 'Tier Distribution' : 'Payout Basis'}</div>
          {!isBoost && (
            <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.6 }}>
              Reps on this carrier are paid from their assigned <b>Incentive Plan</b> — the built‑in KPI‑tier
              multiplier does not apply. Manage pay under{' '}
              <a href="/commcalc/payout-plans" style={{ color: 'var(--accent)' }}>Incentive Payout Plans</a>.
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
                  {topReps.map((r, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 500 }}>
                        {r.epay_salesperson}
                        <span style={{ display: 'block', marginTop: 2 }}>
                          <GoogleRatingChips list={googleFor((r as any).storeops_name || r.epay_salesperson)} compact />
                        </span>
                      </td>
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
