'use client'
// Support case view (mig 715) — timeline (internal notes distinct from user-visible replies), a reply
// box with a canned-response PICKER (pick-don't-type §3b), status/priority/assign controls, and a right
// rail: the page's support_md playbook + linked failure_log entries + ticket origin (incl. tenant name).
// House-gated server-side; a tenant user never reaches these endpoints.
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { api } from '@/lib/client'

const STATUSES = ['new', 'in_progress', 'waiting_user', 'resolved', 'closed']
const PRIORITIES = ['low', 'normal', 'high', 'urgent']

function Pill({ label, color }: { label?: string | null; color?: string }) {
  if (!label) return null
  return <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, whiteSpace: 'nowrap',
    background: (color || '#888') + '22', color: color || '#555', border: `1px solid ${(color || '#888')}55` }}>{label}</span>
}
const SEV_COLOR: Record<string, string> = { error: '#dc2626', warning: '#d97706', info: '#2563eb' }
const PRIORITY_COLOR: Record<string, string> = { urgent: '#ef4444', high: '#f97316', normal: '#3b82f6', low: '#6b7280' }

export default function SupportCase() {
  const { id } = useParams<{ id: string }>()
  const [data, setData] = useState<any>(null)
  const [doc, setDoc] = useState<any>(null)
  const [canned, setCanned] = useState<any[]>([])
  const [reply, setReply] = useState('')
  const [note, setNote] = useState('')
  const [resolution, setResolution] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try { setData(await api(`/api/v1/helpdesk/support/cases/${id}`)) }
    catch (e: any) { setErr(e?.message || 'Case not found') }
  }, [id])
  useEffect(() => { load() }, [load])
  useEffect(() => { api('/api/v1/helpdesk/support/canned-responses').then((d: any) => setCanned(d.canned || [])).catch(() => {}) }, [])
  // Resolve the page's support playbook once the case (and its page_key) is known.
  useEffect(() => {
    const pk = data?.case?.page_key
    if (!pk) { setDoc(null); return }
    api(`/api/v1/core/support-doc/resolve?path=${encodeURIComponent(pk)}`).then((r: any) => setDoc(r?.found ? r.doc : null)).catch(() => setDoc(null))
  }, [data?.case?.page_key])

  async function post(path: string, body: any, after?: () => void) {
    setBusy(true); setErr('')
    try { await api(`/api/v1/helpdesk/support/cases/${id}/${path}`, { method: 'POST', body: JSON.stringify(body) }); if (after) after(); await load() }
    catch (e: any) { setErr(e?.message || 'Action failed') } finally { setBusy(false) }
  }

  if (err && !data) return <div style={{ padding: 24, color: '#c0392b' }}>{err} · <Link href="/admin/support" style={{ color: '#2563eb' }}>Back</Link></div>
  if (!data) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading…</div>
  const c = data.case; const t = data.ticket
  const sel = { padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }

  return (
    <div style={{ padding: 24 }}>
      <Link href="/admin/support" style={{ color: '#2563eb', fontSize: 13 }}>← Support Console</Link>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', margin: '8px 0 2px' }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>{t?.subject || '(ticket unavailable)'}</h1>
        <Pill label={c.priority} color={PRIORITY_COLOR[c.priority] || '#6b7280'} />
        <Pill label={c.status} color="#475569" />
        <span style={{ fontSize: 12, color: 'var(--accent)', fontWeight: 600 }}>🏢 {c.tenant_name}</span>
      </div>
      {err && <div className="card" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 10, margin: '8px 0' }}>{err}</div>}

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {/* MAIN */}
        <div style={{ flex: 2, minWidth: 340 }}>
          {/* Controls */}
          <div className="card" style={{ padding: 12, display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Status
              <select style={{ ...sel, marginLeft: 6 }} disabled={busy} value={c.status}
                onChange={e => { const v = e.target.value; if (v === 'resolved') return; post('status', { status: v }) }}>
                {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}</select></label>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Priority
              <select style={{ ...sel, marginLeft: 6 }} disabled={busy} value={c.priority}
                onChange={e => post('status', { priority: e.target.value })}>
                {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}</select></label>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Assignee
              <input style={{ ...sel, marginLeft: 6 }} disabled={busy} defaultValue={c.assignee_email || ''} placeholder="email"
                onBlur={e => { if (e.target.value !== (c.assignee_email || '')) post('assign', { assignee_email: e.target.value }) }} /></label>
            {c.sla_due_at && <span style={{ fontSize: 12, color: new Date(c.sla_due_at).getTime() < Date.now() ? '#dc2626' : 'var(--text3)' }}>
              SLA due {String(c.sla_due_at).slice(0, 16).replace('T', ' ')}</span>}
          </div>

          {/* Resolve box */}
          {!['resolved', 'closed'].includes(c.status) && (
            <div className="card" style={{ padding: 12, marginBottom: 12 }}>
              <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Resolve case</div>
              <textarea className="input" style={{ width: '100%', minHeight: 48 }} value={resolution} onChange={e => setResolution(e.target.value)} placeholder="Resolution note (required to resolve)…" />
              <button className="btn btn-sm" disabled={busy || !resolution.trim()} style={{ marginTop: 6 }}
                onClick={() => post('status', { status: 'resolved', resolution }, () => setResolution(''))}>✅ Mark resolved</button>
            </div>
          )}
          {c.resolution && <div className="card" style={{ padding: 10, marginBottom: 12, background: '#f0fdf4', border: '1px solid #bbf7d0' }}>
            <b style={{ fontSize: 12 }}>Resolution:</b> <span style={{ fontSize: 13 }}>{c.resolution}</span></div>}

          {/* Timeline */}
          <div className="card" style={{ padding: 14, marginBottom: 12 }}>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>Timeline</div>
            {(data.events || []).length === 0 && <div style={{ color: 'var(--text3)', fontSize: 13 }}>No activity yet.</div>}
            {(data.events || []).map((e: any) => {
              const isNote = e.kind === 'internal_note'
              const isReply = e.kind === 'reply'
              const bg = isNote ? '#fffbeb' : isReply ? '#eff6ff' : 'var(--bg2)'
              const bd = isNote ? '#fde68a' : isReply ? '#bfdbfe' : 'var(--border)'
              return (
                <div key={e.id} style={{ padding: '8px 10px', borderRadius: 8, marginBottom: 6, background: bg, border: `1px solid ${bd}` }}>
                  <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 2 }}>
                    {e.author_email || 'system'} · {String(e.created_at).slice(0, 16).replace('T', ' ')}
                    {isNote && <span style={{ color: '#b45309', marginLeft: 6 }}>internal note (hidden from user)</span>}
                    {isReply && <span style={{ color: '#1d4ed8', marginLeft: 6 }}>reply → sent to user</span>}
                    {(e.kind === 'status' || e.kind === 'assign') && <span style={{ color: 'var(--text3)', marginLeft: 6 }}>{e.kind}</span>}
                  </div>
                  {e.body && <div style={{ fontSize: 14, whiteSpace: 'pre-wrap' }}>{e.body}</div>}
                </div>
              )
            })}
          </div>

          {/* Reply + canned picker */}
          <div className="card" style={{ padding: 14 }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
              <div style={{ fontWeight: 700 }}>Reply to user</div>
              <span style={{ flex: 1 }} />
              <label style={{ fontSize: 12, color: 'var(--text3)' }}>Canned
                <select style={{ ...sel, marginLeft: 6 }} value="" onChange={e => {
                  const cr = canned.find(x => x.id === e.target.value); if (cr) setReply(r => (r ? r + '\n\n' : '') + cr.body); e.target.value = ''
                }}>
                  <option value="">Insert…</option>
                  {canned.map(cr => <option key={cr.id} value={cr.id}>{cr.title}</option>)}
                </select></label>
            </div>
            <textarea className="input" style={{ width: '100%', minHeight: 70 }} value={reply} onChange={e => setReply(e.target.value)} placeholder="This reply is sent to the tenant user's ticket thread…" />
            <div style={{ display: 'flex', gap: 10, marginTop: 6, flexWrap: 'wrap' }}>
              <button className="btn btn-primary btn-sm" disabled={busy || !reply.trim()} onClick={() => post('reply', { body: reply }, () => setReply(''))}>Send reply</button>
              <span style={{ flex: 1 }} />
            </div>
            <div style={{ borderTop: '1px solid var(--border)', marginTop: 10, paddingTop: 10 }}>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 4 }}>Internal note (support-only, never sent to the user)</div>
              <textarea className="input" style={{ width: '100%', minHeight: 44 }} value={note} onChange={e => setNote(e.target.value)} placeholder="Private note…" />
              <button className="btn btn-sm" disabled={busy || !note.trim()} style={{ marginTop: 6 }} onClick={() => post('note', { body: note }, () => setNote(''))}>Add internal note</button>
            </div>
          </div>
        </div>

        {/* RIGHT RAIL */}
        <div style={{ flex: 1, minWidth: 280 }}>
          <div className="card" style={{ padding: 12, marginBottom: 12 }}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Ticket origin</div>
            <div style={{ fontSize: 13 }}><b>Tenant:</b> {c.tenant_name}</div>
            <div style={{ fontSize: 13 }}><b>Ticket:</b> {t?.display_number || '—'}</div>
            <div style={{ fontSize: 13 }}><b>Requester:</b> {t?.requester_name || t?.requester_email || '—'}</div>
            <div style={{ fontSize: 13 }}><b>From page:</b> {c.page_key || '—'}</div>
            {t?.description && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6, whiteSpace: 'pre-wrap', maxHeight: 160, overflow: 'auto' }}>{t.description}</div>}
          </div>

          <div className="card" style={{ padding: 12, marginBottom: 12 }}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>📖 Support playbook{doc?.title ? ` · ${doc.title}` : ''}</div>
            {doc?.support_md ? <div style={{ fontSize: 12, whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{doc.support_md}</div>
              : <div style={{ fontSize: 12, color: 'var(--text3)' }}>No playbook for this page yet. <Link href="/admin/support/docs" style={{ color: '#2563eb' }}>Add one</Link>.</div>}
            {Array.isArray(doc?.common_issues) && doc.common_issues.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 4 }}>Common issues</div>
                {doc.common_issues.map((ci: any, i: number) => (
                  <div key={i} style={{ fontSize: 12, marginBottom: 6, borderLeft: '2px solid var(--border)', paddingLeft: 8 }}>
                    <div><b>{ci.symptom}</b></div>
                    {ci.diagnosis && <div style={{ color: 'var(--text3)' }}>Dx: {ci.diagnosis}</div>}
                    {ci.fix && <div>Fix: {ci.fix}</div>}
                    {ci.escalate_when && <div style={{ color: '#b45309' }}>Escalate when: {ci.escalate_when}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card" style={{ padding: 12 }}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>🩺 Failure logs (±24h)</div>
            {(data.failures || []).length === 0 ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>None recorded around this ticket.</div>
              : (data.failures || []).map((f: any) => (
                <div key={f.id} style={{ fontSize: 12, marginBottom: 6 }}>
                  <Pill label={f.severity} color={SEV_COLOR[f.severity]} /> <b>{f.category}</b>
                  <div style={{ color: 'var(--text3)' }}>{f.message}</div>
                  {f.remediation && <div style={{ color: '#0369a1' }}>{f.remediation}</div>}
                </div>
              ))}
          </div>
        </div>
      </div>
    </div>
  )
}
