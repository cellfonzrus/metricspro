'use client'

// POS module — Phase 2: Activations Manager (ported from the standalone pos-system app's
// app/activations/page.tsx; data access rewired from direct Supabase to the FastAPI /pos router).
//
// Intended behavior changes vs the standalone source:
//   * Identity: stores are store_code TEXT, employees are employee_id TEXT. The BACKEND stamps
//     employee_id on create (403 when the login isn't linked) and preserves the original
//     attribution on edits (employee_id is not writable via PATCH) — so the source's client-side
//     employee resolution/blockers are gone. store_code is still stamped from the active store
//     on create.
//   * Cancelling (status → 'cancelled') is enforced SERVER-side via the pos_activations_cancel
//     permission — the option stays enabled and a 403 on save is surfaced instead.
//   * The server mirrors cell_number → mobile_phone and coerces empty uuid/date strings to null.

import { useEffect, useState } from 'react'
import { api, addDays, localToday } from '@/lib/client'
import { apiCached } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import { getActiveStore, setActiveStore } from '@/lib/pos-store'
import { PosConfigValues, loadEffectivePosConfig, resolvePosConfig } from '@/lib/pos-config'

interface Activation {
  id: string; activation_number: number; customer_id: string | null
  sale_id: string | null; store_code: string | null; employee_id: string | null
  service_plan_id: string | null
  carrier: string | null; activation_date: string | null; service_plan_date: string | null
  dealer_code: string | null; contract_type: string | null; contract_terms: string | null
  monthly_fee: number | null; included_minutes: number | null; plan_code: string | null
  plan_description: string | null; service_area: string | null
  phone_serial: string | null; phone_model: string | null; sim_card: string | null
  mobile_phone: string | null; cell_number: string | null; account_number: string | null
  deposit_amount: number | null
  memo: string | null; description: string | null; status: string
  promotion_offered?: string | null; trade_in_credit?: number | null; special_promo?: string | null
  customer_name?: string | null; customer_cust_number?: number | null
}

interface TradeInRow {
  id: string; device_description: string | null; serial_number: string | null
  imei: string | null; notes: string | null; credit_amount: number | null
}

interface ActivationDetail extends Activation {
  trade_in: TradeInRow | null
  sale_transaction_id?: number | null
}

interface Customer { id: string; cust_number: number; first_name: string | null; last_name: string | null; company_name: string | null }

interface ActNote { id: string; note: string; created_at: string; severity: string; employee_id: string | null }

interface DealerCode { id: string; code: string; carrier: string | null; store_code: string | null; is_active: boolean }

interface CarrierPortal { carrier: string; url: string; is_active: boolean; sort_order: number }

interface ServicePlan {
  id: string; carrier: string; plan_code: string | null; plan_name: string
  plan_description: string | null; monthly_fee: number | null
  contract_type: string | null; contract_terms: string | null
}

interface Store { store_code: string; address?: string | null }

const NOTE_SEVERITIES = ['normal', 'important', 'urgent'] as const
const SEVERITY_COLORS: Record<string, string> = { normal: '#6b7280', important: '#f39c12', urgent: '#e74c3c' }
const EMPTY_TRADE_IN = { device_description: '', serial_number: '', imei: '', notes: '' }

const CARRIERS = ['Verizon', 'AT&T', 'T-Mobile', 'Sprint', 'Boost', 'Cricket', 'MetroPCS', 'Tracfone', 'GCI', 'Other']
const CONTRACT_TYPES = ['New Activation', 'Upgrade', 'Port-In', 'Add-a-Line', 'Prepaid', 'Business']
const CONTRACT_TERMS = ['Month-to-Month', '12 months', '24 months', '30']
const FORM_TABS = ['Service Plan & Equipment', 'Plan Options', 'Promotions & Trade-in', 'Billing Address', 'Additional Info']

// Function (not a module-level constant) so the default dates are fresh each
// time the form opens instead of frozen at first page load.
function makeEmptyForm() {
  const today = localToday()
  return {
    carrier: 'Verizon', activation_date: today, service_plan_date: today, dealer_code: '',
    contract_type: '', contract_terms: '', monthly_fee: 0, included_minutes: 0,
    plan_code: '', plan_description: '', service_area: '', phone_serial: '', phone_model: '',
    sim_card: '', cell_number: '', account_number: '', deposit_amount: 0,
    memo: '', description: '', status: 'active',
    promotion_offered: '', trade_in_credit: 0, special_promo: '',
    service_plan_id: '',
  }
}

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const cell: React.CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const panel: React.CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }
const modalOverlay: React.CSSProperties = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }

function custName(c: { first_name?: string | null; last_name?: string | null; company_name?: string | null }): string {
  return `${c.first_name || ''} ${c.last_name || ''}`.trim() || c.company_name || ''
}

