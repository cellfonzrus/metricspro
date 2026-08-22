'use client'
import { useState, useEffect, useMemo, useRef } from 'react'
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

// Free-trial countdown (mig 908). Banner-only, exactly like SetupBanner above: NOTHING is blocked
// when a trial lapses. Whether a lapsed customer keeps working stays the operator's explicit call
// through the existing tenant on/off switch — an automatic lockout is not something this banner, or
// anything else in this change, decides on their behalf.
//
// Shown to admins only: a sales rep can do nothing about the subscription, so the countdown would be
// noise on their screen. Silent for a tenant with no trial (every pre-907 tenant) because
// `tenant.trial` is null there.
function TrialBanner() {
  const { tenant, user } = useAuth()
  const trial = tenant?.trial
  if (!trial) return null
  if (trial.status !== 'trialing' && trial.status !== 'trial_expired') return null
  if ((user?.role || '').toLowerCase() !== 'admin') return null
  const expired = trial.status === 'trial_expired'
  const days = trial.days_left ?? 0
  // Quiet for most of the trial — it only earns a bar in the last week, or once it has lapsed.
  if (!expired && days > 7) return null
  const tone = expired
    ? { bg: '#fef2f2', border: '#fecaca', text: '#b91c1c' }
    : { bg: '#eff6ff', border: '#bfdbfe', text: '#1d4ed8' }
  return (
    <div style={{ background: tone.bg, borderBottom: `1px solid ${tone.border}`, padding: '10px 24px',
      display: 'flex', alignItems: 'center', gap: 12, fontSize: 13, color: tone.text, flexWrap: 'wrap' }}>
      <span style={{ fontWeight: 700 }}>{expired ? '⏳ Your free trial has ended.' : '⏳ Free trial:'}</span>
      <span>
        {expired
          ? 'Everything still works — talk to us to keep it that way.'
          : `${days} day${days === 1 ? '' : 's'} left on ${tenant?.name || 'your company'}'s trial.`}
      </span>
    </div>
  )
}

