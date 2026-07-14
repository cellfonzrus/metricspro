'use client'
import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { safeHomeFor } from '@/lib/rbac'

export default function LoginPage() {
  const router = useRouter()
  const { session, permissions, loading, provisioned, active, user, signOut,
          tenants, needsTenantChoice, switchTenant } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [picking, setPicking] = useState('')

  // Already signed in → bounce to the role's home (or password reset if required).
  useEffect(() => {
    if (loading || !session || !provisioned || !active) return
    if (user?.must_reset_password) router.replace('/account/password')
    else router.replace(safeHomeFor(permissions))
  }, [loading, session, provisioned, active, permissions, user, router])

  // Credentials accepted but the login belongs to MORE THAN ONE tenant and none is chosen yet →
  // show a tenant picker (platform-core-9). Picking one loads that tenant's profile, and the effect
  // above then redirects to its home. `picking` keeps the picker mounted through the switch so the
  // "no access" screen never flashes between needsTenantChoice clearing and the profile loading.
  // Single-tenant logins never reach this branch.
  if (!loading && session && (needsTenantChoice || picking)) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%)', padding: 20 }}>
        <div style={{ width: '100%', maxWidth: 400, background: 'white', borderRadius: 14, padding: '30px',
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
          <div style={{ textAlign: 'center', marginBottom: 6, fontSize: 18, fontWeight: 800, color: '#1e3a5f' }}>
            Choose a company
          </div>
          <div style={{ textAlign: 'center', fontSize: 13, color: '#64748b', marginBottom: 18 }}>
            Your login works for {tenants.length} companies. Pick the one to work in — you can switch anytime from the top bar.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {tenants.map(t => (
              <button key={t.org_id} disabled={!!picking}
                onClick={async () => { setPicking(t.org_id); await switchTenant(t.org_id) }}
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10,
                  width: '100%', textAlign: 'left', padding: '12px 14px', borderRadius: 10,
                  border: '1px solid #cbd5e1', background: picking === t.org_id ? '#eef2ff' : 'white',
                  cursor: picking ? 'wait' : 'pointer' }}>
                <span>
                  <span style={{ display: 'block', fontSize: 14, fontWeight: 700, color: '#1e293b' }}>{t.name}</span>
                  <span style={{ display: 'block', fontSize: 12, color: '#64748b' }}>
                    {t.role_display || t.role || 'Member'}{t.is_default ? ' · default' : ''}
                  </span>
                </span>
                <span style={{ color: '#94a3b8', fontSize: 16 }}>{picking === t.org_id ? '…' : '→'}</span>
              </button>
            ))}
          </div>
          <button onClick={() => signOut()} style={{ marginTop: 18, width: '100%', background: 'none',
            border: 'none', color: '#64748b', fontSize: 12, cursor: 'pointer' }}>
            Sign out
          </button>
        </div>
      </div>
    )
  }

  // Signed in but no app account / disabled → explain instead of a blank stuck page.
  const stuck = !loading && session && !needsTenantChoice && (!provisioned || !active)
  if (stuck) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%)', padding: 20 }}>
        <div style={{ width: '100%', maxWidth: 380, background: 'white', borderRadius: 14, padding: '30px',
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)', textAlign: 'center' }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: '#1e3a5f' }}>
            {provisioned ? 'Access disabled' : 'No access yet'}
          </div>
          <div style={{ fontSize: 13, color: '#64748b', margin: '8px 0 18px' }}>
            You're signed in, but {provisioned ? 'your access has been turned off' : 'no role has been assigned to this account'}.
            Please contact your administrator.
          </div>
          <button onClick={() => signOut()} className="btn">Sign out</button>
        </div>
      </div>
    )
  }

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
