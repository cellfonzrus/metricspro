'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import RunCommissionButton from '../_lib/RunCommissionButton'
import { useActiveCarrier } from '@/lib/auth-context'

// Commission Payout Plans — the ONE place that answers "how does each carrier's rep get paid?".
// It reads /payout-plans/overview, which uses the SAME carrier gate as the live calculator, so what
// you see here is exactly what Run Calculation will do. Boost tenants pay via the built-in KPI-tier
// rates; every other carrier (e.g. Total) pays ONLY from its configured Commission Plans / Payout
// Schedules — never the hardcoded Boost tiers.

type CarrierRow = {
  id: string; name: string; code: string | null; is_default: boolean; is_boost: boolean
  pays_via: 'boost_rates' | 'commission_plans' | 'unconfigured'; ready: boolean
  plan_count: number; assignment_count: number; schedule_count: number
}
type Overview = {
  org_carrier_mode: 'boost' | 'plan'
  default_carrier: { id: string; name: string; code: string | null } | null
  carriers: CarrierRow[]
}

const PAYS: Record<string, { label: string; tone: string; href: string; cta: string }> = {
  boost_rates:      { label: 'Boost KPI‑tier rates (built‑in engine)', tone: 'var(--accent)',  href: '/commcalc/settings',         cta: 'Edit Boost Rates' },
  commission_plans: { label: 'Configurable Incentive Plans',          tone: '#16a34a',         href: '/commcalc/commission-plans', cta: 'Edit Plans' },
  unconfigured:     { label: 'Not configured yet',                     tone: '#dc2626',         href: '/commcalc/commission-plans', cta: 'Set up a plan' },
}

type Diag = {
  period: string; carrier_mode: string
  sales: { rows: number; reps: string[] }
  raw_mi: { rows: number; reps: string[] }
  plans: { name: string; carrier_id: string | null; is_active: boolean; rules: number; assignments: { scope: string; value: string | null }[] }[]
  assignments_total: number; schedules: number
  plan_engine: { reps: string[]; note: string | null }
  installment_engine: { reps: string[]; totals: any; note: string | null }
  rep_commissions_now: number
  reasons: string[]
}

