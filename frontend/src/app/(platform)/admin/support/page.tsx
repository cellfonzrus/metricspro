'use client'
// Tech-Support Console — the HOUSE support team's CROSS-TENANT queue of escalated cases (mig 715).
// Server-gated (super_admin OR house-org membership w/ modules.support); a tenant user never reaches it.
// RULE FOUR exports via ReportExportBar. Support cases have no store/market/rep dimension, so the RULE
// FIVE core-set is applied WHERE MEANINGFUL (status/priority/tenant/assignee/page filters) — documented.
import { useState, useEffect, useMemo, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { api } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

type Case = {
  id: string; org_id: string; tenant_name: string; ticket_number: string | null; ticket_subject: string | null
  page_key: string | null; status: string; priority: string; assignee_email: string | null
  sla_due_at: string | null; requester: string | null; created_at: string
}

function Pill({ label, color }: { label?: string | null; color?: string }) {
  if (!label) return null
  return <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, whiteSpace: 'nowrap',
    background: (color || '#888') + '22', color: color || '#555', border: `1px solid ${(color || '#888')}55` }}>{label}</span>
}
const PRIORITY_COLOR: Record<string, string> = { urgent: '#ef4444', high: '#f97316', normal: '#3b82f6', low: '#6b7280' }
const STATUS_COLOR: Record<string, string> = { new: '#3b82f6', in_progress: '#f59e0b', waiting_user: '#6b7280', resolved: '#22c55e', closed: '#475569' }

function slaBadge(due: string | null) {
  if (!due) return null
  const ms = new Date(due).getTime() - Date.now()
  if (Number.isNaN(ms)) return null
  if (ms < 0) return { label: 'SLA overdue', color: '#dc2626' }
  if (ms < 8 * 3600 * 1000) return { label: 'SLA due soon', color: '#d97706' }
  return { label: 'On track', color: '#16a34a' }
}

