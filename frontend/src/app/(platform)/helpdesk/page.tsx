'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { api, ORG_ID } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import AiAssistant from '@/components/AiAssistant'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

const enc = encodeURIComponent
type Ticket = {
  id: string; display_number: string; subject: string; created_at: string
  requester_name: string | null; requester_email: string | null; assignee: string | null
  status: { label: string; color: string; stage: string }
  priority: { label: string; color: string }; category: { name: string | null }
}

function Badge({ label, color }: { label?: string; color?: string }) {
  if (!label) return null
  return <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, whiteSpace: 'nowrap',
    background: (color || '#888') + '22', color: color || '#555', border: `1px solid ${(color || '#888')}55` }}>{label}</span>
}

export default function HelpdeskInbox() {
  const { user, permissions } = useAuth()
  const router = useRouter()
  const isAgent = (permissions?.scope || 'all') !== 'self'
  const requester = user?.email || ''

  const [tickets, setTickets] = useState<Ticket[]>([])
  const [statuses, setStatuses] = useState<any[]>([])
  const [priorities, setPriorities] = useState<any[]>([])
  const [view, setView] = useState<'all' | 'mine' | 'unassigned'>('all')
  const [statusKey, setStatusKey] = useState('')
  const [priorityKey, setPriorityKey] = useState('')
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    api(`/api/v1/helpdesk/config/bootstrap?org_id=${ORG_ID}`)
      .then((d: any) => { setStatuses(d.statuses || []); setPriorities(d.priorities || []) })
      .catch(() => {})
  }, [])

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const qs = `org_id=${ORG_ID}&agent=${isAgent}&requester=${enc(requester)}&view=${view}` +
        `&status_key=${enc(statusKey)}&priority_key=${enc(priorityKey)}&q=${enc(q)}`
      setTickets(await api(`/api/v1/helpdesk/tickets?${qs}`))
    } catch (e: any) { setErr(e?.message || 'Failed to load tickets') }
    finally { setLoading(false) }
  }, [isAgent, requester, view, statusKey, priorityKey, q])
  useEffect(() => { load() }, [load])

  // RULE FOUR (§3c) exports — the loaded `tickets` already reflect the view/status/priority/search filters
  // (applied server-side + already scoped to what the caller may see), so what's listed is what exports.
  const exportCols: ExportColumn[] = [
    { header: 'Number', field: 'display_number', get: t => t.display_number },
    { header: 'Subject', field: 'subject', get: t => t.subject },
    { header: 'Category', field: 'category', get: t => t.category?.name || '' },
    { header: 'Priority', field: 'priority', get: t => t.priority?.label || '' },
    { header: 'Status', field: 'status', get: t => t.status?.label || '' },
    { header: 'Requester', field: 'requester', role: 'rep', get: t => t.requester_name || t.requester_email || '' },
    { header: 'Assignee', field: 'assignee', get: t => t.assignee || '' },
    { header: 'Created', field: 'created_at', type: 'date', get: t => String(t.created_at).slice(0, 10) },
  ]

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🎫 Helpdesk</h1>
        <span style={{ flex: 1 }} />
        {isAgent && <Link href="/helpdesk/dashboard" className="btn">📊 Dashboard</Link>}
        {(permissions?.modules?.admin || permissions?.scope === 'all') && <Link href="/helpdesk/settings" className="btn">⚙️ Settings</Link>}
        <Link href="/helpdesk/new" className="btn btn-primary">➕ Raise a ticket</Link>
      </div>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>
        {isAgent ? 'All tickets in your organization. Assign, prioritize, and resolve.' : 'Your tickets. Raise a new one anytime — a manager will pick it up.'}
      </p>

      {(permissions?.modules?.ai_assistant || permissions?.scope === 'all') && <AiAssistant />}

      <div className="card" style={{ padding: 12, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
        {isAgent && (['all', 'mine', 'unassigned'] as const).map(v => (
          <button key={v} className={`btn btn-sm ${view === v ? 'btn-primary' : ''}`} onClick={() => setView(v)}>
            {v === 'all' ? 'All' : v === 'mine' ? 'Assigned to me' : 'Unassigned'}</button>
        ))}
        <select className="input" value={statusKey} onChange={e => setStatusKey(e.target.value)} style={{ width: 150 }}>
          <option value="">All statuses</option>
          {statuses.map(s => <option key={s.id} value={s.key}>{s.label}</option>)}
        </select>
        <select className="input" value={priorityKey} onChange={e => setPriorityKey(e.target.value)} style={{ width: 140 }}>
          <option value="">All priorities</option>
          {priorities.map(p => <option key={p.id} value={p.key}>{p.label}</option>)}
        </select>
        <input className="input" placeholder="Search subject…" value={q} onChange={e => setQ(e.target.value)} style={{ width: 200 }} />
        <span style={{ flex: 1 }} />
        <ReportExportBar title="Helpdesk tickets" filename="helpdesk_tickets" columns={exportCols} rows={tickets} />
      </div>

      {err && <div className="card" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 12, marginBottom: 12 }}>{err}</div>}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {loading ? <div style={{ padding: 30, color: 'var(--text3)' }}>Loading…</div>
          : tickets.length === 0 ? <div style={{ padding: 30, color: 'var(--text3)' }}>No tickets. <Link href="/helpdesk/new" style={{ color: '#2563eb' }}>Raise one →</Link></div>
          : tickets.map(t => (
            <div key={t.id} onClick={() => router.push(`/helpdesk/${t.id}`)}
              style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '10px 14px', cursor: 'pointer',
                borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
              <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text3)', width: 78 }}>{t.display_number}</span>
              <span style={{ fontWeight: 600, flex: 1, minWidth: 200 }}>{t.subject}</span>
              <Badge label={t.category?.name || undefined} color="#64748b" />
              <Badge label={t.priority?.label} color={t.priority?.color} />
              <Badge label={t.status?.label} color={t.status?.color} />
              {isAgent && <span style={{ fontSize: 12, color: 'var(--text3)', width: 130, textAlign: 'right' }}>
                {t.assignee ? `→ ${t.assignee}` : (t.requester_name || t.requester_email || '')}</span>}
              <span style={{ fontSize: 12, color: 'var(--text3)', width: 86, textAlign: 'right' }}>{String(t.created_at).slice(0, 10)}</span>
            </div>
          ))}
      </div>
    </div>
  )
}
