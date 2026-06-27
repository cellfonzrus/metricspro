'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, ORG_ID } from '@/lib/client'

// Employee-facing helpdesk INSIDE the kiosk portal (no platform chrome). Reps raise + track their own
// tickets; internal notes are hidden (every call is agent=false, so the backend filters them out).
const enc = encodeURIComponent
const card: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 16 }
const inp: React.CSSProperties = { width: '100%', marginTop: 4, padding: '10px 12px', borderRadius: 9, border: '1px solid #cbd5e1', fontSize: 15, boxSizing: 'border-box' }
const lbl: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: '#475569' }

function Badge({ label, color }: { label?: string; color?: string }) {
  if (!label) return null
  return <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, whiteSpace: 'nowrap',
    background: (color || '#888') + '22', color: color || '#555', border: `1px solid ${(color || '#888')}55` }}>{label}</span>
}

export default function PortalHelpdesk({ email, name, empId, onOpenCount }:
  { email: string; name: string; empId: string; onOpenCount?: (n: number) => void }) {
  const [view, setView] = useState<'list' | 'new' | 'detail'>('list')
  const [tickets, setTickets] = useState<any[]>([])
  const [cfg, setCfg] = useState<any>(null)
  const [detail, setDetail] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  // new-ticket form
  const [subject, setSubject] = useState('')
  const [description, setDescription] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [priorityId, setPriorityId] = useState('')
  const [cf, setCf] = useState<Record<string, any>>({})
  const [reply, setReply] = useState('')
  const [busy, setBusy] = useState(false)

  const loadList = useCallback(() => {
    setLoading(true); setErr('')
    api(`/api/v1/helpdesk/tickets?org_id=${ORG_ID}&agent=false&requester=${enc(email)}`)
      .then((d: any) => {
        const list = Array.isArray(d) ? d : []
        setTickets(list)
        onOpenCount?.(list.filter((t: any) => t.status?.stage !== 'done').length)
      })
      .catch(e => setErr(e?.message || 'Could not load tickets'))
      .finally(() => setLoading(false))
  }, [email, onOpenCount])
  useEffect(() => { loadList() }, [loadList])
  useEffect(() => {
    api(`/api/v1/helpdesk/config/bootstrap?org_id=${ORG_ID}`).then((d: any) => {
      setCfg(d)
      const n = (d.priorities || []).find((p: any) => p.key === 'normal') || (d.priorities || [])[0]
      if (n) setPriorityId(n.id)
    }).catch(() => {})
  }, [])

  async function openTicket(id: string) {
    setBusy(true); setErr('')
    try { setDetail(await api(`/api/v1/helpdesk/tickets/${id}?org_id=${ORG_ID}&agent=false`)); setView('detail') }
    catch (e: any) { setErr(e?.message || 'Could not open ticket') } finally { setBusy(false) }
  }

  async function submitNew() {
    if (!subject.trim() || !description.trim()) { setErr('Subject and description are required.'); return }
    setBusy(true); setErr('')
    try {
      await api(`/api/v1/helpdesk/tickets?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify({
        subject, description, category_id: categoryId || null, priority_id: priorityId || null,
        custom_fields: cf, requester_id: empId || null, requester_name: name || email, requester_email: email,
      }) })
      setSubject(''); setDescription(''); setCf({}); setCategoryId('')
      setView('list'); loadList()
    } catch (e: any) { setErr(e?.message || 'Could not create ticket') } finally { setBusy(false) }
  }

  async function sendReply() {
    if (!reply.trim() || !detail) return
    setBusy(true)
    try {
      await api(`/api/v1/helpdesk/tickets/${detail.ticket.id}/comments?org_id=${ORG_ID}&agent=false`, {
        method: 'POST', body: JSON.stringify({ body: reply, author: email, author_name: name || email }) })
      setReply(''); await openTicket(detail.ticket.id)
    } catch (e: any) { setErr(e?.message || 'Could not send reply') } finally { setBusy(false) }
  }

  // ── detail ──
  if (view === 'detail' && detail) {
    const t = detail.ticket
    return (
      <div style={card}>
        <button onClick={() => { setView('list'); setDetail(null) }} style={{ background: 'none', border: 'none', color: '#2563eb', fontSize: 14, cursor: 'pointer', padding: 0 }}>← My tickets</button>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', margin: '8px 0 4px' }}>
          <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text3)' }}>{t.display_number}</span>
          <Badge label={t.status?.label} color={t.status?.color} />
          <Badge label={t.priority?.label} color={t.priority?.color} />
        </div>
        <div style={{ fontSize: 17, fontWeight: 700 }}>{t.subject}</div>
        <div style={{ fontSize: 14, color: 'var(--text2)', whiteSpace: 'pre-wrap', margin: '8px 0 14px' }}>{t.description}</div>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>Conversation</div>
        {(detail.comments || []).length === 0 && <div style={{ fontSize: 13, color: 'var(--text3)' }}>No replies yet — we’ll update you here.</div>}
        {(detail.comments || []).map((c: any) => (
          <div key={c.id} style={{ background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 9, padding: 10, marginBottom: 6 }}>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{c.author_name || c.author || '—'} · {String(c.created_at).slice(0, 16).replace('T', ' ')}</div>
            <div style={{ fontSize: 14, whiteSpace: 'pre-wrap' }}>{c.body}</div>
          </div>
        ))}
        <textarea style={{ ...inp, minHeight: 64 }} value={reply} onChange={e => setReply(e.target.value)} placeholder="Add a reply…" />
        {err && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 6 }}>{err}</div>}
        <button disabled={busy} onClick={sendReply} style={{ width: '100%', marginTop: 8, padding: 12, borderRadius: 10, border: 'none', background: '#1E3A5F', color: '#fff', fontWeight: 700, fontSize: 15, cursor: 'pointer', opacity: busy ? 0.7 : 1 }}>Send reply</button>
      </div>
    )
  }

  // ── new ──
  if (view === 'new') {
    return (
      <div style={card}>
        <button onClick={() => setView('list')} style={{ background: 'none', border: 'none', color: '#2563eb', fontSize: 14, cursor: 'pointer', padding: 0 }}>← Cancel</button>
        <div style={{ fontSize: 18, fontWeight: 700, margin: '6px 0 12px' }}>Raise a ticket</div>
        <div style={{ display: 'grid', gap: 12 }}>
          <div><label style={lbl}>Subject *</label><input style={inp} value={subject} onChange={e => setSubject(e.target.value)} placeholder="Short summary" /></div>
          <div><label style={lbl}>Description *</label><textarea style={{ ...inp, minHeight: 100 }} value={description} onChange={e => setDescription(e.target.value)} placeholder="What’s the issue?" /></div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 130 }}><label style={lbl}>Category</label>
              <select style={inp} value={categoryId} onChange={e => setCategoryId(e.target.value)}>
                <option value="">—</option>{(cfg?.categories || []).map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select></div>
            <div style={{ flex: 1, minWidth: 130 }}><label style={lbl}>Priority</label>
              <select style={inp} value={priorityId} onChange={e => setPriorityId(e.target.value)}>
                {(cfg?.priorities || []).map((p: any) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select></div>
          </div>
          {(cfg?.custom_fields || []).filter((f: any) => f.is_active).map((f: any) => (
            <div key={f.id}>
              <label style={lbl}>{f.label}{f.is_required ? ' *' : ''}</label>
              {f.field_type === 'textarea'
                ? <textarea style={{ ...inp, minHeight: 60 }} value={cf[f.field_key] || ''} onChange={e => setCf(v => ({ ...v, [f.field_key]: e.target.value }))} />
                : f.field_type === 'select'
                ? <select style={inp} value={cf[f.field_key] || ''} onChange={e => setCf(v => ({ ...v, [f.field_key]: e.target.value }))}>
                    <option value="">—</option>{(f.options || []).map((o: string) => <option key={o} value={o}>{o}</option>)}</select>
                : f.field_type === 'checkbox'
                ? <div><input type="checkbox" checked={!!cf[f.field_key]} onChange={e => setCf(v => ({ ...v, [f.field_key]: e.target.checked }))} /></div>
                : <input style={inp} type={f.field_type === 'number' ? 'number' : f.field_type === 'date' ? 'date' : 'text'} value={cf[f.field_key] || ''} onChange={e => setCf(v => ({ ...v, [f.field_key]: e.target.value }))} />}
            </div>
          ))}
          {err && <div style={{ color: '#dc2626', fontSize: 13 }}>{err}</div>}
          <button disabled={busy} onClick={submitNew} style={{ padding: 14, borderRadius: 10, border: 'none', background: '#f5a623', color: '#1E3A5F', fontWeight: 700, fontSize: 16, cursor: 'pointer', opacity: busy ? 0.7 : 1 }}>{busy ? 'Submitting…' : 'Submit ticket'}</button>
        </div>
      </div>
    )
  }

  // ── list ──
  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ fontSize: 18, fontWeight: 700 }}>🎫 My Tickets</div>
        <button onClick={() => { setErr(''); setView('new') }} style={{ padding: '9px 14px', borderRadius: 9, border: 'none', background: '#1E3A5F', color: '#fff', fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>➕ Raise a ticket</button>
      </div>
      {err && <div style={{ color: '#dc2626', fontSize: 13, marginBottom: 8 }}>{err}</div>}
      {loading ? <div style={{ color: 'var(--text3)', padding: 20, textAlign: 'center' }}>Loading…</div>
        : tickets.length === 0 ? <div style={{ color: 'var(--text3)', fontSize: 14, padding: '14px 0' }}>No tickets yet. Have an issue? Tap <b>Raise a ticket</b>.</div>
        : tickets.map(t => (
          <div key={t.id} onClick={() => openTicket(t.id)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '11px 4px', borderTop: '1px solid var(--border)', cursor: 'pointer', flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600, flex: 1, minWidth: 160 }}>{t.subject}</span>
            <Badge label={t.priority?.label} color={t.priority?.color} />
            <Badge label={t.status?.label} color={t.status?.color} />
            <span style={{ fontSize: 12, color: 'var(--text3)', width: 70, textAlign: 'right' }}>{String(t.created_at).slice(0, 10)}</span>
          </div>
        ))}
    </div>
  )
}
