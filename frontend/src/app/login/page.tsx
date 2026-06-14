'use client'
import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { homeFor } from '@/lib/rbac'

export default function LoginPage() {
  const router = useRouter()
  const { session, permissions, loading, provisioned, user } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // Already signed in → bounce to the role's home (or password reset if required).
  useEffect(() => {
    if (loading || !session || !provisioned) return
    if (user?.must_reset_password) router.replace('/account/password')
    else router.replace(homeFor(permissions))
  }, [loading, session, provisioned, permissions, user, router])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(''); setBusy(true)
    const { error } = await supabase.auth.signInWithPassword({ email: email.trim(), password })
    setBusy(false)
    if (error) { setErr(error.message || 'Sign-in failed'); return }
    // onAuthStateChange in AuthProvider loads the profile; the effect above redirects.
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%)', padding: 20 }}>
      <div style={{ width: '100%', maxWidth: 380, background: 'white', borderRadius: 14,
        padding: '34px 30px', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div style={{ fontSize: 22, fontWeight: 800, color: '#1e3a5f' }}>MetricsPro</div>
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 2 }}>Sign in to continue</div>
        </div>
        <form onSubmit={submit}>
          <label style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>Email</label>
          <input type="email" value={email} onChange={e => setEmail(e.target.value)} required autoFocus
            style={inp} placeholder="you@cellfonzrus.com" />
          <label style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginTop: 14, display: 'block' }}>Password</label>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)} required
            style={inp} placeholder="••••••••" />
          {err && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 12 }}>{err}</div>}
          <button type="submit" disabled={busy} style={{
            width: '100%', marginTop: 20, padding: '11px 0', borderRadius: 9, border: 'none',
            background: '#1e3a5f', color: 'white', fontSize: 15, fontWeight: 600,
            cursor: busy ? 'wait' : 'pointer', opacity: busy ? 0.7 : 1 }}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <div style={{ fontSize: 12, color: '#94a3b8', textAlign: 'center', marginTop: 18 }}>
          Trouble signing in? Contact your administrator.
        </div>
      </div>
    </div>
  )
}

const inp: React.CSSProperties = {
  width: '100%', marginTop: 5, padding: '10px 12px', borderRadius: 9,
  border: '1px solid #cbd5e1', fontSize: 14, boxSizing: 'border-box',
}
