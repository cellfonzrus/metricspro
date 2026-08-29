'use client'
// Fix Requests — the approved-fix-request pipeline (mig 716). House support (or an admin from /failures) clubs
// similar failures into ONE request; a SUPER-ADMIN approves it (the approval gate) into the automation queue
// the operator/agent fleet picks up. Server-gated (_support_ctx); approve/reject additionally require a
// login-level super_admin. Nothing here edits code or prod data — approval only enters a queue.
import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

type Affected = { org_id: string; count: number; org_name?: string }
type FR = {
  id: string; org_id: string; owner_name?: string; kind: string | null; module: string | null
  title: string | null; summary: string | null; proposed_action: string | null; code_hint: string | null
  sample_failure_ids: string[] | null; affected_orgs: Affected[] | null; failure_count: number
  status: string; created_by: string | null; approved_by: string | null; approved_at: string | null
  resolution: string | null; created_at: string
}
type Failure = { id: string; tenant_name: string; category: string; severity: string; message: string; created_at: string; reviewed?: boolean }

const STATUS_COLOR: Record<string, string> = {
  new: '#6b7280', pending_approval: '#d97706', approved: '#2563eb', in_progress: '#7c3aed', resolved: '#16a34a', rejected: '#dc2626',
}
const when = (iso?: string | null) => { if (!iso) return '—'; try { return new Date(iso).toLocaleString() } catch { return iso } }

function Pill({ s }: { s: string }) {
  return <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, whiteSpace: 'nowrap', textTransform: 'capitalize', background: (STATUS_COLOR[s] || '#888') + '22', color: STATUS_COLOR[s] || '#555', border: `1px solid ${(STATUS_COLOR[s] || '#888')}55` }}>{s.replace('_', ' ')}</span>
}

