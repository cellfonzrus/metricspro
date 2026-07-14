'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// SALE-TRIGGERED multi-month rep pay (commission-0 doctrine, mig 201). A schedule attaches to a
// Commission Plan and is triggered by the SALE LINE (M1..N relative to trans_date). Months are PAID-GATED:
// a month pays only when the sold line is active + receiving residual (raw_mi presence). Which sales pay
// (backfill vs cutover) is USER-DEFINED. MRC for %-of-MRC lines comes from the product_mrc catalog,
// auto-prefilled from the description and USER-CONFIRMED. Nothing here changes pay until Run Calculation.

type Line = { month_index: number; payout_kind: string; flat_amount: any; mrc_pct: any; mrc_source: string }
type Sched = {
  id?: string; plan_id: string; name?: string; num_months: number
  trigger_match_field: string; trigger_match_op: string; trigger_match_value?: string
  gate_mode: string; gate_from_month: number; clawback_enabled: boolean
  effective_from?: string; effective_to?: string; eligible_sale_periods?: string[]
  is_active: boolean; lines?: Line[]
}

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const GATES = [
  { v: 'paid_residual', l: 'Paid + residual received (recommended)' },
  { v: 'active_status', l: 'Line Active that month' },
  { v: 'nonzero_residual', l: 'Non-zero residual that month' },
  { v: 'none', l: 'No gate — pay on the calendar' },
]
const MATCH_FIELDS = ['any', 'contract_type', 'department', 'category', 'product_desc', 'sku', 'trans_type']
const blankLine = (i: number): Line => ({ month_index: i, payout_kind: i === 1 ? 'flat' : 'flat', flat_amount: '', mrc_pct: '', mrc_source: 'product_catalog' })
const blankSched = (): Sched => ({
  plan_id: '', name: '', num_months: 3, trigger_match_field: 'any', trigger_match_op: 'equals',
  trigger_match_value: '', gate_mode: 'paid_residual', gate_from_month: 1, clawback_enabled: false,
  effective_from: '', effective_to: '', eligible_sale_periods: [], is_active: true,
  lines: [blankLine(1), blankLine(2), blankLine(3)],
})

