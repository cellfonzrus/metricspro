'use client'
import { useEffect, useMemo, useState } from 'react'
import { api, supabase, activeOrgHeader } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

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
  // migration 402: a task with multiple files now contributes one row per file (file_id + 1-based
  // file_index/file_count so "1 of 2" can be shown) instead of collapsing to just the latest.
  file_id?: string | null; file_index?: number | null; file_count?: number
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
  const [fetchError, setFetchError] = useState('')
  const [loading, setLoading] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const r = await api(`/api/v1/hr/onboarding/compliance-documents${q ? `?q=${encodeURIComponent(q)}` : ''}`)
      setReady(r?.ready !== false); setDocs(r?.documents || [])
      // Truth-telling: a failed read on the backend now comes back as ready:false + an explicit error
      // message rather than a silent empty list — show it instead of letting the page look like an
      // empty-but-healthy repository.
      setFetchError(r?.ready === false ? (r?.error || '') : '')
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
    setLoading(false)
  }
  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t) }, [q])  // eslint-disable-line react-hooks/exhaustive-deps

  async function viewDoc(d: Doc) {
    try {
      // migration 402: a file-level row (file_id set) opens exactly that file; a legacy/pre-402 row
      // (file_id null) falls back to the old "most recent file" endpoint — no regression.
      const path = d.file_id
        ? `/api/v1/hr/onboarding/employee/${encodeURIComponent(d.employee_id)}/task/${encodeURIComponent(d.task_id)}/document/${encodeURIComponent(d.file_id)}`
        : `/api/v1/hr/onboarding/employee/${encodeURIComponent(d.employee_id)}/task/${encodeURIComponent(d.task_id)}/doc`
      const r = await api(path)
      if (r?.url) window.open(r.url, '_blank')
    } catch (e: any) { setMsg(e?.message || 'No document') }
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

  // RULE FOUR (§3c) — TABLE-VIEW export (Excel/PDF/Print/Send), alongside the existing ZIP file
  // export. PII SAFETY: this row shape is exactly what `onboarding_compliance_documents` returns —
  // employee_id/name/email, document LABEL (e.g. "SS Card"), category, status, signed/upload dates,
  // who verified/signed — sourced ONLY from `storeops.employee_onboarding` (see hr/router.py). It never
  // reads `employee_onboarding_profile.intake_data` (the Fernet-encrypted PII table), so there is no SSN/
  // bank/routing VALUE in this payload to mask or leak in the first place — the same "no file content in
  // a spreadsheet cell" property the sibling ZIP export has for the underlying documents themselves.
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'employee_name', role: 'rep', get: (d: Doc) => d.employee_name || d.employee_id },
    { header: 'Employee ID', field: 'employee_id', get: (d: Doc) => d.employee_id },
    { header: 'Email', field: 'employee_email', get: (d: Doc) => d.employee_email || '' },
    { header: 'Document', field: 'document_label', get: (d: Doc) => d.document_label + (d.file_index ? ` (${d.file_index} of ${d.file_count})` : '') },
    { header: 'Category', field: 'category', get: (d: Doc) => d.category || '' },
    { header: 'Status', field: 'status', get: (d: Doc) => d.status || '' },
    { header: 'On File', field: 'on_file', get: (d: Doc) => d.has_document ? `File: ${d.document_name || ''}` : (d.has_signature_page ? 'Signed online' : 'No file') },
    { header: 'Date', field: 'signed_at', role: 'date', type: 'date', get: (d: Doc) => (d.signed_at || '').slice(0, 10) },
    { header: 'Signed Name', field: 'signed_name', get: (d: Doc) => d.signed_name || '' },
    { header: 'Verified By', field: 'verified_by', get: (d: Doc) => d.verified_by || '' },
  ]

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🗂️ Compliance Document Repository</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 16 }}>
        Every uploaded or signed onboarding document across the roster, grouped by employee — with each document&apos;s
        signature page linked alongside it. Reuses the same storage the <a href="/hr/onboarding" style={{ color: 'var(--accent,#2563eb)' }}>Documents board</a> tracks; nothing is duplicated here.
      </p>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}
      {!ready && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '10px 14px', fontSize: 13, marginBottom: 14 }}>
        {fetchError || 'Run migration 073_hr_onboarding.sql to activate onboarding before this repository has anything to show.'}
      </div>}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <input style={{ ...inp, width: 260 }} placeholder="Search employee name / ID / email…" value={q} onChange={e => setQ(e.target.value)} />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{loading ? 'Loading…' : `${docs.length} document(s) · ${grouped.length} employee(s)`}</span>
        <div style={{ flex: 1 }} />
        {/* Table-view metadata export (Excel/PDF/Print/Send) — separate from the ZIP below, which
            bundles the actual files. Neither one ever touches Fernet-encrypted intake PII. */}
        <ReportExportBar title="Compliance Document Repository" subtitle={q ? `filter: "${q}"` : undefined} columns={cols} rows={docs} />
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
            <div key={`${d.task_id}-${d.file_id || (d.has_signature_page ? 'sig' : 'x')}`} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', borderTop: '1px solid var(--border)', fontSize: 13 }}>
              <span style={{ flex: 1 }}>{d.document_label}{d.file_index ? <span style={{ color: 'var(--text3)', fontSize: 11 }}> ({d.file_index} of {d.file_count})</span> : ''}{d.category ? <span style={{ color: 'var(--text3)', fontSize: 11 }}> · {d.category}</span> : ''}</span>
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
