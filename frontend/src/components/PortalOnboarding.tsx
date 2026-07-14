'use client'
import { useEffect, useState, useCallback } from 'react'
import { api, apiUpload } from '@/lib/client'
import OnboardSignModal from '@/components/OnboardSignModal'

// Employee self-service onboarding (way 2): a logged-in new hire completes their own onboarding from
// the /portal kiosk — picks their work state, fills the structured intake form (which propagates into
// their employee record), and uploads their signed documents. Auth is the Supabase session (api() and
// apiUpload() attach the token); the backend derives the employee from it.

const card: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 18 }
const inp: React.CSSProperties = { padding: '10px 12px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 15, width: '100%', boxSizing: 'border-box' }
const btnP: React.CSSProperties = { padding: '10px 16px', borderRadius: 8, border: 'none', background: '#2563eb', color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer' }
const lbl: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: '#334155', display: 'block', margin: '0 0 4px' }
const SECTION_LABEL: Record<string, string> = { personal: 'Your details', address: 'Home address', emergency: 'Emergency contact', work_eligibility: 'Work eligibility (I-9)', tax: 'Tax withholding (W-4)', direct_deposit: 'Direct deposit', policies: 'Policy acknowledgements', custom: 'Additional info' }
// Item 2: client-side format hint (real enforcement is server-side, magic-byte sniffed — see
// hr/router.py _format_allowed). Config-driven off tenant_config.upload_allowed_formats.
const EXT_FOR: Record<string, string[]> = { pdf: ['.pdf'], jpeg: ['.jpg', '.jpeg'], png: ['.png'] }
function acceptAttr(formats?: string[]): string { return (formats && formats.length ? formats : ['pdf', 'jpeg']).flatMap(f => EXT_FOR[f] || []).join(',') }
function extLooksAllowed(name: string, formats?: string[]): boolean {
  const allow = (formats && formats.length ? formats : ['pdf', 'jpeg']).flatMap(f => EXT_FOR[f] || [])
  return allow.some(e => name.toLowerCase().endsWith(e))
}

type Task = { id: string; label: string; description?: string; doc_url?: string; doc_label?: string; requires_upload?: boolean; status: string; has_document?: boolean; document_name?: string; template_url?: string | null; template_name?: string | null; requires_signature?: boolean; form_fields?: { key?: string; label?: string; required?: boolean }[] | null; missing_fields?: string[] | null; returned_reason?: string | null; signed_at?: string | null; work_auth?: boolean; sample_name?: string | null; sample_url?: string | null }
type Cat = { key: string; label: string; tasks: Task[] }
type Field = { key: string; label: string; section: string; field_type: string; options?: string[]; required?: boolean; sensitive?: boolean; help_text?: string }

export default function PortalOnboarding({ onCount }: { onCount?: (remaining: number) => void }) {
  const [d, setD] = useState<any>(null)
  const [vals, setVals] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')
  const [signing, setSigning] = useState<Task | null>(null)
  const [ddInitials, setDdInitials] = useState('')
  const [routingInfo, setRoutingInfo] = useState<{ valid_checksum?: boolean; bank_name?: string | null; routing?: string } | null>(null)
  const [routingBusy, setRoutingBusy] = useState(false)

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
    try {
      const dd = ddInitials.trim() ? { dd_disclaimer_initials: ddInitials.trim() } : {}
      await api('/api/v1/hr/onboarding/me/intake', { method: 'POST', body: JSON.stringify({ ...vals, ...dd }) })
      setNote('✓ Your information was saved'); await load()
    }
    catch (e: any) { setNote(e?.message || 'Could not save') }
    setBusy(false)
  }
  async function checkRouting(routing: string) {
    setRoutingInfo(null)
    const digits = routing.replace(/\D/g, '')
    if (digits.length !== 9) return
    setRoutingBusy(true)
    try {
      const r = await api(`/api/v1/hr/onboarding/me/routing-lookup?routing=${digits}`)
      setRoutingInfo(r)
      if (r?.bank_name && !vals['dd_bank_name']) setVals(v => ({ ...v, dd_bank_name: r.bank_name }))
    } catch { /* lookup is a UX aid only — never blocks the form */ }
    setRoutingBusy(false)
  }
  async function upload(t: Task, file: File) {
    const formats = d?.tenant_config?.upload_allowed_formats
    if (!extLooksAllowed(file.name, formats)) { setNote(`Only ${(formats || ['pdf', 'jpeg']).join('/').toUpperCase()} files are accepted here.`); return }
    setBusy(true); setNote('')
    try {
      const fd = new FormData(); fd.append('task_id', t.id); fd.append('file', file)
      const r = await apiUpload('/api/v1/hr/onboarding/me/upload', fd)
      setNote(r?.status === 'returned'
        ? `⚠️ ${file.name} came back incomplete — missing: ${(r.missing || []).join(', ')}. Please fix and re-upload (we also emailed you the list).`
        : `✓ Uploaded ${file.name}${r?.note ? ` — ${r.note}` : ''}`)
      await load()
    }
    catch (e: any) { setNote(e?.message || 'Upload failed') }
    setBusy(false)
  }
  async function signOnline(payload: { form_data: Record<string, string>; signature: string; signed_name: string }) {
    if (!signing) return
    await api('/api/v1/hr/onboarding/me/sign', { method: 'POST', body: JSON.stringify({ task_id: signing.id, ...payload }) })
    setSigning(null); setNote('✓ Signed and submitted — thank you!'); await load()
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
      {/* item 4: persistent notice, shown on every visit while any work-auth doc is outstanding */}
      {(d.work_auth_pending || []).length > 0 && (
        <div style={{ ...card, background: '#fef2f2', borderColor: '#fca5a5', color: '#991b1b', fontSize: 14 }}>
          ⛔ {d.work_auth_notice || 'Your work-authorization documents are still outstanding. Your payroll will be delayed until these documents are submitted.'}
        </div>
      )}
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
              {/* item 3a: bold disclaimer above the bank-details fields */}
              {sec === 'direct_deposit' && (
                <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: '10px 12px', marginBottom: 10, fontSize: 13, fontWeight: 700, color: '#92400e' }}>
                  {d.tenant_config?.dd_disclaimer_text || 'By providing bank account information for direct deposit, I certify the routing and account numbers above are correct. If I submit incorrect information, my employer and the payroll processing company are NOT liable for any loss, delay, or misdirection of my wages that results.'}
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
                          value={vals[f.key] || ''} onChange={e => setVals(v => ({ ...v, [f.key]: e.target.value }))}
                          onBlur={f.key === 'dd_routing' ? (e => checkRouting(e.target.value)) : undefined}
                          placeholder={f.help_text || ''} />}
                    {/* item 3b: ABA checksum + bank-name confirmation, shown right under the routing field */}
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
              {/* item 3a: typed-initials acknowledgment, shown once per employee (server also gates this) */}
              {sec === 'direct_deposit' && !d.dd_disclaimer_signed && (
                <div style={{ marginTop: 10 }}>
                  <label style={lbl}>Type your initials to confirm the disclaimer above<span style={{ color: '#ef4444' }}> *</span></label>
                  <input style={{ ...inp, maxWidth: 120 }} maxLength={6} value={ddInitials} onChange={e => setDdInitials(e.target.value)} placeholder="e.g. JB" />
                </div>
              )}
              {sec === 'direct_deposit' && d.dd_disclaimer_signed && (
                <div style={{ marginTop: 8, fontSize: 12, color: '#059669' }}>✓ Disclaimer acknowledged</div>
              )}
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
                <span style={{ fontSize: 15 }}>{t.status === 'returned' ? '⚠️' : (t.has_document || t.status === 'verified' ? '✅' : '⬜')}</span>
                <span style={{ fontSize: 15, fontWeight: 600 }}>{t.label}</span>
                {t.work_auth && <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 20, background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b' }}>required for payroll</span>}
              </div>
              {t.description && <div style={{ fontSize: 13, color: 'var(--text2)', margin: '2px 0 0 23px' }}>{t.description}</div>}
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
                  {t.has_document ? '↻ Replace upload' : '⬆ Upload signed copy'}
                  <input type="file" accept={acceptAttr(d?.tenant_config?.upload_allowed_formats)} style={{ display: 'none' }} disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) upload(t, f); e.currentTarget.value = '' }} />
                </label>}
                {t.signed_at && <span style={{ fontSize: 12, color: '#059669' }}>✍️ signed online</span>}
                {t.has_document && !t.signed_at && <span style={{ fontSize: 12, color: '#059669' }}>received</span>}
              </div>
            </div>
          ))}
        </div>
      ))}
      {signing && <OnboardSignModal task={signing} onCancel={() => setSigning(null)} onSubmit={signOnline} />}
    </div>
  )
}
