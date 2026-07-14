'use client'
import { useEffect, useMemo, useState } from 'react'
import { api, supabase, activeOrgHeader } from '@/lib/client'

// HR · Compliance Document Repository (item 5). A VIEW + bulk export over the SAME onboarding-docs
// bucket and employee_onboarding rows the Documents board (mig 082) already tracks — not a second
// store. Every uploaded/signed document across the roster, employee-grouped, filterable, each row
// carrying a link to its own file AND (when the item was signed online) its signature page. Bulk
// "pick up at once" export = one ZIP, organized /EmployeeName/DocumentLabel.ext. Backed by
// /api/v1/hr/onboarding/compliance-documents (+ /export). Reachable directly at /hr/compliance —
// a sidebar nav entry is pending (see NEEDS CORE in docs/handoffs/people.md; layout.tsx is shared).

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const inp: React.CSSProperties = { padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const btn: React.CSSProperties = { padding: '6px 11px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, cursor: 'pointer', background: 'var(--surface)' }
const btnP: React.CSSProperties = { ...btn, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none', fontWeight: 600 }

type Doc = {
  employee_id: string; employee_name?: string; employee_email?: string
  task_id: string; document_label: string; category?: string; status?: string; document_name?: string
  has_document?: boolean; has_signature_page?: boolean; signed_at?: string | null; signed_name?: string | null; verified_by?: string | null
}

async function authedFetch(path: string) {
  const { data } = await supabase.auth.getSession().catch(() => ({ data: { session: null } } as any))
  const tok = data?.session?.access_token
  return fetch(`${API_URL}${path}`, { headers: { ...(tok ? { Authorization: `Bearer ${tok}` } : {}), ...activeOrgHeader() } })
}

async function downloadZip(path: string, filename: string, setMsg: (m: string) => void) {
  setMsg('Preparing export…')
  try {
    const res = await authedFetch(path)
    if (!res.ok) { setMsg((await res.json().catch(() => ({})))?.detail || 'Export failed'); return }
    const blob = await res.blob()
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = filename; a.click()
    setMsg('')
  } catch (e: any) { setMsg(e?.message || 'Export failed') }
}

export default function CompliancePage() {
  const [docs, setDocs] = useState<Doc[]>([])
  const [ready, setReady] = useState(true)
  const [q, setQ] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const r = await api(`/api/v1/hr/onboarding/compliance-documents${q ? `?q=${encodeURIComponent(q)}` : ''}`)
      setReady(r?.ready !== false); setDocs(r?.documents || [])
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
    setLoading(false)
  }
  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t) }, [q])  // eslint-disable-line react-hooks/exhaustive-deps

  async function viewDoc(d: Doc) {
    try { const r = await api(`/api/v1/hr/onboarding/employee/${encodeURIComponent(d.employee_id)}/task/${encodeURIComponent(d.task_id)}/doc`); if (r?.url) window.open(r.url, '_blank') }
    catch (e: any) { setMsg(e?.message || 'No document') }
  }
  async function viewSignature(d: Doc) {
    try { const r = await api(`/api/v1/hr/onboarding/employee/${encodeURIComponent(d.employee_id)}/task/${encodeURIComponent(d.task_id)}/signature`); if (r?.url) window.open(r.url, '_blank') }
    catch (e: any) { setMsg(e?.message || 'No signature page') }
  }

  const grouped = useMemo(() => {
    const by: Record<string, { name: string; email?: string; docs: Doc[] }> = {}
    for (const d of docs) {
      const g = (by[d.employee_id] ||= { name: d.employee_name || d.employee_id, email: d.employee_email, docs: [] })
      g.docs.push(d)
    }
    return Object.entries(by).sort((a, b) => a[1].name.localeCompare(b[1].name))
  }, [docs])

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🗂️ Compliance Document Repository</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 16 }}>
        Every uploaded or signed onboarding document across the roster, grouped by employee — with each document&apos;s
        signature page linked alongside it. Reuses the same storage the <a href="/hr/onboarding" style={{ color: 'var(--accent,#2563eb)' }}>Documents board</a> tracks; nothing is duplicated here.
      </p>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}
      {!ready && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '10px 14px', fontSize: 13, marginBottom: 14 }}>
        Run migration <b>073_hr_onboarding.sql</b> to activate onboarding before this repository has anything to show.
      </div>}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <input style={{ ...inp, width: 260 }} placeholder="Search employee name / ID / email…" value={q} onChange={e => setQ(e.target.value)} />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{loading ? 'Loading…' : `${docs.length} document(s) · ${grouped.length} employee(s)`}</span>
        <div style={{ flex: 1 }} />
        <button style={btnP} onClick={() => downloadZip('/api/v1/hr/onboarding/compliance-documents/export', 'onboarding-documents-all.zip', setMsg)}>
          📦 Export ALL (one zip)
        </button>
      </div>

      {grouped.map(([eid, g]) => (
        <div key={eid} style={{ border: '1px solid var(--border)', borderRadius: 10, marginBottom: 12, overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', background: 'var(--surface)' }}>
            <a href={`/hr/onboarding/${eid}`} style={{ fontWeight: 700, fontSize: 14, color: 'var(--accent,#2563eb)', textDecoration: 'none' }}>{g.name}</a>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>{eid}{g.email ? ` · ${g.email}` : ''}</span>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {g.docs.length} document(s)</span>
            <div style={{ flex: 1 }} />
            <button style={{ ...btn, fontSize: 11, padding: '4px 8px' }} onClick={() => downloadZip(`/api/v1/hr/onboarding/compliance-documents/export?employee_id=${encodeURIComponent(eid)}`, `onboarding-documents-${eid}.zip`, setMsg)}>
              📦 Export this employee
            </button>
          </div>
          {g.docs.map(d => (
            <div key={d.task_id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', borderTop: '1px solid var(--border)', fontSize: 13 }}>
              <span style={{ flex: 1 }}>{d.document_label}{d.category ? <span style={{ color: 'var(--text3)', fontSize: 11 }}> · {d.category}</span> : ''}</span>
              <span style={{ fontSize: 11, color: 'var(--text3)', width: 130 }}>{d.signed_at ? String(d.signed_at).slice(0, 10) : ''}</span>
              {d.has_document && <button style={{ ...btn, fontSize: 11, padding: '3px 8px' }} onClick={() => viewDoc(d)}>📄 View {d.document_name ? `(${d.document_name})` : ''}</button>}
              {d.has_signature_page && <button style={{ ...btn, fontSize: 11, padding: '3px 8px' }} onClick={() => viewSignature(d)}>✍️ Signature page</button>}
              {!d.has_document && !d.has_signature_page && <span style={{ fontSize: 11, color: 'var(--text3)' }}>no file</span>}
            </div>
          ))}
        </div>
      ))}
      {!loading && grouped.length === 0 && <div style={{ padding: 20, color: 'var(--text3)', fontSize: 13 }}>No documents on file yet.</div>}
    </div>
  )
}
