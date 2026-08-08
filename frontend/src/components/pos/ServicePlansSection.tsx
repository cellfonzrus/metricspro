'use client'
// POS module — Phase 2: Service Plans section. NEW UI — the standalone pos-system app shipped
// the pos.service_plans catalog empty with no management screen (the activation form showed
// "No service plans configured yet"), so this simple CRUD section is how the owner finally
// seeds plans. Talks to the FastAPI /pos router (GET/POST /api/v1/pos/service-plans,
// PATCH /service-plans/{id}). Required: carrier + plan_name.
// This table lists inactive plans too (include_inactive=true) so they can be reactivated;
// the activation form's dropdown only ever sees active (or status-null) plans.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { friendlyError } from '@/components/pos/PosConfigSection'

interface ServicePlan {
  id: string
  carrier: string
  plan_code: string | null
  plan_name: string
  plan_description: string | null
  monthly_fee: number | null
  included_minutes: number | null
  service_area: string | null
  contract_type: string | null
  contract_terms: string | null
  dealer_code: string | null
  status: string | null
  created_at?: string
}

interface PlanForm {
  id: string | null
  carrier: string
  plan_code: string
  plan_name: string
  plan_description: string
  monthly_fee: string
  included_minutes: string
  service_area: string
  contract_type: string
  contract_terms: string
  dealer_code: string
}

const emptyPlanForm: PlanForm = {
  id: null, carrier: '', plan_code: '', plan_name: '', plan_description: '',
  monthly_fee: '', included_minutes: '', service_area: '', contract_type: '',
  contract_terms: '', dealer_code: '',
}

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 14px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const errorBox: React.CSSProperties = { margin: '12px 16px', border: '1px solid #dc2626', color: '#dc2626', borderRadius: 8, padding: '10px 14px', fontSize: 12 }

