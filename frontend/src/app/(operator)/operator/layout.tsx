'use client'
// THE PLATFORM OPERATOR CONSOLE SHELL — the "separate view for the super admin" (owner 2026-09-05).
//
// WHY A ROUTE GROUP AND NOT A PAGE UNDER /admin. `(operator)` is a sibling of `(platform)`, so this
// shell replaces the tenant application's chrome entirely rather than nesting inside it: no tenant
// sidebar, no tenant switcher, no period picker, no Ask bar, no company branding. That is the
// difference between "the operator tools are a menu inside CellfonzRUs" and "the operator has a
// console of their own", which is what the directive asked for.
//
// NAVIGATION IS NOT THE SEPARATION, THOUGH. The real separation is the AUTHORIZATION model behind
// it: `core.platform_operator` is an identity keyed by auth id with NO org, and every endpoint this
// console calls resolves authority from it (unioned with the legacy flag until the owner performs
// the cutover). This shell is what that model looks like; `operator.py` is what it IS.
//
// FAIL-CLOSED AND SELF-EXPLAINING. The shell asks the SERVER who it is talking to
// (`GET /core/operator/me`). A non-operator gets a plain explanation, never a half-rendered console
// — and never a redirect loop, because the tenant app remains fully reachable at /commcalc.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import { setActiveOrg } from '@/lib/client'
import { loadOperatorMe, OPS, type OperatorMe } from '@/lib/operator'
import { OperatorContext } from '@/lib/operator-context'

