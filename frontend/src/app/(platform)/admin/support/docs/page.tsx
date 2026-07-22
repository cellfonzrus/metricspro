'use client'
// Help Docs editor (mig 715) — the per-page help registry with coverage (which NAV pages have docs vs
// missing), an edit form (page_key PICKED from the NAV registry §3b, user_md / support_md markdown,
// common_issues repeater), a JSON import for the domain content packs, plus SLA-policy + canned-response
// config. All endpoints are house-gated server-side. HOUSE (global/product) docs by default.
import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { NAV } from '@/lib/rbac'

type Doc = { id?: string; page_key: string; title?: string; module?: string; user_md?: string
  support_md?: string; common_issues?: any[]; permissions_needed?: string; related_settings?: any; is_published?: boolean }
type Issue = { symptom?: string; diagnosis?: string; fix?: string; escalate_when?: string }

const NAV_ITEMS = NAV.flatMap(g => g.items.map(it => ({ href: it.href, label: `${g.group} · ${it.label}`, module: it.module })))
const emptyDoc = (): Doc => ({ page_key: '', title: '', module: '', user_md: '', support_md: '', common_issues: [], permissions_needed: '', is_published: true })

export default function HelpDocsEditor() {
  const [docs, setDocs] = useState<Doc[]>([])
  const [form, setForm] = useState<Doc>(emptyDoc())
  const [issues, setIssues] = useState<Issue[]>([])
  const [importText, setImportText] = useState('')
  const [sla, setSla] = useState<any[]>([])
  const [canned, setCanned] = useState<any[]>([])
  const [cannedForm, setCannedForm] = useState<any>({ title: '', body: '', category: '' })
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const loadDocs = useCallback(async () => {
    try { const d = await api('/api/v1/core/support-docs'); setDocs(d.docs || []) }
    catch (e: any) { setErr(e?.message || 'Could not load docs') }
  }, [])
  useEffect(() => { loadDocs() }, [loadDocs])
  useEffect(() => { api('/api/v1/helpdesk/support/sla-policy').then((d: any) => setSla(d.policy || [])).catch(() => {}) }, [])
  useEffect(() => { api('/api/v1/helpdesk/support/canned-responses').then((d: any) => setCanned(d.canned || [])).catch(() => {}) }, [])

  const covered = useMemo(() => new Set(docs.map(d => d.page_key)), [docs])

  function edit(d: Doc) {
    setForm({ ...emptyDoc(), ...d })
    setIssues(Array.isArray(d.common_issues) ? d.common_issues : [])
    setMsg(''); setErr('')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
  function reset() { setForm(emptyDoc()); setIssues([]) }

  async function save() {
    if (!form.page_key.trim()) { setErr('Pick a page.'); return }
    setBusy(true); setErr(''); setMsg('')
    try {
      await api('/api/v1/core/support-docs', { method: 'POST', body: JSON.stringify({ ...form, common_issues: issues }) })
      setMsg(`Saved ${form.page_key}`); reset(); await loadDocs()
    } catch (e: any) { setErr(e?.message || 'Save failed') } finally { setBusy(false) }
  }
  async function del(d: Doc) {
    if (!d.id || !confirm(`Delete the help doc for ${d.page_key}?`)) return
    setBusy(true)
    try { await api(`/api/v1/core/support-docs/${d.id}`, { method: 'DELETE' }); await loadDocs() }
    catch (e: any) { setErr(e?.message || 'Delete failed') } finally { setBusy(false) }
  }
  async function doImport() {
    setBusy(true); setErr(''); setMsg('')
    try {
      const parsed = JSON.parse(importText)
      const r = await api('/api/v1/core/support-docs/import', { method: 'POST', body: JSON.stringify(parsed) })
      setMsg(`Imported ${r.imported} page(s)${r.skipped ? `, skipped ${r.skipped}` : ''}`); setImportText(''); await loadDocs()
    } catch (e: any) { setErr(e?.message?.includes('JSON') ? 'Invalid JSON' : (e?.message || 'Import failed')) } finally { setBusy(false) }
  }
  async function saveSla(row: any) {
    setBusy(true)
    try { await api('/api/v1/helpdesk/support/sla-policy', { method: 'PUT', body: JSON.stringify(row) })
      const d = await api('/api/v1/helpdesk/support/sla-policy'); setSla(d.policy || []); setMsg(`Saved SLA · ${row.priority}`) }
    catch (e: any) { setErr(e?.message || 'SLA save failed') } finally { setBusy(false) }
  }
  async function saveCanned() {
    if (!cannedForm.title.trim() || !cannedForm.body.trim()) { setErr('Canned title + body required'); return }
    setBusy(true)
    try { await api('/api/v1/helpdesk/support/canned-responses', { method: 'POST', body: JSON.stringify(cannedForm) })
      setCannedForm({ title: '', body: '', category: '' }); const d = await api('/api/v1/helpdesk/support/canned-responses'); setCanned(d.canned || []) }
    catch (e: any) { setErr(e?.message || 'Canned save failed') } finally { setBusy(false) }
  }
  async function delCanned(rid: string) {
    setBusy(true)
    try { await api(`/api/v1/helpdesk/support/canned-responses/${rid}`, { method: 'DELETE' }); const d = await api('/api/v1/helpdesk/support/canned-responses'); setCanned(d.canned || []) }
    catch (e: any) { setErr(e?.message || 'Delete failed') } finally { setBusy(false) }
  }

  const sel = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, width: '100%' }
  const lbl = { fontSize: 12, fontWeight: 600, marginBottom: 3, display: 'block' as const, color: 'var(--text2)' }
  const slaFor = (p: string) => sla.find(s => s.priority === p) || { priority: p, response_hours: '', resolve_hours: '' }

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📚 Help Docs</h1>
        <span style={{ flex: 1 }} />
        <Link href="/admin/support" className="btn btn-sm">← Support Console</Link>
      </div>
      <p style={{ color: 'var(--text3)', fontSize: 12, marginTop: 4 }}>
        Per-page help. <b>User help</b> shows in the "?" panel; the <b>support playbook</b> shows only in the console.
      </p>
      {msg && <div className="card" style={{ padding: 8, marginBottom: 10, background: '#f0fdf4', border: '1px solid #bbf7d0', color: '#166534', fontSize: 13 }}>{msg}</div>}
      {err && <div className="card" style={{ padding: 8, marginBottom: 10, borderColor: '#c0392b', color: '#c0392b', fontSize: 13 }}>{err}</div>}

      {/* Edit form */}
      <div className="card" style={{ padding: 16, marginBottom: 16, display: 'grid', gap: 12 }}>
        <div style={{ fontWeight: 700 }}>{form.id ? `Edit · ${form.page_key}` : 'New / edit a help doc'}</div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 240 }}>
            <label style={lbl}>Page (from the menu registry) *</label>
            <select style={sel} value={form.page_key} onChange={e => {
              const it = NAV_ITEMS.find(n => n.href === e.target.value)
              setForm(f => ({ ...f, page_key: e.target.value, module: it?.module || f.module, title: f.title || (it?.label.split(' · ').pop() || '') }))
            }}>
              <option value="">— pick a page —</option>
              {NAV_ITEMS.map(n => <option key={n.href} value={n.href}>{covered.has(n.href) ? '✓ ' : ''}{n.href} — {n.label}</option>)}
            </select>
          </div>
          <div style={{ flex: 1, minWidth: 160 }}>
            <label style={lbl}>Title</label>
            <input style={sel} value={form.title || ''} onChange={e => setForm(f => ({ ...f, title: e.target.value }))} />
          </div>
        </div>
        <div>
          <label style={lbl}>User help (markdown — shows in the "?" panel)</label>
          <textarea style={{ ...sel, minHeight: 110, fontFamily: 'inherit' }} value={form.user_md || ''} onChange={e => setForm(f => ({ ...f, user_md: e.target.value }))} placeholder="What this page does, in plain language. Supports **bold**, `code`, # headings, - bullets." />
        </div>
        <div>
          <label style={lbl}>Support playbook (support staff only)</label>
          <textarea style={{ ...sel, minHeight: 110, fontFamily: 'inherit' }} value={form.support_md || ''} onChange={e => setForm(f => ({ ...f, support_md: e.target.value }))} placeholder="Full diagnosis / how-to-fix for the support team." />
        </div>
        <div>
          <label style={lbl}>Permissions needed</label>
          <input style={sel} value={form.permissions_needed || ''} onChange={e => setForm(f => ({ ...f, permissions_needed: e.target.value }))} />
        </div>
        {/* common_issues repeater */}
        <div>
          <label style={lbl}>Common issues</label>
          {issues.map((iss, i) => (
            <div key={i} style={{ display: 'grid', gap: 6, gridTemplateColumns: '1fr 1fr', border: '1px solid var(--border)', borderRadius: 8, padding: 8, marginBottom: 6 }}>
              <input style={sel} placeholder="Symptom" value={iss.symptom || ''} onChange={e => setIssues(a => a.map((x, j) => j === i ? { ...x, symptom: e.target.value } : x))} />
              <input style={sel} placeholder="Diagnosis" value={iss.diagnosis || ''} onChange={e => setIssues(a => a.map((x, j) => j === i ? { ...x, diagnosis: e.target.value } : x))} />
              <input style={sel} placeholder="Fix" value={iss.fix || ''} onChange={e => setIssues(a => a.map((x, j) => j === i ? { ...x, fix: e.target.value } : x))} />
              <div style={{ display: 'flex', gap: 6 }}>
                <input style={sel} placeholder="Escalate when" value={iss.escalate_when || ''} onChange={e => setIssues(a => a.map((x, j) => j === i ? { ...x, escalate_when: e.target.value } : x))} />
                <button className="btn btn-sm" onClick={() => setIssues(a => a.filter((_, j) => j !== i))}>✕</button>
              </div>
            </div>
          ))}
          <button className="btn btn-sm" onClick={() => setIssues(a => [...a, {}])}>+ Add issue</button>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <label style={{ fontSize: 12, color: 'var(--text3)' }}>
            <input type="checkbox" checked={form.is_published !== false} onChange={e => setForm(f => ({ ...f, is_published: e.target.checked }))} /> Published</label>
          <span style={{ flex: 1 }} />
          {form.id && <button className="btn btn-sm" onClick={reset}>New</button>}
          <button className="btn btn-primary" disabled={busy} onClick={save}>Save doc</button>
        </div>
      </div>

      {/* Coverage / registry */}
      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Coverage · {covered.size}/{NAV_ITEMS.length} menu pages documented</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(280px,1fr))', gap: 6 }}>
          {NAV_ITEMS.map(n => {
            const d = docs.find(x => x.page_key === n.href)
            return (
              <div key={n.href} onClick={() => d ? edit(d) : setForm(f => ({ ...emptyDoc(), page_key: n.href, module: n.module, title: n.label.split(' · ').pop() || '' }))}
                style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '5px 8px', borderRadius: 6, cursor: 'pointer', fontSize: 12, background: d ? '#f0fdf4' : 'var(--bg2)', border: `1px solid ${d ? '#bbf7d0' : 'var(--border)'}` }}>
                <span>{d ? '✓' : '○'}</span>
                <span style={{ fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.href}</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Import packs */}
      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Import a content pack (JSON)</div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>{'{ "domain": "...", "pages": [{ "page_key","title","module","user_md","support_md","common_issues":[...],"permissions_needed","related_settings" }] }'}</div>
        <textarea style={{ ...sel, minHeight: 100, fontFamily: 'monospace' }} value={importText} onChange={e => setImportText(e.target.value)} placeholder='Paste a domain pack JSON…' />
        <button className="btn btn-sm" disabled={busy || !importText.trim()} style={{ marginTop: 6 }} onClick={doImport}>Import</button>
      </div>

      {/* SLA policy */}
      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>SLA policy (hours)</div>
        {['urgent', 'high', 'normal', 'low'].map(p => {
          const row = slaFor(p)
          return (
            <div key={p} style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
              <span style={{ width: 70, fontSize: 13, fontWeight: 600 }}>{p}</span>
              <label style={{ fontSize: 12, color: 'var(--text3)' }}>Response
                <input type="number" style={{ ...sel, width: 90, marginLeft: 6, display: 'inline-block' }} defaultValue={row.response_hours ?? ''}
                  onBlur={e => saveSla({ priority: p, response_hours: e.target.value, resolve_hours: slaFor(p).resolve_hours })} /></label>
              <label style={{ fontSize: 12, color: 'var(--text3)' }}>Resolve
                <input type="number" style={{ ...sel, width: 90, marginLeft: 6, display: 'inline-block' }} defaultValue={row.resolve_hours ?? ''}
                  onBlur={e => saveSla({ priority: p, response_hours: slaFor(p).response_hours, resolve_hours: e.target.value })} /></label>
            </div>
          )
        })}
      </div>

      {/* Canned responses */}
      <div className="card" style={{ padding: 14 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Canned responses</div>
        {canned.map(cr => (
          <div key={cr.id} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{cr.title} {cr.category && <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {cr.category}</span>}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)', whiteSpace: 'pre-wrap' }}>{cr.body}</div>
            </div>
            <button className="btn btn-sm" onClick={() => delCanned(cr.id)}>✕</button>
          </div>
        ))}
        <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
          <div style={{ display: 'flex', gap: 6 }}>
            <input style={sel} placeholder="Title" value={cannedForm.title} onChange={e => setCannedForm((f: any) => ({ ...f, title: e.target.value }))} />
            <input style={{ ...sel, maxWidth: 160 }} placeholder="Category" value={cannedForm.category} onChange={e => setCannedForm((f: any) => ({ ...f, category: e.target.value }))} />
          </div>
          <textarea style={{ ...sel, minHeight: 60 }} placeholder="Response body…" value={cannedForm.body} onChange={e => setCannedForm((f: any) => ({ ...f, body: e.target.value }))} />
          <div><button className="btn btn-sm" disabled={busy} onClick={saveCanned}>+ Add canned response</button></div>
        </div>
      </div>
    </div>
  )
}
