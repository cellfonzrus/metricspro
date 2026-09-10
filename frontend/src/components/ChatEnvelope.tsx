'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { api, supabase } from '@/lib/client'
import { registerChatPush } from '@/lib/chat-push'

// ── CHAT ENVELOPE (owner directive 2026-09-10) ───────────────────────────────────────────────────
// "the chat should be able to send notifications to the people and a notification should be shown as
//  an envelope on teh top of the screen so the logged in person knows that there is a message for
//  them or in teh group they are a part of"
//
// NOTHING HERE IS A NEW MECHANISM. Everything this needs already shipped with Chat and was simply
// unreachable from anywhere but /chat:
//   • GET /chat/unread     — the total across the caller's conversations (its own docstring already
//                            said "for the nav badge"); membership-scoped server-side, DMs and GROUPS
//                            alike, muted conversations excluded. No new endpoint, no second count.
//   • GET /chat/me         — the caller's realtime user topic (naming lives server-side only).
//   • chat/realtime.py     — the backend already fans a hint to that per-user topic on every message,
//                            so ONE subscription keeps the badge live app-wide.
//   • chat/push.py         — send_message already pushes to every other member's devices.
// The defect was reach, not capability: all of it only ran while the chat screen was open, which is
// the one moment a person does not need to be told they have a message. This component lives in the
// platform header, so it is mounted on every page.
//
// WHY THE ENVELOPE IS ALWAYS VISIBLE (and only the BADGE comes and goes): the owner asked for an
// envelope "on the top of the screen so the logged in person knows that there is a message". A
// control that only exists while it has something to say cannot be looked at — you can't check for
// messages, you can only be interrupted by them. So the envelope is a fixed landmark and the count is
// the signal. It disappears entirely only for someone who has no chat at all (below).
//
// FAIL-SILENT, AND THAT IS ALSO THE GATE. Chat's real permission is membership, enforced server-side
// (rbac.ts says so at the nav row). /chat/me answers 403 for a login not linked to an employee, and
// /chat/unread only ever counts conversations the caller belongs to. Any error here renders NOTHING —
// a header widget must never be able to break or block a page it merely decorates.
//
// A ZERO IS NOT AN UNKNOWN. Until the first successful read the badge shows nothing at all rather
// than "0" — "no messages" and "we haven't asked yet" are different facts, and the second one must
// not be dressed up as the first (the house's silent-zero rule, applied to a count instead of money).
const SLOW_MS = 30000   // safety sweep while realtime is up
const FAST_MS = 15000   // socket down: poll harder, but this is a badge, not a thread

export default function ChatEnvelope() {
  const pathname = usePathname()
  const [me, setMe] = useState<{ user_topic?: string; push_web?: boolean } | null>(null)
  const [total, setTotal] = useState<number | null>(null)   // null = not read yet, never rendered as 0
  const [dead, setDead] = useState(false)                   // no chat for this caller — render nothing
  const [rtUp, setRtUp] = useState(false)
  const busy = useRef(false)

  const load = useCallback(async () => {
    if (busy.current) return
    busy.current = true
    try {
      const r: any = await api('/api/v1/chat/unread')
      setTotal(Number(r?.total || 0))
    } catch {
      // A failed sweep leaves the LAST known count on screen rather than blanking it: a momentary
      // network blip must not read as "your messages went away".
    } finally {
      busy.current = false
    }
  }, [])

  useEffect(() => {
    let alive = true
    api('/api/v1/chat/me')
      .then((r: any) => { if (alive) { setMe(r); load() } })
      .catch(() => { if (alive) setDead(true) })
    return () => { alive = false }
  }, [load])

  // Push registration, app-wide (it used to happen only on the chat page). Gated on the SERVER's own
  // answer, so no permission prompt is raised that the backend could not honour — see lib/chat-push.
  useEffect(() => { if (me) registerChatPush(!!me.push_web) }, [me])

  // Realtime: the caller's user topic carries a hint for every conversation they belong to — one
  // subscription, so a message in any DM or group lights the badge without waiting for a poll.
  useEffect(() => {
    if (!me?.user_topic) return
    const ch = supabase.channel(`envelope:${me.user_topic}`, { config: { broadcast: { self: false } } })
    ch.on('broadcast', { event: 'chat' }, () => { load() })
      .subscribe((status: string) => setRtUp(status === 'SUBSCRIBED'))
    return () => { setRtUp(false); supabase.removeChannel(ch) }
  }, [me?.user_topic, load])

  // Poll fallback, and a re-read on every navigation: reading a thread clears its unread server-side,
  // so walking away from /chat must drop the badge without waiting out the interval.
  useEffect(() => {
    if (!me) return
    const t = setInterval(load, rtUp ? SLOW_MS : FAST_MS)
    return () => clearInterval(t)
  }, [me, rtUp, load])
  useEffect(() => { if (me) load() }, [pathname, me, load])

  if (dead || !me) return null
  const n = total ?? 0
  const label = total == null ? 'Messages'
    : n === 0 ? 'Messages — nothing unread'
    : `${n} unread message${n === 1 ? '' : 's'}`

  return (
    <Link href="/chat" title={label} aria-label={label}
      style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', textDecoration: 'none',
        border: '1px solid var(--border)', borderRadius: 8, padding: '5px 10px', fontSize: 13, lineHeight: 1 }}>
      <span aria-hidden style={{ fontSize: 15 }}>✉️</span>
      {/* Rendered only for a count we actually READ and that is actually non-zero. */}
      {total != null && n > 0 && (
        <span aria-hidden style={{ position: 'absolute', top: -6, right: -6, minWidth: 17, height: 17,
          padding: '0 4px', borderRadius: 999, background: '#dc2626', color: 'white', fontSize: 10,
          fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {n > 99 ? '99+' : n}
        </span>
      )}
    </Link>
  )
}
