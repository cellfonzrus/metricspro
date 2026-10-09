'use client'
// THREE BANNERS THE PLATFORM LAYOUT SHOWS ABOVE EVERY SCREEN.
//
// 1. OPERATOR ENTRY BANNER — "a platform operator is inside this company right now, and it is you".
//    VISIBILITY IS A SAFETY PROPERTY, not decoration. The impersonation feature has had a banner
//    since mig 730 for exactly this reason; the cross-tenant switcher — which is how a super-admin
//    has ALWAYS been able to act as another tenant — never had one. An operator reading a customer's
//    commission figures with nothing on screen to say so is how a support session quietly becomes an
//    hour of browsing someone else's business.
//
// 2. PLATFORM STATUS NOTICE — "the platform itself has something to tell you" (mig 981).
//
// 3. THIS MONTH'S FOCUS / THE WEEKLY CHECK-IN (index §62, owner ask 2026-10-09) — "in the beginning
//    of the month Market manager or above when they log in should define the focus for the month …
//    the notification will come every week on Monday on the platform". So it arrives IN THE PLATFORM,
//    not by email: there is no cron job, no mailbox and no send record behind it.
//
//    WHY IT IS NOT THE LOGIN POPUP. `/core/attention` already carries the same due list (its
//    `commcalc_month_focus` provider), but that popup is gated to company-wide logins
//    (`rbac.canSeeAttention`) and the owner asked for market manager and above. Rather than widening
//    that gate — which would put import-health items in front of a district manager — this banner
//    renders the SAME list for the market and district tiers. Both surfaces read the same endpoint,
//    which reads `commcalc/month_focus.outstanding`, so they cannot disagree.
//
//    IT CLEARS ITSELF. The list is computed from the declaration + today, never stored, so declaring
//    the month or confirming the week makes the banner disappear on the next poll. Nothing is "marked
//    read": there is no read state to drift.
//
// COST DISCIPLINE. The entry poll runs ONLY for a login the server has already told us is a platform
// super-admin (`user.super_admin` from /core/me), so an ordinary employee's browser never calls an
// endpoint that would 403 them. The notice poll is cheap, fails soft, and renders nothing on error —
// a status banner that breaks the application is worse than no status banner.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api, setActiveOrg } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { canDeclareMonthFocus } from '@/lib/rbac'
import { countdown, type EntryBanner } from '@/lib/operator'

const SEV: Record<string, { bg: string; fg: string; icon: string }> = {
  info: { bg: '#1e3a8a', fg: '#dbeafe', icon: 'ℹ️' },
  maintenance: { bg: '#78350f', fg: '#fef3c7', icon: '🔧' },
  degraded: { bg: '#7c2d12', fg: '#ffedd5', icon: '⚠️' },
  outage: { bg: '#7f1d1d', fg: '#fee2e2', icon: '🚨' },
}

export default function PlatformBanners() {
  return <><OperatorEntryBanner /><PlatformNotices /><MonthFocusBanner /></>
}

type FocusItem = { key: string; severity: string; label: string; detail: string
  deep_link: string; deep_link_label: string }

function MonthFocusBanner() {
  const { permissions } = useAuth()
  const may = canDeclareMonthFocus(permissions)
  const [items, setItems] = useState<FocusItem[]>([])
  const [hidden, setHidden] = useState(false)

  useEffect(() => {
    // Only the people who can ACT on it are asked. A rep or a store manager never calls this.
    if (!may) { setItems([]); return }
    let alive = true
    const period = new Date().toISOString().slice(0, 7)
    // `plays=0` — the banner needs the due list, not the measured signals, so no login pays for a
    // sales read. The plays are on the page.
    const pull = () => api(`/api/v1/commcalc/month-focus/${period}?plays=0`)
      .then((d: any) => { if (alive) setItems(d?.ready ? (d?.outstanding || []) : []) })
      .catch(() => { if (alive) setItems([]) })      // fails soft: a banner must never break a screen
    pull()
    const t = setInterval(pull, 10 * 60_000)
    return () => { alive = false; clearInterval(t) }
  }, [may])

  // ZERO ITEMS ⇒ NOTHING AT ALL, the same rule the attention popup follows. A declared month with its
  // check-in done shows no bar, so the bar itself means something is genuinely outstanding.
  if (!may || hidden || items.length === 0) return null
  const worst = items.some(i => i.severity === 'error') ? 'error' : 'warning'
  const bg = worst === 'error' ? '#7f1d1d' : '#78350f'
  const fg = worst === 'error' ? '#fee2e2' : '#fef3c7'

  return (
    <div style={{ background: bg, color: fg, padding: '8px 16px', display: 'flex', alignItems: 'center',
      gap: 10, flexWrap: 'wrap', fontSize: 13 }}>
      <span aria-hidden>🧭</span>
      <b>This month&apos;s focus</b>
      <span style={{ opacity: 0.95 }}>
        {items.map(i => i.label).join(' · ')}
      </span>
      <span style={{ flex: 1 }} />
      <Link href="/commcalc/month-focus" style={{ color: fg, textDecoration: 'underline' }}>
        {items[0].deep_link_label}
      </Link>
      {/* Dismissible for the session only — it is a reminder, not an outage, and it comes back on the
          next load until the thing is actually done. */}
      <button onClick={() => setHidden(true)} aria-label="Dismiss"
        style={{ background: 'transparent', color: fg, border: 'none', cursor: 'pointer',
          fontSize: 15, opacity: 0.75 }}>×</button>
    </div>
  )
}

