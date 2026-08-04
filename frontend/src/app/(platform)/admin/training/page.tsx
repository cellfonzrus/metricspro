'use client'
// ── WALK-THROUGH EDITOR (mig 720, owner directive 2026-08-04) ────────────────────────────────────
// Tours are DATA (RULE TWO): every word a user reads in a guided walk-through is editable here — no
// deploy, no developer. A tenant can reword a shipped walk-through for its own process, add its own,
// reorder the steps, or unpublish one.
//
// WHAT "SAVE" DOES, DEPENDING ON WHO YOU ARE (RULE ONE):
//   • a super-admin with "Platform default" ON  → writes the HOUSE row: EVERY tenant sees the change.
//   • a super-admin with it OFF, or a tenant admin → writes THIS organisation's own row. If it has the
//     same short name (slug) as a platform walk-through, this organisation now sees its own version and
//     every other tenant keeps the original. Delete the copy and the platform version comes back.
// The org is decided by the SERVER from the caller's membership; nothing in this form can change it.
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import EntityPicker from '@/components/EntityPicker'
import { api } from '@/lib/client'
import { NAV } from '@/lib/rbac'
import { AUDIENCE_LABEL, MODULE_LABEL, Tour, TourStep, fetchTours, startTour } from '@/lib/tours'

const ORG_HOUSE = '00000000-0000-0000-0000-000000000001'
const inp: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)',
  fontSize: 13, background: 'var(--surface)', width: '100%' }

const blankStep = (order: number): TourStep => ({
  step_order: order, page_href: null, target: null, target_fragile: false, placement: 'auto',
  title: '', body: '', narration: '', action_hint: '',
})

