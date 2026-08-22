'use client'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import OnboardSignModal from '@/components/OnboardSignModal'
import EntityPicker, { US_STATES } from '@/components/EntityPicker'

// PUBLIC onboarding portal — reached by scanning the QR / clicking the emailed link HR generated. NO
// login: the opaque token in the URL + a date-of-birth / last-4-SSN gate are the only credentials, so a
// pre-start employee can read + fill + upload their own forms before they have an account. Talks ONLY to
// the token-guarded /hr/public/onboarding endpoints. Lives outside the (platform) RBAC group on purpose.

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const card: React.CSSProperties = { background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 20, maxWidth: 560, margin: '0 auto' }
const inp: React.CSSProperties = { padding: '10px 12px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 15, width: '100%', boxSizing: 'border-box' }
const btnP: React.CSSProperties = { padding: '10px 16px', borderRadius: 8, border: 'none', background: '#2563eb', color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer' }
const lbl: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: '#334155', display: 'block', margin: '0 0 4px' }

type DocFile = { id: string; name: string; uploaded_at?: string; employee_can_delete?: boolean }
type Task = { id: string; label: string; description?: string; doc_url?: string; doc_label?: string; requires_upload?: boolean; status: string; has_document?: boolean; document_name?: string; documents?: DocFile[]; template_url?: string | null; template_name?: string | null; requires_signature?: boolean; form_fields?: { key?: string; label?: string; required?: boolean }[] | null; missing_fields?: string[] | null; returned_reason?: string | null; signed_at?: string | null; work_auth?: boolean; sample_name?: string | null; sample_url?: string | null }
type Cat = { key: string; label: string; tasks: Task[] }
type Field = { key: string; label: string; section: string; field_type: string; options?: string[]; required?: boolean; sensitive?: boolean; help_text?: string }
type TenantConfig = { upload_allowed_formats?: string[]; dd_disclaimer_text?: string; work_auth_notice_text?: string; routing_lookup_enabled?: boolean }
const SECTION_LABEL: Record<string, string> = { personal: 'Your details', address: 'Home address', emergency: 'Emergency contact', work_eligibility: 'Work eligibility (I-9)', tax: 'Tax withholding (W-4)', direct_deposit: 'Direct deposit', policies: 'Policy acknowledgements', custom: 'Additional info' }
// Item 2: client-side format hint (real enforcement is server-side, magic-byte sniffed — see
// hr/router.py _format_allowed). Config-driven off tenant_config.upload_allowed_formats.
const EXT_FOR: Record<string, string[]> = { pdf: ['.pdf'], jpeg: ['.jpg', '.jpeg'], png: ['.png'] }
function acceptAttr(formats?: string[]): string { return (formats && formats.length ? formats : ['pdf', 'jpeg']).flatMap(f => EXT_FOR[f] || []).join(',') }
function extLooksAllowed(name: string, formats?: string[]): boolean {
  const allow = (formats && formats.length ? formats : ['pdf', 'jpeg']).flatMap(f => EXT_FOR[f] || [])
  return allow.some(e => name.toLowerCase().endsWith(e))
}

export default function PublicOnboardPage() {
  const { token } = useParams<{ token: string }>()
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
  // BUG FIX (2026-07-27): every post-verification message (success AND error) used to render
  // in the SAME green "saved" box — an employee whose save actually FAILED (e.g. the direct-
  // deposit disclaimer gate below) saw a green confirmation-looking banner and had no visual cue
  // anything was wrong. Track the kind explicitly so error/warning states are never disguised as
  // success (see hr/onboarding/[employeeId]/page.tsx's admin-side "Captured information" card,
  // which is exactly what stayed blank when a save silently failed this way).
  const [noteKind, setNoteKind] = useState<'ok' | 'warn' | 'err'>('ok')
  const [busy, setBusy] = useState(false)
  const [signing, setSigning] = useState<Task | null>(null)
  const [tenantConfig, setTenantConfig] = useState<TenantConfig>({})
  const [workAuthPending, setWorkAuthPending] = useState<string[]>([])
  const [workAuthNotice, setWorkAuthNotice] = useState<string | null>(null)
  const [ddSigned, setDdSigned] = useState(false)
  const [ddInitials, setDdInitials] = useState('')
  const [routingInfo, setRoutingInfo] = useState<{ valid_checksum?: boolean; bank_name?: string | null } | null>(null)
  const [routingBusy, setRoutingBusy] = useState(false)

  useEffect(() => {
    fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}`)
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j)))
      // The response is not read: date of birth is the only identity gate (mig 909 removed the
      // last-4-SSN alternative). The call still runs because its REJECTION is what tells the
      // employee the link is invalid or expired — see the catch below.
      .then(() => {})
      .catch(j => setErr(j?.detail || 'This onboarding link is invalid or has expired. Ask HR for a new QR code.'))
  }, [token])

  function absorb(d: any) {
    setFirst(d.first_name || ''); setCats(d.categories || []); setProgress(d.progress || null)
    setFields(d.intake_fields || []); setIntakeDone(!!d.intake_submitted)
    setWorkState(d.work_state || ''); setNeedsState(!!d.needs_work_state); setStates(d.states || [])
    setVals(v => ({ ...(d.intake_values || {}), ...v }))
    setTenantConfig(d.tenant_config || {}); setWorkAuthPending(d.work_auth_pending || [])
    setWorkAuthNotice(d.work_auth_notice || null); setDdSigned(!!d.dd_disclaimer_signed)
  }
  async function checkRouting(routing: string) {
    setRoutingInfo(null)
    const digits = routing.replace(/\D/g, '')
    if (digits.length !== 9) return
    setRoutingBusy(true)
    try {
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/routing-lookup?routing=${digits}&value=${encodeURIComponent(value)}`)
      if (r.ok) {
        const d = await r.json()
        setRoutingInfo(d)
        if (d?.bank_name && !vals['dd_bank_name']) setVals(v => ({ ...v, dd_bank_name: d.bank_name }))
      }
    } catch { /* lookup is a UX aid only — never blocks the form */ }
    setRoutingBusy(false)
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
    setBusy(true); setNote(''); setNoteKind('ok')
    try {
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/state`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value, work_state: st }) })
      if (!r.ok) throw new Error((await r.json())?.detail || 'Could not save')
      await loadChecklist()
    } catch (e: any) { setNote(e?.message || 'Could not save state'); setNoteKind('err') }
    setBusy(false)
  }
  async function saveIntake() {
    setBusy(true); setNote(''); setNoteKind('ok')
    try {
      const dd = ddInitials.trim() ? { dd_disclaimer_initials: ddInitials.trim() } : {}
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/intake`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value, ...vals, ...dd }) })
      const d = await r.json()
      if (!r.ok) throw new Error(typeof d?.detail === 'string' ? d.detail : 'Could not save your information')
      // BUG FIX (2026-07-27): the backend now saves everything EXCEPT direct-deposit fields when the
      // disclaimer initials are missing (was: reject the whole submission, see hr/router.py
      // _apply_intake). Surface that distinctly — amber "still needs attention", never green "all set".
      if (d?.dd_disclaimer_pending) { setNote('⚠️ ' + (d.warning || 'Everything else was saved — direct-deposit details still need your initials above.')); setNoteKind('warn') }
      else { setNote('✓ Your information was saved'); setNoteKind('ok') }
      await loadChecklist()
    } catch (e: any) { setNote(e?.message || 'Could not save'); setNoteKind('err') }
    setBusy(false)
  }
  async function signOnline(payload: { form_data: Record<string, string>; signature: string; signed_name: string }) {
    if (!signing) return
    const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/sign`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value, task_id: signing.id, ...payload }) })
    const d = await r.json()
    if (!r.ok) throw new Error(typeof d?.detail === 'string' ? d.detail : 'Could not submit')
    setSigning(null); setNote('✓ Signed and submitted — thank you!'); setNoteKind('ok'); loadChecklist()
  }
  async function upload(t: Task, file: File) {
    if (!extLooksAllowed(file.name, tenantConfig.upload_allowed_formats)) {
      setNote(`Only ${(tenantConfig.upload_allowed_formats || ['pdf', 'jpeg']).join('/').toUpperCase()} files are accepted here.`); setNoteKind('err')
      return
    }
    setNote(''); setNoteKind('ok'); setBusy(true)
    try {
      const fd = new FormData(); fd.append('value', value); fd.append('task_id', t.id); fd.append('file', file)
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/upload`, { method: 'POST', body: fd })
      const d = await r.json()
      if (!r.ok) throw new Error(d?.detail || 'Upload failed')
      if (d?.status === 'returned') {
        setNote(`⚠️ ${file.name} came back incomplete — missing: ${(d.missing || []).join(', ')}. Please fix and re-upload (we also emailed you the list).`); setNoteKind('warn')
      } else {
        setNote(`✓ Uploaded ${file.name}${d?.note ? ` — ${d.note}` : ''}`); setNoteKind('ok')
      }
      loadChecklist()
    } catch (e: any) { setNote(e?.message || 'Upload failed'); setNoteKind('err') }
    setBusy(false)
  }
  // migration 402: multiple files per document — a new upload APPENDS, never replaces. Delete is
  // server-enforced (employee_can_delete), same rule as the logged-in portal.
  async function viewFile(t: Task, f: DocFile) {
    try {
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/task/${t.id}/document/${f.id}?value=${encodeURIComponent(value)}`)
      const d = await r.json()
      if (!r.ok) throw new Error(d?.detail || 'Could not open that file')
      if (d?.url) window.open(d.url, '_blank')
    } catch (e: any) { setNote(e?.message || 'Could not open that file'); setNoteKind('err') }
  }
  async function deleteFile(t: Task, f: DocFile) {
    if (!window.confirm(`Remove ${f.name}? This cannot be undone.`)) return
    setBusy(true)
    try {
      const r = await fetch(`${API_URL}/api/v1/hr/public/onboarding/${token}/task/${t.id}/document/${f.id}?value=${encodeURIComponent(value)}`, { method: 'DELETE' })
      const d = await r.json()
      if (!r.ok) throw new Error(d?.detail || 'Could not remove that file')
      loadChecklist()
    } catch (e: any) { setNote(e?.message || 'Could not remove that file'); setNoteKind('err') }
    setBusy(false)
  }

  const sections = Array.from(new Set(fields.map(f => f.section || 'personal')))
  // Gate-1 fold N1 (2026-07-27): a PERSISTENT cue (not tied to the one-shot `note` banner, which
  // clears on the next action/reload) for a returning employee whose intake was submitted but
  // whose direct-deposit fields were withheld pending the disclaimer initials (see _apply_intake's
  // dd_disclaimer_pending fix above this in the same package) — otherwise the only surviving signal
  // was the passive green "· saved ✓" section chip, easy to read as fully done.
  const ddConfigured = fields.some(f => (f.section || 'personal') === 'direct_deposit')
  const ddPending = intakeDone && !ddSigned && ddConfigured

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
          <p style={{ color: '#475569', fontSize: 14 }}>For your security, enter your date of birth to continue.</p>
          <input style={inp} type="date" value={value} onChange={e => setValue(e.target.value)} />
          <button style={{ ...btnP, marginTop: 14, width: '100%', opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={loadChecklist}>{busy ? 'Checking…' : 'Continue'}</button>
        </div>
      ) : (
        <div style={{ maxWidth: 560, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* item 4: persistent notice, shown on every visit while any work-auth doc is outstanding */}
          {workAuthPending.length > 0 && (
            <div style={{ ...card, background: '#fef2f2', borderColor: '#fca5a5', color: '#991b1b', fontSize: 14 }}>
              ⛔ {workAuthNotice || 'Your work-authorization documents are still outstanding. Your payroll will be delayed until these documents are submitted.'}
            </div>
          )}
          {/* Gate-1 fold N1: persistent (survives reload, unlike `note`) — bank details were withheld
              until the disclaimer is acknowledged. */}
          {ddPending && (
            <div style={{ ...card, background: '#fffbeb', borderColor: '#fde68a', color: '#92400e', fontSize: 14 }}>
              🏦 Bank details pending — add your initials to finish direct deposit (see &quot;Your information&quot; below).
            </div>
          )}
          {progress && <div style={{ ...card, padding: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ flex: 1, height: 8, background: '#e2e8f0', borderRadius: 8, overflow: 'hidden' }}><div style={{ width: `${progress.total ? Math.round(progress.done / progress.total * 100) : 0}%`, height: '100%', background: '#059669' }} /></div>
            <span style={{ fontSize: 13, color: '#475569' }}>{progress.done}/{progress.total} done</span>
          </div>}
          {note && <div style={{ ...card, padding: 12,
            background: noteKind === 'err' ? '#fef2f2' : noteKind === 'warn' ? '#fffbeb' : '#ecfdf5',
            borderColor: noteKind === 'err' ? '#fca5a5' : noteKind === 'warn' ? '#fde68a' : '#a7f3d0',
            color: noteKind === 'err' ? '#991b1b' : noteKind === 'warn' ? '#92400e' : '#065f46', fontSize: 14 }}>{note}</div>}

          {/* Step 1 — which state do you work in? (drives which tax forms you get) */}
          <div style={{ ...card, borderColor: needsState ? '#fca5a5' : '#e5e7eb' }}>
            <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 6px' }}>Which state will you work in?</h2>
            <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 8px' }}>We&apos;ll show you only the tax forms for that state.</p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <EntityPicker options={US_STATES} value={workState || null} width={200} disabled={busy}
                onChange={v => { if (v) saveState(v) }} placeholder="Select state…" clearable={false} />
              {workState && <span style={{ alignSelf: 'center', fontSize: 13, color: '#059669' }}>✓ {workState}</span>}
            </div>
          </div>

          {/* Step 2 — structured intake form (configurable) */}
          {fields.length > 0 && (
            <div style={card}>
              <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 4px' }}>Your information {intakeDone && (ddPending ? <span style={{ fontSize: 12, color: '#92400e', fontWeight: 600 }}>· bank details pending</span> : <span style={{ fontSize: 12, color: '#059669', fontWeight: 600 }}>· saved ✓</span>)}</h2>
              <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 12px' }}>This fills your employee record automatically — no PDF needed for these.</p>
              {sections.map(sec => (
                <div key={sec} style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.4, margin: '0 0 8px' }}>{SECTION_LABEL[sec] || sec}</div>
                  {/* item 3a: bold disclaimer above the bank-details fields */}
                  {sec === 'direct_deposit' && (
                    <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: '10px 12px', marginBottom: 10, fontSize: 13, fontWeight: 700, color: '#92400e' }}>
                      {tenantConfig.dd_disclaimer_text || 'By providing bank account information for direct deposit, I certify the routing and account numbers above are correct. If I submit incorrect information, my employer and the payroll processing company are NOT liable for any loss, delay, or misdirection of my wages that results.'}
                    </div>
                  )}
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
                              onBlur={f.key === 'dd_routing' ? (e => checkRouting(e.target.value)) : undefined}
                              placeholder={f.help_text || ''} />}
                        {/* item 3b: ABA checksum + bank-name confirmation */}
                        {f.key === 'dd_routing' && routingBusy && <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>Checking…</div>}
                        {f.key === 'dd_routing' && !routingBusy && routingInfo && (
                          routingInfo.valid_checksum ? (
                            <div style={{ fontSize: 12, color: '#059669', marginTop: 4 }}>
                              ✓ Valid routing number{routingInfo.bank_name ? <> — you&apos;re entering an account at <b>{routingInfo.bank_name}</b>. Correct?</> : ''}
                            </div>
                          ) : <div style={{ fontSize: 12, color: '#dc2626', marginTop: 4 }}>⚠️ That doesn&apos;t look like a valid routing number — please double-check it.</div>
                        )}
                      </div>
                    ))}
                  </div>
                  {/* item 3a: typed-initials acknowledgment (server also gates this) */}
                  {sec === 'direct_deposit' && !ddSigned && (
                    <div style={{ marginTop: 10 }}>
                      <label style={lbl}>Type your initials to confirm the disclaimer above<span style={{ color: '#ef4444' }}> *</span></label>
                      <input style={{ ...inp, maxWidth: 120 }} maxLength={6} value={ddInitials} onChange={e => setDdInitials(e.target.value)} placeholder="e.g. JB" />
                    </div>
                  )}
                  {sec === 'direct_deposit' && ddSigned && <div style={{ marginTop: 8, fontSize: 12, color: '#059669' }}>✓ Disclaimer acknowledged</div>}
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
                    {t.work_auth && <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 20, background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b' }}>required for payroll</span>}
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
                    {t.sample_url && <a href={t.sample_url} target="_blank" rel="noreferrer" style={{ padding: '7px 12px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 13, color: '#2563eb', textDecoration: 'none', fontWeight: 600 }}>👁 View completed sample</a>}
                    <button onClick={() => setSigning(t)} disabled={busy} style={{ padding: '7px 12px', borderRadius: 8, border: 'none', background: '#059669', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>✍️ Fill &amp; sign online</button>
                    {t.requires_upload && <label style={{ padding: '7px 12px', borderRadius: 8, background: t.has_document ? '#f8fafc' : '#2563eb', color: t.has_document ? '#334155' : '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer', border: t.has_document ? '1px solid #cbd5e1' : 'none' }}>
                      {t.has_document ? '+ Add another file' : '⬆ Upload signed copy'}
                      <input type="file" accept={acceptAttr(tenantConfig.upload_allowed_formats)} style={{ display: 'none' }} disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) upload(t, f); e.currentTarget.value = '' }} />
                    </label>}
                    {t.signed_at && <span style={{ fontSize: 12, color: '#059669' }}>✍️ signed online</span>}
                  </div>
                  {/* migration 402: every file on this document, each independently viewable — and,
                      only while employee_can_delete says the task is still editable, removable. */}
                  {(t.documents || []).length > 0 && (
                    <div style={{ margin: '6px 0 0 23px', display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {(t.documents || []).map(f => (
                        <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                          <button onClick={() => viewFile(t, f)} style={{ background: 'none', border: 'none', padding: 0, color: '#2563eb', cursor: 'pointer', fontSize: 12, textDecoration: 'underline' }}>📄 {f.name}</button>
                          {f.employee_can_delete && <button onClick={() => deleteFile(t, f)} disabled={busy} title="Remove this file" style={{ background: 'none', border: 'none', padding: 0, color: '#dc2626', cursor: 'pointer', fontSize: 12 }}>✕ remove</button>}
                        </div>
                      ))}
                    </div>
                  )}
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
