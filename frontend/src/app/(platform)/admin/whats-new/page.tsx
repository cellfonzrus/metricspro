'use client'
// ── WHAT'S NEW — the permanent, admin-only log (mig 721, owner directive 2026-08-04) ────────────
// "…there should be 2 more areas new features and improvements and keep them logged somewhere only for
// admin staff." This is the "logged somewhere" half: every entry ever published, filterable and
// exportable, next to the form that publishes a new one.
//
// GATE: the server applies the SAME gate as the login warnings, so a rep who reaches this URL sees the
// "administrators only" card and no data. Writing additionally needs the edit grant, and only a
// super-admin can publish a PLATFORM-WIDE entry that every tenant's admins will read.
//
// RULE FOUR (exports) — the log renders through ReportShell, so Excel / PDF / print / email / WhatsApp
// come for free and respect the ACTIVE filters.
// RULE FIVE (filters) — the standard bar's period range is here as released-from / released-to; the
// store/market/rep dimensions do not exist for a changelog, so category + area take their place
// (both pick-don't-type, RULE THREE).
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import EntityPicker from '@/components/EntityPicker'
import ReportShell from '@/components/ReportShell'
import { MODULE_LABEL } from '@/lib/tours'
import {
  CATEGORY_ICON, CATEGORY_LABEL, CATEGORY_ORDER, ReleaseNote, WhatsNewPayload, EMPTY_PAYLOAD,
  fetchWhatsNew, markSeen,
} from '@/lib/whats-new'
import { api } from '@/lib/client'

const ORG_HOUSE = '00000000-0000-0000-0000-000000000001'
const inp: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)',
  fontSize: 13, background: 'var(--surface)', width: '100%' }

const blank = (): ReleaseNote => ({
  slug: '', category: 'new_feature', module: null, title: '', body: '',
  status: 'shipped', deep_link: '', released_at: new Date().toISOString().slice(0, 10),
  is_published: true,
})

