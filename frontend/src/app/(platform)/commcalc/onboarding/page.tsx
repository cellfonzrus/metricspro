'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { api, getActiveOrg } from '@/lib/client'

// Onboarding Wizard — a guided, plain-English setup so a new tenant can stand the platform up without knowing
// the menus. Driven by GET /onboarding-checklist (which reads the data-lineage schematic): each data feed /
// config item shows whether it's ready for this tenant, what it powers, and a button to the exact place to
// complete it. DISPLAY/config only.
const orgQ = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

// Where to go to complete each item (deep link + verb). Unknown keys fall back to the System Schematic.
const CTA: Record<string, { label: string; href: string }> = {
  sales_transactions:        { label: 'Upload sales', href: '/commcalc/upload' },
  activation_details_report: { label: 'Upload Activation Details', href: '/commcalc/upload' },
  bill_payments_report:      { label: 'Upload Bill Payments', href: '/commcalc/upload' },
  product_sales_report:      { label: 'Upload Sales by Product', href: '/commcalc/upload' },
  store_performance_report:  { label: 'Upload Store Performance', href: '/commcalc/upload' },
  processor_epay:            { label: 'Set up ePay', href: '/commcalc/epay' },
  processor_vidapay:         { label: 'Set up VidaPay / MA', href: '/commcalc/ma-upload' },
  residual_report:           { label: 'Upload residual / MA commission', href: '/commcalc/ma-upload' },
  mi_report:                 { label: 'Upload MI report', href: '/commcalc/upload' },
  daily_cash:                { label: 'Set up daily closing', href: '/commcalc/expenses' },
  store_identity:            { label: 'Map stores', href: '/commcalc/mapping' },
  accessories:               { label: 'Set accessory departments', href: '/commcalc/sales-report' },
  activation_basis:          { label: 'Choose activation basis', href: '/commcalc/activations' },
}

