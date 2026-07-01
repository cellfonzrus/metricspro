'use client'
import { useEffect, useState, useCallback } from 'react'
import { api, apiUpload } from '@/lib/client'

// Employee self-service onboarding (way 2): a logged-in new hire completes their own onboarding from
// the /portal kiosk — picks their work state, fills the structured intake form (which propagates into
// their employee record), and uploads their signed documents. Auth is the Supabase session (api() and
// apiUpload() attach the token); the backend derives the employee from it.

const card: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 18 }
const inp: React.CSSProperties = { padding: '10px 12px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 15, width: '100%', boxSizing: 'border-box' }
const btnP: React.CSSProperties = { padding: '10px 16px', borderRadius: 8, border: 'none', background: '#2563eb', color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer' }
const lbl: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: '#334155', display: 'block', margin: '0 0 4px' }
const SECTION_LABEL: Record<string, string> = { personal: 'Your details', address: 'Home address', emergency: 'Emergency contact', direct_deposit: 'Direct deposit', custom: 'Additional info' }

type Task = { id: string; label: string; description?: string; doc_url?: string; doc_label?: string; requires_upload?: boolean; status: string; has_document?: boolean; document_name?: string }
type Cat = { key: string; label: string; tasks: Task[] }
type Field = { key: string; label: string; section: string; field_type: string; options?: string[]; required?: boolean; sensitive?: boolean; help_text?: string }

export default function PortalOnboarding({ onCount }: { onCount?: (remaining: number) => void }) {
  const [d, setD] = useState<any>(null)
  const [vals, setVals] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await api('/api/v1/hr/onboarding/me')
      setD(r); setVals(v => ({ ...(r.intake_values || {}), ...v }))
      const remaining = (r.progress ? Math.max(0, (r.progress.total || 0) - (r.progress.done || 0)) : 0) + (r.intake_submitted ? 0 : (r.intake_fields?.length ? 1 : 0))
      onCount?.(remaining)
    } catch (e: any) { setErr(e?.message || 'Could not load your onboarding') }
  }, [onCount])
  useEffect(() => { load() }, [load])

  async function saveState(st: string) {
    setBusy(true); setNote('')
    try { await api('/api/v1/hr/onboarding/me/state', { method: 'POST', body: JSON.stringify({ work_state: st }) }); await load() }
    catch (e: any) { setNote(e?.message || 'Could not save state') }
    setBusy(false)
  }
  async function saveIntake() {
    setBusy(true); setNote('')
    try { await api('/api/v1/hr/onboarding/me/intake', { method: 'POST', body: JSON.stringify(vals) }); setNote('✓ Your information was saved'); await load() }
    catch (e: any) { setNote(e?.message || 'Could not save') }
    setBusy(false)
  }
  async function upload(t: Task, file: File) {
    setBusy(true); setNote('')
    try { const fd = new FormData(); fd.append('task_id', t.id); fd.append('file', file); await apiUpload('/api/v1/hr/onboarding/me/upload', fd); setNote(`✓ Uploaded ${file.name}`); await load() }
    catch (e: any) { setNote(e?.message || 'Upload failed') }
    setBusy(false)
  }

  if (err) return <div style={{ ...card, color: '#991b1b' }}>{err}</div>
  if (!d) return <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
  if (!d.ready) return <div style={card}>Onboarding isn&apos;t set up yet. Ask HR.</div>

  const fields: Field[] = d.intake_fields || []
  const cats: Cat[] = d.categories || []
  const states: string[] = d.states || []
  const sections = Array.from(new Set(fields.map(f => f.section || 'personal')))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {d.progress && <div style={{ ...card, padding: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ flex: 1, height: 8, background: '#e2e8f0', borderRadius: 8, overflow: 'hidden' }}><div style={{ width: `${d.progress.total ? Math.round(d.progress.done / d.progress.total * 100) : 0}%`, height: '100%', background: '#059669' }} /></div>
        <span style={{ fontSize: 13, color: 'var(--text2)' }}>{d.progress.done}/{d.progress.total} done</span>
      </div>}
      {note && <div style={{ ...card, padding: 12, background: '#ecfdf5', borderColor: '#a7f3d0', color: '#065f46', fontSize: 14 }}>{note}</div>}

      <div style={{ ...card, borderColor: d.needs_work_state ? '#fca5a5' : 'var(--border)' }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>Which state will you work in?</div>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 8 }}>We&apos;ll show only your state&apos;s tax forms.</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select style={{ ...inp, width: 'auto', minWidth: 140 }} value={d.work_state || ''} onChange={e => saveState(e.target.value)} disabled={busy}>
            <option value="">Select state…</option>
            {states.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          {d.work_state && <span style={{ fontSize: 13, color: '#059669' }}>✓ {d.work_state}</span>}
        </div>
      </div>

      {fields.length > 0 && (
        <div style={card}>
          <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>Your information {d.intake_submitted && <span style={{ fontSize: 12, color: '#059669', fontWeight: 600 }}>· saved ✓</span>}</div>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 12 }}>This fills your employee record automatically.</div>
          {sections.map(sec => (
            <div key={sec} style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.4, margin: '0 0 8px' }}>{SECTION_LABEL[sec] || sec}</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                {fields.filter(f => (f.section || 'personal') === sec).map(f => (
                  <div key={f.key} style={{ gridColumn: f.field_type === 'select' || f.key === 'address_line1' ? 'span 2' : 'auto' }}>
                    <label style={lbl}>{f.label}{f.required && <span style={{ color: '#ef4444' }}> *</span>}{f.sensitive && <span style={{ color: '#94a3b8', fontWeight: 400 }}> · private</span>}</label>
                    {f.field_type === 'select'
                      ? <select style={inp} value={vals[f.key] || ''} onChange={e => setVals(v => ({ ...v, [f.key]: e.target.value }))}>
                          <option value="">Select…</option>
                          {(f.options || []).map(o => <option key={o} value={o}>{o}</option>)}
                        </select>
                      : <input style={inp} type={f.field_type === 'date' ? 'date' : f.field_type === 'number' ? 'number' : 'text'}
                          value={vals[f.key] || ''} onChange={e => setVals(v => ({ ...v, [f.key]: e.target.value }))} placeholder={f.help_text || ''} />}
                  </div>
                ))}
              </div>
            </div>
          ))}
          <button style={{ ...btnP, opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={saveIntake}>{busy ? 'Saving…' : (d.intake_submitted ? 'Update my information' : 'Save my information')}</button>
        </div>
      )}

      {cats.map(c => (
        <div key={c.key} style={card}>
          <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>{c.label}</div>
          {c.tasks.map(t => (
            <div key={t.id} style={{ padding: '10px 0', borderTop: '1px solid #f1f5f9' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 15 }}>{t.has_document || t.status === 'verified' ? '✅' : '⬜'}</span>
                <span style={{ fontSize: 15, fontWeight: 600 }}>{t.label}</span>
              </div>
              {t.description && <div style={{ fontSize: 13, color: 'var(--text2)', margin: '2px 0 0 23px' }}>{t.description}</div>}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', margin: '8px 0 0 23px' }}>
                {t.doc_url && <a href={t.doc_url} target="_blank" rel="noreferrer" style={{ padding: '7px 12px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 13, color: '#2563eb', textDecoration: 'none', fontWeight: 600 }}>📄 Open {t.doc_label || 'form'}</a>}
                {t.requires_upload && <label style={{ padding: '7px 12px', borderRadius: 8, background: t.has_document ? '#f8fafc' : '#2563eb', color: t.has_document ? '#334155' : '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer', border: t.has_document ? '1px solid #cbd5e1' : 'none' }}>
                  {t.has_document ? '↻ Replace upload' : '⬆ Upload completed form'}
                  <input type="file" style={{ display: 'none' }} disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) upload(t, f); e.currentTarget.value = '' }} />
                </label>}
                {t.has_document && <span style={{ fontSize: 12, color: '#059669' }}>received</span>}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
