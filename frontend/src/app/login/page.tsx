'use client'
import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { supabase, api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { safeHomeFor } from '@/lib/rbac'

export default function LoginPage() {
  const router = useRouter()
  const { session, permissions, loading, provisioned, active, user, signOut,
          tenants, needsTenantChoice, switchTenant,
          pendingConnections, connectTenant, disableAndSwitch, dismissPending,
          needs2fa, twofa, startTwoFactor, verifyTwoFactor } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [picking, setPicking] = useState('')
  // Pending account-link invite panel (platform-core-11):
  const [code, setCode] = useState('')
  const [panelBusy, setPanelBusy] = useState(false)
  const [panelErr, setPanelErr] = useState('')
  const [disabledInfo, setDisabledInfo] = useState<any>(null)
  // 2FA OTP screen (auth-hardening):
  const [otp, setOtp] = useState('')
  const [remember, setRemember] = useState(false)
  const [otpBusy, setOtpBusy] = useState(false)
  const [otpMsg, setOtpMsg] = useState('')
  const [otpErr, setOtpErr] = useState('')
  const [otpSent, setOtpSent] = useState(false)
  // Forgot-password flow (auth-hardening): 'signin' → 'forgot' (request) → 'reset' (code + new pw).
  const [mode, setMode] = useState<'signin' | 'forgot' | 'reset'>('signin')
  const [fpEmail, setFpEmail] = useState('')
  const [fpCode, setFpCode] = useState('')
  const [fpPw, setFpPw] = useState('')
  const [fpBusy, setFpBusy] = useState(false)
  const [fpMsg, setFpMsg] = useState('')
  const [fpErr, setFpErr] = useState('')

  // Auto-send the first 2FA code once the OTP screen appears.
  useEffect(() => {
    if (needs2fa && !otpSent && session) {
      setOtpSent(true)
      startTwoFactor().then((r: any) => setOtpMsg(r?.message || 'A code was sent.'))
        .catch((e: any) => setOtpErr(e?.message || 'Could not send a code'))
    }
  }, [needs2fa, otpSent, session, startTwoFactor])

  // Already signed in → bounce to the role's home (or password reset if required). Pause the redirect
  // while a pending account-link invite OR an unmet 2FA challenge is unresolved (handled by the panels).
  useEffect(() => {
    if (loading || !session || !provisioned || !active) return
    if (pendingConnections.length) return
    if (needs2fa) return
    if (user?.must_reset_password) router.replace('/account/password')
    else router.replace(safeHomeFor(permissions))
  }, [loading, session, provisioned, active, permissions, user, router, pendingConnections, needs2fa])

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

  // Signed in, password OK, but the tenant/user requires 2FA and this session isn't verified yet →
  // show the OTP screen. A code is auto-sent on mount; the user can resend or switch channel.
  if (!loading && session && provisioned && active && !needsTenantChoice && !pendingConnections.length && needs2fa) {
    return (
      <Shell>
        <div style={{ textAlign: 'center', fontSize: 18, fontWeight: 800, color: '#1e3a5f', marginBottom: 6 }}>
          Verify it's you
        </div>
        <div style={{ fontSize: 13, color: '#334155', margin: '4px 0 14px' }}>
          Enter the one-time code we sent to your {(twofa.user_channels || ['email'])[0] === 'whatsapp' ? 'WhatsApp' : 'email'}.
        </div>
        <label style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>Verification code</label>
        <input value={otp} onChange={e => setOtp(e.target.value.replace(/\D/g, ''))} autoFocus inputMode="numeric"
          maxLength={6} style={{ ...inp, letterSpacing: 4, fontFamily: 'monospace' }} placeholder="6-digit code" />
        <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: '#475569', marginTop: 12 }}>
          <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} />
          Remember this device for 30 days
        </label>
        {otpMsg && !otpErr && <div style={{ color: '#059669', fontSize: 12, marginTop: 10 }}>{otpMsg}</div>}
        {otpErr && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 10 }}>{otpErr}</div>}
        <button disabled={otpBusy || otp.length < 4} style={{ ...primaryBtn, opacity: (otpBusy || otp.length < 4) ? 0.6 : 1 }}
          onClick={async () => {
            setOtpErr(''); setOtpBusy(true)
            try { await verifyTwoFactor(otp.trim(), remember) }
            catch (e: any) { setOtpErr(e?.message || 'Invalid or expired code.') }
            finally { setOtpBusy(false) }
          }}>
          {otpBusy ? 'Verifying…' : 'Verify'}
        </button>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12 }}>
          <button onClick={async () => {
            setOtpErr(''); setOtpMsg('Sending…')
            try { const r = await startTwoFactor(); setOtpMsg(r?.message || 'A new code was sent.') }
            catch (e: any) { setOtpErr(e?.message || 'Could not send a code'); setOtpMsg('') }
          }} style={linkBtn}>Resend code</button>
          <button onClick={async () => {
            setOtpErr(''); setOtpMsg('Sending…')
            try { const r = await startTwoFactor('email'); setOtpMsg(r?.message || 'Code sent by email.') }
            catch (e: any) { setOtpErr(e?.message || 'Could not send a code'); setOtpMsg('') }
          }} style={linkBtn}>Use email instead</button>
        </div>
        <button onClick={() => signOut()} style={{ marginTop: 16, width: '100%', background: 'none',
          border: 'none', color: '#64748b', fontSize: 12, cursor: 'pointer' }}>Sign out</button>
      </Shell>
    )
  }

  // Signed in and there's a pending invitation to CONNECT another company (platform-core-11). Shows
  // ONLY the inviting tenant's name (zero cross-tenant disclosure). The user can connect it onto this
  // login, take a separate login instead, or defer. Single-tenant users without an invite never see this.
  const invite = (!loading && session && !needsTenantChoice && pendingConnections[0]) || null
  if (invite) {
    if (disabledInfo) {
      return (
        <Shell>
          <div style={{ textAlign: 'center', fontSize: 18, fontWeight: 800, color: '#1e3a5f', marginBottom: 6 }}>
            Your new login is ready
          </div>
          <div style={{ fontSize: 13, color: '#334155', margin: '4px 0 14px' }}>
            Sign in with the login and access code below for <strong>{invite.tenant_name}</strong>.
          </div>
          <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 10, padding: 14, fontFamily: 'monospace', fontSize: 13 }}>
            <div>Login: <strong>{disabledInfo.new_login_email}</strong></div>
            <div>Access code: <strong>{disabledInfo.access_code || disabledInfo.temp_password}</strong></div>
          </div>
          <div style={{ fontSize: 12, color: '#92400e', background: '#fffbeb', border: '1px solid #fde68a',
            borderRadius: 8, padding: 10, marginTop: 12 }}>
            {disabledInfo.policy}
          </div>
          <button onClick={() => signOut()} style={primaryBtn}>Sign out & sign in with the new login</button>
        </Shell>
      )
    }
    return (
      <Shell>
        <div style={{ textAlign: 'center', fontSize: 18, fontWeight: 800, color: '#1e3a5f', marginBottom: 6 }}>
          Connect a company
        </div>
        <div style={{ fontSize: 13, color: '#334155', margin: '4px 0 14px' }}>
          <strong>{invite.tenant_name}</strong> has invited this email to access MetricsPro. Enter the
          access code your administrator gave you to connect it to your current login — you'll switch
          between companies from the top bar.
        </div>
        <label style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>Access code</label>
        <input value={code} onChange={e => setCode(e.target.value)} autoFocus style={inp} placeholder="Paste the access code" />
        {panelErr && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 10 }}>{panelErr}</div>}
        <button disabled={panelBusy || !code.trim()} style={{ ...primaryBtn, opacity: (panelBusy || !code.trim()) ? 0.6 : 1 }}
          onClick={async () => {
            setPanelErr(''); setPanelBusy(true)
            try { await connectTenant(invite.org_id, code.trim()); setCode('') }
            catch (e: any) { setPanelErr(e?.message || 'Could not connect') }
            finally { setPanelBusy(false) }
          }}>
          {panelBusy ? 'Connecting…' : `Connect ${invite.tenant_name}`}
        </button>
        <details style={{ marginTop: 14 }}>
          <summary style={{ fontSize: 12, color: '#64748b', cursor: 'pointer' }}>
            This isn't the right account — use a separate login instead
          </summary>
          <div style={{ fontSize: 12, color: '#64748b', margin: '8px 0' }}>
            Disable your current login and get a brand-new, separate login for {invite.tenant_name}.
            A disabled login can only be restored by a MetricsPro super-admin.
          </div>
          <button disabled={panelBusy || !code.trim()} style={{ ...secondaryBtn, opacity: (panelBusy || !code.trim()) ? 0.6 : 1 }}
            onClick={async () => {
              if (!confirm('Disable your current login and start fresh for this company? Only a super-admin can restore it.')) return
              setPanelErr(''); setPanelBusy(true)
              try { setDisabledInfo(await disableAndSwitch(invite.org_id, code.trim())) }
              catch (e: any) { setPanelErr(e?.message || 'Could not switch logins') }
              finally { setPanelBusy(false) }
            }}>
            Disable old login & start fresh
          </button>
        </details>
        <button onClick={() => dismissPending(invite.org_id)} style={{ marginTop: 16, width: '100%', background: 'none',
          border: 'none', color: '#64748b', fontSize: 12, cursor: 'pointer' }}>
          Not now — continue to my current company
        </button>
      </Shell>
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
    const em = email.trim().toLowerCase()
    // Soft lockout (mig 859) — defense-in-depth. Sign-in itself goes straight to Supabase; this just
    // refuses locally after repeated failures instead of hammering it, and records every attempt so
    // failed logins are visible. Both calls FAIL OPEN: a precheck/record fault never blocks a real login.
    try {
      const pc: any = await api('/api/v1/core/auth/login-precheck', { method: 'POST', body: JSON.stringify({ email: em }) })
      if (pc?.locked) {
        setBusy(false)
        const mins = Math.max(1, Math.ceil((pc.retry_after || 900) / 60))
        setErr(`Too many failed attempts. Try again in about ${mins} minute${mins === 1 ? '' : 's'}, or reset your password.`)
        return
      }
    } catch { /* fail-open */ }
    const { error } = await supabase.auth.signInWithPassword({ email: email.trim(), password })
    setBusy(false)
    // Fire-and-forget: record pass/fail for the ledger + lockout counter.
    void api('/api/v1/core/auth/login-record', { method: 'POST', body: JSON.stringify({ email: em, success: !error }) }).catch(() => {})
    if (error) { setErr(error.message || 'Sign-in failed'); return }
    // onAuthStateChange in AuthProvider loads the profile; the effect above redirects.
  }

  // Forgot-password (public): request a code, then set a new password with it. Anti-enumeration — the
  // request step ALWAYS reports the same generic message whether or not the account exists.
  if (!session && (mode === 'forgot' || mode === 'reset')) {
    return (
      <Shell>
        <div style={{ textAlign: 'center', fontSize: 18, fontWeight: 800, color: '#1e3a5f', marginBottom: 6 }}>
          Reset your password
        </div>
        {mode === 'forgot' ? (
          <>
            <div style={{ fontSize: 13, color: '#334155', margin: '4px 0 14px' }}>
              Enter your email and we'll send a one-time code.
            </div>
            <label style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>Email</label>
            <input type="email" value={fpEmail} onChange={e => setFpEmail(e.target.value)} autoFocus style={inp} placeholder="you@company.com" />
            {fpMsg && <div style={{ color: '#059669', fontSize: 13, marginTop: 10 }}>{fpMsg}</div>}
            {fpErr && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 10 }}>{fpErr}</div>}
            <button disabled={fpBusy || !fpEmail.trim()} style={{ ...primaryBtn, opacity: (fpBusy || !fpEmail.trim()) ? 0.6 : 1 }}
              onClick={async () => {
                setFpErr(''); setFpBusy(true)
                try {
                  const r = await api('/api/v1/core/auth/forgot-password', { method: 'POST', body: JSON.stringify({ email: fpEmail.trim() }) })
                  setFpMsg(r?.message || 'If this email has an account, a code has been sent.')
                  setMode('reset')
                } catch (e: any) { setFpErr(e?.message || 'Something went wrong') }
                finally { setFpBusy(false) }
              }}>{fpBusy ? 'Sending…' : 'Send reset code'}</button>
          </>
        ) : (
          <>
            <div style={{ fontSize: 13, color: '#334155', margin: '4px 0 14px' }}>
              Enter the code we sent and choose a new password.
            </div>
            <label style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>Email</label>
            <input type="email" value={fpEmail} onChange={e => setFpEmail(e.target.value)} style={inp} placeholder="you@company.com" />
            <label style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginTop: 12, display: 'block' }}>Code</label>
            <input value={fpCode} onChange={e => setFpCode(e.target.value.replace(/\D/g, ''))} inputMode="numeric" maxLength={6}
              style={{ ...inp, letterSpacing: 4, fontFamily: 'monospace' }} placeholder="6-digit code" />
            <label style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginTop: 12, display: 'block' }}>New password</label>
            <input type="password" value={fpPw} onChange={e => setFpPw(e.target.value)} style={inp} placeholder="New password" />
            {fpMsg && <div style={{ color: '#059669', fontSize: 13, marginTop: 10 }}>{fpMsg}</div>}
            {fpErr && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 10 }}>{fpErr}</div>}
            <button disabled={fpBusy || !fpCode.trim() || !fpPw} style={{ ...primaryBtn, opacity: (fpBusy || !fpCode.trim() || !fpPw) ? 0.6 : 1 }}
              onClick={async () => {
                setFpErr(''); setFpBusy(true)
                try {
                  const r = await api('/api/v1/core/auth/reset-password', { method: 'POST',
                    body: JSON.stringify({ email: fpEmail.trim(), code: fpCode.trim(), new_password: fpPw }) })
                  setFpMsg(r?.message || 'Your password has been updated.')
                  setTimeout(() => { setMode('signin'); setFpCode(''); setFpPw(''); setFpMsg('') }, 1200)
                } catch (e: any) { setFpErr(e?.message || 'Invalid or expired code.') }
                finally { setFpBusy(false) }
              }}>{fpBusy ? 'Updating…' : 'Set new password'}</button>
          </>
        )}
        <button onClick={() => { setMode('signin'); setFpErr(''); setFpMsg('') }}
          style={{ marginTop: 16, width: '100%', background: 'none', border: 'none', color: '#64748b', fontSize: 12, cursor: 'pointer' }}>
          Back to sign in
        </button>
      </Shell>
    )
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
        <div style={{ textAlign: 'center', marginTop: 14 }}>
          <button onClick={() => { setMode('forgot'); setFpEmail(email); setErr('') }} style={linkBtn}>
            Forgot password?
          </button>
        </div>
        <div style={{ fontSize: 12, color: '#94a3b8', textAlign: 'center', marginTop: 10 }}>
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

const primaryBtn: React.CSSProperties = {
  width: '100%', marginTop: 16, padding: '11px 0', borderRadius: 9, border: 'none',
  background: '#1e3a5f', color: 'white', fontSize: 15, fontWeight: 600, cursor: 'pointer',
}
const secondaryBtn: React.CSSProperties = {
  width: '100%', padding: '9px 0', borderRadius: 9, border: '1px solid #cbd5e1',
  background: 'white', color: '#1e3a5f', fontSize: 14, fontWeight: 600, cursor: 'pointer',
}
const linkBtn: React.CSSProperties = {
  background: 'none', border: 'none', color: '#1e3a5f', fontSize: 12, fontWeight: 600, cursor: 'pointer',
}

// The centered white card used by every login-page state.
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%)', padding: 20 }}>
      <div style={{ width: '100%', maxWidth: 420, background: 'white', borderRadius: 14, padding: '30px',
        boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
        {children}
      </div>
    </div>
  )
}
