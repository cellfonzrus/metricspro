'use client'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'

// PUBLIC onboarding portal — reached by scanning the QR HR generated. NO login: the opaque token in the
// URL + a date-of-birth / last-4-SSN gate are the only credentials, so a pre-start employee can read and
// upload their own forms before they have an account. Talks ONLY to the token-guarded /hr/public/onboarding
// endpoints (which never expose internal data). Lives outside the (platform) RBAC group on purpose.

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const card: React.CSSProperties = { background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 20, maxWidth: 560, margin: '0 auto' }
const inp: React.CSSProperties = { padding: '10px 12px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 15, width: '100%', boxSizing: 'border-box' }
const btnP: React.CSSProperties = { padding: '10px 16px', borderRadius: 8, border: 'none', background: '#2563eb', color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer' }

type Task = { id: string; label: string; description?: string; doc_url?: string; doc_label?: string; is_fillable?: boolean; requires_upload?: boolean; status: string; has_document?: boolean }
type Cat = { key: string; label: string; tasks: Task[] }

export default function PublicOnboardPage() {
  const { token } = useParams<{ token: string }>()
  const [kind, setKind] = useState<string>('dob')
  const [value, setValue] = useState('')
  const [verified, setVerified] = useState(false)
  const [first, setFirst] = useState('')
  const [cats, setCats] = useState<Cat[]>([])
  const [progress, setProgress] = useState<{ total: number; done: number } | null>(null)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}`)
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j)))
      .then(d => setKind(d.verify_kind || 'dob'))
      .catch(j => setErr(j?.detail || 'This onboarding link is invalid or has expired. Ask HR for a new QR code.'))
  }, [token])

  async function loadChecklist() {
    setErr(''); setBusy(true)
    try {
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) })
      const d = await r.json()
      if (!r.ok) throw new Error(d?.detail || 'That didn’t match. Please try again.')
      setVerified(true); setFirst(d.first_name || ''); setCats(d.categories || []); setProgress(d.progress || null)
    } catch (e: any) { setErr(e?.message || 'Verification failed') }
    setBusy(false)
  }
  async function upload(t: Task, file: File) {
    setNote(''); setBusy(true)
    try {
      const fd = new FormData(); fd.append('value', value); fd.append('task_id', t.id); fd.append('file', file)
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/upload`, { method: 'POST', body: fd })
      const d = await r.json()
      if (!r.ok) throw new Error(d?.detail || 'Upload failed')
      setNote(`✓ Uploaded ${file.name}`); loadChecklist()
    } catch (e: any) { setNote(e?.message || 'Upload failed') }
    setBusy(false)
  }

  return (
    <div style={{ minHeight: '100vh', background: '#f1f5f9', padding: '32px 16px', fontFamily: 'system-ui, sans-serif' }}>
      <div style={{ maxWidth: 560, margin: '0 auto 16px', textAlign: 'center' }}>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: '#0f172a', margin: 0 }}>Welcome aboard{first ? `, ${first}` : ''} 👋</h1>
        <p style={{ color: '#475569', fontSize: 14 }}>Complete your new-hire forms below — fill each one, then upload it here.</p>
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
          {cats.map(c => (
            <div key={c.key} style={card}>
              <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px' }}>{c.label}</h2>
              {c.tasks.map(t => (
                <div key={t.id} style={{ padding: '10px 0', borderTop: '1px solid #f1f5f9' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 15 }}>{t.has_document || t.status === 'verified' ? '✅' : '⬜'}</span>
                    <span style={{ fontSize: 15, fontWeight: 600, color: '#0f172a' }}>{t.label}</span>
                  </div>
                  {t.description && <div style={{ fontSize: 13, color: '#64748b', margin: '2px 0 0 23px' }}>{t.description}</div>}
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', margin: '8px 0 0 23px' }}>
                    {t.doc_url && <a href={t.doc_url} target="_blank" rel="noreferrer" style={{ padding: '7px 12px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 13, color: '#2563eb', textDecoration: 'none', fontWeight: 600 }}>📄 {t.is_fillable ? 'Open & fill' : 'Open'} {t.doc_label || 'form'}</a>}
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
          {cats.length === 0 && <div style={card}>No documents are assigned to you yet. Check back later or ask HR.</div>}
          <p style={{ textAlign: 'center', fontSize: 12, color: '#94a3b8' }}>Your uploads go straight to HR. You can close this page and return with the same link anytime.</p>
        </div>
      )}
    </div>
  )
}
