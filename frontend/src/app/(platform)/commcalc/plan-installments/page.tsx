'use client'
import { useState, useEffect, Fragment } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// SALE-TRIGGERED multi-month rep pay (commission-0 doctrine, mig 201; edit + m1-gate mig 210). A schedule
// attaches to a Commission Plan and is triggered by the SALE LINE (M1..N relative to trans_date). Months are
// PAID-GATED: a month pays only when the sold line is active + receiving residual (raw_mi presence) — OR, for
// month 1 under m1_gate='activation_payment', when the ACTIVATION TRANSACTION itself shows a first-month
// payment. Which sales pay (backfill vs cutover) is USER-DEFINED. MRC for %-of-MRC lines comes from the
// product_mrc catalog, auto-prefilled from the description and USER-CONFIRMED. Schedules are EDITABLE:
// an edit takes effect from the NEXT Run Calculation onward; already-paid ledger months are not retroffed.

type Line = { month_index: number; payout_kind: string; flat_amount: any; mrc_pct: any; mrc_source: string }
type Sched = {
  id?: string; plan_id: string; name?: string; num_months: number
  trigger_match_field: string; trigger_match_op: string; trigger_match_value?: string
  gate_mode: string; gate_from_month: number; m1_gate: string; clawback_enabled: boolean
  effective_from?: string; effective_to?: string; eligible_sale_periods?: string[]
  is_active: boolean; lines?: Line[]
}
type Matcher = { departments: string[]; categories: string[]; product_keywords: string[]; value_field: string; min_amount: any }

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const GATES = [
  { v: 'paid_residual', l: 'Paid + residual received (recommended)' },
  { v: 'active_status', l: 'Line Active that month' },
  { v: 'nonzero_residual', l: 'Non-zero residual that month' },
  { v: 'none', l: 'No gate — pay on the calendar' },
]
const M1_GATES = [
  { v: 'inherit', l: 'Inherit — month 1 uses the paid gate above' },
  { v: 'activation_payment', l: 'Paid at activation — customer paid their 1st month at the sale' },
]
const MATCH_FIELDS = ['any', 'contract_type', 'department', 'category', 'product_desc', 'sku', 'trans_type']
// The line classifications the MRC mapping assigns (shared mig-210 sales vocabulary; product_mrc.classification).
const CLASSIFICATIONS = ['accessory', 'activation', 'upgrade', 'swap', 'bill_payment', 'rebate', 'misc_other']
const blankLine = (i: number): Line => ({ month_index: i, payout_kind: 'flat', flat_amount: '', mrc_pct: '', mrc_source: 'product_catalog' })
const blankSched = (): Sched => ({
  plan_id: '', name: '', num_months: 3, trigger_match_field: 'any', trigger_match_op: 'equals',
  trigger_match_value: '', gate_mode: 'paid_residual', gate_from_month: 1, m1_gate: 'inherit', clawback_enabled: false,
  effective_from: '', effective_to: '', eligible_sale_periods: [], is_active: true,
  lines: [blankLine(1), blankLine(2), blankLine(3)],
})

// pick-don't-type chip editor: add from a dropdown of EXISTING values (RULE THREE), or a free keyword box.
function TagPicker({ label, values, options, onChange, allowFree }: { label: string; values: string[]; options?: string[]; onChange: (v: string[]) => void; allowFree?: boolean }) {
  const [pick, setPick] = useState('')
  const [free, setFree] = useState('')
  const avail = (options || []).filter(o => !values.includes(o))
  return (
    <div style={{ fontSize: 12 }}>
      <div style={{ marginBottom: 4, color: 'var(--text2)' }}>{label}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
        {values.map(v => (
          <span key={v} className="badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            {v}<button className="btn" style={{ padding: '0 5px', lineHeight: 1 }} onClick={() => onChange(values.filter(x => x !== v))}>×</button>
          </span>
        ))}
        {values.length === 0 && <span style={{ color: 'var(--text3)' }}>none</span>}
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        {options && (
          <select style={sel} value={pick} onChange={e => { const v = e.target.value; if (v) { onChange([...values, v]); setPick('') } }}>
            <option value="">+ add existing…</option>
            {avail.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        )}
        {allowFree && (
          <>
            <input style={{ ...sel, width: 150 }} placeholder="add keyword…" value={free} onChange={e => setFree(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && free.trim()) { onChange([...values, free.trim()]); setFree('') } }} />
            <button className="btn" onClick={() => { if (free.trim()) { onChange([...values, free.trim()]); setFree('') } }}>Add</button>
          </>
        )}
      </div>
    </div>
  )
}

