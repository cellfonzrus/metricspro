'use client'
// Outside agencies — the registry, plus what each one is actually sitting on. A lead pushed to an
// agency that never answers reads as "handled" on every internal report, so the unanswered count is
// the first thing on the page.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, input, label, btn, btnPrimary, th, cell, fmtMoney, fmtDate, type Lead, type RefRow } from '@/lib/crm'

interface Agency extends RefRow {
  type: string; contact_name: string | null; email: string | null; phone: string | null
  commission_note: string | null; portal_enabled: boolean
}
const TYPES = ['referral', 'outsourced_sales', 'distributor', 'marketing', 'other']
const emptyForm = { name: '', type: 'referral', contact_name: '', email: '', phone: '', commission_note: '' }

export default function AgenciesPage() {
  const [agencies, setAgencies] = useState<Agency[]>([])
  const [leads, setLeads] = useState<Lead[]>([])
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState({ ...emptyForm })
  const [editing, setEditing] = useState<string>('')
  const [showForm, setShowForm] = useState(false)
  const [canEdit, setCanEdit] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setMsg('')
    try {
      const [a, l, cfg] = await Promise.all([
        api('/api/v1/crm/lists/agencies?include_inactive=true'),
        api('/api/v1/crm/leads?status=open&limit=1000'),
        api('/api/v1/crm/config'),
      ])
      setAgencies(a || []); setLeads((l?.rows || []).filter((x: Lead) => x.agency_id))
      setCanEdit(!!cfg?.can_edit)
    } catch (e: any) { setMsg(e?.message || String(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  async function save() {
    setBusy(true); setMsg('')
    try {
      if (editing) await api(`/api/v1/crm/lists/agencies/${editing}`, { method: 'PUT', body: JSON.stringify(form) })
      else await api('/api/v1/crm/lists/agencies', { method: 'POST', body: JSON.stringify(form) })
      setForm({ ...emptyForm }); setEditing(''); setShowForm(false)
      await load()
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setBusy(false)
  }

  const statsFor = (id: string) => {
    const mine = leads.filter(l => l.agency_id === id)
    return {
      total: mine.length,
      pending: mine.filter(l => !l.agency_accepted_at).length,
      value: mine.reduce((s, l) => s + Number(l.value_estimate || 0), 0),
    }
  }

  return (
    <div style={{ padding: 20, maxWidth: 1200 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🤝 Outside Agencies</h1>
        <div style={{ flex: 1 }} />
        {canEdit && (
          <button style={btnPrimary} onClick={() => { setShowForm(!showForm); setEditing(''); setForm({ ...emptyForm }) }}>
            {showForm ? 'Close' : '➕ Add an agency'}
          </button>
        )}
      </div>
      {!canEdit && <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
        You can see agencies and their workload; adding or editing one needs the CRM settings permission.
      </div>}

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 12 }}>{msg}</div>}

      {showForm && canEdit && (
        <div style={{ ...panel, display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 12, marginBottom: 14 }}>
          <div><span style={label}>Name *</span><input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} style={input} /></div>
          <div><span style={label}>Type</span>
            <select value={form.type} onChange={e => setForm({ ...form, type: e.target.value })} style={input}>
              {TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
            </select>
          </div>
          <div><span style={label}>Contact</span><input value={form.contact_name} onChange={e => setForm({ ...form, contact_name: e.target.value })} style={input} /></div>
          <div><span style={label}>Email</span><input value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} type="email" style={input} /></div>
          <div><span style={label}>Phone</span><input value={form.phone} onChange={e => setForm({ ...form, phone: e.target.value })} style={input} /></div>
          <div style={{ gridColumn: '1 / -1' }}>
            <span style={label}>What they are paid (a note — agency pay is not wired to payouts)</span>
            <input value={form.commission_note} onChange={e => setForm({ ...form, commission_note: e.target.value })} style={input} />
          </div>
          <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8 }}>
            <button style={btnPrimary} disabled={busy || !form.name.trim()} onClick={save}>{editing ? 'Save changes' : 'Add agency'}</button>
            <button style={btn} onClick={() => { setShowForm(false); setEditing(''); setForm({ ...emptyForm }) }}>Cancel</button>
          </div>
        </div>
      )}

      <div style={{ ...panel, padding: 0, marginBottom: 16 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr><th style={th}>Agency</th><th style={th}>Type</th><th style={th}>Contact</th>
              <th style={th}>Leads</th><th style={th}>Awaiting response</th><th style={th}>Value</th>
              <th style={th}>Active</th>{canEdit && <th style={th} />}</tr>
          </thead>
          <tbody>
            {agencies.map(a => {
              const s = statsFor(a.id)
              return (
                <tr key={a.id} style={{ opacity: a.is_active === false ? 0.5 : 1 }}>
                  <td style={{ ...cell, fontWeight: 600 }}>{a.name}</td>
                  <td style={cell}>{String(a.type || '').replace(/_/g, ' ')}</td>
                  <td style={cell}>{a.contact_name || '—'}{a.email ? ` · ${a.email}` : ''}</td>
                  <td style={cell}>{s.total}</td>
                  <td style={{ ...cell, color: s.pending ? '#f39c12' : undefined, fontWeight: s.pending ? 600 : 400 }}>{s.pending}</td>
                  <td style={cell}>{fmtMoney(s.value)}</td>
                  <td style={cell}>{a.is_active === false ? 'no' : 'yes'}</td>
                  {canEdit && (
                    <td style={cell}>
                      <button style={{ ...btn, padding: '3px 8px', fontSize: 12 }}
                              onClick={() => {
                                setEditing(a.id); setShowForm(true)
                                setForm({ name: a.name, type: a.type || 'referral', contact_name: a.contact_name || '',
                                          email: a.email || '', phone: a.phone || '', commission_note: a.commission_note || '' })
                              }}>Edit</button>
                    </td>
                  )}
                </tr>
              )
            })}
            {agencies.length === 0 && <tr><td colSpan={8} style={{ ...cell, color: 'var(--text2)', textAlign: 'center', padding: 20 }}>No agencies yet.</td></tr>}
          </tbody>
        </table>
      </div>

      <div style={panel}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Leads currently with an agency</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr><th style={th}>#</th><th style={th}>Lead</th><th style={th}>Agency</th><th style={th}>Sent</th><th style={th}>Response</th><th style={th}>Value</th></tr></thead>
          <tbody>
            {leads.map(l => (
              <tr key={l.id}>
                <td style={cell}>{l.lead_no}</td>
                <td style={cell}><Link href={`/crm/leads/${l.id}`}>{l.display_name}</Link></td>
                <td style={cell}>{l.agency_name || '—'}</td>
                <td style={cell}>{fmtDate(l.agency_assigned_at)}</td>
                <td style={{ ...cell, color: l.agency_accepted_at ? '#16a34a' : '#f39c12' }}>
                  {l.agency_accepted_at ? 'accepted' : 'waiting'}
                </td>
                <td style={cell}>{fmtMoney(l.value_estimate)}</td>
              </tr>
            ))}
            {leads.length === 0 && <tr><td colSpan={6} style={{ ...cell, color: 'var(--text2)' }}>Nothing is out with an agency right now.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
