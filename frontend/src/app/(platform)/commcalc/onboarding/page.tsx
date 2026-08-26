'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, getActiveOrg } from '@/lib/client'

// Setup Wizard — an ADAPTIVE onboarding questionnaire. It starts by asking who you are (company, carrier,
// POS, payment processor); those answers TAILOR which later steps + menus appear. Then stores/team,
// connections, data feeds, mapping, and pay/goals — each deep-linking to its existing page. Leads with the
// data-first view (what's ingested → what it powers). State persists in onboarding_state; config never lives
// here — every real answer writes through its own settings page. DISPLAY/config.
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

  const saveProfile = (patch: any) => put('profile', { answers: { ...answers, ...patch }, status: 'in_progress' })
  const toggleCarrier = (c: string) => {
    const cur: string[] = Array.isArray(answers.carriers) ? answers.carriers : []
    saveProfile({ carriers: cur.includes(c) ? cur.filter(x => x !== c) : [...cur, c] })
  }
  const inp: React.CSSProperties = { padding: '7px 10px', fontSize: 13, border: '1px solid var(--border)', borderRadius: 8 }

  return (
    <div style={{ padding: '18px 22px', maxWidth: 940 }}>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>Setup Wizard</h1>
      <p style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 6, marginBottom: 14, maxWidth: 780 }}>
        Answer a few questions and we&rsquo;ll tailor the setup to your business — only the steps that apply to
        your carrier, POS and processor. Each step links to where you complete it; green means done.
      </p>

      {err && <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b', borderRadius: 8, padding: '9px 12px', fontSize: 12.5, marginBottom: 10 }}>❌ {err}</div>}
      {loading && <div style={{ color: 'var(--text3)', fontSize: 13, padding: 20 }}>Loading…</div>}

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
    </div>
  )
}