export default function FixRequestsPage() {
  const [rows, setRows] = useState<FR[]>([])
  const [statuses, setStatuses] = useState<string[]>([])
  const [canApprove, setCanApprove] = useState(false)
  const [fStatus, setFStatus] = useState('')
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('')
  const [detail, setDetail] = useState<{ fr: FR; failures: Failure[] } | null>(null)

  const load = useCallback(() => {
    setLoading(true); setErr('')
    api(`/api/v1/helpdesk/support/fix-requests${fStatus ? `?status=${fStatus}` : ''}`).then((d: any) => {
      setRows(d.fix_requests || []); setStatuses(d.statuses || []); setCanApprove(!!d.can_approve)
    }).catch((e: any) => setErr(e?.message || 'Could not load fix requests')).finally(() => setLoading(false))
  }, [fStatus])
  useEffect(() => { load() }, [load])

  async function openDetail(id: string) {
    setMsg('')
    try { const d = await api(`/api/v1/helpdesk/support/fix-requests/${id}`); setDetail({ fr: d.fix_request, failures: d.failures || [] }); setCanApprove(!!d.can_approve) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  async function transition(fr: FR, status: string, opts: { resolution?: string; mark_reviewed?: boolean } = {}) {
    if ((status === 'approved' || status === 'rejected') && !canApprove) { setMsg('❌ Only a super-admin can approve or reject.'); return }
    let resolution = opts.resolution; let mark_reviewed = opts.mark_reviewed
    if (status === 'resolved') {
      resolution = window.prompt('Resolution note (what was fixed)?', fr.resolution || '') || ''
      if (resolution === '') return
      mark_reviewed = window.confirm('Also mark the clubbed failure rows as reviewed?')
    }
    if (status === 'rejected' && !window.confirm('Reject this fix request?')) return
    setBusy(true); setMsg('')
    try {
      await api(`/api/v1/helpdesk/support/fix-requests/${fr.id}/status`, { method: 'POST', body: JSON.stringify({ status, resolution, mark_reviewed }) })
      setMsg(`✅ ${status.replace('_', ' ')}.`); setDetail(null); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  const sel2: React.CSSProperties = { padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }
  const exportCols: ExportColumn[] = [
    { header: 'Title', get: (r: FR) => r.title || r.kind || '' }, { header: 'Kind', get: (r: FR) => r.kind || '' },
    { header: 'Module', get: (r: FR) => r.module || '' }, { header: 'Status', get: (r: FR) => r.status },
    { header: 'Failures', get: (r: FR) => String(r.failure_count) },
    { header: 'Tenants', get: (r: FR) => String((r.affected_orgs || []).length) },
    { header: 'Owner', get: (r: FR) => r.owner_name || '' }, { header: 'Created by', get: (r: FR) => r.created_by || '' },
    { header: 'Approved by', get: (r: FR) => r.approved_by || '' }, { header: 'Created', get: (r: FR) => when(r.created_at) },
  ]
  const queueCount = useMemo(() => rows.filter(r => r.status === 'approved').length, [rows])

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🛠️ Fix Requests</h1>
        <span style={{ fontSize: 13, color: 'var(--text3)' }}>{rows.length} request{rows.length === 1 ? '' : 's'}
          {queueCount > 0 && <span style={{ color: '#2563eb', fontWeight: 600 }}> · {queueCount} approved (queued)</span>}</span>
        <span style={{ flex: 1 }} />
        <Link href="/admin/support/failures" className="btn btn-sm">🩺 Fleet Triage</Link>
        <Link href="/admin/support" className="btn btn-sm">🎧 Console</Link>
      </div>
      <p className="pg-note" style={{ color: 'var(--text3)', fontSize: 12, marginTop: 4 }}>
        Clubbed groups of similar failures. {canApprove ? 'You are a super-admin — you can approve/reject.' : 'Approval is super-admin only.'} Approved requests form the queue the fleet picks up. Nothing here ships code automatically.
      </p>

      <div className="card" style={{ padding: 12, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', margin: '10px 0' }}>
        <select style={sel2} value={fStatus} onChange={e => setFStatus(e.target.value)}>
          <option value="">All statuses</option>{statuses.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
          {statuses.length === 0 && ['new', 'pending_approval', 'approved', 'in_progress', 'resolved', 'rejected'].map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
        </select>
        <button className="btn btn-sm" onClick={load}>Refresh</button>
        {msg && <span style={{ fontSize: 12.5 }}>{msg}</span>}
        <span style={{ flex: 1 }} />
        <ReportExportBar title="Fix Requests" filename="fix_requests" columns={exportCols} rows={rows} />
      </div>
      {err && <div className="card" style={{ padding: 14, color: '#dc2626' }}>{err}{err.includes('716') && ' — run migration 716 in Supabase.'}</div>}

      {loading ? <div className="card" style={{ padding: 16 }}>Loading…</div> : rows.length === 0 ? (
        <div className="card" style={{ padding: 16, color: 'var(--text3)' }}>No fix requests yet. Club a group of failures on the Fleet Triage page to create one.</div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          {rows.map(r => (
            <div key={r.id} onClick={() => openDetail(r.id)} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '11px 14px', cursor: 'pointer', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 600, flex: 1, minWidth: 200 }}>{r.title || r.kind || '(untitled)'}</span>
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>{r.module || '—'}</span>
              <span style={{ fontSize: 12, color: 'var(--text2)' }}>{r.failure_count} failure{r.failure_count === 1 ? '' : 's'} · {(r.affected_orgs || []).length} tenant{(r.affected_orgs || []).length === 1 ? '' : 's'}</span>
              <Pill s={r.status} />
              <span style={{ fontSize: 11, color: 'var(--text3)', width: 90, textAlign: 'right' }}>{String(r.created_at).slice(0, 10)}</span>
            </div>
          ))}
        </div>
      )}

      {detail && (
        <div onClick={() => setDetail(null)} style={{ position: 'fixed', inset: 0, background: '#0007', display: 'flex', justifyContent: 'center', alignItems: 'flex-start', padding: 30, zIndex: 50, overflow: 'auto' }}>
          <div onClick={e => e.stopPropagation()} className="card" style={{ padding: 20, maxWidth: 760, width: '100%' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
              <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0, flex: 1 }}>{detail.fr.title || detail.fr.kind}</h2>
              <Pill s={detail.fr.status} />
              <button className="btn btn-sm" onClick={() => setDetail(null)}>✕</button>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>
              {detail.fr.kind} · {detail.fr.module || '—'} · created by {detail.fr.created_by || '—'} on {when(detail.fr.created_at)}
              {detail.fr.approved_by && <> · approved by {detail.fr.approved_by} {when(detail.fr.approved_at)}</>}
            </div>
            <div style={{ display: 'grid', gap: 8, margin: '14px 0', fontSize: 13 }}>
              <div><b>Summary:</b> {detail.fr.summary || '—'}</div>
              <div><b>Proposed action:</b> {detail.fr.proposed_action || '—'}</div>
              {detail.fr.code_hint && <div style={{ color: 'var(--text2)' }}><b>Code area:</b> <code>{detail.fr.code_hint}</code></div>}
              <div style={{ color: 'var(--text2)' }}><b>Affected:</b> {(detail.fr.affected_orgs || []).map(o => `${o.org_name || 'Tenant'} (${o.count})`).join(' · ') || '—'}</div>
              {detail.fr.resolution && <div style={{ color: '#16a34a' }}><b>Resolution:</b> {detail.fr.resolution}</div>}
            </div>

            {/* Actions */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '10px 0 14px' }}>
              {['new', 'pending_approval'].includes(detail.fr.status) && (
                <>
                  <button className="btn btn-sm btn-primary" disabled={busy || !canApprove} title={canApprove ? '' : 'Super-admin only'} onClick={() => transition(detail.fr, 'approved')}>✓ Approve</button>
                  <button className="btn btn-sm" disabled={busy || !canApprove} title={canApprove ? '' : 'Super-admin only'} onClick={() => transition(detail.fr, 'rejected')}>✕ Reject</button>
                </>
              )}
              {detail.fr.status === 'new' && <button className="btn btn-sm" disabled={busy} onClick={() => transition(detail.fr, 'pending_approval')}>Submit for approval</button>}
              {detail.fr.status === 'approved' && <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => transition(detail.fr, 'in_progress')}>▶ Start (in progress)</button>}
              {['approved', 'in_progress'].includes(detail.fr.status) && <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => transition(detail.fr, 'resolved')}>✓ Resolve</button>}
            </div>

            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Clubbed failures ({detail.failures.length})</div>
            <div className="table-wrapper" style={{ padding: 0, maxHeight: 300, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>{['When', 'Tenant', 'What happened', 'Reviewed'].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
                <tbody>
                  {detail.failures.length === 0 && <tr><td colSpan={4} style={{ padding: 10, fontSize: 12, color: 'var(--text3)' }}>No sample rows retained.</td></tr>}
                  {detail.failures.map(f => (
                    <tr key={f.id}>
                      <td style={{ padding: '6px 10px', fontSize: 12, color: 'var(--text3)', borderTop: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{when(f.created_at)}</td>
                      <td style={{ padding: '6px 10px', fontSize: 12, fontWeight: 600, borderTop: '1px solid var(--border)' }}>{f.tenant_name}</td>
                      <td style={{ padding: '6px 10px', fontSize: 12, borderTop: '1px solid var(--border)' }}>{f.message}</td>
                      <td style={{ padding: '6px 10px', fontSize: 12, borderTop: '1px solid var(--border)' }}>{f.reviewed ? '✓' : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
