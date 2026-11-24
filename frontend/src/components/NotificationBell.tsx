'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { api } from '@/lib/client'
import { unreadCount, badgeLabel, type InboxPayload } from '@/lib/notification-inbox'

// ── NOTIFICATION BELL (owner directive 2026-11-24, index §CORE-49) ───────────────────────────────
// "Stop sending daily messages about data not uploading instead create [one] for each compay in
//  their notifications inbox and overwrite if it is the same message everyday"
//
// The inbox replaces an EMAIL, so the company has to be able to see it without being mailed — hence
// a landmark in the header, mounted in the platform layout like the chat envelope (§23t) whose shape
// this deliberately mirrors rather than reinventing:
//   • always visible, only the BADGE comes and goes (a control that exists only when it has news
//     cannot be CHECKED, only obeyed);
//   • a ZERO IS NOT AN UNKNOWN — nothing renders until a read has actually succeeded;
//   • a failed poll leaves the last known number up: a blip must not read as "it cleared itself";
//   • FAIL-SILENT, and that is also the gate. The endpoint 403s for a caller who is not entitled to
//     see import health, so a non-admin simply never sees the bell. No RBAC of its own, no second
//     gate to drift from the backend's.
// Every display decision is in lib/notification-inbox, shared with the page, so the badge can never
// disagree with the list it opens.
//
// THE READ IS A SUBSCRIPTION, NOT AN AWAIT INSIDE THE EFFECT. `react-hooks/set-state-in-effect`
// flags a setState reached synchronously from an effect, and it is right to: the state here arrives
// from an external system, so it is set in the promise's own callback. That keeps this component
// OFF `frontend/eslint_pending.txt` — the lint register may only shrink, and a new file joining it
// is new debt (index §CORE-30).
const POLL_MS = 120000   // these findings change hourly at best; a badge is not a thread

/** Only an entitlement answer is permanent; any other failure keeps the last known count. */
function isEntitlementError(e: unknown): boolean {
  const status = (e as { status?: number } | null)?.status
  return status === 401 || status === 403
}

export default function NotificationBell() {
  const pathname = usePathname()
  const [unread, setUnread] = useState<number | null>(null)
  const [dead, setDead] = useState(false)      // not entitled (403) or no inbox — render nothing
  const busy = useRef(false)

  const load = useCallback(() => {
    if (busy.current) return
    busy.current = true
    api('/api/v1/core/notifications')
      .then((r) => { setUnread(unreadCount(r as InboxPayload)) })
      .catch((e: unknown) => { if (isEntitlementError(e)) setDead(true) })
      .finally(() => { busy.current = false })
  }, [])

  useEffect(() => { load() }, [load, pathname])
  useEffect(() => {
    const t = setInterval(load, POLL_MS)
    return () => clearInterval(t)
  }, [load])

  if (dead) return null
  const n = unread ?? 0
  const label = badgeLabel(unread)

  return (
    <Link href="/notify/notifications" title={label} aria-label={label}
      style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', textDecoration: 'none',
        border: '1px solid var(--border)', borderRadius: 8, padding: '5px 10px', fontSize: 13, lineHeight: 1 }}>
      <span aria-hidden style={{ fontSize: 15 }}>🔔</span>
      {unread != null && n > 0 && (
        <span aria-hidden style={{ position: 'absolute', top: -6, right: -6, minWidth: 17, height: 17,
          padding: '0 4px', borderRadius: 999, background: 'var(--c-bg-dc2626)', color: 'var(--c-fg-ffffff)',
          fontSize: 10, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {n > 99 ? '99+' : n}
        </span>
      )}
    </Link>
  )
}
