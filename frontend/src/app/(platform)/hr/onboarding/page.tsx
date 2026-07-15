'use client'
import { useEffect, useState } from 'react'
import { api, apiUpload } from '@/lib/client'
import EntityPicker, { US_STATES } from '@/components/EntityPicker'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

// HR · Onboarding Checklist (admin) — the CONFIGURABLE template every new hire is onboarded against.
// Items group under collapsible CATEGORIES; each item has an OWNER role (Employee / HR / DM / Market
// Manager), an optional live state/federal document LINK, and upload/fillable flags. Per-employee
// progress + uploaded documents live on the employee's record (open from HR · People → Onboarding).
// Backed by storeops.onboarding_category / onboarding_task (migration 073).

type Task = {
  id: string; category_id: string; key?: string; label: string; description?: string; owner_role: string
  doc_url?: string; doc_label?: string; is_fillable?: boolean; requires_upload?: boolean
  applies_state?: string | null; sort_order?: number; is_active?: boolean
  template_name?: string | null
  requires_signature?: boolean; form_fields?: { key?: string; label?: string; required?: boolean }[] | string | null
  // migration 401 (items 1 / 4 / 6)
  is_mandatory?: boolean; work_auth?: boolean; sample_name?: string | null
}
type Cat = { id: string; key: string; label: string; sort_order?: number; is_active?: boolean; tasks: Task[] }
type IField = { id?: string; key?: string; label: string; section?: string; field_type?: string; options?: string[] | null; required?: boolean; propagate_to?: string | null; sensitive?: boolean; help_text?: string; sort_order?: number; is_active?: boolean }
const SECTIONS = ['personal', 'address', 'emergency', 'work_eligibility', 'tax', 'direct_deposit', 'policies', 'custom']
const FIELD_TYPES = ['text', 'date', 'tel', 'email', 'number', 'select']