export default function AdminTrainingPage() {
  const [tours, setTours] = useState<Tour[]>([])
  const [ready, setReady] = useState(true)
  const [canEdit, setCanEdit] = useState(false)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const [editing, setEditing] = useState<Tour | null>(null)
  const [steps, setSteps] = useState<TourStep[]>([])
  const [asPlatform, setAsPlatform] = useState(false)
  const [isSuper, setIsSuper] = useState(false)

  // Pick-don't-type (RULE THREE): pages come from the real nav, not typed by hand. A sub-path the nav
  // doesn't list (e.g. a detail page) is still reachable through the "create" affordance.
  const pageOptions = useMemo(() => {
    const seen = new Map<string, string>()
    NAV.forEach(g => g.items.forEach(i => { if (!seen.has(i.href)) seen.set(i.href, `${i.label} — ${i.href}`) }))
    return [...seen.entries()].map(([href, label]) => ({ id: href, label }))
  }, [])
  const moduleOptions = useMemo(() =>
    Object.entries(MODULE_LABEL).map(([k, v]) => ({ id: k, label: v })), [])
  const audienceOptions = useMemo(() =>
    Object.entries(AUDIENCE_LABEL).map(([k, v]) => ({ id: k, label: v })), [])

  const load = useCallback(() => {
    setLoading(true)
    fetchTours().then(r => { setTours(r.tours); setReady(r.ready); setCanEdit(r.can_edit) })
      .finally(() => setLoading(false))
    api('/api/v1/core/me').then((m: any) => setIsSuper(!!m?.super_admin)).catch(() => setIsSuper(false))
  }, [])
  useEffect(() => { load() }, [load])

  async function openTour(slug: string) {
    setMsg('')
    try {
      const r: any = await api(`/api/v1/core/training/tours/${encodeURIComponent(slug)}`)
      setEditing(r.tour)
      setSteps((r.steps || []).map((s: any, i: number) => ({ ...s, step_order: i + 1 })))
      setAsPlatform(isSuper && r.tour?.org_id === ORG_HOUSE)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  function newTour() {
    setEditing({ slug: '', title: '', module: 'closing', audience: 'all', description: '',
      start_href: '', est_minutes: null, sort_order: 100, is_published: true })
    setSteps([blankStep(1)])
    setAsPlatform(false)
    setMsg('')
  }

  async function save() {
    if (!editing) return
    if (!editing.title.trim()) { setMsg('❌ Give the walk-through a title.'); return }
    if (!steps.length || steps.some(s => !s.title.trim() || !s.body.trim())) {
      setMsg('❌ Every step needs a heading and some text.'); return
    }
    setBusy(true); setMsg('')
    const org = (isSuper && asPlatform) ? ORG_HOUSE : ''
    try {
      const r: any = await api(`/api/v1/core/training/tours${org ? `?org_id=${org}` : ''}`, {
        method: 'POST',
        body: JSON.stringify({ ...editing, steps: steps.map((s, i) => ({ ...s, step_order: i + 1 })) }),
      })
      setMsg(r?.is_platform_default
        ? '✅ Saved as a platform default — every organisation sees this.'
        : '✅ Saved for your organisation.')
      load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  async function remove(t: Tour) {
    if (!t.id) return
    if (!window.confirm(`Delete "${t.title}"? If this is your organisation's own version of a shipped walk-through, the original comes back.`)) return
    setBusy(true); setMsg('')
    const org = (isSuper && t.org_id === ORG_HOUSE) ? ORG_HOUSE : ''
    try {
      await api(`/api/v1/core/training/tours/${t.id}${org ? `?org_id=${org}` : ''}`, { method: 'DELETE' })
      setMsg('✅ Deleted.')
      setEditing(null); setSteps([]); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  async function reseed() {
    if (!window.confirm('Reload the walk-throughs that ship with MetricsPro? Anything you have edited is left alone.')) return
    setBusy(true); setMsg('')
    try {
      const r: any = await api('/api/v1/core/training/seed', { method: 'POST' })
      setMsg(`✅ Reloaded — ${r?.inserted || 0} added, ${r?.updated || 0} refreshed, ${r?.skipped || 0} left alone (edited).`)
      load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  const setStep = (i: number, patch: Partial<TourStep>) =>
    setSteps(ss => ss.map((s, j) => (j === i ? { ...s, ...patch } : s)))
  const moveStep = (i: number, dir: -1 | 1) => setSteps(ss => {
    const j = i + dir
    if (j < 0 || j >= ss.length) return ss
    const out = [...ss]; const tmp = out[i]; out[i] = out[j]; out[j] = tmp
    return out
  })

  if (!loading && !canEdit) {
    return (
      <div className="card" style={{ padding: 26, fontSize: 13.5, color: 'var(--text2)', lineHeight: 1.7 }}>
        Editing walk-throughs is restricted to administrators. You can still take any walk-through from the{' '}
        <Link href="/training" style={{ color: 'var(--accent)' }}>Training Center</Link>.
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
        gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎓 Walk-through editor</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 780, lineHeight: 1.6 }}>
            Every word a user reads in a guided walk-through lives here. Reword a shipped one for the way
            your business does it, or write your own — nothing here needs a developer.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Link href="/training" className="btn btn-secondary" style={{ fontSize: 13 }}>← Training Center</Link>
          {isSuper && <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={busy} onClick={reseed}>↻ Reload shipped set</button>}
          <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={newTour}>＋ New walk-through</button>
        </div>
      </div>

      {msg && <div className="card" style={{ padding: '9px 13px', marginBottom: 12, fontSize: 13 }}>{msg}</div>}

      {!ready && (
        <div className="card" style={{ padding: 20, marginBottom: 14, fontSize: 13.5, color: 'var(--text2)', lineHeight: 1.7 }}>
          The walk-through tables aren&apos;t set up on this system yet (migration 720). Until an
          administrator runs it, nothing can be saved here and the Training Center shows an empty state.
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 340px) 1fr', gap: 16, alignItems: 'start' }}>
        {/* ── list ─────────────────────────────────────────────────────────────────────────────── */}
        <div className="card" style={{ padding: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase',
            letterSpacing: '0.06em', marginBottom: 8 }}>Walk-throughs ({tours.length})</div>
          {loading ? <div className="spinner" /> : tours.map(t => (
            <button key={t.slug} onClick={() => openTour(t.slug)}
              style={{ display: 'block', width: '100%', textAlign: 'left', cursor: 'pointer', marginBottom: 6,
                background: editing?.slug === t.slug ? 'var(--bg2)' : 'transparent',
                border: '1px solid var(--border)', borderRadius: 10, padding: '8px 10px' }}>
              <div style={{ fontSize: 13, fontWeight: 700 }}>{t.title}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
                {MODULE_LABEL[t.module || ''] || t.module || 'Other'} · {t.step_count || 0} steps
                {t.is_tenant_override ? ' · your version' : ''}
                {t.is_published === false ? ' · hidden' : ''}
              </div>
            </button>
          ))}
        </div>

        {/* ── editor ───────────────────────────────────────────────────────────────────────────── */}
        {!editing ? (
          <div className="card" style={{ padding: 30, textAlign: 'center', color: 'var(--text3)', fontSize: 13.5 }}>
            Pick a walk-through on the left to edit it, or start a new one.
          </div>
        ) : (
          <div className="card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
              <label style={{ flex: '1 1 260px' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Title</div>
                <input style={inp} value={editing.title}
                  onChange={e => setEditing({ ...editing, title: e.target.value })}
                  placeholder="e.g. Close out your day" />
              </label>
              <label style={{ flex: '0 0 200px' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Area</div>
                <EntityPicker options={moduleOptions} value={editing.module || null}
                  onChange={v => setEditing({ ...editing, module: v })} placeholder="Area…" width="100%" />
              </label>
              <label style={{ flex: '0 0 190px' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Who it&apos;s for</div>
                <EntityPicker options={audienceOptions} value={editing.audience || 'all'}
                  onChange={v => setEditing({ ...editing, audience: v || 'all' })} placeholder="Everyone…" width="100%" />
              </label>
            </div>

            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
              <label style={{ flex: '1 1 100%' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>One-line description</div>
                <input style={inp} value={editing.description || ''}
                  onChange={e => setEditing({ ...editing, description: e.target.value })}
                  placeholder="What the person will be able to do afterwards" />
              </label>
              <label style={{ flex: '1 1 300px' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Starts on page</div>
                <EntityPicker options={pageOptions} value={editing.start_href || null}
                  onChange={v => setEditing({ ...editing, start_href: v })} allowCreate
                  onCreate={v => setEditing({ ...editing, start_href: v })}
                  placeholder="Page…" width="100%" />
              </label>
              <label style={{ flex: '0 0 130px' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Minutes</div>
                <input style={inp} inputMode="decimal" value={editing.est_minutes ?? ''}
                  onChange={e => setEditing({ ...editing, est_minutes: e.target.value === '' ? null : Number(e.target.value) })} />
              </label>
              <label style={{ flex: '0 0 120px' }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Order</div>
                <input style={inp} inputMode="numeric" value={editing.sort_order ?? 100}
                  onChange={e => setEditing({ ...editing, sort_order: Number(e.target.value) || 100 })} />
              </label>
              <label style={{ display: 'flex', alignItems: 'flex-end', gap: 6, fontSize: 12.5, paddingBottom: 8 }}>
                <input type="checkbox" checked={editing.is_published !== false}
                  onChange={e => setEditing({ ...editing, is_published: e.target.checked })} /> Visible to users
              </label>
            </div>

            {isSuper && (
              <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5, marginBottom: 12,
                padding: '8px 10px', borderRadius: 8, background: asPlatform ? '#eff6ff' : 'var(--bg2)' }}>
                <input type="checkbox" checked={asPlatform} onChange={e => setAsPlatform(e.target.checked)} />
                <span>
                  <b>Platform default</b> — save this for <b>every organisation</b> on MetricsPro.
                  Leave it off to save a version that only your own organisation sees.
                </span>
              </label>
            )}

            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', margin: '6px 0 8px',
              borderTop: '1px solid var(--border)', paddingTop: 12 }}>
              Steps ({steps.length})
            </div>

            {steps.map((s, i) => (
              <div key={i} className="card" style={{ padding: 12, marginBottom: 10, background: 'var(--bg2)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)' }}>Step {i + 1}</span>
                  <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 7px' }}
                    onClick={() => moveStep(i, -1)} disabled={i === 0}>↑</button>
                  <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 7px' }}
                    onClick={() => moveStep(i, 1)} disabled={i === steps.length - 1}>↓</button>
                  <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 7px', marginLeft: 'auto' }}
                    onClick={() => setSteps(ss => ss.filter((_, j) => j !== i))}>✕ Remove</button>
                </div>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  <label style={{ flex: '1 1 260px' }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Page</div>
                    <EntityPicker options={pageOptions} value={s.page_href || null}
                      onChange={v => setStep(i, { page_href: v })} allowCreate
                      onCreate={v => setStep(i, { page_href: v })} placeholder="Page…" width="100%" />
                  </label>
                  <label style={{ flex: '1 1 260px' }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>
                      What to point at (optional)
                    </div>
                    <input style={inp} value={s.target || ''} onChange={e => setStep(i, { target: e.target.value })}
                      placeholder='e.g. text:Submit closing — leave empty for a centered card' />
                  </label>
                  <label style={{ flex: '0 0 150px' }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Card position</div>
                    <EntityPicker options={['auto', 'top', 'bottom', 'left', 'right'].map(x => ({ id: x, label: x }))}
                      value={s.placement || 'auto'} onChange={v => setStep(i, { placement: (v as any) || 'auto' })}
                      placeholder="auto" width="100%" />
                  </label>
                </div>
                <label style={{ display: 'block', marginTop: 8 }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Heading</div>
                  <input style={inp} value={s.title} onChange={e => setStep(i, { title: e.target.value })} />
                </label>
                <label style={{ display: 'block', marginTop: 8 }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>
                    What the person reads
                  </div>
                  <textarea style={{ ...inp, minHeight: 66, fontFamily: 'inherit' }} value={s.body}
                    onChange={e => setStep(i, { body: e.target.value })} />
                </label>
                <details style={{ marginTop: 8 }}>
                  <summary style={{ fontSize: 12, color: 'var(--text3)', cursor: 'pointer' }}>
                    Video script (only used if this walk-through is ever recorded)
                  </summary>
                  <label style={{ display: 'block', marginTop: 8 }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Narration</div>
                    <textarea style={{ ...inp, minHeight: 50, fontFamily: 'inherit' }} value={s.narration || ''}
                      onChange={e => setStep(i, { narration: e.target.value })} />
                  </label>
                  <label style={{ display: 'block', marginTop: 8 }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Camera action</div>
                    <input style={inp} value={s.action_hint || ''}
                      onChange={e => setStep(i, { action_hint: e.target.value })} />
                  </label>
                  <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, marginTop: 8 }}>
                    <input type="checkbox" checked={!!s.target_fragile}
                      onChange={e => setStep(i, { target_fragile: e.target.checked })} />
                    This is pointing at a page another team owns — the anchor may move
                  </label>
                </details>
              </div>
            ))}

            <button className="btn btn-secondary" style={{ fontSize: 13 }}
              onClick={() => setSteps(ss => [...ss, blankStep(ss.length + 1)])}>＋ Add step</button>

            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 16,
              borderTop: '1px solid var(--border)', paddingTop: 12 }}>
              <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={busy} onClick={save}>
                {busy ? '…' : '💾 Save walk-through'}
              </button>
              {editing.slug && (
                <button className="btn btn-secondary" style={{ fontSize: 13 }}
                  onClick={() => startTour(editing.slug)}>▶︎ Try it</button>
              )}
              <button className="btn btn-secondary" style={{ fontSize: 13, marginLeft: 'auto' }}
                onClick={() => { setEditing(null); setSteps([]) }}>Cancel</button>
              {editing.id && (
                <button className="btn btn-secondary" style={{ fontSize: 13, color: '#b42318' }}
                  disabled={busy} onClick={() => remove(editing)}>Delete</button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
