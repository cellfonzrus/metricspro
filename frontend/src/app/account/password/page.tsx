'use client'
import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { homeFor } from '@/lib/rbac'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export default function PasswordPage() {
  const router = useRouter()
  const { session, loading, user, permissions, token, refresh, signOut } = useAuth()
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
    if (pw.length < 8) { setErr('Use at least 8 characters.'); return }
    if (pw !== pw2) { setErr('Passwords do not match.'); return }
    setBusy(true)
    const { error } = await supabase.auth.updateUser({ password: pw })
    if (error) { setBusy(false); setErr(error.message || 'Could not update password'); return }
    // Clear the must-reset flag (token-verified backend call), then continue.
    try {
      await fetch(`${API_URL}/api/v1/core/me/password-changed`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` },
      })
    } catch { /* non-fatal */ }
    await refresh()
    setBusy(false)
    setMsg('Password updated.')
    setTimeout(() => router.replace(homeFor(permissions)), 700)
  }

  const forced = !!user?.must_reset_password
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