export default function PlanInstallmentsPage() {
  const { period } = usePeriod()
  const [plans, setPlans] = useState<any[]>([])
  const [scheds, setScheds] = useState<Sched[]>([])
  const [ready, setReady] = useState(true)
  const [draft, setDraft] = useState<Sched>(blankSched())
  const [settings, setSettings] = useState<any>({ pay_disabled: false, residual_visibility: 'all' })
  const [matcher, setMatcher] = useState<Matcher | null>(null)
  const [matcherOpts, setMatcherOpts] = useState<{ departments: string[]; categories: string[]; value_fields: string[]; is_default: boolean }>({ departments: [], categories: [], value_fields: ['ext_price', 'gp'], is_default: true })
  const [cands, setCands] = useState<any[]>([])
  const [candFilter, setCandFilter] = useState('')                 // write-in filter ("rtr")
  const [pickedCands, setPickedCands] = useState<Set<string>>(new Set())  // selected plan strings (bulk)
  const [bulkCat, setBulkCat] = useState('')                       // one category → all selected
  const [conflicts, setConflicts] = useState<any[]>([])            // cross-menu guard result
  const [preview, setPreview] = useState<any>(null)
  const [audit, setAudit] = useState<{ sid: string; rows: any[] } | null>(null)
  const [showAdvMatcher, setShowAdvMatcher] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => { load() }, [])

  async function load() {
    try {
      const [pl, sc, st, mt] = await Promise.all([
        api(`/api/v1/commcalc/commission-plans?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/plan-installments?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/commission-settings?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/plan-installments/activation-matcher?org_id=${ORG_ID}`),
      ])
      setPlans(pl?.plans || [])
      setScheds(sc?.schedules || [])
      setReady(sc?.ready !== false)
      setSettings(st || { pay_disabled: false, residual_visibility: 'all' })
      if (mt?.matcher) { setMatcher(mt.matcher); setMatcherOpts({ departments: mt.departments || [], categories: mt.categories || [], value_fields: mt.value_fields || ['ext_price', 'gp'], is_default: !!mt.is_default }) }
    } catch (e: any) { setMsg(e.message) }
  }

  function setLine(i: number, patch: Partial<Line>) {
    setDraft(d => ({ ...d, lines: (d.lines || []).map((l, k) => k === i ? { ...l, ...patch } : l) }))
  }

  function editSched(s: Sched) {
    const n = Math.max(1, Math.min(12, s.num_months || 1))
    const lines = Array.from({ length: n }, (_, i) => (s.lines || [])[i] || blankLine(i + 1))
    setDraft({ ...blankSched(), ...s, m1_gate: s.m1_gate || 'inherit', num_months: n, lines,
      effective_from: s.effective_from || '', effective_to: s.effective_to || '', eligible_sale_periods: s.eligible_sale_periods || [] })
    setMsg(`Editing "${s.name || 'schedule'}" — Save applies from the NEXT Run Calculation; already-paid months are not changed.`)
    if (typeof window !== 'undefined') window.scrollTo({ top: document.body.scrollHeight * 0.35, behavior: 'smooth' })
  }

  async function saveSched() {
    setMsg('')
    if (!draft.plan_id) { setMsg('Pick a Commission Plan for this schedule.'); return }
    try {
      const body: any = {
        ...draft,
        effective_from: draft.effective_from || null,
        effective_to: draft.effective_to || null,
        eligible_sale_periods: (draft.eligible_sale_periods || []).filter(Boolean),
        lines: (draft.lines || []).slice(0, draft.num_months),
      }
      if (draft.id) {
        await api(`/api/v1/commcalc/plan-installments/${draft.id}?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify(body) })
        setMsg('Schedule updated. Applies from the next Run Calculation; already-paid months unchanged.')
      } else {
        await api(`/api/v1/commcalc/plan-installments?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify(body) })
        setMsg('Saved.')
      }
      setDraft(blankSched()); load()
    } catch (e: any) { setMsg(e.message) }
  }

  async function delSched(id?: string) {
    if (!id || !confirm('Delete this installment schedule?')) return
    try { await api(`/api/v1/commcalc/plan-installments/${id}?org_id=${ORG_ID}`, { method: 'DELETE' }); load() }
    catch (e: any) { setMsg(e.message) }
  }

  async function showAudit(id?: string) {
    if (!id) return
    if (audit?.sid === id) { setAudit(null); return }
    try {
      const r = await api(`/api/v1/commcalc/plan-installments/${id}/audit?org_id=${ORG_ID}`)
      setAudit({ sid: id, rows: r?.audit || [] })
    } catch (e: any) { setMsg(e.message) }
  }

  async function saveSettings() {
    setMsg('')
    try {
      await api(`/api/v1/commcalc/commission-settings?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify(settings) })
      setMsg('Pay settings saved.')
    } catch (e: any) { setMsg(e.message) }
  }

  async function saveMatcher(reset = false) {
    setMsg('')
    try {
      const body: any = reset ? { reset: true } : { ...matcher }
      await api(`/api/v1/commcalc/plan-installments/activation-matcher?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify(body) })
      setMsg(reset ? 'Reset to the default activation-payment matcher.' : 'Activation-payment matcher saved.')
      load()
    } catch (e: any) { setMsg(e.message) }
  }

  async function loadCandidates() {
    setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/mrc-mapping/candidates?period=${encodeURIComponent(period)}&org_id=${ORG_ID}`)
      setCands(r?.candidates || [])
    } catch (e: any) { setMsg(e.message) }
  }

  async function confirmMrc() {
    setMsg('')
    const items = cands.filter(c => c.confirmed_mrc != null || c.prefill_mrc != null).map(c => ({
      plan: c.plan, mrc: c.confirmed_mrc != null ? c.confirmed_mrc : c.prefill_mrc,
      classification: c.classification, prefill_mrc: c.prefill_mrc,
    }))
    if (!items.length) { setMsg('No MRC amounts to confirm — enter or accept a prefill first.'); return }
    try {
      const r = await api(`/api/v1/commcalc/mrc-mapping/confirm?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify({ items }) })
      setMsg(`Confirmed ${r?.saved || 0} MRC mapping(s).`); loadCandidates()
    } catch (e: any) { setMsg(e.message) }
  }

  // Bulk-assign ONE category to every selected (filtered) plan in a single call. A dry_run pre-flight
  // fetches the cross-menu conflict list (api() flattens error bodies), then the real write applies —
  // the backend re-checks the guard and 409s independently, so nothing diverges. product_mrc.classification
  // is display/config (not a pay input); nothing changes pay until Run Calculation.
  async function applyBulkClassify() {
    setMsg(''); setConflicts([])
    if (pickedCands.size === 0) { setMsg('Select some products first (checkboxes).'); return }
    if (!bulkCat) { setMsg('Pick a category to assign.'); return }
    const items = cands.filter(c => pickedCands.has(c.plan)).map(c => ({
      plan: c.plan,
      mrc: c.confirmed_mrc != null ? c.confirmed_mrc : c.prefill_mrc,
      prefill_mrc: c.prefill_mrc,
    }))
    try {
      const chk = await api(`/api/v1/commcalc/mrc-mapping/bulk-classify?org_id=${ORG_ID}`, {
        method: 'POST', body: JSON.stringify({ items, classification: bulkCat, dry_run: true }) })
      if (chk?.conflicts?.length) {
        setConflicts(chk.conflicts)
        setMsg(`Blocked — ${chk.conflicts.length} product(s) already have a different category on the Item / Model Mapping menu. Nothing saved.`)
        return
      }
      const r = await api(`/api/v1/commcalc/mrc-mapping/bulk-classify?org_id=${ORG_ID}`, {
        method: 'POST', body: JSON.stringify({ items, classification: bulkCat }) })
      setMsg(`Assigned "${bulkCat}" to ${r?.applied || 0} mapping(s). Takes effect on the next Run Calculation.`)
      setPickedCands(new Set()); setBulkCat(''); loadCandidates()
    } catch (e: any) { setMsg(e.message) }
  }

  async function runPreview() {
    setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/plan-installments/preview/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      setPreview(r)
    } catch (e: any) { setMsg(e.message) }
  }

  const anyActivation = scheds.some(s => (s.m1_gate || 'inherit') === 'activation_payment') || draft.m1_gate === 'activation_payment'

  return (
    <div style={{ maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Multi-month Commission (sale-triggered)</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 20px' }}>
        Pay a rep across up to 12 months from ONE sale line — paid-gated on the line staying active &
        receiving residual ("we pay as we get paid"). Schedules are editable; an edit applies from the next
        Run Calculation onward and never rewrites months already paid. Nothing here changes pay until you Run Calculation.
      </p>
      {msg && <div className="card" style={{ marginBottom: 16, borderLeft: '4px solid var(--accent)', fontSize: 13 }}>{msg}</div>}
      {!ready && <div className="card" style={{ marginBottom: 16, borderLeft: '4px solid var(--amber)', fontSize: 13 }}>
        Migration 201 not applied yet — schedules save once it runs (endpoints degrade to a code default meanwhile).
      </div>}

      {/* ── Pay settings (R1 override + residual visibility) ────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>Tenant pay settings</div>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: 10 }}>
          <input type="checkbox" checked={!!settings.pay_disabled}
            onChange={e => setSettings({ ...settings, pay_disabled: e.target.checked })} />
          This tenant intentionally pays <b>no commissions</b> (silences the "unconfigured — refused to pay" guard)
        </label>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: 12 }}>
          Carrier-residual visibility:
          <select style={sel} value={settings.residual_visibility}
            onChange={e => setSettings({ ...settings, residual_visibility: e.target.value })}>
            <option value="all">Visible to all (default)</option>
            <option value="permissioned">Restricted — require the carrier_residual permission</option>
          </select>
        </label>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: 6 }}>
          Contract-type matching in Commission Plans:
          <select style={sel} value={settings.plan_ct_resolution || 'raw'}
            onChange={e => setSettings({ ...settings, plan_ct_resolution: e.target.value })}>
            <option value="raw">Raw Contract Type only (default)</option>
            <option value="mapped">Raw value OR the mapped activation bucket</option>
          </select>
        </label>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 12px', maxWidth: 760 }}>
          A plan rule keyed on <b>Contract type</b> compares the raw POS value. If your POS leaves Contract
          Type <b>blank</b> (or uses carrier-specific labels), those lines can never match and pay $0. Set
          this to <b>mapped</b> and the same rule will ALSO match the line's resolved activation bucket
          (premium / upgrade / BYOD) from this tenant's own classification settings.
          <b> This can increase pay</b> — it takes effect on the next Run Calculation. Check
          <a href="/commcalc/commission-plans" style={{ color: 'var(--accent)' }}> Commission Plans → Plan coverage</a> first.
        </p>
        <button className="btn btn-primary" onClick={saveSettings}>Save pay settings</button>
      </div>

      {/* ── Activation-payment matcher (month-1 "paid at activation" config) ─────────────── */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>What counts as "payment received at activation" (month-1 gate)</div>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 12px' }}>
          Used when a schedule sets month-1 to <b>Paid at activation</b>. The AUTHORITATIVE signal is the
          item mapping: an item mapped to the <b>Activation payment</b> category (sales or KPI dimension) on
          the <a href="/commcalc/item-mapping" style={{ color: 'var(--accent)' }}>Item / Model Mapping</a> page,
          with money collected, qualifies month 1. Until you map items, a seeded heuristic (departments /
          categories / keywords below) is used as the fallback. The <b>money-collected</b> gate below applies in
          both cases. {matcherOpts.is_default ? 'Currently using the seeded default matcher.' : 'Currently using a tenant override.'}
        </p>
        {matcher && (
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end', marginBottom: 12 }}>
            <label style={{ fontSize: 12 }}>Value field (money collected)
              <select style={{ ...sel, width: 160 }} value={matcher.value_field} onChange={e => setMatcher({ ...matcher, value_field: e.target.value })}>
                {matcherOpts.value_fields.map(f => <option key={f} value={f}>{f}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12 }}>Min amount
              <input style={{ ...sel, width: 90 }} value={matcher.min_amount}
                onChange={e => setMatcher({ ...matcher, min_amount: e.target.value === '' ? '' : Number(e.target.value) })} />
            </label>
          </div>
        )}
        {matcher && (
          <div style={{ marginBottom: 12 }}>
            <button className="btn" style={{ fontSize: 12 }} onClick={() => setShowAdvMatcher(s => !s)}>
              {showAdvMatcher ? '▲ Hide' : '▼ Show'} seeded fallback heuristic (used until items are categorized)
            </button>
            {showAdvMatcher && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, marginTop: 12 }}>
                <TagPicker label="Payment departments" values={matcher.departments} options={matcherOpts.departments}
                  onChange={v => setMatcher({ ...matcher, departments: v })} />
                <TagPicker label="Payment categories" values={matcher.categories} options={matcherOpts.categories}
                  onChange={v => setMatcher({ ...matcher, categories: v })} />
                <TagPicker label="Product-desc keywords" values={matcher.product_keywords} allowFree
                  onChange={v => setMatcher({ ...matcher, product_keywords: v })} />
              </div>
            )}
          </div>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary" onClick={() => saveMatcher(false)}>Save matcher</button>
          <button className="btn" onClick={() => saveMatcher(true)}>Reset to default</button>
        </div>
      </div>

      {/* ── Existing schedules ─────────────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20, padding: 0 }}>
        <div style={{ padding: '14px 18px', fontWeight: 600, borderBottom: '1px solid var(--border)' }}>Installment schedules</div>
        <div className="table-wrapper" style={{ border: 'none' }}>
          <table>
            <thead><tr><th>Plan</th><th>Name</th><th>Months</th><th>Month-1</th><th>Gate</th><th>Effective</th><th>Active</th><th></th></tr></thead>
            <tbody>
              {scheds.map(s => {
                const openAudit = audit && audit.sid === s.id ? audit : null
                return (
                <Fragment key={s.id}>
                  <tr>
                    <td>{plans.find(p => p.id === s.plan_id)?.name || s.plan_id?.slice(0, 8)}</td>
                    <td>{s.name || '—'}</td>
                    <td>{s.num_months}</td>
                    <td>{(s.m1_gate || 'inherit') === 'activation_payment' ? <span className="badge badge-green">paid at activation</span> : <span style={{ color: 'var(--text3)' }}>inherit</span>}</td>
                    <td>{s.gate_mode}{s.gate_from_month > 1 ? ` (from M${s.gate_from_month})` : ''}</td>
                    <td style={{ fontSize: 12 }}>{(s.eligible_sale_periods || []).join(', ') || `${s.effective_from || '—'} → ${s.effective_to || '—'}`}</td>
                    <td>{s.is_active ? '✓' : '—'}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button className="btn" onClick={() => editSched(s)}>Edit</button>{' '}
                      <button className="btn" onClick={() => showAudit(s.id)}>History</button>{' '}
                      <button className="btn" onClick={() => delSched(s.id)}>Delete</button>
                    </td>
                  </tr>
                  {openAudit && (
                    <tr>
                      <td colSpan={8} style={{ background: 'var(--surface2)', fontSize: 12 }}>
                        <b>Edit history</b> ({openAudit.rows.length})
                        {openAudit.rows.length === 0 && <span style={{ color: 'var(--text3)' }}> — none (run migration 210 to record edits)</span>}
                        {openAudit.rows.map((a, i) => (
                          <div key={i} style={{ padding: '3px 0', color: 'var(--text2)' }}>
                            {a.action} · {(a.changed_at || '').slice(0, 19).replace('T', ' ')} · by {a.changed_by || 'web'}
                          </div>
                        ))}
                      </td>
                    </tr>
                  )}
                </Fragment>
                )
              })}
              {scheds.length === 0 && <tr><td colSpan={8} style={{ textAlign: 'center', color: 'var(--text3)', padding: 20 }}>No schedules yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── New / Edit schedule ─────────────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontWeight: 600 }}>{draft.id ? 'Edit installment schedule' : 'New installment schedule'}</div>
          {draft.id && <button className="btn" onClick={() => { setDraft(blankSched()); setMsg('') }}>Cancel edit</button>}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 12 }}>
          <label style={{ fontSize: 12 }}>Commission Plan
            <select style={{ ...sel, width: '100%' }} value={draft.plan_id} onChange={e => setDraft({ ...draft, plan_id: e.target.value })}>
              <option value="">— pick a plan —</option>
              {plans.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12 }}>Name<input style={{ ...sel, width: '100%' }} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
          <label style={{ fontSize: 12 }}>Months (1-12)
            <input type="number" min={1} max={12} style={{ ...sel, width: '100%' }} value={draft.num_months}
              onChange={e => {
                const n = Math.max(1, Math.min(12, Number(e.target.value) || 1))
                setDraft(d => {
                  const lines = Array.from({ length: n }, (_, i) => (d.lines || [])[i] || blankLine(i + 1))
                  return { ...d, num_months: n, lines }
                })
              }} />
          </label>
          <label style={{ fontSize: 12 }}>Paid gate (months {draft.m1_gate === 'activation_payment' ? '2..N' : '1..N'})
            <select style={{ ...sel, width: '100%' }} value={draft.gate_mode} onChange={e => setDraft({ ...draft, gate_mode: e.target.value })}>
              {GATES.map(g => <option key={g.v} value={g.v}>{g.l}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12 }}>Month-1 gate
            <select style={{ ...sel, width: '100%' }} value={draft.m1_gate} onChange={e => setDraft({ ...draft, m1_gate: e.target.value })}>
              {M1_GATES.map(g => <option key={g.v} value={g.v}>{g.l}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12 }}>Gate from month
            <input type="number" min={1} max={12} style={{ ...sel, width: '100%' }} value={draft.gate_from_month}
              disabled={draft.m1_gate === 'activation_payment'}
              onChange={e => setDraft({ ...draft, gate_from_month: Math.max(1, Number(e.target.value) || 1) })} />
          </label>
          <label style={{ fontSize: 12 }}>Trigger match
            <select style={{ ...sel, width: '100%' }} value={draft.trigger_match_field} onChange={e => setDraft({ ...draft, trigger_match_field: e.target.value })}>
              {MATCH_FIELDS.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          {draft.trigger_match_field !== 'any' && (
            <label style={{ fontSize: 12 }}>Match value
              <input style={{ ...sel, width: '100%' }} value={draft.trigger_match_value} onChange={e => setDraft({ ...draft, trigger_match_value: e.target.value })} />
            </label>
          )}
          <label style={{ fontSize: 12 }}>Effective from (cutover)<input type="date" style={{ ...sel, width: '100%' }} value={draft.effective_from} onChange={e => setDraft({ ...draft, effective_from: e.target.value })} /></label>
          <label style={{ fontSize: 12 }}>Effective to<input type="date" style={{ ...sel, width: '100%' }} value={draft.effective_to} onChange={e => setDraft({ ...draft, effective_to: e.target.value })} /></label>
          <label style={{ fontSize: 12 }}>Eligible sale months (comma-sep, overrides dates)
            <input style={{ ...sel, width: '100%' }} placeholder="June 2026, July 2026"
              value={(draft.eligible_sale_periods || []).join(', ')}
              onChange={e => setDraft({ ...draft, eligible_sale_periods: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} />
          </label>
        </div>
        {draft.m1_gate === 'activation_payment' && (
          <div style={{ fontSize: 12, color: 'var(--text2)', background: 'var(--surface2)', padding: '8px 10px', borderRadius: 7, marginBottom: 12 }}>
            Month 1 pays when the customer paid their first month <b>at the sale</b> (per the activation-payment
            matcher above), regardless of carrier residual. Months 2..N use the <b>paid gate</b> selected above.
            A month-1 line with no activation payment is withheld and flagged (commission tracking + employee miss).
          </div>
        )}
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: 12 }}>
          <input type="checkbox" checked={draft.clawback_enabled} onChange={e => setDraft({ ...draft, clawback_enabled: e.target.checked })} />
          Enable clawback on early deactivation (optional; default off)
        </label>
        <div style={{ fontWeight: 600, fontSize: 13, margin: '8px 0' }}>Per-month payout</div>
        <table style={{ marginBottom: 12 }}>
          <thead><tr><th>Month</th><th>Kind</th><th>Flat $</th><th>% of MRC (0.05 = 5%)</th></tr></thead>
          <tbody>
            {(draft.lines || []).map((l, i) => (
              <tr key={i}>
                <td>M{l.month_index}</td>
                <td>
                  <select style={sel} value={l.payout_kind} onChange={e => setLine(i, { payout_kind: e.target.value })}>
                    <option value="flat">Flat $</option><option value="pct_mrc">% of MRC</option>
                  </select>
                </td>
                <td><input style={{ ...sel, width: 90 }} value={l.flat_amount} disabled={l.payout_kind !== 'flat'} onChange={e => setLine(i, { flat_amount: e.target.value })} /></td>
                <td><input style={{ ...sel, width: 110 }} value={l.mrc_pct} disabled={l.payout_kind !== 'pct_mrc'} onChange={e => setLine(i, { mrc_pct: e.target.value })} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <button className="btn btn-primary" onClick={saveSched}>{draft.id ? 'Update schedule' : 'Save schedule'}</button>
      </div>

      {/* ── MRC mapping (classification-first) — write-in filter + bulk assign + cross-menu guard ── */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, gap: 8, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 600 }}>MRC mapping — {period} (auto-classified + $ prefilled from the description)</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={loadCandidates}>Scan sales</button>
            <button className="btn btn-primary" onClick={confirmMrc} disabled={!cands.length}>Confirm mappings</button>
          </div>
        </div>
        {cands.length > 0 ? (() => {
          const shown = cands.filter(c => !candFilter.trim() || String(c.plan || '').toLowerCase().includes(candFilter.trim().toLowerCase()))
          const shownKeys = shown.map(c => c.plan)
          const allShownPicked = shown.length > 0 && shown.every(c => pickedCands.has(c.plan))
          const toggleAllShown = () => setPickedCands(p => { const n = new Set(p); if (allShownPicked) shownKeys.forEach(k => n.delete(k)); else shownKeys.forEach(k => n.add(k)); return n })
          const toggleOne = (k: string) => setPickedCands(p => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n })
          return (
          <>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
              <input style={{ ...sel, width: 240 }} placeholder="Filter plans… (e.g. rtr)" value={candFilter}
                onChange={e => setCandFilter(e.target.value)} aria-label="Filter plans" />
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>{shown.length} of {cands.length} shown · {pickedCands.size} selected</span>
              {candFilter && <button className="btn" style={{ fontSize: 12 }} onClick={() => setCandFilter('')}>clear</button>}
            </div>
            {pickedCands.size > 0 && (
              <div className="card" style={{ padding: 10, marginBottom: 10, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderLeft: '4px solid var(--accent)' }}>
                <b style={{ fontSize: 13 }}>{pickedCands.size} selected →</b>
                <span style={{ fontSize: 12 }}>category</span>
                <select style={sel} value={bulkCat} onChange={e => setBulkCat(e.target.value)} aria-label="Bulk category">
                  <option value="">— pick —</option>
                  {CLASSIFICATIONS.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
                <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={applyBulkClassify} disabled={!bulkCat}>Assign to {pickedCands.size}</button>
                <button className="btn" style={{ fontSize: 13 }} onClick={() => setPickedCands(new Set())}>Clear</button>
              </div>
            )}
            {conflicts.length > 0 && (
              <div className="card" style={{ padding: 10, marginBottom: 10, borderLeft: '4px solid #dc2626', fontSize: 12 }}>
                <b>⚠️ Cross-menu conflict — nothing was saved.</b> These products already carry a different category on the{' '}
                <a href="/commcalc/item-mapping" style={{ color: 'var(--accent)' }}>Item / Model Mapping</a> menu. Resolve the divergence there first, or change your selection.
                <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                  {conflicts.map((c, i) => (
                    <li key={i}>&quot;{c.plan}&quot; — assigning <b>{c.assigning}</b>, but it is <b>{c.other_category}</b> on {c.other_menu} ({c.other_key})</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="table-wrapper" style={{ border: 'none' }}>
              <table>
                <thead><tr>
                  <th style={{ width: 28 }}><input type="checkbox" checked={allShownPicked} onChange={toggleAllShown} aria-label="Select all shown" /></th>
                  <th>Plan / product</th><th>Lines</th><th>Classification</th><th>MRC ($)</th><th>Confirmed</th>
                </tr></thead>
                <tbody>
                  {shown.map((c) => {
                    const i = cands.indexOf(c)
                    return (
                    <tr key={i} style={pickedCands.has(c.plan) ? { background: 'var(--surface2)' } : undefined}>
                      <td style={{ textAlign: 'center' }}><input type="checkbox" checked={pickedCands.has(c.plan)} onChange={() => toggleOne(c.plan)} aria-label={`Select ${c.plan}`} /></td>
                      <td style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.plan}</td>
                      <td>{c.count}</td>
                      <td>
                        <select style={sel} value={c.classification || 'misc_other'}
                          onChange={e => setCands(cs => cs.map((x, k) => k === i ? { ...x, classification: e.target.value } : x))}>
                          {CLASSIFICATIONS.map(o => <option key={o} value={o}>{o}</option>)}
                        </select>
                      </td>
                      <td>
                        <input style={{ ...sel, width: 90 }}
                          value={c.confirmed_mrc != null ? c.confirmed_mrc : (c.prefill_mrc != null ? c.prefill_mrc : '')}
                          placeholder={c.prefill_mrc != null ? String(c.prefill_mrc) : '—'}
                          onChange={e => setCands(cs => cs.map((x, k) => k === i ? { ...x, confirmed_mrc: e.target.value === '' ? null : Number(e.target.value) } : x))} />
                      </td>
                      <td>{c.confirmed ? <span className="badge badge-green">✓</span> : <span className="badge badge-amber">prefill</span>}</td>
                    </tr>
                  )})}
                </tbody>
              </table>
            </div>
          </>
          )
        })() : <div style={{ color: 'var(--text3)', fontSize: 13 }}>Scan a period's sales to classify plan lines and prefill their MRC.</div>}
      </div>

      {/* ── Preview ────────────────────────────────────────────────────────────────────── */}
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontWeight: 600 }}>Preview — {period} (read-only, does not change pay)</div>
          <button className="btn" onClick={runPreview}>Run preview</button>
        </div>
        {preview && (
          <div style={{ fontSize: 13 }}>
            <div style={{ marginBottom: 8, color: 'var(--text2)' }}>
              {preview.totals?.reps || 0} reps · {fmt(preview.totals?.amount || 0)} · paid {preview.totals?.paid || 0} · withheld {preview.totals?.withheld || 0}
              {anyActivation ? ` · activation-payment qualified ${(preview.ledger || []).filter((l: any) => l.gate_kind === 'activation_payment' && l.paid_gate_met).length}` : ''}
              {preview.note ? ` · ${preview.note}` : ''}
            </div>
            <table>
              <thead><tr><th>Rep</th><th>MDN</th><th>Sale mo</th><th>Month</th><th>Kind</th><th style={{ textAlign: 'right' }}>$</th><th>Gate</th></tr></thead>
              <tbody>
                {(preview.ledger || []).slice(0, 40).map((l: any, i: number) => (
                  <tr key={i}>
                    <td>{l.epay_salesperson}</td><td>{l.mdn}</td><td>{l.sale_period}</td><td>M{l.month_index}</td>
                    <td>{l.payout_kind}</td><td style={{ textAlign: 'right' }}>{fmt(l.amount || 0)}</td>
                    <td>
                      {l.paid_gate_met ? <span className="badge badge-green">paid</span> : <span className="badge badge-red">withheld</span>}
                      {l.gate_kind === 'activation_payment' && <span style={{ fontSize: 11, color: 'var(--text3)' }}> via activation</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
