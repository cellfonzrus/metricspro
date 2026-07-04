'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// Tenant Billing (SaaS) — SUPER-ADMIN only. Price each tenant by basis × cycle, see the live
// quantity drivers, generate invoices, and track MRR/ARR. Payment gateway is a later phase.

type Plan = {
  org_id: string; basis: string; unit_price: number; cycle: string; currency: string
  modules: string[] | null; is_active: boolean; notes: string | null
}
type Drivers = { per_store: number; per_user: number; per_entity: number; per_module: number; flat: number }
type PlanRow = { org_id: string; name: string; is_active_tenant: boolean; plan: Plan | null; drivers: Drivers }
type Invoice = {
  id: string; org_id: string; period_start: string; period_end: string; basis: string
  quantity: number; unit_price: number; amount: number; currency: string; status: string
  issued_at: string | null; due_date: string | null; payment_ref: string | null; notes: string | null; created_at: string
}
type SummaryRow = { org_id: string; name: string; plan: Plan | null; quantity: number; monthly_amount: number; latest_invoice: Invoice | null }
type Connector = {
  id?: string; provider: string; display_name?: string; credential?: string; credential_masked?: string; has_credential?: boolean
  config?: any; flat_monthly_cost?: number | null; is_enabled?: boolean
  last_cost?: number | null; last_currency?: string; last_synced_at?: string | null
  last_status?: string | null; last_detail?: string | null; sort_order?: number; notes?: string | null
}
type Provider = { key: string; label: string; live: boolean; hint: string }

const BASES = ['flat', 'per_store', 'per_entity', 'per_user', 'per_module']
const CYCLES = ['monthly', 'annual']
const basisDriverKey: Record<string, keyof Drivers> = {
  flat: 'flat', per_store: 'per_store', per_entity: 'per_entity', per_user: 'per_user', per_module: 'per_module',
}
const basisLabel: Record<string, string> = {
  flat: 'Flat', per_store: 'Per store', per_entity: 'Per entity', per_user: 'Per user', per_module: 'Per module',
}

