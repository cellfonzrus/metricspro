'use client'
import { useState, useEffect, useMemo, useCallback } from 'react'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import { SortableTh, useTableSort } from '@/components/SortableTh'

// FOLLOW UP WITH MANAGERS (owner 2026-10-03, index §47.14), verbatim: "then alert the management via
// a whats app message for all followup items with the managers - this will be a seprate module -
// Follow Up with Managers , all pending jobs assigned to the managers will be followed up via this
// module".
//
// THIS SCREEN SPELLS NO QUEUE NAME OF ITS OWN. The queue vocabulary — key, label and the page that
// owns each queue — comes from the SERVER (`labels` / `sources` <- manager_followup.sources(), which
// dereferences commcalc/compliance_summary.CATEGORIES, the registry the Flags & Compliance dashboard
// already counts off). So a queue can be renamed or added in the pure module without touching the UI,
// and this page can never disagree with the dashboard about what a pending job is. Same posture as
// the accountability chain's server-supplied stage catalog (§48).
//
// WHAT IT DELIBERATELY SHOWS RATHER THAN HIDES, because a follow-up board that quietly answers for
// part of the work is worse than no board:
//   · `unattributed` — open items carrying NO store, so no manager owns them. They are NOT rows
//     (nobody can be followed up about them) but they are stated at the top, because an unowned
//     backlog is exactly what this module exists to surface.
//   · `not_attributed` — every queue this board cannot assign per manager, WITH THE REASON.
//   · `truncated` — any queue whose read hit the row ceiling or failed outright. A failed read is
//     "we could not see it", never "there is none" (the §47.8 absence-is-not-zero class).
//   · an age that could not be read renders "unknown", never "0 days" — a 324-day-old row must not
//     hide in the freshest band.
const BAND_UNKNOWN = 'unknown'
const ageText = (d: number | null | undefined) => (d === null || d === undefined ? 'unknown' : `${d}d`)

type FollowItem = {
  store_code: string
  source: string
  open: number
  oldest_days: number | null
  band: string
  escalated: boolean
  oldest_items?: { ref?: string | number | null; label?: string | null; age_days?: number | null; opened_at?: string | null }[]
}
type SourceSlot = { open: number; oldest_days: number | null; bands?: Record<string, number>; unattributed?: number }
type SourceDef = { key: string; label: string; href: string; meaning?: string }
// The dry-run payload, typed so this screen cannot quietly read a key the sweep does not return.
type PlannedDigest = {
  to?: string
  addresses?: Record<string, string>
  already_sent?: boolean
  subject?: string
  items?: unknown[]
}
type PreviewResult = {
  org_id?: string
  error?: string
  followups?: number
  escalated?: number
  send_time?: string
  channels?: string[]
  email_configured?: boolean
  whatsapp_configured?: boolean
  planned?: PlannedDigest[] | null
}
type Preview = { ran?: number; dry_run?: boolean; results?: PreviewResult[] }
type Board = {
  as_of?: string
  // The tenant's resolved config, as the server returns it (the house defaults when the tenant has
  // configured nothing), so this screen states the real send time and channels rather than guessing.
  config?: {
    enabled?: boolean
    send_time?: string
    channels?: string[]
    escalate_after_days?: number
    show_oldest?: number
    min_items?: number
  }
  totals?: { open?: number; unattributed?: number; oldest_days?: number | null; stores?: number; sources?: number }
  by_source?: Record<string, SourceSlot>
  items?: FollowItem[]
  labels?: Record<string, string>
  sources?: SourceDef[]
  not_attributed?: Record<string, string>
  truncated?: string[]
  read?: Record<string, number>
}

