'use client'
// ── TRAINING CENTER (mig 720, owner directive 2026-08-04) ────────────────────────────────────────
// "need to create simulation training videos for all modules to walk the users through."
//
// PHASE 1 (this page): every guided walk-through in one place, grouped by module. "Start" launches the
// tour on the REAL page — the app spotlights each control and explains it while the user clicks
// through it themselves. Progress is remembered so a rep can see what they have already done.
//
// PHASE 2 (the "Recording scripts" tab, admins only): the same tours rendered as a shooting script —
// scene by scene, what is on screen, the narration line, and the camera action — exportable to Excel /
// PDF / email / WhatsApp through ReportShell (RULE FOUR). That export IS the source material for the
// recorded videos; nothing here records anything.
//
// MULTI-TENANT: the list comes from GET /api/v1/core/training/tours, which returns the platform
// defaults ∪ this tenant's own tours with the tenant's version winning. A tenant never sees another
// tenant's tours. FAIL-SILENT: an un-run migration renders an honest empty state, never an error page.
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import EntityPicker from '@/components/EntityPicker'
import ReportShell from '@/components/ReportShell'
import {
  AUDIENCE_LABEL, MODULE_LABEL, Tour, clearTourDone, fetchTours, groupByModule, startTour, tourDoneAt,
} from '@/lib/tours'
import { api } from '@/lib/client'
import { safeHref } from '@/lib/safe-url'   // H6: start_href is tenant-writable tour config

type Scene = {
  scene: number; page: string; anchor: string
  on_screen_title: string; on_screen_body: string; narration: string; camera_action: string
}
type Script = { slug: string; title: string; module?: string; scenes: number; storyboard: Scene[]; narration_text: string }

