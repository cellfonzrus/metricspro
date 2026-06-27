'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, ORG_ID } from '@/lib/client'

const base = `/api/v1/helpdesk/config`
const q = `org_id=${ORG_ID}`
type Tab = 'categories' | 'priorities' | 'statuses' | 'custom-fields' | 'teams' | 'settings'

export default function HelpdeskSettings() {
  const [tab, setTab] = useState<Tab>('categories')
  return (
    <div style={{ padding: 24, maxWidth: 860 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⚙️ Helpdesk Settings</h1>
        <span style={{ flex: 1 }} /><Link href="/helpdesk" className="btn">🎫 Inbox</Link>
      </div>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>Configure this organization’s helpdesk. Changes are data, not code — they apply immediately.</p>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
        {(['categories', 'priorities', 'statuses', 'custom-fields', 'teams', 'settings'] as Tab[]).map(t => (
          <button key={t} className={`btn btn-sm ${tab === t ? 'btn-primary' : ''}`} onClick={() => setTab(t)} style={{ textTransform: 'capitalize' }}>{t.replace('-', ' ')}</button>
        ))}
      </div>
      {tab === 'categories' && <Categories />}
      {tab === 'priorities' && <Priorities />}
      {tab === 'statuses' && <Statuses />}
      {tab === 'custom-fields' && <CustomFields />}
      {tab === 'teams' && <Teams />}
      {tab === 'settings' && <Settings />}
    </div>
  )
}

function useList(path: string) {
  const [rows, setRows] = useState<any[]>([])
  const [msg, setMsg] = useState('')
  const load = useCallback(() => { api(`${base}/${path}?${q}`).then(setRows).catch(e => setMsg(e?.message || 'load failed')) }, [path])
  useEffect(() => { load() }, [load])
  const create = async (body: any) => { try { await api(`${base}/${path}?${q}`, { method: 'POST', body: JSON.stringify(body) }); load() } catch (e: any) { setMsg(e?.message) } }
  const update = async (id: string, body: any) => { try { await api(`${base}/${path}/${id}?${q}`, { method: 'PATCH', body: JSON.stringify(body) }); load() } catch (e: any) { setMsg(e?.message) } }
  const remove = async (id: string) => { if (!confirm('Delete this?')) return; try { await api(`${base}/${path}/${id}?${q}`, { method: 'DELETE' }); load() } catch (e: any) { setMsg(e?.message) } }
  return { rows, msg, create, update, remove }
}

const inp = { padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }
const Row = ({ children }: any) => <div style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '7px 0', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>{children}</div>
const Card = ({ children }: any) => <div className="card" style={{ padding: 16 }}>{children}</div>

function Categories() {
  const { rows, msg, create, update, remove } = useList('categories')
  const [name, setName] = useState('')
  return <Card>{msg && <div style={{ color: '#c0392b', fontSize: 12 }}>{msg}</div>}
    <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>Set <b>alert emails</b> per category to route new-ticket emails (e.g. IT → IT lead, HR/Payroll → HR). Leave blank to use the shared list on the <b>settings</b> tab.</div>
    {rows.map(r => <Row key={r.id}>
      <input style={{ ...inp, width: 150 }} defaultValue={r.name} onBlur={e => e.target.value !== r.name && update(r.id, { name: e.target.value })} />
      <input style={{ ...inp, flex: 1, minWidth: 180 }} defaultValue={(r.notify_emails || []).join(', ')} placeholder="alert emails (optional)"
        onBlur={e => { const v = e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean); update(r.id, { notify_emails: v }) }} />
      <label style={{ fontSize: 12 }}><input type="checkbox" checked={r.is_active} onChange={e => update(r.id, { is_active: e.target.checked })} /> active</label>
      <button className="btn btn-sm" style={{ color: '#c0392b' }} onClick={() => remove(r.id)}>Delete</button></Row>)}
    <Row><input style={{ ...inp, flex: 1 }} placeholder="New category name" value={name} onChange={e => setName(e.target.value)} />
      <button className="btn btn-sm btn-primary" onClick={() => { if (name.trim()) { create({ name: name.trim(), sort_order: rows.length * 10 }); setName('') } }}>+ Add</button></Row>
  </Card>
}

function Priorities() {
  const { rows, msg, create, update, remove } = useList('priorities')
  const [n, setN] = useState({ key: '', label: '', color: '#3b82f6' })
  return <Card>{msg && <div style={{ color: '#c0392b', fontSize: 12 }}>{msg}</div>}
    {rows.map(r => <Row key={r.id}>
      <span style={{ fontFamily: 'monospace', fontSize: 12, width: 80, color: 'var(--text3)' }}>{r.key}</span>
      <input style={{ ...inp, flex: 1 }} defaultValue={r.label} onBlur={e => e.target.value !== r.label && update(r.id, { label: e.target.value })} />
      <input type="color" value={r.color || '#888888'} onChange={e => update(r.id, { color: e.target.value })} />
      <button className="btn btn-sm" style={{ color: '#c0392b' }} onClick={() => remove(r.id)}>Delete</button></Row>)}
    <Row><input style={{ ...inp, width: 110 }} placeholder="key" value={n.key} onChange={e => setN(v => ({ ...v, key: e.target.value }))} />
      <input style={{ ...inp, flex: 1 }} placeholder="label" value={n.label} onChange={e => setN(v => ({ ...v, label: e.target.value }))} />
      <input type="color" value={n.color} onChange={e => setN(v => ({ ...v, color: e.target.value }))} />
      <button className="btn btn-sm btn-primary" onClick={() => { if (n.key && n.label) { create({ ...n, key: n.key.toLowerCase().replace(/\s+/g, '_'), sort_order: rows.length * 10 }); setN({ key: '', label: '', color: '#3b82f6' }) } }}>+ Add</button></Row>
  </Card>
}

function Statuses() {
  const { rows, msg, create, update, remove } = useList('statuses')
  const [n, setN] = useState({ key: '', label: '', stage: 'open', color: '#3b82f6' })
  const stages = ['open', 'pending', 'done']
  return <Card>{msg && <div style={{ color: '#c0392b', fontSize: 12 }}>{msg}</div>}
    <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>Each status maps to a fixed lifecycle <b>stage</b> (open / pending / done) so dashboards keep working even when you rename labels.</div>
    {rows.map(r => <Row key={r.id}>
      <span style={{ fontFamily: 'monospace', fontSize: 12, width: 90, color: 'var(--text3)' }}>{r.key}</span>
      <input style={{ ...inp, flex: 1 }} defaultValue={r.label} onBlur={e => e.target.value !== r.label && update(r.id, { label: e.target.value })} />
      <select style={inp} value={r.stage} onChange={e => update(r.id, { stage: e.target.value })}>{stages.map(s => <option key={s}>{s}</option>)}</select>
      <input type="color" value={r.color || '#888888'} onChange={e => update(r.id, { color: e.target.value })} />
      <button className="btn btn-sm" style={{ color: '#c0392b' }} onClick={() => remove(r.id)}>Delete</button></Row>)}
    <Row><input style={{ ...inp, width: 110 }} placeholder="key" value={n.key} onChange={e => setN(v => ({ ...v, key: e.target.value }))} />
      <input style={{ ...inp, flex: 1 }} placeholder="label" value={n.label} onChange={e => setN(v => ({ ...v, label: e.target.value }))} />
      <select style={inp} value={n.stage} onChange={e => setN(v => ({ ...v, stage: e.target.value }))}>{stages.map(s => <option key={s}>{s}</option>)}</select>
      <button className="btn btn-sm btn-primary" onClick={() => { if (n.key && n.label) { create({ ...n, key: n.key.toLowerCase().replace(/\s+/g, '_'), sort_order: rows.length * 10 }); setN({ key: '', label: '', stage: 'open', color: '#3b82f6' }) } }}>+ Add</button></Row>
  </Card>
}

function CustomFields() {
  const { rows, msg, create, update, remove } = useList('custom-fields')
  const types = ['text', 'textarea', 'number', 'date', 'select', 'multiselect', 'checkbox']
  const [n, setN] = useState({ field_key: '', label: '', field_type: 'text', options: '', is_required: false })
  return <Card>{msg && <div style={{ color: '#c0392b', fontSize: 12 }}>{msg}</div>}
    <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>Extra fields shown on the new-ticket form. For <b>select</b>, list options comma-separated.</div>
    {rows.map(r => <Row key={r.id}>
      <span style={{ fontFamily: 'monospace', fontSize: 12, width: 110, color: 'var(--text3)' }}>{r.field_key}</span>
      <input style={{ ...inp, flex: 1 }} defaultValue={r.label} onBlur={e => e.target.value !== r.label && update(r.id, { label: e.target.value })} />
      <span style={{ fontSize: 12, color: 'var(--text3)' }}>{r.field_type}</span>
      <label style={{ fontSize: 12 }}><input type="checkbox" checked={r.is_required} onChange={e => update(r.id, { is_required: e.target.checked })} /> required</label>
      <button className="btn btn-sm" style={{ color: '#c0392b' }} onClick={() => remove(r.id)}>Delete</button></Row>)}
    <Row><input style={{ ...inp, width: 120 }} placeholder="key" value={n.field_key} onChange={e => setN(v => ({ ...v, field_key: e.target.value }))} />
      <input style={{ ...inp, flex: 1 }} placeholder="label" value={n.label} onChange={e => setN(v => ({ ...v, label: e.target.value }))} />
      <select style={inp} value={n.field_type} onChange={e => setN(v => ({ ...v, field_type: e.target.value }))}>{types.map(t => <option key={t}>{t}</option>)}</select>
      {(n.field_type === 'select' || n.field_type === 'multiselect') && <input style={{ ...inp, width: 150 }} placeholder="a, b, c" value={n.options} onChange={e => setN(v => ({ ...v, options: e.target.value }))} />}
      <label style={{ fontSize: 12 }}><input type="checkbox" checked={n.is_required} onChange={e => setN(v => ({ ...v, is_required: e.target.checked }))} /> req</label>
      <button className="btn btn-sm btn-primary" onClick={() => {
        if (!n.field_key || !n.label) return
        const opts = (n.field_type === 'select' || n.field_type === 'multiselect') ? n.options.split(',').map(s => s.trim()).filter(Boolean) : null
        create({ field_key: n.field_key.toLowerCase().replace(/\s+/g, '_'), label: n.label, field_type: n.field_type, options: opts, is_required: n.is_required, sort_order: rows.length * 10 })
        setN({ field_key: '', label: '', field_type: 'text', options: '', is_required: false })
      }}>+ Add</button></Row>
  </Card>
}

function Teams() {
  const { rows, msg, create, remove } = useList('teams')
  const [name, setName] = useState('')
  const [mem, setMem] = useState<Record<string, string>>({})
  const addMember = async (tid: string) => { const m = (mem[tid] || '').trim(); if (!m) return; await api(`${base}/teams/${tid}/members?${q}`, { method: 'POST', body: JSON.stringify({ member: m }) }); setMem(v => ({ ...v, [tid]: '' })); location.reload() }
  return <Card>{msg && <div style={{ color: '#c0392b', fontSize: 12 }}>{msg}</div>}
    {rows.map(r => <div key={r.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <b style={{ flex: 1 }}>{r.name}</b>
        <button className="btn btn-sm" style={{ color: '#c0392b' }} onClick={() => remove(r.id)}>Delete</button></div>
      <div style={{ fontSize: 12, color: 'var(--text3)', margin: '4px 0' }}>{(r.members || []).map((m: any) => m.member || m).join(', ') || 'no members'}</div>
      <div style={{ display: 'flex', gap: 6 }}><input style={{ ...inp, width: 180 }} placeholder="add member (name/email)" value={mem[r.id] || ''} onChange={e => setMem(v => ({ ...v, [r.id]: e.target.value }))} />
        <button className="btn btn-sm" onClick={() => addMember(r.id)}>+ Member</button></div>
    </div>)}
    <Row><input style={{ ...inp, flex: 1 }} placeholder="New team name" value={name} onChange={e => setName(e.target.value)} />
      <button className="btn btn-sm btn-primary" onClick={() => { if (name.trim()) { create({ name: name.trim() }); setName('') } }}>+ Add</button></Row>
  </Card>
}

function Settings() {
  const [s, setS] = useState<any>(null)
  const [emails, setEmails] = useState('')
  const [msg, setMsg] = useState('')
  useEffect(() => { api(`${base}/settings?${q}`).then((d: any) => { setS(d); setEmails((d.notify_emails || []).join(', ')) }).catch(e => setMsg(e?.message)) }, [])
  const save = async () => {
    try { await api(`${base}/settings?${q}`, { method: 'PUT', body: JSON.stringify({ notify_emails: emails.split(',').map(e => e.trim()).filter(Boolean), default_assignee: s?.default_assignee || null }) }); setMsg('Saved ✓') }
    catch (e: any) { setMsg(e?.message) }
  }
  if (!s) return <Card>{msg || 'Loading…'}</Card>
  return <Card>
    <div style={{ fontWeight: 700, marginBottom: 8 }}>Notifications</div>
    <label style={{ fontSize: 13, fontWeight: 600 }}>Email these people when a ticket is raised</label>
    <input style={{ ...inp, width: '100%', marginTop: 4 }} value={emails} onChange={e => setEmails(e.target.value)} placeholder="manager@company.com, ops@company.com" />
    <div style={{ fontSize: 12, color: 'var(--text3)', margin: '4px 0 12px' }}>Comma-separated. Uses the existing Resend email channel.</div>
    <label style={{ fontSize: 13, fontWeight: 600 }}>Default assignee (optional)</label>
    <input style={{ ...inp, width: 240, marginTop: 4 }} defaultValue={s.default_assignee || ''} onChange={e => setS((v: any) => ({ ...v, default_assignee: e.target.value }))} placeholder="name / email" />
    <div style={{ marginTop: 14 }}><button className="btn btn-primary" onClick={save}>💾 Save</button> <span style={{ marginLeft: 10, fontSize: 13, color: '#15803d' }}>{msg}</span></div>
  </Card>
}