// ── Admin "view as employee" banner (owner directive 2026-08-06) ─────────────────────────────────
// Requirement: it must be IMPOSSIBLE to forget you are inside someone else's session. So this bar is
// high-contrast, sticky above everything, names the employee, counts down to the hard server-side
// expiry, and carries a one-click exit. It is driven by `impersonationInfo` — what the SERVER said on
// /core/me — not by anything the browser stored, so it cannot be dismissed by editing localStorage.
function ImpersonationBanner() {
  const { impersonationInfo, impersonation, stopImpersonation, unlockClockPunch } = useAuth()
  const [unlockOpen, setUnlockOpen] = useState(false)
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!impersonationInfo) return
    const t = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(t)
  }, [impersonationInfo])
  if (!impersonationInfo) return null
  const who = impersonationInfo.target_name || impersonationInfo.target_email
    || impersonation?.target_name || 'this employee'
  const expTs = impersonationInfo.expires_at ? Date.parse(impersonationInfo.expires_at) : 0
  const minsLeft = expTs ? Math.max(0, Math.round((expTs - now) / 60000)) : null

  async function submitUnlock() {
    setBusy(true); setErr(''); setOk('')
    try {
      const r = await unlockClockPunch(pw)
      setOk(`Unlocked — good for ONE clock in or clock out in the next ${r.valid_minutes} minutes. Open 🕐 Clock in.`)
      setPw(''); setUnlockOpen(false)
    } catch (e: any) {
      setErr(e?.message || 'That password did not work.')
    } finally { setBusy(false) }
  }

  return (
    <div data-tour-id="impersonation-banner" style={{ position: 'sticky', top: 0, zIndex: 40,
      background: '#7f1d1d', color: '#fff', borderBottom: '3px solid #fca5a5' }}>
      <div style={{ padding: '9px 20px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', fontSize: 13 }}>
        <span style={{ fontWeight: 800, letterSpacing: '0.03em' }}>👁️ VIEWING AS</span>
        <span style={{ fontWeight: 700, background: 'rgba(255,255,255,0.16)', padding: '3px 10px', borderRadius: 999 }}>
          {who}{impersonationInfo.target_role ? ` · ${impersonationInfo.target_role}` : ''}
        </span>
        <span style={{ opacity: 0.85 }}>
          You are seeing exactly what they see. Anything you change is recorded against
          {' '}<b>{impersonationInfo.actor_email || 'your account'}</b>.
        </span>
        {minsLeft !== null && (
          <span style={{ opacity: 0.85 }}>Ends automatically in <b>{minsLeft} min</b>.</span>
        )}
        <button data-tour-id="impersonation-unlock" onClick={() => { setUnlockOpen(o => !o); setErr(''); setOk('') }}
          style={{ marginLeft: 'auto', background: 'rgba(255,255,255,0.12)', color: '#fff', fontWeight: 700,
            border: '1px solid rgba(255,255,255,0.5)', borderRadius: 8, padding: '5px 12px', cursor: 'pointer', fontSize: 13 }}>
          🔓 Unlock clock in/out
        </button>
        <button data-tour-id="impersonation-exit" onClick={() => stopImpersonation()}
          style={{ background: '#fff', color: '#7f1d1d', fontWeight: 800, border: 'none', borderRadius: 8,
            padding: '6px 14px', cursor: 'pointer', fontSize: 13 }}>
          ✕ Exit — back to my account
        </button>
      </div>
      {ok && <div style={{ padding: '0 20px 9px', fontSize: 12.5, color: '#dcfce7' }}>{ok}</div>}
      {unlockOpen && (
        <div style={{ padding: '0 20px 12px' }}>
          <div style={{ background: 'rgba(0,0,0,0.25)', borderRadius: 10, padding: 12, maxWidth: 640 }}>
            <div style={{ fontSize: 12.5, marginBottom: 8, lineHeight: 1.5 }}>
              Clocking in or out is the one thing you cannot do on someone&apos;s behalf. A punch is a
              record that <b>{who}</b> was physically at work, so <b>they</b> must type their own password
              here — once per punch. Nothing else in the app needs this.
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <input value={impersonationInfo.target_email || ''} readOnly aria-label="Employee email"
                style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.4)',
                  background: 'rgba(255,255,255,0.1)', color: '#fff', fontSize: 13, width: 240 }} />
              <input type="password" value={pw} autoFocus placeholder="Their password"
                aria-label="Employee password"
                onChange={e => setPw(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && pw && !busy) submitUnlock() }}
                style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.4)',
                  background: 'rgba(255,255,255,0.1)', color: '#fff', fontSize: 13, width: 200 }} />
              <button disabled={!pw || busy} onClick={submitUnlock}
                style={{ background: '#fff', color: '#7f1d1d', fontWeight: 800, border: 'none', borderRadius: 8,
                  padding: '6px 14px', cursor: pw && !busy ? 'pointer' : 'not-allowed', fontSize: 13, opacity: pw && !busy ? 1 : 0.6 }}>
                {busy ? 'Checking…' : 'Unlock one punch'}
              </button>
            </div>
            {err && <div style={{ marginTop: 8, fontSize: 12.5, color: '#fecaca' }}>{err}</div>}
          </div>
        </div>
      )}
    </div>
  )
}

