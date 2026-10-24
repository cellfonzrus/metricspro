'use client'
// THE ONE HOME for "what a send result looks like on screen" (backend one home: notify/send_outcome,
// index §NOTIFY-01).
//
// The reported defect (owner 2026-10-24): the Send box said "Sent: 6 ok, 10 failed." and stopped, so
// the only way to learn WHO missed the report was to read the database. Every surface that sends
// something renders THIS, so a failure is named the same way wherever it is shown — and the words are
// the backend's, never re-worded here: a second spelling of a remedy is a future divergence.
//
// The failure list is a DATA TABLE, so it sorts from its own headers through the house hook rather
// than joining the unsorted-table register: a send to thirty recipients is read by grouping the same
// reason together, and a new unsorted table is exactly the debt that lock refuses.
import { useTableSort, SortableTh } from '@/components/SortableTh'

export type SendOutcome = {
  headline: string
  ok_count: number
  failed_count: number
  attempts?: { sent: number; failed: number }
  note?: string
  fixes?: string[]
  ok?: { channel: string; target: string }[]
  failed?: {
    channel: string; target: string; attempts?: number; failed_attempts?: number
    partial?: boolean; code?: string; reason: string; fix?: string; recognised?: boolean
  }[]
}

const CHANNEL_LABEL: Record<string, string> = { email: 'Email', whatsapp: 'WhatsApp' }
function channelLabel(c: string) { return CHANNEL_LABEL[c] || c }

// STABLE (module scope), per useTableSort's contract — a fresh closure re-runs the memo every render.
function failedCell(f: NonNullable<SendOutcome['failed']>[number], field: string) {
  if (field === 'channel') return channelLabel(f.channel)
  if (field === 'reason') return f.reason
  if (field === 'fix') return f.fix || ''
  return f.target
}

/** The sentence alone — for a caller with no room for the list. Never re-worded in the browser. */
export function outcomeHeadline(o: SendOutcome | null | undefined): string {
  return (o?.headline || '').trim()
}

export function SendOutcomeView({ outcome }: { outcome: SendOutcome | null | undefined }) {
  // The hook runs before the early return, never after it.
  const failed = outcome?.failed || []
  const { sorted, sort, toggle } = useTableSort(failed, failedCell)
  if (!outcome) return null
  const green = failed.length === 0 && outcome.ok_count > 0
  return (
    <div style={{ fontSize: 12, marginTop: 6 }}>
      <div style={{ color: green ? 'green' : failed.length ? '#b45309' : '#64748b', fontWeight: 600 }}>
        {green ? '✓ ' : failed.length ? '⚠ ' : ''}{outcome.headline}
      </div>
      {/* People, not attachments. The attempt count is shown only when it differs, so a reader who
          remembers "10 failed" can see where that number came from. */}
      {outcome.attempts && outcome.attempts.failed > failed.length && (
        <div style={{ color: '#64748b', marginTop: 2 }}>
          ({outcome.attempts.failed} delivery attempts failed — each recipient is counted once below;
          a report sent as two files is two attempts.)
        </div>
      )}
      {failed.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 6 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: '#64748b' }}>
              <SortableTh field="target" sort={sort} onSort={toggle} style={cell}>Did not arrive</SortableTh>
              <SortableTh field="channel" sort={sort} onSort={toggle} style={cell}>Channel</SortableTh>
              <SortableTh field="reason" sort={sort} onSort={toggle} style={cell}>Why</SortableTh>
              <SortableTh field="fix" sort={sort} onSort={toggle} style={cell}>How to fix it</SortableTh>
            </tr>
          </thead>
          <tbody>
            {sorted.map((f, i) => (
              <tr key={i}>
                <td style={cell}>{f.target}{f.partial ? ' (part of it did)' : ''}</td>
                <td style={cell}>{channelLabel(f.channel)}</td>
                <td style={cell}>{f.reason}</td>
                {/* An unrecognised failure shows no remedy rather than a guessed one — the backend
                    allowlist decides, and a blank here is that honesty, not a missing field. */}
                <td style={cell}>{f.fix || (f.recognised ? '' : 'Not a failure this app has a ruling on yet — send this reason to support.')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {outcome.note && !failed.length && outcome.ok_count === 0 && (
        <div style={{ color: '#64748b', marginTop: 2 }}>{outcome.note}</div>
      )}
    </div>
  )
}

const cell: React.CSSProperties = {
  padding: '3px 6px', borderBottom: '1px solid var(--border,#eee)', verticalAlign: 'top',
}