export default function BillingAdmin() {
  const { user, loading } = useAuth()
  const isSuper = !!user?.super_admin
  const [plans, setPlans] = useState<PlanRow[]>([])
  const [mrr, setMrr] = useState(0)
  const [arr, setArr] = useState(0)
  const [err, setErr] = useState('')
  const [ready, setReady] = useState(true)
  const [editing, setEditing] = useState<string | null>(null)        // org_id whose plan modal is open
  const [draft, setDraft] = useState<Partial<Plan>>({})
  const [invFor, setInvFor] = useState<PlanRow | null>(null)          // tenant whose invoices panel is open
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [period, setPeriod] = useState({ period_start: '', period_end: '', due_date: '' })
  const [busy, setBusy] = useState(false)
  // Platform costs (operator's own spend) + derived cost-per-tenant
  const [pc, setPc] = useState<{ total_monthly: number; active_tenants: number; cost_per_tenant: number; connectors: Connector[]; ready: boolean }>({ total_monthly: 0, active_tenants: 0, cost_per_tenant: 0, connectors: [], ready: true })
  const [providers, setProviders] = useState<Provider[]>([])
  const [pcEdit, setPcEdit] = useState<Partial<Connector> | null>(null)
  const [pcBusy, setPcBusy] = useState(false)
  const [pcMsg, setPcMsg] = useState('')

  const load = useCallback(() => {
    api('/api/v1/billing/plans')
      .then((d: any) => { setPlans(d.plans || []); setReady(d.ready !== false) })
      .catch(e => setErr(e?.message || 'Failed to load plans'))
    api('/api/v1/billing/summary')
      .then((d: any) => { setMrr(d.mrr || 0); setArr(d.arr || 0) })
      .catch(() => {})
  }, [])
  useEffect(() => { if (isSuper) load() }, [isSuper, load])

  const loadCosts = useCallback(() => {
    api('/api/v1/billing/platform-costs')
      .then((d: any) => setPc({ total_monthly: d.total_monthly || 0, active_tenants: d.active_tenants || 0, cost_per_tenant: d.cost_per_tenant || 0, connectors: d.connectors || [], ready: d.ready !== false }))
      .catch(() => {})
    api('/api/v1/billing/platform-providers').then((d: any) => setProviders(d.providers || [])).catch(() => {})
  }, [])
  useEffect(() => { if (isSuper) loadCosts() }, [isSuper, loadCosts])

  async function refreshCosts() {
    setPcBusy(true); setPcMsg('')
    try { const d = await api('/api/v1/billing/platform-costs/refresh', { method: 'POST', body: '{}' }); setPcMsg(`Refreshed ${d.refreshed} connector(s).`); loadCosts() }
    catch (e: any) { setPcMsg(e?.message || 'Refresh failed') } finally { setPcBusy(false) }
  }
  async function saveConnector() {
    if (!pcEdit?.provider) { setPcMsg('Pick a provider.'); return }
    setPcBusy(true); setPcMsg('')
    try { await api('/api/v1/billing/platform-connectors', { method: 'POST', body: JSON.stringify(pcEdit) }); setPcEdit(null); loadCosts() }
    catch (e: any) { setPcMsg(e?.message || 'Save failed') } finally { setPcBusy(false) }
  }
  async function deleteConnector(id?: string) {
    if (!id || !confirm('Remove this platform connector?')) return
    try { await api(`/api/v1/billing/platform-connectors/${id}`, { method: 'DELETE' }); setPcEdit(null); loadCosts() }
    catch (e: any) { setPcMsg(e?.message || 'Delete failed') }
  }

  function openEdit(row: PlanRow) {
    setDraft(row.plan ? { ...row.plan } : { org_id: row.org_id, basis: 'flat', unit_price: 0, cycle: 'monthly', currency: 'USD', is_active: true })
    setEditing(row.org_id)
  }
  async function savePlan() {
    if (!editing) return
    setBusy(true); setErr('')
    try {
      await api('/api/v1/billing/plan', { method: 'POST', body: JSON.stringify({ ...draft, org_id: editing }) })
      setEditing(null); load()
    } catch (e: any) { setErr(e?.message || 'Save failed') } finally { setBusy(false) }
  }
  async function deletePlan(org_id: string) {
    if (!confirm('Remove this tenant’s billing plan?')) return
    setBusy(true)
    try { await api(`/api/v1/billing/plan?org_id=${encodeURIComponent(org_id)}`, { method: 'DELETE' }); setEditing(null); load() }
    catch (e: any) { setErr(e?.message || 'Delete failed') } finally { setBusy(false) }
  }

  async function openInvoices(row: PlanRow) {
    setInvFor(row); setInvoices([])
    setPeriod({ period_start: '', period_end: '', due_date: '' })
    try {
      const d = await api(`/api/v1/billing/invoices?org_id=${encodeURIComponent(row.org_id)}`)
      setInvoices(d.invoices || [])
    } catch (e: any) { setErr(e?.message || 'Failed to load invoices') }
  }
  async function generate() {
    if (!invFor || !period.period_start || !period.period_end) { setErr('Pick a period start and end first.'); return }
    setBusy(true); setErr('')
    try {
      await api('/api/v1/billing/invoices/generate', { method: 'POST', body: JSON.stringify({ org_id: invFor.org_id, ...period }) })
      openInvoices(invFor); load()
    } catch (e: any) { setErr(e?.message || 'Generate failed') } finally { setBusy(false) }
  }
  async function setStatus(inv: Invoice, status: string) {
    let payment_ref: string | undefined
    if (status === 'paid') { payment_ref = prompt('Payment reference (wire/check/Zelle id):', inv.payment_ref || '') || undefined }
    setBusy(true)
    try {
      if (status === 'paid') await api(`/api/v1/billing/invoices/${inv.id}/pay`, { method: 'POST', body: JSON.stringify({ payment_ref }) })
      else await api(`/api/v1/billing/invoices/${inv.id}`, { method: 'PATCH', body: JSON.stringify({ status }) })
      if (invFor) openInvoices(invFor); load()
    } catch (e: any) { setErr(e?.message || 'Update failed') } finally { setBusy(false) }
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading…</div>
  if (!isSuper) return (
    <div style={{ padding: 24, maxWidth: 560 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700 }}>💳 Billing (Tenants)</h1>
      <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>This page is for <b>super-admins</b> only — they price and bill the companies on MetricsPro.</div>
    </div>
  )

  const inp = { padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14 }
  const driverVal = (row: PlanRow, basis?: string) => basis ? row.drivers[basisDriverKey[basis]] ?? 1 : null
  const planAmount = (row: PlanRow) => {
    if (!row.plan) return 0
    const q = driverVal(row, row.plan.basis) ?? 1
    return q * (row.plan.unit_price || 0)
  }
  const statusColor: Record<string, string> = { draft: '#64748b', sent: '#2563eb', paid: '#16a34a', void: '#b45309' }

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>💳 Billing (Tenants)</h1>
      <p style={{ color: 'var(--text3)', fontSize: 13, marginTop: 0 }}>
        Price each company by a <b>basis</b> (flat / per store / per entity / per user / per module) and <b>cycle</b>,
        then generate invoices. The live drivers show what would bill. Payments are recorded manually for now.
      </p>

      {/* MRR / ARR header */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <div className="card" style={{ padding: 16, minWidth: 180 }}>
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>MRR (monthly recurring)</div>
          <div style={{ fontSize: 26, fontWeight: 800 }}>{fmt(mrr)}</div>
        </div>
        <div className="card" style={{ padding: 16, minWidth: 180 }}>
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>ARR (annual run-rate)</div>
          <div style={{ fontSize: 26, fontWeight: 800 }}>{fmt(arr)}</div>
        </div>
        <div className="card" style={{ padding: 16, minWidth: 140 }}>
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>Tenants</div>
          <div style={{ fontSize: 26, fontWeight: 800 }}>{plans.length}</div>
        </div>
        <div className="card" style={{ padding: 16, minWidth: 170 }}>
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>Monthly run-cost</div>
          <div style={{ fontSize: 26, fontWeight: 800 }}>{fmt(pc.total_monthly)}</div>
          <div style={{ fontSize: 11, color: 'var(--text3)' }}>what all platforms cost</div>
        </div>
        <div className="card" style={{ padding: 16, minWidth: 175, borderColor: '#c7d2fe' }}>
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>Break-even / tenant</div>
          <div style={{ fontSize: 26, fontWeight: 800 }}>{fmt(pc.cost_per_tenant)}</div>
          <div style={{ fontSize: 11, color: 'var(--text3)' }}>{pc.active_tenants} active · price above this</div>
        </div>
      </div>

      {!ready && <div className="card" style={{ borderColor: '#f59e0b', color: '#b45309', padding: 12, marginBottom: 12 }}>
        ⚠️ Migration <code>064_billing.sql</code> hasn’t been applied yet — plans/invoices can’t be saved. Run it in the Supabase SQL editor.</div>}
      {err && <div className="card" style={{ borderColor: '#c0392b', color: '#c0392b', padding: 12, marginBottom: 12 }}>{err}</div>}

      {/* Tenants + plans table */}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ display: 'flex', fontSize: 12, color: 'var(--text3)', fontWeight: 600, padding: '10px 14px', borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
          <span style={{ flex: 1.4, minWidth: 150 }}>Company</span>
          <span style={{ flex: 1 }}>Plan</span>
          <span style={{ flex: 1.6, minWidth: 200 }}>Live drivers</span>
          <span style={{ width: 110, textAlign: 'right' }}>Per cycle</span>
          <span style={{ width: 210 }}></span>
        </div>
        {plans.length === 0 ? <div style={{ padding: 20, color: 'var(--text3)' }}>No companies yet.</div>
          : plans.map(row => (
            <div key={row.org_id} style={{ display: 'flex', alignItems: 'center', padding: '11px 14px', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
              <span style={{ flex: 1.4, minWidth: 150, fontWeight: 600 }}>
                {row.name}{!row.is_active_tenant && <span style={{ color: '#b45309', fontSize: 12 }}> · inactive</span>}
              </span>
              <span style={{ flex: 1, fontSize: 13 }}>
                {row.plan
                  ? <>{basisLabel[row.plan.basis] || row.plan.basis} · {fmt(row.plan.unit_price)} / {row.plan.cycle}{row.plan.is_active ? '' : ' (off)'}</>
                  : <span style={{ color: 'var(--text3)' }}>— no plan —</span>}
              </span>
              <span style={{ flex: 1.6, minWidth: 200, fontSize: 12, color: 'var(--text3)' }}>
                {row.drivers.per_store} stores · {row.drivers.per_user} users · {row.drivers.per_entity} entities · {row.drivers.per_module} modules
              </span>
              <span style={{ width: 110, textAlign: 'right', fontWeight: 600, fontSize: 13 }}>{row.plan ? fmt(planAmount(row)) : '—'}</span>
              <span style={{ width: 210, display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                <button className="btn btn-sm" onClick={() => openEdit(row)}>{row.plan ? 'Edit plan' : 'Set plan'}</button>
                <button className="btn btn-sm" onClick={() => openInvoices(row)}>Invoices</button>
              </span>
            </div>
          ))}
      </div>

      {/* Platform costs — the operator's own spend to run MetricsPro + cost per tenant */}
      <div className="card" style={{ padding: 16, marginTop: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
          <div style={{ fontWeight: 700 }}>🧾 Platform costs — what it costs to run MetricsPro</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-sm" disabled={pcBusy} onClick={refreshCosts}>{pcBusy ? 'Refreshing…' : '↻ Refresh live'}</button>
            <button className="btn btn-primary btn-sm" onClick={() => setPcEdit({ provider: 'anthropic', is_enabled: true })}>+ Add platform</button>
          </div>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
          Live cost is pulled where the platform has a cost API (Anthropic first); the rest use a flat monthly figure.
          Total ÷ active tenants = your <b>break-even per tenant</b> — set plans above it for margin.
        </div>
        {!pc.ready && <div style={{ color: '#b45309', fontSize: 13, marginBottom: 8 }}>⚠️ Run migration <code>090_platform_billing.sql</code> in Supabase to enable this.</div>}
        {pcMsg && <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>{pcMsg}</div>}
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
          {pc.connectors.length === 0 ? <div style={{ padding: 12, color: 'var(--text3)', fontSize: 13 }}>No platforms yet — add Anthropic, Railway, Supabase, Vercel, Bluehost…</div>
            : pc.connectors.map(c => {
              const live = providers.find(p => p.key === c.provider)?.live
              const sc = c.last_status === 'ok' ? '#16a34a' : c.last_status === 'manual' ? '#2563eb' : c.last_status === 'error' ? '#c0392b' : '#64748b'
              return (
                <div key={c.id} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '9px 12px', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 600, flex: 1, minWidth: 150 }}>{c.display_name || c.provider}
                    {!c.is_enabled && <span style={{ color: '#b45309', fontSize: 12 }}> · off</span>}
                    {live && <span style={{ fontSize: 11, color: '#16a34a', marginLeft: 6 }}>● live</span>}
                  </span>
                  <span style={{ width: 100, textAlign: 'right', fontWeight: 700 }}>{c.last_cost != null ? fmt(c.last_cost) : '—'}</span>
                  <span style={{ width: 110, fontSize: 12, color: sc }} title={c.last_detail || ''}>{c.last_status || 'not synced'}</span>
                  <span style={{ fontSize: 11, color: 'var(--text3)', width: 150 }}>{c.last_synced_at ? new Date(c.last_synced_at).toLocaleString() : ''}</span>
                  <button className="btn btn-sm" onClick={() => setPcEdit({ ...c, credential: '' })}>Edit</button>
                </div>
              )
            })}
        </div>
      </div>

      {/* Plan edit modal */}
      {editing && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }} onClick={() => setEditing(null)}>
          <div className="card" style={{ padding: 20, width: 420, maxWidth: '92vw' }} onClick={e => e.stopPropagation()}>
            <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 12 }}>
              Plan — {plans.find(p => p.org_id === editing)?.name}
            </div>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Basis</label>
            <select className="select" style={{ ...inp, width: '100%', marginBottom: 10 }} value={draft.basis || 'flat'} onChange={e => setDraft(d => ({ ...d, basis: e.target.value }))}>
              {BASES.map(b => <option key={b} value={b}>{basisLabel[b]}</option>)}
            </select>
            <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
              <div style={{ flex: 1 }}>
                <label style={{ fontSize: 12, color: 'var(--text3)' }}>Unit price</label>
                <input type="number" step="0.01" style={{ ...inp, width: '100%' }} value={draft.unit_price ?? 0} onChange={e => setDraft(d => ({ ...d, unit_price: parseFloat(e.target.value) || 0 }))} />
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ fontSize: 12, color: 'var(--text3)' }}>Cycle</label>
                <select className="select" style={{ ...inp, width: '100%' }} value={draft.cycle || 'monthly'} onChange={e => setDraft(d => ({ ...d, cycle: e.target.value }))}>
                  {CYCLES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <div style={{ width: 80 }}>
                <label style={{ fontSize: 12, color: 'var(--text3)' }}>Currency</label>
                <input style={{ ...inp, width: '100%' }} value={draft.currency || 'USD'} onChange={e => setDraft(d => ({ ...d, currency: e.target.value }))} />
              </div>
            </div>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Modules covered (comma-separated, blank = all)</label>
            <input style={{ ...inp, width: '100%', marginBottom: 10 }} placeholder="e.g. commissions, asset"
              value={(draft.modules || []).join(', ')}
              onChange={e => setDraft(d => ({ ...d, modules: e.target.value.split(',').map(s => s.trim()).filter(Boolean) }))} />
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginBottom: 10 }}>
              <input type="checkbox" checked={draft.is_active ?? true} onChange={e => setDraft(d => ({ ...d, is_active: e.target.checked }))} /> Plan active (counts toward MRR)
            </label>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>
              Live quantity for this basis: <b>{driverVal(plans.find(p => p.org_id === editing)!, draft.basis) ?? 1}</b>
              {' '}→ would bill <b>{fmt((driverVal(plans.find(p => p.org_id === editing)!, draft.basis) ?? 1) * (draft.unit_price || 0))}</b> / {draft.cycle || 'monthly'}
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between' }}>
              <button className="btn btn-sm" style={{ color: '#c0392b' }} onClick={() => deletePlan(editing)}>Delete plan</button>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-sm" onClick={() => setEditing(null)}>Cancel</button>
                <button className="btn btn-primary btn-sm" disabled={busy} onClick={savePlan}>{busy ? 'Saving…' : 'Save plan'}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Invoices panel */}
      {invFor && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }} onClick={() => setInvFor(null)}>
          <div className="card" style={{ padding: 20, width: 760, maxWidth: '94vw', maxHeight: '88vh', overflow: 'auto' }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div style={{ fontWeight: 700, fontSize: 16 }}>Invoices — {invFor.name}</div>
              <button className="btn btn-sm" onClick={() => setInvFor(null)}>Close</button>
            </div>
            <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div><label style={{ fontSize: 12, color: 'var(--text3)' }}>Period start</label><br />
                <input type="date" style={inp} value={period.period_start} onChange={e => setPeriod(p => ({ ...p, period_start: e.target.value }))} /></div>
              <div><label style={{ fontSize: 12, color: 'var(--text3)' }}>Period end</label><br />
                <input type="date" style={inp} value={period.period_end} onChange={e => setPeriod(p => ({ ...p, period_end: e.target.value }))} /></div>
              <div><label style={{ fontSize: 12, color: 'var(--text3)' }}>Due date</label><br />
                <input type="date" style={inp} value={period.due_date} onChange={e => setPeriod(p => ({ ...p, due_date: e.target.value }))} /></div>
              <button className="btn btn-primary" disabled={busy || !invFor.plan} onClick={generate}>{busy ? '…' : 'Generate invoice'}</button>
              {!invFor.plan && <span style={{ fontSize: 12, color: '#b45309' }}>Set a plan first.</span>}
            </div>
            {invoices.length === 0 ? <div style={{ color: 'var(--text3)', padding: 8 }}>No invoices yet.</div>
              : <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead><tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 12 }}>
                  <th style={{ padding: '6px 4px' }}>Period</th><th>Basis</th><th>Qty</th><th>Unit</th><th>Amount</th><th>Status</th><th>Ref</th><th></th>
                </tr></thead>
                <tbody>
                  {invoices.map(inv => (
                    <tr key={inv.id} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 4px' }}>{inv.period_start} → {inv.period_end}</td>
                      <td>{basisLabel[inv.basis] || inv.basis}</td>
                      <td>{inv.quantity}</td>
                      <td>{fmt(inv.unit_price)}</td>
                      <td style={{ fontWeight: 600 }}>{fmt(inv.amount)}</td>
                      <td><span style={{ color: statusColor[inv.status] || 'var(--text2)', fontWeight: 600 }}>{inv.status}</span></td>
                      <td style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text3)' }}>{inv.payment_ref || '—'}</td>
                      <td style={{ display: 'flex', gap: 4 }}>
                        {inv.status === 'draft' && <button className="btn btn-sm" onClick={() => setStatus(inv, 'sent')}>Send</button>}
                        {(inv.status === 'draft' || inv.status === 'sent') && <button className="btn btn-sm" onClick={() => setStatus(inv, 'paid')}>Paid</button>}
                        {inv.status !== 'void' && inv.status !== 'paid' && <button className="btn btn-sm" onClick={() => setStatus(inv, 'void')}>Void</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>}
          </div>
        </div>
      )}

      {/* Platform connector modal */}
      {pcEdit && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }} onClick={() => setPcEdit(null)}>
          <div className="card" style={{ padding: 20, width: 450, maxWidth: '92vw' }} onClick={e => e.stopPropagation()}>
            <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 12 }}>{pcEdit.id ? 'Edit platform' : 'Add platform'}</div>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Provider</label>
            <select className="select" style={{ ...inp, width: '100%', marginBottom: 6 }} value={pcEdit.provider || 'anthropic'} onChange={e => setPcEdit(d => ({ ...d!, provider: e.target.value }))}>
              {providers.map(p => <option key={p.key} value={p.key}>{p.label}{p.live ? ' · live' : ''}</option>)}
            </select>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>{providers.find(p => p.key === pcEdit.provider)?.hint}</div>
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Label</label>
            <input style={{ ...inp, width: '100%', marginBottom: 10 }} placeholder="e.g. Anthropic (Claude)" value={pcEdit.display_name || ''} onChange={e => setPcEdit(d => ({ ...d!, display_name: e.target.value }))} />
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>API key / token {pcEdit.has_credential ? '(blank = keep current)' : ''}</label>
            <input type="password" autoComplete="off" style={{ ...inp, width: '100%', marginBottom: 10 }} placeholder={pcEdit.has_credential ? '•••• stored' : 'paste token (stored server-side, never logged)'} value={pcEdit.credential || ''} onChange={e => setPcEdit(d => ({ ...d!, credential: e.target.value }))} />
            <label style={{ fontSize: 12, color: 'var(--text3)' }}>Flat monthly cost (for platforms with no cost API, or an override)</label>
            <input type="number" step="0.01" style={{ ...inp, width: '100%', marginBottom: 10 }} placeholder="e.g. 20" value={pcEdit.flat_monthly_cost ?? ''} onChange={e => setPcEdit(d => ({ ...d!, flat_monthly_cost: e.target.value === '' ? null : parseFloat(e.target.value) }))} />
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginBottom: 12 }}>
              <input type="checkbox" checked={pcEdit.is_enabled ?? true} onChange={e => setPcEdit(d => ({ ...d!, is_enabled: e.target.checked }))} /> Count in the total
            </label>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              {pcEdit.id ? <button className="btn btn-sm" style={{ color: '#c0392b' }} onClick={() => deleteConnector(pcEdit.id)}>Delete</button> : <span />}
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-sm" onClick={() => setPcEdit(null)}>Cancel</button>
                <button className="btn btn-primary btn-sm" disabled={pcBusy} onClick={saveConnector}>{pcBusy ? 'Saving…' : 'Save'}</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
