'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

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
  commission_plans: { label: 'Configurable Commission Plans',          tone: '#16a34a',         href: '/commcalc/commission-plans', cta: 'Edit Plans' },
  unconfigured:     { label: 'Not configured yet',                     tone: '#dc2626',         href: '/commcalc/commission-plans', cta: 'Set up a plan' },
}

export default function PayoutPlansHub() {
  const [ov, setOv] = useState<Overview | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api('/api/v1/commcalc/payout-plans/overview')
      .then((d: Overview) => setOv(d))
      .catch((e: any) => setErr(e?.message || String(e)))
      .finally(() => setLoading(false))
  }, [])

  const chip = (bg: string, color = '#fff') => ({
    padding: '2px 9px', borderRadius: 999, fontSize: 11, fontWeight: 700,
    background: bg, color, display: 'inline-block',
  })

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💳 Commission Payout Plans</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
          One home for how every carrier pays its reps. Each carrier below maps to the engine that
          actually calculates its commission — this is exactly what <b>Run Calculation</b> uses.
        </p>
      </div>

      {/* configuration surfaces */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
        <Link href="/commcalc/commission-plans" className="btn btn-sm">🧮 Commission Plans</Link>
        <Link href="/commcalc/payout-schedules" className="btn btn-sm">📆 Payout Schedules</Link>
        <Link href="/commcalc/settings" className="btn btn-sm">⚙️ Boost Rates</Link>
        <Link href="/commcalc/carrier-mapping" className="btn btn-sm">📡 Carrier Mapping</Link>
        <Link href="/commcalc/commission-category-map" className="btn btn-sm">🗺️ Category → Bucket Map</Link>
        <Link href="/commcalc/commission-import" className="btn btn-sm">🪄 Import Wizard</Link>
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
            {ov.carriers.map(c => {
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
                      Commission plans: <b>{c.plan_count}</b> &nbsp;·&nbsp; Rep assignments: <b>{c.assignment_count}</b> &nbsp;·&nbsp; Payout schedules: <b>{c.schedule_count}</b>
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
            <b>How pay is decided:</b> the calculator looks at this company’s <b>default carrier</b>
            {ov.default_carrier ? <> (<b>{ov.default_carrier.name}</b>)</> : ''}. If it’s Boost, reps are paid
            by the built‑in KPI‑tier rates. If it’s any other carrier, the Boost tiers are <b>skipped entirely</b>
            {' '}and each rep is paid from the Commission Plan / Payout Schedule assigned to them. Current mode:{' '}
            <span style={chip(ov.org_carrier_mode === 'boost' ? 'var(--accent)' : '#16a34a')}>
              {ov.org_carrier_mode === 'boost' ? 'BOOST ENGINE' : 'CONFIGURABLE PLANS'}
            </span>
          </div>
        </>
      )}
    </div>
  )
}
