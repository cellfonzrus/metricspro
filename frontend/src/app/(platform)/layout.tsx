'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { PeriodProvider, usePeriod } from '@/lib/period-context'
import { useAuth } from '@/lib/auth-context'
import { api, setActiveOrg } from '@/lib/client'
import { NAV, canSeeItem, canAccessPath, carrierOK, safeHomeFor, applyNavLayout, type NavItem, type NavLayout } from '@/lib/rbac'
import HelpPanel from '@/components/HelpPanel'
import AdminAttention from '@/components/AdminAttention'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

function Splash({ text }: { text: string }) {
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      color: 'var(--text3)', fontSize: 14, background: 'var(--bg)' }}>{text}</div>
  )
}

// One full-screen explanatory card. `actionLabel` renames the primary button (the "session expired"
// state says "Sign in again", not "Sign out"); `secondary` adds an optional extra action; `hint`
// adds a smaller line under the body. All optional — existing call sites are unchanged.
function Notice({ title, body, onSignOut, actionLabel, hint, secondary }: {
  title: string; body: string; onSignOut: () => void
  actionLabel?: string; hint?: string
  secondary?: { label: string; onClick: () => void }
}) {
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
      <div className="card" style={{ maxWidth: 440, padding: 32, textAlign: 'center' }}>
        <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>{title}</div>
        <div style={{ fontSize: 14, color: 'var(--text2)', marginBottom: hint ? 10 : 20 }}>{body}</div>
        {hint && <div style={{ fontSize: 12.5, color: 'var(--text3)', marginBottom: 20 }}>{hint}</div>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
          {secondary && <button className="btn" onClick={secondary.onClick}>{secondary.label}</button>}
          <button className="btn" onClick={onSignOut}>{actionLabel || 'Sign out'}</button>
        </div>
      </div>
    </div>
  )
}

// Banner-only onboarding gate (mig 085): prompt a tenant admin to define the pay period / work-week
// when the tenant hasn't been set up. Nothing is blocked — the admin decides when to proceed.
function SetupBanner() {
  const { tenant, user } = useAuth()
  const pathname = usePathname()
  if (!tenant || tenant.setup_complete) return null
  if ((user?.role || '').toLowerCase() !== 'admin') return null
  if (pathname?.startsWith('/admin/tenant-settings')) return null
  return (
    <div style={{ background: '#fffbeb', borderBottom: '1px solid #fde68a', padding: '10px 24px',
      display: 'flex', alignItems: 'center', gap: 12, fontSize: 13, color: '#92400e' }}>
      <span style={{ fontWeight: 700 }}>⚙️ Finish setup:</span>
      <span>Define {tenant.name ? <b>{tenant.name}</b> : 'your company'}&apos;s pay period &amp; work-week so schedules and payroll line up.</span>
      <Link href="/admin/tenant-settings" style={{ marginLeft: 'auto', fontWeight: 700, color: '#92400e',
        border: '1px solid #fbbf24', borderRadius: 8, padding: '5px 12px', textDecoration: 'none', whiteSpace: 'nowrap' }}>
        Set pay period →
      </Link>
    </div>
  )
}

