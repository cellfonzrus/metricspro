'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useAuth } from '@/lib/auth-context'
import { api, getActiveOrg } from '@/lib/client'
import { canSeeAttention } from '@/lib/rbac'
import {
  CATEGORY_ICON, CATEGORY_LABEL, ReleaseNote, WhatsNewPayload, EMPTY_PAYLOAD,
  fetchWhatsNew, lastSeen, markSeen,
} from '@/lib/whats-new'

// ── ADMIN ATTENTION (owner directive 2026-07-25, mig 717) ────────────────────────────────────────────
// "if any imports are not scheduled as defined in the entire system it should come up as a pop for every
// admin as soon as they log in on the main page and take them to the upload menu to manually upload the
// data or fix the import channel … also the admin should be notified for pending mappings or duplicate data"
//
// Behaviour:
//   • Renders NOTHING for a non-admin (gate mirrors the backend `can_view_attention`, via rbac.canSeeAttention).
//   • Fetches GET /api/v1/core/attention (cheap providers only — a login never pays for a 40k-row scan; the
//     heavy scans are behind the "Run full check" button, deep=1).
//   • Pops the modal ONCE PER LOGIN SESSION per acting tenant (sessionStorage), so it does not nag on every
//     navigation; a persistent header pill remains and reopens it on demand.
//   • Every item carries the deep link that FIXES it (import → the channel page or the upload menu).
//   • Multi-tenant: api() scopes the call to the ACTING org, so a super-admin acting as a tenant sees that
//     tenant's items only. Switching tenants changes the dismissal key, so the new tenant's popup shows.
//   • Fail-silent: any error (mig 717 un-run, 403, offline) leaves the whole component invisible. It can
//     never block or break a page.
//
// ── "A NOTIFICATION MUST DISAPPEAR ONCE THE CHECK IS OK" (owner, 2026-07-26) ─────────────────────────
// ZERO items ⇒ this component renders NOTHING — no pill, no popup (the single `return null` guard below
// covers both, and the popup is only ever opened when items exist). The backend providers report live
// state, so an item disappears the moment its cause is fixed.
// The component lives in the platform LAYOUT, so it does NOT remount as an admin navigates: a
// fetch-once-per-mount would leave a STALE pill up for the rest of the session after a fix. It therefore
// RE-FETCHES on navigation (throttled to at most once per REFRESH_MS so a click-heavy session cannot
// hammer the endpoint) and offers an explicit "Re-check now" button. Cost of a refresh = the cheap
// providers only (config-table reads, TTL-memoized feed derivation).
//
// ── "NEW FEATURES" + "IMPROVEMENTS" (owner directive 2026-08-04, mig 721) ────────────────────────────
// "like we have the warnings for the admin who logs in, there should be 2 more areas new features and
// improvements and keep them logged somewhere only for admin staff."
// Two ADDITIONAL tabs on this same modal, behind this same gate. The WARNINGS tab's semantics are
// deliberately untouched: its items, counts, deep links and "disappears once fixed" contract are exactly
// as before. The new tabs read GET /core/whats-new (fail-silent, so an un-run mig 721 simply hides them),
// and "unseen" is a per-tenant localStorage watermark stamped when the admin opens the tab.
// The popup now also fires when there are NO warnings but there ARE unseen updates — an admin with a
// clean system still learns what shipped — and still renders NOTHING when both are empty.
const REFRESH_MS = 20_000

type Item = {
  group: string; key: string; severity: 'error' | 'warning' | 'info'
  label: string; detail: string; count: number
  deep_link: string | null; deep_link_label: string | null; provider?: string
}
type Counts = {
  total: number; error: number; warning: number
  import: number; mapping: number; duplicate: number; config?: number; system?: number
}
type Payload = {
  items: Item[]
  deferred: { key: string; label: string; group: string }[]
  counts: Counts
  deep: boolean; ready?: boolean; hint?: string | null
}

