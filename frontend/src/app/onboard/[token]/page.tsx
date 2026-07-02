'use client'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import OnboardSignModal from '@/components/OnboardSignModal'

// PUBLIC onboarding portal — reached by scanning the QR / clicking the emailed link HR generated. NO
// login: the opaque token in the URL + a date-of-birth / last-4-SSN gate are the only credentials, so a
// pre-start employee can read + fill + upload their own forms before they have an account. Talks ONLY to
// the token-guarded /hr/public/onboarding endpoints. Lives outside the (platform) RBAC group on purpose.

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const card: React.CSSProperties = { background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 20, maxWidth: 560, margin: '0 auto' }
const inp: React.CSSProperties = { padding: '10px 12px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 15, width: '100%', boxSizing: 'border-box' }
const btnP: React.CSSProperties = { padding: '10px 16px', borderRadius: 8, border: 'none', background: '#2563eb', color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer' }
const lbl: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: '#334155', display: 'block', margin: '0 0 4px' }

type Task = { id: string; label: string; description?: string; doc_url?: string; doc_label?: string; requires_upload?: boolean; status: string; has_document?: boolean; document_name?: string; template_url?: string | null; template_name?: string | null; requires_signature?: boolean; form_fields?: { key?: string; label?: string; required?: boolean }[] | null; missing_fields?: string[] | null; returned_reason?: string | null; signed_at?: string | null }
type Cat = { key: string; label: string; tasks: Task[] }
type Field = { key: string; label: string; section: string; field_type: string; options?: string[]; required?: boolean; sensitive?: boolean; help_text?: string }
const SECTION_LABEL: Record<string, string> = { personal: 'Your details', address: 'Home address', emergency: 'Emergency contact', work_eligibility: 'Work eligibility (I-9)', tax: 'Tax withholding (W-4)', direct_deposit: 'Direct deposit', policies: 'Policy acknowledgements', custom: 'Additional info' }

export default function PublicOnboardPage() {
  const { token } = useParams<{ token: string }>()
  const [kind, setKind] = useState<string>('dob')
  const [value, setValue] = useState('')
  const [verified, setVerified] = useState(false)
  const [first, setFirst] = useState('')
  const [cats, setCats] = useState<Cat[]>([])
  const [progress, setProgress] = useState<{ total: number; done: number } | null>(null)
  const [fields, setFields] = useState<Field[]>([])
  const [vals, setVals] = useState<Record<string, string>>({})
  const [intakeDone, setIntakeDone] = useState(false)
  const [workState, setWorkState] = useState('')
  const [needsState, setNeedsState] = useState(false)
  const [states, setStates] = useState<string[]>([])
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [signing, setSigning] = useState<Task | null>(null)

  useEffect(() => {
    fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}`)
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j)))
      .then(d => setKind(d.verify_kind || 'dob'))
      .catch(j => setErr(j?.detail || 'This onboarding link is invalid or has expired. Ask HR for a new QR code.'))
  }, [token])

  function absorb(d: any) {
    setFirst(d.first_name || ''); setCats(d.categories || []); setProgress(d.progress || null)
    setFields(d.intake_fields || []); setIntakeDone(!!d.intake_submitted)
    setWorkState(d.work_state || ''); setNeedsState(!!d.needs_work_state); setStates(d.states || [])
    setVals(v => ({ ...(d.intake_values || {}), ...v }))
  }
  async function loadChecklist() {
    setErr(''); setBusy(true)
    try {
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) })
      const d = await r.json()
      if (!r.ok) throw new Error(d?.detail || 'That didn’t match. Please try again.')
      setVerified(true); absorb(d)
    } catch (e: any) { setErr(e?.message || 'Verification failed') }
    setBusy(false)
  }
  async function saveState(st: string) {
    setBusy(true); setNote('')
    try {
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/state`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value, work_state: st }) })
      if (!r.ok) throw new Error((await r.json())?.detail || 'Could not save')
      await loadChecklist()
    } catch (e: any) { setNote(e?.message || 'Could not save state') }
    setBusy(false)
  }
  async function saveIntake() {
    setBusy(true); setNote('')
    try {
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/intake`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value, ...vals }) })
      const d = await r.json()
      if (!r.ok) throw new Error(typeof d?.detail === 'string' ? d.detail : 'Could not save your information')
      setNote('✓ Your information was saved'); await loadChecklist()
    } catch (e: any) { setNote(e?.message || 'Could not save') }
    setBusy(false)
  }
  async function signOnline(payload: { form_data: Record<string, string>; signature: string; signed_name: string }) {
    if (!signing) return
    const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/sign`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value, task_id: signing.id, ...payload }) })
    const d = await r.json()
    if (!r.ok) throw new Error(typeof d?.detail === 'string' ? d.detail : 'Could not submit')
    setSigning(null); setNote('✓ Signed and submitted — thank you!'); loadChecklist()
  }
  async function upload(t: Task, file: File) {
    setNote(''); setBusy(true)
    try {
      const fd = new FormData(); fd.append('value', value); fd.append('task_id', t.id); fd.append('file', file)
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/upload`, { method: 'POST', body: fd })
      const d = await r.json()
      if (!r.ok) throw new Error(d?.detail || 'Upload failed')
      setNote(d?.status === 'returned'
        ? `⚠️ ${file.name} came back incomplete — missing: ${(d.missing || []).join(', ')}. Please fix and re-upload (we also emailed you the list).`
        : `✓ Uploaded ${file.name}${d?.note ? ` — ${d.note}` : ''}`)
      loadChecklist()
    } catch (e: any) { setNote(e?.message || 'Upload failed') }
    setBusy(false)
  }

  const sections = Array.from(new Set(fields.map(f => f.section || 'personal')))

  return (
    <div style={{ minHeight: '100vh', background: '#f1f5f9', padding: '32px 16px', fontFamily: 'system-ui, sans-serif' }}>
      <div style={{ maxWidth: 560, margin: '0 auto 16px', textAlign: 'center' }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: '#0f172a', margin: 0 }}>Welcome aboard{first ? `, ${first}` : ''} 👋</h1>
        <p style={{ color: '#475569', fontSize: 14 }}>Tell us about yourself, then fill and upload your new-hire forms.</p>
      </div>

      {err && <div style={{ ...card, borderColor: '#fca5a5', background: '#fef2f2', color: '#991b1b', fontSize: 14, marginBottom: 12 }}>{err}</div>}

      {!verified ? (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 0 }}>Confirm it&apos;s you</h2>
          <p style={{ color: '#475569', fontSize: 14 }}>For your security, enter your {kind === 'ssn4' ? 'last 4 digits of SSN' : 'date of birth'} to continue.</p>
          {kind === 'ssn4'
            ? <input style={inp} inputMode="numeric" maxLength={4} placeholder="1234" value={value} onChange={e => setValue(e.target.value.replace(/\D/g, ''))} />
            : <input style={inp} type="date" value={value} onChange={e => setValue(e.target.value)} />}
          <button style={{ ...btnP, marginTop: 14, width: '100%', opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={loadChecklist}>{busy ? 'Checking…' : 'Continue'}</button>
        </div>
      ) : (
        <div style={{ maxWidth: 560, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {progress && <div style={{ ...card, padding: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ flex: 1, height: 8, background: '#e2e8f0', borderRadius: 8, overflow: 'hidden' }}><div style={{ width: `${progress.total ? Math.round(progress.done / progress.total * 100) : 0}%`, height: '100%', background: '#059669' }} /></div>
            <span style={{ fontSize: 13, color: '#475569' }}>{progress.done}/{progress.total} done</span>
          </div>}
          {note && <div style={{ ...card, padding: 12, background: '#ecfdf5', borderColor: '#a7f3d0', color: '#065f46', fontSize: 14 }}>{note}</div>}

          {/* Step 1 — which state do you work in? (drives which tax forms you get) */}
          <div style={{ ...card, borderColor: needsState ? '#fca5a5' : '#e5e7eb' }}>
            <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 6px' }}>Which state will you work in?</h2>
            <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 8px' }}>We&apos;ll show you only the tax forms for that state.</p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <select style={{ ...inp, width: 'auto', minWidth: 140 }} value={workState} onChange={e => saveState(e.target.value)} disabled={busy}>
                <option value="">Select state…</option>
                {states.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              {workState && <span style={{ alignSelf: 'center', fontSize: 13, color: '#059669' }}>✓ {workState}</span>}
            </div>
          </div>

          {/* Step 2 — structured intake form (configurable) */}
          {fields.length > 0 && (
            <div style={card}>
              <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 4px' }}>Your information {intakeDone && <span style={{ fontSize: 12, color: '#059669', fontWeight: 600 }}>· saved ✓</span>}</h2>
              <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 12px' }}>This fills your employee record automatically — no PDF needed for these.</p>
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
                              inputMode={f.field_type === 'tel' ? 'tel' : undefined}
                              value={vals[f.key] || ''} onChange={e => setVals(v => ({ ...v, [f.key]: e.target.value }))}
                              placeholder={f.help_text || ''} />}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              <button style={{ ...btnP, marginTop: 4, opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={saveIntake}>{busy ? 'Saving…' : (intakeDone ? 'Update my information' : 'Save my information')}</button>
            </div>
          )}

          {/* Step 3 — documents to fill + upload */}
          {cats.map(c => (
            <div key={c.key} style={card}>
              <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px' }}>{c.label}</h2>
              {c.tasks.map(t => (
                <div key={t.id} style={{ padding: '10px 0', borderTop: '1px solid #f1f5f9' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 15 }}>{t.status === 'returned' ? '⚠️' : (t.has_document || t.status === 'verified' ? '✅' : '⬜')}</span>
                    <span style={{ fontSize: 15, fontWeight: 600, color: '#0f172a' }}>{t.label}</span>
                  </div>
                  {t.description && <div style={{ fontSize: 13, color: '#64748b', margin: '2px 0 0 23px' }}>{t.description}</div>}
                  {t.status === 'returned' && (
                    <div style={{ margin: '6px 0 0 23px', background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8, padding: '8px 10px', fontSize: 13, color: '#991b1b' }}>
                      ↩ <b>Returned — please fix and resubmit.</b>
                      {(t.missing_fields || []).length > 0 && <div style={{ marginTop: 2 }}>Missing: <b>{(t.missing_fields || []).join(', ')}</b></div>}
                      {t.returned_reason && <div style={{ fontSize: 12, marginTop: 2 }}>{t.returned_reason}</div>}
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', margin: '8px 0 0 23px' }}>
                    {t.doc_url && <a href={t.doc_url} target="_blank" rel="noreferrer" style={{ padding: '7px 12px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 13, color: '#2563eb', textDecoration: 'none', fontWeight: 600 }}>📄 Open {t.doc_label || 'form'}</a>}
                    {t.template_url && <a href={t.template_url} target="_blank" rel="noreferrer" style={{ padding: '7px 12px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 13, color: '#2563eb', textDecoration: 'none', fontWeight: 600 }}>📎 {t.template_name || 'Download template'}</a>}
                    <button onClick={() => setSigning(t)} disabled={busy} style={{ padding: '7px 12px', borderRadius: 8, border: 'none', background: '#059669', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>✍️ Fill &amp; sign online</button>
                    {t.requires_upload && <label style={{ padding: '7px 12px', borderRadius: 8, background: t.has_document ? '#f8fafc' : '#2563eb', color: t.has_document ? '#334155' : '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer', border: t.has_document ? '1px solid #cbd5e1' : 'none' }}>
                      {t.has_document ? '↻ Replace upload' : '⬆ Upload signed copy'}
                      <input type="file" style={{ display: 'none' }} disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) upload(t, f); e.currentTarget.value = '' }} />
                    </label>}
                    {t.signed_at && <span style={{ fontSize: 12, color: '#059669' }}>✍️ signed online</span>}
                    {t.has_document && !t.signed_at && <span style={{ fontSize: 12, color: '#059669' }}>received</span>}
                  </div>
                </div>
              ))}
            </div>
          ))}
          {cats.length === 0 && fields.length === 0 && <div style={card}>Nothing is assigned to you yet. Check back later or ask HR.</div>}
          <p style={{ textAlign: 'center', fontSize: 12, color: '#94a3b8' }}>Your information goes straight to HR. You can close this page and return with the same link anytime.</p>
          {signing && <OnboardSignModal task={signing} onCancel={() => setSigning(null)} onSubmit={signOnline} />}
        </div>
      )}
    </div>
  )
}
