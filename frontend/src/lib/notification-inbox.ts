// ── THE COMPANY NOTIFICATION INBOX — the view model (owner directive 2026-11-24, index §CORE-49) ──
// "Stop sending daily messages about data not uploading instead create [one] for each compay in their
//  notifications inbox and overwrite if it is the same message everyday"
//
// The page and the header badge both read GET /api/v1/core/notifications. Every DISPLAY decision
// lives here, as pure functions over the payload, so the badge can never disagree with the list it
// opens — the same rule the chat envelope had to be fixed to obey (index §23t).
//
// A ZERO IS NOT AN UNKNOWN (the house silent-zero rule, §23e). `unread` is `number | null`: null
// means nothing has been read yet and the badge renders NOTHING, rather than a confident 0.

export type InboxItem = {
  id: string
  scope: string
  inbox_key: string
  title: string
  body?: string | null
  severity?: string | null
  deep_link?: string | null
  affected?: string[] | null
  occurrences?: number | null
  first_seen_at?: string | null
  last_seen_at?: string | null
  read_at?: string | null
  resolved_at?: string | null
}

export type InboxPayload = {
  org_id?: string
  items?: InboxItem[]
  unread?: number
  open?: number
  read_ok?: boolean
}

export const SEVERITY_RANK: Record<string, number> = { error: 0, warning: 1, info: 2 }

/** Worst first, then the finding that has been standing LONGEST — the one most likely forgotten. */
export function sortItems(items: InboxItem[]): InboxItem[] {
  return [...(items || [])].sort((a, b) => {
    const ra = SEVERITY_RANK[(a.severity || 'warning')] ?? 1
    const rb = SEVERITY_RANK[(b.severity || 'warning')] ?? 1
    if (ra !== rb) return ra - rb
    return String(a.first_seen_at || '').localeCompare(String(b.first_seen_at || ''))
  })
}

/** The badge's number, or null when no successful read has happened yet. */
export function unreadCount(p: InboxPayload | null | undefined): number | null {
  if (!p || p.read_ok !== true) return null
  return Number(p.unread || 0)
}

export function badgeLabel(unread: number | null): string {
  if (unread == null) return 'Notifications'
  if (unread === 0) return 'Notifications — nothing new'
  return `${unread} new notification${unread === 1 ? '' : 's'}`
}

/**
 * How long this finding has been standing, from its FIRST sighting — the number the daily email
 * could never show, because every day's mail looked like a brand-new problem.
 */
export function standingFor(item: InboxItem, now: Date = new Date()): string {
  const first = item.first_seen_at ? new Date(item.first_seen_at) : null
  if (!first || Number.isNaN(first.getTime())) return 'just now'
  const days = Math.floor((now.getTime() - first.getTime()) / 86400000)
  if (days <= 0) return 'today'
  if (days === 1) return 'since yesterday'
  return `for ${days} days`
}

/** "Reported 9 times" is the honest replacement for nine identical emails. */
export function seenLabel(item: InboxItem): string {
  const n = Number(item.occurrences || 1)
  return n <= 1 ? 'reported once' : `reported ${n} times`
}

/**
 * The scope's heading. DERIVED FROM THE SCOPE VALUE, with the raw value shown when the platform has
 * no words for it: a scope is data (RULE TWO), so an unknown one must still be legible rather than
 * swallowed or mislabelled as something it is not.
 */
const SCOPE_WORDS: Record<string, string> = {
  connector: 'Data not arriving',
  upload_due: 'Files waiting to be uploaded',
}
export function scopeLabel(scope: string): string {
  return SCOPE_WORDS[scope] || scope
}

export function groupByScope(items: InboxItem[]): { scope: string; label: string; items: InboxItem[] }[] {
  const by = new Map<string, InboxItem[]>()
  for (const i of sortItems(items)) {
    const k = i.scope || 'other'
    if (!by.has(k)) by.set(k, [])
    by.get(k)!.push(i)
  }
  return [...by.entries()].map(([scope, rows]) => ({ scope, label: scopeLabel(scope), items: rows }))
}
