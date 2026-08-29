'use client'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, supabase, activeOrgHeader } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import EntityPicker, { type EntityOption } from '@/components/EntityPicker'

// HR · Compliance Document Repository (item 5). A VIEW + bulk export over the SAME onboarding-docs
// bucket and employee_onboarding rows the Documents board (mig 082) already tracks — not a second
// store. Every uploaded/signed document across the roster, employee-grouped, filterable, each row
// carrying a link to its own file AND (when the item was signed online) its signature page. Bulk
// "pick up at once" export = one ZIP, organized /EmployeeName/DocumentLabel.ext. Backed by
// /api/v1/hr/onboarding/compliance-documents (+ /export). Reachable directly at /hr/compliance —
// a sidebar nav entry is pending (see NEEDS CORE in docs/handoffs/people.md; layout.tsx is shared).
//
// OWNER DIRECTIVE 2026-07-27 — two independent date-range filters (when the request was SENT, when
// the document was SUBMITTED) + an employee MULTI-select picker (RULE THREE §3b) replacing the old
// free-text employee search. No store/market filter: onboarding documents carry no store dimension
// (they're per-employee HR paperwork, not tied to a location) — RULE FIVE (§3d) applies "where it
// makes sense"; inventing one here would reference data that doesn't exist. The two date ranges don't
// map onto StandardFilterBar's single `period` slot (it's built for ONE time dimension), so this page
// uses the same shared pick-don't-type primitive (<EntityPicker multi>) directly rather than the full
// bar, styled to match it. Both date inputs are native <input type="date"> — the raw 'YYYY-MM-DD'
// value is passed straight through as a query param string, never round-tripped through `new Date()`,
// so the JS off-by-one class (see lib/client.ts parseLocalDate) never has a chance to bite. Gate-1 N1:
// both dates are UTC CALENDAR dates (same convention as every other date already shown here) — a
// separate storage-timezone class (a late-evening America/New_York upload stamps as the next UTC day)
// is a deliberate, filed follow-up (see docs/handoffs/people.md 2026-07-27 fold), not fixed here,
// because display/filter/export all agreeing today is more honest than a filter-only fix that would
// disagree with what the row still visibly shows. "Request sent" is labeled "Packet sent" in the UI
// (Gate-1 N7) — it's the date the WHOLE onboarding packet was requested, not a per-document event;
// see the backend docstring for why no such per-document event exists to filter on instead.

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const inp: React.CSSProperties = { padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const btn: React.CSSProperties = { padding: '6px 11px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, cursor: 'pointer', background: 'var(--surface)' }
const btnP: React.CSSProperties = { ...btn, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none', fontWeight: 600 }
const lbl: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }

type Doc = {
  employee_id: string; employee_name?: string; employee_email?: string
  task_id: string; document_label: string; category?: string; status?: string; document_name?: string
  has_document?: boolean; has_signature_page?: boolean; signed_at?: string | null; signed_name?: string | null; verified_by?: string | null
  // migration 402: a task with multiple files now contributes one row per file (file_id + 1-based
  // file_index/file_count so "1 of 2" can be shown) instead of collapsing to just the latest.
  file_id?: string | null; file_index?: number | null; file_count?: number
  // OWNER DIRECTIVE 2026-07-27: when the onboarding document REQUEST was sent to this employee
  // (employee_onboarding_profile.docs_sent_at, falling back to invited_at — see the backend docstring
  // for why this is employee-level, not per-document: there is no per-document "request sent" event in
  // this product, the whole packet is requested at once). Same value on every row for one employee.
  request_sent_at?: string | null
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
  const [msg, setMsg] = useState('')
  const [fetchError, setFetchError] = useState('')
  const [loading, setLoading] = useState(false)

  // Employee MULTI-select (RULE THREE §3b) — replaces the old free-text search box. Options come from
  // the org's real roster (GET /storeops/employees, org-scoped by the caller's JWT — read-only use of
  // an existing endpoint, no new roster store). employee_id (the business id, NOT the numeric row id —
  // that mismatch is the exact bug class the payroll money-fix just closed) drives the server query.
  const [employeeOptions, setEmployeeOptions] = useState<EntityOption[]>([])
  const [employeeIds, setEmployeeIds] = useState<string[]>([])
  useEffect(() => {
    apiCached('/api/v1/storeops/employees?all_company=true&include_inactive=true', LOOKUP)
      .then((rows: any[]) => {
        const opts = (rows || [])
          .filter(e => (e?.employee_id || '').toString().trim())
          .map(e => ({
            id: String(e.employee_id),
            label: (e.name || e.employee_id) + (e.is_active === false ? ' (inactive)' : ''),
            sublabel: e.email || undefined,
          }))
          .sort((a, b) => a.label.localeCompare(b.label))
        setEmployeeOptions(opts)
      })
      .catch(() => {})
  }, [])

  // Two independent, composable (AND), inclusive-both-ends date-range filters.
  const [sentFrom, setSentFrom] = useState('')
  const [sentTo, setSentTo] = useState('')
  const [submittedFrom, setSubmittedFrom] = useState('')
  const [submittedTo, setSubmittedTo] = useState('')

  // Honest-degrade counters from the backend — never let a filter make rows silently vanish with no
  // explanation (owner directive).
  const [notSubmittedCount, setNotSubmittedCount] = useState(0)
  const [notSubmittedEmpCount, setNotSubmittedEmpCount] = useState(0)
  const [submittedUnknownCount, setSubmittedUnknownCount] = useState(0)
  const [sentUnknownCount, setSentUnknownCount] = useState(0)

  const anyDateFilter = !!(sentFrom || sentTo || submittedFrom || submittedTo)
  const anyFilter = anyDateFilter || employeeIds.length > 0

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams()
      if (employeeIds.length) qs.set('employee_ids', employeeIds.join(','))
      if (sentFrom) qs.set('sent_from', sentFrom)
      if (sentTo) qs.set('sent_to', sentTo)
      if (submittedFrom) qs.set('submitted_from', submittedFrom)
      if (submittedTo) qs.set('submitted_to', submittedTo)
      const r = await api(`/api/v1/hr/onboarding/compliance-documents${qs.toString() ? `?${qs.toString()}` : ''}`)
      setReady(r?.ready !== false); setDocs(r?.documents || [])
      // Truth-telling: a failed read on the backend now comes back as ready:false + an explicit error
      // message rather than a silent empty list — show it instead of letting the page look like an
      // empty-but-healthy repository.
      setFetchError(r?.ready === false ? (r?.error || '') : '')
      setNotSubmittedCount(r?.not_submitted_count || 0)
      setNotSubmittedEmpCount(r?.not_submitted_employee_count || 0)
      setSubmittedUnknownCount(r?.submitted_unknown_count || 0)
      setSentUnknownCount(r?.sent_unknown_count || 0)
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
    setLoading(false)
  }
  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  // Re-fetch (server re-filters) whenever any filter changes — skip the initial mount (already loaded above).
  const didMount = useRef(false)
  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return }
    const t = setTimeout(load, 250)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employeeIds, sentFrom, sentTo, submittedFrom, submittedTo])

  function clearFilters() {
    setEmployeeIds([]); setSentFrom(''); setSentTo(''); setSubmittedFrom(''); setSubmittedTo('')
  }

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
    const by: Record<string, { name: string; email?: string; sentAt?: string | null; docs: Doc[] }> = {}
    for (const d of docs) {
      const g = (by[d.employee_id] ||= { name: d.employee_name || d.employee_id, email: d.employee_email, sentAt: d.request_sent_at, docs: [] })
      g.docs.push(d)
    }
    return Object.entries(by).sort((a, b) => a[1].name.localeCompare(b[1].name))
  }, [docs])

  // RULE FOUR (§3c) — TABLE-VIEW export (Excel/PDF/Print/Send), alongside the existing ZIP file
  // export. What-you-see-is-what-exports: `rows={docs}` is the SAME already-filtered array the page
  // renders (server-side filtered by the active employee/date-range selection above). PII SAFETY:
  // this row shape is exactly what `onboarding_compliance_documents` returns — employee_id/name/email,
  // document LABEL (e.g. "SS Card"), category, status, signed/upload dates, who verified/signed —
  // sourced ONLY from `storeops.employee_onboarding` (+ profile send dates). It never reads
  // `employee_onboarding_profile.intake_data` (the Fernet-encrypted PII table), so there is no SSN/
  // bank/routing VALUE in this payload to mask or leak in the first place — the same "no file content
  // in a spreadsheet cell" property the sibling ZIP export has for the underlying documents themselves.
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'employee_name', role: 'rep', get: (d: Doc) => d.employee_name || d.employee_id },
    { header: 'Employee ID', field: 'employee_id', get: (d: Doc) => d.employee_id },
    { header: 'Email', field: 'employee_email', get: (d: Doc) => d.employee_email || '' },
    { header: 'Document', field: 'document_label', get: (d: Doc) => d.document_label + (d.file_index ? ` (${d.file_index} of ${d.file_count})` : '') },
    { header: 'Category', field: 'category', get: (d: Doc) => d.category || '' },
    { header: 'Status', field: 'status', get: (d: Doc) => d.status || '' },
    { header: 'On File', field: 'on_file', get: (d: Doc) => d.has_document ? `File: ${d.document_name || ''}` : (d.has_signature_page ? 'Signed online' : 'No file') },
    { header: 'Packet Sent', field: 'request_sent_at', role: 'date', type: 'date', get: (d: Doc) => d.request_sent_at ? String(d.request_sent_at).slice(0, 10) : '(no date recorded)' },
    { header: 'Submitted', field: 'signed_at', role: 'date', type: 'date', get: (d: Doc) => (d.signed_at || '').slice(0, 10) },
    { header: 'Signed Name', field: 'signed_name', get: (d: Doc) => d.signed_name || '' },
    { header: 'Verified By', field: 'verified_by', get: (d: Doc) => d.verified_by || '' },
  ]

  return (
    <div style={{ padding: 24, maxWidth: 1080 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🗂️ Compliance Document Repository</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 16 }}>
        Every uploaded or signed onboarding document across the roster, grouped by employee — with each document&apos;s
        signature page linked alongside it. Reuses the same storage the <a href="/hr/onboarding" style={{ color: 'var(--accent,#2563eb)' }}>Documents board</a> tracks; nothing is duplicated here.
      </p>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}
      {!ready && <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '10px 14px', fontSize: 13, marginBottom: 14 }}>
        {fetchError || 'Run migration 073_hr_onboarding.sql to activate onboarding before this repository has anything to show.'}
      </div>}

      {/* Filter row — employee multi-select (pick, don't type) + the two independent date ranges.
          No store/market: onboarding documents carry no store dimension (see file-header note). */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <EntityPicker multi options={employeeOptions} value={employeeIds} onChange={setEmployeeIds}
          placeholder="Filter by employee(s)…" ariaLabel="Filter by employee" width={280} />
        <label style={lbl}>Packet sent
          <input type="date" style={inp} value={sentFrom} onChange={e => setSentFrom(e.target.value)} aria-label="Packet sent from" />
          <span style={{ color: 'var(--text3)' }}>–</span>
          <input type="date" style={inp} value={sentTo} onChange={e => setSentTo(e.target.value)} aria-label="Packet sent to" />
        </label>
        <label style={lbl}>Submitted
          <input type="date" style={inp} value={submittedFrom} onChange={e => setSubmittedFrom(e.target.value)} aria-label="Submitted from" />
          <span style={{ color: 'var(--text3)' }}>–</span>
          <input type="date" style={inp} value={submittedTo} onChange={e => setSubmittedTo(e.target.value)} aria-label="Submitted to" />
        </label>
        {anyFilter && <button style={btn} onClick={clearFilters}>Clear filters</button>}
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6 }}>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>
          {loading ? 'Loading…' : `${docs.length} document(s) · ${grouped.length} employee(s)`}
          {notSubmittedCount > 0 && ` · ${notSubmittedCount} outstanding item(s) (${notSubmittedEmpCount} employee(s))`}
        </span>
        <div style={{ flex: 1 }} />
        {/* Table-view metadata export (Excel/PDF/Print/Send) — separate from the ZIP below, which
            bundles the actual files. Neither one ever touches Fernet-encrypted intake PII. */}
        <ReportExportBar title="Compliance Document Repository"
          subtitle={anyFilter ? [
            employeeIds.length ? `${employeeIds.length} employee(s)` : '',
            (sentFrom || sentTo) ? `packet sent ${sentFrom || '…'} → ${sentTo || '…'}` : '',
            (submittedFrom || submittedTo) ? `submitted ${submittedFrom || '…'} → ${submittedTo || '…'}` : '',
          ].filter(Boolean).join(' · ') : undefined}
          columns={cols} rows={docs} />
        <button style={btnP} onClick={() => downloadZip('/api/v1/hr/onboarding/compliance-documents/export', 'onboarding-documents-all.zip', setMsg)}>
          📦 Export ALL (one zip)
        </button>
      </div>

      {/* Honest degrade — a submitted-range filter naturally has nothing to show for a task nobody has
          touched yet (it was never in `documents` to begin with); say so instead of letting those
          employees/rows just silently vanish. */}
      {(submittedFrom || submittedTo) && notSubmittedCount > 0 && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginBottom: 10 }}>
          ⚠️ {notSubmittedCount} outstanding item(s) across {notSubmittedEmpCount} active employee(s) haven&apos;t been submitted yet —
          they can&apos;t appear under a submitted-date filter (there&apos;s nothing to date yet), so they&apos;re not counted above.
        </div>
      )}
      {(submittedFrom || submittedTo) && submittedUnknownCount > 0 && (
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text2)', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginBottom: 10 }}>
          ℹ️ {submittedUnknownCount} document(s) on file have no recorded submission date and are excluded by this filter (never fabricated as in/out of range).
        </div>
      )}
      {(sentFrom || sentTo) && sentUnknownCount > 0 && (
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text2)', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginBottom: 10 }}>
          ℹ️ {sentUnknownCount} record(s) — some already-submitted documents, some employees&apos; outstanding items — have no
          recorded packet-sent date (no invite or &quot;Send documents&quot; click on file) and are excluded by this filter.
        </div>
      )}

      {grouped.map(([eid, g]) => (
        <div key={eid} style={{ border: '1px solid var(--border)', borderRadius: 10, marginBottom: 12, overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', background: 'var(--surface)' }}>
            <a href={`/hr/onboarding/${eid}`} style={{ fontWeight: 700, fontSize: 14, color: 'var(--accent,#2563eb)', textDecoration: 'none' }}>{g.name}</a>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>{eid}{g.email ? ` · ${g.email}` : ''}</span>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {g.docs.length} document(s)</span>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>
              · Packet sent {g.sentAt ? String(g.sentAt).slice(0, 10) : '(no date recorded)'}
            </span>
            <div style={{ flex: 1 }} />
            <button style={{ ...btn, fontSize: 11, padding: '4px 8px' }} onClick={() => downloadZip(`/api/v1/hr/onboarding/compliance-documents/export?employee_id=${encodeURIComponent(eid)}`, `onboarding-documents-${eid}.zip`, setMsg)}>
              📦 Export this employee
            </button>
          </div>
          {g.docs.map(d => (
            <div key={`${d.task_id}-${d.file_id || (d.has_signature_page ? 'sig' : 'x')}`} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', borderTop: '1px solid var(--border)', fontSize: 13 }}>
              <span style={{ flex: 1 }}>{d.document_label}{d.file_index ? <span style={{ color: 'var(--text3)', fontSize: 11 }}> ({d.file_index} of {d.file_count})</span> : ''}{d.category ? <span style={{ color: 'var(--text3)', fontSize: 11 }}> · {d.category}</span> : ''}</span>
              <span style={{ fontSize: 11, color: 'var(--text3)', width: 130 }} title="Submitted">{d.signed_at ? String(d.signed_at).slice(0, 10) : ''}</span>
              {d.has_document && <button style={{ ...btn, fontSize: 11, padding: '3px 8px' }} onClick={() => viewDoc(d)}>📄 View {d.document_name ? `(${d.document_name})` : ''}</button>}
              {d.has_signature_page && <button style={{ ...btn, fontSize: 11, padding: '3px 8px' }} onClick={() => viewSignature(d)}>✍️ Signature page</button>}
              {!d.has_document && !d.has_signature_page && <span style={{ fontSize: 11, color: 'var(--text3)' }}>no file</span>}
            </div>
          ))}
        </div>
      ))}
      {!loading && grouped.length === 0 && (
        <div style={{ padding: 20, color: 'var(--text3)', fontSize: 13 }}>
          {anyFilter ? 'No documents match the current filters.' : 'No documents on file yet.'}
        </div>
      )}
    </div>
  )
}
