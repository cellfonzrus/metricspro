'use client'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { QRCodeSVG } from 'qrcode.react'
import { api, apiUpload } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// HR · Employee Onboarding — one new hire's checklist. HR verifies items, views/uploads documents, sets
// the work state (so the right state tax form appears), and generates the credential-less QR a pre-start
// employee scans to fill & upload their own forms. Backed by /api/v1/hr/onboarding/* (migration 073).

type Task = {
  id: string; label: string; description?: string; owner_role: string; doc_url?: string; doc_label?: string
  is_fillable?: boolean; requires_upload?: boolean; applies_state?: string | null
  status: string; note?: string; document_name?: string; has_document?: boolean; verified_by?: string; verified_at?: string
}
type Cat = { id: string; key: string; label: string; tasks: Task[] }
type Data = {
  ready: boolean; employee_id: string; employee_name?: string; work_state?: string | null
  needs_work_state?: boolean; categories: Cat[]; progress?: { total: number; done: number }
  profile?: { has_token?: boolean; token_active?: boolean; verify_kind?: string; token_expires_at?: string | null }
  states?: string[]
}
const ROLE_LABELS: Record<string, string> = { employee: 'Employee', hr: 'HR', dm: 'District Manager', market_manager: 'Market Manager' }
const ROLE_COLOR: Record<string, string> = { employee: '#2563eb', hr: '#7c3aed', dm: '#059669', market_manager: '#d97706' }
const ST_COLOR: Record<string, string> = { pending: '#64748b', submitted: '#d97706', verified: '#059669', na: '#94a3b8' }
const ST_LABEL: Record<string, string> = { pending: 'Pending', submitted: 'Submitted', verified: 'Verified', na: 'N/A' }
const inp: React.CSSProperties = { padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const btn: React.CSSProperties = { padding: '5px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, cursor: 'pointer', background: 'var(--surface)' }
const btnP: React.CSSProperties = { ...btn, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none', fontWeight: 600 }

export default function EmployeeOnboardingPage() {
  const { employeeId } = useParams<{ employeeId: string }>()
  const { user } = useAuth()
  const [d, setD] = useState<Data | null>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState('')
  const [qr, setQr] = useState<{ url: string; expires?: string | null } | null>(null)
  const [gen, setGen] = useState<{ kind: string; value: string; expires_days: string }>({ kind: 'dob', value: '', expires_days: '' })
  const origin = typeof window !== 'undefined' ? window.location.origin : ''

  async function load() {
    try { setD(await api(`/api/v1/hr/onboarding/employee/${employeeId}`)) }
    catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  useEffect(() => { load() }, [employeeId])  // eslint-disable-line react-hooks/exhaustive-deps
  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 4000) }

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
    try {
      const fd = new FormData(); fd.append('file', file); fd.append('task_id', t.id); fd.append('uploader', user?.full_name || user?.email || 'HR')
      await apiUpload(`/api/v1/hr/onboarding/employee/${employeeId}/upload`, fd); flash(`Uploaded ${file.name}`); load()
    } catch (e: any) { flash(e?.message || 'Upload failed') }
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
        {/* progress */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '8px 0 16px' }}>
          <div style={{ flex: 1, height: 8, background: 'var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: '#059669' }} />
          </div>
          <span style={{ fontSize: 12, color: 'var(--text2)' }}>{d.progress?.done}/{d.progress?.total} complete</span>
        </div>

        {/* work state + QR access */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
          <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14, flex: 1, minWidth: 260 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 8 }}>Work state</div>
            <input style={{ ...inp, width: 120 }} list="states" defaultValue={d.work_state || ''} placeholder="e.g. NY"
              onBlur={e => { const v = e.target.value.trim().toUpperCase(); if (v !== (d.work_state || '')) setState(v) }} />
            <datalist id="states">{(d.states || []).map(s => <option key={s} value={s} />)}</datalist>
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
                  </div>
                  {t.description && <div style={{ fontSize: 12, color: 'var(--text3)', margin: '3px 0' }}>{t.description}</div>}
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 6 }}>
                    {t.doc_url && <a href={t.doc_url} target="_blank" rel="noreferrer" style={{ ...btn, color: 'var(--accent,#2563eb)', textDecoration: 'none' }}>🔗 {t.doc_label || 'Open form'}</a>}
                    {t.has_document
                      ? <button style={btn} onClick={() => viewDoc(t)}>📄 View {t.document_name ? `(${t.document_name})` : 'upload'}</button>
                      : <span style={{ fontSize: 12, color: 'var(--text3)' }}>{t.requires_upload ? 'no document yet' : ''}</span>}
                    <label style={{ ...btn }}>⬆ Upload<input type="file" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadDoc(t, f); e.currentTarget.value = '' }} /></label>
                    {t.status !== 'verified' && <button style={{ ...btn, color: '#059669' }} onClick={() => setStatus(t, 'verified')}>✓ Verify</button>}
                    {t.status !== 'na' && <button style={btn} onClick={() => setStatus(t, 'na')}>N/A</button>}
                    {(t.status === 'verified' || t.status === 'na') && <button style={btn} onClick={() => setStatus(t, 'pending')}>↺ Reset</button>}
                    {t.verified_by && t.status === 'verified' && <span style={{ fontSize: 11, color: 'var(--text3)' }}>by {t.verified_by}{t.verified_at ? ` · ${String(t.verified_at).slice(0, 10)}` : ''}</span>}
                  </div>
                </div>
              ))}
            </div>
          )
        })}
      </>}
    </div>
  )
}