export default function WhatsNewAdminPage() {
  const [data, setData] = useState<WhatsNewPayload>(EMPTY_PAYLOAD)
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const [fCat, setFCat] = useState<string | null>(null)
  const [fModule, setFModule] = useState<string | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  const [editing, setEditing] = useState<ReleaseNote | null>(null)
  const [asPlatform, setAsPlatform] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api('/api/v1/core/whats-new')
      setData({
        entries: r?.entries || [], unseen: r?.unseen || [],
        counts: r?.counts || EMPTY_PAYLOAD.counts, unseen_counts: r?.unseen_counts || EMPTY_PAYLOAD.unseen_counts,
        ready: !!r?.ready, can_edit: !!r?.can_edit, is_super: !!r?.is_super, hint: r?.hint,
      })
      setDenied(false)
    } catch (e: any) {
      // A 403 here means "not an administrator" — show the honest card, never an error page.
      setDenied(String(e?.message || '').toLowerCase().includes('administrator'))
      setData(EMPTY_PAYLOAD)
    } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  // Opening the log counts as having looked at it, so the popup's "N new" badge clears.
  useEffect(() => { if (data.ready) markSeen(null) }, [data.ready])

  const rows = useMemo(() => (data.entries || []).filter(e => {
    if (fCat && e.category !== fCat) return false
    if (fModule && (e.module || '') !== fModule) return false
    if (from && String(e.released_at) < from) return false
    if (to && String(e.released_at) > to) return false
    return true
  }), [data.entries, fCat, fModule, from, to])

  const moduleOptions = useMemo(() => {
    const keys = Array.from(new Set((data.entries || []).map(e => e.module || 'other')))
    return keys.map(k => ({ id: k, label: MODULE_LABEL[k] || k }))
  }, [data.entries])

  async function save() {
    if (!editing) return
    if (!editing.title.trim()) { setMsg('❌ Give the update a title.'); return }
    setBusy(true); setMsg('')
    const org = (data.is_super && asPlatform) ? ORG_HOUSE : ''
    try {
      const r: any = await api(`/api/v1/core/whats-new${org ? `?org_id=${org}` : ''}`,
        { method: 'POST', body: JSON.stringify(editing) })
      setMsg(r?.is_platform_wide
        ? '✅ Published to every organisation.'
        : '✅ Published to your organisation.')
      setEditing(null); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  async function remove(n: ReleaseNote) {
    if (!n.id) return
    if (!window.confirm(`Remove "${n.title}" from the log?`)) return
    setBusy(true); setMsg('')
    const org = (data.is_super && n.org_id === ORG_HOUSE) ? ORG_HOUSE : ''
    try {
      await api(`/api/v1/core/whats-new/${n.id}${org ? `?org_id=${org}` : ''}`, { method: 'DELETE' })
      setMsg('✅ Removed.'); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  if (denied) {
    return (
      <div className="card" style={{ padding: 26, fontSize: 13.5, color: 'var(--text2)', lineHeight: 1.7 }}>
        The updates log is for administrators. If you think you should have access, ask whoever manages
        roles for your organisation.
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
        gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>✨ What&apos;s new</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 800, lineHeight: 1.6 }}>
            Everything that has been added or improved, newest first. This is the same list your admins see
            beside the login warnings — nobody outside your admin staff sees any of it.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Link href="/training" className="btn btn-secondary" style={{ fontSize: 13 }}>🎓 Training Center</Link>
          {data.can_edit && (
            <button className="btn btn-primary" style={{ fontSize: 13 }}
              onClick={() => { setEditing(blank()); setAsPlatform(false); setMsg('') }}>＋ Post an update</button>
          )}
        </div>
      </div>

      {msg && <div className="card" style={{ padding: '9px 13px', marginBottom: 12, fontSize: 13 }}>{msg}</div>}

      {!loading && !data.ready && (
        <div className="card" style={{ padding: 22, marginBottom: 14, fontSize: 13.5, color: 'var(--text2)', lineHeight: 1.7 }}>
          The updates log isn&apos;t set up on this system yet (migration 721). Until an administrator runs
          it, nothing is recorded here and the login popup shows only its warnings — everything else works
          exactly as before.
        </div>
      )}

      {editing && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>Post an update</div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <label style={{ flex: '1 1 320px' }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Title</div>
              <input style={inp} value={editing.title} onChange={e => setEditing({ ...editing, title: e.target.value })}
                placeholder="One plain line — what changed, from the user's point of view" />
            </label>
            <label style={{ flex: '0 0 190px' }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Kind</div>
              <EntityPicker options={CATEGORY_ORDER.map(c => ({ id: c, label: CATEGORY_LABEL[c] }))}
                value={editing.category} onChange={v => setEditing({ ...editing, category: (v as any) || 'new_feature' })}
                placeholder="Kind…" width="100%" />
            </label>
            <label style={{ flex: '0 0 190px' }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Area</div>
              <EntityPicker options={Object.entries(MODULE_LABEL).map(([k, v]) => ({ id: k, label: v }))}
                value={editing.module || null} onChange={v => setEditing({ ...editing, module: v })}
                placeholder="Area…" width="100%" />
            </label>
            <label style={{ flex: '0 0 170px' }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Status</div>
              <EntityPicker options={[{ id: 'shipped', label: 'Live now' }, { id: 'in_progress', label: 'Coming shortly' }]}
                value={editing.status || 'shipped'} onChange={v => setEditing({ ...editing, status: (v as any) || 'shipped' })}
                placeholder="Status…" width="100%" />
            </label>
            <label style={{ flex: '0 0 160px' }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Date</div>
              <input type="date" style={inp} value={editing.released_at}
                onChange={e => setEditing({ ...editing, released_at: e.target.value })} />
            </label>
          </div>
          <label style={{ display: 'block', marginTop: 10 }}>
            <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>
              What it means for them (1–3 sentences, no jargon)
            </div>
            <textarea style={{ ...inp, minHeight: 70, fontFamily: 'inherit' }} value={editing.body || ''}
              onChange={e => setEditing({ ...editing, body: e.target.value })} />
          </label>
          <label style={{ display: 'block', marginTop: 10 }}>
            <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Where to see it (optional)</div>
            <input style={inp} value={editing.deep_link || ''} placeholder="/closing/envelope-payout"
              onChange={e => setEditing({ ...editing, deep_link: e.target.value })} />
          </label>
          {data.is_super && (
            <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5, marginTop: 12,
              padding: '8px 10px', borderRadius: 8, background: asPlatform ? '#eff6ff' : 'var(--bg2)' }}>
              <input type="checkbox" checked={asPlatform} onChange={e => setAsPlatform(e.target.checked)} />
              <span><b>Platform-wide</b> — every organisation&apos;s admins will see this. Leave it off to post
                an update only your own organisation sees.</span>
            </label>
          )}
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={busy} onClick={save}>
              {busy ? '…' : '📣 Publish'}
            </button>
            <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </div>
      )}

      {/* Filters — pick-don't-type + a date range (RULE THREE / RULE FIVE, adapted: a changelog has no
          store, market or rep dimension). They drive the table AND every export (RULE FOUR). */}
      <div className="card" style={{ padding: '10px 14px', marginBottom: 14, display: 'flex',
        gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <EntityPicker options={CATEGORY_ORDER.map(c => ({ id: c, label: CATEGORY_LABEL[c] }))}
          value={fCat} onChange={setFCat} placeholder="All kinds…" width={190} ariaLabel="Kind" />
        <EntityPicker options={moduleOptions} value={fModule} onChange={setFModule}
          placeholder="All areas…" width={200} ariaLabel="Area" />
        <input type="date" style={{ ...inp, width: 160 }} value={from} aria-label="From date"
          onChange={e => setFrom(e.target.value)} />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>to</span>
        <input type="date" style={{ ...inp, width: 160 }} value={to} aria-label="To date"
          onChange={e => setTo(e.target.value)} />
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text3)' }}>
          {rows.length} of {data.entries.length} update{data.entries.length === 1 ? '' : 's'}
        </span>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          <ReportShell
            title="What's new"
            subtitle={`${rows.length} update${rows.length === 1 ? '' : 's'}${from || to ? ` · ${from || '…'} → ${to || '…'}` : ''}`}
            filename="whats-new"
            columns={[
              { header: 'Date', field: 'released_at', type: 'date', get: (r: any) => r.released_at },
              { header: 'Kind', field: 'kind', get: (r: any) => CATEGORY_LABEL[r.category] || r.category },
              { header: 'Area', field: 'area', get: (r: any) => MODULE_LABEL[r.module || ''] || r.module || '—' },
              { header: 'Update', field: 'title', get: (r: any) => r.title },
              { header: 'What it means', field: 'body', get: (r: any) => r.body || '' },
              { header: 'Status', field: 'status', get: (r: any) => (r.status === 'in_progress' ? 'Coming shortly' : 'Live') },
              { header: 'Scope', field: 'scope', get: (r: any) => (r.org_id === ORG_HOUSE ? 'Platform-wide' : 'This organisation') },
            ]}
            rows={rows} compact stickyHeader
          />

          {data.can_edit && rows.length > 0 && (
            <div className="card" style={{ padding: 14, marginTop: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase',
                letterSpacing: '0.06em', marginBottom: 8 }}>Manage entries</div>
              {rows.map(n => (
                <div key={n.slug} style={{ display: 'flex', gap: 8, alignItems: 'center',
                  borderTop: '1px solid var(--border)', padding: '7px 0', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 13 }}>{CATEGORY_ICON[n.category]} {n.title}</span>
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>{n.released_at}</span>
                  {n.org_id === ORG_HOUSE && <span className="badge" style={{ fontSize: 10.5 }}>Platform-wide</span>}
                  <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                    <button className="btn btn-secondary" style={{ fontSize: 11.5 }}
                      onClick={() => { setEditing({ ...n }); setAsPlatform(!!data.is_super && n.org_id === ORG_HOUSE); setMsg('') }}>
                      Edit
                    </button>
                    <button className="btn btn-secondary" style={{ fontSize: 11.5, color: '#b42318' }}
                      disabled={busy} onClick={() => remove(n)}>Remove</button>
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
