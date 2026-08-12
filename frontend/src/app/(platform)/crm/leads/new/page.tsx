'use client'
// Log a lead. Capture MUST be faster than not capturing, so the only hard requirement is a phone
// number (or an email). Every reference field is a picker over the tenant's own configured values —
// RULE THREE, pick-don't-type — and the duplicate check runs as you type the number.
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { api } from '@/lib/client'
import EntityPicker, { US_STATES } from '@/components/EntityPicker'
import { panel, input, label, btn, btnPrimary, fmtPhone, type RefRow } from '@/lib/crm'

export default function NewLeadPage() {
  const router = useRouter()
  const [sources, setSources] = useState<RefRow[]>([])
  const [interests, setInterests] = useState<RefRow[]>([])
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [dupes, setDupes] = useState<any[]>([])
  const [f, setF] = useState({
    first_name: '', last_name: '', company_name: '', phone: '', email: '',
    address_1: '', city: '', state: '', zip: '',
    source_id: '', interest_id: '', value_estimate: '', lines_estimate: '',
    expected_close_date: '', notes: '', sms_opt_in: false, do_not_call: false,
  })

  useEffect(() => {
    (async () => {
      try {
        const [s, i] = await Promise.all([
          api('/api/v1/crm/lists/sources'),
          api('/api/v1/crm/lists/interests'),
        ])
        setSources(s || []); setInterests(i || [])
      } catch { /* config not reachable — the form still submits with no source */ }
    })()
  }, [])

  // Live duplicate warning. Advisory only: a customer coming back IS a second lead, and refusing it
  // just teaches reps to change a digit.
  useEffect(() => {
    const digits = f.phone.replace(/[^0-9]/g, '')
    if (digits.length < 7) { setDupes([]); return }
    const t = setTimeout(async () => {
      try {
        const r = await api('/api/v1/crm/leads/dedupe-check', {
          method: 'POST', body: JSON.stringify({ phone: f.phone, email: f.email }),
        })
        setDupes(r.duplicates || [])
      } catch { setDupes([]) }
    }, 400)
    return () => clearTimeout(t)
  }, [f.phone, f.email])

  async function save() {
    setSaving(true); setMsg('')
    try {
      const r = await api('/api/v1/crm/leads', {
        method: 'POST',
        body: JSON.stringify({
          ...f,
          value_estimate: Number(f.value_estimate) || 0,
          lines_estimate: Number(f.lines_estimate) || 0,
          expected_close_date: f.expected_close_date || null,
        }),
      })
      router.push(`/crm/leads/${r.lead.id}`)
    } catch (e: any) { setMsg(e?.message || String(e)); setSaving(false) }
  }

  const set = (k: string, v: any) => setF(p => ({ ...p, [k]: v }))
  const opts = (rows: RefRow[]) => rows.map(r => ({ id: r.id, label: r.name }))

  return (
    <div style={{ padding: 20, maxWidth: 780 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>➕ Log a Lead</h1>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14 }}>
        A phone number is enough. Everything else can be filled in later — the follow-up starts the moment you save.
      </div>

      {dupes.length > 0 && (
        <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 14 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>⚠️ We already have this number</div>
          {dupes.map(d => (
            <div key={d.id} style={{ fontSize: 13, marginBottom: 3 }}>
              <Link href={`/crm/leads/${d.id}`}>#{d.lead_no} {d.name}</Link>
              <span style={{ color: 'var(--text2)' }}> · {d.status} · {d.owner_employee_id || 'unassigned'}</span>
            </div>
          ))}
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>
            Work the existing lead if this is the same conversation. Saving anyway is fine when it is genuinely a new opportunity.
          </div>
        </div>
      )}

      <div style={{ ...panel, display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
        <div style={{ gridColumn: '1 / -1' }}>
          <span style={label}>Phone *</span>
          <input value={f.phone} onChange={e => set('phone', e.target.value)} placeholder="(516) 555-0134"
                 autoFocus inputMode="tel" style={{ ...input, fontSize: 16 }} />
          {f.phone && <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 3 }}>{fmtPhone(f.phone)}</div>}
        </div>
        <div><span style={label}>First name</span><input value={f.first_name} onChange={e => set('first_name', e.target.value)} style={input} /></div>
        <div><span style={label}>Last name</span><input value={f.last_name} onChange={e => set('last_name', e.target.value)} style={input} /></div>
        <div><span style={label}>Email</span><input value={f.email} onChange={e => set('email', e.target.value)} type="email" style={input} /></div>
        <div><span style={label}>Company (business lead)</span><input value={f.company_name} onChange={e => set('company_name', e.target.value)} style={input} /></div>

        <div><span style={label}>Where did they come from?</span>
          <EntityPicker options={opts(sources)} value={f.source_id || null}
                        onChange={v => set('source_id', v || '')} placeholder="Lead source…" />
        </div>
        <div><span style={label}>What do they want?</span>
          <EntityPicker options={opts(interests)} value={f.interest_id || null}
                        onChange={v => set('interest_id', v || '')} placeholder="Interest…" />
        </div>

        <div><span style={label}>Estimated value ($)</span><input value={f.value_estimate} onChange={e => set('value_estimate', e.target.value)} inputMode="decimal" style={input} /></div>
        <div><span style={label}>Lines</span><input value={f.lines_estimate} onChange={e => set('lines_estimate', e.target.value)} inputMode="numeric" style={input} /></div>
        <div><span style={label}>Expected close</span><input type="date" value={f.expected_close_date} onChange={e => set('expected_close_date', e.target.value)} style={input} /></div>

        <div><span style={label}>City</span><input value={f.city} onChange={e => set('city', e.target.value)} style={input} /></div>
        <div><span style={label}>State</span>
          <EntityPicker options={US_STATES} value={f.state || null} onChange={v => set('state', v || '')} placeholder="State…" />
        </div>
        <div><span style={label}>ZIP</span><input value={f.zip} onChange={e => set('zip', e.target.value)} style={input} /></div>

        <div style={{ gridColumn: '1 / -1' }}>
          <span style={label}>Notes</span>
          <textarea value={f.notes} onChange={e => set('notes', e.target.value)} rows={3} style={{ ...input, resize: 'vertical' }} />
        </div>
        <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 18, fontSize: 13 }}>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={f.sms_opt_in} onChange={e => set('sms_opt_in', e.target.checked)} />
            They agreed to receive texts
          </label>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={f.do_not_call} onChange={e => set('do_not_call', e.target.checked)} />
            Do not call
          </label>
        </div>
      </div>

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginTop: 12 }}>{msg}</div>}

      <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
        <button onClick={save} disabled={saving || (!f.phone.trim() && !f.email.trim())} style={btnPrimary}>
          {saving ? 'Saving…' : 'Save lead'}
        </button>
        <Link href="/crm/leads" style={{ ...btn, textDecoration: 'none' }}>Cancel</Link>
      </div>
    </div>
  )
}
