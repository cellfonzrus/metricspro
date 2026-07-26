'use client'
// HR Letters — Approval Queue. Every letter whose template is 'approval' mode (or that failed to
// send) lands here for HR to review, optionally edit, then approve (sends now) or reject. Tier-3/5
// disciplinary letters default to this mode — see the Template Library page.
import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/client'

const btn: React.CSSProperties = { padding: '6px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text1)', cursor: 'pointer', fontSize: 13 }
const primaryBtn: React.CSSProperties = { ...btn, background: 'var(--accent)', color: '#fff', border: 'none' }
const dangerBtn: React.CSSProperties = { ...btn, color: '#c0392b', borderColor: '#c0392b' }
const box: React.CSSProperties = { padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text1)', width: '100%' }

export default function LettersQueuePage() {
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [editing, setEditing] = useState<any>(null)
  const [draft, setDraft] = useState<{ subject: string; body: string }>({ subject: '', body: '' })
  const [busy, setBusy] = useState<string>('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try { setRows((await api('/api/v1/hr/letters/queue')).queue || []) }
    catch (e: any) { setErr(e?.message || 'Failed to load queue') }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  function openRow(r: any) { setEditing(r); setDraft({ subject: r.subject || '', body: r.body || '' }); setMsg(''); setErr('') }

  async function approve(r: any, useDraft: boolean) {
    setBusy(r.id); setErr(''); setMsg('')
    try {
      await api(`/api/v1/hr/letters/queue/${r.id}/approve`, {
        method: 'POST',
        body: JSON.stringify(useDraft ? { subject: draft.subject, body: draft.body } : {}),
      })
      setMsg(`Sent to ${r.employee_name || r.employee_id}.`)
      setEditing(null); await load()
    } catch (e: any) { setErr(e?.message || 'Approve failed') }
    setBusy('')
  }

  async function reject(r: any) {
    const reason = window.prompt('Reason for rejecting this letter (optional):') || ''
    setBusy(r.id); setErr(''); setMsg('')
    try {
      await api(`/api/v1/hr/letters/queue/${r.id}/reject`, { method: 'POST', body: JSON.stringify({ reason }) })
      setMsg('Letter rejected.')
      await load()
    } catch (e: any) { setErr(e?.message || 'Reject failed') }
    setBusy('')
  }

  return (
    <div style={{ padding: 20, maxWidth: 900 }}>
      <h2 style={{ margin: '0 0 4px' }}>📥 HR Letters — Approval Queue</h2>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginTop: 0 }}>
        Letters queued for HR sign-off (approval-mode templates) or that failed to send on a retry.
      </p>
      {err && <div style={{ color: '#c0392b', fontSize: 13, margin: '8px 0' }}>{err}</div>}
      {msg && <div style={{ color: '#1e8e3e', fontSize: 13, margin: '8px 0' }}>{msg}</div>}
      {loading && <div style={{ fontSize: 13 }}>Loading…</div>}
      {!loading && rows.length === 0 && <div style={{ fontSize: 13, color: 'var(--text2)' }}>Nothing waiting for approval.</div>}

      {rows.map(r => (
        <div key={r.id} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 10, background: 'var(--surface)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
            <div>
              <b>{r.employee_name || r.employee_id}</b> — {r.subject}
              {r.status === 'failed' && <span style={{ color: '#c0392b', marginLeft: 8, fontSize: 12 }}>⚠️ previously failed: {r.send_error}</span>}
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                {r.category}{r.escalation_tier ? ` · tier ${r.escalation_tier}` : ''} · {r.trigger === 'auto' ? 'auto-detected' : 'manual'} ·
                {' '}{new Date(r.created_at).toLocaleString()}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={btn} onClick={() => openRow(r)}>View / Edit</button>
              <button style={primaryBtn} disabled={busy === r.id} onClick={() => approve(r, false)}>Approve & send</button>
              <button style={dangerBtn} disabled={busy === r.id} onClick={() => reject(r)}>Reject</button>
            </div>
          </div>
        </div>
      ))}

      {editing && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3000 }}
          onMouseDown={e => { if (e.target === e.currentTarget) setEditing(null) }}>
          <div style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 640, maxHeight: '85vh', overflowY: 'auto' }}>
            <h3 style={{ marginTop: 0 }}>{editing.employee_name || editing.employee_id}</h3>
            <label style={{ fontSize: 12, fontWeight: 600 }}>Subject</label>
            <input style={{ ...box, marginBottom: 10 }} value={draft.subject} onChange={e => setDraft({ ...draft, subject: e.target.value })} />
            <label style={{ fontSize: 12, fontWeight: 600 }}>Body</label>
            <textarea style={{ ...box, minHeight: 220, marginBottom: 14, fontFamily: 'inherit' }} value={draft.body}
              onChange={e => setDraft({ ...draft, body: e.target.value })} />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button style={btn} onClick={() => setEditing(null)}>Cancel</button>
              <button style={dangerBtn} disabled={busy === editing.id} onClick={() => reject(editing)}>Reject</button>
              <button style={primaryBtn} disabled={busy === editing.id} onClick={() => approve(editing, true)}>Approve & send (with edits)</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