export default function PayoutPlansHub() {
  const { period, setPeriod } = usePeriod()
  // Active-carrier lens: a dual-carrier tenant sees only the active carrier's card + rule, and the
  // Boost-rates shortcut/row only under the Boost lens. Single-carrier tenants are unchanged.
  const { activeCarrier, multi } = useActiveCarrier()
  const carrierMatchesActive = (c: CarrierRow) => {
    const t = (c.code || c.name || '').toLowerCase()
    return !!t && (t.includes(activeCarrier) || activeCarrier.includes(t))
  }
  const [ov, setOv] = useState<Overview | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [diag, setDiag] = useState<Diag | null>(null)
  const [diagBusy, setDiagBusy] = useState(false)
  const [diagErr, setDiagErr] = useState('')

  useEffect(() => {
    api('/api/v1/commcalc/payout-plans/overview')
      .then((d: Overview) => setOv(d))
      .catch((e: any) => setErr(e?.message || String(e)))
      .finally(() => setLoading(false))
  }, [])

  function runDiag() {
    setDiagBusy(true); setDiagErr(''); setDiag(null)
    api(`/api/v1/commcalc/payout-plans/diagnose?period=${encodeURIComponent(period)}`)
      .then((d: Diag) => setDiag(d))
      .catch((e: any) => setDiagErr(e?.message || String(e)))
      .finally(() => setDiagBusy(false))
  }

  const chip = (bg: string, color = '#fff') => ({
    padding: '2px 9px', borderRadius: 999, fontSize: 11, fontWeight: 700,
    background: bg, color, display: 'inline-block',
  })

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💳 Incentive Payout Plans</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
          One home for how every carrier pays its reps. Each carrier below maps to the engine that
          actually calculates its incentive — this is exactly what <b>Run Calculation</b> uses.
        </p>
      </div>

      {/* configuration surfaces */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
        <Link href="/commcalc/commission-plans" className="btn btn-sm">🧮 Incentive Plans</Link>
        <Link href="/commcalc/payout-schedules" className="btn btn-sm">📆 Payout Schedules</Link>
        {activeCarrier === 'boost' && <Link href="/commcalc/settings" className="btn btn-sm">⚙️ Boost Rates</Link>}
        <Link href="/commcalc/carrier-mapping" className="btn btn-sm">📡 Carrier Mapping</Link>
        <Link href="/commcalc/commission-category-map" className="btn btn-sm">🗺️ Category → Bucket Map</Link>
        <Link href="/commcalc/commission-import" className="btn btn-sm">🪄 Import Wizard</Link>
      </div>

      {/* RUN COMMISSION (owner directive 2026-08-05) — the same shared control the three editor pages
          mount. This hub is where an operator lands after changing which engine a carrier pays from,
          so the recalculate lives here too. Hidden for anyone who cannot reach /commcalc/payout-schedules. */}
      <div className="card" style={{ padding: 16, marginBottom: 18, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>⚡ Apply the current structure to live pay</div>
        <RunCommissionButton period={period} onPeriodChange={setPeriod}
          note="Config changes on any of the pages above do nothing until the period is recalculated." />
      </div>

      {loading && <div className="card" style={{ padding: 16 }}>Loading…</div>}
      {err && <div className="card" style={{ padding: 16, color: '#dc2626' }}>Failed to load: {err}</div>}

      {ov && !loading && (
        <>
          {ov.carriers.length === 0 && (
            <div className="card" style={{ padding: 16 }}>
              No carriers selected yet. Pick this company’s carrier(s) on{' '}
              <Link href="/admin/tenant-settings">Pay Period &amp; Work‑Week → Carriers</Link>, then set up a plan here.
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 }}>
            {(multi ? ov.carriers.filter(carrierMatchesActive) : ov.carriers).map(c => {
              const p = PAYS[c.pays_via]
              return (
                <div key={c.id} className="card" style={{ padding: 16, borderLeft: `4px solid ${p.tone}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <div style={{ fontSize: 16, fontWeight: 700 }}>{c.name}</div>
                    {c.is_default && <span style={chip('var(--text3)')}>DEFAULT</span>}
                    {c.ready
                      ? <span style={chip('#16a34a')}>PAYS</span>
                      : <span style={chip('#dc2626')}>$0 — SETUP</span>}
                  </div>

                  <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 4 }}>Reps paid via</div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: p.tone, marginBottom: 10 }}>{p.label}</div>

                  {!c.is_boost && (
                    <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.7, marginBottom: 10 }}>
                      Incentive plans: <b>{c.plan_count}</b> &nbsp;·&nbsp; Rep assignments: <b>{c.assignment_count}</b> &nbsp;·&nbsp; Payout schedules: <b>{c.schedule_count}</b>
                      {!c.ready && (
                        <div style={{ color: '#dc2626', marginTop: 6 }}>
                          ⚠️ No plan assignment or schedule — reps on this carrier will calculate to <b>$0</b> until you add one.
                        </div>
                      )}
                    </div>
                  )}
                  {c.is_boost && (
                    <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6, marginBottom: 10 }}>
                      Uses the verified Boost engine (flat spiffs × KPI‑count tier). Edit its rates, KPI targets and tiers on Boost Rates.
                    </div>
                  )}

                  <Link href={p.href} className="btn btn-sm">{p.cta} →</Link>
                </div>
              )
            })}
          </div>

          <div className="card" style={{ padding: 14, marginTop: 16, fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
            {multi
              ? <><b>How pay is decided:</b> {activeCarrier === 'boost'
                  ? 'reps are paid by the built‑in KPI‑tier rates.'
                  : 'reps are paid from the Incentive Plan / Payout Schedule assigned to them.'} Current mode:{' '}
                  <span style={chip(activeCarrier === 'boost' ? 'var(--accent)' : '#16a34a')}>
                    {activeCarrier === 'boost' ? 'BOOST ENGINE' : 'CONFIGURABLE PLANS'}
                  </span></>
              : <><b>How pay is decided:</b> the calculator looks at this company’s <b>default carrier</b>
                  {ov.default_carrier ? <> (<b>{ov.default_carrier.name}</b>)</> : ''}. {ov.org_carrier_mode === 'boost'
                    ? 'Reps are paid by the built‑in KPI‑tier rates.'
                    : 'Each rep is paid from the Incentive Plan / Payout Schedule assigned to them.'} Current mode:{' '}
                  <span style={chip(ov.org_carrier_mode === 'boost' ? 'var(--accent)' : '#16a34a')}>
                    {ov.org_carrier_mode === 'boost' ? 'BOOST ENGINE' : 'CONFIGURABLE PLANS'}
                  </span></>}
          </div>

          {/* Diagnostic — why aren't reps captured in the report? */}
          <div className="card" style={{ padding: 16, marginTop: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <div style={{ fontWeight: 700, fontSize: 15 }}>🔎 Why aren’t reps showing in the incentive report?</div>
              <button className="btn btn-sm btn-primary" disabled={diagBusy} onClick={runDiag}>
                {diagBusy ? 'Checking…' : `Run diagnostic for ${period}`}
              </button>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>
              Read-only. Checks the exact roster sources + plan/installment engines for this period and tells you what’s missing.
            </div>
            {diagErr && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 10 }}>Failed: {diagErr}</div>}
            {diag && (
              <div style={{ marginTop: 12 }}>
                <ol style={{ margin: '0 0 12px 18px', fontSize: 13, lineHeight: 1.7 }}>
                  {diag.reasons.map((r, i) => <li key={i} style={{ marginBottom: 4 }}>{r}</li>)}
                </ol>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 10, fontSize: 12.5 }}>
                  {[
                    ['Carrier mode', diag.carrier_mode.toUpperCase()],
                    ['Sales rows', `${diag.sales.rows} (${diag.sales.reps.length} reps)`],
                    ['raw_mi rows', `${diag.raw_mi.rows} (${diag.raw_mi.reps.length} reps)`],
                    ['Plans', String(diag.plans.length)],
                    ['Rep assignments', String(diag.assignments_total)],
                    ['Payout schedules', String(diag.schedules)],
                    ['Plan-engine reps', String(diag.plan_engine.reps.length)],
                    ['Installment reps', String(diag.installment_engine.reps.length)],
                    ['In report now', String(diag.rep_commissions_now)],
                  ].map(([k, v]) => (
                    <div key={k} className="card" style={{ padding: '8px 10px' }}>
                      <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{k}</div>
                      <div style={{ fontWeight: 700, marginTop: 2 }}>{v}</div>
                    </div>
                  ))}
                </div>
                {diag.plans.length > 0 && (
                  <div style={{ marginTop: 12, fontSize: 12.5 }}>
                    <div style={{ fontWeight: 700, marginBottom: 4 }}>Plans &amp; assignments</div>
                    {diag.plans.map((pl, i) => (
                      <div key={i} style={{ padding: '4px 0', borderTop: '1px solid var(--border)' }}>
                        <b>{pl.name}</b> — {pl.rules} rule(s), {pl.assignments.length} assignment(s)
                        {pl.assignments.length > 0 && (
                          <span style={{ color: 'var(--text3)' }}> · {pl.assignments.map(a => `${a.scope}=${a.value ?? '(any)'}`).join(', ')}</span>
                        )}
                        {!pl.is_active && <span style={{ color: '#dc2626' }}> · INACTIVE</span>}
                      </div>
                    ))}
                  </div>
                )}
                <div style={{ marginTop: 10, fontSize: 12 }}>
                  After fixing config, run <Link href="/commcalc" style={{ color: 'var(--accent)' }}>Dashboard → Run Calculation</Link> for {period}.
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