export default function SupportConsole() {
  const router = useRouter()
  const [cases, setCases] = useState<Case[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [fStatus, setFStatus] = useState('')
  const [fPriority, setFPriority] = useState('')
  const [fTenant, setFTenant] = useState('')
  const [fAssignee, setFAssignee] = useState('')
  const [q, setQ] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const d = await api('/api/v1/helpdesk/support/cases?limit=500')
      setCases(d.cases || [])
    } catch (e: any) { setErr(e?.message || 'Could not load the support console') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  const tenants = useMemo(() => Array.from(new Map(cases.map(c => [c.org_id, c.tenant_name])).entries()), [cases])
  const assignees = useMemo(() => Array.from(new Set(cases.map(c => c.assignee_email).filter(Boolean))) as string[], [cases])

  const visible = useMemo(() => cases.filter(c =>
    (!fStatus || c.status === fStatus) &&
    (!fPriority || c.priority === fPriority) &&
    (!fTenant || c.org_id === fTenant) &&
    (!fAssignee || c.assignee_email === fAssignee) &&
    (!q || `${c.ticket_number || ''} ${c.ticket_subject || ''} ${c.tenant_name || ''} ${c.page_key || ''} ${c.requester || ''}`.toLowerCase().includes(q.toLowerCase()))
  ), [cases, fStatus, fPriority, fTenant, fAssignee, q])

  const exportRows = visible.map(c => ({
    number: c.ticket_number || '', tenant: c.tenant_name, subject: c.ticket_subject || '',
    priority: c.priority, status: c.status, assignee: c.assignee_email || '',
    page: c.page_key || '', sla_due: c.sla_due_at || '', requester: c.requester || '',
    created: String(c.created_at).slice(0, 16).replace('T', ' '),
  }))
  const cols: ExportColumn[] = [
    { header: 'Ticket', get: r => r.number }, { header: 'Tenant', get: r => r.tenant },
    { header: 'Subject', get: r => r.subject }, { header: 'Priority', get: r => r.priority },
    { header: 'Status', get: r => r.status }, { header: 'Assignee', get: r => r.assignee },
    { header: 'Page', get: r => r.page }, { header: 'SLA due', get: r => r.sla_due },
    { header: 'Requester', get: r => r.requester }, { header: 'Created', get: r => r.created },
  ]
  const sel = { padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }
  const overdue = visible.filter(c => c.sla_due_at && new Date(c.sla_due_at).getTime() < Date.now() && !['resolved', 'closed'].includes(c.status)).length

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎧 Support Console</h1>
        <span style={{ fontSize: 13, color: 'var(--text3)' }}>Cross-tenant queue · {visible.length} case{visible.length === 1 ? '' : 's'}
          {overdue > 0 && <span style={{ color: '#dc2626', fontWeight: 600 }}> · {overdue} overdue</span>}</span>
        <span style={{ flex: 1 }} />
        <Link href="/admin/support/docs" className="btn btn-sm">📚 Help Docs</Link>
      </div>
      <p className="pg-note" style={{ color: 'var(--text3)', fontSize: 12, marginTop: 4 }}>
        Escalated tickets from every tenant, handled here by the house tech-support team.
      </p>

      <div className="card" style={{ padding: 12, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', margin: '10px 0' }}>
        <input className="input" style={{ ...sel, minWidth: 200 }} placeholder="Search subject / tenant / page…" value={q} onChange={e => setQ(e.target.value)} />
        <select style={sel} value={fStatus} onChange={e => setFStatus(e.target.value)}>
          <option value="">All statuses</option>{['new', 'in_progress', 'waiting_user', 'resolved', 'closed'].map(s => <option key={s} value={s}>{s}</option>)}</select>
        <select style={sel} value={fPriority} onChange={e => setFPriority(e.target.value)}>
          <option value="">All priorities</option>{['urgent', 'high', 'normal', 'low'].map(s => <option key={s} value={s}>{s}</option>)}</select>
        <select style={sel} value={fTenant} onChange={e => setFTenant(e.target.value)}>
          <option value="">All tenants</option>{tenants.map(([oid, name]) => <option key={oid} value={oid}>{name}</option>)}</select>
        <select style={sel} value={fAssignee} onChange={e => setFAssignee(e.target.value)}>
          <option value="">Any assignee</option>{assignees.map(a => <option key={a} value={a}>{a}</option>)}</select>
        <span style={{ flex: 1 }} />
        <ReportExportBar title="Support Console" filename="support_console" columns={cols} rows={exportRows} />
      </div>

      {err && <div className="card" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 12, marginBottom: 12 }}>{err}</div>}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {loading ? <div style={{ padding: 16, color: 'var(--text3)' }}>Loading…</div>
          : visible.length === 0 ? <div style={{ padding: 16, color: 'var(--text3)' }}>No cases match.</div>
          : visible.map(c => {
            const sla = slaBadge(c.sla_due_at)
            return (
              <div key={c.id} onClick={() => router.push(`/admin/support/${c.id}`)}
                style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '10px 14px', cursor: 'pointer', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
                <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text3)', width: 70 }}>{c.ticket_number || '—'}</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)', width: 130, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={c.tenant_name}>{c.tenant_name}</span>
                <span style={{ fontWeight: 600, flex: 1, minWidth: 200 }}>{c.ticket_subject || '(no subject)'}</span>
                <Pill label={c.priority} color={PRIORITY_COLOR[c.priority]} />
                <Pill label={c.status} color={STATUS_COLOR[c.status]} />
                {sla && !['resolved', 'closed'].includes(c.status) && <Pill label={sla.label} color={sla.color} />}
                {c.assignee_email && <span style={{ fontSize: 11, color: 'var(--text3)', width: 120, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis' }}>→ {c.assignee_email}</span>}
                <span style={{ fontSize: 11, color: 'var(--text3)', width: 84, textAlign: 'right' }}>{String(c.created_at).slice(0, 10)}</span>
              </div>
            )
          })}
      </div>
    </div>
  )
}
