'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { safeHref } from '@/lib/safe-url'   // H6: deep_link is tenant-editable on this very page

// IMPORT HEALTH (owner directive 2026-07-25, mig 717) — the universal registry of every import this
// tenant expects, its EXPECTED CADENCE, when it last actually delivered, and the page an admin fixes it
// at. Rows are AUTO-DERIVED from the schedules the system already knows (email/FTP sweep patterns, the
// portal sweeps, the Google closing sheet, the VidaPay/T-CETRA portal logins x their report map, and the
// connector report catalogue) and are then fully editable here — cadence, grace, deep link, enabled,
// snooze. Re-syncing is idempotent: it only ADDS feeds it has never seen; your edits are never
// overwritten and a disabled feed stays disabled.
//
// Also shows the consolidated ATTENTION list (imports + pending mappings + duplicate-data signals) that
// backs the login popup, with a "Run full check" that additionally executes the slower scans.

type Feed = {
  id: string | null; feed_key: string; label: string; module: string | null; source_type: string
  deep_link: string | null; enabled: boolean; auto_derived: boolean; derived_from: string | null
  muted_until: string | null; notes: string | null
  last_success: string | null; last_status: string; channel_success: string | null; channel_stale: boolean
  state: 'ok' | 'overdue' | 'never'; overdue: boolean; never_run: boolean
  age_hours: number | null; due_at: string | null; cadence_hours: number; grace_hours: number
}
type Health = { feeds: Feed[]; overdue: number; never: number; ready: boolean; hint?: string | null; can_edit?: boolean }
type Item = {
  group: string; key: string; severity: 'error' | 'warning' | 'info'
  label: string; detail: string; count: number; deep_link: string | null; deep_link_label: string | null
}
type Attention = {
  items: Item[]; deferred: { key: string; label: string }[]
  counts: {
    total: number; error: number; warning: number
    import: number; mapping: number; duplicate: number; config?: number; system?: number
  }
  deep: boolean; provider_errors?: { key: string; error: string }[]
}

const STATE: Record<string, { label: string; color: string; bg: string }> = {
  ok: { label: 'OK', color: '#166534', bg: '#f0fdf4' },
  overdue: { label: 'OVERDUE', color: '#b91c1c', bg: '#fef2f2' },
  never: { label: 'NEVER RUN', color: '#b45309', bg: '#fffbeb' },
}
const SRC: Record<string, string> = {
  email_sweep: 'Email sweep', ftp: 'FTP', pull: 'Portal pull',
  google_sa: 'Google sheet', manual_expected: 'Manual upload expected',
}
const when = (iso?: string | null) => { if (!iso) return '—'; try { return new Date(iso).toLocaleString() } catch { return iso } }
const hrs = (h: number | null | undefined) => (h === null || h === undefined ? '—' : `${Number(h).toLocaleString()}h`)

