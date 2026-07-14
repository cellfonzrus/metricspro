'use client'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { QRCodeSVG } from 'qrcode.react'
import { api, apiUpload } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import EntityPicker, { US_STATES } from '@/components/EntityPicker'

// HR · Employee Onboarding — one new hire's checklist. HR verifies items, views/uploads documents, sets
// the work state (so the right state tax form appears), and generates the credential-less QR a pre-start
// employee scans to fill & upload their own forms. Backed by /api/v1/hr/onboarding/* (migration 073).

type Task = {
  id: string; label: string; description?: string; owner_role: string; doc_url?: string; doc_label?: string
  is_fillable?: boolean; requires_upload?: boolean; applies_state?: string | null
  status: string; note?: string; document_name?: string; has_document?: boolean; verified_by?: string; verified_at?: string
  missing_fields?: string[] | null; returned_reason?: string | null; returned_by?: string | null
  signed_at?: string | null; signed_name?: string | null; has_signature?: boolean
  form_data?: Record<string, string> | null; validation?: { checkable?: boolean; missing?: string[]; empty?: string[]; filled?: number; fields?: number; signed?: boolean | null; online?: boolean } | null
  // migration 401 (items 1 / 4 / 6)
  is_mandatory?: boolean; work_auth?: boolean; sample_name?: string | null; sample_url?: string | null
}
type Cat = { id: string; key: string; label: string; tasks: Task[] }
type Field = { key: string; label: string; sensitive?: boolean }
type Data = {
  ready: boolean; employee_id: string; employee_name?: string; work_state?: string | null
  needs_work_state?: boolean; categories: Cat[]; progress?: { total: number; done: number }
  profile?: { has_token?: boolean; token_active?: boolean; verify_kind?: string; token_expires_at?: string | null }
  states?: string[]
  workflow_status?: string; workflow_label?: string; workflow_statuses?: { key: string; label: string }[]
  invite_method?: string | null; intake_submitted?: boolean
  intake_fields?: Field[]; intake_values?: Record<string, string>; sensitive_on_file?: string[]
  // migration 401
  mandatory_progress?: { total: number; done: number }
  work_auth_pending?: string[]; work_auth_notice?: string | null
  dd_disclaimer_signed?: boolean
  tenant_config?: { upload_allowed_formats?: string[]; dd_disclaimer_text?: string; work_auth_notice_text?: string; routing_lookup_enabled?: boolean }
}
const WF_COLOR: Record<string, string> = { invited: '#64748b', in_progress: '#d97706', docs_submitted: '#2563eb', docs_verified: '#7c3aed', provisioned: '#059669', active: '#059669' }
const ROLE_LABELS: Record<string, string> = { employee: 'Employee', hr: 'HR', dm: 'District Manager', market_manager: 'Market Manager' }
const ROLE_COLOR: Record<string, string> = { employee: '#2563eb', hr: '#7c3aed', dm: '#059669', market_manager: '#d97706' }
const ST_COLOR: Record<string, string> = { pending: '#64748b', submitted: '#d97706', verified: '#059669', na: '#94a3b8', returned: '#dc2626' }
const ST_LABEL: Record<string, string> = { pending: 'Pending', submitted: 'Submitted', verified: 'Verified', na: 'N/A', returned: 'Returned' }
const inp: React.CSSProperties = { padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const btn: React.CSSProperties = { padding: '5px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, cursor: 'pointer', background: 'var(--surface)' }
// Item 2: client-side format hint (real enforcement is server-side, magic-byte sniffed — this is just a
// fast friendly error + the file input's `accept` attribute). Config-driven off tenant_config.upload_allowed_formats.
const EXT_FOR: Record<string, string[]> = { pdf: ['.pdf'], jpeg: ['.jpg', '.jpeg'], png: ['.png'] }
function acceptAttr(formats?: string[]): string { return (formats && formats.length ? formats : ['pdf', 'jpeg']).flatMap(f => EXT_FOR[f] || []).join(',') }
function extLooksAllowed(name: string, formats?: string[]): boolean {
  const allow = (formats && formats.length ? formats : ['pdf', 'jpeg']).flatMap(f => EXT_FOR[f] || [])
  const lower = name.toLowerCase()
  return allow.some(e => lower.endsWith(e))
}
const btnP: React.CSSProperties = { ...btn, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none', fontWeight: 600 }

export default function EmployeeOnboardingPage() {
  const { employeeId } = useParams<{ employeeId: string }>()
  const { user } = useAuth()
  const [d, setD] = useState<Data | null>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState('')
  const [qr, setQr] = useState<{ url: string; expires?: string | null } | null>(null)
  const [gen, setGen] = useState<{ kind: string; value: string; expires_days: string }>({ kind: 'dob', value: '', expires_days: '' })
  const [events, setEvents] = useState<any[]>([])
  const [revealed, setRevealed] = useState<Record<string, { label: string; value: string; encrypted?: boolean }> | null>(null)
  const [revealMsg, setRevealMsg] = useState('')
  const [prov, setProv] = useState<{ role_name: string; override: boolean; reason: string; override_compliance: boolean; compliance_reason: string } | null>(null)
  const [inviteRes, setInviteRes] = useState<any>(null)
  const origin = typeof window !== 'undefined' ? window.location.origin : ''

  async function load() {
    try { setD(await api(`/api/v1/hr/onboarding/employee/${employeeId}`)) }
    catch (e: any) { setMsg(e?.message || 'Load failed') }
    try { const r = await api(`/api/v1/hr/onboarding/employee/${employeeId}/events`); setEvents(r?.events || []) } catch { /* audit optional */ }
  }
  useEffect(() => { load() }, [employeeId])  // eslint-disable-line react-hooks/exhaustive-deps
  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 4000) }

  async function sendInvite(method: 'link' | 'login') {
    setInviteRes(null)
    if (method === 'link' && !gen.value.trim()) { flash('Enter the date of birth (or last-4 SSN) above first — a link invite needs an identity gate.'); return }
    try {
      const body: any = { method, send_email: true }
      if (method === 'link') { if (gen.kind === 'dob') body.dob = gen.value; else body.ssn4 = gen.value; if (gen.expires_days) body.expires_days = Number(gen.expires_days) }
      const r = await api(`/api/v1/hr/onboarding/employee/${employeeId}/invite`, { method: 'POST', body: JSON.stringify(body) })
      setInviteRes(r); if (r.token) setQr({ url: r.portal_url, expires: r.token_expires_at })
      flash(r.emailed ? 'Invite emailed ✓' : (r.email_note || 'Invite prepared')); load()
    } catch (e: any) { flash(e?.message || 'Invite failed') }
  }
  async function advance(to_status: string, override_compliance?: boolean, compliance_override_reason?: string) {
    try {
      await api(`/api/v1/hr/onboarding/employee/${employeeId}/advance`, { method: 'POST',
        body: JSON.stringify({ to_status, actor: user?.full_name || user?.email || 'HR', override_compliance, compliance_override_reason }) })
      load()
    } catch (e: any) {
      const m = e?.message || 'Update failed'
      if (m.includes('override_compliance') && !override_compliance) {
        const reason = window.prompt(m + '\n\nType a reason to override and continue (leave blank to cancel):')
        if (reason && reason.trim()) { advance(to_status, true, reason.trim()); return }
      }
      flash(m)
    }
  }
  async function doProvision() {
    if (!prov) return
    try {
      const r = await api(`/api/v1/hr/onboarding/employee/${employeeId}/provision`, { method: 'POST',
        body: JSON.stringify({ role_name: prov.role_name || undefined, override: prov.override, reason: prov.reason || undefined,
          override_compliance: prov.override_compliance, compliance_override_reason: prov.compliance_reason || undefined,
          actor: user?.full_name || user?.email || 'HR', send_email: true }) })
      setProv(null); setInviteRes(r)
      flash(r.emailed ? `Provisioned + credentials emailed ✓ (temp pw: ${r.temp_password})` : `Provisioned ✓ — temp password: ${r.temp_password}`); load()
    } catch (e: any) {
      const m = e?.message || 'Provision failed'
      if (m.includes('override_compliance')) { flash(m); return }
      flash(m.includes('docs_incomplete') || m.includes("aren't verified") ? 'Documents aren’t verified yet — tick the override box with a reason to provision anyway.' : m)
    }
  }

  async function revealSensitive() {
    setRevealMsg('Revealing…')
    try {
      const r: any = await api(`/api/v1/hr/onboarding/employee/${employeeId}/sensitive`)
      setRevealed(r.values || {})
      setRevealMsg(r.encryption_enabled ? '' : '⚠️ Encryption key not set — these were stored in the clear.')
    } catch (e: any) { setRevealMsg('❌ ' + (e?.message || e)) }
  }

  async function setState(work_state: string) {
    try { await api(`/api/v1/hr/onboarding/employee/${employeeId}`, { method: 'PATCH', body: JSON.stringify({ work_state }) }); load() }
    catch (e: any) { flash(e?.message || 'Save failed') }
  }
  async function setStatus(t: Task, status: string) {
    try {
      await api(`/api/v1/hr/onboarding/employee/${employeeId}/task/${t.id}`, { method: 'POST',
        body: JSON.stringify({ status, verified_by: user?.full_name || user?.email || '' }) })
      load()
    } catch (e: any) { flash(e?.message || 'Update failed') }
  }
  async function uploadDoc(t: Task, file: File) {
    const formats = d?.tenant_config?.upload_allowed_formats
    if (!extLooksAllowed(file.name, formats)) { flash(`Only ${(formats || ['pdf', 'jpeg']).join('/').toUpperCase()} files are accepted here.`); return }
    try {
      const fd = new FormData(); fd.append('file', file); fd.append('task_id', t.id); fd.append('uploader', user?.full_name || user?.email || 'HR')
      await apiUpload(`/api/v1/hr/onboarding/employee/${employeeId}/upload`, fd); flash(`Uploaded ${file.name}`); load()
    } catch (e: any) { flash(e?.message || 'Upload failed') }
  }
  async function returnTask(t: Task) {
    const prefill = (t.validation?.empty || t.validation?.missing || []).slice(0, 8).join(', ')
    const missing = window.prompt('What is missing or wrong? (comma-separated — the employee sees this list in the portal AND by email)', prefill)
    if (missing === null) return
    const reason = window.prompt('Optional note to the employee:') || ''
    try {
      const r = await api(`/api/v1/hr/onboarding/employee/${employeeId}/task/${t.id}/return`, { method: 'POST',
        body: JSON.stringify({ missing_fields: missing.split(',').map(s => s.trim()).filter(Boolean), reason, actor: user?.full_name || user?.email || 'HR' }) })
      flash(r.emailed ? '↩ Returned to the employee + emailed the list ✓' : '↩ Returned (no email on file — tell them to check the portal)'); load()
    } catch (e: any) { flash(e?.message || 'Return failed — is migration 082 applied?') }
  }
  async function viewSignature(t: Task) {
    try { const r = await api(`/api/v1/hr/onboarding/employee/${employeeId}/task/${t.id}/signature`); if (r?.url) window.open(r.url, '_blank') }
    catch (e: any) { flash(e?.message || 'No signature on file') }
  }
  async function viewDoc(t: Task) {
    try { const r = await api(`/api/v1/hr/onboarding/employee/${employeeId}/task/${t.id}/doc`); if (r?.url) window.open(r.url, '_blank') }
    catch (e: any) { flash(e?.message || 'No document') }
  }
  async function mintToken() {
    if (!gen.value.trim()) { flash(gen.kind === 'dob' ? 'Enter the date of birth' : 'Enter the last 4 of SSN'); return }
    try {
      const r = await api(`/api/v1/hr/onboarding/employee/${employeeId}/token`, { method: 'POST',
        body: JSON.stringify({ verify_kind: gen.kind, verify_value: gen.value, expires_days: gen.expires_days || undefined }) })
      setQr({ url: `${origin}${r.portal_path}`, expires: r.token_expires_at }); load()
    } catch (e: any) { flash(e?.message || 'Could not generate — is migration 073 applied?') }
  }
  async function revokeToken() {
    try { await api(`/api/v1/hr/onboarding/employee/${employeeId}/token`, { method: 'DELETE' }); setQr(null); flash('Access link revoked'); load() }
    catch (e: any) { flash(e?.message || 'Revoke failed') }
  }

  const pct = d?.progress?.total ? Math.round((d.progress.done / d.progress.total) * 100) : 0

  return (
    <div style={{ padding: 24, maxWidth: 880 }}>
      <a href="/hr/people" style={{ fontSize: 12, color: 'var(--accent,#2563eb)' }}>← HR · People</a>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 2px' }}>🧩 Onboarding — {d?.employee_name || employeeId}</h1>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, margin: '8px 0' }}>{msg}</div>}

      {d && !d.ready && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '10px 14px', fontSize: 13, margin: '10px 0' }}>
        Run migration <b>073_hr_onboarding.sql</b> to activate onboarding.
      </div>}

      {d?.ready && <>
        {/* migration 401 (item 4): persistent work-auth notice — shown at the top so it's visible on
            every visit to this hire's checklist, not just tucked into one task row */}
        {(d.work_auth_pending || []).length > 0 && (
          <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b', borderRadius: 8, padding: '10px 14px', fontSize: 13, margin: '8px 0' }}>
            ⛔ {d.work_auth_notice || 'Work-authorization documents are still outstanding — payroll will be delayed until they are submitted.'}
            {' '}Outstanding: <b>{(d.work_auth_pending || []).join(', ')}</b>
          </div>
        )}

        {/* progress */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '8px 0 4px' }}>
          <div style={{ flex: 1, height: 8, background: 'var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: '#059669' }} />
          </div>
          <span style={{ fontSize: 12, color: 'var(--text2)' }}>{d.progress?.done}/{d.progress?.total} complete</span>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 12px' }}>
          {d.mandatory_progress && (d.mandatory_progress.total !== d.progress?.total || d.mandatory_progress.done !== d.progress?.done)
            ? <>of which <b>{d.mandatory_progress.done}/{d.mandatory_progress.total} mandatory</b></> : ' '}
        </div>

        {/* workflow status + provisioning */}
        <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase' }}>Workflow</span>
            <span style={{ fontSize: 12, fontWeight: 700, padding: '3px 10px', borderRadius: 20, color: '#fff', background: WF_COLOR[d.workflow_status || 'invited'] || '#64748b' }}>{d.workflow_label || d.workflow_status || 'Invited'}</span>
            {d.invite_method && <span style={{ fontSize: 11, color: 'var(--text3)' }}>invited via {d.invite_method === 'login' ? 'portal login' : 'link'}</span>}
            <div style={{ flex: 1 }} />
            {(d.workflow_status !== 'provisioned' && d.workflow_status !== 'active')
              ? <button style={{ ...btnP, background: '#059669' }} onClick={() => setProv({ role_name: '', override: false, reason: '', override_compliance: false, compliance_reason: '' })}>🚀 Provision login</button>
              : <span style={{ fontSize: 12, color: '#059669', fontWeight: 600 }}>✓ Login provisioned</span>}
          </div>
          {/* stepper */}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            {(d.workflow_statuses || []).map(s => {
              const active = s.key === d.workflow_status
              return <button key={s.key} onClick={() => advance(s.key)} title="Move the workflow to this step (out-of-order = recorded as an override)"
                style={{ ...btn, fontSize: 11, background: active ? WF_COLOR[s.key] : 'var(--surface)', color: active ? '#fff' : 'var(--text2)', border: active ? 'none' : '1px solid var(--border)' }}>{s.label}</button>
            })}
          </div>
          {prov && (
            <div style={{ marginTop: 12, padding: 12, border: '1px dashed var(--border)', borderRadius: 8, background: 'var(--surface)' }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Create this hire&apos;s login + assign their role</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <input style={inp} placeholder="RBAC role (e.g. sales_rep)" value={prov.role_name} onChange={e => setProv(p => p && { ...p, role_name: e.target.value })} />
                <label style={{ fontSize: 12, display: 'flex', gap: 5, alignItems: 'center' }}>
                  <input type="checkbox" checked={prov.override} onChange={e => setProv(p => p && { ...p, override: e.target.checked })} /> Override (docs not verified)
                </label>
              </div>
              {prov.override && <input style={{ ...inp, marginTop: 8, width: '100%' }} placeholder="Reason for overriding (recorded in the audit trail)" value={prov.reason} onChange={e => setProv(p => p && { ...p, reason: e.target.value })} />}
              {(d.work_auth_pending || []).length > 0 || d.needs_work_state ? (
                <div style={{ marginTop: 8, padding: 8, background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8 }}>
                  <label style={{ fontSize: 12, display: 'flex', gap: 5, alignItems: 'center', color: '#991b1b', fontWeight: 600 }}>
                    <input type="checkbox" checked={prov.override_compliance} onChange={e => setProv(p => p && { ...p, override_compliance: e.target.checked })} />
                    ⛔ Compliance override (work-authorization / state) — a hard floor, separately audited from the docs override above
                  </label>
                  {prov.override_compliance && <input style={{ ...inp, marginTop: 6, width: '100%' }} placeholder="Reason for the compliance override (required)" value={prov.compliance_reason} onChange={e => setProv(p => p && { ...p, compliance_reason: e.target.value })} />}
                </div>
              ) : null}
              <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
                <button style={{ ...btnP, background: '#059669' }} onClick={doProvision}>Provision & email credentials</button>
                <button style={btn} onClick={() => setProv(null)}>Cancel</button>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>Creates the Supabase login, assigns the role, emails a temp password, and marks the hire Provisioned.</div>
            </div>
          )}
          {inviteRes?.temp_password && <div style={{ marginTop: 10, fontSize: 12, background: '#ecfdf5', border: '1px solid #a7f3d0', color: '#065f46', borderRadius: 8, padding: '8px 10px' }}>
            Temp password for <b>{inviteRes.email}</b>: <code>{inviteRes.temp_password}</code>{inviteRes.emailed ? ' (emailed)' : ' — hand it over manually'}
          </div>}
        </div>

        {/* captured info (from the intake form) */}
        {(d.intake_submitted || (d.sensitive_on_file && d.sensitive_on_file.length > 0)) && (
          <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 8 }}>Captured information {d.intake_submitted && <span style={{ color: '#059669' }}>· submitted</span>}</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(180px,1fr))', gap: 8 }}>
              {(d.intake_fields || []).filter(f => !f.sensitive && (d.intake_values || {})[f.key]).map(f => (
                <div key={f.key} style={{ fontSize: 12 }}><span style={{ color: 'var(--text3)' }}>{f.label}: </span><b>{(d.intake_values || {})[f.key]}</b></div>
              ))}
            </div>
            {(d.sensitive_on_file || []).some(l => l.toLowerCase().includes('bank') || l.toLowerCase().includes('routing') || l.toLowerCase().includes('account')) && (
              <div style={{ marginTop: 8, fontSize: 12, color: d.dd_disclaimer_signed ? '#059669' : '#9a3412' }}>
                {d.dd_disclaimer_signed ? '✓ Direct-deposit disclaimer initialed by the employee (see History below for the timestamp).' : '⚠️ Direct-deposit disclaimer not yet initialed.'}
              </div>
            )}
            {d.sensitive_on_file && d.sensitive_on_file.length > 0 && (
              <div style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
                {!revealed ? (
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 12, color: 'var(--text3)' }}>🔒 Encrypted & on file: {d.sensitive_on_file.join(', ')}</span>
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 10px' }} onClick={revealSensitive}>🔓 Reveal (HR/admin · audited)</button>
                    {revealMsg && <span style={{ fontSize: 12 }}>{revealMsg}</span>}
                  </div>
                ) : (
                  <div>
                    <div style={{ fontSize: 12, color: '#92400e', marginBottom: 6 }}>🔓 Revealed — this view was recorded in the audit log.{revealMsg && <> {revealMsg}</>}</div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(200px,1fr))', gap: 8 }}>
                      {Object.entries(revealed).map(([k, v]) => (
                        <div key={k} style={{ fontSize: 12 }}><span style={{ color: 'var(--text3)' }}>{v.label}: </span><b style={{ fontFamily: 'monospace' }}>{v.value}</b></div>
                      ))}
                    </div>
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 10px', marginTop: 8 }} onClick={() => { setRevealed(null); setRevealMsg('') }}>Hide</button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* work state + QR access */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
          <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14, flex: 1, minWidth: 260 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 8 }}>Work state</div>
            <EntityPicker options={US_STATES} value={d.work_state || null} width={160}
              onChange={v => { if (v && v !== (d.work_state || '')) setState(v) }} placeholder="Work state…" clearable={false} />
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>Sets which state withholding form appears.</div>
            {d.needs_work_state && <div style={{ fontSize: 12, color: '#9a3412', marginTop: 4 }}>⚠️ Set the work state to show the state tax form.</div>}
          </div>

          <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14, flex: 1, minWidth: 280 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 8 }}>Pre-start QR access</div>
            {qr ? (
              <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                <div style={{ background: '#fff', padding: 8, borderRadius: 8 }}><QRCodeSVG value={qr.url} size={104} /></div>
                <div style={{ fontSize: 12 }}>
                  <div style={{ color: 'var(--text2)', marginBottom: 4 }}>Employee scans this — no login needed. They&apos;ll confirm their {gen.kind === 'dob' ? 'date of birth' : 'last-4 SSN'} to continue.</div>
                  <button style={{ ...btn, marginRight: 6 }} onClick={() => navigator.clipboard?.writeText(qr.url).then(() => flash('Link copied'))}>Copy link</button>
                  <button style={{ ...btn, color: '#b91c1c' }} onClick={revokeToken}>Revoke</button>
                  {qr.expires && <div style={{ color: 'var(--text3)', marginTop: 4 }}>Expires {String(qr.expires).slice(0, 10)}</div>}
                </div>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  <select style={inp} value={gen.kind} onChange={e => setGen(g => ({ ...g, kind: e.target.value, value: '' }))}>
                    <option value="dob">Verify by date of birth</option>
                    <option value="ssn4">Verify by last-4 SSN</option>
                  </select>
                  {gen.kind === 'dob'
                    ? <input style={inp} type="date" value={gen.value} onChange={e => setGen(g => ({ ...g, value: e.target.value }))} />
                    : <input style={{ ...inp, width: 80 }} maxLength={4} inputMode="numeric" placeholder="1234" value={gen.value} onChange={e => setGen(g => ({ ...g, value: e.target.value.replace(/\D/g, '') }))} />}
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <input style={{ ...inp, width: 70 }} type="number" placeholder="days" value={gen.expires_days} onChange={e => setGen(g => ({ ...g, expires_days: e.target.value }))} />
                  <span style={{ fontSize: 12, color: 'var(--text3)' }}>expiry (optional)</span>
                  <button style={btnP} onClick={mintToken}>Generate QR</button>
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', borderTop: '1px dashed var(--border)', paddingTop: 8 }}>
                  <button style={btn} onClick={() => sendInvite('link')}>✉️ Email onboarding link</button>
                  <button style={btn} onClick={() => sendInvite('login')}>🔑 Create portal login &amp; email</button>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>Email link = no password (DOB/last-4 gate). Portal login = temp password to sign into the app.</div>
                {d.profile?.has_token && d.profile?.token_active && <div style={{ fontSize: 12, color: 'var(--text3)' }}>An active link already exists — generating a new one replaces it.</div>}
              </div>
            )}
          </div>
        </div>

        {/* checklist by collapsible category */}
        {d.categories.map(c => {
          const isOpen = open[c.id] ?? true
          const done = c.tasks.filter(t => t.status === 'verified' || t.status === 'na').length
          return (
            <div key={c.id} style={{ border: '1px solid var(--border)', borderRadius: 10, marginBottom: 12, overflow: 'hidden' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', background: 'var(--surface)', cursor: 'pointer' }} onClick={() => setOpen(o => ({ ...o, [c.id]: !isOpen }))}>
                <span style={{ fontSize: 12, color: 'var(--text3)' }}>{isOpen ? '▼' : '▶'}</span>
                <b style={{ fontSize: 14 }}>{c.label}</b>
                <span style={{ fontSize: 12, color: 'var(--text3)' }}>{done}/{c.tasks.length}</span>
              </div>
              {isOpen && c.tasks.map(t => (
                <div key={t.id} style={{ padding: '10px 16px', borderTop: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 20, color: '#fff', background: ST_COLOR[t.status] }}>{ST_LABEL[t.status]}</span>
                    <span style={{ fontSize: 13, fontWeight: 600 }}>{t.label}</span>
                    <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 20, color: '#fff', background: ROLE_COLOR[t.owner_role] || '#64748b' }}>{ROLE_LABELS[t.owner_role] || t.owner_role}</span>
                    {t.applies_state && <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 20, background: 'var(--border)', color: 'var(--text2)' }}>{t.applies_state}</span>}
                    {t.is_mandatory === false && <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 20, background: 'var(--border)', color: 'var(--text3)' }}>optional</span>}
                    {t.work_auth && <span title="Blocks provisioning/active until complete (item 4)" style={{ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 20, background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b' }}>⛔ work-auth</span>}
                  </div>
                  {t.description && <div style={{ fontSize: 12, color: 'var(--text3)', margin: '3px 0' }}>{t.description}</div>}
                  {t.status === 'returned' && (
                    <div style={{ margin: '4px 0', background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8, padding: '6px 10px', fontSize: 12, color: '#991b1b' }}>
                      ↩ Returned{t.returned_by ? ` by ${t.returned_by}` : ''}{(t.missing_fields || []).length > 0 && <> — missing: <b>{(t.missing_fields || []).join(', ')}</b></>}
                      {t.returned_reason && <div style={{ marginTop: 2 }}>{t.returned_reason}</div>}
                    </div>
                  )}
                  {t.status === 'submitted' && t.validation && !t.validation.online && (
                    <div style={{ margin: '4px 0', fontSize: 12, color: t.validation.checkable ? '#92400e' : 'var(--text3)' }}>
                      {t.validation.checkable
                        ? `🔎 Auto-check: ${t.validation.filled}/${t.validation.fields} fields filled${(t.validation.empty || []).length ? ` · ${(t.validation.empty || []).length} empty` : ''}${t.validation.signed === null ? ' · signature not machine-checkable' : t.validation.signed ? ' · signed ✓' : ''}`
                        : '🔎 Not machine-checkable (scan/photo) — review the signature by eye.'}
                    </div>
                  )}
                  {t.signed_at && (
                    <div style={{ margin: '4px 0', fontSize: 12, color: '#059669' }}>✍️ Signed online {String(t.signed_at).slice(0, 10)}{t.signed_name ? ` as “${t.signed_name}”` : ''}
                      {t.form_data && Object.keys(t.form_data).length > 0 && <span style={{ color: 'var(--text2)' }}> — {Object.entries(t.form_data).map(([k, v]) => `${k}: ${v}`).join(' · ')}</span>}
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 6 }}>
                    {t.doc_url && <a href={t.doc_url} target="_blank" rel="noreferrer" style={{ ...btn, color: 'var(--accent,#2563eb)', textDecoration: 'none' }}>🔗 {t.doc_label || 'Open form'}</a>}
                    {t.sample_url && <a href={t.sample_url} target="_blank" rel="noreferrer" style={{ ...btn, color: 'var(--accent,#2563eb)', textDecoration: 'none' }} title="Compare this submission against a correctly completed example">👁 Completed sample</a>}
                    {t.has_document
                      ? <button style={btn} onClick={() => viewDoc(t)}>📄 View {t.document_name ? `(${t.document_name})` : 'upload'}</button>
                      : <span style={{ fontSize: 12, color: 'var(--text3)' }}>{t.requires_upload ? 'no document yet' : ''}</span>}
                    {t.has_signature && <button style={btn} onClick={() => viewSignature(t)}>✍️ View signature</button>}
                    <label style={{ ...btn }}>⬆ Upload<input type="file" accept={acceptAttr(d?.tenant_config?.upload_allowed_formats)} style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadDoc(t, f); e.currentTarget.value = '' }} /></label>
                    {t.status !== 'verified' && <button style={{ ...btn, color: '#059669' }} onClick={() => setStatus(t, 'verified')}>✓ Verify</button>}
                    {(t.status === 'submitted' || t.status === 'verified') && <button style={{ ...btn, color: '#dc2626' }} onClick={() => returnTask(t)}>↩ Return for fixes</button>}
                    {t.status !== 'na' && <button style={btn} onClick={() => setStatus(t, 'na')}>N/A</button>}
                    {(t.status === 'verified' || t.status === 'na') && <button style={btn} onClick={() => setStatus(t, 'pending')}>↺ Reset</button>}
                    {t.verified_by && t.status === 'verified' && <span style={{ fontSize: 11, color: 'var(--text3)' }}>by {t.verified_by}{t.verified_at ? ` · ${String(t.verified_at).slice(0, 10)}` : ''}</span>}
                  </div>
                </div>
              ))}
            </div>
          )
        })}

        {/* audit trail — the workflow stays in the system */}
        {events.length > 0 && (
          <div style={{ border: '1px solid var(--border)', borderRadius: 10, marginTop: 6 }}>
            <div style={{ padding: '10px 14px', fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase' }}>History</div>
            {events.map((e, i) => (
              <div key={e.id || i} style={{ padding: '8px 14px', borderTop: '1px solid var(--border)', fontSize: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--text3)', minWidth: 128 }}>{String(e.created_at || '').replace('T', ' ').slice(0, 16)}</span>
                <b>{(e.event_type || '').replace(/_/g, ' ')}</b>
                {e.from_status && <span style={{ color: 'var(--text3)' }}>{e.from_status} → {e.to_status}</span>}
                {e.is_override && <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 10, background: '#fef3c7', color: '#92400e' }}>OVERRIDE</span>}
                {e.actor && <span style={{ color: 'var(--text3)' }}>by {e.actor}</span>}
                {e.reason && <span style={{ color: 'var(--text2)' }}>— {e.reason}</span>}
              </div>
            ))}
          </div>
        )}
      </>}
    </div>
  )
}
