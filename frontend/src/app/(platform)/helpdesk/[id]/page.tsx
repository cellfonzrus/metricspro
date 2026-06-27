'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { api, apiUpload, ORG_ID } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

const enc = encodeURIComponent
function Badge({ label, color }: { label?: string; color?: string }) {
  if (!label) return null
  return <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10,
    background: (color || '#888') + '22', color: color || '#555', border: `1px solid ${(color || '#888')}55` }}>{label}</span>
}

export default function TicketDetail() {
  const { id } = useParams<{ id: string }>()
  const { user, permissions } = useAuth()
  const isAgent = (permissions?.scope || 'all') !== 'self'
  const actor = user?.full_name || user?.email || ''

  const [data, setData] = useState<any>(null)
  const [cfg, setCfg] = useState<any>(null)
  const [err, setErr] = useState('')
  const [comment, setComment] = useState('')
  const [internal, setInternal] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { setData(await api(`/api/v1/helpdesk/tickets/${id}?org_id=${ORG_ID}&agent=${isAgent}`)) }
    catch (e: any) { setErr(e?.message || 'Not found') }
  }, [id, isAgent])
  useEffect(() => { load() }, [load])
  useEffect(() => { api(`/api/v1/helpdesk/config/bootstrap?org_id=${ORG_ID}`).then(setCfg).catch(() => {}) }, [])

  async function patch(body: any) {
    setBusy(true)
    try { await api(`/api/v1/helpdesk/tickets/${id}?org_id=${ORG_ID}&actor=${enc(actor)}`, { method: 'PATCH', body: JSON.stringify(body) }); await load() }
    catch (e: any) { setErr(e?.message || 'Update failed') } finally { setBusy(false) }
  }
  async function postComment() {
    if (!comment.trim()) return
    setBusy(true)
    try {
      await api(`/api/v1/helpdesk/tickets/${id}/comments?org_id=${ORG_ID}&agent=${isAgent}`, {
        method: 'POST', body: JSON.stringify({ body: comment, is_internal: internal, author: user?.email, author_name: actor }) })
      setComment(''); setInternal(false); await load()
    } catch (e: any) { setErr(e?.message || 'Comment failed') } finally { setBusy(false) }
  }
  async function uploadFile(file: File) {
    setBusy(true)
    try { const f = new FormData(); f.append('file', file)
      await apiUpload(`/api/v1/helpdesk/tickets/${id}/attachments?org_id=${ORG_ID}&uploader=${enc(actor)}`, f); await load()
    } catch (e: any) { setErr(e?.message || 'Upload failed') } finally { setBusy(false) }
  }
  async function openAttachment(aid: string) {
    try { const r = await api(`/api/v1/helpdesk/tickets/${id}/attachments/${aid}/url?org_id=${ORG_ID}`); if (r?.url) window.open(r.url, '_blank') }
    catch { setErr('Could not open attachment') }
  }

  if (err && !data) return <div style={{ padding: 24, color: '#c0392b' }}>{err} · <Link href="/helpdesk" style={{ color: '#2563eb' }}>Back</Link></div>
  if (!data) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading…</div>
  const t = data.ticket
  const cf = t.custom_fields || {}
  const sel = { padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }

  return (
    <div style={{ padding: 24, maxWidth: 860 }}>
      <Link href="/helpdesk" style={{ color: '#2563eb', fontSize: 13 }}>← Helpdesk</Link>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', margin: '8px 0 2px' }}>
        <span style={{ fontFamily: 'monospace', color: 'var(--text3)' }}>{t.display_number}</span>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>{t.subject}</h1>
        <Badge label={t.status?.label} color={t.status?.color} />
        <Badge label={t.priority?.label} color={t.priority?.color} />
      </div>
      <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>
        Raised by {t.requester_name || t.requester_email || '—'} · {String(t.created_at).slice(0, 16).replace('T', ' ')}
        {t.store_code ? ` · ${t.store_code}` : ''}{t.category?.name ? ` · ${t.category.name}` : ''}
      </div>
      {err && <div className="card" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 10, marginBottom: 12 }}>{err}</div>}

      {isAgent && cfg && (
        <div className="card" style={{ padding: 12, display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
          <label style={{ fontSize: 12, color: 'var(--text3)' }}>Status
            <select style={{ ...sel, marginLeft: 6 }} disabled={busy} value={t.status_id || ''} onChange={e => patch({ status_id: e.target.value })}>
              {(cfg.statuses || []).map((s: any) => <option key={s.id} value={s.id}>{s.label}</option>)}</select></label>
          <label style={{ fontSize: 12, color: 'var(--text3)' }}>Priority
            <select style={{ ...sel, marginLeft: 6 }} disabled={busy} value={t.priority_id || ''} onChange={e => patch({ priority_id: e.target.value })}>
              <option value="">—</option>{(cfg.priorities || []).map((p: any) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
          <label style={{ fontSize: 12, color: 'var(--text3)' }}>Category
            <select style={{ ...sel, marginLeft: 6 }} disabled={busy} value={t.category_id || ''} onChange={e => patch({ category_id: e.target.value })}>
              <option value="">—</option>{(cfg.categories || []).map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          {(cfg.teams || []).length > 0 && <label style={{ fontSize: 12, color: 'var(--text3)' }}>Team
            <select style={{ ...sel, marginLeft: 6 }} disabled={busy} value={t.team_id || ''} onChange={e => patch({ team_id: e.target.value })}>
              <option value="">—</option>{(cfg.teams || []).map((tm: any) => <option key={tm.id} value={tm.id}>{tm.name}</option>)}</select></label>}
          <label style={{ fontSize: 12, color: 'var(--text3)' }}>Assignee
            <input style={{ ...sel, marginLeft: 6, width: 140 }} disabled={busy} defaultValue={t.assignee || ''}
              onBlur={e => { if (e.target.value !== (t.assignee || '')) patch({ assignee: e.target.value || null }) }} placeholder="name / email" /></label>
        </div>
      )}

      <div className="card" style={{ padding: 14, marginBottom: 12, whiteSpace: 'pre-wrap', fontSize: 14 }}>{t.description}</div>

      {Object.keys(cf).length > 0 && (
        <div className="card" style={{ padding: 14, marginBottom: 12 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Details</div>
          {Object.entries(cf).map(([k, v]) => <div key={k} style={{ fontSize: 13 }}><b>{k}:</b> {String(v)}</div>)}
        </div>
      )}

      <div className="card" style={{ padding: 14, marginBottom: 12 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Conversation</div>
        {(data.comments || []).length === 0 && <div style={{ color: 'var(--text3)', fontSize: 13 }}>No replies yet.</div>}
        {(data.comments || []).map((c: any) => (
          <div key={c.id} style={{ padding: '8px 10px', borderRadius: 8, marginBottom: 6,
            background: c.is_internal ? '#fffbeb' : 'var(--bg2)', border: c.is_internal ? '1px solid #fde68a' : '1px solid var(--border)' }}>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 2 }}>
              {c.author_name || c.author || '—'} · {String(c.created_at).slice(0, 16).replace('T', ' ')}
              {c.is_internal && <span style={{ color: '#b45309', marginLeft: 6 }}>internal note</span>}</div>
            <div style={{ fontSize: 14, whiteSpace: 'pre-wrap' }}>{c.body}</div>
          </div>
        ))}
        <textarea className="input" style={{ width: '100%', minHeight: 64, marginTop: 8 }} value={comment} onChange={e => setComment(e.target.value)} placeholder="Write a reply…" />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 6, flexWrap: 'wrap' }}>
          <button className="btn btn-primary btn-sm" disabled={busy} onClick={postComment}>Reply</button>
          {isAgent && <label style={{ fontSize: 12, color: 'var(--text3)' }}>
            <input type="checkbox" checked={internal} onChange={e => setInternal(e.target.checked)} /> Internal note (hidden from requester)</label>}
          <span style={{ flex: 1 }} />
          <label className="btn btn-sm" style={{ cursor: 'pointer' }}>📎 Attach
            <input type="file" hidden disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) uploadFile(f); e.currentTarget.value = '' }} /></label>
        </div>
        {(data.attachments || []).length > 0 && (
          <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {(data.attachments || []).map((a: any) => (
              <button key={a.id} className="btn btn-sm" onClick={() => openAttachment(a.id)}>📄 {a.file_name}</button>))}
          </div>
        )}
      </div>

      {isAgent && (data.events || []).length > 0 && (
        <details className="card" style={{ padding: 14 }}>
          <summary style={{ cursor: 'pointer', fontWeight: 700, fontSize: 13 }}>Activity ({data.events.length})</summary>
          <div style={{ marginTop: 8 }}>
            {(data.events || []).map((e: any) => (
              <div key={e.id} style={{ fontSize: 12, color: 'var(--text3)', padding: '2px 0' }}>
                {String(e.created_at).slice(0, 16).replace('T', ' ')} · {e.actor || '—'} · {e.event_type}
                {e.detail?.to ? ` → ${e.detail.to}` : ''}</div>))}
          </div>
        </details>
      )}
    </div>
  )
}