export default function ServicePlansSection() {
  const [plans, setPlans] = useState<ServicePlan[]>([])
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState<PlanForm | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    loadPlans().then(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function loadPlans() {
    try {
      const r = await api('/api/v1/pos/service-plans?include_inactive=true')
      setPlans((r.service_plans || []) as ServicePlan[])
    } catch (err) {
      setError(friendlyError(err, 'Could not load service plans'))
    }
  }

  async function savePlan() {
    if (!form) return
    const carrier = form.carrier.trim()
    const planName = form.plan_name.trim()
    if (!carrier) { setError('Carrier is required.'); return }
    if (!planName) { setError('Plan name is required.'); return }
    const feeRaw = form.monthly_fee.trim()
    const fee = Number(feeRaw)
    if (feeRaw !== '' && (!Number.isFinite(fee) || fee < 0)) { setError('Monthly fee must be a number of 0 or more.'); return }
    const minutesRaw = form.included_minutes.trim()
    const minutes = Math.round(Number(minutesRaw))
    if (minutesRaw !== '' && (!Number.isFinite(minutes) || minutes < 0)) { setError('Included minutes must be a whole number of 0 or more.'); return }
    setSaving(true)
    setError('')
    try {
      const payload = {
        carrier,
        plan_name: planName,
        plan_code: form.plan_code.trim() || null,
        plan_description: form.plan_description.trim() || null,
        monthly_fee: feeRaw === '' ? null : Math.round(fee * 100) / 100,
        included_minutes: minutesRaw === '' ? null : minutes,
        service_area: form.service_area.trim() || null,
        contract_type: form.contract_type.trim() || null,
        contract_terms: form.contract_terms.trim() || null,
        dealer_code: form.dealer_code.trim() || null,
      }
      if (form.id) await api(`/api/v1/pos/service-plans/${form.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
      else await api('/api/v1/pos/service-plans', { method: 'POST', body: JSON.stringify({ ...payload, status: 'active' }) })
      setForm(null)
      await loadPlans()
    } catch (err) {
      setError(friendlyError(err, 'Could not save service plan'))
    } finally {
      setSaving(false)
    }
  }

  async function togglePlan(p: ServicePlan) {
    const active = p.status !== 'inactive'
    if (active && !confirm(`Deactivate "${p.plan_name}"? It will disappear from the activation form's plan dropdown.`)) return
    setError('')
    try {
      await api(`/api/v1/pos/service-plans/${p.id}`, { method: 'PATCH', body: JSON.stringify({ status: active ? 'inactive' : 'active' }) })
      await loadPlans()
    } catch (err) {
      setError(friendlyError(err, 'Could not update service plan'))
    }
  }

  function openEdit(p: ServicePlan) {
    setError('')
    setForm({
      id: p.id,
      carrier: p.carrier || '',
      plan_code: p.plan_code || '',
      plan_name: p.plan_name || '',
      plan_description: p.plan_description || '',
      monthly_fee: p.monthly_fee === null || p.monthly_fee === undefined ? '' : String(p.monthly_fee),
      included_minutes: p.included_minutes === null || p.included_minutes === undefined ? '' : String(p.included_minutes),
      service_area: p.service_area || '',
      contract_type: p.contract_type || '',
      contract_terms: p.contract_terms || '',
      dealer_code: p.dealer_code || '',
    })
  }

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, marginBottom: 16, overflow: 'hidden' }}>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>📶 Service Plans</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>Carrier plan catalog — feeds the activation form&apos;s plan dropdown, which auto-fills fee and contract fields</div>
        </div>
        {!form && (
          <button className="btn btn-primary" onClick={() => { setError(''); setForm({ ...emptyPlanForm }) }}>+ Add Service Plan</button>
        )}
      </div>

      {/* Plan form */}
      {form && (
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 10 }}>{form.id ? 'Edit Service Plan' : 'New Service Plan'}</div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <label style={label}>Carrier *</label>
              <input value={form.carrier} onChange={e => setForm({ ...form, carrier: e.target.value })} placeholder="e.g. T-Mobile" style={{ ...input, width: 150 }} />
            </div>
            <div>
              <label style={label}>Plan name *</label>
              <input value={form.plan_name} onChange={e => setForm({ ...form, plan_name: e.target.value })} placeholder="e.g. Essentials Saver" style={{ ...input, width: 200 }} />
            </div>
            <div>
              <label style={label}>Plan code</label>
              <input value={form.plan_code} onChange={e => setForm({ ...form, plan_code: e.target.value })} placeholder="e.g. ESS-50" style={{ ...input, width: 120 }} />
            </div>
            <div>
              <label style={label}>Monthly fee ($)</label>
              <input value={form.monthly_fee} onChange={e => setForm({ ...form, monthly_fee: e.target.value })} placeholder="50.00" inputMode="decimal" style={{ ...input, width: 100 }} />
            </div>
            <div>
              <label style={label}>Included minutes</label>
              <input value={form.included_minutes} onChange={e => setForm({ ...form, included_minutes: e.target.value })} placeholder="unlimited = blank" inputMode="numeric" style={{ ...input, width: 130 }} />
            </div>
            <div>
              <label style={label}>Service area</label>
              <input value={form.service_area} onChange={e => setForm({ ...form, service_area: e.target.value })} placeholder="e.g. Nationwide" style={{ ...input, width: 140 }} />
            </div>
            <div>
              <label style={label}>Contract type</label>
              <input value={form.contract_type} onChange={e => setForm({ ...form, contract_type: e.target.value })} placeholder="e.g. Postpaid" style={{ ...input, width: 130 }} />
            </div>
            <div>
              <label style={label}>Contract terms</label>
              <input value={form.contract_terms} onChange={e => setForm({ ...form, contract_terms: e.target.value })} placeholder="e.g. Month-to-month" style={{ ...input, width: 160 }} />
            </div>
            <div>
              <label style={label}>Dealer code</label>
              <input value={form.dealer_code} onChange={e => setForm({ ...form, dealer_code: e.target.value })} placeholder="optional" style={{ ...input, width: 130 }} />
            </div>
            <div style={{ flexBasis: '100%' }}>
              <label style={label}>Description</label>
              <textarea value={form.plan_description} onChange={e => setForm({ ...form, plan_description: e.target.value })} rows={2}
                placeholder="What the plan includes — shown to reps picking a plan"
                style={{ ...input, width: '100%', maxWidth: 560, resize: 'vertical', fontFamily: 'inherit', boxSizing: 'border-box' }} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" onClick={savePlan} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
              <button className="btn btn-secondary" onClick={() => { setForm(null); setError('') }}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {error && <div style={errorBox}>{error}</div>}

      {/* Plans table (backend orders by carrier, then plan name) */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 30 }}><div className="spinner" /></div>
      ) : plans.length === 0 ? (
        <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
          No service plans yet — add your carriers&apos; plans so the activation form can auto-fill fees and contract fields.
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--surface2)' }}>
                <th style={th}>Carrier</th><th style={th}>Plan Name</th><th style={th}>Code</th><th style={th}>Monthly Fee</th><th style={th}>Contract</th><th style={th}>Status</th><th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {plans.map((p, i) => {
                const active = p.status !== 'inactive'
                const firstOfCarrier = i === 0 || plans[i - 1].carrier !== p.carrier
                return (
                  <tr key={p.id} style={{ opacity: active ? 1 : 0.5 }}>
                    <td style={{ ...td, fontWeight: 700, color: firstOfCarrier ? 'var(--text)' : 'var(--text3)' }}>{firstOfCarrier ? p.carrier : '〃'}</td>
                    <td style={{ ...td, fontWeight: 600 }}>
                      {p.plan_name}
                      {p.plan_description && <div style={{ fontSize: 12, color: 'var(--text2)', fontWeight: 400 }}>{p.plan_description}</div>}
                    </td>
                    <td style={{ ...td, color: 'var(--text2)' }}>{p.plan_code || '—'}</td>
                    <td style={{ ...td, color: '#16a34a', fontWeight: 700 }}>{p.monthly_fee === null || p.monthly_fee === undefined ? '—' : `$${Number(p.monthly_fee).toFixed(2)}/mo`}</td>
                    <td style={{ ...td, color: 'var(--text2)' }}>{[p.contract_type, p.contract_terms].filter(Boolean).join(' · ') || '—'}</td>
                    <td style={td}><span style={{ color: active ? '#16a34a' : '#dc2626', fontWeight: 600 }}>{active ? 'Active' : 'Inactive'}</span></td>
                    <td style={td}>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => openEdit(p)}>Edit</button>
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px', color: active ? '#dc2626' : '#16a34a' }}
                          onClick={() => togglePlan(p)}>{active ? 'Deactivate' : 'Reactivate'}</button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
        💡 The activation form lists these plans grouped by carrier and auto-fills the fee, minutes, and contract fields
        from the selected plan. Deactivated plans stay listed here (for reactivation) but are hidden from that dropdown.
      </div>
    </div>
  )
}
