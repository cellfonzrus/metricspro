'use client'

// Register lock (POS Settings > Enable cash register lock) — ported from the standalone
// pos-system app's components/RegisterLock.tsx.
// Renders a 🔒 Lock button for the register header plus a full-screen overlay when locked;
// unlocking re-verifies the signed-in user's password via supabase.auth.signInWithPassword
// (the platform login's email comes from useAuth). Optional auto-lock after N minutes of
// inactivity (pointerdown/keydown reset the timer).

import { useEffect, useRef, useState } from 'react'
import { supabase } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

export default function RegisterLock({ autoLockMinutes }: { autoLockMinutes: number }) {
  const { user } = useAuth()
  const email = user?.email || ''
  const [locked, setLocked] = useState(false)
  const [password, setPassword] = useState('')
  const [unlockError, setUnlockError] = useState<string | null>(null)
  const [unlocking, setUnlocking] = useState(false)
  const lockedRef = useRef(false)
  useEffect(() => { lockedRef.current = locked }, [locked])

  // Inactivity auto-lock — armed only when a positive minute count is configured.
  useEffect(() => {
    if (!(autoLockMinutes > 0)) return
    let timer: ReturnType<typeof setTimeout> | null = null
    const arm = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => { if (!lockedRef.current) setLocked(true) }, autoLockMinutes * 60 * 1000)
    }
    const onActivity = () => { if (!lockedRef.current) arm() }
    window.addEventListener('pointerdown', onActivity)
    window.addEventListener('keydown', onActivity)
    arm()
    return () => {
      if (timer) clearTimeout(timer)
      window.removeEventListener('pointerdown', onActivity)
      window.removeEventListener('keydown', onActivity)
    }
  }, [autoLockMinutes])

  async function unlock() {
    if (unlocking) return
    if (!email) { setUnlockError('No signed-in email found — sign in again from the login page.'); return }
    setUnlocking(true)
    setUnlockError(null)
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    setUnlocking(false)
    if (error) {
      setUnlockError(error.message === 'Invalid login credentials' ? 'Wrong password — try again.' : error.message)
      return
    }
    setPassword('')
    setUnlockError(null)
    setLocked(false)
  }

  return (
    <>
      <button className="btn btn-secondary" onClick={() => setLocked(true)}
        title="Lock the register (unlock requires your password)"
        style={{ fontSize: 12, padding: '5px 10px', whiteSpace: 'nowrap' }}>
        🔒 Lock
      </button>

      {locked && (
        <div style={{ position: 'fixed', inset: 0, background: 'var(--bg)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: '40px 50px', textAlign: 'center', width: 340 }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>🔒</div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>Register locked</div>
            <div style={{ fontSize: 12, color: 'var(--text3)', margin: '6px 0 18px' }}>{email || 'Signed-in user'}</div>
            <input
              type="password" value={password} autoFocus placeholder="Password"
              onChange={e => setPassword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && unlock()}
              style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 7, padding: '9px 12px', fontSize: 14, color: 'var(--text)', outline: 'none', width: '100%', boxSizing: 'border-box', marginBottom: 10, textAlign: 'center' }}
            />
            {unlockError && (
              <div style={{ fontSize: 12, color: 'var(--red)', marginBottom: 10 }}>{unlockError}</div>
            )}
            <button className="btn btn-primary" onClick={unlock} disabled={unlocking}
              style={{ width: '100%', justifyContent: 'center', cursor: unlocking ? 'wait' : 'pointer', opacity: unlocking ? 0.7 : 1 }}>
              {unlocking ? 'Checking…' : 'Unlock'}
            </button>
          </div>
        </div>
      )}
    </>
  )
}
