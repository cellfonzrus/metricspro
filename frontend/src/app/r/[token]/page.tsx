'use client'
// PUBLIC referral redemption — the page the referred customer's phone loads when the in-store QR is
// scanned. NO login: the HMAC capability token in the URL is the only credential (mirrors the HR
// onboarding portal and the notify no-login download). Talks ONLY to the token-guarded public endpoints
// GET/POST /api/v1/referral/redeem/{token}. Any bad/expired/used token returns a UNIFORM "not valid"
// screen — there is no way to tell the failure modes apart, so a scammer can't probe the system. Lives
// outside the (platform) RBAC group on purpose.
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
// The six product 'bubbles', verbatim from the owner directive. The server re-validates against its own
// allowed set (referral_core.ALLOWED_PRODUCTS), so this list is a convenience, not the source of truth.
const PRODUCTS = ['Phone', 'Activations', 'Tablet', 'BYOD', 'Home Internet', 'Accessories']

const wrap: React.CSSProperties = { minHeight: '100vh', background: '#0f172a', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }
const card: React.CSSProperties = { background: '#fff', borderRadius: 16, padding: 24, maxWidth: 460, width: '100%', boxShadow: '0 10px 40px rgba(0,0,0,.3)' }
const inp: React.CSSProperties = { padding: '12px 14px', borderRadius: 10, border: '1px solid #cbd5e1', fontSize: 16, width: '100%', boxSizing: 'border-box' }
const lbl: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: '#334155', display: 'block', margin: '0 0 6px' }
const btnP: React.CSSProperties = { padding: '13px 16px', borderRadius: 10, border: 'none', background: '#2563eb', color: '#fff', fontSize: 16, fontWeight: 700, cursor: 'pointer', width: '100%' }

export default function RedeemPage() {
  const { token } = useParams<{ token: string }>()
  const [state, setState] = useState<'loading' | 'form' | 'invalid' | 'done'>('loading')
  const [allowed, setAllowed] = useState<string[]>(PRODUCTS)
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [products, setProducts] = useState<string[]>([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API_URL}/api/v1/referral/redeem/${token}`)
        if (!res.ok) { setState('invalid'); return }
        const d = await res.json()
        setAllowed(d.allowed_products?.length ? d.allowed_products : PRODUCTS)
        setProducts(d.prefill_products || [])
        setState('form')
      } catch { setState('invalid') }
    })()
  }, [token])

  const toggle = (p: string) => setProducts(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])

  async function submit() {
    setBusy(true); setErr('')
    try {
      const res = await fetch(`${API_URL}/api/v1/referral/redeem/${token}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ customer_name: name, customer_phone: phone, products }),
      })
      if (res.ok) { setState('done'); return }
      const d = await res.json().catch(() => ({}))
      if (res.status === 404) { setState('invalid'); return }
      setErr(d?.detail || 'Something went wrong — please show this screen to the store staff.')
    } catch { setErr('Network error — please try again.') }
    setBusy(false)
  }

  if (state === 'loading') return <div style={wrap}><div style={{ ...card, textAlign: 'center', color: '#64748b' }}>Loading…</div></div>

  if (state === 'invalid') return (
    <div style={wrap}><div style={{ ...card, textAlign: 'center' }}>
      <div style={{ fontSize: 40, marginBottom: 8 }}>🔗</div>
      <h1 style={{ fontSize: 20, fontWeight: 700, color: '#0f172a' }}>This referral link isn't valid</h1>
      <p style={{ color: '#64748b', fontSize: 14, marginTop: 8 }}>
        It may have already been used or expired. Please ask the store to create a new one.
      </p>
    </div></div>
  )

  if (state === 'done') return (
    <div style={wrap}><div style={{ ...card, textAlign: 'center' }}>
      <div style={{ fontSize: 40, marginBottom: 8 }}>🎉</div>
      <h1 style={{ fontSize: 20, fontWeight: 700, color: '#0f172a' }}>Thanks — you're all set!</h1>
      <p style={{ color: '#64748b', fontSize: 14, marginTop: 8 }}>
        Show this screen to the store staff. They'll take it from here.
      </p>
    </div></div>
  )

  return (
    <div style={wrap}><div style={card}>
      <div style={{ fontSize: 34, marginBottom: 4 }}>🎁</div>
      <h1 style={{ fontSize: 22, fontWeight: 800, color: '#0f172a', marginBottom: 4 }}>You've been referred!</h1>
      <p style={{ color: '#64748b', fontSize: 14, marginBottom: 18 }}>
        Fill this in at the counter — it helps us set you up faster (and rewards the friend who sent you).
      </p>

      <div style={{ marginBottom: 14 }}>
        <span style={lbl}>Your name</span>
        <input value={name} onChange={e => setName(e.target.value)} style={inp} placeholder="Full name" />
      </div>
      <div style={{ marginBottom: 14 }}>
        <span style={lbl}>Your phone number *</span>
        <input value={phone} onChange={e => setPhone(e.target.value)} style={inp} inputMode="tel" placeholder="(555) 555-5555" />
      </div>
      <div style={{ marginBottom: 18 }}>
        <span style={lbl}>What are you interested in?</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {allowed.map(p => (
            <button key={p} type="button" onClick={() => toggle(p)}
              style={{ padding: '9px 14px', borderRadius: 999, fontSize: 14, cursor: 'pointer',
                       border: products.includes(p) ? '1px solid #2563eb' : '1px solid #cbd5e1',
                       background: products.includes(p) ? '#2563eb' : '#fff',
                       color: products.includes(p) ? '#fff' : '#334155', fontWeight: 600 }}>
              {products.includes(p) ? '✓ ' : ''}{p}
            </button>
          ))}
        </div>
      </div>

      {err && <div style={{ background: '#fef2f2', color: '#dc2626', padding: 10, borderRadius: 8, fontSize: 13, marginBottom: 12 }}>{err}</div>}
      <button onClick={submit} disabled={busy || !phone.trim()} style={{ ...btnP, opacity: busy || !phone.trim() ? 0.6 : 1 }}>
        {busy ? 'Submitting…' : 'Submit'}
      </button>
    </div></div>
  )
}
