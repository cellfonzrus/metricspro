'use client'
import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { api, ORG_ID } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import EntityPicker, { EntityOption } from '@/components/EntityPicker'

export default function NewTicket() {
  const { user } = useAuth()
  const router = useRouter()
  const [cfg, setCfg] = useState<any>(null)
  const [subject, setSubject] = useState('')
  const [description, setDescription] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [priorityId, setPriorityId] = useState('')
  const [storeCode, setStoreCode] = useState('')
  // Store roster — "pick, don't type" (RULE THREE §3b). id===store_code (the byte-identical string the
  // ticket already stores); label adds the address for recognition. allowCreate stays false.
  const [stores, setStores] = useState<EntityOption[]>([])
  const [cf, setCf] = useState<Record<string, any>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api(`/api/v1/helpdesk/config/bootstrap?org_id=${ORG_ID}`).then((d: any) => {
      setCfg(d)
      const normal = (d.priorities || []).find((p: any) => p.key === 'normal') || (d.priorities || [])[0]
      if (normal) setPriorityId(normal.id)
    }).catch(e => setErr(e?.message || 'Failed to load form'))
  }, [])

  // Prefill from the "?" help panel's "Contact support" deep-link (?subject=&page=) — page context so
  // support knows where the user was. Read from location (no useSearchParams → no Suspense concern).
  useEffect(() => {
    try {
      const sp = new URLSearchParams(window.location.search)
      const s = sp.get('subject'); const pg = sp.get('page')
      if (s) setSubject(prev => prev || s)
      if (pg) setDescription(prev => prev || `Page: ${pg}\n\n`)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    apiCached(`/api/v1/storeops/stores`, LOOKUP).then((rows: any) => {
      const opts = ((rows || []) as any[])
        .map(s => ({ id: String(s.store_code || '').trim(),
          label: `${s.store_code}${s.address ? ' — ' + String(s.address).substring(0, 32) : ''}` }))
        .filter(o => o.id)
        .sort((a, b) => a.id.localeCompare(b.id)) as EntityOption[]
      setStores(opts)
    }).catch(() => {})
  }, [])

  async function submit() {
    if (!subject.trim() || !description.trim()) { setErr('Subject and description are required.'); return }
    setBusy(true); setErr('')
    try {
      const t = await api(`/api/v1/helpdesk/tickets?org_id=${ORG_ID}`, {
        method: 'POST',
        body: JSON.stringify({
          subject, description, category_id: categoryId || null, priority_id: priorityId || null,
          store_code: storeCode || null, custom_fields: cf,
          requester_id: user?.id || null, requester_name: user?.full_name || user?.email || null,
          requester_email: user?.email || null,
        }),
      })
      router.push(`/helpdesk/${t.id}`)
    } catch (e: any) { setErr(e?.message || 'Could not create ticket'); setBusy(false) }
  }

  if (!cfg) return <div style={{ padding: 24, color: 'var(--text3)' }}>{err || 'Loading…'}</div>
  const lbl = { fontSize: 13, fontWeight: 600, marginBottom: 4, display: 'block' as const }

  return (
    <div style={{ padding: 24, maxWidth: 640 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>➕ Raise a ticket</h1>
      <p className="pg-note" style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>Describe the issue; a manager will pick it up.</p>
      {err && <div className="card" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 12, marginBottom: 12 }}>{err}</div>}

      <div className="card" style={{ padding: 16, display: 'grid', gap: 14 }}>
        <div><label style={lbl}>Subject *</label>
          <input className="input" style={{ width: '100%' }} value={subject} onChange={e => setSubject(e.target.value)} placeholder="Short summary" /></div>
        <div><label style={lbl}>Description *</label>
          <textarea className="input" style={{ width: '100%', minHeight: 120 }} value={description} onChange={e => setDescription(e.target.value)} placeholder="What happened? Steps, error messages, etc." /></div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 180 }}><label style={lbl}>Category</label>
            <select className="input" style={{ width: '100%' }} value={categoryId} onChange={e => setCategoryId(e.target.value)}>
              <option value="">—</option>
              {(cfg.categories || []).map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select></div>
          <div style={{ flex: 1, minWidth: 180 }}><label style={lbl}>Priority</label>
            <select className="input" style={{ width: '100%' }} value={priorityId} onChange={e => setPriorityId(e.target.value)}>
              {(cfg.priorities || []).map((p: any) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select></div>
        </div>
        <div><label style={lbl}>Store (optional)</label>
          <EntityPicker options={stores} value={storeCode || null} onChange={v => setStoreCode(v || '')}
            placeholder="Search store…" ariaLabel="Store" width="100%" /></div>

        {(cfg.custom_fields || []).filter((f: any) => f.is_active).map((f: any) => (
          <div key={f.id}>
            <label style={lbl}>{f.label}{f.is_required ? ' *' : ''}</label>
            {f.field_type === 'textarea'
              ? <textarea className="input" style={{ width: '100%', minHeight: 70 }} value={cf[f.field_key] || ''} onChange={e => setCf(v => ({ ...v, [f.field_key]: e.target.value }))} />
              : f.field_type === 'select'
              ? <select className="input" style={{ width: '100%' }} value={cf[f.field_key] || ''} onChange={e => setCf(v => ({ ...v, [f.field_key]: e.target.value }))}>
                  <option value="">—</option>{(f.options || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
                </select>
              : f.field_type === 'checkbox'
              ? <input type="checkbox" checked={!!cf[f.field_key]} onChange={e => setCf(v => ({ ...v, [f.field_key]: e.target.checked }))} />
              : <input className="input" style={{ width: '100%' }} type={f.field_type === 'number' ? 'number' : f.field_type === 'date' ? 'date' : 'text'}
                  value={cf[f.field_key] || ''} onChange={e => setCf(v => ({ ...v, [f.field_key]: e.target.value }))} />}
          </div>
        ))}

        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary" disabled={busy} onClick={submit}>{busy ? 'Submitting…' : 'Submit ticket'}</button>
          <button className="btn" disabled={busy} onClick={() => router.push('/helpdesk')}>Cancel</button>
        </div>
      </div>
    </div>
  )
}