export default function TrainingCenterPage() {
  const [tours, setTours] = useState<Tour[]>([])
  const [ready, setReady] = useState(true)
  const [canEdit, setCanEdit] = useState(false)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'tours' | 'scripts'>('tours')
  const [scripts, setScripts] = useState<Script[] | null>(null)
  const [doneTick, setDoneTick] = useState(0)          // bumps to re-read localStorage after a change

  // Filters — pick-don't-type (RULE THREE): module and audience are dropdowns over the values that
  // actually exist in the loaded tours, never free text. (RULE FIVE's period/store/rep bar does not
  // apply here: a walk-through catalog has no period, store or rep dimension — see the handoff note.)
  const [fModule, setFModule] = useState<string | null>(null)
  const [fAudience, setFAudience] = useState<string | null>(null)
  const [q, setQ] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    fetchTours().then(r => { setTours(r.tours); setReady(r.ready); setCanEdit(r.can_edit) })
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (tab !== 'scripts' || scripts !== null || !canEdit) return
    api('/api/v1/core/training/scripts').then(r => setScripts(r?.scripts || [])).catch(() => setScripts([]))
  }, [tab, scripts, canEdit])

  const moduleOptions = useMemo(() => {
    const keys = Array.from(new Set(tours.map(t => (t.module || 'other'))))
    return keys.map(k => ({ id: k, label: MODULE_LABEL[k] || k }))
  }, [tours])
  const audienceOptions = useMemo(() => {
    const keys = Array.from(new Set(tours.map(t => (t.audience || 'all'))))
    return keys.map(k => ({ id: k, label: AUDIENCE_LABEL[k] || k }))
  }, [tours])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return tours.filter(t => {
      if (fModule && (t.module || 'other') !== fModule) return false
      if (fAudience && (t.audience || 'all') !== fAudience) return false
      if (needle && !`${t.title} ${t.description || ''}`.toLowerCase().includes(needle)) return false
      return true
    })
  }, [tours, fModule, fAudience, q])

  const groups = useMemo(() => groupByModule(filtered), [filtered])
  const doneCount = useMemo(() => {
    void doneTick
    return tours.filter(t => tourDoneAt(t.slug)).length
  }, [tours, doneTick])

  const scriptRows = useMemo(() => {
    if (!scripts) return []
    const rows: any[] = []
    scripts.forEach(s => (s.storyboard || []).forEach(sc => rows.push({
      walkthrough: s.title, scene: sc.scene, page: sc.page,
      on_screen: `${sc.on_screen_title} — ${sc.on_screen_body}`,
      narration: sc.narration, camera_action: sc.camera_action, anchor: sc.anchor,
    })))
    return rows
  }, [scripts])

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎓 Training Center</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 820, lineHeight: 1.6 }}>
          Guided walk-throughs of the things people actually do in here. Pick one and the app will take
          you through it step by step on the real page — it points at each control and explains it while
          you click through yourself. You can stop at any point and pick it up again later.
        </p>
      </div>

      {canEdit && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
          {(['tours', 'scripts'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)} className="btn"
              style={{ fontSize: 13, background: tab === t ? 'var(--accent)' : undefined,
                color: tab === t ? 'white' : undefined }}>
              {t === 'tours' ? 'Walk-throughs' : 'Recording scripts'}
            </button>
          ))}
          <Link href="/admin/training" className="btn btn-secondary" style={{ fontSize: 13, marginLeft: 'auto' }}>
            ⚙️ Edit walk-throughs
          </Link>
        </div>
      )}

      {tab === 'tours' && (
        <>
          <div className="card" style={{ padding: '10px 14px', marginBottom: 14, display: 'flex',
            gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <EntityPicker options={moduleOptions} value={fModule} onChange={setFModule}
              placeholder="All areas…" width={200} ariaLabel="Area" />
            <EntityPicker options={audienceOptions} value={fAudience} onChange={setFAudience}
              placeholder="Anyone…" width={180} ariaLabel="Who it's for" />
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search walk-throughs…"
              aria-label="Search walk-throughs"
              style={{ padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)',
                fontSize: 13, background: 'var(--surface)', width: 230 }} />
            <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text3)' }}>
              {tours.length} walk-through{tours.length === 1 ? '' : 's'} · {doneCount} completed
            </span>
          </div>

          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
          ) : !ready ? (
            <div className="card" style={{ padding: 26, textAlign: 'center', color: 'var(--text3)', fontSize: 13.5, lineHeight: 1.7 }}>
              The Training Center isn&apos;t switched on for this system yet.<br />
              An administrator needs to run the one-off database step (migration 720); the walk-throughs
              then load themselves the next time anyone signs in.
            </div>
          ) : filtered.length === 0 ? (
            <div className="card" style={{ padding: 26, textAlign: 'center', color: 'var(--text3)', fontSize: 13.5 }}>
              No walk-throughs match those filters.
            </div>
          ) : groups.map(g => (
            <div key={g.key} style={{ marginBottom: 22 }}>
              <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase',
                color: 'var(--text3)', marginBottom: 8 }}>{g.label}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px,1fr))', gap: 12 }}>
                {g.tours.map(t => {
                  const done = tourDoneAt(t.slug)
                  return (
                    <div key={t.slug} className="card" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                        <div style={{ fontSize: 15, fontWeight: 700, flex: 1 }}>{t.title}</div>
                        {done && <span className="badge" style={{ fontSize: 11, background: '#e7f6ec', color: '#16794a' }}>✓ Done</span>}
                        {t.is_tenant_override && (
                          <span className="badge" style={{ fontSize: 11 }} title="Your organisation has its own version of this walk-through">Custom</span>
                        )}
                      </div>
                      {t.description && (
                        <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.55 }}>{t.description}</div>
                      )}
                      <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
                        {t.step_count || 0} steps
                        {t.est_minutes ? ` · about ${t.est_minutes} min` : ''}
                        {t.audience && t.audience !== 'all' ? ` · ${AUDIENCE_LABEL[t.audience] || t.audience}` : ''}
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 'auto', paddingTop: 8 }}>
                        <button className="btn btn-primary" style={{ fontSize: 13 }}
                          onClick={() => startTour(t.slug)}>
                          {done ? '↻ Take it again' : '▶︎ Start walk-through'}
                        </button>
                        {done && (
                          <button className="btn btn-secondary" style={{ fontSize: 12 }}
                            title="Remove the completed tick for this walk-through"
                            onClick={() => { clearTourDone(t.slug); setDoneTick(x => x + 1) }}>
                            Clear tick
                          </button>
                        )}
                        {safeHref(t.start_href) && (
                          <Link href={safeHref(t.start_href, '#')} style={{ fontSize: 12, color: 'var(--text3)', textDecoration: 'none' }}
                            title="Open the page without the walk-through">
                            Just open the page →
                          </Link>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </>
      )}

      {tab === 'scripts' && canEdit && (
        <>
          <div className="card" style={{ padding: 14, marginBottom: 14, fontSize: 13, color: 'var(--text2)', lineHeight: 1.65 }}>
            <b>Recording scripts (for producing the videos).</b> Every walk-through, scene by scene: the
            page it happens on, what the viewer sees on screen, the line to narrate, and what to do with
            the cursor. Export it and hand it to whoever records the screen — the wording never has to be
            written twice, and re-recording after a change means re-exporting this, not rewriting a script.
          </div>
          {scripts === null ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
          ) : (
            <ReportShell
              title="Walk-through recording scripts"
              subtitle={`${scripts.length} walk-throughs · ${scriptRows.length} scenes`}
              filename="training-recording-scripts"
              columns={[
                { header: 'Walk-through', field: 'walkthrough', get: (r: any) => r.walkthrough },
                { header: 'Scene', field: 'scene', type: 'number', get: (r: any) => r.scene },
                { header: 'Page', field: 'page', get: (r: any) => r.page },
                { header: 'On screen', field: 'on_screen', get: (r: any) => r.on_screen },
                { header: 'Narration', field: 'narration', get: (r: any) => r.narration },
                { header: 'Camera action', field: 'camera_action', get: (r: any) => r.camera_action },
                { header: 'Anchor', field: 'anchor', get: (r: any) => r.anchor },
              ]}
              rows={scriptRows}
              compact stickyHeader defaultGroupBy="walkthrough" collapsibleGroups
            />
          )}
        </>
      )}
    </div>
  )
}