function OperatorEntryBanner() {
  const { user, impersonationInfo } = useAuth()
  const [entry, setEntry] = useState<EntryBanner | null>(null)
  const [left, setLeft] = useState(0)

  useEffect(() => {
    // Only a platform super-admin can have an entry session, and an IMPERSONATED session can never
    // have one (the acting org is pinned by the impersonation grant, and that banner takes priority).
    if (!user?.super_admin || impersonationInfo) { setEntry(null); return }
    let alive = true
    const pull = () => api('/api/v1/core/operator/entry')
      .then((d: any) => { if (alive) { setEntry(d?.entry || null); setLeft(d?.entry?.seconds_remaining || 0) } })
      .catch(() => { if (alive) setEntry(null) })
    pull()
    const t = setInterval(pull, 60_000)
    return () => { alive = false; clearInterval(t) }
  }, [user?.super_admin, impersonationInfo])

  // Local countdown so the remaining time is honest between polls. When it reaches zero the session
  // has expired server-side too (the expiry is a hard wall-clock stamp, not a client timer), so the
  // banner removes itself rather than showing a stale "0s".
  useEffect(() => {
    if (!entry) return
    const t = setInterval(() => setLeft(s => Math.max(0, s - 1)), 1000)
    return () => clearInterval(t)
  }, [entry])

  if (!entry || left <= 0) return null

  async function leave() {
    try { await api('/api/v1/core/operator/exit', { method: 'POST', body: JSON.stringify({}) }) } catch { /* the banner must always let you out */ }
    setActiveOrg(null)
    window.location.href = '/operator/tenants'
  }

  return (
    <div style={{ background: '#78350f', color: '#fef3c7', padding: '8px 16px', display: 'flex',
      alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 13,
      borderBottom: '1px solid rgba(0,0,0,0.25)' }}>
      <span aria-hidden>🛰️</span>
      <span>
        You are in <b>{entry.tenant_name || 'this company'}</b> as a <b>platform operator</b>
        {entry.reason ? <> — {entry.reason}</> : null}.
      </span>
      <span style={{ opacity: 0.85 }}>Acting as <b>{entry.actor_email}</b>. This is not “view as employee”.</span>
      <span style={{ flex: 1 }} />
      <span style={{ opacity: 0.9, fontVariantNumeric: 'tabular-nums' }}>ends in {countdown(left)}</span>
      <button onClick={leave} style={{ background: '#fef3c7', color: '#78350f', border: 'none',
        borderRadius: 7, padding: '5px 12px', fontSize: 12.5, fontWeight: 700, cursor: 'pointer' }}>
        Leave company
      </button>
    </div>
  )
}

function PlatformNotices() {
  const { user } = useAuth()
  const [notices, setNotices] = useState<any[]>([])
  const [hidden, setHidden] = useState<Record<string, boolean>>({})

  useEffect(() => {
    if (!user) return
    let alive = true
    const pull = () => api('/api/v1/core/platform-notice')
      .then((d: any) => { if (alive) setNotices(d?.notices || []) })
      .catch(() => { if (alive) setNotices([]) })   // fail soft: no banner beats a broken app
    pull()
    const t = setInterval(pull, 5 * 60_000)
    return () => { alive = false; clearInterval(t) }
  }, [user?.org_id])

  const live = notices.filter(n => !hidden[n.id])
  if (live.length === 0) return null

  return (
    <>
      {live.map(n => {
        const s = SEV[n.severity] || SEV.info
        return (
          <div key={n.id} style={{ background: s.bg, color: s.fg, padding: '8px 16px', display: 'flex',
            alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 13 }}>
            <span aria-hidden>{s.icon}</span>
            <b>{n.title}</b>
            {n.body && <span style={{ opacity: 0.9 }}>{n.body}</span>}
            <span style={{ flex: 1 }} />
            {/* An OUTAGE cannot be dismissed — it is the one message a tenant must not click past. */}
            {n.severity !== 'outage' && (
              <button onClick={() => setHidden(h => ({ ...h, [n.id]: true }))} aria-label="Dismiss"
                style={{ background: 'transparent', color: s.fg, border: 'none', cursor: 'pointer',
                  fontSize: 15, opacity: 0.75 }}>×</button>
            )}
          </div>
        )
      })}
    </>
  )
}
