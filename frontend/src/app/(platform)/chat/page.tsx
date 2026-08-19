'use client'
// Internal Chat. Owner directive 2026-08-19. A Slack/WhatsApp-style two-pane messenger: conversations on
// the left, the active thread on the right. Live updates arrive over Supabase Realtime broadcast (Phase
// 1b) — the backend fans a lightweight HINT out to the caller's user topic on every change, and the
// client re-fetches the authoritative rows through the membership-gated REST API. A slow REST poll stays
// on as a fallback for when the socket is down. Phase 2 adds reactions, threaded replies, file/image
// attachments, edit/delete, and presence + typing over the per-channel topic. See
// docs/APPROVALS_AND_CHAT_PLAN.md.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, apiUpload, supabase } from '@/lib/client'

interface Channel {
  id: string; kind: string; name?: string | null; topic?: string | null; members: string[]
  unread: number; muted?: boolean
  last_message?: { preview: string; at: string; sender_name?: string | null } | null
}
interface Attachment { file_name: string; storage_path: string; mime_type?: string | null; file_size?: number; url?: string | null }
interface Message {
  id: string; sender_employee_id?: string | null; sender_name?: string | null; body?: string | null
  kind: string; created_at: string; edited_at?: string | null; deleted_at?: string | null
  reply_to_id?: string | null; reply_to?: { id: string; sender_name?: string | null; preview?: string | null } | null
  reactions?: Record<string, string[]>; attachments?: Attachment[]
  approval_request_id?: string | null
  approval?: { id: string; title?: string; summary?: string | null; status: string; decision?: string | null; decided_by_name?: string | null; type?: string; priority?: string } | null
}
interface Person { employee_id: string; name: string }
interface Me { employee_id: string; name: string; user_topic: string; is_chat_admin?: boolean }
interface SearchHit { id: string; channel_id: string; sender_name?: string | null; body?: string; created_at: string; channel?: { id: string; kind: string; name?: string | null } | null }
interface BrowseChannel { id: string; name?: string | null; topic?: string | null; member_count: number; joined: boolean }
interface Member { employee_id: string; name: string; role: string }

const QUICK_EMOJI = ['👍', '❤️', '😂', '🎉', '👀', '✅']

// One attachment: an image renders a lazily-signed thumbnail; anything else is a download chip that
// signs a fresh URL on click. Signed URLs expire, so they are minted on demand, never stored.
function AttachmentView({ channelId, att }: { channelId: string; att: Attachment }) {
  const [url, setUrl] = useState<string | null>(null)
  const isImg = (att.mime_type || '').startsWith('image/')
  const sign = useCallback(async () => {
    try {
      const r = await api(`/api/v1/chat/channels/${channelId}/attachments/sign?path=${encodeURIComponent(att.storage_path)}`)
      return r.url as string
    } catch { return null }
  }, [channelId, att.storage_path])
  useEffect(() => { if (isImg) sign().then(setUrl) }, [isImg, sign])
  if (isImg && url) return (
    <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={att.file_name}
      style={{ maxWidth: 240, maxHeight: 200, borderRadius: 8, border: '1px solid var(--border)', display: 'block' }} /></a>
  )
  return (
    <button onClick={async () => { const u = url || await sign(); if (u) window.open(u, '_blank') }}
      style={{ display: 'inline-flex', gap: 6, alignItems: 'center', padding: '6px 10px', borderRadius: 8,
        border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 12, cursor: 'pointer' }}>
      📎 {att.file_name}
    </button>
  )
}