export default function PosActivationsPage() {
  const { user } = useAuth()

  const [activations, setActivations] = useState<Activation[]>([])
  const [customers, setCustomers] = useState<Customer[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [selected, setSelected] = useState<Activation | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [formData, setFormData] = useState(makeEmptyForm())
  const [formTab, setFormTab] = useState('Service Plan & Equipment')
  const [saving, setSaving] = useState(false)
  const [custSearch, setCustSearch] = useState('')
  const [showCustPicker, setShowCustPicker] = useState(false)
  const [selectedCust, setSelectedCust] = useState<Customer | null>(null)
  // The customer_id the activation had when the edit form opened. Preserved on save unless the
  // user explicitly attaches a different customer or explicitly removes it — a list row whose
  // customer_name failed to resolve (deleted/missing customer) must NOT silently detach.
  const [editingOriginalCustomerId, setEditingOriginalCustomerId] = useState<string | null>(null)
  const [filterCarrier, setFilterCarrier] = useState('')
  // Initialized empty and filled on mount so server render and client
  // hydration produce identical markup (no new Date() during initial render).
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [notes, setNotes] = useState<ActNote[]>([])
  const [notesError, setNotesError] = useState('')
  const [noteText, setNoteText] = useState('')
  const [noteSeverity, setNoteSeverity] = useState('normal')
  const [savingNote, setSavingNote] = useState(false)
  // Attribution: the device's active store (localStorage) — read in an effect so SSR matches.
  const [stores, setStores] = useState<Store[]>([])
  const [activeStore, setActiveStoreState] = useState<string | null>(null)
  const [dealerCodes, setDealerCodes] = useState<DealerCode[]>([])
  const [servicePlans, setServicePlans] = useState<ServicePlan[]>([])
  // Effective POS config for the active store (activation rules). Starts from the
  // code-side defaults so nothing flashes before the effective config loads.
  const [cfg, setCfg] = useState<PosConfigValues>(() => resolvePosConfig([], null).values)
  // Standalone credit-check quick links (carrier portals popover).
  const [showCreditCheck, setShowCreditCheck] = useState(false)
  const [portals, setPortals] = useState<CarrierPortal[] | null>(null)
  // Sale linkage (?sale=<id> deep link, or loaded from an existing activation).
  const [saleId, setSaleId] = useState<string | null>(null)
  const [saleTxn, setSaleTxn] = useState<number | null>(null)
  // At most one trade_ins row linked to the open activation (server upserts by activation id).
  const [hadTradeIn, setHadTradeIn] = useState(false)
  const [tradeIn, setTradeIn] = useState({ ...EMPTY_TRADE_IN })

  useEffect(() => {
    const from = addDays(localToday(), -30)
    const to = localToday()
    setDateFrom(from); setDateTo(to)
    load(from, to)
    setActiveStoreState(getActiveStore())
    apiCached('/api/v1/storeops/stores').then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
    loadDealerCodes()
    loadServicePlans()
    // Deep links: /pos/activations?sale=<id> (from POS) and ?customer=<id> (from Customers).
    const params = new URLSearchParams(window.location.search)
    const saleParam = params.get('sale')
    const custId = params.get('customer')
    if (saleParam) prefillSale(saleParam)
    else if (custId) prefillCustomer(custId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // No store chosen on this device yet → fall back to the login's own store grant, then the
  // first store (fallback is not persisted; only an explicit pick is).
  useEffect(() => {
    if (activeStore) return
    const fallback = user?.store_code || stores[0]?.store_code || null
    if (fallback) setActiveStoreState(fallback)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, stores])

  function pickStore(code: string) {
    setActiveStore(code || null)
    setActiveStoreState(code || null)
  }

  // Reload the effective config whenever the active store changes.
  useEffect(() => {
    let cancelled = false
    loadEffectivePosConfig(activeStore).then(values => { if (!cancelled) setCfg(values) })
    return () => { cancelled = true }
  }, [activeStore])

  async function toggleCreditCheck() {
    const next = !showCreditCheck
    setShowCreditCheck(next)
    if (next && portals === null) {
      // On error the list stays empty, which shows the "none configured" hint.
      try {
        const r = await api('/api/v1/pos/carrier-portals')
        setPortals(((r.carrier_portals || []) as CarrierPortal[]).filter(p => p.is_active))
      } catch { setPortals([]) }
    }
  }

  async function loadDealerCodes() {
    // On error we leave the list empty, which falls back to the free-text input.
    try {
      const r = await api('/api/v1/pos/dealer-codes')
      setDealerCodes(((r.dealer_codes || []) as DealerCode[]).filter(d => d.is_active !== false))
    } catch { setDealerCodes([]) }
  }

  async function loadServicePlans() {
    try {
      const r = await api('/api/v1/pos/service-plans')
      setServicePlans(r.service_plans || [])
    } catch { setServicePlans([]) }
  }

  async function load(from?: string, to?: string) {
    setLoading(true)
    setLoadError('')
    const f = from ?? dateFrom, t = to ?? dateTo
    try {
      const params = new URLSearchParams()
      if (f) params.set('date_from', f)
      if (t) params.set('date_to', t)
      const r = await api(`/api/v1/pos/activations?${params}`)
      setActivations(r.activations || [])
    } catch (err: any) {
      setLoadError(`Failed to load activations: ${err?.message || err}`)
      setActivations([])
    }
    setLoading(false)
  }

  function resetFormExtras() {
    setNotes([]); setNotesError(''); setNoteText(''); setNoteSeverity('normal')
    setSaleId(null); setSaleTxn(null)
    setEditingOriginalCustomerId(null)
    setHadTradeIn(false); setTradeIn({ ...EMPTY_TRADE_IN })
  }

  // Deep-link customer attach: single-customer fetch (404s when missing) — the list endpoint
  // caps at the newest 300 rows, which silently missed older customers.
  async function findCustomerById(id: string): Promise<Customer | null> {
    try {
      const r = await api(`/api/v1/pos/customers/${id}`)
      return (r.customer as Customer) || null
    } catch { return null }
  }

  async function prefillCustomer(id: string) {
    const cust = await findCustomerById(id)
    if (cust) {
      setFormData(makeEmptyForm())
      setSelectedCust(cust)
      setEditingId(null)
      resetFormExtras()
      setFormTab('Service Plan & Equipment')
      setShowForm(true)
    }
  }

  async function prefillSale(id: string) {
    let sale: { id: string; transaction_id: number; customer_id: string | null } | null = null
    try { sale = (await api(`/api/v1/pos/sales/${id}`)).sale || null } catch { sale = null }
    if (!sale) return
    setFormData(makeEmptyForm())
    setSelectedCust(null)
    setEditingId(null)
    resetFormExtras()
    setSaleId(sale.id)
    setSaleTxn(sale.transaction_id)
    if (sale.customer_id) {
      const cust = await findCustomerById(sale.customer_id)
      if (cust) setSelectedCust(cust)
    }
    setFormTab('Service Plan & Equipment')
    setShowForm(true)
  }

  async function searchCustomers() {
    try {
      const r = await api(`/api/v1/pos/customers?search=${encodeURIComponent(custSearch)}`)
      setCustomers(r.customers || [])
    } catch (err: any) { alert(`Customer search failed: ${err?.message || err}`) }
  }

  function openNew() {
    setFormData(makeEmptyForm())
    setSelectedCust(null)
    setEditingId(null)
    resetFormExtras()
    setFormTab('Service Plan & Equipment')
    setShowForm(true)
  }

  function openEdit(a: Activation) {
    setSelected(a)
    setEditingId(a.id)
    setFormData({
      carrier: a.carrier || 'Verizon',
      activation_date: a.activation_date || '',
      service_plan_date: a.service_plan_date || '',
      dealer_code: a.dealer_code || '',
      contract_type: a.contract_type || '',
      contract_terms: a.contract_terms || '',
      monthly_fee: a.monthly_fee ?? 0,
      included_minutes: a.included_minutes ?? 0,
      plan_code: a.plan_code || '',
      plan_description: a.plan_description || '',
      service_area: a.service_area || '',
      phone_serial: a.phone_serial || '',
      phone_model: a.phone_model || '',
      sim_card: a.sim_card || '',
      // Cell and mobile are the same per the owner — prefer cell_number, fall back to mobile_phone.
      cell_number: a.cell_number || a.mobile_phone || '',
      account_number: a.account_number || '',
      deposit_amount: a.deposit_amount ?? 0,
      memo: a.memo || '',
      description: a.description || '',
      status: a.status || 'active',
      promotion_offered: a.promotion_offered || '',
      trade_in_credit: a.trade_in_credit ?? 0,
      special_promo: a.special_promo || '',
      service_plan_id: a.service_plan_id || '',
    })
    setSelectedCust(a.customer_id && a.customer_name != null ? {
      id: a.customer_id, cust_number: a.customer_cust_number ?? 0,
      first_name: a.customer_name, last_name: null, company_name: null,
    } : null)
    resetFormExtras()
    setEditingOriginalCustomerId(a.customer_id || null)
    setSaleId(a.sale_id || null)
    setFormTab('Service Plan & Equipment')
    setShowForm(true)
    loadNotes(a.id)
    // Detail fetch fills in what the list row doesn't carry: the linked trade-in
    // and the linked sale's transaction number.
    api(`/api/v1/pos/activations/${a.id}`).then(r => {
      const det = (r.activation || null) as ActivationDetail | null
      if (!det) return
      if (det.sale_transaction_id != null) setSaleTxn(det.sale_transaction_id)
      if (det.trade_in) {
        setHadTradeIn(true)
        setTradeIn({
          device_description: det.trade_in.device_description || '',
          serial_number: det.trade_in.serial_number || '',
          imei: det.trade_in.imei || '',
          notes: det.trade_in.notes || '',
        })
      }
    }).catch(() => {})
  }

  function closeForm() {
    setShowForm(false)
    setEditingId(null)
  }

  function applyServicePlan(planId: string) {
    if (!planId) { setFormData(f => ({ ...f, service_plan_id: '' })); return }
    const p = servicePlans.find(sp => sp.id === planId)
    if (!p) return
    // Auto-fill from the catalog; every field stays editable afterwards.
    setFormData(f => ({
      ...f,
      service_plan_id: planId,
      plan_code: p.plan_code || '',
      plan_description: p.plan_description || '',
      monthly_fee: p.monthly_fee ?? 0,
      contract_type: p.contract_type || '',
      contract_terms: p.contract_terms || '',
    }))
  }

  async function save() {
    if (saving) return
    // Attribution: the backend stamps employee_id from the login on create and preserves it on
    // edits. store_code: new records take the active store; edits keep the original store.
    if (!editingId && !activeStore) {
      alert('No active store selected. Choose a store from the store selector in the header.')
      return
    }
    const credit = formData.trade_in_credit || 0
    if (credit > 0 && !tradeIn.device_description.trim()) {
      setFormTab('Promotions & Trade-in')
      alert('Trade-in device description is required when a trade-in credit is entered.')
      return
    }
    // Dealer-code warning (activation_dealer_code_warning, default on): new
    // activations without a code, or edits that clear a previously saved code.
    const noDealerCode = !formData.dealer_code.trim()
    const clearingDealerCode = !!editingId && !!selected?.dealer_code && noDealerCode
    if ((cfg.activation_dealer_code_warning ?? true) !== false && noDealerCode && (!editingId || clearingDealerCode)) {
      if (!confirm('No dealer code is selected for this activation — commissions may not attribute correctly. Save anyway?')) return
    }
    setSaving(true)
    // Customer linkage: an unresolved selectedCust (e.g. the list row's customer_name came back
    // null) must not detach the original customer — only an explicit attach/remove changes it.
    const resolvedCustomerId = selectedCust?.id ?? editingOriginalCustomerId ?? null
    // The server coerces empty uuid/date strings to null and mirrors cell_number → mobile_phone.
    const payload: Record<string, unknown> = {
      ...formData,
      customer_id: resolvedCustomerId,
      sale_id: saleId,
    }
    if (!editingId) payload.store_code = activeStore
    let activationId = editingId
    try {
      if (editingId) {
        await api(`/api/v1/pos/activations/${editingId}`, { method: 'PATCH', body: JSON.stringify(payload) })
      } else {
        const r = await api('/api/v1/pos/activations', { method: 'POST', body: JSON.stringify(payload) })
        activationId = r.activation?.id ?? null
      }
    } catch (err: any) {
      setSaving(false)
      const msg = String(err?.message || err)
      // 403s: cancel permission, or a login not linked to an employee record (create only).
      if (msg.includes('pos_activations_cancel')) alert('Cancelling requires the activations-cancel permission.')
      else alert(`Failed to save activation: ${msg}`)
      return
    }
    // Trade-in: keep exactly one linked trade_ins row in sync with the credit
    // (PUT upserts; removing the credit zeroes the amount, never deletes).
    let tradeErr: string | null = null
    if (activationId && (credit > 0 || hadTradeIn)) {
      try {
        await api(`/api/v1/pos/activations/${activationId}/trade-in`, {
          method: 'PUT',
          body: JSON.stringify({
            device_description: tradeIn.device_description.trim(),
            serial_number: tradeIn.serial_number.trim() || null,
            imei: tradeIn.imei.trim() || null,
            notes: tradeIn.notes.trim() || null,
            credit_amount: credit > 0 ? credit : 0,
            customer_id: resolvedCustomerId,
            sale_id: saleId,
          }),
        })
      } catch (err: any) { tradeErr = String(err?.message || err) }
    }
    setSaving(false)
    if (tradeErr) alert(`Activation saved, but the trade-in could not be saved: ${tradeErr}. Reopen the activation to retry.`)
    closeForm()
    load()
  }

  async function loadNotes(activationId: string) {
    try {
      const r = await api(`/api/v1/pos/activations/${activationId}/notes`)
      setNotesError(''); setNotes(r.notes || [])
    } catch (err: any) {
      setNotesError(`Notes unavailable: ${err?.message || err}`); setNotes([])
    }
  }

  async function addNote() {
    if (!editingId || !noteText.trim() || savingNote) return
    setSavingNote(true)
    try {
      // The author is stamped server-side from the login.
      await api(`/api/v1/pos/activations/${editingId}/notes`, {
        method: 'POST',
        body: JSON.stringify({ note: noteText.trim(), severity: noteSeverity }),
      })
      setNoteText('')
      setNoteSeverity('normal')
      loadNotes(editingId)
    } catch (err: any) { alert(`Failed to add note: ${err?.message || err}`) }
    setSavingNote(false)
  }

  const filtered = activations.filter(a => (!filterCarrier || a.carrier === filterCarrier))
  // Dealer codes for the active store: store-specific ones plus org-wide (null store).
  const visibleDealerCodes = dealerCodes.filter(dc => !dc.store_code || dc.store_code === activeStore)
  const planCarriers = Array.from(new Set(servicePlans.map(p => p.carrier)))

  return (
    <div>
      {/* Header — title, store selector, credit check, actions */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📡 Activations Manager</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Carrier activations · {filtered.length} records{selected ? ` · selected: #${selected.activation_number}` : ''}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>🏪 Store:</label>
          <select value={activeStore || ''} onChange={e => pickStore(e.target.value)} style={{ ...input, width: 170 }}>
            <option value="">— select store —</option>
            {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.store_code}</option>)}
          </select>
          {cfg.credit_check_standalone === true && (
            <div style={{ position: 'relative' }}>
              <button className="btn btn-secondary" onClick={toggleCreditCheck}>🔎 Credit Check</button>
              {showCreditCheck && (
                <div style={{ position: 'absolute', top: 'calc(100% + 8px)', right: 0, width: 260, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: 6, boxShadow: '0 8px 24px rgba(0,0,0,0.35)', zIndex: 150 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text3)', padding: '6px 10px', textTransform: 'uppercase', letterSpacing: '0.4px' }}>Carrier Portals</div>
                  {portals === null ? (
                    <div style={{ padding: '8px 10px', fontSize: 12, color: 'var(--text3)' }}>Loading…</div>
                  ) : portals.length === 0 ? (
                    <div style={{ padding: '8px 10px', fontSize: 12, color: 'var(--text3)' }}>No carrier portals configured — add them in Settings → Carrier Portals.</div>
                  ) : portals.map(p => (
                    <a key={p.carrier} href={p.url} target="_blank" rel="noopener noreferrer"
                      style={{ display: 'block', padding: '7px 10px', fontSize: 12, color: '#3498db', textDecoration: 'none', borderRadius: 5 }}
                      onMouseEnter={e => (e.currentTarget as HTMLAnchorElement).style.background = 'var(--surface2)'}
                      onMouseLeave={e => (e.currentTarget as HTMLAnchorElement).style.background = 'transparent'}>
                      {p.carrier} ↗
                    </a>
                  ))}
                </div>
              )}
            </div>
          )}
          <button className="btn btn-primary" onClick={openNew}>+ New Activation</button>
          {selected && <button className="btn btn-secondary" onClick={() => openEdit(selected)}>View/Edit</button>}
        </div>
      </div>

      {/* Filters */}
      <div style={{ ...panel, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={filterCarrier} onChange={e => setFilterCarrier(e.target.value)} style={{ ...input, width: 160 }}>
          <option value="">All Carriers</option>
          {CARRIERS.map(c => <option key={c}>{c}</option>)}
        </select>
        <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ ...input, width: 140 }} />
        <span style={{ color: 'var(--text3)', fontSize: 12 }}>to</span>
        <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ ...input, width: 140 }} />
        <button className="btn btn-primary" onClick={() => load()}>Search</button>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text3)' }}>Records: {filtered.length}</span>
      </div>

      {loadError && (
        <div style={{ ...panel, borderColor: '#e74c3c', color: '#dc2626', marginBottom: 14, fontSize: 12 }}>{loadError}</div>
      )}

      {/* Stats bar */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 10, marginBottom: 14 }}>
        {[
          { label: 'Total Activations', value: activations.length, color: '#3498db' },
          { label: 'Verizon', value: activations.filter(a => a.carrier === 'Verizon').length, color: '#e74c3c' },
          { label: 'Active', value: activations.filter(a => a.status === 'active').length, color: '#27ae60' },
          { label: 'Monthly Revenue', value: `$${activations.filter(a => a.status === 'active').reduce((s, a) => s + (Number(a.monthly_fee) || 0), 0).toFixed(0)}`, color: '#e67e22' },
        ].map((s, i) => (
          <div key={i} style={{ ...panel, padding: 12, borderTop: `3px solid ${s.color}` }}>
            <div style={{ fontSize: 20, fontWeight: 700 }}>{s.value}</div>
            <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Activations table — click a row to open it for editing */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="table-wrapper" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1200, fontSize: 13 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Activation #', 'Customer', 'Carrier', 'Activation Date', 'Serv. Plan Date', 'Cell Number', 'Monthly Fee', 'Inc. Min', 'Plan Description', 'Plan Code', 'Service Area', 'Phone Serial', 'Promotion', 'Status'].map(h =>
                <th key={h} style={{ textAlign: 'left', padding: 8, fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {filtered.map(a => (
                <tr key={a.id} onClick={() => openEdit(a)}
                  style={{ cursor: 'pointer', background: selected?.id === a.id ? 'var(--surface2)' : 'transparent' }}>
                  <td style={{ ...cell, color: '#3498db', fontWeight: 600 }}>{a.activation_number}</td>
                  <td style={{ ...cell, fontWeight: 500 }}>{a.customer_name || '—'}</td>
                  <td style={cell}>{a.carrier || '—'}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{a.activation_date ? new Date(a.activation_date).toLocaleDateString() : '—'}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{a.service_plan_date ? new Date(a.service_plan_date).toLocaleDateString() : '—'}</td>
                  <td style={cell}>{a.cell_number || a.mobile_phone || '—'}</td>
                  <td style={{ ...cell, color: '#27ae60', fontWeight: 600 }}>{a.monthly_fee ? `$${a.monthly_fee}` : '—'}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{a.included_minutes || '—'}</td>
                  <td style={{ ...cell, color: 'var(--text2)', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.plan_description || '—'}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{a.plan_code || '—'}</td>
                  <td style={{ ...cell, color: 'var(--text2)' }}>{a.service_area || '—'}</td>
                  <td style={{ ...cell, color: 'var(--text2)', fontFamily: 'monospace' }}>{a.phone_serial || '—'}</td>
                  <td style={{ ...cell, color: 'var(--text2)', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.promotion_offered || '—'}</td>
                  <td style={cell}>
                    <span style={{ color: a.status === 'active' ? '#27ae60' : '#dc2626', fontWeight: 600, textTransform: 'capitalize' }}>{a.status}</span>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan={14} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  No activations found. Click “+ New Activation” to add one.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* ACTIVATION FORM (create + edit) */}
      {showForm && (
        <div style={modalOverlay}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 780, maxHeight: '92vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            {/* Header */}
            <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--surface2)' }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 700 }}>
                  {editingId ? `Edit Activation #${selected?.activation_number ?? ''}` : 'New Activation'} — {selectedCust ? custName(selectedCust) : 'Select Customer'}
                </div>
                <div style={{ display: 'flex', gap: 16, marginTop: 4, fontSize: 12, color: 'var(--text2)' }}>
                  <span>Account #: <input value={formData.account_number} onChange={e => setFormData(f => ({ ...f, account_number: e.target.value }))} style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, padding: '2px 6px', color: 'var(--text)', fontSize: 12, width: 120 }} /></span>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input type="date" value={formData.activation_date} onChange={e => setFormData(f => ({ ...f, activation_date: e.target.value }))} style={{ ...input, width: 140, fontSize: 12 }} />
                <button onClick={closeForm} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
              </div>
            </div>

            {/* Customer selector */}
            <div style={{ padding: '10px 20px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 10, alignItems: 'center' }}>
              <span style={{ fontSize: 12, color: 'var(--text2)' }}>Customer:</span>
              {selectedCust ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{custName(selectedCust)}{selectedCust.cust_number ? ` (#${selectedCust.cust_number})` : ''}</span>
                  {/* Explicit remove: clears the preserved original too, so save() really detaches */}
                  <button onClick={() => { setSelectedCust(null); setEditingOriginalCustomerId(null) }} style={{ background: 'none', border: 'none', color: '#dc2626', cursor: 'pointer' }}>×</button>
                </div>
              ) : (
                <button className="btn btn-secondary" style={{ color: '#3498db' }} onClick={() => setShowCustPicker(true)}>+ Select Customer</button>
              )}
              {saleId && (
                <span style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 600, background: 'var(--surface2)', color: '#3498db', border: '1px solid #2980b9', borderRadius: 10, padding: '3px 10px', whiteSpace: 'nowrap' }}>
                  Linked to sale #{saleTxn ?? `${saleId.slice(0, 8)}…`}
                </span>
              )}
            </div>

            {/* Tabs */}
            <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'var(--surface2)', overflowX: 'auto' }}>
              {FORM_TABS.map(t => (
                <button key={t} onClick={() => setFormTab(t)}
                  style={{ padding: '9px 16px', fontSize: 12, fontWeight: formTab === t ? 700 : 400, color: formTab === t ? 'var(--text)' : 'var(--text2)', background: formTab === t ? 'var(--surface)' : 'transparent', border: 'none', borderBottom: formTab === t ? '2px solid #3498db' : '2px solid transparent', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                  {t}
                </button>
              ))}
            </div>

            <div style={{ padding: 20, overflowY: 'auto', flex: 1, minWidth: 0 }}>
              {formTab === 'Service Plan & Equipment' && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <div><label style={label}>Carrier</label>
                      <select value={formData.carrier} onChange={e => setFormData(f => ({ ...f, carrier: e.target.value }))} style={input}>
                        {CARRIERS.map(c => <option key={c}>{c}</option>)}
                      </select>
                    </div>
                    <div><label style={label}>Service Plan</label>
                      {servicePlans.length > 0 ? (
                        <select value={formData.service_plan_id} onChange={e => applyServicePlan(e.target.value)} style={input}>
                          <option value="">Select a plan (optional)</option>
                          {planCarriers.map(c => (
                            <optgroup key={c} label={c}>
                              {servicePlans.filter(p => p.carrier === c).map(p => (
                                <option key={p.id} value={p.id}>{p.plan_name}{p.monthly_fee != null ? ` — $${p.monthly_fee}/mo` : ''}</option>
                              ))}
                            </optgroup>
                          ))}
                        </select>
                      ) : (
                        <select disabled style={{ ...input, opacity: 0.6, cursor: 'not-allowed' }}>
                          <option>No service plans configured yet</option>
                        </select>
                      )}
                    </div>
                    <div><label style={label}>Service Plan Date</label><input type="date" value={formData.service_plan_date} onChange={e => setFormData(f => ({ ...f, service_plan_date: e.target.value }))} style={input} /></div>
                    <div><label style={label}>Dealer Code</label>
                      {visibleDealerCodes.length > 0 ? (
                        <select value={formData.dealer_code} onChange={e => setFormData(f => ({ ...f, dealer_code: e.target.value }))} style={input}>
                          <option value="">&lt;None&gt;</option>
                          {/* Keep a value saved before the managed list changed selectable */}
                          {formData.dealer_code && !visibleDealerCodes.some(d => d.code === formData.dealer_code) && (
                            <option value={formData.dealer_code}>{formData.dealer_code}</option>
                          )}
                          {visibleDealerCodes.map(d => (
                            <option key={d.id} value={d.code}>{d.code}{d.carrier ? ` — ${d.carrier}` : ''}</option>
                          ))}
                        </select>
                      ) : (
                        <>
                          <input value={formData.dealer_code} onChange={e => setFormData(f => ({ ...f, dealer_code: e.target.value }))} style={input} placeholder="Dealer code" />
                          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>No dealer codes for this store — manage dealer codes in Settings</div>
                        </>
                      )}
                    </div>
                    <div><label style={label}>Contract Type</label>
                      <select value={formData.contract_type} onChange={e => setFormData(f => ({ ...f, contract_type: e.target.value }))} style={input}>
                        <option value="">Select Type</option>
                        {CONTRACT_TYPES.map(c => <option key={c}>{c}</option>)}
                      </select>
                    </div>
                    <div><label style={label}>Contract Terms</label>
                      <select value={formData.contract_terms} onChange={e => setFormData(f => ({ ...f, contract_terms: e.target.value }))} style={input}>
                        <option value="">Select</option>
                        {CONTRACT_TERMS.map(c => <option key={c}>{c}</option>)}
                      </select>
                    </div>
                    <div><label style={label}>Monthly Fee</label><input type="number" step="0.01" value={formData.monthly_fee} onChange={e => setFormData(f => ({ ...f, monthly_fee: parseFloat(e.target.value) || 0 }))} style={input} /></div>
                    <div><label style={label}>Included Minutes</label><input type="number" value={formData.included_minutes} onChange={e => setFormData(f => ({ ...f, included_minutes: parseInt(e.target.value) || 0 }))} style={input} /></div>
                    <div><label style={label}>Plan Code</label><input value={formData.plan_code} onChange={e => setFormData(f => ({ ...f, plan_code: e.target.value }))} style={input} placeholder="e.g. LOYALTY55" /></div>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <div style={panel}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Plan Description:</div>
                      <textarea value={formData.plan_description} onChange={e => setFormData(f => ({ ...f, plan_description: e.target.value }))} style={{ ...input, height: 80, resize: 'none' }} placeholder="e.g. $55 LOYALTY PLAN — unlimited talk & text" />
                    </div>
                    <div><label style={label}>Phone Serial #</label><input value={formData.phone_serial} onChange={e => setFormData(f => ({ ...f, phone_serial: e.target.value }))} style={input} placeholder="Scan IMEI/Serial" /></div>
                    <div><label style={label}>Phone Model</label><input value={formData.phone_model} onChange={e => setFormData(f => ({ ...f, phone_model: e.target.value }))} style={input} placeholder="e.g. apple iphone 14 128GB midnight" /></div>
                    <div><label style={label}>SIM Card</label><input value={formData.sim_card} onChange={e => setFormData(f => ({ ...f, sim_card: e.target.value }))} style={input} /></div>
                    {/* Cell and mobile are the same per the owner — one field, mirrored server-side */}
                    <div><label style={label}>Cell Number</label><input value={formData.cell_number} onChange={e => setFormData(f => ({ ...f, cell_number: e.target.value }))} style={input} placeholder="(___) ___-____" /></div>
                  </div>
                </div>
              )}
              {formTab === 'Plan Options' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div><label style={label}>Flex-Pay / Deposit Amount</label><input type="number" step="0.01" value={formData.deposit_amount} onChange={e => setFormData(f => ({ ...f, deposit_amount: parseFloat(e.target.value) || 0 }))} style={input} /></div>
                    <div><label style={label}>Service Area</label><input value={formData.service_area} onChange={e => setFormData(f => ({ ...f, service_area: e.target.value }))} style={input} /></div>
                  </div>
                  <div><label style={label}>Memo</label><input value={formData.memo} onChange={e => setFormData(f => ({ ...f, memo: e.target.value }))} style={input} /></div>
                  <div><label style={label}>Description</label><textarea value={formData.description} onChange={e => setFormData(f => ({ ...f, description: e.target.value }))} style={{ ...input, height: 80, resize: 'none' }} /></div>
                </div>
              )}
              {formTab === 'Promotions & Trade-in' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  <div style={panel}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 10 }}>Promotions &amp; Trade-in</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 180px', gap: 10 }}>
                      <div><label style={label}>Promotion Offered</label><input value={formData.promotion_offered} onChange={e => setFormData(f => ({ ...f, promotion_offered: e.target.value }))} style={input} placeholder="e.g. BOGO iPhone 14, $200 gift card" /></div>
                      <div><label style={label}>Trade-in Credit ($)</label><input type="number" step="0.01" value={formData.trade_in_credit} onChange={e => setFormData(f => ({ ...f, trade_in_credit: parseFloat(e.target.value) || 0 }))} style={input} /></div>
                    </div>
                    <div style={{ marginTop: 10 }}><label style={label}>Special Promo</label><input value={formData.special_promo} onChange={e => setFormData(f => ({ ...f, special_promo: e.target.value }))} style={input} placeholder="e.g. holiday port-in credit, waived activation fee" /></div>
                    {/* Trade-in device capture — one linked trade_ins row, revealed when a credit is entered */}
                    {(formData.trade_in_credit || 0) > 0 && (
                      <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 8 }}>
                          Trade-in Device{hadTradeIn ? ' (linked)' : ''}
                        </div>
                        <div style={{ marginBottom: 10 }}>
                          <label style={label}>Device Description *</label>
                          <input value={tradeIn.device_description} onChange={e => setTradeIn(t => ({ ...t, device_description: e.target.value }))} style={input} placeholder="e.g. iPhone 12 64GB black, cracked screen" />
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
                          <div><label style={label}>Serial Number</label><input value={tradeIn.serial_number} onChange={e => setTradeIn(t => ({ ...t, serial_number: e.target.value }))} style={input} /></div>
                          <div><label style={label}>IMEI</label><input value={tradeIn.imei} onChange={e => setTradeIn(t => ({ ...t, imei: e.target.value }))} style={input} /></div>
                        </div>
                        <div><label style={label}>Trade-in Notes</label><input value={tradeIn.notes} onChange={e => setTradeIn(t => ({ ...t, notes: e.target.value }))} style={input} placeholder="condition, accessories included..." /></div>
                      </div>
                    )}
                  </div>

                  {/* Notes trail — available once the activation exists */}
                  {editingId ? (
                    <div style={panel}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 10 }}>Notes ({notes.length})</div>
                      <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
                        <input value={noteText} onChange={e => setNoteText(e.target.value)} onKeyDown={e => e.key === 'Enter' && addNote()} placeholder="Add a note (promo details, follow-ups, port-in status)..." style={{ ...input, flex: 1 }} />
                        <select value={noteSeverity} onChange={e => setNoteSeverity(e.target.value)} title="Note severity" style={{ ...input, width: 110 }}>
                          {NOTE_SEVERITIES.map(s => <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>)}
                        </select>
                        <button className="btn btn-primary" disabled={savingNote} onClick={addNote} style={{ whiteSpace: 'nowrap' }}>{savingNote ? 'Adding…' : 'Add Note'}</button>
                      </div>
                      {notesError && <div style={{ color: '#dc2626', fontSize: 12, marginBottom: 8 }}>{notesError}</div>}
                      {!notesError && notes.length === 0 && <div style={{ color: 'var(--text3)', fontSize: 12, padding: '6px 0' }}>No notes yet</div>}
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 220, overflowY: 'auto' }}>
                        {notes.map(n => (
                          <div key={n.id} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px' }}>
                            <div style={{ fontSize: 12, lineHeight: 1.6 }}>{n.note}</div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 3 }}>
                              <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.4px', color: SEVERITY_COLORS[n.severity] || SEVERITY_COLORS.normal, border: `1px solid ${SEVERITY_COLORS[n.severity] || SEVERITY_COLORS.normal}`, borderRadius: 8, padding: '1px 7px' }}>{n.severity || 'normal'}</span>
                              <span style={{ fontSize: 11, color: 'var(--text3)' }}>
                                {n.employee_id ? `${n.employee_id} — ` : ''}{new Date(n.created_at).toLocaleString()}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div style={{ color: 'var(--text3)', fontSize: 12, padding: '4px 2px' }}>
                      Notes can be added after the activation is processed — click the activation in the list to reopen it.
                    </div>
                  )}
                </div>
              )}
              {formTab === 'Billing Address' && (
                <div style={{ color: 'var(--text3)', textAlign: 'center', padding: 40, fontSize: 13 }}>
                  <div style={{ fontSize: 24, marginBottom: 12 }}>📍</div>
                  Billing address pulls from the customer record.<br />
                  Go to Customers module to update address.
                </div>
              )}
              {formTab === 'Additional Info' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div><label style={label}>Account Number</label><input value={formData.account_number} onChange={e => setFormData(f => ({ ...f, account_number: e.target.value }))} style={input} /></div>
                  <div><label style={label}>Status</label>
                    <select value={formData.status} onChange={e => setFormData(f => ({ ...f, status: e.target.value }))} style={input}>
                      <option value="active">Active</option>
                      {/* Cancelling is enforced server-side (pos_activations_cancel) — the option
                          stays enabled and a 403 on save is surfaced with a friendly message. */}
                      <option value="cancelled">Cancelled</option>
                      <option value="transferred">Transferred</option>
                    </select>
                    {formData.status !== 'cancelled' && selected?.status !== 'cancelled' && (
                      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>Cancelling requires the activations-cancel permission</div>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={closeForm}>Close</button>
              <button className="btn btn-primary" disabled={saving} onClick={save}>
                {saving ? 'Saving…' : editingId ? 'Save Changes' : '✅ Process'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* CUSTOMER PICKER */}
      {showCustPicker && (
        <div style={{ ...modalOverlay, zIndex: 300 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 560, maxHeight: '80vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>Select Customer</b>
              <button onClick={() => setShowCustPicker(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 8 }}>
              <input value={custSearch} onChange={e => setCustSearch(e.target.value)} onKeyDown={e => e.key === 'Enter' && searchCustomers()} placeholder="Search by name, phone..." style={{ ...input, flex: 1 }} autoFocus />
              <button className="btn btn-primary" onClick={searchCustomers}>Search</button>
            </div>
            <div style={{ overflowY: 'auto', flex: 1 }}>
              {customers.map(c => (
                <div key={c.id} onClick={() => { setSelectedCust(c); setShowCustPicker(false) }}
                  style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', cursor: 'pointer', display: 'flex', justifyContent: 'space-between' }}
                  onMouseEnter={e => (e.currentTarget as HTMLDivElement).style.background = 'var(--surface2)'}
                  onMouseLeave={e => (e.currentTarget as HTMLDivElement).style.background = 'transparent'}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{c.first_name} {c.last_name} {c.company_name ? `(${c.company_name})` : ''}</div>
                  <span style={{ fontSize: 11, color: '#3498db' }}>#{c.cust_number}</span>
                </div>
              ))}
              {customers.length === 0 && <div style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>Search for a customer above</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