const ROLE_LABELS: Record<string, string> = { employee: 'Employee', hr: 'HR', dm: 'District Manager', market_manager: 'Market Manager' }
const ROLE_COLOR: Record<string, string> = { employee: '#2563eb', hr: '#7c3aed', dm: '#059669', market_manager: '#d97706' }
const inp: React.CSSProperties = { padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', width: '100%' }
const btn: React.CSSProperties = { padding: '7px 12px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, cursor: 'pointer', background: 'var(--surface)' }
const btnP: React.CSSProperties = { ...btn, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none', fontWeight: 600 }
const chip = (role: string): React.CSSProperties => ({ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 20, color: '#fff', background: ROLE_COLOR[role] || '#64748b' })

export default function OnboardingAdminPage() {
  const [tab, setTab] = useState<'docs' | 'done' | 'setup' | 'reconcile'>('docs')
  const [cats, setCats] = useState<Cat[]>([])
  const [ready, setReady] = useState(true)
  const [states, setStates] = useState<string[]>([])
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [editing, setEditing] = useState<Partial<Task> | null>(null)  // task add/edit form
  const [msg, setMsg] = useState('')
  const [ifields, setIfields] = useState<IField[]>([])
  const [propagatable, setPropagatable] = useState<string[]>([])
  const [fedit, setFedit] = useState<IField | null>(null)  // intake-field add/edit form

  async function load() {
    try {
      const d = await api('/api/v1/hr/onboarding/template?include_inactive=true')
      setReady(d?.ready !== false); setCats(d?.categories || []); setStates(d?.states || [])
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
    try {
      const f = await api('/api/v1/hr/onboarding/intake-fields?include_inactive=true')
      setIfields(f?.fields || []); setPropagatable(f?.propagatable || [])
    } catch { /* intake config optional (pre-077) */ }
  }
  useEffect(() => { load() }, [])
  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 4000) }

  async function saveField() {
    if (!fedit?.label?.trim()) { flash('Field label required'); return }
    const isNew = !fedit.id
    const path = isNew ? '/api/v1/hr/onboarding/intake-fields' : `/api/v1/hr/onboarding/intake-fields/${fedit.id}`
    const body: any = { ...fedit }
    if (typeof body.options === 'string') body.options = String(body.options).split(',').map((s: string) => s.trim()).filter(Boolean)
    try { await api(path, { method: isNew ? 'POST' : 'PATCH', body: JSON.stringify(body) }); setFedit(null); load() }
    catch (e: any) { flash(e?.message || 'Save failed — is migration 077 applied?') }
  }
  async function deleteField(f: IField) {
    if (!window.confirm(`Delete field "${f.label}"?`)) return
    try { await api(`/api/v1/hr/onboarding/intake-fields/${f.id}`, { method: 'DELETE' }); load() }
    catch (e: any) { flash(e?.message || 'Delete failed') }
  }
  const fupd = (patch: Partial<IField>) => setFedit(v => ({ ...(v || { label: '' }), ...patch }))

  async function addCategory() {
    const label = window.prompt('New category name (e.g. "Benefits Enrollment")')?.trim()
    if (!label) return
    try { await api('/api/v1/hr/onboarding/categories', { method: 'POST', body: JSON.stringify({ label, sort_order: (cats.length + 1) * 10 }) }); load() }
    catch (e: any) { flash(e?.message || 'Save failed — is migration 073 applied?') }
  }
  async function renameCategory(c: Cat) {
    const label = window.prompt('Rename category', c.label)?.trim()
    if (!label || label === c.label) return
    try { await api(`/api/v1/hr/onboarding/categories/${c.id}`, { method: 'PATCH', body: JSON.stringify({ label }) }); load() }
    catch (e: any) { flash(e?.message || 'Save failed') }
  }
  async function deleteCategory(c: Cat) {
    if (!window.confirm(`Delete "${c.label}" and its ${c.tasks.length} item(s)?`)) return
    try { await api(`/api/v1/hr/onboarding/categories/${c.id}`, { method: 'DELETE' }); load() }
    catch (e: any) { flash(e?.message || 'Delete failed') }
  }
  async function saveTask() {
    if (!editing?.label?.trim()) { flash('Label required'); return }
    const isNew = !editing.id
    const path = isNew ? '/api/v1/hr/onboarding/tasks' : `/api/v1/hr/onboarding/tasks/${editing.id}`
    const body: any = { ...editing }
    if (typeof body.form_fields === 'string') {
      const labels = String(body.form_fields).split(',').map((s: string) => s.trim()).filter(Boolean)
      body.form_fields = labels.length ? labels.map((label: string) => ({
        key: label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, ''), label, required: true })) : null
    }
    try {
      await api(path, { method: isNew ? 'POST' : 'PATCH', body: JSON.stringify(body) })
      setEditing(null); load()
    } catch (e: any) { flash(e?.message || 'Save failed — is migration 073 applied?') }
  }
  async function deleteTask(t: Task) {
    if (!window.confirm(`Delete "${t.label}"?`)) return
    try { await api(`/api/v1/hr/onboarding/tasks/${t.id}`, { method: 'DELETE' }); load() }
    catch (e: any) { flash(e?.message || 'Delete failed') }
  }
  async function uploadTemplate(t: Task, file: File) {
    const fd = new FormData(); fd.append('file', file)
    try { const r = await apiUpload(`/api/v1/hr/onboarding/tasks/${t.id}/template`, fd); flash(`📎 Template "${r.template_name}" attached to "${t.label}"`); load() }
    catch (e: any) { flash(e?.message || 'Upload failed — is migration 080 applied?') }
  }
  async function downloadTemplate(t: Task) {
    try { const r = await api(`/api/v1/hr/onboarding/tasks/${t.id}/template`); if (r?.url) window.open(r.url, '_blank') }
    catch (e: any) { flash(e?.message || 'Could not open template') }
  }
  async function removeTemplate(t: Task) {
    if (!window.confirm(`Remove the template document from "${t.label}"?`)) return
    try { await api(`/api/v1/hr/onboarding/tasks/${t.id}/template`, { method: 'DELETE' }); load() }
    catch (e: any) { flash(e?.message || 'Remove failed') }
  }
  // item 6: completed-sample document — same upload/download/remove pattern as the blank template above
  async function uploadSample(t: Task, file: File) {
    const fd = new FormData(); fd.append('file', file)
    try { const r = await apiUpload(`/api/v1/hr/onboarding/tasks/${t.id}/sample`, fd); flash(`👁 Sample "${r.sample_name}" attached to "${t.label}"`); load() }
    catch (e: any) { flash(e?.message || 'Upload failed — is migration 401 applied?') }
  }
  async function downloadSample(t: Task) {
    try { const r = await api(`/api/v1/hr/onboarding/tasks/${t.id}/sample`); if (r?.url) window.open(r.url, '_blank') }
    catch (e: any) { flash(e?.message || 'Could not open sample') }
  }
  async function removeSample(t: Task) {
    if (!window.confirm(`Remove the completed-sample document from "${t.label}"?`)) return
    try { await api(`/api/v1/hr/onboarding/tasks/${t.id}/sample`, { method: 'DELETE' }); load() }
    catch (e: any) { flash(e?.message || 'Remove failed') }
  }
  const upd = (patch: Partial<Task>) => setEditing(v => ({ ...v, ...patch }))

  return (
    <div style={{ padding: 24, maxWidth: 920 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🧩 Onboarding Checklist</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 16 }}>
        The template HR runs for every new hire. Group items into collapsible categories and assign each to whoever owns it
        (Employee, HR, the DM, or the Market Manager). Add a live form link — or 📎 upload a default template document (a blank W-4, a policy PDF, the handbook) that every new hire downloads from their onboarding portal.
        Open a person&apos;s checklist from <a href="/hr/people" style={{ color: 'var(--accent,#2563eb)' }}>HR · People</a>, or browse every
        uploaded/signed document across the roster in the <a href="/hr/compliance" style={{ color: 'var(--accent,#2563eb)' }}>Compliance Document Repository</a>.
      </p>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}
      {!ready && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '10px 14px', fontSize: 13, marginBottom: 14 }}>
        Run migration <b>073_hr_onboarding.sql</b> in Supabase to activate the checklist. Until then this page is empty.
      </div>}

      {/* tabs: the operational Documents board vs the checklist/intake template setup */}
      <div style={{ display: 'flex', gap: 6, borderBottom: '1px solid var(--border)', marginBottom: 16 }}>
        {([['docs', '📤 Documents'], ['done', '✅ Completed'], ['reconcile', '🔁 Reconcile mandatory docs'], ['setup', '🧩 Checklist setup']] as const).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} style={{ padding: '8px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer', background: 'none', border: 'none', borderBottom: tab === k ? '2px solid var(--accent,#2563eb)' : '2px solid transparent', color: tab === k ? 'var(--accent,#2563eb)' : 'var(--text2)' }}>{l}</button>
        ))}
      </div>

      {tab === 'docs' && <DocumentsBoard />}

      {tab === 'done' && <CompletedDocuments />}

      {tab === 'reconcile' && <ReconcilePanel />}

      {tab === 'setup' && <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <button style={btnP} onClick={addCategory}>＋ Add category</button>
      </div>

      {cats.map(c => {
        const isOpen = open[c.id] ?? true
        return (
          <div key={c.id} style={{ border: '1px solid var(--border)', borderRadius: 10, marginBottom: 12, overflow: 'hidden', opacity: c.is_active === false ? 0.55 : 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', background: 'var(--surface)', cursor: 'pointer' }}
              onClick={() => setOpen(o => ({ ...o, [c.id]: !isOpen }))}>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>{isOpen ? '▼' : '▶'}</span>
              <b style={{ fontSize: 14 }}>{c.label}</b>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>({c.tasks.length})</span>
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }} onClick={e => e.stopPropagation()}>
                <button style={{ ...btn, fontSize: 11, padding: '4px 8px' }} onClick={() => setEditing({ category_id: c.id, owner_role: 'employee', requires_upload: true, sort_order: (c.tasks.length + 1) * 10 })}>＋ Item</button>
                <button style={{ ...btn, fontSize: 11, padding: '4px 8px' }} onClick={() => renameCategory(c)}>Rename</button>
                <button style={{ ...btn, fontSize: 11, padding: '4px 8px', color: '#b91c1c' }} onClick={() => deleteCategory(c)}>Delete</button>
              </div>
            </div>
            {isOpen && (
              <div style={{ padding: '4px 0' }}>
                {c.tasks.length === 0 && <div style={{ padding: '10px 16px', color: 'var(--text3)', fontSize: 13 }}>No items yet — add one above.</div>}
                {c.tasks.map(t => (
                  <div key={t.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 16px', borderTop: '1px solid var(--border)', opacity: t.is_active === false ? 0.5 : 1 }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 13, fontWeight: 600 }}>{t.label}</span>
                        <span style={chip(t.owner_role)}>{ROLE_LABELS[t.owner_role] || t.owner_role}</span>
                        {t.applies_state && <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 20, background: 'var(--border)', color: 'var(--text2)' }}>{t.applies_state}</span>}
                        {t.requires_upload && <span style={{ fontSize: 11, color: 'var(--text3)' }}>⬆ upload</span>}
                        {t.is_fillable && <span style={{ fontSize: 11, color: 'var(--text3)' }}>✎ fillable</span>}
                        {t.is_mandatory === false && <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 20, background: 'var(--border)', color: 'var(--text3)' }}>optional</span>}
                        {t.work_auth && <span title="Blocking: cannot advance to provisioned/active until complete" style={{ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 20, background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b' }}>⛔ work-auth</span>}
                      </div>
                      {t.description && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{t.description}</div>}
                      {t.doc_url && <div><a href={t.doc_url} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: 'var(--accent,#2563eb)' }}>🔗 {t.doc_label || 'Document link'}</a></div>}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
                        {t.template_name ? (
                          <>
                            <button onClick={() => downloadTemplate(t)} style={{ fontSize: 12, color: 'var(--accent,#2563eb)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>📎 {t.template_name}</button>
                            <label style={{ fontSize: 11, color: 'var(--text3)', cursor: 'pointer', textDecoration: 'underline' }}>replace
                              <input type="file" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadTemplate(t, f); e.currentTarget.value = '' }} /></label>
                            <button onClick={() => removeTemplate(t)} style={{ fontSize: 11, color: '#b91c1c', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>remove</button>
                          </>
                        ) : (
                          <label style={{ fontSize: 12, color: 'var(--text3)', cursor: 'pointer' }}>📎 Upload template document
                            <input type="file" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadTemplate(t, f); e.currentTarget.value = '' }} /></label>
                        )}
                      </div>
                      {/* item 6: completed sample (what a correctly filled-out submission looks like) */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
                        {t.sample_name ? (
                          <>
                            <button onClick={() => downloadSample(t)} style={{ fontSize: 12, color: 'var(--accent,#2563eb)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>👁 {t.sample_name}</button>
                            <label style={{ fontSize: 11, color: 'var(--text3)', cursor: 'pointer', textDecoration: 'underline' }}>replace
                              <input type="file" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadSample(t, f); e.currentTarget.value = '' }} /></label>
                            <button onClick={() => removeSample(t)} style={{ fontSize: 11, color: '#b91c1c', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>remove</button>
                          </>
                        ) : (
                          <label style={{ fontSize: 12, color: 'var(--text3)', cursor: 'pointer' }}>👁 Upload completed sample
                            <input type="file" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadSample(t, f); e.currentTarget.value = '' }} /></label>
                        )}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button style={{ ...btn, fontSize: 11, padding: '4px 8px' }} onClick={() => setEditing(t)}>Edit</button>
                      <button style={{ ...btn, fontSize: 11, padding: '4px 8px', color: '#b91c1c' }} onClick={() => deleteTask(t)}>✕</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}

      {/* ── Employee information capture form (configurable structured intake) ── */}
      <div style={{ marginTop: 26, paddingTop: 18, borderTop: '2px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>📝 Employee information form</h2>
          <div style={{ flex: 1 }} />
          <button style={btnP} onClick={() => setFedit({ label: '', section: 'personal', field_type: 'text', required: false, sensitive: false, sort_order: (ifields.length + 1) * 10 })}>＋ Add field</button>
        </div>
        <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 12px' }}>
          The structured fields a new hire fills in the portal. Values sync into their employee record automatically
          (map a field to a record column with <b>Propagate to</b>). Mark bank details <b>private</b> so they&apos;re never shown back. Tailor these to your own HR intake form.
        </p>
        {ifields.length === 0 && <div style={{ fontSize: 13, color: 'var(--text3)', padding: '8px 0' }}>No fields yet{ready ? '' : ' — run migration 077 first'}.</div>}
        {SECTIONS.filter(sec => ifields.some(f => (f.section || 'personal') === sec)).map(sec => (
          <div key={sec} style={{ border: '1px solid var(--border)', borderRadius: 10, marginBottom: 10, overflow: 'hidden' }}>
            <div style={{ padding: '8px 14px', background: 'var(--surface)', fontSize: 12, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text3)' }}>{sec.replace('_', ' ')}</div>
            {ifields.filter(f => (f.section || 'personal') === sec).map(f => (
              <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', borderTop: '1px solid var(--border)', opacity: f.is_active === false ? 0.5 : 1 }}>
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{f.label}</span>
                  <span style={{ fontSize: 11, color: 'var(--text3)', marginLeft: 8 }}>{f.field_type}{f.required ? ' · required' : ''}{f.propagate_to ? ` · → ${f.propagate_to}` : ''}{f.sensitive ? ' · 🔒 private' : ''}</span>
                </div>
                <button style={{ ...btn, fontSize: 11, padding: '4px 8px' }} onClick={() => setFedit({ ...f, options: (f.options || []) as any })}>Edit</button>
                <button style={{ ...btn, fontSize: 11, padding: '4px 8px', color: '#b91c1c' }} onClick={() => deleteField(f)}>✕</button>
              </div>
            ))}
          </div>
        ))}
      </div>
      </>}

      {fedit && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }} onClick={() => setFedit(null)}>
          <div style={{ background: 'var(--bg,#fff)', border: '1px solid var(--border)', borderRadius: 12, padding: 20, width: 460, maxWidth: '92vw', maxHeight: '90vh', overflow: 'auto' }} onClick={e => e.stopPropagation()}>
            <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 14 }}>{fedit.id ? 'Edit field' : 'New field'}</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Label
                <input style={inp} value={fedit.label || ''} onChange={e => fupd({ label: e.target.value })} placeholder="e.g. Shirt size" /></label>
              <div style={{ display: 'flex', gap: 10 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', flex: 1 }}>Section
                  <select style={inp} value={fedit.section || 'personal'} onChange={e => fupd({ section: e.target.value })}>{SECTIONS.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}</select></label>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', flex: 1 }}>Type
                  <select style={inp} value={fedit.field_type || 'text'} onChange={e => fupd({ field_type: e.target.value })}>{FIELD_TYPES.map(s => <option key={s} value={s}>{s}</option>)}</select></label>
              </div>
              {fedit.field_type === 'select' && <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Options (comma-separated)
                <input style={inp} value={Array.isArray(fedit.options) ? fedit.options.join(', ') : (fedit.options || '')} onChange={e => fupd({ options: e.target.value as any })} placeholder="Small, Medium, Large" /></label>}
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Propagate to employee record (optional)
                <select style={inp} value={fedit.propagate_to || ''} onChange={e => fupd({ propagate_to: e.target.value || null })}>
                  <option value="">— keep in onboarding only —</option>
                  {propagatable.map(c => <option key={c} value={c}>{c}</option>)}
                </select></label>
              <div style={{ display: 'flex', gap: 16, fontSize: 13 }}>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={!!fedit.required} onChange={e => fupd({ required: e.target.checked })} /> Required</label>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={!!fedit.sensitive} onChange={e => fupd({ sensitive: e.target.checked })} /> Private (bank/SSN — never shown back)</label>
              </div>
              {fedit.id && <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}><input type="checkbox" checked={fedit.is_active !== false} onChange={e => fupd({ is_active: e.target.checked })} /> Active</label>}
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 18 }}>
              <button style={btn} onClick={() => setFedit(null)}>Cancel</button>
              <button style={btnP} onClick={saveField}>Save field</button>
            </div>
          </div>
        </div>
      )}

      {editing && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }} onClick={() => setEditing(null)}>
          <div style={{ background: 'var(--bg,#fff)', border: '1px solid var(--border)', borderRadius: 12, padding: 20, width: 460, maxWidth: '92vw', maxHeight: '90vh', overflow: 'auto' }} onClick={e => e.stopPropagation()}>
            <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 14 }}>{editing.id ? 'Edit item' : 'New item'}</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Label
                <input style={inp} value={editing.label || ''} onChange={e => upd({ label: e.target.value })} placeholder="e.g. Federal Form W-4" /></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Description
                <textarea style={{ ...inp, minHeight: 50 }} value={editing.description || ''} onChange={e => upd({ description: e.target.value })} /></label>
              <div style={{ display: 'flex', gap: 10 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', flex: 1 }}>Responsible
                  <select style={inp} value={editing.owner_role || 'employee'} onChange={e => upd({ owner_role: e.target.value })}>
                    {Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select></label>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', flex: 1 }}>Applies to state
                  <EntityPicker options={US_STATES} value={editing.applies_state || null}
                    onChange={v => upd({ applies_state: v })} placeholder="All states" clearable width="100%" />
                  </label>
              </div>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Live document link (URL)
                <input style={inp} value={editing.doc_url || ''} onChange={e => upd({ doc_url: e.target.value })} placeholder="https://www.irs.gov/pub/irs-pdf/fw4.pdf" /></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Document label
                <input style={inp} value={editing.doc_label || ''} onChange={e => upd({ doc_label: e.target.value })} placeholder="IRS Form W-4" /></label>
              <div style={{ display: 'flex', gap: 16, fontSize: 13, flexWrap: 'wrap' }}>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={!!editing.requires_upload} onChange={e => upd({ requires_upload: e.target.checked })} /> Requires upload</label>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={!!editing.is_fillable} onChange={e => upd({ is_fillable: e.target.checked })} /> Fillable online</label>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={editing.requires_signature !== false} onChange={e => upd({ requires_signature: e.target.checked })} /> Requires signature</label>
              </div>
              <div style={{ display: 'flex', gap: 16, fontSize: 13, flexWrap: 'wrap', padding: 8, background: 'var(--surface)', borderRadius: 8 }}>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center' }} title="Uncheck to make this item optional — it stops counting toward checklist completion">
                  <input type="checkbox" checked={editing.is_mandatory !== false} onChange={e => upd({ is_mandatory: e.target.checked })} /> Mandatory
                </label>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center', color: '#991b1b' }} title="Blocking: the hire cannot be provisioned/marked active while this is outstanding — server-enforced, not just a UI hint">
                  <input type="checkbox" checked={!!editing.work_auth} onChange={e => upd({ work_auth: e.target.checked })} /> ⛔ Work-authorization (blocking)
                </label>
              </div>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Online sign form fields (optional, comma-separated)
                <input style={inp} value={Array.isArray(editing.form_fields) ? editing.form_fields.map((f: any) => f.label || f.key).join(', ') : (editing.form_fields || '')}
                  onChange={e => upd({ form_fields: e.target.value as any })} placeholder="e.g. Filing status, Number of dependents, Extra withholding" />
                <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--text3)' }}>Shown in the employee&apos;s “Fill &amp; sign online” form above the signature box. Leave blank for a sign-only acknowledgement.</span></label>
              {editing.id && <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}><input type="checkbox" checked={editing.is_active !== false} onChange={e => upd({ is_active: e.target.checked })} /> Active</label>}
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 18 }}>
              <button style={btn} onClick={() => setEditing(null)}>Cancel</button>
              <button style={btnP} onClick={saveTask}>Save item</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