export default function ChatPage() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [activeId, setActiveId] = useState<string>('')
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [dir, setDir] = useState<Person[]>([])
  const [showNew, setShowNew] = useState<'' | 'dm' | 'channel' | 'browse' | 'manage'>('')
  const [dirQuery, setDirQuery] = useState('')
  const [search, setSearch] = useState('')
  const [results, setResults] = useState<SearchHit[]>([])
  const [browse, setBrowse] = useState<BrowseChannel[]>([])
  const [members, setMembers] = useState<Member[]>([])
  const [msg, setMsg] = useState('')
  const [me, setMe] = useState<Me | null>(null)
  const [rtUp, setRtUp] = useState(false)
  const [replyTo, setReplyTo] = useState<Message | null>(null)
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null)
  const [staged, setStaged] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [online, setOnline] = useState<Set<string>>(new Set())
  const [typing, setTyping] = useState<Record<string, number>>({})   // employee_id → name-expiry marker
  const [typingNames, setTypingNames] = useState<Record<string, string>>({})
  const [pickerFor, setPickerFor] = useState('')
  const paneRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const activeIdRef = useRef('')
  const chanRef = useRef<any>(null)
  const lastTypingSent = useRef(0)
  useEffect(() => { activeIdRef.current = activeId }, [activeId])

  const nameOf = useMemo(() => {
    const m: Record<string, string> = {}; dir.forEach(p => { m[p.employee_id] = p.name }); return m
  }, [dir])

  const title = useCallback((c: Channel) => {
    if (c.kind === 'channel') return `# ${c.name || 'channel'}`
    const names = (c.members || []).filter(id => id !== me?.employee_id).map(id => nameOf[id]).filter(Boolean)
    return names.length ? names.join(', ') : 'Direct message'
  }, [nameOf, me])

  const loadChannels = useCallback(() => {
    api('/api/v1/chat/channels').then((r: any) => setChannels(r.channels || [])).catch(() => {})
  }, [])
  const loadMessages = useCallback((cid: string) => {
    if (!cid) return
    api(`/api/v1/chat/channels/${cid}/messages?limit=100`).then((r: any) => setMessages(r.messages || [])).catch(() => {})
  }, [])

  useEffect(() => { api('/api/v1/chat/directory').then((r: any) => setDir(r.people || [])).catch(() => {}) }, [])
  useEffect(() => { loadChannels() }, [loadChannels])
  useEffect(() => { api('/api/v1/chat/me').then((r: any) => setMe(r)).catch(() => {}) }, [])

  // Realtime (sidebar + thread hints): the caller's user topic receives a hint for every conversation
  // they belong to, so one subscription keeps the whole app live. The socket state drives poll cadence.
  useEffect(() => {
    if (!me?.user_topic) return
    const ch = supabase.channel(me.user_topic, { config: { broadcast: { self: true } } })
    ch.on('broadcast', { event: 'chat' }, (m: any) => {
      const cid = m?.payload?.channel_id
      loadChannels()
      if (cid && cid === activeIdRef.current) loadMessages(cid)
    }).subscribe((status: string) => { setRtUp(status === 'SUBSCRIBED') })
    return () => { setRtUp(false); supabase.removeChannel(ch) }
  }, [me?.user_topic, loadChannels, loadMessages])

  // Per-channel topic for the OPEN thread: presence (who's here) + typing indicators, both client↔client
  // over the same broadcast channel. Also carries message hints for the fastest possible thread refresh.
  useEffect(() => {
    setOnline(new Set()); setTyping({})
    if (!activeId || !me) { chanRef.current = null; return }
    const ch = supabase.channel(`chat:${activeId}`, { config: { presence: { key: me.employee_id } } })
    ch.on('presence', { event: 'sync' }, () => {
      const state: any = ch.presenceState()
      setOnline(new Set(Object.keys(state || {})))
    })
    ch.on('broadcast', { event: 'typing' }, (m: any) => {
      const eid = m?.payload?.employee_id; if (!eid || eid === me.employee_id) return
      setTypingNames(p => ({ ...p, [eid]: m?.payload?.name || eid }))
      setTyping(p => ({ ...p, [eid]: Date.now() + 4000 }))
    })
    ch.on('broadcast', { event: 'chat' }, (m: any) => {
      if (m?.payload?.channel_id === activeIdRef.current) loadMessages(activeIdRef.current)
    })
    ch.subscribe((status: string) => { if (status === 'SUBSCRIBED') ch.track({ employee_id: me.employee_id, name: me.name }) })
    chanRef.current = ch
    return () => { chanRef.current = null; supabase.removeChannel(ch) }
  }, [activeId, me, loadMessages])

  // Expire stale typing markers.
  useEffect(() => {
    const t = setInterval(() => setTyping(p => {
      const now = Date.now(); const next: Record<string, number> = {}; let changed = false
      for (const k of Object.keys(p)) { if (p[k] > now) next[k] = p[k]; else changed = true }
      return changed ? next : p
    }), 1500)
    return () => clearInterval(t)
  }, [])

  // Message search (debounced). Membership-scoped server-side, so results never leak a foreign channel.
  useEffect(() => {
    const term = search.trim()
    if (term.length < 2) { setResults([]); return }
    const t = setTimeout(() => {
      api(`/api/v1/chat/search?q=${encodeURIComponent(term)}`).then((r: any) => setResults(r.results || [])).catch(() => {})
    }, 300)
    return () => clearTimeout(t)
  }, [search])

  // Poll fallback: fast (4s) while the socket is down; a slow 20s safety sweep once realtime is up.
  useEffect(() => {
    const t = setInterval(() => { loadChannels(); if (activeIdRef.current) loadMessages(activeIdRef.current) },
                          rtUp ? 20000 : 4000)
    return () => clearInterval(t)
  }, [rtUp, loadChannels, loadMessages])
  useEffect(() => {
    if (activeId) { loadMessages(activeId); setReplyTo(null); setEditing(null); setStaged([])
      api(`/api/v1/chat/channels/${activeId}/read`, { method: 'POST' }).then(loadChannels).catch(() => {}) }
  }, [activeId, loadMessages, loadChannels])
  useEffect(() => { if (paneRef.current) paneRef.current.scrollTop = paneRef.current.scrollHeight }, [messages])

  function emitTyping() {
    const ch = chanRef.current; if (!ch || !me) return
    const now = Date.now(); if (now - lastTypingSent.current < 1500) return   // throttle
    lastTypingSent.current = now
    try { ch.send({ type: 'broadcast', event: 'typing', payload: { employee_id: me.employee_id, name: me.name } }) } catch { /* best effort */ }
  }

  async function send() {
    const body = draft.trim()
    if ((!body && staged.length === 0) || !activeId) return
    const atts = staged
    setDraft(''); setStaged([]); const rt = replyTo?.id; setReplyTo(null)
    try {
      await api(`/api/v1/chat/channels/${activeId}/messages`,
        { method: 'POST', body: JSON.stringify({ body, reply_to_id: rt || undefined, attachments: atts }) })
      loadMessages(activeId); loadChannels()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)); setDraft(body); setStaged(atts) }
  }
  async function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files || []); e.target.value = ''
    if (!files.length || !activeId) return
    setUploading(true)
    try {
      for (const f of files) {
        const form = new FormData(); form.append('file', f)
        const r = await apiUpload(`/api/v1/chat/channels/${activeId}/attachments`, form)
        if (r?.attachment) setStaged(s => [...s, r.attachment])
      }
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setUploading(false) }
  }
  async function toggleReaction(m: Message, emoji: string) {
    setPickerFor('')
    try { await api(`/api/v1/chat/channels/${activeId}/messages/${m.id}/reactions`, { method: 'POST', body: JSON.stringify({ emoji }) }); loadMessages(activeId) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function saveEdit() {
    if (!editing) return
    const body = editing.body.trim(); if (!body) return
    try { await api(`/api/v1/chat/channels/${activeId}/messages/${editing.id}`, { method: 'PATCH', body: JSON.stringify({ body }) }); setEditing(null); loadMessages(activeId) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function del(m: Message) {
    if (!confirm('Delete this message?')) return
    try { await api(`/api/v1/chat/channels/${activeId}/messages/${m.id}`, { method: 'DELETE' }); loadMessages(activeId) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function decideApproval(m: Message, decision: 'approve' | 'deny') {
    try { await api(`/api/v1/chat/channels/${activeId}/messages/${m.id}/decision`, { method: 'POST', body: JSON.stringify({ decision }) }); loadMessages(activeId) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function requestApproval() {
    const title = prompt('What needs approval?')?.trim(); if (!title || !activeId) return
    const summary = prompt('Add a note (optional):')?.trim() || undefined
    try { await api(`/api/v1/chat/channels/${activeId}/approvals`, { method: 'POST', body: JSON.stringify({ title, summary }) }); loadMessages(activeId); loadChannels() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function openBrowse() {
    try { const r = await api('/api/v1/chat/channels/browse'); setBrowse(r.channels || []); setShowNew('browse') }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function joinChannel(id: string) {
    try { await api(`/api/v1/chat/channels/${id}/join`, { method: 'POST' }); setShowNew(''); await loadChannels(); setActiveId(id) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  const loadMembers = useCallback((cid: string) => {
    api(`/api/v1/chat/channels/${cid}/members`).then((r: any) => setMembers(r.members || [])).catch(() => {})
  }, [])
  async function openManage() { if (!activeId) return; loadMembers(activeId); setShowNew('manage') }
  async function addMember(p: Person) {
    try { await api(`/api/v1/chat/channels/${activeId}/members`, { method: 'POST', body: JSON.stringify({ employee_id: p.employee_id }) }); setDirQuery(''); loadMembers(activeId); loadChannels() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function removeMember(eid: string) {
    try { await api(`/api/v1/chat/channels/${activeId}/members/${eid}`, { method: 'DELETE' }); loadMembers(activeId); loadChannels() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function renameChannel() {
    const name = prompt('New channel name:', active?.name || '')?.trim(); if (!name) return
    try { await api(`/api/v1/chat/channels/${activeId}`, { method: 'PATCH', body: JSON.stringify({ name }) }); loadChannels() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function leaveActive() {
    if (!activeId || !confirm('Leave this conversation?')) return
    try { await api(`/api/v1/chat/channels/${activeId}/leave`, { method: 'POST' }); setShowNew(''); setActiveId(''); loadChannels() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function runRetention() {
    const days = prompt('Delete messages older than how many days? (whole org)')?.trim(); if (!days) return
    try { const r = await api('/api/v1/chat/admin/retention', { method: 'POST', body: JSON.stringify({ days: Number(days) }) }); setMsg(`🧹 Removed ${r.deleted} message(s).`); loadMessages(activeId) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
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
  const canManage = members.some(m => m.employee_id === me?.employee_id && m.role === 'owner') || !!me?.is_chat_admin
  const dirFiltered = dir.filter(p => !dirQuery || p.name.toLowerCase().includes(dirQuery.toLowerCase()))
  const typers = Object.keys(typing).map(eid => typingNames[eid] || nameOf[eid] || eid)

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 130px)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
      {/* Sidebar */}
      <div style={{ width: 280, borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', background: 'var(--surface2)' }}>
        <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <b style={{ fontSize: 15 }}>💬 Chat
            <span title={rtUp ? 'Live' : 'Reconnecting…'} style={{ display: 'inline-block', width: 7, height: 7, borderRadius: 999, marginLeft: 7, verticalAlign: 'middle', background: rtUp ? '#16a34a' : 'var(--text3)' }} /></b>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 8px' }} onClick={() => setShowNew('dm')}>+ DM</button>
            <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 8px' }} onClick={createChannel}>+ Ch</button>
            <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 8px' }} title="Browse channels" onClick={openBrowse}>🔎</button>
          </div>
        </div>
        <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search messages…"
            style={{ width: '100%', padding: '6px 10px', borderRadius: 7, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13, outline: 'none' }} />
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {search.trim().length >= 2 ? (
            <div>
              {results.length === 0 && <div style={{ padding: 16, fontSize: 13, color: 'var(--text3)' }}>No matches.</div>}
              {results.map(r => (
                <div key={r.id} onClick={() => { setActiveId(r.channel_id); setSearch('') }}
                  style={{ padding: '9px 14px', cursor: 'pointer', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>{r.channel?.name ? `# ${r.channel.name}` : 'Direct message'} · {r.sender_name} · {(r.created_at || '').slice(0, 10)}</div>
                  <div style={{ fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.body}</div>
                </div>
              ))}
            </div>
          ) : (<>
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
          </>)}
        </div>
      </div>

      {/* Thread */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: 'var(--surface)', minWidth: 0 }}>
        {!active ? (
          <div style={{ margin: 'auto', color: 'var(--text3)', fontSize: 14 }}>Select a conversation, or start a new one.</div>
        ) : (
          <>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
              <span style={{ fontWeight: 700, fontSize: 14 }}>{title(active)}</span>
              <span style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <span style={{ fontSize: 11, color: 'var(--text3)' }}>{online.size > 0 ? `🟢 ${online.size} here` : ''}</span>
                <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 8px' }} title="Members & settings" onClick={openManage}>👥</button>
              </span>
            </div>
            <div ref={paneRef} style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
              {messages.map(m => {
                const mine = m.sender_employee_id === me?.employee_id
                const reactions = m.reactions || {}
                return (
                  <div key={m.id} style={{ maxWidth: '78%', position: 'relative' }}
                    onMouseLeave={() => setPickerFor(f => f === m.id ? '' : f)}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 2, display: 'flex', gap: 8, alignItems: 'center' }}>
                      <span>{m.sender_name || m.sender_employee_id} · {(m.created_at || '').slice(11, 16)}</span>
                      {m.edited_at && !m.deleted_at && <span style={{ fontStyle: 'italic' }}>(edited)</span>}
                      {!m.deleted_at && (
                        <span style={{ display: 'inline-flex', gap: 6 }}>
                          <button title="React" onClick={() => setPickerFor(pickerFor === m.id ? '' : m.id)} style={actBtn}>😀</button>
                          <button title="Reply" onClick={() => { setReplyTo(m); setEditing(null) }} style={actBtn}>↩︎</button>
                          {mine && <button title="Edit" onClick={() => setEditing({ id: m.id, body: m.body || '' })} style={actBtn}>✏️</button>}
                          {mine && <button title="Delete" onClick={() => del(m)} style={actBtn}>🗑️</button>}
                        </span>
                      )}
                    </div>
                    {m.reply_to && (
                      <div style={{ borderLeft: '3px solid var(--border)', paddingLeft: 8, marginBottom: 3, fontSize: 11, color: 'var(--text3)' }}>
                        <b>{m.reply_to.sender_name}</b>: {m.reply_to.preview || '(deleted)'}
                      </div>
                    )}
                    {editing?.id === m.id ? (
                      <div style={{ display: 'flex', gap: 6 }}>
                        <input autoFocus value={editing.body} onChange={e => setEditing({ id: m.id, body: e.target.value })}
                          onKeyDown={e => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') setEditing(null) }}
                          style={{ flex: 1, padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 14 }} />
                        <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={saveEdit}>Save</button>
                        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setEditing(null)}>Cancel</button>
                      </div>
                    ) : m.kind === 'approval' && m.approval ? (
                      <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 12px', background: 'var(--surface2)' }}>
                        <div style={{ fontSize: 10, letterSpacing: 0.4, color: 'var(--accent)', fontWeight: 700, marginBottom: 3 }}>
                          ✅ APPROVAL REQUEST{m.approval.priority && m.approval.priority !== 'normal' ? ` · ${m.approval.priority.toUpperCase()}` : ''}
                        </div>
                        <div style={{ fontSize: 14, fontWeight: 600 }}>{m.approval.title}</div>
                        {m.approval.summary && <div style={{ fontSize: 13, color: 'var(--text3)', marginTop: 2 }}>{m.approval.summary}</div>}
                        <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
                          {m.approval.status === 'pending' ? (
                            <>
                              <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 12px' }} onClick={() => decideApproval(m, 'approve')}>Approve</button>
                              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 12px' }} onClick={() => decideApproval(m, 'deny')}>Deny</button>
                            </>
                          ) : (
                            <span style={{ fontSize: 12, fontWeight: 700, color: m.approval.status === 'approved' ? '#16a34a' : m.approval.status === 'denied' ? '#dc2626' : 'var(--text3)' }}>
                              {m.approval.status === 'approved' ? '✔ Approved' : m.approval.status === 'denied' ? '✕ Denied' : m.approval.status}
                              {m.approval.decided_by_name ? ` · ${m.approval.decided_by_name}` : ''}
                            </span>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div style={{ background: m.deleted_at ? 'transparent' : 'var(--surface2)', border: m.deleted_at ? '1px dashed var(--border)' : '1px solid var(--border)', borderRadius: 10, padding: '8px 12px', fontSize: 14, whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: m.deleted_at ? 'var(--text3)' : 'var(--text)', fontStyle: m.deleted_at ? 'italic' : 'normal' }}>
                        {m.deleted_at ? 'This message was deleted' : m.body}
                        {!m.deleted_at && (m.attachments || []).length > 0 && (
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: m.body ? 8 : 0 }}>
                            {(m.attachments || []).map((a, i) => <AttachmentView key={i} channelId={activeId} att={a} />)}
                          </div>
                        )}
                      </div>
                    )}
                    {Object.keys(reactions).length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                        {Object.entries(reactions).map(([emoji, ids]) => (
                          <button key={emoji} onClick={() => toggleReaction(m, emoji)}
                            style={{ fontSize: 12, padding: '1px 7px', borderRadius: 999, cursor: 'pointer',
                              border: '1px solid ' + (ids.includes(me?.employee_id || '') ? 'var(--accent)' : 'var(--border)'),
                              background: ids.includes(me?.employee_id || '') ? 'var(--surface2)' : 'var(--surface)', color: 'var(--text)' }}>
                            {emoji} {ids.length}
                          </button>
                        ))}
                      </div>
                    )}
                    {pickerFor === m.id && (
                      <div style={{ position: 'absolute', zIndex: 30, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 999, padding: '4px 8px', display: 'flex', gap: 4, boxShadow: '0 4px 14px rgba(0,0,0,0.18)' }}>
                        {QUICK_EMOJI.map(e => <span key={e} onClick={() => toggleReaction(m, e)} style={{ cursor: 'pointer', fontSize: 17 }}>{e}</span>)}
                      </div>
                    )}
                  </div>
                )
              })}
              {messages.length === 0 && <div style={{ color: 'var(--text3)', fontSize: 13, margin: 'auto' }}>No messages yet — say hello.</div>}
            </div>

            {typers.length > 0 && <div style={{ padding: '2px 16px', fontSize: 11, color: 'var(--text3)', fontStyle: 'italic' }}>{typers.join(', ')} {typers.length === 1 ? 'is' : 'are'} typing…</div>}

            {replyTo && (
              <div style={{ padding: '6px 16px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text3)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--surface2)' }}>
                <span>Replying to <b>{replyTo.sender_name}</b>: {(replyTo.body || '').slice(0, 60)}</span>
                <button onClick={() => setReplyTo(null)} style={actBtn}>✕</button>
              </div>
            )}
            {staged.length > 0 && (
              <div style={{ padding: '6px 16px', borderTop: '1px solid var(--border)', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {staged.map((a, i) => (
                  <span key={i} style={{ fontSize: 12, padding: '3px 8px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface2)', display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                    📎 {a.file_name}
                    <span onClick={() => setStaged(s => s.filter((_, j) => j !== i))} style={{ cursor: 'pointer' }}>✕</span>
                  </span>
                ))}
              </div>
            )}
            <div style={{ padding: 12, borderTop: '1px solid var(--border)', display: 'flex', gap: 8, alignItems: 'center' }}>
              <input ref={fileRef} type="file" multiple style={{ display: 'none' }} onChange={onPickFiles} />
              <button className="btn btn-secondary" title="Attach" disabled={uploading} onClick={() => fileRef.current?.click()} style={{ padding: '8px 10px' }}>{uploading ? '…' : '📎'}</button>
              <button className="btn btn-secondary" title="Request approval" onClick={requestApproval} style={{ padding: '8px 10px' }}>✅</button>
              <input value={draft} onChange={e => { setDraft(e.target.value); emitTyping() }} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
                placeholder="Message…" style={{ flex: 1, padding: '9px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 14, outline: 'none' }} />
              <button className="btn btn-primary" onClick={send} disabled={!draft.trim() && staged.length === 0}>Send</button>
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

      {/* Browse public channels */}
      {showNew === 'browse' && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowNew('')}>
          <div onClick={e => e.stopPropagation()} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 420, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: 14, borderBottom: '1px solid var(--border)', fontWeight: 700 }}>Browse channels</div>
            <div style={{ overflowY: 'auto', padding: 8 }}>
              {browse.length === 0 && <div style={{ padding: 12, color: 'var(--text3)', fontSize: 13 }}>No public channels yet.</div>}
              {browse.map(c => (
                <div key={c.id} style={{ padding: '9px 10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 600 }}># {c.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>{c.member_count} member{c.member_count === 1 ? '' : 's'}{c.topic ? ` · ${c.topic}` : ''}</div>
                  </div>
                  {c.joined
                    ? <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setShowNew(''); setActiveId(c.id) }}>Open</button>
                    : <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={() => joinChannel(c.id)}>Join</button>}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Manage members & settings */}
      {showNew === 'manage' && active && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setShowNew('')}>
          <div onClick={e => e.stopPropagation()} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 440, maxHeight: '80vh', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: 14, borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b>{title(active)}</b>
              <div style={{ display: 'flex', gap: 6 }}>
                {active.kind === 'channel' && (canManage) && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={renameChannel}>Rename</button>}
                <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={leaveActive}>Leave</button>
              </div>
            </div>
            <div style={{ overflowY: 'auto', padding: 8 }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', padding: '4px 8px', fontWeight: 700 }}>MEMBERS ({members.length})</div>
              {members.map(m => (
                <div key={m.employee_id} style={{ padding: '7px 8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: 14 }}>{m.name}{m.role === 'owner' ? ' · owner' : ''}{m.employee_id === me?.employee_id ? ' · you' : ''}</span>
                  {canManage && m.employee_id !== me?.employee_id && <button onClick={() => removeMember(m.employee_id)} style={actBtn} title="Remove">✕</button>}
                </div>
              ))}
              {active.kind !== 'dm' && (
                <>
                  <div style={{ fontSize: 11, color: 'var(--text3)', padding: '8px 8px 4px', fontWeight: 700 }}>ADD PEOPLE</div>
                  <input value={dirQuery} onChange={e => setDirQuery(e.target.value)} placeholder="Search people…"
                    style={{ width: '100%', boxSizing: 'border-box', margin: '0 0 6px', padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
                  {dirQuery && dir.filter(p => p.name.toLowerCase().includes(dirQuery.toLowerCase()) && !members.some(m => m.employee_id === p.employee_id)).slice(0, 8).map(p => (
                    <div key={p.employee_id} onClick={() => addMember(p)} style={{ padding: '7px 8px', cursor: 'pointer', fontSize: 14 }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface2)')} onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>+ {p.name}</div>
                  ))}
                </>
              )}
              {me?.is_chat_admin && (
                <div style={{ borderTop: '1px solid var(--border)', marginTop: 8, paddingTop: 8 }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)', padding: '0 8px 4px', fontWeight: 700 }}>ADMIN</div>
                  <button className="btn btn-secondary" style={{ fontSize: 12, margin: '0 8px' }} onClick={runRetention}>🧹 Run retention sweep…</button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const actBtn: React.CSSProperties = { background: 'transparent', border: 'none', cursor: 'pointer', fontSize: 12, padding: 0, lineHeight: 1 }
