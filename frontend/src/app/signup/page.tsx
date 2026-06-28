'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'

// Public self-serve signup — creates a new company (tenant) + its admin login. Gated server-side on
// SIGNUPS_OPEN; this page reads /core/signup-status and shows "closed" when off.
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const inp: React.CSSProperties = { width: '100%', marginTop: 5, padding: '11px 12px', borderRadius: 9, border: '1px solid #cbd5e1', fontSize: 15, boxSizing: 'border-box' }
const lbl: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: '#475569', marginTop: 14, display: 'block' }

export default function SignupPage() {
  const [open, setOpen] = useState<boolean | null>(null)
  const [f, setF] = useState({ name: '', admin_name: '', admin_email: '', password: '' })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(false)

  useEffect(() => {
    fetch(`${API_URL}/api/v1/core/signup-status`).then(r => r.json())
      .then(d => setOpen(!!d.open)).catch(() => setOpen(false))
  }, [])

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setErr(''); setBusy(true)
    try {
      const res = await fetch(`${API_URL}/api/v1/core/signup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(f),
      })
      const d = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(d.detail || `Error ${res.status}`)
      setDone(true)
    } catch (e: any) { setErr(e?.message || 'Signup failed') } finally { setBusy(false) }
  }

  const card: React.CSSProperties = { width: '100%', maxWidth: 420, background: 'white', borderRadius: 14, padding: '34px 30px', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }
  const wrap: React.CSSProperties = { minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%)', padding: 20 }

  if (open === null) return <div style={wrap}><div style={card}>Loading…</div></div>
  if (!open) return (
    <div style={wrap}><div style={{ ...card, textAlign: 'center' }}>
      <div style={{ fontSize: 22, fontWeight: 800, color: '#1e3a5f' }}>MetricsPro</div>
      <p style={{ color: '#475569', marginTop: 16 }}>Self-serve signup isn’t open yet. Contact us to get your company onboarded.</p>
      <Link href="/login" style={{ color: '#2563eb' }}>← Back to sign in</Link>
    </div></div>
  )
  if (done) return (
    <div style={wrap}><div style={{ ...card, textAlign: 'center' }}>
      <div style={{ fontSize: 40 }}>✅</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: '#1e3a5f', marginTop: 8 }}>Company created</div>
      <p style={{ color: '#475569', marginTop: 8 }}>Sign in with <b>{f.admin_email}</b> and the password you chose.</p>
      <Link href="/login" style={{ display: 'inline-block', marginTop: 12, padding: '11px 18px', background: '#1e3a5f', color: '#fff', borderRadius: 9, fontWeight: 700, textDecoration: 'none' }}>Go to sign in →</Link>
    </div></div>
  )

  return (
    <div style={wrap}>
      <div style={card}>
        <div style={{ textAlign: 'center', marginBottom: 8 }}>
          <div style={{ fontSize: 22, fontWeight: 800, color: '#1e3a5f' }}>MetricsPro</div>
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 2 }}>Create your company account</div>
        </div>
        <form onSubmit={submit}>
          <label style={lbl}>Company name</label>
          <input style={inp} required value={f.name} onChange={e => setF(v => ({ ...v, name: e.target.value }))} placeholder="Acme Wireless" />
          <label style={lbl}>Your name</label>
          <input style={inp} value={f.admin_name} onChange={e => setF(v => ({ ...v, admin_name: e.target.value }))} placeholder="Jane Admin" />
          <label style={lbl}>Email</label>
          <input style={inp} type="email" required value={f.admin_email} onChange={e => setF(v => ({ ...v, admin_email: e.target.value }))} placeholder="you@acme.com" />
          <label style={lbl}>Password (8+ characters)</label>
          <input style={inp} type="password" required minLength={8} value={f.password} onChange={e => setF(v => ({ ...v, password: e.target.value }))} placeholder="••••••••" />
          {err && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 12 }}>{err}</div>}
          <button type="submit" disabled={busy} style={{ width: '100%', marginTop: 18, padding: '13px 0', fontSize: 16, fontWeight: 700, background: '#1e3a5f', color: 'white', border: 'none', borderRadius: 10, cursor: 'pointer', opacity: busy ? 0.7 : 1 }}>
            {busy ? 'Creating…' : 'Create company'}
          </button>
        </form>
        <div style={{ fontSize: 12, color: '#94a3b8', textAlign: 'center', marginTop: 16 }}>
          Already have an account? <Link href="/login" style={{ color: '#2563eb' }}>Sign in</Link>
        </div>
      </div>
    </div>
  )
}
