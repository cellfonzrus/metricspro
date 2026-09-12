'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, getActiveOrg } from '@/lib/client'
import { WorkflowNext } from '@/components/WorkflowNext'
import { useCanOpen } from '@/components/ScreenLink'

// Setup Wizard — an ADAPTIVE onboarding questionnaire. It starts by asking who you are (company, carrier,
// POS, payment processor); those answers TAILOR which later steps + menus appear. Then stores/team,
// connections, data feeds, mapping, and pay/goals — each deep-linking to its existing page. Leads with the
// data-first view (what's ingested → what it powers). State persists in onboarding_state; config never lives
// here — every real answer writes through its own settings page. DISPLAY/config.
//
// ── AND IT IS THE IMPLEMENTATION SPINE (owner 2026-09-12) ───────────────────────────────────────
// Owner: "we need to organize the set up of a new tenant in an organized way, right now we have too
// many modules which do not have a flow and one thing leads to the other by links on their
// respective pages to a different module altogether, the implementation wizard should only give
// options relevant to the carrier they are working with with an option to add a carrier and then
// surfacing their respective upload links and automation links, the automation links could be
// linked to the upload links."
//
// THE COMPLAINT IS THE SIBLING WIZARDS, SO THIS PAGE ADDS NONE. There were already five setup
// screens plus /commcalc/implementation; a sixth would be the defect with a new name. This screen
// already WAS the adaptive questionnaire with persistence, so it is the spine and the rest become
// stages within it — announced by the ordered flow strip below and by WorkflowNext at the foot.
//
// Everything carrier-scoped here is rendered from `wiz.implementation`, which the backend builds in
// implementation_spine.py from commcalc.carrier + report_definitions + connector_instances. NO
// CARRIER IS NAMED IN THIS FILE and none may be: a carrier name reaching this file would mean the
// scoping had moved back into code. The upload/automation pairing is likewise not computed here —
// a report row already names its connector, and this only draws what that row says.
const orgQ = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

