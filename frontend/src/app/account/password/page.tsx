'use client'
import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { homeFor } from '@/lib/rbac'

// Client-side echo of the server password policy (server is authoritative). Returns [] when OK.
function policyErrors(pw: string, p: any): string[] {
  const e: string[] = []
  if (pw.length > 128) return ['Password must be at most 128 characters.']
  const pol = p || { min_length: 8, max_length: 12, require_upper: true, require_lower: true, require_digit: true, require_special: true }
  if (pw.length < pol.min_length) e.push(`Use at least ${pol.min_length} characters.`)
  if (pw.length > pol.max_length) e.push(`Use at most ${pol.max_length} characters.`)
  if (pol.require_upper && !/[A-Z]/.test(pw)) e.push('Include an uppercase letter.')
  if (pol.require_lower && !/[a-z]/.test(pw)) e.push('Include a lowercase letter.')
  if (pol.require_digit && !/[0-9]/.test(pw)) e.push('Include a number.')
  if (pol.require_special && !/[!@#$%^&*()\-_=+[\]{};:,.?/]/.test(pw)) e.push('Include a special character (e.g. !@#$%).')
  return e
}

export default function PasswordPage() {
  const router = useRouter()
  const { session, loading, user, permissions, refresh, signOut, passwordPolicy } = useAuth()
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!loading && !session) router.replace('/login')
  }, [loading, session, router])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(''); setMsg('')
    const perr = policyErrors(pw, passwordPolicy)
    if (perr.length) { setErr(perr.join(' ')); return }
    if (pw !== pw2) { setErr('Passwords do not match.'); return }
    setBusy(true)
    // Route the set THROUGH the backend so the tenant password policy can't be bypassed client-side
    // (the old screen called supabase.auth.updateUser directly). The backend validates → sets via the
    // admin API → clears must_reset.
    try {
      await api('/api/v1/core/me/set-password', { method: 'POST', body: JSON.stringify({ new_password: pw }) })
    } catch (e: any) { setBusy(false); setErr(e?.message || 'Could not update password'); return }
    await refresh()
    setBusy(false)
    setMsg('Password updated.')
    setTimeout(() => router.replace(homeFor(permissions)), 700)
  }

  const forced = !!user?.must_reset_password
  const liveErrs = pw ? policyErrors(pw, passwordPolicy) : []
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%)', padding: 20 }}>
      <div style={{ width: '100%', maxWidth: 400, background: 'white', borderRadius: 14,
        padding: '32px 30px', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
        <div style={{ fontSize: 19, fontWeight: 800, color: '#1e3a5f' }}>
          {forced ? 'Set your password' : 'Change password'}
        </div>
        <div style={{ fontSize: 13, color: '#64748b', margin: '4px 0 18px' }}>
          {forced ? 'Choose a new password to finish setting up your account.' : `Signed in as ${user?.email || ''}`}
        </div>
        <form onSubmit={submit}>
          <label style={lab}>New password</label>
          <input type="password" value={pw} onChange={e => setPw(e.target.value)} required autoFocus style={inp} />
          {liveErrs.length > 0 && (
            <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12, color: '#b45309' }}>
              {liveErrs.map((x, i) => <li key={i}>{x}</li>)}
            </ul>
          )}
          <label style={{ ...lab, marginTop: 14 }}>Confirm new password</label>
          <input type="password" value={pw2} onChange={e => setPw2(e.target.value)} required style={inp} />
          {err && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 12 }}>{err}</div>}
          {msg && <div style={{ color: '#059669', fontSize: 13, marginTop: 12 }}>{msg}</div>}
          <button type="submit" disabled={busy} style={{
            width: '100%', marginTop: 18, padding: '11px 0', borderRadius: 9, border: 'none',
            background: '#1e3a5f', color: 'white', fontSize: 15, fontWeight: 600,
            cursor: busy ? 'wait' : 'pointer', opacity: busy ? 0.7 : 1 }}>
            {busy ? 'Saving…' : 'Save password'}
          </button>
        </form>
        <button onClick={() => signOut().then(() => router.replace('/login'))}
          style={{ marginTop: 14, background: 'none', border: 'none', color: '#64748b', fontSize: 13, cursor: 'pointer' }}>
          Sign out
        </button>
      </div>
    </div>
  )
}

const lab: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: '#475569', display: 'block' }
const inp: React.CSSProperties = {
  width: '100%', marginTop: 5, padding: '10px 12px', borderRadius: 9,
  border: '1px solid #cbd5e1', fontSize: 14, boxSizing: 'border-box',
}
