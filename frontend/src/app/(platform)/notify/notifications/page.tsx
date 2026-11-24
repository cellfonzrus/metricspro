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
import {
  groupByScope, standingFor, seenLabel, unreadCount, type InboxItem, type InboxPayload,
} from '@/lib/notification-inbox'

const SEV_COLOR: Record<string, string> = {
  error: 'var(--c-bg-dc2626)', warning: 'var(--c-bg-d97706)', info: 'var(--border)',
}

export default function NotificationsPage() {
  const [payload, setPayload] = useState<InboxPayload | null>(null)
  const [showResolved, setShowResolved] = useState(false)
  const [err, setErr] = useState<string>('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const r = await api(`/api/v1/core/notifications?include_resolved=${showResolved ? 1 : 0}`) as InboxPayload
      setPayload(r); setErr('')
    } catch (e: any) {
      setErr(e?.message || 'Could not load notifications')
    } finally { setBusy(false) }
  }, [showResolved])

  useEffect(() => { load() }, [load])

  const markAllRead = async () => {
    try { await api('/api/v1/core/notifications/read', { method: 'POST', body: JSON.stringify({ all: true }) }) }
    catch (e: any) { setErr(e?.message || 'Could not mark these read') }
    load()
  }

  const unread = unreadCount(payload)
  const items = (payload?.items || []) as InboxItem[]
  const groups = groupByScope(items)

  return (
    <div style={{ padding: 20, maxWidth: 940 }}>
      <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Notifications</h1>
      <p style={{ color: 'var(--muted)', fontSize: 13, margin: '0 0 16px' }}>
        One item per standing problem for this company. When the same problem is found again the item
        is updated instead of a new message being sent, and it closes itself once the check passes.
      </p>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <button onClick={load} disabled={busy}>{busy ? 'Checking…' : 'Refresh'}</button>
        <button onClick={markAllRead} disabled={!unread}>Mark all read</button>
        <label style={{ fontSize: 13, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={showResolved} onChange={e => setShowResolved(e.target.checked)} />
          Include problems that have since been fixed
        </label>
        {/* A ZERO IS NOT AN UNKNOWN: the count only appears once a read has succeeded. */}
        {unread != null && (
          <span style={{ fontSize: 13, color: 'var(--muted)' }}>{unread} unread</span>
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
              background: i.read_at ? 'transparent' : 'var(--surface-2, transparent)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
                <strong style={{ fontSize: 14 }}>
                  {!i.read_at && <span aria-label="unread" style={{ marginRight: 6 }}>•</span>}
                  {i.title}
                </strong>
                <span style={{ fontSize: 12, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                  {i.resolved_at ? 'fixed' : standingFor(i)} · {seenLabel(i)}
                </span>
              </div>
              {i.body && (
                <p style={{ fontSize: 13, color: 'var(--muted)', margin: '6px 0 0', whiteSpace: 'pre-wrap' }}>{i.body}</p>
              )}
              {i.deep_link && !i.resolved_at && (
                <p style={{ margin: '8px 0 0', fontSize: 13 }}>
                  <Link href={i.deep_link}>Open the page that fixes this →</Link>
                </p>
              )}
            </article>
          ))}
        </section>
      ))}
    </div>
  )
}
