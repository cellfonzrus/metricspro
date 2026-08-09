'use client'
// POS ENTRY GATE — owner directive 2026-08-09: "As soon as the user clicks on the POS button they
// should be tasked with completing the pending onboarding tasks."
//
// WHY A LAYOUT AND NOT NINE PAGE EDITS
// A layout under (platform)/pos/ runs for every /pos/* route and NOTHING else. The alternative —
// a check bolted onto each of the nine POS pages — is nine chances to forget it on page ten, which
// is the exact failure mode that put a router-level dependency on the POS backend gate. Zero files
// outside this subtree are touched, so the blast radius is /pos/* and only /pos/*.
//
// IT FAILS OPEN, ON PURPOSE
// This is a guidance gate, not a security gate — every POS route is already gated server-side. If
// the status call errors, or is still in flight, children render unchanged. Making a UX nudge able
// to black-hole a working register whenever /core hiccups would be a strictly worse bug than the
// one it prevents.
//
// IT NAGS, IT DOES NOT TRAP
// "Continue later" is remembered for the browser SESSION only, so the next sign-in prompts again.
// A slim banner stays on every POS page while anything required is outstanding.
import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import Link from 'next/link'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { canAccessPath } from '@/lib/rbac'

const DISMISS_KEY = 'pos_onboarding_dismissed_v1'

type Gate = { complete: boolean; required_total: number; required_done: number; next_task_key: string | null }

export default function PosLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const { permissions } = useAuth()
  const [gate, setGate] = useState<Gate | null>(null)
  const [checked, setChecked] = useState(false)
  const [dismissed, setDismissed] = useState(true)   // assume dismissed until we read storage

  const onWizard = pathname === '/pos/onboarding'

  // The wizard is scoped 'all'/'market' in rbac.ts — a store-scoped cashier is not the person who
  // defines the tenant's departments and tax rates. Redirecting them here anyway would hand them
  // straight to the PLATFORM layout's canAccessPath guard, which bounces them to their home page
  // with no explanation: they would click "Register" and silently land on the dashboard. So the
  // redirect is gated on the caller actually being allowed in, and everyone else just sees the
  // banner (which, for them, says "ask your manager" rather than offering a link).
  const mayConfigure = canAccessPath(permissions, '/pos/onboarding')

  useEffect(() => {
    try { setDismissed(sessionStorage.getItem(DISMISS_KEY) === '1') } catch { setDismissed(false) }
  }, [])

  useEffect(() => {
    let dead = false
    ;(async () => {
      try {
        const g: Gate = await api('/api/v1/core/onboarding/pos/status')
        if (!dead) setGate(g)
      } catch {
        // Fail open — see the header note. A tenant whose status we cannot read is treated as ready.
        if (!dead) setGate(null)
      } finally { if (!dead) setChecked(true) }
    })()
    return () => { dead = true }
  }, [])

  // The redirect. Only once we KNOW setup is incomplete, only when the user has not asked to
  // continue later, and never when they are already looking at the wizard.
  useEffect(() => {
    if (!checked || onWizard || dismissed || !mayConfigure) return
    if (gate && !gate.complete) router.replace('/pos/onboarding')
  }, [checked, onWizard, dismissed, mayConfigure, gate, router])

  const outstanding = gate && !gate.complete
    ? Math.max(0, gate.required_total - gate.required_done) : 0

  return (
    <>
      {outstanding > 0 && !onWizard && (
        <div style={{ background: '#fffbeb', borderBottom: '1px solid #fde68a', padding: '9px 24px',
          display: 'flex', alignItems: 'center', gap: 12, fontSize: 13, color: '#92400e' }}>
          <span style={{ fontWeight: 700 }}>🛠️ POS setup incomplete:</span>
          <span>
            {outstanding} required step{outstanding === 1 ? '' : 's'} left before you can ring a sale
            reliably.{!mayConfigure && ' Ask your manager to finish the POS setup.'}
          </span>
          {mayConfigure && (
            <Link href="/pos/onboarding" style={{ marginLeft: 'auto', fontWeight: 700, color: '#92400e',
              border: '1px solid #fbbf24', borderRadius: 8, padding: '5px 12px',
              textDecoration: 'none', whiteSpace: 'nowrap' }}>
              Finish setup →
            </Link>
          )}
        </div>
      )}

      {/* The interstitial the redirect lands on is the wizard page itself; this branch only shows
          when the user chose "continue later" and is browsing POS with setup outstanding. */}
      {children}

      {onWizard && outstanding > 0 && (
        <div style={{ padding: '0 24px 24px', maxWidth: 1180, margin: '0 auto' }}>
          <button className="btn"
            onClick={() => {
              try { sessionStorage.setItem(DISMISS_KEY, '1') } catch { /* private mode */ }
              setDismissed(true)
              router.push('/pos/sales')
            }}
            style={{ fontSize: 12.5 }}>
            Continue later — take me to the register anyway
          </button>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>
            You will be asked again next time you sign in. Anything you have not set up yet will
            simply be missing from the register.
          </div>
        </div>
      )}
    </>
  )
}