export default function OperatorLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, signOut } = useAuth()
  const [me, setMe] = useState<OperatorMe | null>(null)
  const [err, setErr] = useState('')
  const [ready, setReady] = useState(false)
  const path = usePathname()
  const router = useRouter()

  useEffect(() => {
    if (loading) return
    loadOperatorMe().then(m => { setMe(m); setReady(true) })
      .catch(e => { setErr(e?.message || 'Not a platform operator'); setReady(true) })
  }, [loading, user?.email])

  if (loading || !ready) return <Splash text="Opening the operator console…" />

  if (!me) {
    // Not an operator (or the API refused). Say so plainly and offer the way back — a console that
    // dead-ends a tenant admin is a support ticket.
    return (
      <Splash>
        <div style={{ maxWidth: 460, textAlign: 'center' }}>
          <div style={{ fontSize: 34, marginBottom: 12 }}>🛰️</div>
          <h1 style={{ fontSize: 19, margin: '0 0 10px', color: OPS.text }}>Platform Operator Console</h1>
          <p style={{ color: OPS.text2, fontSize: 13.5, lineHeight: 1.65, margin: '0 0 20px' }}>
            This console is for people who operate the MetricsPro platform itself — not for
            administering a company inside it. {err ? <><br /><span style={{ color: OPS.text3 }}>{err}</span></> : null}
          </p>
          <Link href="/commcalc" style={{ ...btn, textDecoration: 'none' }}>← Back to the app</Link>
        </div>
      </Splash>
    )
  }

  const onlyLegacy = me.sources.length === 1 && me.sources[0] === 'legacy'

  return (
    <div style={{ minHeight: '100vh', background: OPS.bg, color: OPS.text, display: 'flex',
      flexDirection: 'column', fontSize: 14 }}>
      {/* A permanent, unmissable identity strip. An operator should never be in doubt about which
          hat they are wearing — the whole point of separating the personas. */}
      <div style={{ background: OPS.accentSoft, borderBottom: `1px solid ${OPS.border}`,
        padding: '7px 20px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <strong style={{ color: OPS.accent, fontSize: 11.5, letterSpacing: '0.09em' }}>PLATFORM OPERATOR</strong>
        <span style={{ color: OPS.text2, fontSize: 12.5 }}>
          {me.email}{me.operator_role ? ` · ${me.operator_role}` : ''}
        </span>
        <span style={{ flex: 1 }} />
        {/* THE SEPARATION READOUT. While this says "tenant super-admin flag", the operator's power
            is still a column on their employment record. It is the honest status of the migration,
            shown rather than hidden. */}
        <span title={onlyLegacy
          ? 'Your platform access still comes from the super_admin flag on your tenant membership.'
          : 'Your platform access comes from your own platform-operator record.'}
          style={{ fontSize: 11.5, padding: '2px 9px', borderRadius: 999,
            border: `1px solid ${onlyLegacy ? OPS.warn : OPS.good}`,
            color: onlyLegacy ? OPS.warn : OPS.good }}>
          {onlyLegacy ? 'authority: tenant super-admin flag' : 'authority: platform operator record'}
        </span>
      </div>

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <aside style={{ width: 230, flexShrink: 0, background: OPS.panel,
          borderRight: `1px solid ${OPS.border}`, display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '18px 16px 12px', display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 28, height: 28, borderRadius: 8, background: OPS.accent, color: '#1c1917',
              display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 800 }}>M</div>
            <div style={{ lineHeight: 1.2 }}>
              <div style={{ fontWeight: 700, fontSize: 13.5 }}>MetricsPro</div>
              <div style={{ color: OPS.text3, fontSize: 11 }}>Operator Console</div>
            </div>
          </div>
          <nav style={{ padding: '6px 8px', display: 'flex', flexDirection: 'column', gap: 2 }}>
            {/* Sections come from the SERVER, filtered by the caller's capabilities
                (operator.console_sections) — so a `billing` operator is never shown a Companies link
                they would be 403'd on. Nav is convenience; the endpoints gate authoritatively. */}
            {me.sections.map(s => {
              const active = s.href === '/operator' ? path === '/operator' : path.startsWith(s.href)
              const external = !s.href.startsWith('/operator')
              return (
                <Link key={s.href} href={s.href} title={s.description}
                  style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '8px 10px',
                    borderRadius: 8, textDecoration: 'none', fontSize: 13.2,
                    color: active ? '#fff' : OPS.text2,
                    background: active ? OPS.panelSoft : 'transparent' }}>
                  <span aria-hidden>{s.icon}</span>
                  <span style={{ flex: 1 }}>{s.label}</span>
                  {external && <span aria-hidden style={{ color: OPS.text3, fontSize: 11 }}>↗</span>}
                </Link>
              )
            })}
          </nav>
          <div style={{ flex: 1 }} />
          <div style={{ padding: 12, borderTop: `1px solid ${OPS.border}`, display: 'flex',
            flexDirection: 'column', gap: 8 }}>
            {/* The way BACK to the tenant application. Leaving the console clears any acting tenant
                the operator had chosen, so returning never drops them into someone else's company
                by accident. */}
            <button onClick={() => { setActiveOrg(null); router.push('/commcalc') }} style={btnGhost}>
              ← Tenant application
            </button>
            <button onClick={() => signOut()} style={btnGhost}>Sign out</button>
          </div>
        </aside>

        <main style={{ flex: 1, minWidth: 0, overflow: 'auto', padding: '22px 26px 60px' }}>
          <OperatorContext.Provider value={me}>{children}</OperatorContext.Provider>
        </main>
      </div>
    </div>
  )
}

function Splash({ text, children }: { text?: string; children?: React.ReactNode }) {
  return (
    <div style={{ minHeight: '100vh', background: OPS.bg, color: OPS.text2, display: 'flex',
      alignItems: 'center', justifyContent: 'center', padding: 24, fontSize: 14 }}>
      {children || text}
    </div>
  )
}

const btn: React.CSSProperties = {
  display: 'inline-block', padding: '9px 16px', borderRadius: 9, fontSize: 13, fontWeight: 600,
  background: OPS.accent, color: '#1c1917', border: 'none', cursor: 'pointer',
}
const btnGhost: React.CSSProperties = {
  padding: '7px 10px', borderRadius: 8, fontSize: 12.5, textAlign: 'left',
  background: 'transparent', color: OPS.text2, border: `1px solid ${OPS.border}`, cursor: 'pointer',
}