// ── 📤 Documents board — who was SENT the onboarding packet, what came BACK, what's missing ──────
function DocumentsBoard() {
  const [rows, setRows] = useState<any[]>([])
  const [ready, setReady] = useState(true)
  const [sel, setSel] = useState<Record<string, boolean>>({})
  const [filter, setFilter] = useState('all')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [res, setRes] = useState<any>(null)

  async function load() {
    try { const d = await api('/api/v1/hr/onboarding/doc-status'); setReady(d?.ready !== false); setRows(d?.employees || []) }
    catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  useEffect(() => { load() }, [])

  const shown = rows.filter(r => {
    if (q && !`${r.name || ''} ${r.employee_id || ''} ${r.email || ''}`.toLowerCase().includes(q.toLowerCase())) return false
    if (filter === 'not_sent') return !r.sent
    if (filter === 'awaiting') return r.sent && (r.pending > 0 || r.returned > 0)
    if (filter === 'returned') return r.returned > 0
    if (filter === 'complete') return r.total > 0 && r.pending === 0 && r.returned === 0
    return true
  })
  const selIds = Object.keys(sel).filter(k => sel[k])
  const allShownSel = shown.length > 0 && shown.every(r => sel[r.employee_id])
  const fmt = (d?: string | null) => (d ? String(d).slice(0, 10) : '')

  async function sendDocs() {
    if (!selIds.length) return
    if (!window.confirm(`Send the onboarding documents to ${selIds.length} employee(s)? Each gets an email with their personal onboarding link.`)) return
    setBusy(true); setRes(null); setMsg('')
    try { const r = await api('/api/v1/hr/onboarding/send-documents', { method: 'POST', body: JSON.stringify({ employee_ids: selIds }) }); setRes(r); setSel({}); load() }
    catch (e: any) { setMsg(e?.message || 'Send failed') }
    setBusy(false)
  }

  const chips: [string, string][] = [['all', 'All'], ['not_sent', 'Not sent'], ['awaiting', 'Awaiting return'], ['returned', 'Returned for fixes'], ['complete', 'All back']]
  const count = (k: string) => rows.filter(r => (k === 'not_sent' ? !r.sent : k === 'awaiting' ? r.sent && (r.pending > 0 || r.returned > 0) : k === 'returned' ? r.returned > 0 : k === 'complete' ? r.total > 0 && r.pending === 0 && r.returned === 0 : true)).length

  // RULE FOUR (§3c): export the search/filter-CHIP-visible rows — status/counts only, no document
  // content or Fernet-encrypted intake PII (that lives on the per-employee page, HR-only reveal).
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'name', role: 'rep', get: (r: any) => r.name || r.employee_id },
    { header: 'Employee ID', field: 'employee_id', get: (r: any) => r.employee_id || '' },
    { header: 'Email', field: 'email', get: (r: any) => r.email || '' },
    { header: 'Sent', field: 'sent', get: (r: any) => r.sent ? (r.docs_sent_at || r.invited_at || 'yes').slice(0, 10) : 'not sent' },
    { header: 'Documents Back', field: 'back', get: (r: any) => `${(r.submitted || 0) + (r.verified || 0)}/${r.total || 0}` },
    { header: 'Returned for Fixes', field: 'returned', type: 'number', get: (r: any) => r.returned || 0 },
    { header: 'Outstanding', field: 'outstanding', get: (r: any) => (r.pending_labels || []).join('; ') },
    { header: 'Last Activity', field: 'last_activity', role: 'date', type: 'date', get: (r: any) => (r.last_activity || '').slice(0, 10) },
  ]

  return (
    <div>
      <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 12px' }}>
        Who has been <b>sent</b> the onboarding documents and what has come <b>back</b>. Tick the employees who still
        need theirs and hit send — each gets an email with their personal onboarding link (online form + federal/state
        forms to sign online or print &amp; upload). Incomplete returns are flagged here and bounced back automatically
        with the missing fields listed.
      </p>
      {msg && <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 10 }}>{msg}</div>}
      {!ready && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 10 }}>Run migration <b>073</b> (and <b>082</b> for the send/return tracking) to activate this board.</div>}
      {res && <div style={{ background: '#ecfdf5', border: '1px solid #a7f3d0', color: '#065f46', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 10 }}>
        Sent to {res.sent}/{res.total} · {res.emailed} emailed
        {(res.results || []).filter((r: any) => !r.ok).map((r: any, i: number) => <div key={i} style={{ color: '#991b1b' }}>✕ {r.name || r.employee_id}: {r.error}</div>)}
      </div>}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <input style={{ ...inp, width: 220 }} placeholder="Search name / ID / email…" value={q} onChange={e => setQ(e.target.value)} />
        {chips.map(([k, l]) => (
          <button key={k} onClick={() => setFilter(k)} style={{ ...btn, fontSize: 12, padding: '4px 10px', borderRadius: 20, background: filter === k ? 'var(--accent,#2563eb)' : 'var(--surface)', color: filter === k ? '#fff' : 'var(--text2)', border: filter === k ? 'none' : '1px solid var(--border)' }}>{l} ({count(k)})</button>
        ))}
        <div style={{ flex: 1 }} />
        <ReportExportBar title="Onboarding — Documents Board" columns={cols} rows={shown} />
        <button style={{ ...btnP, opacity: selIds.length && !busy ? 1 : 0.5 }} disabled={!selIds.length || busy} onClick={sendDocs}>
          {busy ? 'Sending…' : `📤 Send onboarding documents (${selIds.length})`}
        </button>
      </div>

      <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '34px 1.4fr 0.8fr 1fr 1.4fr 0.8fr', gap: 8, padding: '8px 12px', background: 'var(--surface)', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text3)', alignItems: 'center' }}>
          <input type="checkbox" checked={allShownSel} onChange={e => { const on = e.target.checked; setSel(s => { const n = { ...s }; shown.forEach(r => { n[r.employee_id] = on }); return n }) }} />
          <span>Employee</span><span>Sent</span><span>Documents back</span><span>Outstanding</span><span>Last activity</span>
        </div>
        {shown.map(r => {
          const back = (r.submitted || 0) + (r.verified || 0)
          return (
            <div key={r.employee_id} style={{ display: 'grid', gridTemplateColumns: '34px 1.4fr 0.8fr 1fr 1.4fr 0.8fr', gap: 8, padding: '9px 12px', borderTop: '1px solid var(--border)', fontSize: 13, alignItems: 'center' }}>
              <input type="checkbox" checked={!!sel[r.employee_id]} onChange={e => setSel(s => ({ ...s, [r.employee_id]: e.target.checked }))} />
              <div>
                <a href={`/hr/onboarding/${r.employee_id}`} style={{ fontWeight: 600, color: 'var(--accent,#2563eb)', textDecoration: 'none' }}>{r.name || r.employee_id}</a>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{r.employee_id}{r.email ? ` · ${r.email}` : ' · no email'}</div>
              </div>
              <div>
                {r.sent
                  ? <span style={{ fontSize: 12, color: '#059669' }}>✓ {fmt(r.docs_sent_at || r.invited_at)}</span>
                  : <span style={{ fontSize: 12, fontWeight: 700, color: '#b91c1c' }}>not sent</span>}
              </div>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <div style={{ flex: 1, height: 6, background: 'var(--border)', borderRadius: 6, overflow: 'hidden', display: 'flex' }}>
                    <div style={{ width: `${r.total ? (r.verified / r.total) * 100 : 0}%`, background: '#059669' }} />
                    <div style={{ width: `${r.total ? (r.submitted / r.total) * 100 : 0}%`, background: '#d97706' }} />
                    <div style={{ width: `${r.total ? (r.returned / r.total) * 100 : 0}%`, background: '#dc2626' }} />
                  </div>
                  <span style={{ fontSize: 12, color: 'var(--text2)', whiteSpace: 'nowrap' }}>{back}/{r.total}</span>
                </div>
                {r.returned > 0 && <div style={{ fontSize: 11, color: '#dc2626', fontWeight: 600 }}>↩ {r.returned} returned for fixes</div>}
                {!r.intake_submitted && <div style={{ fontSize: 11, color: 'var(--text3)' }}>info form not filled</div>}
              </div>
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {(r.returned_labels || []).map((l: string) => <span key={`r${l}`} title="returned for fixes" style={{ fontSize: 11, padding: '1px 7px', borderRadius: 12, background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b' }}>↩ {l}</span>)}
                {(r.pending_labels || []).slice(0, 3).map((l: string) => <span key={l} style={{ fontSize: 11, padding: '1px 7px', borderRadius: 12, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text2)' }}>{l}</span>)}
                {(r.pending_labels || []).length > 3 && <span style={{ fontSize: 11, color: 'var(--text3)' }}>+{(r.pending_labels || []).length - 3} more</span>}
                {r.total > 0 && r.pending === 0 && r.returned === 0 && <span style={{ fontSize: 12, color: '#059669', fontWeight: 600 }}>✓ everything back</span>}
              </div>
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>{fmt(r.last_activity)}</span>
            </div>
          )
        })}
        {shown.length === 0 && <div style={{ padding: '14px 16px', fontSize: 13, color: 'var(--text3)' }}>No employees match.</div>}
      </div>
    </div>
  )
}


// ── ✅ Completed — review & approve finished packets, forward to a customizable accounting destination ──
function CompletedDocuments() {
  const [rows, setRows] = useState<any[]>([])
  const [ready, setReady] = useState(true)
  const [cfg, setCfg] = useState<any>({ emails: [], subject: '', message: '', include_portal_link: true, email_configured: true, ready: true })
  const [emailsText, setEmailsText] = useState('')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [savingCfg, setSavingCfg] = useState(false)

  async function load() {
    try { const d = await api('/api/v1/hr/onboarding/doc-status'); setReady(d?.ready !== false); setRows(d?.employees || []) }
    catch (e: any) { setMsg(e?.message || 'Load failed') }
    try { const c = await api('/api/v1/hr/onboarding/accounting-settings'); setCfg(c); setEmailsText((c.emails || []).join(', ')) }
    catch { /* migration 100 optional */ }
  }
  useEffect(() => { load() }, [])

  const APPROVED = new Set(['docs_verified', 'provisioned', 'active'])
  const complete = rows.filter(r => r.total > 0 && r.pending === 0 && r.returned === 0)
  const shown = complete.filter(r => !q || `${r.name || ''} ${r.employee_id || ''} ${r.email || ''}`.toLowerCase().includes(q.toLowerCase()))
  const fmt = (d?: string | null) => (d ? String(d).slice(0, 10) : '')
  const hasDest = emailsText.trim().length > 0 || (cfg.emails || []).length > 0

  // RULE FOUR (§3c): export the visible (search-filtered) completed list — approval/forward status
  // only, no document content or PII.
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'name', role: 'rep', get: (r: any) => r.name || r.employee_id },
    { header: 'Employee ID', field: 'employee_id', get: (r: any) => r.employee_id || '' },
    { header: 'Email', field: 'email', get: (r: any) => r.email || '' },
    { header: 'Approval', field: 'approval', get: (r: any) => APPROVED.has(r.workflow_status) ? 'Approved' : 'Pending' },
    { header: 'Forwarded', field: 'forwarded', get: (r: any) => r.accounting_forwarded_at ? fmt(r.accounting_forwarded_at) : '' },
    { header: 'Forwarded To', field: 'forwarded_to', get: (r: any) => r.accounting_forwarded_to || '' },
  ]

  async function saveCfg() {
    setSavingCfg(true); setMsg('')
    try {
      await api('/api/v1/hr/onboarding/accounting-settings', { method: 'PUT', body: JSON.stringify({ emails: emailsText, subject: cfg.subject, message: cfg.message, include_portal_link: cfg.include_portal_link }) })
      setMsg('✅ Accounting destination saved.'); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setSavingCfg(false)
  }
  async function approve(r: any) {
    setBusy('approve:' + r.employee_id); setMsg('')
    try { await api(`/api/v1/hr/onboarding/employee/${encodeURIComponent(r.employee_id)}/approve`, { method: 'POST', body: '{}' }); setMsg(`✅ Approved ${r.name || r.employee_id}.`); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy('')
  }
  async function forward(r: any) {
    if (!hasDest) { setMsg('⚠️ Set the accounting recipient email above (and Save) first.'); return }
    const dest = emailsText.trim() || (cfg.emails || []).join(', ')
    if (!window.confirm(`Forward ${r.name || r.employee_id}'s completed paperwork to accounting (${dest})?`)) return
    setBusy('fwd:' + r.employee_id); setMsg('')
    try {
      const res: any = await api(`/api/v1/hr/onboarding/employee/${encodeURIComponent(r.employee_id)}/forward-accounting`, { method: 'POST', body: '{}' })
      setMsg(`↗ Forwarded ${r.name || r.employee_id} to ${(res.sent_to || []).join(', ')} — ${res.docs} document link(s).`); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy('')
  }

  return (
    <div>
      <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 12px' }}>
        New hires whose paperwork is <b>all back</b> (nothing pending or returned). Open each to <b>review</b>, mark it
        <b> approved</b>, then <b>forward</b> the completed packet to your accounting office. The forward destination is
        fully customizable below.
      </p>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 10 }}>{msg}</div>}
      {!ready && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 10 }}>Run migration <b>073</b> (+ <b>082</b>) to activate onboarding tracking.</div>}
      {cfg.ready === false && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 10 }}>Run migration <b>100_hr_accounting_forward.sql</b> to save the accounting destination + track forwards.</div>}
      {cfg.ready !== false && cfg.email_configured === false && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 10 }}>Email delivery isn&apos;t configured (RESEND_API_KEY + NOTIFY_FROM_EMAIL) — forwarding will fail until it&apos;s set in Railway.</div>}

      {/* Customizable forward destination */}
      <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 14, background: 'var(--surface)' }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>↗ Forward destination — accounting office</div>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 10px' }}>
          Where a completed &amp; approved packet is sent. Set one or more emails (comma-separated). Each forward emails a
          per-document summary + <b>secure 7-day download links</b> for every file — accounting doesn&apos;t need an app login.
        </p>
        <div style={{ display: 'grid', gap: 8 }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Accounting email(s)
            <input style={inp} value={emailsText} onChange={e => setEmailsText(e.target.value)} placeholder="accounting@company.com, ap@company.com" /></label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Subject (optional · {'{name}'} = employee)
            <input style={inp} value={cfg.subject || ''} onChange={e => setCfg((c: any) => ({ ...c, subject: e.target.value }))} placeholder="Completed onboarding paperwork — {name}" /></label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Message (optional)
            <textarea style={{ ...inp, minHeight: 44 }} value={cfg.message || ''} onChange={e => setCfg((c: any) => ({ ...c, message: e.target.value }))} placeholder="Intro line for the accounting email…" /></label>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <label style={{ fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={cfg.include_portal_link !== false} onChange={e => setCfg((c: any) => ({ ...c, include_portal_link: e.target.checked }))} /> Include a MetricsPro reference line</label>
            <div style={{ flex: 1 }} />
            <button style={btnP} disabled={savingCfg} onClick={saveCfg}>{savingCfg ? 'Saving…' : 'Save destination'}</button>
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
        <input style={{ ...inp, width: 240 }} placeholder="Search completed…" value={q} onChange={e => setQ(e.target.value)} />
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{shown.length} complete</span>
        <ReportExportBar title="Onboarding — Completed" columns={cols} rows={shown} />
      </div>

      <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr 1.1fr 1.4fr', gap: 8, padding: '8px 12px', background: 'var(--surface)', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text3)' }}>
          <span>Employee</span><span>Review</span><span>Approval</span><span>Forward to accounting</span>
        </div>
        {shown.map(r => {
          const approved = APPROVED.has(r.workflow_status)
          const fwd = r.accounting_forwarded_at
          return (
            <div key={r.employee_id} style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr 1.1fr 1.4fr', gap: 8, padding: '9px 12px', borderTop: '1px solid var(--border)', fontSize: 13, alignItems: 'center' }}>
              <div>
                <div style={{ fontWeight: 600 }}>{r.name || r.employee_id}</div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{r.employee_id}{r.email ? ' · ' + r.email : ''}</div>
              </div>
              <div><a href={`/hr/onboarding/${r.employee_id}`} style={{ fontSize: 12, color: 'var(--accent,#2563eb)', textDecoration: 'none' }}>📄 Review packet</a></div>
              <div>
                {approved
                  ? <span style={{ fontSize: 12, color: '#059669', fontWeight: 600 }}>✓ Approved</span>
                  : <button style={{ ...btn, fontSize: 12, padding: '4px 10px' }} disabled={busy === 'approve:' + r.employee_id} onClick={() => approve(r)}>{busy === 'approve:' + r.employee_id ? '…' : 'Approve'}</button>}
              </div>
              <div>
                <button style={{ ...btnP, fontSize: 12, padding: '4px 10px', opacity: busy === 'fwd:' + r.employee_id ? 0.6 : 1 }} disabled={busy === 'fwd:' + r.employee_id} onClick={() => forward(r)}>{busy === 'fwd:' + r.employee_id ? 'Sending…' : '↗ Forward'}</button>
                {fwd && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>sent {fmt(fwd)}{r.accounting_forwarded_to ? ' → ' + r.accounting_forwarded_to : ''}</div>}
              </div>
            </div>
          )
        })}
        {shown.length === 0 && <div style={{ padding: '14px 16px', fontSize: 13, color: 'var(--text3)' }}>No completed packets yet. An employee appears here once every assigned document is back (nothing pending or returned).</div>}
      </div>
    </div>
  )
}