function PlatformShell({ children, open }: { children: React.ReactNode; open: boolean }) {
  const { period, setPeriod, periods } = usePeriod()
  const { user, permissions, carriers, signOut, tenants, activeOrg } = useAuth()
  const [collapsed, setCollapsed] = useState(false)
  const [openGroup, setOpenGroup] = useState<string | null>(null)  // accordion: only one group's items shown at a time
  const [menuOpen, setMenuOpen] = useState(false)
  const [navCfg, setNavCfg] = useState<{ labels?: Record<string, string>; capabilities?: Record<string, boolean | null>; layout?: NavLayout }>({})
  const pathname = usePathname()
  const router = useRouter()

  // Per-tenant display config: nickname labels + capability flags. Best-effort — any failure leaves
  // navCfg empty, so built-in labels show and every item stays visible (today's behavior). Never blocks.
  useEffect(() => {
    let alive = true
    api('/api/v1/commcalc/nav-config').then(c => { if (alive && c) setNavCfg(c) }).catch(() => {})
    return () => { alive = false }
  }, [])
  const caps = navCfg.capabilities || {}
  const labelOf = (key: string, fallback: string) => navCfg.labels?.[key] || fallback
  // Hide an item ONLY when its capability is explicitly false; unknown/null/true → show (default-safe).
  const capOK = (it: NavItem) => !it.cap || caps[it.cap] !== false

  // When login isn't enforced (open), show the full nav (today's behavior); otherwise gate it. Then
  // apply tenant capability gating (e.g. hide Asset Lending when no consignment distributor).
  const filteredGroups = (open ? NAV : NAV.map(g => ({ ...g, items: g.items.filter(it => canSeeItem(permissions, it)) })))
    .map(g => ({ ...g, items: g.items.filter(capOK).filter(it => carrierOK(it.href, carriers, caps)) }))
    .filter(g => g.items.length > 0)
  // Per-org admin layout override (move items between groups / hide) — applied AFTER all access gating,
  // so anything an admin hasn't touched keeps its built-in placement and a newly-enabled item still shows.
  const groups = applyNavLayout(filteredGroups, navCfg.layout)

  // Accordion: keep every module group collapsed and open only the one holding the current page
  // (so the user is never lost) — clicking another header opens that one and closes the rest.
  // Pick the group owning the MOST SPECIFIC (longest-href) matching nav item, not merely the FIRST
  // group with any prefix match. The Commissions "Dashboard" item (/commcalc) is a prefix of every
  // /commcalc/* page, so a first-match would always resolve Asset/Distributor/Targets/Mapping/
  // Integrations sub-pages to the Commissions group (highlighting Dashboard) — the sidebar "jumps back
  // to the dashboard" and the user has to hunt for the module they were in. Longest-prefix resolves to
  // the real sub-module group so navigating between a module's tabs keeps that module expanded.
  let activeGroup: string | null = null
  {
    let bestLen = -1
    for (const g of groups) for (const it of g.items) {
      if ((pathname === it.href || pathname.startsWith(it.href + '/')) && it.href.length > bestLen) {
        activeGroup = g.group; bestLen = it.href.length
      }
    }
  }
  useEffect(() => { if (activeGroup) setOpenGroup(activeGroup) }, [activeGroup])

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
          {groups.map(({ group, items }) => {
            // In the icon rail (collapsed) every item shows as an icon (no headers); in the full
            // sidebar only the open group's items render.
            const isOpen = collapsed || openGroup === group
            return (
            <div key={group}>
              {!collapsed && (
                <button onClick={() => setOpenGroup(g => (g === group ? null : group))}
                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%',
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: 'rgba(255,255,255,0.35)', fontSize: 10, textTransform: 'uppercase',
                    letterSpacing: '0.08em', padding: '12px 16px 4px', fontWeight: 600 }}>
                  <span>{labelOf('group:' + group, group)}</span>
                  <span style={{ fontSize: 9, display: 'inline-block', transition: 'transform 0.15s',
                    transform: isOpen ? 'rotate(90deg)' : 'none' }}>▸</span>
                </button>
              )}
              {isOpen && items.map(({ href, label, icon }) => {
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
          )})}
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
          <div style={{ fontSize: 14, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 16 }}>
            <span><span style={{ color: 'var(--text3)' }}>Period: </span><span style={{ fontWeight: 600, color: 'var(--accent)' }}>{period}</span></span>
            <Link href="/portal" title="Open the employee kiosk to clock in / out"
              style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)', textDecoration: 'none', border: '1px solid var(--border)', borderRadius: 8, padding: '5px 10px' }}>
              🕐 Clock in
            </Link>
            {/* Per-page help "?" panel (mig 715 tech-support) — fail-silent, never breaks the page. */}
            <HelpPanel />
            {/* Admin attention (mig 717, owner directive 2026-07-25): overdue/never-run imports, pending
                mappings and duplicate-data signals. Renders NOTHING for a non-admin or when there is
                nothing to report, pops ONCE per login session, and is fail-silent on any error — so it
                can never block a page or leak an item to a user who may not see it. */}
            <AdminAttention />
            {(permissions?.modules?.admin || permissions?.scope === 'all') && (
              <Link href="/configurations" title="All settings & configuration in one place"
                style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)', textDecoration: 'none', border: '1px solid var(--border)', borderRadius: 8, padding: '5px 10px' }}>
                ⚙️ Settings
              </Link>
            )}
            {/* Multi-tenant login switcher (platform-core-9): only for a login that belongs to >1 tenant.
                Persist the choice + hard-reload so every page refetches under the new active tenant. */}
            {tenants.length > 1 && (
              <select value={activeOrg || ''} title="Switch company"
                onChange={e => { setActiveOrg(e.target.value); window.location.reload() }}
                style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)', background: 'white',
                  border: '1px solid var(--border)', borderRadius: 8, padding: '5px 10px', cursor: 'pointer' }}>
                {tenants.map(t => <option key={t.org_id} value={t.org_id}>🏢 {t.name}</option>)}
              </select>
            )}
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
        <SetupBanner />
        <div style={{ flex: 1, padding: 24, minWidth: 0 }}>{children}</div>
      </main>
    </div>
  )
}