const SEV: Record<string, { color: string; bg: string; icon: string }> = {
  error: { color: '#b91c1c', bg: '#fef2f2', icon: '⛔' },
  warning: { color: '#b45309', bg: '#fffbeb', icon: '⚠️' },
  info: { color: '#1d4ed8', bg: '#eff6ff', icon: 'ℹ️' },
}
const GROUP_LABEL: Record<string, string> = {
  import: 'Imports overdue or never run',
  mapping: 'Pending mappings',
  duplicate: 'Possible duplicate data',
  config: 'Setup not finished',
  system: 'System errors to review',
  other: 'Other',
}
const GROUP_ORDER = ['import', 'mapping', 'duplicate', 'config', 'system', 'other']
// 'other' is the CATCH-ALL: an item whose group this build doesn't know (another module registered a
// provider with a new group, e.g. 'people' / 'ops' / 'security') must still be VISIBLE. `bucketOf` maps
// any unknown/missing group onto 'other' — which is why GROUP_ORDER must always contain 'other'.
const KNOWN_GROUPS = new Set(GROUP_ORDER)
const bucketOf = (g?: string) => (g && KNOWN_GROUPS.has(g) ? g : 'other')

export default function AdminAttention() {
  const { permissions, loading, session } = useAuth()
  const pathname = usePathname()
  const [data, setData] = useState<Payload | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const lastAt = useRef(0)
  const allowed = canSeeAttention(permissions)
  // What's New (mig 721). Separate state + separate fetch: a failure here must not affect warnings.
  const [wn, setWn] = useState<WhatsNewPayload>(EMPTY_PAYLOAD)
  const [tab, setTab] = useState<'warnings' | 'new_feature' | 'improvement'>('warnings')
  const org = getActiveOrg()
  const [seen, setSeen] = useState('')
  useEffect(() => { setSeen(lastSeen(org)) }, [org])

  const loadWhatsNew = useCallback(async (since: string) => {
    const p = await fetchWhatsNew({ since })
    setWn(p)
  }, [])

  const load = useCallback(async (deep: boolean) => {
    setBusy(true)
    lastAt.current = Date.now()
    try {
      const d: Payload = await api(`/api/v1/core/attention${deep ? '?deep=1' : ''}`)
      setData(d)
      return d
    } catch {
      setData(null)   // fail-silent: 403 / un-run migration / offline → the component stays invisible
      return null
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    if (loading || !allowed || !session) return
    loadWhatsNew(lastSeen(org))
  }, [loading, allowed, session, org, loadWhatsNew])

  // Once per login SESSION per acting tenant — sessionStorage clears when the tab/session ends, so the
  // next real login pops again while ordinary navigation inside the session does not. Claimed by
  // whichever of the two triggers below fires first; the other then does not re-open.
  const claimSessionPop = useCallback(() => {
    try {
      const k = `mp_attention_seen_${getActiveOrg() || 'default'}`
      if (window.sessionStorage.getItem(k) === '1') return false
      window.sessionStorage.setItem(k, '1')
    } catch { /* private mode → just show it */ }
    return true
  }, [])

  // Trigger 1 — WARNINGS (unchanged semantics: only fires when there is at least one item).
  useEffect(() => {
    if (loading || !allowed || !session) return
    let alive = true
    load(false).then(d => {
      if (!alive || !d || !(d.items || []).length) return
      if (!claimSessionPop()) return
      setTab('warnings')
      setOpen(true)
    })
    return () => { alive = false }
  }, [loading, allowed, session, load, claimSessionPop])

  // Trigger 2 — UNSEEN UPDATES. An admin whose system is clean still learns what shipped. Runs when the
  // What's New payload lands; if the warnings trigger already claimed the session pop, this does nothing.
  useEffect(() => {
    if (loading || !allowed || !session) return
    if (!wn.ready || !wn.unseen_counts.total) return
    if (!claimSessionPop()) return
    setTab(wn.unseen_counts.new_feature ? 'new_feature' : 'improvement')
    setOpen(true)
  }, [loading, allowed, session, wn, claimSessionPop])

  // Live-refresh on navigation (throttled): an admin who has just FIXED something navigates back, and the
  // pill/popup must reflect the new state instead of the state at mount. Never opens the popup by itself.
  useEffect(() => {
    if (loading || !allowed || !session) return
    if (Date.now() - lastAt.current < REFRESH_MS) return
    load(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname])

  // RENDER NOTHING when there is nothing to say — the owner's "a notification must disappear once the
  // check is OK" rule, now extended: no warnings AND no unseen updates ⇒ no pill, no popup.
  const warnItems = (data?.items || [])
  const unseenTotal = wn.unseen_counts.total
  if (!allowed || (!warnItems.length && !unseenTotal)) return null
  const c = data?.counts || { total: 0, error: 0, warning: 0, import: 0, mapping: 0, duplicate: 0 }
  const notesFor = (cat: 'new_feature' | 'improvement') =>
    (wn.entries || []).filter(e => e.category === cat)
  const openTab = (t: 'warnings' | 'new_feature' | 'improvement') => {
    setTab(t)
    if (t !== 'warnings' && unseenTotal) { markSeen(org); setSeen(new Date().toISOString().slice(0, 10)); loadWhatsNew(new Date().toISOString().slice(0, 10)) }
  }
  // Bucket EVERY item, including a group this build has never heard of. Any module can register a provider
  // (register_provider(..., group=…)) and ship a new group before this component knows about it — with a
  // strict `group === g` filter such an item inflated the pill count (counts.total is computed server-side
  // over ALL items) but never appeared in the modal body. Unknown group ⇒ rendered under "Other" with its
  // own severity/label/detail intact, so the number on the pill and the rows on screen can never disagree.
  const groups = GROUP_ORDER
    .map(g => ({ g, items: warnItems.filter(i => bucketOf(i.group) === g) }))
    .filter(x => x.items.length > 0)
  // …and keep the one-line summary honest for those same unknown groups (it lists only the named ones).
  const residual = Math.max(0, (c.total || 0) - (c.import || 0) - (c.mapping || 0) - (c.duplicate || 0)
    - (c.config || 0) - (c.system || 0))

  return (
    <>
      {/* persistent indicators — stay after the popup is dismissed. The warnings pill is byte-identical
          to before; it is simply not rendered when there are no warnings (previously the whole component
          returned null in that case, so nothing regresses). */}
      {warnItems.length > 0 && (
        <button onClick={() => { setTab('warnings'); setOpen(true) }} title="Items needing an administrator's attention"
          style={{
            fontSize: 13, fontWeight: 700, cursor: 'pointer',
            color: c.error ? '#b91c1c' : '#b45309', background: c.error ? '#fef2f2' : '#fffbeb',
            border: `1px solid ${c.error ? '#fecaca' : '#fde68a'}`, borderRadius: 8, padding: '5px 10px',
          }}>
          {c.error ? '⛔' : '⚠️'} {c.total} needs attention
        </button>
      )}
      {unseenTotal > 0 && (
        <button onClick={() => { openTab(wn.unseen_counts.new_feature ? 'new_feature' : 'improvement'); setOpen(true) }}
          title="New features and improvements you haven't looked at yet"
          style={{
            fontSize: 13, fontWeight: 700, cursor: 'pointer', color: '#1d4ed8', background: '#eff6ff',
            border: '1px solid #bfdbfe', borderRadius: 8, padding: '5px 10px', marginLeft: 6,
          }}>
          ✨ {unseenTotal} new
        </button>
      )}

      {open && (
        <div role="dialog" aria-modal="true" onClick={() => setOpen(false)}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000,
            display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '6vh 16px',
          }}>
          <div onClick={e => e.stopPropagation()} className="card"
            style={{ background: 'white', maxWidth: 760, width: '100%', maxHeight: '84vh', overflowY: 'auto',
              borderRadius: 14, boxShadow: '0 20px 60px rgba(0,0,0,0.25)', padding: 0 }}>
            <div style={{ padding: '18px 22px 12px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{ fontSize: 17, fontWeight: 800 }}>
                  {tab === 'warnings' ? 'Needs your attention' : CATEGORY_LABEL[tab]}
                </div>
                <button onClick={() => setOpen(false)} aria-label="Close"
                  style={{ marginLeft: 'auto', background: 'none', border: 'none', fontSize: 20,
                    cursor: 'pointer', color: 'var(--text3)', lineHeight: 1 }}>×</button>
              </div>

              {/* Tabs. Only shown once there is more than one thing to show, so a system with warnings
                  and no update log looks exactly as it did before. */}
              {(wn.ready && (wn.counts.new_feature > 0 || wn.counts.improvement > 0)) && (
                <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
                  {([
                    ['warnings', `⚠️ Warnings`, warnItems.length],
                    ['new_feature', `${CATEGORY_ICON.new_feature} ${CATEGORY_LABEL.new_feature}`, wn.unseen_counts.new_feature],
                    ['improvement', `${CATEGORY_ICON.improvement} ${CATEGORY_LABEL.improvement}`, wn.unseen_counts.improvement],
                  ] as const).map(([k, label, badge]) => (
                    <button key={k} onClick={() => openTab(k as any)}
                      style={{ fontSize: 12.5, fontWeight: 700, cursor: 'pointer', borderRadius: 8,
                        padding: '5px 10px', border: '1px solid var(--border)',
                        background: tab === k ? 'var(--accent)' : 'white',
                        color: tab === k ? 'white' : 'var(--text2)' }}>
                      {label}{badge ? ` (${badge})` : ''}
                    </button>
                  ))}
                </div>
              )}

              {tab === 'warnings' ? (
                <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 6 }}>
                  {c.import > 0 && <>{c.import} import{c.import === 1 ? '' : 's'} overdue or never run. </>}
                  {c.mapping > 0 && <>{c.mapping} pending mapping issue{c.mapping === 1 ? '' : 's'}. </>}
                  {c.duplicate > 0 && <>{c.duplicate} possible duplicate-data signal{c.duplicate === 1 ? '' : 's'}. </>}
                  {!!c.config && <>{c.config} setup item{c.config === 1 ? '' : 's'} unfinished. </>}
                  {!!c.system && <>{c.system} system error{c.system === 1 ? '' : 's'} to review. </>}
                  {residual > 0 && <>{residual} other item{residual === 1 ? '' : 's'}. </>}
                  Each item disappears from here as soon as it is fixed.
                </div>
              ) : (
                <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 6 }}>
                  {tab === 'new_feature'
                    ? 'What has been added to MetricsPro. Only your admin staff see this.'
                    : 'Things that already existed and now work better. Only your admin staff see this.'}
                </div>
              )}
            </div>

            {tab !== 'warnings' && (
              <div style={{ padding: '8px 22px 4px' }}>
                {notesFor(tab).length === 0 ? (
                  <div style={{ fontSize: 13, color: 'var(--text3)', padding: '14px 0' }}>
                    Nothing logged here yet.
                  </div>
                ) : notesFor(tab).map(n => (
                  <div key={n.slug} style={{ border: '1px solid var(--border)', borderLeft: '4px solid #2563eb',
                    background: '#f8fbff', borderRadius: 10, padding: '10px 12px', marginBottom: 8 }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                      <div style={{ fontSize: 13.5, fontWeight: 700 }}>{n.title}</div>
                      {n.status === 'in_progress' && (
                        <span className="badge" style={{ fontSize: 10.5, background: '#fffbeb', color: '#b45309' }}>
                          Coming shortly
                        </span>
                      )}
                      {(n.released_at > (seen || '')) && (
                        <span className="badge" style={{ fontSize: 10.5, background: '#eff6ff', color: '#1d4ed8' }}>New</span>
                      )}
                      <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text3)' }}>{n.released_at}</span>
                    </div>
                    {n.body && <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 3, lineHeight: 1.55 }}>{n.body}</div>}
                    {n.deep_link && n.status !== 'in_progress' && (
                      <Link href={n.deep_link} onClick={() => setOpen(false)}
                        style={{ fontSize: 12, fontWeight: 700, color: 'var(--accent)', textDecoration: 'none',
                          display: 'inline-block', marginTop: 6 }}>
                        Take a look →
                      </Link>
                    )}
                  </div>
                ))}
              </div>
            )}

            {tab === 'warnings' && (
            <div style={{ padding: '8px 22px 4px' }}>
              {groups.map(({ g, items }) => (
                <div key={g} style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.06em',
                    textTransform: 'uppercase', color: 'var(--text3)', marginBottom: 6 }}>
                    {GROUP_LABEL[g] || g}
                  </div>
                  {items.map(it => {
                    const s = SEV[it.severity] || SEV.info
                    return (
                      // key includes the PROVIDER: two independently registered providers may legitimately
                      // pick the same item key, and a duplicate React key mis-diffs (a row can vanish).
                      <div key={`${it.provider || ''}:${it.group || ''}:${it.key}`}
                        style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
                        border: '1px solid var(--border)', borderLeft: `4px solid ${s.color}`,
                        background: s.bg, borderRadius: 10, padding: '10px 12px', marginBottom: 8 }}>
                        <span style={{ fontSize: 15, lineHeight: '20px' }}>{s.icon}</span>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ fontSize: 13.5, fontWeight: 700, color: s.color }}>{it.label}</div>
                          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 2 }}>{it.detail}</div>
                        </div>
                        {it.deep_link && (
                          <Link href={it.deep_link} onClick={() => setOpen(false)}
                            style={{ alignSelf: 'center', whiteSpace: 'nowrap', fontSize: 12.5, fontWeight: 700,
                              textDecoration: 'none', color: 'white', background: s.color,
                              borderRadius: 8, padding: '6px 11px' }}>
                            {it.deep_link_label || 'Fix'} →
                          </Link>
                        )}
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>
            )}

            <div style={{ padding: '10px 22px 18px', borderTop: '1px solid var(--border)', marginTop: 10,
              display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              {tab === 'warnings' && data && (
                <>
                  <button className="btn" disabled={busy} onClick={() => load(data.deep)}
                    title="Re-run the checks now — anything you have just fixed drops off the list"
                    style={{ fontSize: 12.5 }}>
                    {busy ? 'Checking…' : 'Re-check now'}
                  </button>
                  {!data.deep && (data.deferred || []).length > 0 && (
                    <button className="btn" disabled={busy} onClick={() => load(true)}
                      title={`Also runs the slower checks: ${(data.deferred || []).map(d => d.label).join(', ')}`}
                      style={{ fontSize: 12.5 }}>
                      {busy ? 'Checking…' : 'Run full check'}
                    </button>
                  )}
                  <Link href="/admin/import-health" onClick={() => setOpen(false)}
                    style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--accent)', textDecoration: 'none' }}>
                    Manage import schedules →
                  </Link>
                </>
              )}
              {wn.ready && (
                <Link href="/admin/whats-new" onClick={() => setOpen(false)}
                  style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--accent)', textDecoration: 'none' }}>
                  All updates →
                </Link>
              )}
              <button onClick={() => setOpen(false)}
                style={{ marginLeft: 'auto', fontSize: 12.5, background: 'none', border: '1px solid var(--border)',
                  borderRadius: 8, padding: '6px 12px', cursor: 'pointer', color: 'var(--text2)' }}>
                Dismiss for now
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
