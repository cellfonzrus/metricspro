'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { PeriodProvider, usePeriod } from '@/lib/period-context'
import { useAuth } from '@/lib/auth-context'
import { api } from '@/lib/client'
import { NAV, canSeeItem, canAccessPath, safeHomeFor, type NavItem } from '@/lib/rbac'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

function Splash({ text }: { text: string }) {
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      color: 'var(--text3)', fontSize: 14, background: 'var(--bg)' }}>{text}</div>
  )
}

function Notice({ title, body, onSignOut }: { title: string; body: string; onSignOut: () => void }) {
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
      <div className="card" style={{ maxWidth: 440, padding: 32, textAlign: 'center' }}>
        <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>{title}</div>
        <div style={{ fontSize: 14, color: 'var(--text2)', marginBottom: 20 }}>{body}</div>
        <button className="btn" onClick={onSignOut}>Sign out</button>
      </div>
    </div>
  )
}

function PlatformShell({ children, open }: { children: React.ReactNode; open: boolean }) {
  const { period, setPeriod, periods } = usePeriod()
  const { user, permissions, signOut } = useAuth()
  const [collapsed, setCollapsed] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [navCfg, setNavCfg] = useState<{ labels?: Record<string, string>; capabilities?: Record<string, boolean | null> }>({})
  const pathname = usePathname()
  const router = useRouter()

  // Per-tenant display config: nickname labels + capability flags. Best-effort — any failure leaves
  // navCfg empty, so built-in labels show and every item stays visible (today's behavior). Never blocks.
  useEffect(() => {
    let alive = true
    api('/commcalc/nav-config').then(c => { if (alive && c) setNavCfg(c) }).catch(() => {})
    return () => { alive = false }
  }, [])
  const caps = navCfg.capabilities || {}
  const labelOf = (key: string, fallback: string) => navCfg.labels?.[key] || fallback
  // Hide an item ONLY when its capability is explicitly false; unknown/null/true → show (default-safe).
  const capOK = (it: NavItem) => !it.cap || caps[it.cap] !== false

  // When login isn't enforced (open), show the full nav (today's behavior); otherwise gate it. Then
  // apply tenant capability gating (e.g. hide Asset Lending when no consignment distributor).
  const groups = (open ? NAV : NAV.map(g => ({ ...g, items: g.items.filter(it => canSeeItem(permissions, it)) })))
    .map(g => ({ ...g, items: g.items.filter(capOK) }))
    .filter(g => g.items.length > 0)

  const initials = (user?.full_name || user?.email || '?').split(/[\s@.]+/).filter(Boolean)
    .slice(0, 2).map(s => s[0]?.toUpperCase()).join('') || 'U'

  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: 'var(--bg)' }}>
      <aside style={{ width: collapsed ? 56 : 220, background: 'var(--accent)', flexShrink: 0,
        display: 'flex', flexDirection: 'column', transition: 'width 0.2s', overflow: 'hidden' }}>
        <div style={{ padding: '20px 16px 12px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
          {!collapsed && (
            <div>
              <div style={{ color: 'white', fontWeight: 700, fontSize: 16 }}>MetricsPro</div>
              <div style={{ color: 'rgba(255,255,255,0.5)', fontSize: 11 }}>Commission Intelligence</div>
            </div>
          )}
          <button onClick={() => setCollapsed(!collapsed)} style={{ background: 'none', border: 'none',
            color: 'rgba(255,255,255,0.6)', cursor: 'pointer', fontSize: 18,
            padding: collapsed ? '4px 0' : '8px 0 0', display: 'block' }}>
            {collapsed ? '→' : '←'}
          </button>
        </div>

        {!collapsed && (
          <div style={{ padding: '12px 16px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
            <label style={{ color: 'rgba(255,255,255,0.5)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Period</label>
            <select value={period} onChange={e => setPeriod(e.target.value)}
              style={{ width: '100%', background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.2)',
                borderRadius: 6, color: 'white', padding: '5px 8px', fontSize: 13, marginTop: 4 }}>
              {periods.map(p => <option key={p} value={p} style={{ color: 'black' }}>{p}</option>)}
            </select>
          </div>
        )}

        <nav style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
          {groups.map(({ group, items }) => (
            <div key={group}>
              {!collapsed && (
                <div style={{ color: 'rgba(255,255,255,0.35)', fontSize: 10, textTransform: 'uppercase',
                  letterSpacing: '0.08em', padding: '12px 16px 4px', fontWeight: 600 }}>{labelOf('group:' + group, group)}</div>
              )}
              {items.map(({ href, label, icon }) => {
                const active = pathname === href || pathname.startsWith(href + '/')
                return (
                  <Link key={href} href={href} style={{ display: 'flex', alignItems: 'center', gap: 10,
                    padding: collapsed ? '10px 0' : '8px 16px', justifyContent: collapsed ? 'center' : 'flex-start',
                    color: active ? 'white' : 'rgba(255,255,255,0.6)',
                    background: active ? 'rgba(255,255,255,0.12)' : 'transparent',
                    textDecoration: 'none', fontSize: 13, fontWeight: active ? 600 : 400,
                    borderLeft: active ? '3px solid rgba(255,255,255,0.6)' : '3px solid transparent', transition: 'all 0.1s' }}>
                    <span style={{ fontSize: 15 }}>{icon}</span>
                    {!collapsed && labelOf(href, label)}
                  </Link>
                )
              })}
            </div>
          ))}
        </nav>

        {!collapsed && (
          <div style={{ padding: '12px 16px', borderTop: '1px solid rgba(255,255,255,0.1)',
            color: 'rgba(255,255,255,0.4)', fontSize: 11 }}>
            Cellular Services · v1.0
          </div>
        )}
      </aside>

      <main style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <header style={{ background: 'white', borderBottom: '1px solid var(--border)', padding: '0 24px',
          height: 56, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          position: 'sticky', top: 0, zIndex: 10 }}>
          <div style={{ fontSize: 14, color: 'var(--text2)' }}>
            <span style={{ color: 'var(--text3)' }}>Period: </span>
            <span style={{ fontWeight: 600, color: 'var(--accent)' }}>{period}</span>
          </div>
          {open ? (
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>🔓 Login not enforced</span>
          ) : (
          <div style={{ position: 'relative' }}>
            <button onClick={() => setMenuOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 10,
              background: 'none', border: 'none', cursor: 'pointer' }}>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text1)' }}>{user?.full_name || user?.email}</div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{user?.role_display || user?.role || ''}</div>
              </div>
              <div style={{ width: 32, height: 32, background: 'var(--accent)', borderRadius: '50%',
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'white', fontSize: 12, fontWeight: 700 }}>
                {initials}
              </div>
            </button>
            {menuOpen && (
              <div style={{ position: 'absolute', right: 0, top: 44, background: 'white', border: '1px solid var(--border)',
                borderRadius: 10, boxShadow: '0 10px 30px rgba(0,0,0,0.12)', minWidth: 180, zIndex: 20, overflow: 'hidden' }}>
                <Link href="/account/password" onClick={() => setMenuOpen(false)}
                  style={{ display: 'block', padding: '10px 14px', fontSize: 13, color: 'var(--text1)', textDecoration: 'none' }}>
                  🔑 Change password
                </Link>
                <button onClick={() => signOut().then(() => router.replace('/login'))}
                  style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 14px', fontSize: 13,
                    color: '#dc2626', background: 'none', border: 'none', borderTop: '1px solid var(--border)', cursor: 'pointer' }}>
                  ↩︎ Sign out
                </button>
              </div>
            )}
          </div>
          )}
        </header>
        <div style={{ flex: 1, padding: 24, minWidth: 0 }}>{children}</div>
      </main>
    </div>
  )
}