// ── 🔁 Reconcile mandatory docs — item 1's backfill path. The checklist total/done is ALREADY computed
// live against the current template (see the migration 401 header in hr/router.py for why the reported
// "5/5 without the IL W-4" bug was a state-matching bug, not a snapshot bug) — this panel is the
// PROACTIVE half: scan the whole roster now, review a dry-run report, then apply + notify.
type ReconcileRow = {
  employee_id: string; employee_name?: string; workflow_status?: string
  missing_mandatory: { task_id: string; key?: string; label: string; category?: string }[]
  state_undetermined?: boolean; notified?: boolean
}
function ReconcilePanel() {
  const [report, setReport] = useState<ReconcileRow[]>([])
  const [meta, setMeta] = useState<{ employees_scanned?: number; employees_affected?: number; dry_run?: boolean; generated_at?: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [notifyOnApply, setNotifyOnApply] = useState(true)

  // RULE FOUR (§3c): export the dry-run/applied report — task labels + status flags only, no PII.
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'employee', role: 'rep', get: (r: ReconcileRow) => r.employee_name || r.employee_id },
    { header: 'Employee ID', field: 'employee_id', get: (r: ReconcileRow) => r.employee_id },
    { header: 'Status', field: 'workflow_status', get: (r: ReconcileRow) => r.workflow_status || '' },
    { header: 'Missing Mandatory', field: 'missing', get: (r: ReconcileRow) => r.missing_mandatory.map(m => m.label).join('; ') },
    { header: 'State Undetermined', field: 'state_undetermined', get: (r: ReconcileRow) => r.state_undetermined ? 'Yes' : '' },
    { header: 'Notified', field: 'notified', get: (r: ReconcileRow) => (meta?.dry_run === false ? (r.notified ? 'Emailed' : 'No email on file') : '') },
  ]

  async function runDryRun() {
    setBusy(true); setMsg(''); setReport([]); setMeta(null)
    try {
      const r = await api('/api/v1/hr/onboarding/reconcile', { method: 'POST', body: JSON.stringify({ dry_run: true }) })
      setReport(r?.report || []); setMeta(r)
      if (!r?.employees_affected) setMsg('✓ Nothing to reconcile — every mandatory document in the current template is accounted for.')
    } catch (e: any) { setMsg(e?.message || 'Dry run failed') }
    setBusy(false)
  }
  async function applyAndNotify() {
    if (!meta || meta.dry_run === false) return
    if (!window.confirm(`Reopen ${meta.employees_affected} employee(s)' checklists and ${notifyOnApply ? 'EMAIL each of them' : 'log it without emailing'}? This cannot be undone (though it never revokes a login or deletes data).`)) return
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/hr/onboarding/reconcile', { method: 'POST', body: JSON.stringify({ dry_run: false, notify: notifyOnApply }) })
      setReport(r?.report || []); setMeta(r)
      setMsg(`✓ Reconciled ${r?.employees_affected || 0} employee(s)${notifyOnApply ? ` — ${r?.report?.filter((x: ReconcileRow) => x.notified).length || 0} emailed` : ' (not notified)'}.`)
    } catch (e: any) { setMsg(e?.message || 'Apply failed') }
    setBusy(false)
  }

  return (
    <div>
      <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 12px' }}>
        When a document is made <b>mandatory</b> in the checklist setup below (or a state-gated form should now apply
        but an employee&apos;s work state was never confirmed), their checklist total already updates automatically —
        but nobody is proactively told. Run a <b>dry run</b> first to see exactly who is affected and what they&apos;re
        missing; nothing is written or emailed until you explicitly apply it.
      </p>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 10 }}>{msg}</div>}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <button style={btnP} disabled={busy} onClick={runDryRun}>{busy ? 'Scanning…' : '🔍 Run dry run'}</button>
        {meta && meta.dry_run !== false && (
          <>
            <label style={{ fontSize: 12, display: 'flex', gap: 5, alignItems: 'center' }}>
              <input type="checkbox" checked={notifyOnApply} onChange={e => setNotifyOnApply(e.target.checked)} /> Email affected employees
            </label>
            <button style={{ ...btn, color: '#fff', background: '#059669', border: 'none', fontWeight: 600, opacity: (meta.employees_affected || 0) > 0 ? 1 : 0.5 }}
              disabled={busy || !(meta.employees_affected && meta.employees_affected > 0)} onClick={applyAndNotify}>
              ✅ Apply — reopen &amp; {notifyOnApply ? 'notify' : 'log'} ({meta.employees_affected || 0})
            </button>
          </>
        )}
        {meta && <span style={{ fontSize: 12, color: 'var(--text3)' }}>{meta.employees_scanned} scanned · {meta.employees_affected} affected{meta.dry_run === false ? ' · APPLIED' : ' · dry run only'}</span>}
        {report.length > 0 && <ReportExportBar title="Onboarding — Reconcile Report" columns={cols} rows={report} />}
      </div>

      {report.length > 0 && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 2fr 0.7fr', gap: 8, padding: '8px 12px', background: 'var(--surface)', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text3)' }}>
            <span>Employee</span><span>Status</span><span>Missing mandatory</span><span>Notified</span>
          </div>
          {report.map(r => (
            <div key={r.employee_id} style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 2fr 0.7fr', gap: 8, padding: '9px 12px', borderTop: '1px solid var(--border)', fontSize: 13, alignItems: 'flex-start' }}>
              <div>
                <a href={`/hr/onboarding/${r.employee_id}`} style={{ fontWeight: 600, color: 'var(--accent,#2563eb)', textDecoration: 'none' }}>{r.employee_name || r.employee_id}</a>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{r.employee_id}</div>
              </div>
              <span style={{ fontSize: 12, color: 'var(--text2)' }}>{r.workflow_status}</span>
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {r.missing_mandatory.map(m => <span key={m.task_id} style={{ fontSize: 11, padding: '1px 7px', borderRadius: 12, background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b' }}>{m.label}</span>)}
                {r.state_undetermined && <span style={{ fontSize: 11, padding: '1px 7px', borderRadius: 12, background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e' }}>work state not confirmed</span>}
              </div>
              <span style={{ fontSize: 12 }}>{meta?.dry_run === false ? (r.notified ? '✓ emailed' : 'no email on file') : '—'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