export default function OnboardingPage() {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [showDone, setShowDone] = useState(true)
  const [dr, setDr] = useState<any>(null)   // data-first reverse map (what's ingested → what it powers)

  const load = () => {
    setLoading(true); setErr(null)
    Promise.all([
      api(`/api/v1/commcalc/onboarding-checklist${orgQ()}`),
      api(`/api/v1/commcalc/data-readiness${orgQ()}`).catch(() => null),
    ]).then(([cl, d]: any[]) => { setData(cl); setDr(d) })
      .catch(e => setErr(e?.message || String(e))).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const items: any[] = data?.items || []
  const ready = data?.ready ?? 0
  const total = data?.total ?? 0
  const pct = total ? Math.round((ready / total) * 100) : 0
  const shown = items.filter(i => showDone || !i.present)

  return (
    <div style={{ padding: '18px 22px', maxWidth: 900 }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>Setup Wizard</h1>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 6, marginBottom: 14, maxWidth: 760 }}>
        A step-by-step of everything the platform needs to work for you. Each item shows whether it&rsquo;s set up,
        what it powers, and a button to complete it. Green means ready; amber means a report or number will be
        blank until you provide it.
      </p>

      {/* DATA-FIRST view (owner): what's ingested → what it powers, and which reports are blocked. */}
      {!loading && dr && (
        <div style={{ marginBottom: 20 }}>
          <h2 style={{ fontSize: 15, fontWeight: 800, margin: '0 0 6px' }}>
            Your data <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12.5 }}>({dr.ingested_count ?? 0} feeds ingested · {dr.reports_powered ?? 0}/{dr.reports_total ?? 0} reports powered)</span>
          </h2>
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8, maxWidth: 780 }}>
            What the platform has actually ingested for you, and the reports each feed powers. This is the
            reverse of the schematic — start here to see what&rsquo;s live and what&rsquo;s still blank.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            {/* Ingested feeds */}
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
              <div style={{ background: 'var(--surface2, #f8fafc)', padding: '7px 11px', fontSize: 12, fontWeight: 700 }}>Ingested feeds</div>
              {(dr.ingested || []).map((s: any) => (
                <div key={s.source_key} style={{ padding: '7px 11px', borderTop: '1px solid var(--border)', fontSize: 12.5 }}>
                  <span style={{ marginRight: 6 }}>{s.present ? '✅' : '⬜'}</span>
                  <b>{s.source_label}</b>{s.present && s.count ? <span style={{ color: 'var(--text3)' }}> · {s.count}</span> : null}
                  {(s.reports || []).length > 0 && (
                    <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 2, marginLeft: 20 }}>
                      powers: {(s.reports || []).slice(0, 5).join(' · ')}{(s.reports || []).length > 5 ? ` +${s.reports.length - 5}` : ''}
                    </div>
                  )}
                </div>
              ))}
            </div>
            {/* Reports status */}
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
              <div style={{ background: 'var(--surface2, #f8fafc)', padding: '7px 11px', fontSize: 12, fontWeight: 700 }}>Reports &amp; menus</div>
              {(dr.reports || []).map((r: any, i: number) => (
                <div key={i} style={{ padding: '7px 11px', borderTop: '1px solid var(--border)', fontSize: 12.5 }}>
                  <span style={{ marginRight: 6 }}>{r.powered ? '🟢' : '🔴'}</span>
                  <b>{r.report}</b>
                  {!r.powered && (r.needs || []).length > 0 && (
                    <div style={{ fontSize: 11.5, color: '#92400e', marginTop: 2, marginLeft: 20 }}>needs: {(r.needs || []).join(' · ')}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <h2 style={{ fontSize: 15, fontWeight: 800, margin: '4px 0 8px' }}>Setup steps</h2>

      {/* Progress */}
      {!loading && !err && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 4 }}>
            <span style={{ fontWeight: 700 }}>{ready} of {total} set up</span>
            <span style={{ color: 'var(--text3)' }}>{pct}%</span>
          </div>
          <div style={{ height: 10, background: 'var(--surface2, #eef2f7)', borderRadius: 999, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: pct === 100 ? '#16a34a' : '#2563eb', transition: 'width .3s' }} />
          </div>
          <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center', fontSize: 12, marginTop: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={showDone} onChange={e => setShowDone(e.target.checked)} /> Show completed steps
          </label>
        </div>
      )}

      {err && <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b', borderRadius: 8, padding: '9px 12px', fontSize: 12.5, marginBottom: 10 }}>❌ {err}</div>}
      {loading && <div style={{ color: 'var(--text3)', fontSize: 13, padding: 20 }}>Loading…</div>}

      {!loading && shown.map((it, idx) => {
        const cta = CTA[it.key] || { label: 'See in schematic', href: '/commcalc/schematic' }
        const done = it.present
        return (
          <div key={it.key} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', border: '1px solid var(--border)',
            borderRadius: 10, padding: '12px 14px', marginBottom: 10,
            background: done ? 'var(--surface)' : '#fffdf5' }}>
            <div style={{ flexShrink: 0, width: 26, height: 26, borderRadius: 999, display: 'flex', alignItems: 'center',
              justifyContent: 'center', fontSize: 14, fontWeight: 800,
              background: done ? '#dcfce7' : '#fef3c7', color: done ? '#166534' : '#92400e' }}>
              {done ? '✓' : idx + 1}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 14, fontWeight: 700 }}>{it.label}</span>
                <span style={{ fontSize: 11, fontWeight: 700, borderRadius: 6, padding: '1px 7px',
                  background: done ? '#dcfce7' : '#fef3c7', color: done ? '#166534' : '#92400e' }}>
                  {done ? `Ready${it.count ? ` · ${it.count}` : ''}` : 'Not set up'}
                </span>
              </div>
              <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 3 }}>{it.how}</div>
              {(it.powers || []).length > 0 && (
                <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>
                  Powers: {(it.powers || []).slice(0, 4).map((p: any) => p.affected || p.surface).filter(Boolean).join(' · ')}
                  {(it.powers || []).length > 4 ? ` +${it.powers.length - 4} more` : ''}
                </div>
              )}
            </div>
            <Link href={cta.href} style={{ flexShrink: 0, alignSelf: 'center', fontSize: 12.5, fontWeight: 700,
              textDecoration: 'none', padding: '7px 12px', borderRadius: 8,
              background: done ? 'transparent' : 'var(--accent, #2563eb)', color: done ? 'var(--text2)' : '#fff',
              border: done ? '1px solid var(--border)' : 'none' }}>
              {done ? 'Review' : cta.label} →
            </Link>
          </div>
        )
      })}

      {!loading && shown.length === 0 && (
        <div style={{ background: '#ecfdf5', border: '1px solid #6ee7b7', color: '#065f46', borderRadius: 8, padding: '12px 14px', fontSize: 13 }}>
          🎉 Everything is set up. Nothing left to configure.
        </div>
      )}

      <div style={{ marginTop: 14, fontSize: 12, color: 'var(--text3)' }}>
        Want the full dependency map? Open the <Link href="/commcalc/schematic" style={{ color: 'var(--text2)', fontWeight: 700 }}>System Schematic</Link>.
      </div>
    </div>
  )
}
