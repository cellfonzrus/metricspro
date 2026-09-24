'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import Mark from '@/components/Mark'

// Public self-serve signup — creates a new company (tenant) + its admin login. Gated server-side on
// SIGNUPS_OPEN; this page reads /core/signup-status and shows "closed" when off.
//
// THE PLANS ARE READ FROM THE SAME FEED THE MARKETING SITE READS (/billing/public-pricing, mig 908).
// There is deliberately no second price list in this file: a hardcoded price here would be a second
// derivation of what the operator publishes in Admin -> Pricing & Free Trial, and the two would
// drift the first time a price changed. When nothing is published, or the call fails, the form
// still works and simply does not offer a plan — signing up must never depend on the price list.
//
// NO CARD IS TAKEN HERE, on purpose. The trial is card-free and the marketing site says so in as
// many words; the plan captured is what they intend to land on when the trial ends. Charging is a
// separate step that belongs at conversion, not at intake.
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

type Pkg = {
  key: string; name: string; tagline?: string | null; price?: number | null
  cycle?: string | null; currency?: string | null; unit_label?: string | null
  price_note?: string | null; features?: string[] | null; is_featured?: boolean
}

const inp: React.CSSProperties = { width: '100%', marginTop: 5, padding: '11px 12px', borderRadius: 9, border: '1px solid #cbd5e1', fontSize: 15, boxSizing: 'border-box' }
const lbl: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: '#475569', marginTop: 14, display: 'block' }

function money(amount: number, currency?: string | null) {
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency', currency: currency || 'USD',
      minimumFractionDigits: Number.isInteger(amount) ? 0 : 2,
      maximumFractionDigits: Number.isInteger(amount) ? 0 : 2,
    }).format(amount)
  } catch { return `$${amount}` }
}