function Guard({ children }: { children: React.ReactNode }) {
  const { loading, session, user, permissions, provisioned, active, signOut, needsTenantChoice,
          rbacEnabled, sessionInvalid } = useAuth()
  const pathname = usePathname()
  const router = useRouter()
  // Master switch: until the admin turns enforcement ON, the app stays fully open (today's
  // behavior) so deploying this never locks anyone out. null = still checking.
  const [enforce, setEnforce] = useState<boolean | null>(null)

  useEffect(() => {
    // Fast path: the ONE-call /api/v1/core/bootstrap (auth-context) already carried rbac_enabled —
    // use it and skip the extra round trip. rbacEnabled === null ⇒ bootstrap didn't run/supply it
    // (older backend, waterfall path, signed-out) → keep the direct auth-config fetch as before so
    // nothing regresses. Explicit /api/v1 path — bare paths 404 silently in the UI.
    if (rbacEnabled !== null) { setEnforce(rbacEnabled); return }
    let on = true
    fetch(`${API_URL}/api/v1/core/auth-config`)
      .then(r => r.json()).then(d => { if (on) setEnforce(!!d.rbac_enabled) })
      .catch(() => { if (on) setEnforce(false) })
    return () => { on = false }
  }, [rbacEnabled])

  useEffect(() => {
    if (enforce !== true || loading) return
    // Dead client session: auth-context has already signed the stale session out, so `session` is
    // about to go null. HOLD here — otherwise this effect fires router.replace('/login') and the
    // user never sees why they were thrown out (the exact experience of the 2026-08-03 incident).
    // The "Sign in again" button below does the navigation deliberately.
    if (sessionInvalid) return
    if (!session) { router.replace('/login'); return }
    // Login belongs to >1 tenant and none chosen yet → the picker lives on /login.
    if (needsTenantChoice) { router.replace('/login'); return }
    if (!provisioned || !active) return
    if (user?.must_reset_password) { router.replace('/account/password'); return }
    if (!canAccessPath(permissions, pathname)) {
      const dest = safeHomeFor(permissions)
      if (dest !== pathname) router.replace(dest)   // guard against redirecting to a gated-off home (loop)
    }
  }, [enforce, loading, session, provisioned, active, user, permissions, pathname, router, needsTenantChoice, sessionInvalid])

  if (enforce === null) return <Splash text="Loading…" />
  if (enforce === false) return <PlatformShell open>{children}</PlatformShell>  // app open

  // enforcement ON ↓
  // The client held a session the backend rejects. Before 2026-08-03 this rendered the full shell
  // with EVERY page showing its own "authentication required" error — one clear card instead.
  if (sessionInvalid) return <Notice title="Session expired"
    body="Your session has expired or is no longer valid — please sign in again."
    hint="You have been signed out on this device. Nothing you were working on was submitted."
    actionLabel="Sign in again"
    onSignOut={() => signOut().then(() => router.replace('/login'))} />
  if (loading) return <Splash text="Loading…" />
  if (!session) return <Splash text="Redirecting to sign-in…" />
  if (needsTenantChoice) return <Splash text="Choose a company…" />
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