export default function ImportHealthPage() {
  const [health, setHealth] = useState<Health | null>(null)
  const [att, setAtt] = useState<Attention | null>(null)
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [showOk, setShowOk] = useState(true)
  const [edit, setEdit] = useState<Record<string, { cadence_hours: string; grace_hours: string }>>({})

  const loadHealth = useCallback(async () => {
    setErr('')
    try { setHealth(await api('/api/v1/core/import-feeds')) }
    catch (e: any) { setErr(e?.message || String(e)); setHealth(null) }
  }, [])

  const loadAtt = useCallback(async (deep: boolean) => {
    try { setAtt(await api(`/api/v1/core/attention${deep ? '?deep=1' : ''}`)) }
    catch { setAtt(null) }
  }, [])

  useEffect(() => {
    setLoading(true)
    Promise.all([loadHealth(), loadAtt(false)]).finally(() => setLoading(false))
  }, [loadHealth, loadAtt])

  const canEdit = !!health?.can_edit
  // A channel-stale feed is state 'ok' by construction (the data IS arriving, just not through the
  // configured channel — see feed_status), so the problems-only view must keep it visible or the one
  // surface that reports a silently-broken sweep would hide it.
  const feeds = useMemo(
    () => (health?.feeds || []).filter(f => showOk || f.state !== 'ok' || f.channel_stale),
    [health, showOk])

  const sync = async () => {
    setBusy(true); setMsg(''); setErr('')
    try {
      const r: any = await api('/api/v1/core/import-feeds/sync', { method: 'POST' })
      setMsg(`Re-synced — ${r.feeds} feed(s) registered${r.added ? `, ${r.added} new` : ' (nothing new)'}.`)
      await loadHealth()
    } catch (e: any) { setErr(e?.message || String(e)) } finally { setBusy(false) }
  }

  const patch = async (f: Feed, body: any) => {
    if (!f.id) { setErr('This feed has not been saved yet — re-sync first.'); return }
    setBusy(true); setMsg(''); setErr('')
    try {
      await api(`/api/v1/core/import-feeds/${f.id}`, { method: 'PUT', body: JSON.stringify(body) })
      await loadHealth(); setMsg('Saved.')
    } catch (e: any) { setErr(e?.message || String(e)) } finally { setBusy(false) }
  }

  const saveCadence = (f: Feed) => {
    const e = edit[f.feed_key]; if (!e) return
    const c = Number(e.cadence_hours), g = Number(e.grace_hours)
    if (!isFinite(c) || c < 0 || !isFinite(g) || g < 0) { setErr('Cadence and grace must be hours ≥ 0.'); return }
    patch(f, { cadence_hours: c, grace_hours: g })
    setEdit(s => { const n = { ...s }; delete n[f.feed_key]; return n })
  }

  const snooze = (f: Feed, days: number) =>
    patch(f, { muted_until: new Date(Date.now() + days * 86400000).toISOString() })

  if (loading) return <div style={{ color: 'var(--text3)' }}>Loading import health…</div>

  return (
    <div style={{ maxWidth: 1180 }}>
      <h1 style={{ fontSize: 22, fontWeight: 800, marginBottom: 4 }}>📡 Import Health</h1>
      <p className="pg-note" style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 16, maxWidth: 900 }}>
        Every data feed this company expects, how often it should arrive, and when it last actually
        delivered. Anything overdue or never-run also pops up for admins at login. Feeds are discovered
        automatically from your import settings — edit the cadence, disable one you don&apos;t use, or
        snooze it while you sort it out.
      </p>

      {err && <div className="card" style={{ background: '#fef2f2', color: '#b91c1c', padding: 12, marginBottom: 12, fontSize: 13 }}>{err}</div>}
      {msg && <div className="card" style={{ background: '#f0fdf4', color: '#166534', padding: 12, marginBottom: 12, fontSize: 13 }}>{msg}</div>}
      {health && health.ready === false && (
        <div className="card" style={{ background: '#fffbeb', color: '#92400e', padding: 12, marginBottom: 12, fontSize: 13 }}>
          {health.hint || 'Import health is not set up yet — run migration 717.'}
        </div>
      )}

      {/* ── attention summary (same data the login popup shows) ─────────────────────────────── */}
      {att && (
        <div className="card" style={{ padding: 14, marginBottom: 18 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>Needs attention</div>
            <span style={{ fontSize: 12.5, color: 'var(--text2)' }}>
              {att.counts.import} import · {att.counts.mapping} mapping · {att.counts.duplicate} duplicate
              {' · '}{att.counts.config || 0} setup · {att.counts.system || 0} system
            </span>
            <button className="btn" disabled={busy} style={{ fontSize: 12.5 }}
              title="Re-run the checks now — anything you have just fixed drops off the list"
              onClick={async () => { setBusy(true); await loadAtt(att.deep); setBusy(false) }}>
              Re-check
            </button>
            {!att.deep && (att.deferred || []).length > 0 && (
              <button className="btn" disabled={busy} style={{ marginLeft: 'auto', fontSize: 12.5 }}
                onClick={async () => { setBusy(true); await loadAtt(true); setBusy(false) }}>
                Run full check ({(att.deferred || []).length} slower scan{att.deferred.length === 1 ? '' : 's'})
              </button>
            )}
          </div>
          {(att.items || []).length === 0
            ? <div style={{ fontSize: 13, color: '#166534', marginTop: 8 }}>✅ Nothing needs attention right now.</div>
            : (
              <div style={{ marginTop: 10, display: 'grid', gap: 6 }}>
                {/* every item renders here regardless of its group (this list is flat, not bucketed);
                    the key carries the group so two providers using the same item key can't collide */}
                {att.items.map(i => (
                  <div key={`${i.group || ''}:${i.key}`} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 12.5 }}>
                    <span style={{ width: 74, flexShrink: 0, fontWeight: 700, textTransform: 'uppercase',
                      fontSize: 10, letterSpacing: '0.05em', color: 'var(--text3)' }}>{i.group}</span>
                    <span style={{ fontWeight: 600 }}>{i.label}</span>
                    <span style={{ color: 'var(--text2)', minWidth: 0, flex: 1 }}>{i.detail}</span>
                    {safeHref(i.deep_link) && <Link href={safeHref(i.deep_link, '#')} style={{ whiteSpace: 'nowrap', fontWeight: 700, color: 'var(--accent)', textDecoration: 'none' }}>{i.deep_link_label || 'Fix'} →</Link>}
                  </div>
                ))}
              </div>
            )}
        </div>
      )}

      {/* ── the registry ───────────────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 700, fontSize: 15 }}>
          Registered feeds ({health?.feeds?.length || 0})
          {health ? <span style={{ fontWeight: 400, color: 'var(--text2)', fontSize: 13 }}> · {health.overdue} overdue · {health.never} never run</span> : null}
        </div>
        <label style={{ fontSize: 12.5, color: 'var(--text2)', display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={showOk} onChange={e => setShowOk(e.target.checked)} /> show healthy
        </label>
        {canEdit && (
          <button className="btn" onClick={sync} disabled={busy} style={{ marginLeft: 'auto', fontSize: 12.5 }}>
            {busy ? 'Working…' : 'Re-sync from import settings'}
          </button>
        )}
      </div>

      <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
          <thead>
            <tr style={{ background: 'var(--bg)', textAlign: 'left' }}>
              {['Feed', 'Source', 'Expected every', 'Last success', 'Status', ''].map(h => (
                <th key={h} style={{ padding: '9px 12px', fontWeight: 700, color: 'var(--text2)',
                  borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {feeds.length === 0 && (
              <tr><td colSpan={6} style={{ padding: 18, color: 'var(--text3)' }}>
                No feeds registered yet. Configure an import (Email / FTP / portal sweep) and re-sync.
              </td></tr>
            )}
            {feeds.map(f => {
              const st = STATE[f.state] || STATE.ok
              const e = edit[f.feed_key]
              const muted = !!f.muted_until && new Date(f.muted_until) > new Date()
              return (
                <tr key={f.feed_key} style={{ borderBottom: '1px solid var(--border)', opacity: f.enabled ? 1 : 0.55 }}>
                  <td style={{ padding: '9px 12px', minWidth: 240 }}>
                    <div style={{ fontWeight: 600 }}>{f.label}</div>
                    <div style={{ color: 'var(--text3)', fontSize: 11 }}>
                      {f.feed_key}{f.derived_from ? ` · from ${f.derived_from}` : ''}
                    </div>
                  </td>
                  <td style={{ padding: '9px 12px', whiteSpace: 'nowrap' }}>{SRC[f.source_type] || f.source_type}</td>
                  <td style={{ padding: '9px 12px', whiteSpace: 'nowrap' }}>
                    {canEdit && e ? (
                      <span style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                        <input value={e.cadence_hours} onChange={ev => setEdit(s => ({ ...s, [f.feed_key]: { ...e, cadence_hours: ev.target.value } }))}
                          style={{ width: 58 }} /> h +
                        <input value={e.grace_hours} onChange={ev => setEdit(s => ({ ...s, [f.feed_key]: { ...e, grace_hours: ev.target.value } }))}
                          style={{ width: 52 }} /> grace
                        <button className="btn" style={{ fontSize: 11 }} disabled={busy} onClick={() => saveCadence(f)}>Save</button>
                      </span>
                    ) : (
                      <span>
                        {hrs(f.cadence_hours)} <span style={{ color: 'var(--text3)' }}>+{hrs(f.grace_hours)} grace</span>
                        {canEdit && (
                          <button onClick={() => setEdit(s => ({ ...s, [f.feed_key]: { cadence_hours: String(f.cadence_hours), grace_hours: String(f.grace_hours) } }))}
                            style={{ marginLeft: 6, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent)', fontSize: 11 }}>edit</button>
                        )}
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '9px 12px', whiteSpace: 'nowrap' }}>
                    {when(f.last_success)}
                    {f.channel_stale && f.last_success && (
                      <div style={{ color: '#b45309', fontSize: 11 }}>channel stale — data arrived another way</div>
                    )}
                  </td>
                  <td style={{ padding: '9px 12px', whiteSpace: 'nowrap' }}>
                    <span style={{ fontWeight: 700, fontSize: 11, color: st.color, background: st.bg,
                      border: `1px solid ${st.color}33`, borderRadius: 6, padding: '2px 7px' }}>{st.label}</span>
                    {!f.enabled && <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--text3)' }}>disabled</span>}
                    {muted && <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--text3)' }}>snoozed</span>}
                    {f.age_hours !== null && f.state !== 'ok' && (
                      <div style={{ color: 'var(--text3)', fontSize: 11 }}>{hrs(f.age_hours)} ago</div>
                    )}
                  </td>
                  <td style={{ padding: '9px 12px', whiteSpace: 'nowrap', textAlign: 'right' }}>
                    {safeHref(f.deep_link) && (
                      <Link href={safeHref(f.deep_link, '#')} style={{ fontWeight: 700, color: 'var(--accent)', textDecoration: 'none', marginRight: 10 }}>Fix / Upload →</Link>
                    )}
                    {canEdit && (
                      <>
                        <button onClick={() => patch(f, { enabled: !f.enabled })} disabled={busy}
                          style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 6,
                            padding: '3px 8px', cursor: 'pointer', fontSize: 11, marginRight: 6 }}>
                          {f.enabled ? 'Disable' : 'Enable'}
                        </button>
                        <button onClick={() => snooze(f, 7)} disabled={busy}
                          style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 6,
                            padding: '3px 8px', cursor: 'pointer', fontSize: 11 }}>Snooze 7d</button>
                      </>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {!canEdit && (
        <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
          You can view import health but not change schedules. Ask an owner to grant the
          &ldquo;Import Health&rdquo; setting to your role under Roles &amp; Access.
        </p>
      )}
      {att?.provider_errors?.length ? (
        <p style={{ fontSize: 12, color: '#b45309', marginTop: 10 }}>
          Some checks could not run: {att.provider_errors.map(p => p.key).join(', ')}.
        </p>
      ) : null}
    </div>
  )
}