function PlatformShell({ children, open }: { children: React.ReactNode; open: boolean }) {
  const { period, setPeriod, periods } = usePeriod()
  const { user, permissions, carriers, signOut, tenants, activeOrg, impersonationInfo } = useAuth()
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

  // ── MODULE SEARCH (owner directive 2026-08-10) ─────────────────────────────────────────────────
  // Jump straight to any page in the sidebar without hunting through the accordion. The index is
  // built from `groups` — i.e. AFTER RBAC gating, tenant capability gating, carrier gating and the
  // admin layout override — so search can never surface a page the user could not already see and
  // click. It matches the DISPLAYED label (tenant nicknames from navCfg), not the built-in one.
  const [query, setQuery] = useState('')
  const [hi, setHi] = useState(0)                       // highlighted result (keyboard)
  const searchRef = useRef<HTMLInputElement>(null)

  const index = useMemo(
    () => groups.flatMap(g => g.items.map(it => ({
      href: it.href, icon: it.icon,
      label: labelOf(it.href, it.label),
      group: labelOf('group:' + g.group, g.group),
    }))),
    [groups, navCfg.labels])

  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    const seen = new Set<string>()
    // Rank: label starts-with, then label contains, then group contains. De-duped by href so an item
    // the layout override placed in two groups (`also`) offers ONE destination, not a double row.
    const rank = (r: typeof index[number]) => {
      const l = r.label.toLowerCase()
      return l.startsWith(q) ? 0 : l.includes(q) ? 1 : r.group.toLowerCase().includes(q) ? 2 : 9
    }
    return index.map(r => ({ r, k: rank(r) })).filter(x => x.k < 9)
      .sort((a, b) => a.k - b.k || a.r.label.localeCompare(b.r.label))
      .map(x => x.r).filter(r => (seen.has(r.href) ? false : (seen.add(r.href), true)))
      .slice(0, 12)
  }, [index, query])

  useEffect(() => { setHi(0) }, [query])

  const go = (href: string) => { setQuery(''); router.push(href) }

  // ⌘K / Ctrl-K from anywhere focuses the box (expanding the icon rail first, since the input only
  // renders when the sidebar is open). Ignored while typing in another field so it never steals a key.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault(); setCollapsed(false)
        setTimeout(() => searchRef.current?.focus(), 0)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: 'var(--bg)', flexDirection: 'column' }}>
    <ImpersonationBanner />
    <div style={{ display: 'flex', flex: 1, minHeight: 0, background: 'var(--bg)' }}>
      <aside className="mp-sidebar" style={{ width: collapsed ? 60 : 248, flexShrink: 0,
        display: 'flex', flexDirection: 'column', transition: 'width 0.18s ease', overflow: 'hidden' }}>
        {/* Brand + collapse. The mark keeps the rail identifiable when the wordmark is hidden. */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10,
          padding: collapsed ? '16px 0' : '16px 14px', justifyContent: collapsed ? 'center' : 'flex-start' }}>
          <div style={{ width: 28, height: 28, flexShrink: 0, borderRadius: 8, display: 'flex',
            alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 13, fontWeight: 800,
            letterSpacing: '-0.02em', background: 'linear-gradient(135deg,#3b82f6,#2e75b6)',
            boxShadow: '0 1px 3px rgba(0,0,0,0.35)' }}>M</div>
          {!collapsed && (
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ color: 'rgba(255,255,255,0.95)', fontWeight: 650, fontSize: 14, letterSpacing: '-0.01em' }}>MetricsPro</div>
              <div style={{ color: 'rgba(255,255,255,0.42)', fontSize: 10.5, whiteSpace: 'nowrap',
                overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {tenants.find(t => t.org_id === activeOrg)?.name || tenants[0]?.name || 'Commission Intelligence'}
              </div>
            </div>
          )}
          {!collapsed && (
            <button className="mp-icon-btn" onClick={() => setCollapsed(true)} title="Collapse menu"
              aria-label="Collapse menu">‹</button>
          )}
        </div>

        {/* MODULE SEARCH. In the rail it degrades to a button that expands + focuses (⌘K does the same). */}
        {collapsed ? (
          <button className="mp-icon-btn" title="Search modules (⌘K)" aria-label="Search modules"
            onClick={() => { setCollapsed(false); setTimeout(() => searchRef.current?.focus(), 0) }}
            style={{ margin: '0 auto 6px', fontSize: 14 }}>⌕</button>
        ) : (
          <div style={{ padding: '0 12px 10px', position: 'relative' }}>
            <span aria-hidden style={{ position: 'absolute', left: 22, top: 7, fontSize: 12.5,
              color: 'rgba(255,255,255,0.4)', pointerEvents: 'none' }}>⌕</span>
            <input ref={searchRef} value={query} onChange={e => setQuery(e.target.value)}
              placeholder="Search modules…" aria-label="Search modules"
              onKeyDown={e => {
                if (e.key === 'ArrowDown') { e.preventDefault(); setHi(h => Math.min(h + 1, results.length - 1)) }
                else if (e.key === 'ArrowUp') { e.preventDefault(); setHi(h => Math.max(h - 1, 0)) }
                else if (e.key === 'Enter' && results[hi]) { e.preventDefault(); go(results[hi].href) }
                else if (e.key === 'Escape') { setQuery(''); searchRef.current?.blur() }
              }}
              className="mp-search" style={{ width: '100%', padding: '6px 30px 6px 26px' }} />
            {query
              ? <button className="mp-icon-btn" onClick={() => { setQuery(''); searchRef.current?.focus() }}
                  title="Clear" aria-label="Clear search"
                  style={{ position: 'absolute', right: 15, top: 3 }}>×</button>
              : <span aria-hidden style={{ position: 'absolute', right: 19, top: 7, fontSize: 9.5, fontWeight: 600,
                  letterSpacing: '0.04em', color: 'rgba(255,255,255,0.32)', pointerEvents: 'none' }}>⌘K</span>}
          </div>
        )}

        {!collapsed && !query && (
          <div style={{ padding: '0 12px 12px' }}>
            <label style={{ display: 'block', color: 'rgba(255,255,255,0.38)', fontSize: 9.5, fontWeight: 600,
              textTransform: 'uppercase', letterSpacing: '0.09em', marginBottom: 4 }}>Period</label>
            <select value={period} onChange={e => setPeriod(e.target.value)} className="mp-search"
              style={{ width: '100%', padding: '6px 8px', cursor: 'pointer' }}>
              {periods.map(p => <option key={p} value={p} style={{ color: '#0f172a' }}>{p}</option>)}
            </select>
          </div>
        )}

        <nav className="mp-nav" style={{ flex: 1, overflowY: 'auto', padding: '2px 8px 8px' }}>
          {/* SEARCH RESULTS replace the accordion while a query is active — one flat, ranked list, so
              the user never has to know which group a page lives in. Gated on !collapsed as well as on
              the query: the input is hidden in the icon rail, so a stale query must not leave the user
              staring at two-line result rows in a 60px column with no way to see or clear the box. */}
          {query && !collapsed ? (
            results.length === 0 ? (
              <div style={{ color: 'rgba(255,255,255,0.4)', fontSize: 12, padding: '14px 8px', lineHeight: 1.5 }}>
                No module matches “{query}”.
              </div>
            ) : results.map((r, i) => (
              <button key={r.href} onClick={() => go(r.href)} onMouseEnter={() => setHi(i)}
                className={'mp-nav-item' + (i === hi ? ' is-active' : '')}
                style={{ width: '100%', textAlign: 'left', border: 'none', cursor: 'pointer' }}>
                <span className="mp-nav-icon">{r.icon}</span>
                <span style={{ minWidth: 0, flex: 1 }}>
                  <span style={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.label}</span>
                  <span style={{ display: 'block', fontSize: 10.5, fontWeight: 400, color: 'rgba(255,255,255,0.42)',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.group}</span>
                </span>
              </button>
            ))
          ) : groups.map(({ group, items, subs }) => {
            // In the icon rail (collapsed) every item shows as an icon (no headers); in the full
            // sidebar only the open group's items render.
            const isOpen = collapsed || openGroup === group
            // Sub-categories (tenant layout, roadmap #5). `items` carries EVERY item in the group, so
            // the loose list is whatever no sub claimed — that keeps a group with no subs identical to
            // before, and an item whose sub was deleted still renders instead of vanishing.
            const claimed = new Set((subs || []).flatMap(s => s.items.map(i => i.href)))
            const loose = subs?.length ? items.filter(i => !claimed.has(i.href)) : items
            const renderItem = ({ href, label, icon }: NavItem, inSub = false) => {
              const active = pathname === href || pathname.startsWith(href + '/')
              return (
                <Link key={href} href={href} title={collapsed ? labelOf(href, label) : undefined}
                  className={'mp-nav-item' + (active ? ' is-active' : '')}
                  style={collapsed ? { justifyContent: 'center', padding: '9px 0' }
                                   : (inSub ? { paddingLeft: 30 } : undefined)}>
                  <span className="mp-nav-icon">{icon}</span>
                  {!collapsed && <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap' }}>{labelOf(href, label)}</span>}
                </Link>
              )
            }
            return (
            <div key={group} style={{ marginBottom: collapsed ? 0 : 1 }}>
              {!collapsed && (
                <button onClick={() => setOpenGroup(g => (g === group ? null : group))}
                  className="mp-nav-group" aria-expanded={isOpen}>
                  <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {labelOf('group:' + group, group)}
                  </span>
                  <span aria-hidden style={{ fontSize: 8, opacity: 0.75, display: 'inline-block',
                    transition: 'transform 0.15s ease', transform: isOpen ? 'rotate(90deg)' : 'none' }}>▶</span>
                </button>
              )}
              {isOpen && loose.map(it => renderItem(it))}
              {/* Sub-categories render AFTER the loose items so an unassigned page never hides below a
                  heading. In the icon rail there are no headings at all — only the icons, in order. */}
              {isOpen && !!subs?.length && subs.map(s => (
                <div key={s.name}>
                  {!collapsed && (
                    <div className="mp-nav-sub" style={{ padding: '7px 14px 3px 22px', fontSize: 10,
                      letterSpacing: '.08em', textTransform: 'uppercase', fontWeight: 600,
                      color: 'rgba(255,255,255,0.38)', overflow: 'hidden', textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap' }}>
                      {labelOf('sub:' + group + ':' + s.name, s.name)}
                    </div>
                  )}
                  {s.items.map(it => renderItem(it, true))}
                </div>
              ))}
            </div>
          )})}
        </nav>

        <div style={{ padding: collapsed ? '10px 0' : '10px 14px', borderTop: '1px solid rgba(255,255,255,0.08)',
          display: 'flex', alignItems: 'center', justifyContent: collapsed ? 'center' : 'space-between',
          color: 'rgba(255,255,255,0.34)', fontSize: 10.5 }}>
          {collapsed
            ? <button className="mp-icon-btn" onClick={() => setCollapsed(false)} title="Expand menu"
                aria-label="Expand menu">›</button>
            : <><span>v1.0</span><span>{index.length} pages</span></>}
        </div>
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
                Persist the choice + hard-reload so every page refetches under the new active tenant.
                HIDDEN while viewing as an employee: the acting tenant is pinned by the impersonation
                grant (the backend overrides both org_id and x-active-org from it), so offering a
                switcher there would be a control that visibly does nothing. */}
            {tenants.length > 1 && !impersonationInfo && (
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
        <TrialBanner />
        <SetupBanner />
        <div style={{ flex: 1, padding: 24, minWidth: 0 }}>{children}</div>
      </main>
    </div>
    </div>
  )
}

function Guard({ children }: { children: React.ReactNode }) {
  const { loading, session, user, permissions, provisioned, active, signOut, needsTenantChoice,
          rbacEnabled, sessionInvalid, refresh } = useAuth()
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
  // ZERO-DISCLOSURE (account-linking privacy doctrine): an unprovisioned visitor must learn NOTHING
  // about which companies exist, who administers them, or whether this email is known anywhere else.
  // So: no org name, no admin email, no "we couldn't find you in <tenant>". What it CAN do is tell
  // the person the one action that resolves it — ask whoever invited them — and let them re-check
  // without signing out and back in (the 2026-08-03 incident's second wall).
  if (!provisioned) return <Notice title="Account not set up"
    body="Your sign-in worked, but no role has been assigned to this account yet, so there is nothing to show you."
    hint="Ask the administrator who invited you to assign your role, then use “Check again”. You do not need a new password."
    secondary={{ label: '↻ Check again', onClick: () => { refresh() } }}
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
