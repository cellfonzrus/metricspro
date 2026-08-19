'use client'
// Internal Chat — Phase 1 (core messaging). Owner directive 2026-08-19. A Slack/WhatsApp-style two-pane
// messenger: conversations on the left, the active thread on the right. Phase 1 polls for new messages
// (~4s); Supabase Realtime broadcast + rich features (reactions, threads, attachments, presence) arrive
// in later phases. See docs/APPROVALS_AND_CHAT_PLAN.md.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/lib/client'

interface Channel {
  id: string; kind: string; name?: string | null; topic?: string | null; members: string[]
  unread: number; muted?: boolean
  last_message?: { preview: string; at: string; sender_name?: string | null } | null
}
interface Message {
  id: string; sender_employee_id?: string | null; sender_name?: string | null; body?: string
  kind: string; created_at: string
}
interface Person { employee_id: string; name: string }

export default function ChatPage() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [activeId, setActiveId] = useState<string>('')
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [dir, setDir] = useState<Person[]>([])
  const [showNew, setShowNew] = useState<'' | 'dm' | 'channel'>('')
  const [dirQuery, setDirQuery] = useState('')
  const [msg, setMsg] = useState('')
  const paneRef = useRef<HTMLDivElement>(null)

  const nameOf = useMemo(() => {
    const m: Record<string, string> = {}; dir.forEach(p => { m[p.employee_id] = p.name }); return m
  }, [dir])

  const title = useCallback((c: Channel) => {
    if (c.kind === 'channel') return `# ${c.name || 'channel'}`
    const others = (c.members || []).filter(id => nameOf[id])   // self is filtered out of the directory
    const names = (c.members || []).map(id => nameOf[id]).filter(Boolean)
    return names.length ? names.join(', ') : (others.length ? others.join(', ') : 'Direct message')
  }, [nameOf])

  const loadChannels = useCallback(() => {
    api('/api/v1/chat/channels').then((r: any) => setChannels(r.channels || [])).catch(() => {})
  }, [])
  const loadMessages = useCallback((cid: string) => {
    if (!cid) return
    api(`/api/v1/chat/channels/${cid}/messages?limit=100`).then((r: any) => setMessages(r.messages || [])).catch(() => {})
  }, [])

  useEffect(() => { api('/api/v1/chat/directory').then((r: any) => setDir(r.people || [])).catch(() => {}) }, [])
  useEffect(() => { loadChannels() }, [loadChannels])
  // Poll: channels for unread/recency, and the open thread for new messages.
  useEffect(() => {
    const t = setInterval(() => { loadChannels(); if (activeId) loadMessages(activeId) }, 4000)
    return () => clearInterval(t)
  }, [activeId, loadChannels, loadMessages])
  useEffect(() => { if (activeId) { loadMessages(activeId); api(`/api/v1/chat/channels/${activeId}/read`, { method: 'POST' }).then(loadChannels).catch(() => {}) } }, [activeId, loadMessages, loadChannels])
  useEffect(() => { if (paneRef.current) paneRef.current.scrollTop = paneRef.current.scrollHeight }, [messages])

  async function send() {
    const body = draft.trim(); if (!body || !activeId) return
    setDraft('')
    try {
      await api(`/api/v1/chat/channels/${activeId}/messages`, { method: 'POST', body: JSON.stringify({ body }) })
      loadMessages(activeId); loadChannels()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)); setDraft(body) }
  }
  async function startDm(p: Person) {
    try {
      const r = await api('/api/v1/chat/dm', { method: 'POST', body: JSON.stringify({ employee_id: p.employee_id }) })
      setShowNew(''); setDirQuery(''); await loadChannels(); setActiveId(r.channel.id)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function createChannel() {
    const name = prompt('Channel name:')?.trim(); if (!name) return
    try {
      const r = await api('/api/v1/chat/channels', { method: 'POST', body: JSON.stringify({ name }) })
      setShowNew(''); await loadChannels(); setActiveId(r.channel.id)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const active = channels.find(c => c.id === activeId)
  const dirFiltered = dir.filter(p => !dirQuery || p.name.toLowerCase().includes(dirQuery.toLowerCase()))

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 130px)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
      {/* Sidebar */}
      <div style={{ width: 280, borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', background: 'var(--surface2)' }}>
        <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <b style={{ fontSize: 15 }}>💬 Chat</b>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 8px' }} onClick={() => setShowNew('dm')}>+ DM</button>
            <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 8px' }} onClick={createChannel}>+ Ch</button>
          </div>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {channels.length === 0 && <div style={{ padding: 16, fontSize: 13, color: 'var(--text3)' }}>No conversations yet. Start a DM.</div>}
          {channels.map(c => (
            <div key={c.id} onClick={() => setActiveId(c.id)}
              style={{ padding: '10px 14px', cursor: 'pointer', borderBottom: '1px solid var(--border)',
                background: c.id === activeId ? 'var(--surface)' : 'transparent', display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: c.unread ? 700 : 500, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title(c)}</div>
                {c.last_message && <div style={{ fontSize: 11, color: 'var(--text3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.last_message.preview}</div>}
              </div>
              {c.unread > 0 && <span style={{ background: 'var(--accent)', color: 'white', borderRadius: 999, fontSize: 11, fontWeight: 700, padding: '1px 7px', height: 'fit-content' }}>{c.unread}</span>}
            </div>
          ))}
        </div>
      </div>

      {/* Thread */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: 'var(--surface)' }}>
        {!active ? (
          <div style={{ margin: 'auto', color: 'var(--text3)', fontSize: 14 }}>Select a conversation, or start a new one.</div>
        ) : (
          <>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 700, fontSize: 14 }}>{title(active)}</div>
            <div ref={paneRef} style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
              {messages.map(m => (
                <div key={m.id} style={{ maxWidth: '75%' }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 2 }}>{m.sender_name || m.sender_employee_id} · {(m.created_at || '').slice(11, 16)}</div>
                  <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 10, padding: '8px 12px', fontSize: 14, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.body}</div>
                </div>
              ))}
              {messages.length === 0 && <div style={{ color: 'var(--text3)', fontSize: 13, margin: 'auto' }}>No messages yet — say hello.</div>}
            </div>
            <div style={{ padding: 12, borderTop: '1px solid var(--border)', display: 'flex', gap: 8 }}>
              <input value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                placeholder="Message…" style={{ flex: 1, padding: '9px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 14, outline: 'none' }} />
              <button className="btn btn-primary" onClick={send} disabled={!draft.trim()}>Send</button>
            </div>
          </>
        )}
        {msg && <div style={{ padding: '6px 16px', fontSize: 12, color: '#dc2626' }}>{msg}</div>}
      </div>

      {/* New DM picker */}
      {showNew === 'dm' && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowNew('')}>
          <div onClick={e => e.stopPropagation()} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 380, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: 14, borderBottom: '1px solid var(--border)', fontWeight: 700 }}>New direct message</div>
            <input autoFocus value={dirQuery} onChange={e => setDirQuery(e.target.value)} placeholder="Search people…"
              style={{ margin: 12, padding: '8px 10px', borderRadius: 7, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
            <div style={{ overflowY: 'auto', padding: '0 8px 12px' }}>
              {dirFiltered.map(p => (
                <div key={p.employee_id} onClick={() => startDm(p)} style={{ padding: '9px 10px', cursor: 'pointer', borderRadius: 7, fontSize: 14 }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface2)')} onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>{p.name}</div>
              ))}
              {dirFiltered.length === 0 && <div style={{ padding: 12, color: 'var(--text3)', fontSize: 13 }}>No one found.</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