export default function SignupPage() {
  const [open, setOpen] = useState<boolean | null>(null)
  const [f, setF] = useState({ name: '', admin_name: '', admin_email: '', password: '' })
  const [pkgs, setPkgs] = useState<Pkg[]>([])
  const [trialDays, setTrialDays] = useState(30)
  const [picked, setPicked] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(false)

  useEffect(() => {
    fetch(`${API_URL}/api/v1/core/signup-status`).then(r => r.json())
      .then(d => setOpen(!!d.open)).catch(() => setOpen(false))
  }, [])

  useEffect(() => {
    // Best-effort: a failure here leaves the plan step out entirely rather than blocking signup.
    fetch(`${API_URL}/api/v1/billing/public-pricing`).then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return
        if (Number(d.trial_days) > 0) setTrialDays(Number(d.trial_days))
        if (d.show_pricing !== false && Array.isArray(d.packages)) {
          setPkgs(d.packages)
          const feat = d.packages.find((p: Pkg) => p.is_featured) || d.packages[0]
          if (feat) setPicked(feat.key)
        }
      }).catch(() => {})
  }, [])

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setErr(''); setBusy(true)
    try {
      const res = await fetch(`${API_URL}/api/v1/core/signup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...f, package_key: picked || undefined }),
      })
      const d = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(d.detail || `Error ${res.status}`)
      setDone(true)
    } catch (e: any) { setErr(e?.message || 'Signup failed') } finally { setBusy(false) }
  }

  const card: React.CSSProperties = { width: '100%', maxWidth: pkgs.length ? 660 : 420, background: 'white', borderRadius: 14, padding: '34px 30px', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }
  const wrap: React.CSSProperties = { minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%)', padding: 20 }
  const brand = (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 9 }}>
      <Mark size={24} />
      <span style={{ fontSize: 22, fontWeight: 800, color: '#1e3a5f', letterSpacing: '-.02em' }}>MetricsPro</span>
    </div>
  )

  if (open === null) return <div style={wrap}><div style={{ ...card, maxWidth: 420 }}>Loading…</div></div>
  if (!open) return (
    <div style={wrap}><div style={{ ...card, maxWidth: 420, textAlign: 'center' }}>
      {brand}
      <p style={{ color: '#475569', marginTop: 16 }}>Self-serve signup isn’t open yet. Contact us to get your company onboarded.</p>
      <Link href="/login" style={{ color: '#2563eb' }}>← Back to sign in</Link>
    </div></div>
  )
  if (done) return (
    <div style={wrap}><div style={{ ...card, maxWidth: 420, textAlign: 'center' }}>
      <div style={{ fontSize: 40 }}>✅</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: '#1e3a5f', marginTop: 8 }}>Company created</div>
      <p style={{ color: '#475569', marginTop: 8 }}>
        Your {trialDays}-day trial has started. Sign in with <b>{f.admin_email}</b> and the password you chose.
      </p>
      <Link href="/login" style={{ display: 'inline-block', marginTop: 12, padding: '11px 18px', background: '#1e3a5f', color: '#fff', borderRadius: 9, fontWeight: 700, textDecoration: 'none' }}>Go to sign in →</Link>
    </div></div>
  )

  return (
    <div style={wrap}>
      <div style={card}>
        <div style={{ textAlign: 'center', marginBottom: 6 }}>
          {brand}
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>
            Create your company account — {trialDays} days free, no card required
          </div>
        </div>

        {pkgs.length > 0 && (
          <div style={{ marginTop: 20 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginBottom: 8 }}>
              Choose a plan <span style={{ fontWeight: 400, color: '#94a3b8' }}>— nothing is charged during the trial</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(pkgs.length, 3)}, 1fr)`, gap: 10 }}>
              {pkgs.map(p => {
                const on = picked === p.key
                return (
                  <button type="button" key={p.key} onClick={() => setPicked(p.key)}
                    aria-pressed={on}
                    style={{
                      textAlign: 'left', cursor: 'pointer', padding: '14px 14px 16px', borderRadius: 11,
                      background: on ? '#fdf2df' : '#fff', border: `1.5px solid ${on ? '#c9770f' : '#e2e8f0'}`,
                      boxShadow: on ? '0 1px 2px rgba(13,27,38,.06)' : 'none',
                    }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 15, fontWeight: 700, color: '#0f172a' }}>{p.name}</span>
                      {p.is_featured && <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase', color: '#c9770f', background: '#fdf2df', padding: '3px 5px', borderRadius: 3 }}>Popular</span>}
                    </div>
                    <div style={{ marginTop: 6, fontVariantNumeric: 'tabular-nums' }}>
                      {Number(p.price) > 0
                        ? <><span style={{ fontSize: 21, fontWeight: 700, color: '#0f172a' }}>{money(Number(p.price), p.currency)}</span>
                            {/* Own line: "per store / month" is long enough to break mid-phrase
                                beside the figure at this card width, which reads as a typo. */}
                            <span style={{ display: 'block', fontSize: 11.5, color: '#64748b', marginTop: 2 }}>{p.unit_label || `per ${p.cycle === 'annual' ? 'year' : 'month'}`}</span></>
                        : <span style={{ fontSize: 15, fontWeight: 600, color: '#475569' }}>Talk to us</span>}
                    </div>
                    {p.tagline && <div style={{ fontSize: 12, color: '#64748b', marginTop: 6, lineHeight: 1.45 }}>{p.tagline}</div>}
                    {!!(p.features && p.features.length) && (
                      <ul style={{ margin: '9px 0 0', padding: 0, listStyle: 'none', display: 'grid', gap: 4 }}>
                        {p.features.map((x, i) => (
                          <li key={i} style={{ fontSize: 11.5, color: '#475569', paddingLeft: 13, position: 'relative' }}>
                            <span style={{ position: 'absolute', left: 0, color: '#217a5e', fontWeight: 700 }}>✓</span>{x}
                          </li>
                        ))}
                      </ul>
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        )}

        <form onSubmit={submit} style={{ marginTop: pkgs.length ? 20 : 8 }}>
          <div style={{ display: 'grid', gridTemplateColumns: pkgs.length ? '1fr 1fr' : '1fr', gap: '0 14px' }}>
            <div>
              <label style={lbl}>Company name</label>
              <input style={inp} required value={f.name} onChange={e => setF(v => ({ ...v, name: e.target.value }))} placeholder="Acme Wireless" />
            </div>
            <div>
              <label style={lbl}>Your name</label>
              <input style={inp} value={f.admin_name} onChange={e => setF(v => ({ ...v, admin_name: e.target.value }))} placeholder="Jane Admin" />
            </div>
            <div>
              <label style={lbl}>Email</label>
              <input style={inp} type="email" required value={f.admin_email} onChange={e => setF(v => ({ ...v, admin_email: e.target.value }))} placeholder="you@acme.com" />
            </div>
            <div>
              <label style={lbl}>Password (8+ characters)</label>
              <input style={inp} type="password" required minLength={8} value={f.password} onChange={e => setF(v => ({ ...v, password: e.target.value }))} placeholder="••••••••" />
            </div>
          </div>
          {err && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 12 }}>{err}</div>}
          <button type="submit" disabled={busy} style={{ width: '100%', marginTop: 18, padding: '13px 0', fontSize: 16, fontWeight: 700, background: '#1e3a5f', color: 'white', border: 'none', borderRadius: 10, cursor: 'pointer', opacity: busy ? 0.7 : 1 }}>
            {busy ? 'Creating…' : `Start my ${trialDays}-day trial`}
          </button>
          <div style={{ fontSize: 11.5, color: '#94a3b8', textAlign: 'center', marginTop: 10, lineHeight: 1.5 }}>
            By creating an account you agree to our{' '}
            <a href="https://metricspro.tech/legal/terms.html" target="_blank" rel="noopener noreferrer" style={{ color: '#64748b' }}>Terms</a>{' and '}
            <a href="https://metricspro.tech/legal/privacy.html" target="_blank" rel="noopener noreferrer" style={{ color: '#64748b' }}>Privacy Policy</a>.
          </div>
        </form>
        <div style={{ fontSize: 12, color: '#94a3b8', textAlign: 'center', marginTop: 14 }}>
          Already have an account? <Link href="/login" style={{ color: '#2563eb' }}>Sign in</Link>
        </div>
      </div>
    </div>
  )
}
