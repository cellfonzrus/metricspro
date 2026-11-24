'use client'
// ── THE COMPANY NOTIFICATION INBOX (owner directive 2026-11-24, index §CORE-49) ──────────────────
// "Stop sending daily messages about data not uploading instead create [one] for each compay in
//  their notifications inbox and overwrite if it is the same message everyday"
//
// WHAT THIS REPLACES, measured before it was built: three companies received 5-9 emails EVERY DAY
// for eight days on alert scope `connector` — stale feeds, errored sweeps, report files no rule
// matched, offline cameras. Each one was correct, and the ninth told you nothing the first had not.
//
// One row here is one standing FINDING, not one message. It carries what the mail could never show:
// how long the problem has been standing, how many times it has been reported, and the page that
// fixes it. Every display decision comes from lib/notification-inbox, shared with the header bell.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { safeHref } from '@/lib/safe-url'   // every deep_link goes through the house rule, §CORE-33
import {
  groupByScope, standingFor, seenLabel, unreadCount, type InboxItem, type InboxPayload,
} from '@/lib/notification-inbox'

/** An Error's own message, or the given fallback — never `any`. */
function messageOf(e: unknown, fallback: string): string {
  const m = (e as { message?: unknown } | null)?.message
  return typeof m === 'string' && m ? m : fallback
}

const SEV_COLOR: Record<string, string> = {
  error: 'var(--c-bg-dc2626)', warning: 'var(--c-bg-d97706)', info: 'var(--border)',
}

export default function NotificationsPage() {
  const [payload, setPayload] = useState<InboxPayload | null>(null)
  const [showResolved, setShowResolved] = useState(false)
  const [err, setErr] = useState<string>('')
  const [busy, setBusy] = useState(false)

  // THE READ IS A SUBSCRIPTION, NOT AN AWAIT INSIDE THE EFFECT. `react-hooks/set-state-in-effect`
  // flags a setState reached synchronously from an effect; the payload arrives from an external
  // system, so it is set in the promise's own callback. That keeps this page OFF
  // `frontend/eslint_pending.txt`, a register that may only shrink (index §CORE-30).
  const load = useCallback(() => {
    api(`/api/v1/core/notifications?include_resolved=${showResolved ? 1 : 0}`)
      .then((r) => { setPayload(r as InboxPayload); setErr('') })
      .catch((e: unknown) => { setErr(messageOf(e, 'Could not load notifications')) })
      .finally(() => { setBusy(false) })
  }, [showResolved])

  useEffect(() => { load() }, [load])

  const refresh = () => { setBusy(true); load() }

  const markAllRead = () => {
    api('/api/v1/core/notifications/read', { method: 'POST', body: JSON.stringify({ all: true }) })
      .catch((e: unknown) => { setErr(messageOf(e, 'Could not mark these read')) })
      .finally(load)
  }

  const unread = unreadCount(payload)
  const items = (payload?.items || []) as InboxItem[]
  const groups = groupByScope(items)

  return (
    <div style={{ padding: 20, maxWidth: 940 }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Notifications</h1>
      <p style={{ color: 'var(--text3)', fontSize: 13, margin: '0 0 16px' }}>
        One item per standing problem for this company. When the same problem is found again the item
        is updated instead of a new message being sent, and it closes itself once the check passes.
      </p>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <button onClick={refresh} disabled={busy}>{busy ? 'Checking…' : 'Refresh'}</button>
        <button onClick={markAllRead} disabled={!unread}>Mark all read</button>
        <label style={{ fontSize: 13, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={showResolved} onChange={e => setShowResolved(e.target.checked)} />
          Include problems that have since been fixed
        </label>
        {/* A ZERO IS NOT AN UNKNOWN: the count only appears once a read has succeeded. */}
        {unread != null && (
          <span style={{ fontSize: 13, color: 'var(--text3)' }}>{unread} unread</span>
        )}
      </div>

      {err && <div style={{ color: 'var(--c-bg-dc2626)', fontSize: 13, marginBottom: 12 }}>{err}</div>}

      {!err && payload && items.length === 0 && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 18, fontSize: 14 }}>
          Nothing to report. Every data feed and upload this company is watching is up to date.
        </div>
      )}

      {groups.map(g => (
        <section key={g.scope} style={{ marginBottom: 22 }}>
          <h2 style={{ fontSize: 15, margin: '0 0 8px' }}>{g.label}</h2>
          {g.items.map(i => (
            <article key={i.id} style={{
              border: '1px solid var(--border)', borderLeft: `4px solid ${SEV_COLOR[i.severity || 'warning'] || 'var(--border)'}`,
              borderRadius: 8, padding: '10px 12px', marginBottom: 8,
              background: i.read_at ? 'transparent' : 'var(--surface2)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
                <strong style={{ fontSize: 14 }}>
                  {!i.read_at && <span aria-label="unread" style={{ marginRight: 6 }}>•</span>}
                  {i.title}
                </strong>
                <span style={{ fontSize: 12, color: 'var(--text3)', whiteSpace: 'nowrap' }}>
                  {i.resolved_at ? 'fixed' : standingFor(i)} · {seenLabel(i)}
                </span>
              </div>
              {i.body && (
                <p style={{ fontSize: 13, color: 'var(--text3)', margin: '6px 0 0', whiteSpace: 'pre-wrap' }}>{i.body}</p>
              )}
              {/* THE SAME RULE THE SIBLING SURFACE OBEYS. Today every deep_link is a literal written
                  by the backend, so nothing attacker-shaped can reach this href — but AdminAttention
                  wraps the import-health deep_link for exactly this reason (it is config), and the
                  moment `alert_route` or an item gains an editor, an unwrapped href here would be the
                  `javascript:` hole safe-url exists to close. Wrapped now, not later. */}
              {safeHref(i.deep_link, '') && !i.resolved_at && (
                <p style={{ margin: '8px 0 0', fontSize: 13 }}>
                  <Link href={safeHref(i.deep_link, '#')}>Open the page that fixes this →</Link>
                </p>
              )}
            </article>
          ))}
        </section>
      ))}
    </div>
  )
}