export default function OnboardingPage() {
  const [wiz, setWiz] = useState<any>(null)
  const [dr, setDr] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [saving, setSaving] = useState('')

  const load = useCallback(() => {
    setLoading(true); setErr(null)
    Promise.all([
      api(`/api/v1/commcalc/onboarding${orgQ()}`),
      api(`/api/v1/commcalc/data-readiness${orgQ()}`).catch(() => null),
    ]).then(([w, d]: any[]) => { setWiz(w); setDr(d) })
      .catch(e => setErr(e?.message || String(e))).finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const put = async (step: string, payload: any) => {
    setSaving(step)
    try {
      await api(`/api/v1/commcalc/onboarding/${encodeURIComponent(step)}${orgQ()}`,
        { method: 'PUT', body: JSON.stringify(payload) })
      load()
    } catch (e: any) { setErr(e?.message || String(e)) } finally { setSaving('') }
  }

  const steps: any[] = wiz?.steps || []
  const profileStep = steps.find(s => s.kind === 'profile')
  const answers = profileStep?.answers || {}
  const flowSteps = steps.filter(s => s.kind !== 'profile')
  const ready = wiz?.ready ?? 0, total = wiz?.total ?? 0
  const pct = total ? Math.round((ready / total) * 100) : 0

  // group tailored steps by phase, preserving order
  const phases: { name: string; steps: any[] }[] = []
  for (const s of flowSteps) {
    let p = phases.find(x => x.name === s.phase)
    if (!p) { p = { name: s.phase, steps: [] }; phases.push(p) }
    p.steps.push(s)
  }

  const impl = wiz?.implementation
  const saveProfile = (patch: any) => put('profile', { answers: { ...answers, ...patch }, status: 'in_progress' })
  const toggleCarrier = (c: string) => {
    const cur: string[] = Array.isArray(answers.carriers) ? answers.carriers : []
    saveProfile({ carriers: cur.includes(c) ? cur.filter(x => x !== c) : [...cur, c] })
  }
  const inp: React.CSSProperties = { padding: '7px 10px', fontSize: 13, border: '1px solid var(--border)', borderRadius: 8 }

  return (
    <div style={{ padding: '18px 22px', maxWidth: 940 }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>Setup Wizard</h1>
      <p className="pg-note" style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 6, marginBottom: 14, maxWidth: 780 }}>
        Answer a few questions and we&rsquo;ll tailor the setup to your business — only the steps that apply to
        your carrier, POS and processor. Each step links to where you complete it; green means done.
      </p>

      {err && <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b', borderRadius: 8, padding: '9px 12px', fontSize: 12.5, marginBottom: 10 }}>❌ {err}</div>}
      {loading && <div style={{ color: 'var(--text3)', fontSize: 13, padding: 20 }}>Loading…</div>}

      {/* THE ORDERED FLOW. Read from the backend spine, which is pinned equal to the runbook's own
          stage list — so the strip, the "what next" prompt and the training material are one order. */}
      {!loading && impl?.spine?.length > 0 && <FlowStrip spine={impl.spine} here="/commcalc/onboarding" />}

      {/* Progress */}
      {!loading && !err && (
        <div style={{ marginBottom: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 4 }}>
            <span style={{ fontWeight: 700 }}>{ready} of {total} steps done</span><span style={{ color: 'var(--text3)' }}>{pct}%</span>
          </div>
          <div style={{ height: 10, background: 'var(--surface2, #eef2f7)', borderRadius: 999, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: pct === 100 ? '#16a34a' : '#2563eb', transition: 'width .3s' }} />
          </div>
        </div>
      )}

      {/* Step 1 — the adaptive PROFILE questionnaire */}
      {!loading && profileStep && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', marginBottom: 18, background: '#f8fbff' }}>
          <div style={{ fontSize: 14, fontWeight: 800, marginBottom: 2 }}>{profileStep.phase}</div>
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 12 }}>{profileStep.question}</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            {(profileStep.options || []).map((q: any) => (
              <div key={q.key}>
                <label style={{ fontSize: 12, fontWeight: 700, display: 'block', marginBottom: 4 }}>{q.label}</label>
                {q.type === 'text' && (
                  <input style={{ ...inp, width: '100%' }} defaultValue={answers[q.key] || ''}
                    onBlur={e => { if (e.target.value !== (answers[q.key] || '')) saveProfile({ [q.key]: e.target.value }) }}
                    placeholder="Type and tab out to save" />
                )}
                {q.type === 'select' && (
                  <select style={{ ...inp, width: '100%' }} value={answers[q.key] || ''} onChange={e => saveProfile({ [q.key]: e.target.value })}>
                    <option value="">Select…</option>
                    {(q.options || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
                  </select>
                )}
                {q.type === 'multiselect' && (
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {(q.options || []).map((o: string) => {
                      const on = Array.isArray(answers[q.key]) && answers[q.key].includes(o)
                      return (
                        <button key={o} onClick={() => toggleCarrier(o)} disabled={saving === 'profile'}
                          style={{ fontSize: 12.5, fontWeight: 700, padding: '6px 12px', borderRadius: 999, cursor: 'pointer',
                            border: '1px solid ' + (on ? '#2563eb' : 'var(--border)'), background: on ? '#2563eb' : 'transparent', color: on ? '#fff' : 'var(--text2)' }}>
                          {on ? '✓ ' : ''}{o}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
          {!profileStep.done && <div style={{ fontSize: 12, color: '#92400e', marginTop: 10 }}>Answer all four to unlock the tailored steps below.</div>}
        </div>
      )}

      {/* CARRIER-SCOPED WORK. Every card below belongs to a carrier this tenant actually runs, and
          each report shows BOTH ways it can arrive. Rendered even before the profile is complete:
          adding a carrier is the action that unblocks the profile, so hiding it behind the profile
          would be the gate-deadlocks-its-own-task shape (§23l) all over again. */}
      {!loading && impl && (
        <CarrierFlow impl={impl} onChanged={load} setErr={setErr} />
      )}

      {/* Tailored steps, grouped by phase, prereq-gated */}
      {!loading && profileStep?.done && phases.map(ph => (
        <div key={ph.name} style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 800, margin: '2px 0 8px', color: 'var(--text2)' }}>{ph.name}</div>
          {ph.steps.map(s => {
            const locked = !s.unlocked
            const done = s.done
            return (
              <div key={s.key} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', border: '1px solid var(--border)',
                borderRadius: 10, padding: '11px 14px', marginBottom: 9, opacity: locked ? 0.55 : 1,
                background: done ? 'var(--surface)' : (locked ? 'var(--surface)' : '#fffdf5') }}>
                <div style={{ flexShrink: 0, width: 24, height: 24, borderRadius: 999, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 13, fontWeight: 800, background: done ? '#dcfce7' : '#fef3c7', color: done ? '#166534' : '#92400e' }}>{done ? '✓' : (locked ? '🔒' : '•')}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 13.5, fontWeight: 700 }}>{s.title}</span>
                    <span style={{ fontSize: 11, fontWeight: 700, borderRadius: 6, padding: '1px 7px',
                      background: done ? '#dcfce7' : '#fef3c7', color: done ? '#166534' : '#92400e' }}>
                      {done ? `Done${s.count ? ` · ${s.count}` : ''}` : (s.status === 'skipped' ? 'Skipped' : 'To do')}
                    </span>
                    {s.kind === 'gate' && !done && <span style={{ fontSize: 11, color: '#b45309' }}>required</span>}
                  </div>
                  <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 3 }}>{s.question}</div>
                  {locked && <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 3 }}>Complete the earlier step(s) first.</div>}
                </div>
                <div style={{ flexShrink: 0, display: 'flex', gap: 8, alignItems: 'center' }}>
                  {/* review-based steps (no automatic probe) get a Mark done / Skip */}
                  {!s.auto && !done && !locked && (
                    <>
                      <button onClick={() => put(s.key, { status: 'reviewed' })} disabled={saving === s.key}
                        style={{ fontSize: 12, fontWeight: 700, padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'transparent', cursor: 'pointer' }}>
                        {saving === s.key ? '…' : 'Mark done'}
                      </button>
                      {s.kind !== 'gate' && (
                        <button onClick={() => put(s.key, { status: 'skipped' })} disabled={saving === s.key}
                          style={{ fontSize: 12, padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text3)', cursor: 'pointer' }}>Skip</button>
                      )}
                    </>
                  )}
                  {s.cta && !locked && (
                    <Link href={s.cta.href} style={{ fontSize: 12.5, fontWeight: 700, textDecoration: 'none', padding: '7px 12px', borderRadius: 8,
                      background: done ? 'transparent' : 'var(--accent, #2563eb)', color: done ? 'var(--text2)' : '#fff', border: done ? '1px solid var(--border)' : 'none' }}>
                      {done ? 'Review' : s.cta.label} →
                    </Link>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ))}

      {/* DATA-FIRST view — what's ingested → what it powers, and which reports are blocked. */}
      {!loading && dr && (
        <div style={{ marginTop: 22 }}>
          <h2 style={{ fontSize: 15, fontWeight: 800, margin: '0 0 6px' }}>
            Your data <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12.5 }}>({dr.ingested_count ?? 0} feeds ingested · {dr.reports_powered ?? 0}/{dr.reports_total ?? 0} reports powered)</span>
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
              <div style={{ background: 'var(--surface2, #f8fafc)', padding: '7px 11px', fontSize: 12, fontWeight: 700 }}>Ingested feeds</div>
              {(dr.ingested || []).map((s: any) => (
                <div key={s.source_key} style={{ padding: '7px 11px', borderTop: '1px solid var(--border)', fontSize: 12.5 }}>
                  <span style={{ marginRight: 6 }}>{s.present ? '✅' : '⬜'}</span><b>{s.source_label}</b>
                  {(s.reports || []).length > 0 && <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 2, marginLeft: 20 }}>powers: {(s.reports || []).slice(0, 5).join(' · ')}</div>}
                </div>
              ))}
            </div>
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
              <div style={{ background: 'var(--surface2, #f8fafc)', padding: '7px 11px', fontSize: 12, fontWeight: 700 }}>Reports &amp; menus</div>
              {(dr.reports || []).map((r: any, i: number) => (
                <div key={i} style={{ padding: '7px 11px', borderTop: '1px solid var(--border)', fontSize: 12.5 }}>
                  <span style={{ marginRight: 6 }}>{r.powered ? '🟢' : '🔴'}</span><b>{r.report}</b>
                  {!r.powered && (r.needs || []).length > 0 && <div style={{ fontSize: 11.5, color: '#92400e', marginTop: 2, marginLeft: 20 }}>needs: {(r.needs || []).join(' · ')}</div>}
                </div>
              ))}
            </div>
          </div>
          <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text3)' }}>
            Full dependency map: <Link href="/commcalc/schematic" style={{ color: 'var(--text2)', fontWeight: 700 }}>System Schematic</Link>.
          </div>
        </div>
      )}

      {/* Ask the chart what comes next — the same component and the same stage list the runbook uses. */}
      <WorkflowNext here="/commcalc/onboarding" />
    </div>
  )
}

// ── THE ORDERED FLOW STRIP ──────────────────────────────────────────────────────────────────────
// The sequence, drawn once at the top so the implementation reads as a flow rather than a page of
// links. It renders the BACKEND's spine (pinned equal to the runbook's stages), never a third copy
// of the order typed here.
//
// Every step gates on the destination's OWN nav entry via the shared `useCanOpen` — the same
// predicate the sidebar, ScreenLink and WorkflowNext use. A step this viewer may not open is shown
// as plain text rather than a link they would be bounced out of, and is never silently dropped: a
// flow with a hole in it reads as a mistake, whereas a greyed step reads as "not yours".
function FlowStrip({ spine, here }: { spine: any[]; here: string }) {
  const canOpen = useCanOpen()
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 12px', marginBottom: 16,
      background: 'var(--surface)' }}>
      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.09em',
        color: 'var(--text3)', marginBottom: 8, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <span>The implementation, in order</span>
        <Link href="/training/flowcharts/tenant-implementation"
          style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text3)', textDecoration: 'none', fontWeight: 600 }}>
          🗺️ See the whole flow →
        </Link>
      </div>
      <ol style={{ display: 'flex', gap: 6, flexWrap: 'wrap', listStyle: 'none', margin: 0, padding: 0 }}>
        {spine.map((s: any, i: number) => {
          const open = canOpen(s.href)
          const cur = s.href === here
          const body = (
            <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 6, fontSize: 12.5,
              fontWeight: cur ? 800 : 600, color: cur ? 'var(--accent)' : (open ? 'var(--text2)' : 'var(--text3)') }}>
              <span style={{ fontSize: 10.5, opacity: .7 }}>{i + 1}</span>{s.label}
            </span>
          )
          return (
            <li key={s.href} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span title={s.does} style={{ padding: '4px 9px', borderRadius: 999,
                border: `1px solid ${cur ? 'var(--accent)' : 'var(--border)'}`,
                background: cur ? 'var(--surface2, #eef2ff)' : 'transparent' }}>
                {open ? <Link href={s.href} style={{ textDecoration: 'none' }}>{body}</Link> : body}
              </span>
              {i < spine.length - 1 && <span style={{ color: 'var(--text3)', fontSize: 11 }}>→</span>}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

// ── ONE CARRIER'S WORK, WITH ITS AUTOMATION BESIDE ITS UPLOAD ───────────────────────────────────
// The owner's last clause — "the automation links could be linked to the upload links" — is drawn
// here and computed nowhere: `report_definitions.connector_id` has pointed each report at the
// connector that fetches it since mig 039. This renders that existing field instead of inventing a
// second way to relate the two.
function CarrierFlow({ impl, onChanged, setErr }:
  { impl: any; onChanged: () => void; setErr: (s: string | null) => void }) {
  const canOpen = useCanOpen()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const blocks: any[] = impl?.blocks || []
  const prog = impl?.progress || {}

  // ADD A CARRIER — a first-class action in the flow, not a trip to a settings page. It posts to the
  // carrier endpoint that already exists; this file stores no carrier config of its own.
  async function addCarrier() {
    const nm = name.trim()
    if (!nm) return
    setBusy(true); setErr(null)
    try {
      await api(`/api/v1/commcalc/carriers${orgQ()}`,
        { method: 'POST', body: JSON.stringify({ name: nm, code: code.trim() || undefined }) })
      setName(''); setCode(''); setAdding(false); onChanged()
    } catch (e: any) { setErr(e?.message || String(e)) } finally { setBusy(false) }
  }

  const link = (href: string, label: string, tone: 'primary' | 'plain') => {
    const style: React.CSSProperties = tone === 'primary'
      ? { fontSize: 12, fontWeight: 700, padding: '5px 10px', borderRadius: 7, textDecoration: 'none',
          background: 'var(--accent, #2563eb)', color: '#fff' }
      : { fontSize: 12, fontWeight: 600, padding: '5px 10px', borderRadius: 7, textDecoration: 'none',
          border: '1px solid var(--border)', color: 'var(--text2)' }
    // An href with no nav entry for this viewer degrades to plain text — never a link to a 403.
    return canOpen(href)
      ? <Link href={href} style={style}>{label}</Link>
      : <span style={{ ...style, opacity: .55, background: 'transparent', color: 'var(--text3)',
          border: '1px solid var(--border)' }} title="You do not have access to this screen">{label}</span>
  }

  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <h2 style={{ fontSize: 15, fontWeight: 800, margin: 0 }}>Your carriers</h2>
        <span style={{ fontSize: 12.5, color: 'var(--text3)' }}>
          {prog.feeds_mapped ?? 0}/{prog.feeds ?? 0} reports mapped · {prog.feeds_automated ?? 0} automated
        </span>
        <span style={{ flex: 1 }} />
        {!adding && (
          <button onClick={() => setAdding(true)}
            style={{ fontSize: 12.5, fontWeight: 700, padding: '6px 12px', borderRadius: 8, cursor: 'pointer',
              border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)' }}>
            ＋ Add a carrier
          </button>
        )}
      </div>

      {adding && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 12px', marginBottom: 12,
          display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', background: 'var(--surface)' }}>
          <input autoFocus value={name} onChange={e => setName(e.target.value)} placeholder="Carrier name"
            onKeyDown={e => { if (e.key === 'Enter') addCarrier(); if (e.key === 'Escape') setAdding(false) }}
            style={{ padding: '7px 10px', fontSize: 13, border: '1px solid var(--border)', borderRadius: 8, width: 200 }} />
          <input value={code} onChange={e => setCode(e.target.value)} placeholder="Short code (optional)"
            style={{ padding: '7px 10px', fontSize: 13, border: '1px solid var(--border)', borderRadius: 8, width: 160 }} />
          <button onClick={addCarrier} disabled={busy || !name.trim()}
            style={{ fontSize: 12.5, fontWeight: 700, padding: '7px 12px', borderRadius: 8, cursor: 'pointer',
              border: 'none', background: 'var(--accent, #2563eb)', color: '#fff' }}>
            {busy ? '…' : 'Add carrier'}
          </button>
          <button onClick={() => setAdding(false)}
            style={{ fontSize: 12.5, padding: '7px 10px', borderRadius: 8, cursor: 'pointer',
              border: '1px solid var(--border)', background: 'transparent', color: 'var(--text3)' }}>Cancel</button>
          <span style={{ fontSize: 11.5, color: 'var(--text3)', flexBasis: '100%' }}>
            Adding a carrier creates a config row. Its reports are registered next, on Connectors —
            nothing about a carrier lives in code.
          </span>
        </div>
      )}

      {blocks.length === 0 && (
        <div style={{ border: '1px dashed var(--border)', borderRadius: 10, padding: '14px 16px', fontSize: 12.5,
          color: 'var(--text2)' }}>
          No carriers yet. Add the carrier this tenant sells — every step below is scoped to it, so
          nothing else can be narrowed until one exists.
        </div>
      )}

      {blocks.map((b: any) => (
        <div key={b.carrier_id || '__shared__'} style={{ border: '1px solid var(--border)', borderRadius: 10,
          marginBottom: 10, overflow: 'hidden' }}>
          <div style={{ background: 'var(--surface2, #f8fafc)', padding: '8px 12px', display: 'flex',
            gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <b style={{ fontSize: 13.5 }}>{b.carrier_name}</b>
            {b.is_default && <span style={{ fontSize: 10.5, fontWeight: 700, color: 'var(--text3)' }}>DEFAULT</span>}
            {b.shared && <span style={{ fontSize: 11, color: 'var(--text3)' }}>not tied to one carrier</span>}
            <span style={{ flex: 1 }} />
            <span style={{ fontSize: 11.5, color: 'var(--text3)' }}>{b.ready}/{b.total} mapped · {b.automated} automated</span>
          </div>

          {/* A carrier with nothing registered is NAMED and told what to do. Dropping it would make an
              unfinished implementation look finished. */}
          {b.total === 0 && (
            <div style={{ padding: '11px 13px', fontSize: 12.5, color: 'var(--text2)' }}>
              {b.empty_reason} {b.empty_next}
              <div style={{ marginTop: 8 }}>{link('/commcalc/connectors', 'Register its reports →', 'primary')}</div>
            </div>
          )}

          {b.items.map((it: any) => (
            <div key={it.report_key} style={{ padding: '10px 13px', borderTop: '1px solid var(--border)',
              display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={{ minWidth: 210, flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 700 }}>{it.label}</div>
                <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 2 }}>
                  {it.mapping.ready
                    ? '✅ every required field is mapped'
                    : it.mapping.required
                      ? `${it.mapping.required_mapped}/${it.mapping.required} required fields mapped`
                      : 'no field registry — map it on Column Mapping'}
                  {it.mapping.sample_seen ? ' · a sample has been through the mapper' : ''}
                </div>
              </div>
              {/* THE PAIR. Upload on the left, the automation that replaces it on the right — because
                  the report row names both. */}
              {link(canOpen(it.upload.href) ? it.upload.href : (it.upload.fallback_href || it.upload.href),
                '⬆️ Upload', it.mapping.ready ? 'plain' : 'primary')}
              {it.automation ? (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                  {link(it.automation.href,
                    it.automation.state === 'on' ? '🤖 Automated' : '🤖 Automate', 'plain')}
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>
                    {it.automation.vendor}
                    {it.automation.state === 'manual_only' ? ' · manual only' : ''}
                    {it.automation.state === 'off' ? ' · not pulling this yet' : ''}
                  </span>
                </span>
              ) : (
                <span style={{ fontSize: 11.5, color: 'var(--text3)' }}>
                  no source registered — {link('/commcalc/connectors', 'add one', 'plain')}
                </span>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