export default function ManagerFollowupPage() {
  const [data, setData] = useState<Board | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [onlyEscalated, setOnlyEscalated] = useState(false)
  const [queue, setQueue] = useState('')
  // The dry-run preview: it answers "who would be messaged, about what" and SENDS NOTHING. The
  // endpoint defaults to a dry run, and this screen never passes `send` — turning the alert on is a
  // config change, not a button, so a screen cannot message five managers by mis-click.
  const [preview, setPreview] = useState<Preview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewErr, setPreviewErr] = useState('')

  useEffect(() => {
    let live = true
    api('/api/v1/commcalc/manager-followup')
      .then((d: Board) => { if (live) { setData(d); setErr(''); setLoading(false) } })
      .catch(e => { if (live) { setErr(e?.message || String(e)); setData(null); setLoading(false) } })
    return () => { live = false }
  }, [])

  const runPreview = useCallback(() => {
    setPreviewing(true); setPreviewErr(''); setPreview(null)
    api('/api/v1/commcalc/manager-followup/alerts/run-now', { method: 'POST' })
      .then((r: Preview) => { setPreview(r); setPreviewing(false) })
      .catch(e => { setPreviewErr(e?.message || String(e)); setPreviewing(false) })
  }, [])

  const labels = useMemo(() => data?.labels || {}, [data?.labels])
  const lbl = useCallback((k: string) => labels[k] || k, [labels])
  const hrefOf = useCallback(
    (k: string) => (data?.sources || []).find(s => s.key === k)?.href || '',
    [data?.sources])

  const rows = useMemo(() => {
    let r = data?.items || []
    if (onlyEscalated) r = r.filter(i => i.escalated)
    if (queue) r = r.filter(i => i.source === queue)
    return r
  }, [data?.items, onlyEscalated, queue])

  const getField = useCallback((r: FollowItem, f: string): string | number | boolean => {
    if (f === 'source') return lbl(r.source)
    // An unknown age sorts as the OLDEST, not as the newest: it is unverified, and burying it under
    // fresh work is how it stays unverified. Mirrors the server's own worst-first ordering.
    if (f === 'oldest_days') return r.oldest_days === null || r.oldest_days === undefined ? Number.MAX_SAFE_INTEGER : r.oldest_days
    return (r as unknown as Record<string, string | number | boolean>)[f]
  }, [lbl])
  const { sort, toggle, sorted } = useTableSort<FollowItem>(rows, getField)

  const columns: ExportColumn[] = useMemo(() => ([
    { header: 'Store', field: 'store_code', role: 'store', get: (r: FollowItem) => r.store_code },
    { header: 'Queue', field: 'source', get: (r: FollowItem) => lbl(r.source) },
    { header: 'Open', field: 'open', type: 'number', get: (r: FollowItem) => r.open },
    { header: 'Oldest (days)', field: 'oldest_days', get: (r: FollowItem) => ageText(r.oldest_days) },
    { header: 'Age band', field: 'band', get: (r: FollowItem) => r.band },
    { header: 'Past escalation', field: 'escalated', get: (r: FollowItem) => (r.escalated ? 'Yes' : 'No') },
  ]), [lbl])

  const totals = data?.totals || {}
  const cfg = data?.config || {}
  const unattr = Number(totals.unattributed || 0)
  const bySource = data?.by_source || {}
  const notAttributed = data?.not_attributed || {}
  const truncated = data?.truncated || []

  const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const tile: React.CSSProperties = { padding: '10px 14px', borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)', minWidth: 132 }
  const th: React.CSSProperties = { textAlign: 'left', padding: '7px 9px', borderBottom: '1px solid var(--border)', fontSize: 12.5, color: 'var(--text3)' }
  const td: React.CSSProperties = { padding: '7px 9px', borderBottom: '1px solid var(--border)', fontSize: 13.5 }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📌 Follow Up With Managers</h1>
          {/* Always visible, not a `.pg-note`: the reader cannot judge a row without knowing that
              this is a roll-up per manager and queue rather than a list of every open item. */}
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 860 }}>
            Every pending job a manager owns, <b>rolled up per store and queue</b> — how many are open, how old
            the oldest is, and whether it has passed the escalation age. Work older than{' '}
            <b>{cfg.escalate_after_days ?? 30} days</b> also reaches the manager <b>above</b> the owner. The full
            list of any queue lives on that queue&apos;s own page, linked below — this board is the follow-up, not
            the to-do list. The daily digest goes out at <b>{cfg.send_time || '10:30'}</b> local
            by <b>{(cfg.channels || []).join(' and ') || 'email'}</b>
            {cfg.enabled === false && <> — <b>currently switched off</b>, so nothing is being sent yet</>}.
          </p>
        </div>
        {!loading && sorted.length > 0 && (
          <ReportExportBar
            title="Follow Up With Managers"
            subtitle={`as of ${data?.as_of || ''}`}
            filename={`manager-followup_${data?.as_of || ''}`}
            sheets={[{ name: 'Follow-ups', columns, rows: sorted }]}
          />
        )}
      </div>

      {err && <div style={{ color: 'var(--danger)', margin: '10px 0' }}>{err}</div>}
      {loading && <div style={{ margin: '12px 0', color: 'var(--text2)' }}>Loading…</div>}

      {!loading && data && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '14px 0' }}>
            <div style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>Open items</div>
              <div style={{ fontSize: 19, fontWeight: 700 }}>{(totals.open ?? 0).toLocaleString()}</div>
            </div>
            <div style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>Follow-ups</div>
              <div style={{ fontSize: 19, fontWeight: 700 }}>{(data.items || []).length}</div>
            </div>
            <div style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>Past escalation</div>
              <div style={{ fontSize: 19, fontWeight: 700, color: 'var(--danger)' }}>
                {(data.items || []).filter(i => i.escalated).length}
              </div>
            </div>
            <div style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>Oldest</div>
              <div style={{ fontSize: 19, fontWeight: 700 }}>{ageText(totals.oldest_days)}</div>
            </div>
            <div style={tile}>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>Stores</div>
              <div style={{ fontSize: 19, fontWeight: 700 }}>{totals.stores ?? 0}</div>
            </div>
            {/* THE OWNERSHIP TILE. Work with no store cannot be followed up with anyone, and this is
                the number that says so out loud instead of it vanishing from the grid below. */}
            <div style={{ ...tile, borderColor: unattr ? 'var(--warn)' : 'var(--border)' }}>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>No owner</div>
              <div style={{ fontSize: 19, fontWeight: 700 }}>{unattr.toLocaleString()}</div>
            </div>
          </div>

          {unattr > 0 && (
            <div style={{ border: '1px solid var(--warn)', background: 'var(--surface)', borderRadius: 10,
                          padding: '10px 14px', margin: '0 0 14px', fontSize: 13.5, maxWidth: 900 }}>
              <b>{unattr.toLocaleString()} open {unattr === 1 ? 'item carries' : 'items carry'} no store</b>, so{' '}
              {unattr === 1 ? 'it' : 'they'} cannot be assigned to any manager and {unattr === 1 ? 'is' : 'are'} not
              counted in the rows below. {unattr === 1 ? 'It needs' : 'They need'} an owner before{' '}
              {unattr === 1 ? 'it' : 'they'} can be followed up.
            </div>
          )}

          {/* PER QUEUE — counts and age bands, with each queue linking to the page that owns it. */}
          <h2 style={{ fontSize: 15.5, fontWeight: 700, margin: '0 0 8px' }}>By queue</h2>
          <div style={{ overflowX: 'auto', marginBottom: 18 }}>
            <table style={{ borderCollapse: 'collapse', minWidth: 760 }}>
              <thead>
                <tr>
                  <th style={th}>Queue</th>
                  <th style={{ ...th, textAlign: 'right' }}>Open</th>
                  <th style={{ ...th, textAlign: 'right' }}>Oldest</th>
                  <th style={th}>Age bands</th>
                  <th style={{ ...th, textAlign: 'right' }}>No owner</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(bySource).map(([k, s]) => (
                  <tr key={k}>
                    <td style={td}>
                      {hrefOf(k) ? <a href={hrefOf(k)}>{lbl(k)}</a> : lbl(k)}
                      {truncated.includes(k) && (
                        <span style={{ color: 'var(--warn)', fontSize: 12, marginLeft: 8 }}>
                          partial — this queue could not be read in full
                        </span>
                      )}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>{(s.open || 0).toLocaleString()}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{ageText(s.oldest_days)}</td>
                    <td style={{ ...td, color: 'var(--text2)', fontSize: 12.5 }}>
                      {Object.entries(s.bands || {})
                        .filter(([, n]) => Number(n) > 0)
                        .map(([b, n]) => `${b === BAND_UNKNOWN ? 'age unknown' : b}: ${Number(n).toLocaleString()}`)
                        .join('  ·  ') || '—'}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>{(s.unattributed || 0).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', margin: '0 0 10px' }}>
            <select style={sel} value={queue} onChange={e => setQueue(e.target.value)}
              title="Show only one queue's follow-ups.">
              <option value="">Queue: all</option>
              {Object.keys(bySource).map(k => (<option key={k} value={k}>{lbl(k)}</option>))}
            </select>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <input type="checkbox" checked={onlyEscalated} onChange={e => setOnlyEscalated(e.target.checked)} />
              Past escalation only
            </label>
            <button onClick={runPreview} disabled={previewing} style={{ ...sel, cursor: 'pointer' }}
              title="Shows exactly who would be messaged, on which channels, and about which stores and queues. Sends nothing.">
              {previewing ? 'Checking…' : 'Preview the digest (sends nothing)'}
            </button>
          </div>

          {previewErr && <div style={{ color: 'var(--danger)', margin: '6px 0' }}>{previewErr}</div>}
          {preview && (
            <div style={{ border: '1px solid var(--border)', background: 'var(--surface)', borderRadius: 10,
                          padding: '10px 14px', margin: '0 0 14px', fontSize: 13.5, maxWidth: 940 }}>
              <b>Preview — nothing was sent.</b>
              {(preview.results || []).map((r: PreviewResult, i: number) => (
                <div key={i} style={{ marginTop: 8 }}>
                  {r.error
                    ? <span style={{ color: 'var(--danger)' }}>Could not build the digest: {String(r.error)}</span>
                    : (
                      <>
                        <div style={{ color: 'var(--text2)' }}>
                          {r.followups} follow-ups, {r.escalated} past escalation, at {r.send_time} by{' '}
                          {(r.channels || []).join(' and ')}.
                          {/* Said plainly: a channel with no credentials delivers nothing, and the
                              sweep reports that honestly rather than counting a send that never happened. */}
                          {r.email_configured === false && ' Email is not configured on the server.'}
                          {r.whatsapp_configured === false && ' WhatsApp is not configured on the server.'}
                        </div>
                        <ul style={{ margin: '6px 0 0 18px' }}>
                          {(r.planned || []).map((p: PlannedDigest, j: number) => (
                            <li key={j}>
                              {p.to} ({Object.keys(p.addresses || {}).join(', ') || 'no channel'}) —{' '}
                              {p.already_sent ? 'already followed up today' : `${(p.items || []).length} items`}
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                </div>
              ))}
            </div>
          )}

          <h2 style={{ fontSize: 15.5, fontWeight: 700, margin: '0 0 8px' }}>
            Follow-ups {sorted.length !== (data.items || []).length && `(${sorted.length} of ${(data.items || []).length})`}
          </h2>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', minWidth: 820 }}>
              <thead>
                <tr>
                  <SortableTh field="store_code" sort={sort} onSort={toggle} style={th}>Store</SortableTh>
                  <SortableTh field="source" sort={sort} onSort={toggle} style={th}>Queue</SortableTh>
                  <SortableTh field="open" sort={sort} onSort={toggle} style={{ ...th, textAlign: 'right' }}>Open</SortableTh>
                  <SortableTh field="oldest_days" sort={sort} onSort={toggle} style={{ ...th, textAlign: 'right' }}
                    title="The age of the oldest open item in this queue for this store. An age that could not be read shows as unknown and never escalates.">Oldest</SortableTh>
                  <SortableTh field="band" sort={sort} onSort={toggle} style={th}>Band</SortableTh>
                  <th style={th}></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => (
                  <tr key={`${r.store_code}|${r.source}|${i}`}>
                    <td style={td}>{r.store_code}</td>
                    <td style={td}>{hrefOf(r.source) ? <a href={hrefOf(r.source)}>{lbl(r.source)}</a> : lbl(r.source)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{r.open.toLocaleString()}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{ageText(r.oldest_days)}</td>
                    <td style={{ ...td, color: r.band === BAND_UNKNOWN ? 'var(--warn)' : undefined }}>
                      {r.band === BAND_UNKNOWN ? 'age unknown' : r.band}
                    </td>
                    <td style={td}>
                      {r.escalated && (
                        <span style={{ color: 'var(--danger)', fontWeight: 600, fontSize: 12.5 }}>escalated</span>
                      )}
                    </td>
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr><td style={{ ...td, color: 'var(--text2)' }} colSpan={6}>
                    Nothing to follow up on in this view.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* THE HONESTY FOOTER (§15z). Every queue this board cannot answer for, with the reason —
              so nobody can read the grid above as "that is everything". */}
          {Object.keys(notAttributed).length > 0 && (
            <div style={{ marginTop: 18, fontSize: 13, color: 'var(--text2)', maxWidth: 940 }}>
              <b>Not counted above</b>, because these queues cannot yet be assigned to a manager:
              <ul style={{ margin: '6px 0 0 18px' }}>
                {Object.entries(notAttributed).map(([k, why]) => (
                  <li key={k}>
                    {hrefOf(k) ? <a href={hrefOf(k)}>{lbl(k)}</a> : lbl(k)} — {why}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.as_of && (
            <p style={{ marginTop: 14, fontSize: 12.5, color: 'var(--text3)' }}>As of {data.as_of}.</p>
          )}
        </>
      )}
    </div>
  )
}