export default function PlanInstallmentsPage() {
  const { period } = usePeriod()
  const [plans, setPlans] = useState<any[]>([])
  const [scheds, setScheds] = useState<Sched[]>([])
  const [ready, setReady] = useState(true)
  const [draft, setDraft] = useState<Sched>(blankSched())
  const [settings, setSettings] = useState<any>({ pay_disabled: false, residual_visibility: 'all' })
  const [cands, setCands] = useState<any[]>([])
  const [preview, setPreview] = useState<any>(null)
  const [msg, setMsg] = useState('')

  useEffect(() => { load() }, [])

  async function load() {
    try {
      const [pl, sc, st] = await Promise.all([
        api(`/api/v1/commcalc/commission-plans?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/plan-installments?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/commission-settings?org_id=${ORG_ID}`),
      ])
      setPlans(pl?.plans || [])
      setScheds(sc?.schedules || [])
      setReady(sc?.ready !== false)
      setSettings(st || { pay_disabled: false, residual_visibility: 'all' })
    } catch (e: any) { setMsg(e.message) }
  }

  function setLine(i: number, patch: Partial<Line>) {
    setDraft(d => ({ ...d, lines: (d.lines || []).map((l, k) => k === i ? { ...l, ...patch } : l) }))
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
      await api(`/api/v1/commcalc/plan-installments?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify(body) })
      setDraft(blankSched()); setMsg('Saved.'); load()
    } catch (e: any) { setMsg(e.message) }
  }

  async function delSched(id?: string) {
    if (!id || !confirm('Delete this installment schedule?')) return
    try { await api(`/api/v1/commcalc/plan-installments/${id}?org_id=${ORG_ID}`, { method: 'DELETE' }); load() }
    catch (e: any) { setMsg(e.message) }
  }

  async function saveSettings() {
    setMsg('')
    try {
      await api(`/api/v1/commcalc/commission-settings?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify(settings) })
      setMsg('Pay settings saved.')
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

  async function runPreview() {
    setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/plan-installments/preview/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      setPreview(r)
    } catch (e: any) { setMsg(e.message) }
  }

  return (
    <div style={{ maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Multi-month Commission (sale-triggered)</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 20px' }}>
        Pay a rep across up to 12 months from ONE sale line — paid-gated on the line staying active &
        receiving residual ("we pay as we get paid"). Nothing here changes pay until you Run Calculation.
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
        <button className="btn btn-primary" onClick={saveSettings}>Save pay settings</button>
      </div>

      {/* ── Existing schedules ─────────────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20, padding: 0 }}>
        <div style={{ padding: '14px 18px', fontWeight: 600, borderBottom: '1px solid var(--border)' }}>Installment schedules</div>
        <div className="table-wrapper" style={{ border: 'none' }}>
          <table>
            <thead><tr><th>Plan</th><th>Name</th><th>Months</th><th>Gate</th><th>Effective</th><th>Active</th><th></th></tr></thead>
            <tbody>
              {scheds.map(s => (
                <tr key={s.id}>
                  <td>{plans.find(p => p.id === s.plan_id)?.name || s.plan_id?.slice(0, 8)}</td>
                  <td>{s.name || '—'}</td>
                  <td>{s.num_months}</td>
                  <td>{s.gate_mode}{s.gate_from_month > 1 ? ` (from M${s.gate_from_month})` : ''}</td>
                  <td style={{ fontSize: 12 }}>{(s.eligible_sale_periods || []).join(', ') || `${s.effective_from || '—'} → ${s.effective_to || '—'}`}</td>
                  <td>{s.is_active ? '✓' : '—'}</td>
                  <td><button className="btn" onClick={() => delSched(s.id)}>Delete</button></td>
                </tr>
              ))}
              {scheds.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text3)', padding: 20 }}>No schedules yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── New schedule ───────────────────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>New installment schedule</div>
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
          <label style={{ fontSize: 12 }}>Paid gate
            <select style={{ ...sel, width: '100%' }} value={draft.gate_mode} onChange={e => setDraft({ ...draft, gate_mode: e.target.value })}>
              {GATES.map(g => <option key={g.v} value={g.v}>{g.l}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12 }}>Gate from month
            <input type="number" min={1} max={12} style={{ ...sel, width: '100%' }} value={draft.gate_from_month}
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
        <button className="btn btn-primary" onClick={saveSched}>Save schedule</button>
      </div>

      {/* ── MRC mapping (classification-first) ──────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontWeight: 600 }}>MRC mapping — {period} (auto-classified + $ prefilled from the description)</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={loadCandidates}>Scan sales</button>
            <button className="btn btn-primary" onClick={confirmMrc} disabled={!cands.length}>Confirm mappings</button>
          </div>
        </div>
        {cands.length > 0 ? (
          <div className="table-wrapper" style={{ border: 'none' }}>
            <table>
              <thead><tr><th>Plan / product</th><th>Lines</th><th>Classification</th><th>MRC ($)</th><th>Confirmed</th></tr></thead>
              <tbody>
                {cands.map((c, i) => (
                  <tr key={i}>
                    <td style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.plan}</td>
                    <td>{c.count}</td>
                    <td>
                      <select style={sel} value={c.classification || 'misc_other'}
                        onChange={e => setCands(cs => cs.map((x, k) => k === i ? { ...x, classification: e.target.value } : x))}>
                        {['accessory', 'activation', 'upgrade', 'swap', 'bill_payment', 'rebate', 'misc_other'].map(o => <option key={o} value={o}>{o}</option>)}
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
                ))}
              </tbody>
            </table>
          </div>
        ) : <div style={{ color: 'var(--text3)', fontSize: 13 }}>Scan a period's sales to classify plan lines and prefill their MRC.</div>}
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
              {preview.note ? ` · ${preview.note}` : ''}
            </div>
            <table>
              <thead><tr><th>Rep</th><th>MDN</th><th>Sale mo</th><th>Month</th><th>Kind</th><th style={{ textAlign: 'right' }}>$</th><th>Gate</th></tr></thead>
              <tbody>
                {(preview.ledger || []).slice(0, 40).map((l: any, i: number) => (
                  <tr key={i}>
                    <td>{l.epay_salesperson}</td><td>{l.mdn}</td><td>{l.sale_period}</td><td>M{l.month_index}</td>
                    <td>{l.payout_kind}</td><td style={{ textAlign: 'right' }}>{fmt(l.amount || 0)}</td>
                    <td>{l.paid_gate_met ? <span className="badge badge-green">paid</span> : <span className="badge badge-red">withheld</span>}</td>
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
