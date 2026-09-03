'use client'
// Lease & Insurance panel for Store Setup (owner directive 2026-09-03, mig 946): landlord + site
// contact, rent payment links / ACH, current rent + escalation (% OR an explicit monthly-rent
// schedule), rent due (house default = FIRST WEEK of the month via the org config — per-store
// override here), insurance + premium due, and lease/COI document uploads (append-only versions,
// downloadable any time via a gated signed URL). SERVER-GATED whole (mig-434 posture, fail-closed
// 403 for callers below market manager unless granted `store_lease_docs`) — this panel just renders
// the gate's answer; hiding fields client-side is never the protection.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { sel } from '../lib'

type Due = { kind: 'week' | 'day'; value: number }
type SchedRow = { effective_from: string; monthly_rent: number | string }

const lbl: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: 'var(--text3)', textTransform: 'uppercase' }
const group: React.CSSProperties = { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }
const fld = (w: number): React.CSSProperties => ({ display: 'flex', flexDirection: 'column', gap: 3, width: w })

function Field({ label, width = 160, children }: { label: string; width?: number; children: React.ReactNode }) {
  return <div style={fld(width)}><span style={lbl}>{label}</span>{children}</div>
}

export default function LeasePanel({ storeCode }: { storeCode: string }) {
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [lease, setLease] = useState<any>({})
  const [docs, setDocs] = useState<{ lease: any[]; insurance_coi: any[] }>({ lease: [], insurance_coi: [] })
  const [dueDefault, setDueDefault] = useState<Due>({ kind: 'week', value: 1 })
  const [dueKind, setDueKind] = useState<'' | 'week' | 'day'>('')  // '' = company default
  const [dueValue, setDueValue] = useState<number>(1)
  const [links, setLinks] = useState('')
  const [sched, setSched] = useState<SchedRow[]>([])
  const [monthRent, setMonthRent] = useState<number | null>(null)
  const [upBusy, setUpBusy] = useState<Record<string, boolean>>({})

  async function load() {
    setLoading(true); setMsg('')
    try {
      const r = await api(`/api/v1/storeops/store-lease?store_code=${encodeURIComponent(storeCode)}`)
      const l = r?.lease || {}
      setLease(l)
      setDocs({ lease: r?.documents?.lease || [], insurance_coi: r?.documents?.insurance_coi || [] })
      setDueDefault(r?.rent_due_default || { kind: 'week', value: 1 })
      setDueKind(l?.rent_due?.kind || '')
      setDueValue(l?.rent_due?.value || 1)
      setLinks((l?.rent_payment_links || []).join('\n'))
      setSched((l?.rent_schedule || []).map((e: any) => ({ effective_from: e.effective_from, monthly_rent: e.monthly_rent })))
      setMonthRent(typeof r?.current_month_rent === 'number' ? r.current_month_rent : null)
      setDenied(false)
    } catch (err: any) {
      if (err?.status === 403) setDenied(true)
      else setMsg('Load failed: ' + (err?.message || err))
    }
    setLoading(false)
  }
  useEffect(() => { load() }, [storeCode]) // eslint-disable-line react-hooks/exhaustive-deps

  const set = (patch: any) => setLease((l: any) => ({ ...l, ...patch }))

  async function save() {
    setBusy(true); setMsg('')
    try {
      const body: any = {
        landlord_name: lease.landlord_name, landlord_email: lease.landlord_email, landlord_phone: lease.landlord_phone,
        site_contact_name: lease.site_contact_name, site_contact_phone: lease.site_contact_phone,
        rent_payment_links: links,
        ach_bank_name: lease.ach_bank_name, ach_routing_number: lease.ach_routing_number,
        ach_account_number: lease.ach_account_number, ach_notes: lease.ach_notes,
        current_rent: lease.current_rent, rent_effective_from: lease.rent_effective_from,
        escalation_pct: lease.escalation_pct,
        rent_schedule: sched.filter(e => e.effective_from && e.monthly_rent !== '' && e.monthly_rent != null)
          .map(e => ({ effective_from: e.effective_from, monthly_rent: Number(e.monthly_rent) })),
        rent_due: dueKind ? { kind: dueKind, value: Number(dueValue) || 1 } : null,
        lease_start: lease.lease_start, lease_end: lease.lease_end,
        insurance_company: lease.insurance_company, insurance_policy_number: lease.insurance_policy_number,
        insurance_premium: lease.insurance_premium, insurance_premium_due: lease.insurance_premium_due,
        insurance_premium_frequency: lease.insurance_premium_frequency || 'annual',
        insurance_notes: lease.insurance_notes, notes: lease.notes,
      }
      await api(`/api/v1/storeops/store-lease?store_code=${encodeURIComponent(storeCode)}`,
        { method: 'PUT', body: JSON.stringify(body) })
      setMsg('✓ saved')
      await load()
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)) }
    setBusy(false)
  }

  async function uploadDoc(kind: 'lease' | 'insurance_coi', file: File) {
    setUpBusy(b => ({ ...b, [kind]: true })); setMsg('')
    try {
      const data = await new Promise<string>((resolve, reject) => {
        const rd = new FileReader()
        rd.onload = () => resolve(String(rd.result || ''))
        rd.onerror = () => reject(new Error('could not read the file'))
        rd.readAsDataURL(file)
      })
      await api('/api/v1/storeops/store-lease/doc', {
        method: 'POST',
        body: JSON.stringify({ store_code: storeCode, doc_kind: kind, file_name: file.name, data }),
      })
      setMsg(`✓ ${kind === 'lease' ? 'lease' : 'COI'} uploaded`)
      await load()
    } catch (err: any) { setMsg('Upload failed: ' + (err?.message || err)) }
    setUpBusy(b => ({ ...b, [kind]: false }))
  }

  async function download(docId: string) {
    setMsg('')
    try {
      const r = await api(`/api/v1/storeops/store-lease/doc-url?doc_id=${encodeURIComponent(docId)}`)
      if (r?.url) window.open(r.url, '_blank', 'noopener')
    } catch (err: any) { setMsg('Download failed: ' + (err?.message || err)) }
  }

  if (loading) return <div style={{ padding: 14, color: 'var(--text3)', fontSize: 13 }}>Loading lease details…</div>
  if (denied) return (
    <div style={{ padding: 14, color: 'var(--text3)', fontSize: 13 }}>
      Lease, landlord and insurance details are restricted to management roles.
    </div>
  )

  const defaultLabel = dueDefault.kind === 'week'
    ? `Company default (week ${dueDefault.value} of the month)`
    : `Company default (day ${dueDefault.value} of the month)`

  const DocList = ({ kind, title }: { kind: 'lease' | 'insurance_coi'; title: string }) => (
    <div style={{ flex: 1, minWidth: 260 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 700 }}>{title}</span>
        <label className="btn" style={{ fontSize: 12, padding: '3px 8px', cursor: upBusy[kind] ? 'default' : 'pointer', margin: 0 }}>
          {upBusy[kind] ? '⏳ Uploading…' : '⬆️ Upload new version'}
          <input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" style={{ display: 'none' }} disabled={!!upBusy[kind]}
            onChange={e => { const f = e.target.files?.[0]; if (f) uploadDoc(kind, f); e.currentTarget.value = '' }} />
        </label>
      </div>
      {(docs[kind] || []).length === 0
        ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>No document on file.</div>
        : (docs[kind] || []).map((d: any, i: number) => (
          <div key={d.id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, padding: '3px 0' }}>
            <button className="btn" style={{ fontSize: 12, padding: '2px 8px' }} onClick={() => download(d.id)}>⬇️</button>
            <span style={{ fontWeight: i === 0 ? 700 : 400 }}>
              {d.file_name || 'document'}{i === 0 ? ' (current)' : ''}
            </span>
            <span style={{ color: 'var(--text3)' }}>{String(d.uploaded_at || '').slice(0, 10)}{d.uploaded_by ? ` · ${d.uploaded_by}` : ''}</span>
          </div>
        ))}
    </div>
  )

  return (
    <div style={{ padding: '12px 14px', background: 'var(--surface2)', borderRadius: 8, display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Landlord + site contact */}
      <div style={group}>
        <Field label="Landlord name"><input style={sel} value={lease.landlord_name || ''} onChange={e => set({ landlord_name: e.target.value })} /></Field>
        <Field label="Landlord email" width={200}><input style={sel} type="email" value={lease.landlord_email || ''} onChange={e => set({ landlord_email: e.target.value })} /></Field>
        <Field label="Landlord phone" width={140}><input style={sel} value={lease.landlord_phone || ''} onChange={e => set({ landlord_phone: e.target.value })} /></Field>
        <Field label="Site contact"><input style={sel} value={lease.site_contact_name || ''} onChange={e => set({ site_contact_name: e.target.value })} /></Field>
        <Field label="Site contact phone" width={140}><input style={sel} value={lease.site_contact_phone || ''} onChange={e => set({ site_contact_phone: e.target.value })} /></Field>
      </div>

      {/* Rent amount + due + escalation */}
      <div style={group}>
        <Field label="Current rent ($/mo)" width={130}><input style={sel} type="number" value={lease.current_rent ?? ''} onChange={e => set({ current_rent: e.target.value })} /></Field>
        <Field label="Rent effective from" width={150}><input style={sel} type="date" title="The date the current rent took effect — annual escalations count from here" value={lease.rent_effective_from || ''} onChange={e => set({ rent_effective_from: e.target.value })} /></Field>
        <Field label="Annual escalation %" width={140}><input style={sel} type="number" step="0.1" title="Compounded once per lease year. Leave blank if you enter the monthly-rent schedule instead." value={lease.escalation_pct ?? ''} onChange={e => set({ escalation_pct: e.target.value })} /></Field>
        <Field label="Rent due" width={210}>
          <select style={sel} value={dueKind} onChange={e => setDueKind(e.target.value as any)}>
            <option value="">{defaultLabel}</option>
            <option value="week">Week of the month…</option>
            <option value="day">Day of the month…</option>
          </select>
        </Field>
        {dueKind && (
          <Field label={dueKind === 'week' ? 'Week (1-5)' : 'Day (1-31)'} width={90}>
            <input style={sel} type="number" min={1} max={dueKind === 'week' ? 5 : 31} value={dueValue}
              onChange={e => setDueValue(Number(e.target.value) || 1)} />
          </Field>
        )}
        <Field label="Lease start" width={140}><input style={sel} type="date" value={lease.lease_start || ''} onChange={e => set({ lease_start: e.target.value })} /></Field>
        <Field label="Lease end" width={140}><input style={sel} type="date" value={lease.lease_end || ''} onChange={e => set({ lease_end: e.target.value })} /></Field>
        {monthRent != null && <span style={{ fontSize: 12, color: 'var(--text2)', paddingBottom: 6 }}>This month&apos;s rent: <b>${monthRent.toLocaleString()}</b></span>}
      </div>

      {/* Explicit monthly-rent schedule (wins over the % when a period matches) */}
      <div>
        <div style={{ ...lbl, marginBottom: 4 }}>Monthly-rent schedule (optional — overrides the % for the periods listed)</div>
        {sched.map((e, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
            <input style={{ ...sel, width: 150 }} type="date" value={e.effective_from}
              onChange={ev => setSched(s => s.map((x, j) => j === i ? { ...x, effective_from: ev.target.value } : x))} />
            <input style={{ ...sel, width: 130 }} type="number" placeholder="Monthly rent $" value={e.monthly_rent}
              onChange={ev => setSched(s => s.map((x, j) => j === i ? { ...x, monthly_rent: ev.target.value } : x))} />
            <button className="btn" style={{ fontSize: 12, padding: '3px 8px' }} onClick={() => setSched(s => s.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <button className="btn" style={{ fontSize: 12, padding: '3px 8px' }} onClick={() => setSched(s => [...s, { effective_from: '', monthly_rent: '' }])}>➕ Add rent period</button>
      </div>

      {/* Payment rails */}
      <div style={group}>
        <Field label="Rent payment link(s) — one per line" width={280}>
          <textarea style={{ ...sel, height: 54, resize: 'vertical' }} value={links} onChange={e => setLinks(e.target.value)} />
        </Field>
        <Field label="ACH bank" width={150}><input style={sel} value={lease.ach_bank_name || ''} onChange={e => set({ ach_bank_name: e.target.value })} /></Field>
        <Field label="ACH routing #" width={130}><input style={sel} value={lease.ach_routing_number || ''} onChange={e => set({ ach_routing_number: e.target.value })} /></Field>
        <Field label="ACH account #" width={150}><input style={sel} value={lease.ach_account_number || ''} onChange={e => set({ ach_account_number: e.target.value })} /></Field>
        <Field label="ACH notes" width={200}><input style={sel} value={lease.ach_notes || ''} onChange={e => set({ ach_notes: e.target.value })} /></Field>
      </div>

      {/* Insurance */}
      <div style={group}>
        <Field label="Insurance company"><input style={sel} value={lease.insurance_company || ''} onChange={e => set({ insurance_company: e.target.value })} /></Field>
        <Field label="Policy #" width={150}><input style={sel} value={lease.insurance_policy_number || ''} onChange={e => set({ insurance_policy_number: e.target.value })} /></Field>
        <Field label="Premium ($)" width={110}><input style={sel} type="number" value={lease.insurance_premium ?? ''} onChange={e => set({ insurance_premium: e.target.value })} /></Field>
        <Field label="Premium due date" width={150}><input style={sel} type="date" value={lease.insurance_premium_due || ''} onChange={e => set({ insurance_premium_due: e.target.value })} /></Field>
        <Field label="Billing frequency" width={130}>
          <select style={sel} value={lease.insurance_premium_frequency || 'annual'} onChange={e => set({ insurance_premium_frequency: e.target.value })}>
            <option value="annual">Annual</option>
            <option value="semiannual">Semi-annual</option>
            <option value="quarterly">Quarterly</option>
            <option value="monthly">Monthly</option>
          </select>
        </Field>
        <Field label="Insurance notes" width={220}><input style={sel} value={lease.insurance_notes || ''} onChange={e => set({ insurance_notes: e.target.value })} /></Field>
      </div>

      {/* Documents */}
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
        <DocList kind="lease" title="📄 Current lease" />
        <DocList kind="insurance_coi" title="🛡️ Insurance COI" />
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="btn btn-primary" disabled={busy} onClick={save}>{busy ? '⏳ Saving…' : '💾 Save lease & insurance'}</button>
        {msg && <span style={{ fontSize: 12, color: msg.startsWith('✓') ? '#166534' : '#b91c1c' }}>{msg}</span>}
      </div>
    </div>
  )
}
