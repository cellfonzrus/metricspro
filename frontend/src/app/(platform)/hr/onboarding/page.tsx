'use client'
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'

// HR · Onboarding Checklist (admin) — the CONFIGURABLE template every new hire is onboarded against.
// Items group under collapsible CATEGORIES; each item has an OWNER role (Employee / HR / DM / Market
// Manager), an optional live state/federal document LINK, and upload/fillable flags. Per-employee
// progress + uploaded documents live on the employee's record (open from HR · People → Onboarding).
// Backed by storeops.onboarding_category / onboarding_task (migration 073).

type Task = {
  id: string; category_id: string; key?: string; label: string; description?: string; owner_role: string
  doc_url?: string; doc_label?: string; is_fillable?: boolean; requires_upload?: boolean
  applies_state?: string | null; sort_order?: number; is_active?: boolean
}
type Cat = { id: string; key: string; label: string; sort_order?: number; is_active?: boolean; tasks: Task[] }

const ROLE_LABELS: Record<string, string> = { employee: 'Employee', hr: 'HR', dm: 'District Manager', market_manager: 'Market Manager' }
const ROLE_COLOR: Record<string, string> = { employee: '#2563eb', hr: '#7c3aed', dm: '#059669', market_manager: '#d97706' }
const inp: React.CSSProperties = { padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', width: '100%' }
const btn: React.CSSProperties = { padding: '7px 12px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, cursor: 'pointer', background: 'var(--surface)' }
const btnP: React.CSSProperties = { ...btn, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none', fontWeight: 600 }
const chip = (role: string): React.CSSProperties => ({ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 20, color: '#fff', background: ROLE_COLOR[role] || '#64748b' })

export default function OnboardingAdminPage() {
  const [cats, setCats] = useState<Cat[]>([])
  const [ready, setReady] = useState(true)
  const [states, setStates] = useState<string[]>([])
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [editing, setEditing] = useState<Partial<Task> | null>(null)  // task add/edit form
  const [msg, setMsg] = useState('')

  async function load() {
    try {
      const d = await api('/api/v1/hr/onboarding/template?include_inactive=true')
      setReady(d?.ready !== false); setCats(d?.categories || []); setStates(d?.states || [])
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  useEffect(() => { load() }, [])
  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 4000) }

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
    try {
      await api(path, { method: isNew ? 'POST' : 'PATCH', body: JSON.stringify(editing) })
      setEditing(null); load()
    } catch (e: any) { flash(e?.message || 'Save failed — is migration 073 applied?') }
  }
  async function deleteTask(t: Task) {
    if (!window.confirm(`Delete "${t.label}"?`)) return
    try { await api(`/api/v1/hr/onboarding/tasks/${t.id}`, { method: 'DELETE' }); load() }
    catch (e: any) { flash(e?.message || 'Delete failed') }
  }
  const upd = (patch: Partial<Task>) => setEditing(v => ({ ...v, ...patch }))

  return (
    <div style={{ padding: 24, maxWidth: 920 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🧩 Onboarding Checklist</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 16 }}>
        The template HR runs for every new hire. Group items into collapsible categories and assign each to whoever owns it
        (Employee, HR, the DM, or the Market Manager). Add a live form link so the employee can fill it before they start.
        Open a person&apos;s checklist from <a href="/hr/people" style={{ color: 'var(--accent,#2563eb)' }}>HR · People</a>.
      </p>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}
      {!ready && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '10px 14px', fontSize: 13, marginBottom: 14 }}>
        Run migration <b>073_hr_onboarding.sql</b> in Supabase to activate the checklist. Until then this page is empty.
      </div>}

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
                      </div>
                      {t.description && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{t.description}</div>}
                      {t.doc_url && <a href={t.doc_url} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: 'var(--accent,#2563eb)' }}>🔗 {t.doc_label || 'Document link'}</a>}
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
                  <input style={inp} list="states" value={editing.applies_state || ''} onChange={e => upd({ applies_state: e.target.value })} placeholder="all" />
                  <datalist id="states">{states.map(s => <option key={s} value={s} />)}</datalist></label>
              </div>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Live document link (URL)
                <input style={inp} value={editing.doc_url || ''} onChange={e => upd({ doc_url: e.target.value })} placeholder="https://www.irs.gov/pub/irs-pdf/fw4.pdf" /></label>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Document label
                <input style={inp} value={editing.doc_label || ''} onChange={e => upd({ doc_label: e.target.value })} placeholder="IRS Form W-4" /></label>
              <div style={{ display: 'flex', gap: 16, fontSize: 13 }}>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={!!editing.requires_upload} onChange={e => upd({ requires_upload: e.target.checked })} /> Requires upload</label>
                <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={!!editing.is_fillable} onChange={e => upd({ is_fillable: e.target.checked })} /> Fillable online</label>
              </div>
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