function Guard({ children }: { children: React.ReactNode }) {
  const { loading, session, user, permissions, provisioned, active, signOut } = useAuth()
  const pathname = usePathname()
  const router = useRouter()
  // Master switch: until the admin turns enforcement ON, the app stays fully open (today's
  // behavior) so deploying this never locks anyone out. null = still checking.
  const [enforce, setEnforce] = useState<boolean | null>(null)

  useEffect(() => {
    let on = true
    fetch(`${API_URL}/api/v1/core/auth-config`)
      .then(r => r.json()).then(d => { if (on) setEnforce(!!d.rbac_enabled) })
      .catch(() => { if (on) setEnforce(false) })
    return () => { on = false }
  }, [])

  useEffect(() => {
    if (enforce !== true || loading) return
    if (!session) { router.replace('/login'); return }
    if (!provisioned || !active) return
    if (user?.must_reset_password) { router.replace('/account/password'); return }
    if (!canAccessPath(permissions, pathname)) {
      const dest = safeHomeFor(permissions)
      if (dest !== pathname) router.replace(dest)   // guard against redirecting to a gated-off home (loop)
    }
  }, [enforce, loading, session, provisioned, active, user, permissions, pathname, router])

  if (enforce === null) return <Splash text="Loading…" />
  if (enforce === false) return <PlatformShell open>{children}</PlatformShell>  // app open

  // enforcement ON ↓
  if (loading) return <Splash text="Loading…" />
  if (!session) return <Splash text="Redirecting to sign-in…" />
  if (!provisioned) return <Notice title="Account not set up"
    body="Your login exists but no role has been assigned yet. Please contact your administrator."
    onSignOut={() => signOut().then(() => router.replace('/login'))} />
  if (!active) return <Notice title="Access disabled"
    body="Your access has been turned off. Please contact your administrator."
    onSignOut={() => signOut().then(() => router.replace('/login'))} />
  if (user?.must_reset_password) return <Splash text="Redirecting…" />
  if (!canAccessPath(permissions, pathname)) return <Splash text="Redirecting…" />
  return <PlatformShell open={false}>{children}</PlatformShell>
}

export default function PlatformLayout({ children }: { children: React.ReactNode }) {
  return (
    <PeriodProvider>
      <Guard>{children}</Guard>
    </PeriodProvider>
  )
}
