'use client'
import { useState, useEffect, useMemo, Fragment } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { PlanOptions, MatchValuePicker, MatchEvidence, FALLBACK_VOCAB, countMatches, OptionsSourceNote } from '../_lib/planMatch'
import EntityPicker from '@/components/EntityPicker'
import RunCommissionButton from '../_lib/RunCommissionButton'

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
  qualifying_categories?: Record<string, boolean> | null   // mig 245 — null = inherit the tenant setting
}
type CatCfg = {
  qualification: Record<string, boolean>; is_default: boolean; defaults: Record<string, boolean>
  categories: { key: string; label: string }[]; ready: boolean; rules_ready: boolean
  rules: any[]; builtin_rules: any[]; match_fields: string[]; match_ops: string[]
  schedules: { id: string; name?: string; qualifying_categories?: any }[]
  departments: string[]; categories_seen: string[]; products: string[]
}
type Matcher = { departments: string[]; categories: string[]; product_keywords: string[]; value_field: string; min_amount: any }
// mig 256 — FLAT (one-time) payout by device category. `amount: null` means NOT CONFIGURED, which is
// deliberately different from 0: an unconfigured flat category keeps paying monthly installments.
type PayoutEntry = { mode: string; amount: number | null; pay_month: number }
type PayCfg = {
  payout: Record<string, PayoutEntry>; is_default: boolean; defaults: Record<string, PayoutEntry>
  categories: { key: string; label: string }[]; modes: { key: string; label: string }[]
  max_pay_month: number; flat_categories: string[]
  schedules: { id: string; name?: string; num_months?: number; category_payout?: any }[]
  ready: boolean; migration: string | null
}

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
// MATCH FIELDS/OPS come from the ENGINE (GET /commcalc/plan-field-options → commission_engine.MATCH_FIELDS
// + _rule_matches), which the sale-installment trigger evaluates through the very same matcher
// (sale_installment_engine._rule_matches). The list hard-coded here previously offered 7 of the 10 fields —
// notably not 'activation_bucket', which is the one a blank-Contract-Type tenant needs.
// The line classifications the MRC mapping assigns (shared mig-210 sales vocabulary; product_mrc.classification).
const CLASSIFICATIONS = ['accessory', 'activation', 'upgrade', 'swap', 'bill_payment', 'rebate', 'misc_other']
const blankLine = (i: number): Line => ({ month_index: i, payout_kind: 'flat', flat_amount: '', mrc_pct: '', mrc_source: 'product_catalog' })
const blankSched = (): Sched => ({
  plan_id: '', name: '', num_months: 3, trigger_match_field: 'any', trigger_match_op: 'equals',
  trigger_match_value: '', gate_mode: 'paid_residual', gate_from_month: 1, m1_gate: 'inherit', clawback_enabled: false,
  effective_from: '', effective_to: '', eligible_sale_periods: [], is_active: true,
  lines: [blankLine(1), blankLine(2), blankLine(3)],
  qualifying_categories: null,
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
  // The page's OWN period context — the Run Commission control targets exactly this period and its
  // picker writes back here, so the page can never show one month and recompute another.
  const { period, setPeriod } = usePeriod()
  const [plans, setPlans] = useState<any[]>([])
  const [scheds, setScheds] = useState<Sched[]>([])
  const [ready, setReady] = useState(true)
  const [draft, setDraft] = useState<Sched>(blankSched())
  const [settings, setSettings] = useState<any>({ pay_disabled: false, residual_visibility: 'all' })
  const [matcher, setMatcher] = useState<Matcher | null>(null)
  const [matcherOpts, setMatcherOpts] = useState<{ departments: string[]; categories: string[]; value_fields: string[]; is_default: boolean }>({ departments: [], categories: [], value_fields: ['ext_price', 'gp'], is_default: true })
  // mig 233 — which sale line carries the activation's monthly charge (the %-of-MRC basis)
  const [planLine, setPlanLine] = useState<{ departments: string[]; categories: string[]; product_keywords: string[] } | null>(null)
  const [planLineOpts, setPlanLineOpts] = useState<{ departments: string[]; categories: string[]; is_default: boolean; ready: boolean }>({ departments: [], categories: [], is_default: true, ready: true })
  const [cands, setCands] = useState<any[]>([])
  const [candFilter, setCandFilter] = useState('')                 // write-in filter ("rtr")
  const [pickedCands, setPickedCands] = useState<Set<string>>(new Set())  // selected plan strings (bulk)
  const [bulkCat, setBulkCat] = useState('')                       // one category → all selected
  const [conflicts, setConflicts] = useState<any[]>([])            // cross-menu guard result
  const [preview, setPreview] = useState<any>(null)
  const [catCfg, setCatCfg] = useState<CatCfg | null>(null)          // mig 245 qualifying categories
  const [catDraft, setCatDraft] = useState<Record<string, boolean>>({})
  const [ruleDraft, setRuleDraft] = useState<any>({ category_key: 'tablet', match_field: 'product_desc', match_op: 'word', match_value: '', priority: 50 })
  const [showBuiltins, setShowBuiltins] = useState(false)
  const [impact, setImpact] = useState<any>(null)
  // mig 256 — flat (one-time) payout by category. `payDraft` mirrors the saved config; a blank
  // amount box stays BLANK (never coerced to 0) so "not configured" survives a round trip.
  const [payCfg, setPayCfg] = useState<PayCfg | null>(null)
  const [payDraft, setPayDraft] = useState<Record<string, { mode: string; amount: string; pay_month: string }>>({})
  const [payImpact, setPayImpact] = useState<any>(null)
  const [payHypo, setPayHypo] = useState<{ category: string; amount: string; pay_month: string }>({ category: 'home_internet', amount: '', pay_month: '1' })
  const [audit, setAudit] = useState<{ sid: string; rows: any[] } | null>(null)
  const [showAdvMatcher, setShowAdvMatcher] = useState(false)
  const [msg, setMsg] = useState('')
  // engine vocabulary + this tenant's observed values for the trigger matcher (RULE THREE §3b)
  const [planOpts, setPlanOpts] = useState<PlanOptions | null>(null)

  useEffect(() => { load() }, [])

  async function load() {
    try {
      const [pl, sc, st, mt, plm, cq, cp] = await Promise.all([
        api(`/api/v1/commcalc/commission-plans?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/plan-installments?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/commission-settings?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/plan-installments/activation-matcher?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/plan-installments/plan-line-matcher?org_id=${ORG_ID}`),
        api(`/api/v1/commcalc/plan-installments/category-qualification?org_id=${ORG_ID}&period=${encodeURIComponent(period || '')}`).catch(() => null),
        api(`/api/v1/commcalc/plan-installments/category-payout?org_id=${ORG_ID}`).catch(() => null),
      ])
      setPlans(pl?.plans || [])
      setScheds(sc?.schedules || [])
      setReady(sc?.ready !== false)
      setSettings(st || { pay_disabled: false, residual_visibility: 'all' })
      if (mt?.matcher) { setMatcher(mt.matcher); setMatcherOpts({ departments: mt.departments || [], categories: mt.categories || [], value_fields: mt.value_fields || ['ext_price', 'gp'], is_default: !!mt.is_default }) }
      if (plm?.matcher) { setPlanLine(plm.matcher); setPlanLineOpts({ departments: plm.departments || [], categories: plm.categories || [], is_default: !!plm.is_default, ready: plm.ready !== false }) }
      if (cq?.qualification) { setCatCfg(cq); setCatDraft({ ...cq.qualification }) }
      if (cp?.payout) {
        setPayCfg(cp)
        const d: Record<string, { mode: string; amount: string; pay_month: string }> = {}
        Object.entries(cp.payout as Record<string, PayoutEntry>).forEach(([k, v]) => {
          d[k] = { mode: v?.mode || 'installments',
                   amount: v?.amount === null || v?.amount === undefined ? '' : String(v.amount),
                   pay_month: String(v?.pay_month ?? 1) }
        })
        setPayDraft(d)
      }
    } catch (e: any) { setMsg(e.message) }
    // read-only; never blocks the page (the picker degrades to the engine's field list + free text)
    try {
      const o: PlanOptions = await api(`/api/v1/commcalc/plan-field-options?months=3&period=${encodeURIComponent(period || '')}`)
      setPlanOpts({ ...o, vocab: o?.vocab || FALLBACK_VOCAB })
    } catch { setPlanOpts({ ready: false, vocab: FALLBACK_VOCAB, fields: {}, facets: null, periods: [] }) }
  }

  const vocab = planOpts?.vocab || FALLBACK_VOCAB
  // periods this tenant actually has sales for (+ anything already saved on the schedule — zero-wipe)
  const periodOptions = useMemo(() => {
    const out = (planOpts?.periods || []).map(p => ({ id: p.value, label: p.value, sublabel: `${(p.lines || 0).toLocaleString()} sale lines` }))
    ;(draft.eligible_sale_periods || []).forEach(v => {
      if (v && !out.some(o => o.id === v)) out.push({ id: v, label: v, sublabel: 'no sales rows found' })
    })
    return out
  }, [planOpts?.periods, draft.eligible_sale_periods])
  // Same source, but WITHOUT the draft's saved-eligibility additions: the recalculate picker must only
  // offer periods this tenant genuinely has sales for.
  const runPeriodOptions = useMemo(
    () => (planOpts?.periods || []).map(p => ({ id: p.value, label: p.value, sublabel: `${(p.lines || 0).toLocaleString()} sale lines` })),
    [planOpts?.periods])
  const triggerCount = useMemo(() => countMatches(planOpts, {
    match_field: draft.trigger_match_field, match_op: draft.trigger_match_op,
    match_value: draft.trigger_match_value,
  }), [planOpts, draft.trigger_match_field, draft.trigger_match_op, draft.trigger_match_value])

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
    if (!draft.plan_id) { setMsg('Pick an Incentive Plan for this schedule.'); return }
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

  async function savePlanLine(reset = false) {
    setMsg('')
    try {
      const body: any = reset ? { reset: true } : { ...planLine }
      await api(`/api/v1/commcalc/plan-installments/plan-line-matcher?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify(body) })
      setMsg(reset ? 'Reset to the default rate-plan line matcher. Run Calculation to apply it.'
                   : 'Rate-plan line matcher saved. Nothing changes until you Run Calculation.')
      load()
    } catch (e: any) { setMsg(e.message) }
  }

  // MONEY CONFIG (mig 245): which device categories earn a multi-month installment. Saving changes
  // nothing until the next Run Calculation — and every excluded activation is reported in the preview
  // warnings, so an unticked box can never turn into a silent zero.
  async function saveCategories(reset = false) {
    setMsg('')
    try {
      const body = reset ? { reset: true } : { qualification: catDraft }
      await api(`/api/v1/commcalc/plan-installments/category-qualification?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify(body) })
      setMsg(reset ? 'Reset to the defaults (tablets + SIM excluded). Applies on the next Run Calculation.'
        : 'Qualifying categories saved. Applies on the next Run Calculation — run the impact check below first.')
      load()
    } catch (e: any) { setMsg(e.message) }
  }

  // MONEY CONFIG (mig 256; owner 2026-08-01 "fwa is paid on flat rate should not be in monthly
  // payments"). A category switched to one-time pays the amount YOU type, once, and its other
  // installment months stop. A blank amount is saved but does NOT take effect — those chains keep
  // paying monthly and the Run-Calculation warnings say so. Nothing moves until Run Calculation.
  async function savePayout(reset = false) {
    setMsg('')
    try {
      const payout: Record<string, any> = {}
      Object.entries(payDraft).forEach(([k, v]) => {
        payout[k] = { mode: v.mode || 'installments',
                      amount: String(v.amount ?? '').trim() === '' ? null : Number(v.amount),
                      pay_month: Number(v.pay_month || 1) }
      })
      const r = await api(`/api/v1/commcalc/plan-installments/category-payout?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify(reset ? { reset: true } : { payout }) })
      setMsg(reset ? 'Reset — every category is back on monthly installments. Applies on the next Run Calculation.'
        : (r?.note || 'Flat payout saved. Applies on the next Run Calculation — check the impact below first.'))
      load()
    } catch (e: any) { setMsg(e.message) }
  }

  // READ-ONLY "what would it cost" — the amount is ALWAYS the one typed above; this never
  // invents a dollar and never recomputes anything.
  async function runPayoutImpact() {
    setMsg('')
    setPayImpact(null)
    try {
      const q = new URLSearchParams({ org_id: ORG_ID })
      if (payHypo.category && String(payHypo.amount).trim() !== '') {
        q.set('category', payHypo.category)
        q.set('amount', String(payHypo.amount).trim())
        q.set('pay_month', String(payHypo.pay_month || 1))
      }
      setPayImpact(await api(`/api/v1/commcalc/plan-installments/category-payout-impact/${encodeURIComponent(period)}?${q.toString()}`))
    } catch (e: any) { setMsg(e.message) }
  }

  async function saveRule() {
    setMsg('')
    if (!ruleDraft.match_value) { setMsg('Pick a value for the rule (Department / Category / product wording).'); return }
    try {
      await api(`/api/v1/commcalc/plan-installments/category-rules?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify(ruleDraft) })
      setRuleDraft({ ...ruleDraft, match_value: '' })
      load()
    } catch (e: any) { setMsg(e.message) }
  }

  async function delRule(id: string) {
    try { await api(`/api/v1/commcalc/plan-installments/category-rules/${id}?org_id=${ORG_ID}`, { method: 'DELETE' }); load() }
    catch (e: any) { setMsg(e.message) }
  }

  async function runImpact() {
    setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/plan-installments/category-impact/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      setImpact(r)
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

      {/* RUN COMMISSION (owner directive 2026-08-05) — a schedule edit applies from the NEXT calculation
          onward, so the recalculate control belongs on the page where the schedule is edited. */}
      <div className="card" style={{ marginBottom: 20, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>⚡ Apply these schedules to live pay</div>
        <RunCommissionButton period={period} onPeriodChange={setPeriod} periodOptions={runPeriodOptions}
          note="Editing a multi-month schedule changes nothing until the period is recalculated. Months already paid in the ledger are never rewritten." />
      </div>

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
          Contract-type matching in Incentive Plans:
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
          <a href="/commcalc/commission-plans" style={{ color: 'var(--accent)' }}> Incentive Plans → Plan coverage</a> first.
        </p>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: 6 }}>
          Store resolution (market + store-scope plan assignments):
          <select style={sel} value={settings.store_resolution || 'exact'}
            onChange={e => setSettings({ ...settings, store_resolution: e.target.value })}>
            <option value="exact">Exact store_mapping match only (default)</option>
            <option value="alias">Also resolve through the /store-match alias table</option>
          </select>
        </label>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 12px', maxWidth: 760 }}>
          A rep&apos;s <b>market</b> is looked up from the raw store string the POS writes on the sale.
          On the default it must match a <a href="/commcalc/settings" style={{ color: 'var(--accent)' }}>Stores
          &amp; Markets</a> address (or store code) <b>exactly</b>, so a POS that spells the store even
          slightly differently leaves the market <b>blank</b> — and a market- or store-scope assignment can
          then never attach to that rep. Set this to <b>alias</b> and the store string is additionally
          resolved through the mappings you confirmed at{' '}
          <a href="/commcalc/store-match" style={{ color: 'var(--accent)' }}>Store matching</a>. It is a
          strict superset — it can only ATTACH a plan that attaches to nobody today, never detach one —
          so <b>this can increase pay</b> on the next Run Calculation. See exactly who it would move first:
          <a href="/commcalc/commission-plans" style={{ color: 'var(--accent)' }}> Incentive Plans → Plan
          coverage</a> previews it per rep regardless of this setting.
        </p>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: 6 }}>
          <input type="checkbox" checked={settings.installment_mrc_hardware_guard !== false}
            onChange={e => setSettings({ ...settings, installment_mrc_hardware_guard: e.target.checked })} />
          A <b>device line can never donate its own price</b> as a monthly charge (recommended)
        </label>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 12px', maxWidth: 760 }}>
          A handset/tablet line carries an IMEI and a PRICE; the rate-plan line carries the mobile number
          and the MONTHLY charge. With this on, a promo description like
          <i> "Galaxy Tab … Promo $279.99, Min $50 tablet plan"</i> can no longer be paid as if $279.99
          were the monthly charge (that paid <b>5% × $279.99 = $14.00</b> per tablet in July 2026). Turning
          it off restores the old behaviour and <b>increases pay</b> on the next Run Calculation.
        </p>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: 6 }}>
          Multi-month %-of-MRC is paid on:
          <select style={sel} value={settings.installment_mrc_basis || 'plan_line'}
            onChange={e => setSettings({ ...settings, installment_mrc_basis: e.target.value })}>
            <option value="plan_line">The activation's rate-plan line (default)</option>
            <option value="trigger_line">Legacy — whichever line triggered the schedule</option>
          </select>
        </label>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 12px', maxWidth: 760 }}>
          A multi-month installment pays <b>once per activation</b>, as a percentage of that activation's
          <b> monthly charge</b>. On the default the MRC is read from the sale's rate-plan line — the
          confirmed <a href="#mrc" style={{ color: 'var(--accent)' }}>MRC mapping</a> first, then a
          description that states a monthly amount, then the rate-plan line matcher below. The legacy
          option resolves the MRC from whichever line matched the trigger, which on a POS that stamps the
          same Contract Type on every line let a <b>handset's price</b> be paid as if it were a monthly
          charge. <b>Switching to legacy can increase pay</b> — it takes effect on the next Run Calculation.
        </p>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: 6 }}>
          Treat a calculation as dead after:
          <input type="number" min={1} max={1440} style={{ ...sel, width: 110 }}
            placeholder="20"
            value={settings.calc_stale_minutes ?? ''}
            onChange={e => setSettings({ ...settings, calc_stale_minutes: e.target.value === '' ? null : Number(e.target.value) })} />
          minutes
        </label>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 12px', maxWidth: 760 }}>
          Only <b>one</b> calculation can run for a month at a time — pressing Run Calculation again while
          one is still going is refused, so the two runs can&apos;t overwrite each other half-way and leave
          the month part-written. If a calculation is interrupted (a deploy, a restart) it can be left
          marked &quot;running&quot; forever; after this many minutes the next Run Calculation assumes it
          died and takes over. Leave blank for the default of <b>20 minutes</b>. Set it comfortably above
          your longest real calculation. <b>This does not change anyone&apos;s pay.</b>
        </p>
        <button className="btn btn-primary" onClick={saveSettings}>Save pay settings</button>
      </div>

      {/* ── Rate-plan line matcher (mig 233 — the %-of-MRC basis) ────────────────────────── */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>Which line carries the rate plan (multi-month MRC basis)</div>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 12px', maxWidth: 900 }}>
          One activation rings several lines — a handset, a rate plan, a SIM. This says which of them
          carries the <b>monthly charge</b> a %-of-MRC installment is a percentage of. A confirmed
          <a href="#mrc" style={{ color: 'var(--accent)' }}> MRC mapping</a> always wins, and a description
          that states a monthly amount (&quot;$25/mo&quot;, &quot;MRC $30&quot;) is always trusted; these
          settings decide the rest. Keywords match <b>whole words</b> — &quot;plan&quot; never matches
          &quot;PLANTRONICS&quot;. If no rate-plan line can be identified the installment resolves to
          <b> $0</b> rather than paying a percentage of a device price, and the
          <a href="#preview" style={{ color: 'var(--accent)' }}> preview</a> lists every such activation.
          {planLineOpts.is_default ? ' Currently using the seeded default.' : ' Currently using a tenant override.'}
          {!planLineOpts.ready && <b> Migration 233 is not applied yet — the engine uses the default and saves will fail until it runs.</b>}
        </p>
        {planLine && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, marginBottom: 12 }}>
            <TagPicker label="Rate-plan departments" values={planLine.departments} options={planLineOpts.departments}
              onChange={v => setPlanLine({ ...planLine, departments: v })} />
            <TagPicker label="Rate-plan categories" values={planLine.categories} options={planLineOpts.categories}
              onChange={v => setPlanLine({ ...planLine, categories: v })} />
            <TagPicker label="Product-desc keywords" values={planLine.product_keywords} allowFree
              onChange={v => setPlanLine({ ...planLine, product_keywords: v })} />
          </div>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary" onClick={() => savePlanLine(false)}>Save rate-plan matcher</button>
          <button className="btn" onClick={() => savePlanLine(true)}>Reset to default</button>
        </div>
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

      {/* ── Expected vs Earned pointer (mig 258, owner directive 2026-08-01) ───────────── */}
      <div className="card" style={{ marginBottom: 20, borderLeft: '4px solid var(--blue)' }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>Expected vs Earned — months 2–6</div>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 8px', maxWidth: 860 }}>
          Every month on these schedules now shows what it <b>will</b> pay (expected) next to what it{' '}
          <b>has</b> paid (earned). Earned fills in on its own the moment the carrier shows us paid.
          Expected is a column only — it is never added to anyone&rsquo;s commission. If a statement is
          late or the system misses one, someone with permission can move a single month across, with a
          reason, and that decision survives every recalculation.
        </p>
        <a className="btn" href="/commcalc/expected-commission">Open Expected vs Earned →</a>
      </div>

      {/* ── Qualifying device categories (mig 245, owner directive 2026-07-27) ─────────── */}
      <div className="card" style={{ marginBottom: 20 }} id="categories">
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Qualifying device categories</div>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 12px', maxWidth: 820 }}>
          Which activations earn a multi-month installment at all. Untick a category and those chains stop
          paying from the <b>next Run Calculation</b> — nothing moves right now. Every excluded activation
          is listed in the preview + Run-Calculation warnings with the dollars involved, so an unticked box
          can never become a silent zero.
        </p>
        {catCfg && !catCfg.ready && (
          <div style={{ marginBottom: 10, padding: '8px 10px', borderLeft: '3px solid var(--amber)', background: 'var(--surface2)', fontSize: 12 }}>
            Migration 245 isn't applied yet, so these boxes can't be saved. The engine is already using the
            defaults below (tablets + SIM excluded).
          </div>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, marginBottom: 10 }}>
          {(catCfg?.categories || []).map(c => (
            <label key={c.key} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
              <input type="checkbox" checked={catDraft[c.key] !== false}
                onChange={e => setCatDraft({ ...catDraft, [c.key]: e.target.checked })} />
              {c.label}
              {catCfg?.defaults?.[c.key] === false && <span className="badge" style={{ background: 'var(--surface2)', color: 'var(--text3)', fontSize: 10 }}>off by default</span>}
            </label>
          ))}
        </div>
        {catDraft.sim === false && (
          <div style={{ marginBottom: 10, padding: '8px 10px', borderLeft: '3px solid var(--amber)', background: 'var(--surface2)', fontSize: 12, maxWidth: 820 }}>
            <b>Heads up on SIM:</b> a BYOD activation whose receipt has only a SIM kit + a rate plan (the
            customer brought their own phone) is classified as <b>SIM</b> — so unticking this also stops
            those real activations from paying. A SIM sold <i>with</i> a handset stays a phone. Run the
            impact check below to see exactly whose pay moves.
          </div>
        )}
        {catDraft.unknown === false && (
          <div style={{ marginBottom: 10, padding: '8px 10px', borderLeft: '3px solid var(--amber)', background: 'var(--surface2)', fontSize: 12, maxWidth: 820 }}>
            With "Could not be classified" unticked, an activation we cannot categorise pays nothing. It is
            still reported in the warnings — but consider adding a rule below instead.
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
          <button className="btn btn-primary" onClick={() => saveCategories(false)}>Save categories</button>
          <button className="btn" onClick={() => saveCategories(true)}>Reset to defaults</button>
          <button className="btn" onClick={runImpact}>Check impact for {period}</button>
          {catCfg?.is_default && <span style={{ fontSize: 12, color: 'var(--text3)', alignSelf: 'center' }}>using the built-in defaults</span>}
        </div>

        {impact && (
          <div style={{ marginBottom: 14, fontSize: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              Impact for {impact.period} — read-only, nothing was recalculated
            </div>
            <div style={{ color: 'var(--text2)', marginBottom: 6 }}>
              Before this release <b>{fmt(impact.totals?.before || 0)}</b> → with corrected monthly
              charges <b>{fmt(impact.totals?.mrc_corrected || 0)}</b> → with your category ticks{' '}
              <b>{fmt(impact.totals?.now || 0)}</b> · change{' '}
              <b style={{ color: (impact.totals?.delta || 0) < 0 ? 'var(--red)' : 'var(--green)' }}>{fmt(impact.totals?.delta || 0)}</b>
              {impact.mrc_moves_count ? ` · ${impact.mrc_moves_count} activation(s) had their monthly charge corrected` : ''}
            </div>
            <table>
              <thead><tr><th>Rep</th><th style={{ textAlign: 'right' }}>Before</th><th style={{ textAlign: 'right' }}>Monthly-charge fix</th><th style={{ textAlign: 'right' }}>Category ticks</th><th style={{ textAlign: 'right' }}>Now</th><th style={{ textAlign: 'right' }}>Total change</th></tr></thead>
              <tbody>
                {(impact.by_rep || []).map((r: any) => (
                  <tr key={r.rep}>
                    <td>{r.rep}</td>
                    <td style={{ textAlign: 'right' }}>{fmt(r.before)}</td>
                    <td style={{ textAlign: 'right', color: r.delta_mrc < 0 ? 'var(--red)' : 'var(--text3)' }}>{fmt(r.delta_mrc)}</td>
                    <td style={{ textAlign: 'right', color: r.delta_category < 0 ? 'var(--red)' : 'var(--text3)' }}>{fmt(r.delta_category)}</td>
                    <td style={{ textAlign: 'right' }}>{fmt(r.now)}</td>
                    <td style={{ textAlign: 'right', color: r.delta < 0 ? 'var(--red)' : r.delta > 0 ? 'var(--green)' : 'var(--text3)' }}>{fmt(r.delta)}</td>
                  </tr>
                ))}
                {(impact.by_rep || []).length === 0 && <tr><td colSpan={6} style={{ color: 'var(--text3)' }}>No multi-month pay in this period.</td></tr>}
              </tbody>
            </table>
            {(impact.mrc_moves || []).length > 0 && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontWeight: 600, marginBottom: 3 }}>Corrected monthly charges</div>
                {(impact.mrc_moves || []).slice(0, 12).map((m: any, i: number) => (
                  <div key={i} style={{ color: 'var(--text2)' }}>
                    {m.rep} · M{m.month_index} · {m.label || m.imei} — MRC {fmt(m.mrc_before)} → {fmt(m.mrc_now)}
                    {' '}({fmt(m.amount_before)} → {fmt(m.amount_now)})
                    {m.still_paid === false ? <span style={{ color: 'var(--text3)' }}> · excluded by category, so it pays $0</span> : null}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* how the category is decided — tenant rules first, built-ins as the tail */}
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>How a sale's category is decided</div>
          <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 10px', maxWidth: 820 }}>
            Your rules are checked first, then the product catalog, then the POS Department / Category /
            product wording, then the serial's own shape (an IMEI means a device, an ICCID means a SIM).
            An activation's category is the <b>strongest</b> signal any of its lines carries, so a tablet
            sold with a SIM kit is a tablet and a case never out-votes the handset.
          </p>
          {catCfg && !catCfg.rules_ready && (
            <div style={{ marginBottom: 8, fontSize: 12, color: 'var(--text3)' }}>
              (Rule table not created yet — run migration 245. The built-in rules below are in force.)
            </div>
          )}
          {(catCfg?.rules || []).length > 0 && (
            <table style={{ marginBottom: 8 }}>
              <thead><tr><th>Category</th><th>Field</th><th>Op</th><th>Value</th><th>Priority</th><th></th></tr></thead>
              <tbody>
                {(catCfg?.rules || []).map((r: any) => (
                  <tr key={r.id}>
                    <td>{r.category_key}</td><td>{r.match_field}</td><td>{r.match_op}</td>
                    <td>{r.match_value}</td><td>{r.priority}</td>
                    <td><button className="btn" onClick={() => delRule(r.id)}>Delete</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <label style={{ fontSize: 12 }}>Category
              <select style={{ ...sel, width: 150 }} value={ruleDraft.category_key}
                onChange={e => setRuleDraft({ ...ruleDraft, category_key: e.target.value })}>
                {(catCfg?.categories || []).filter(c => c.key !== 'unknown').map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12 }}>Field
              <select style={{ ...sel, width: 160 }} value={ruleDraft.match_field}
                onChange={e => setRuleDraft({ ...ruleDraft, match_field: e.target.value, match_value: '' })}>
                {(catCfg?.match_fields || []).map(f => <option key={f} value={f}>{f}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12 }}>Op
              <select style={{ ...sel, width: 110 }} value={ruleDraft.match_op}
                onChange={e => setRuleDraft({ ...ruleDraft, match_op: e.target.value })}>
                {(catCfg?.match_ops || []).map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12 }}>Value
              {/* RULE THREE §3b — the values come from THIS tenant's own sale lines. */}
              <EntityPicker width={260} allowCreate clearable
                ariaLabel="Category rule value"
                placeholder={ruleDraft.match_field === 'serial_kind' ? 'imei / iccid…' : 'pick a real value…'}
                options={(ruleDraft.match_field === 'department' ? (catCfg?.departments || [])
                  : ruleDraft.match_field === 'category' ? (catCfg?.categories_seen || [])
                    : ruleDraft.match_field === 'serial_kind' ? ['imei', 'iccid']
                      : (catCfg?.products || [])).map(v => ({ id: v, label: v }))}
                value={ruleDraft.match_value || ''} createLabel={v => `Use “${v}”`}
                onChange={v => setRuleDraft({ ...ruleDraft, match_value: v || '' })} />
            </label>
            <label style={{ fontSize: 12 }}>Priority
              <input type="number" style={{ ...sel, width: 80 }} value={ruleDraft.priority}
                onChange={e => setRuleDraft({ ...ruleDraft, priority: Number(e.target.value) || 50 })} />
            </label>
            <button className="btn btn-primary" onClick={saveRule}>Add rule</button>
          </div>
          <button className="btn" style={{ fontSize: 12, marginTop: 10 }} onClick={() => setShowBuiltins(s => !s)}>
            {showBuiltins ? '▲ Hide' : '▼ Show'} the {(catCfg?.builtin_rules || []).length} built-in rules
          </button>
          {showBuiltins && (
            <table style={{ marginTop: 8 }}>
              <thead><tr><th>Category</th><th>Field</th><th>Op</th><th>Value</th><th>Priority</th></tr></thead>
              <tbody>
                {(catCfg?.builtin_rules || []).map((r: any, i: number) => (
                  <tr key={i}><td>{r.category_key}</td><td>{r.match_field}</td><td>{r.match_op}</td><td>{r.match_value}</td><td>{r.priority}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* ── Flat (one-time) payout by category (mig 256, owner directive 2026-08-01) ──── */}
      <div className="card" style={{ marginBottom: 20 }} id="flat-payout">
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Flat (one-time) payout by category</div>
        <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 12px', maxWidth: 860 }}>
          Some products are not paid month after month — home internet / FWA is the usual one. Switch a
          category to <b>one-time</b> and its activations leave the monthly chain entirely: they pay the
          flat amount <b>you type</b>, once, and the schedule's other months stop paying for that
          category. Nothing moves until the next <b>Run Calculation</b>, and every month that stops is
          listed in the warnings with the dollars involved.
        </p>
        <div style={{ marginBottom: 10, padding: '8px 10px', borderLeft: '3px solid var(--amber)', background: 'var(--surface2)', fontSize: 12, maxWidth: 860 }}>
          <b>The amount is yours to enter.</b> There is no default and nothing is pre-filled. If you set a
          category to one-time and leave the amount <i>blank</i>, it does <b>not</b> take effect — those
          activations keep paying monthly exactly as they do today and the calculation warns you. We never
          guess a payout and we never turn a blank into $0.
        </div>
        {payCfg && !payCfg.ready && (
          <div style={{ marginBottom: 10, padding: '8px 10px', borderLeft: '3px solid var(--amber)', background: 'var(--surface2)', fontSize: 12 }}>
            Migration <code>{payCfg.migration}</code> isn't applied yet, so this can't be saved. Every
            category is on monthly installments — which is exactly today's behaviour.
          </div>
        )}
        <div className="table-wrapper" style={{ border: 'none', marginBottom: 10 }}>
          <table>
            <thead><tr><th>Category</th><th>How it pays</th><th>Flat amount</th><th>Paid in month</th><th>Status</th></tr></thead>
            <tbody>
              {(payCfg?.categories || []).map(c => {
                const d = payDraft[c.key] || { mode: 'installments', amount: '', pay_month: '1' }
                const isFlat = d.mode === 'flat_once'
                const blank = String(d.amount ?? '').trim() === ''
                return (
                  <tr key={c.key}>
                    <td>{c.label}</td>
                    <td>
                      <select style={sel} value={d.mode}
                        onChange={e => setPayDraft({ ...payDraft, [c.key]: { ...d, mode: e.target.value } })}>
                        {(payCfg?.modes || []).map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
                      </select>
                    </td>
                    <td>
                      <input style={{ ...sel, width: 110 }} inputMode="decimal" placeholder="you type it"
                        value={d.amount} disabled={!isFlat}
                        onChange={e => setPayDraft({ ...payDraft, [c.key]: { ...d, amount: e.target.value } })} />
                    </td>
                    <td>
                      <select style={{ ...sel, width: 80 }} value={d.pay_month} disabled={!isFlat}
                        onChange={e => setPayDraft({ ...payDraft, [c.key]: { ...d, pay_month: e.target.value } })}>
                        {Array.from({ length: payCfg?.max_pay_month || 12 }, (_, i) => String(i + 1)).map(m => <option key={m} value={m}>M{m}</option>)}
                      </select>
                    </td>
                    <td style={{ fontSize: 12 }}>
                      {!isFlat ? <span style={{ color: 'var(--text3)' }}>monthly installments</span>
                        : blank ? <span style={{ color: 'var(--amber)', fontWeight: 600 }}>⚠ no amount — still paying monthly</span>
                          : <span style={{ color: 'var(--green)', fontWeight: 600 }}>one-time ${Number(d.amount).toFixed(2)}</span>}
                    </td>
                  </tr>
                )
              })}
              {!payCfg && <tr><td colSpan={5} style={{ color: 'var(--text3)' }}>Loading…</td></tr>}
            </tbody>
          </table>
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn btn-primary" onClick={() => savePayout(false)}>Save flat payout</button>
          <button className="btn" onClick={() => savePayout(true)}>Reset — all monthly</button>
          {payCfg?.is_default && <span style={{ fontSize: 12, color: 'var(--text3)' }}>nothing configured — every category pays monthly</span>}
        </div>

        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>What would it cost? (read-only)</div>
          <p style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 8px', maxWidth: 860 }}>
            Try an amount without saving it. This runs the engine twice in memory for <b>{period}</b> and
            shows the per-rep difference. It writes nothing and recalculates nothing — and it will not
            produce a number unless you type an amount.
          </p>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
            <select style={sel} value={payHypo.category} onChange={e => setPayHypo({ ...payHypo, category: e.target.value })}>
              {(payCfg?.categories || []).map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
            <input style={{ ...sel, width: 120 }} inputMode="decimal" placeholder="flat $ amount"
              value={payHypo.amount} onChange={e => setPayHypo({ ...payHypo, amount: e.target.value })} />
            <select style={{ ...sel, width: 80 }} value={payHypo.pay_month} onChange={e => setPayHypo({ ...payHypo, pay_month: e.target.value })}>
              {Array.from({ length: payCfg?.max_pay_month || 12 }, (_, i) => String(i + 1)).map(m => <option key={m} value={m}>M{m}</option>)}
            </select>
            <button className="btn" onClick={runPayoutImpact}>Check impact for {period}</button>
          </div>
          {payImpact && (
            <div style={{ fontSize: 12 }}>
              {payImpact.hypothesis_note && (
                <div style={{ color: 'var(--amber)', marginBottom: 6 }}>{payImpact.hypothesis_note}</div>
              )}
              {payImpact.hypothesis && (
                <div style={{ color: 'var(--text2)', marginBottom: 6 }}>
                  At <b>{fmt(payImpact.hypothesis.amount)}</b> per {(payCfg?.categories || []).find(c => c.key === payImpact.hypothesis.category)?.label || payImpact.hypothesis.category} activation,
                  paid in M{payImpact.hypothesis.pay_month}: total multi-month pay {fmt(payImpact.totals?.now || 0)} →{' '}
                  <b>{fmt(payImpact.totals?.with_flat || 0)}</b> · change{' '}
                  <b style={{ color: (payImpact.totals?.delta || 0) < 0 ? 'var(--red)' : 'var(--green)' }}>{fmt(payImpact.totals?.delta || 0)}</b>
                </div>
              )}
              <table>
                <thead><tr><th>Rep</th><th style={{ textAlign: 'right' }}>Now</th><th style={{ textAlign: 'right' }}>With the flat amount</th><th style={{ textAlign: 'right' }}>Change</th></tr></thead>
                <tbody>
                  {(payImpact.by_rep || []).map((r: any) => (
                    <tr key={r.rep}>
                      <td>{r.rep}</td>
                      <td style={{ textAlign: 'right' }}>{fmt(r.now)}</td>
                      <td style={{ textAlign: 'right' }}>{fmt(r.with_flat)}</td>
                      <td style={{ textAlign: 'right', color: r.delta < 0 ? 'var(--red)' : r.delta > 0 ? 'var(--green)' : 'var(--text3)' }}>{fmt(r.delta)}</td>
                    </tr>
                  ))}
                  {(payImpact.by_rep || []).length === 0 && <tr><td colSpan={4} style={{ color: 'var(--text3)' }}>No multi-month pay in this period.</td></tr>}
                </tbody>
              </table>
              {(payImpact.warnings || []).length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontWeight: 600, marginBottom: 3 }}>What the engine says</div>
                  {(payImpact.warnings || []).slice(0, 10).map((w: any, i: number) => (
                    <div key={i} style={{ color: w.type === 'flat_amount_unconfigured' ? 'var(--amber)' : 'var(--text2)' }}>• {w.detail}</div>
                  ))}
                </div>
              )}
            </div>
          )}
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
          <label style={{ fontSize: 12 }}>Incentive Plan
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
            <select style={{ ...sel, width: '100%' }} value={draft.trigger_match_field}
              title={vocab.match_fields.find(f => f.value === draft.trigger_match_field)?.help || ''}
              onChange={e => setDraft({ ...draft, trigger_match_field: e.target.value })}>
              {vocab.match_fields.map(f => <option key={f.value} value={f.value} title={f.help}>{f.label}</option>)}
              {!vocab.match_fields.some(f => f.value === draft.trigger_match_field) &&
                <option value={draft.trigger_match_field}>{draft.trigger_match_field} (saved)</option>}
            </select>
          </label>
          {draft.trigger_match_field !== 'any' && (
            <label style={{ fontSize: 12 }}>Match op
              <select style={{ ...sel, width: '100%' }} value={draft.trigger_match_op || 'equals'}
                onChange={e => setDraft({ ...draft, trigger_match_op: e.target.value })}>
                {vocab.match_ops.map(o => <option key={o.value} value={o.value} title={o.help}>{o.label}</option>)}
              </select>
            </label>
          )}
          {draft.trigger_match_field !== 'any' && (
            <label style={{ fontSize: 12 }}>Match value
              {/* RULE THREE §3b — picked from this tenant's OWN sale lines. A trigger that matches nothing
                  starts no installment schedule at all, which is invisible until a rep asks where their
                  multi-month money went. */}
              <MatchValuePicker opts={planOpts} field={draft.trigger_match_field}
                op={draft.trigger_match_op || 'equals'} value={draft.trigger_match_value || ''} width={220}
                ariaLabel="Trigger match value"
                onChange={v => setDraft({ ...draft, trigger_match_value: v })} />
              {triggerCount && (
                <span style={{ fontSize: 10.5, color: triggerCount.lines === 0 ? '#b45309' : 'var(--text3)' }}>
                  {triggerCount.lines === 0
                    ? `⚠ matches nothing in the last ${planOpts?.window?.months || 3} months — no schedule would start`
                    : `${triggerCount.lines.toLocaleString()} sale line${triggerCount.lines === 1 ? '' : 's'} would trigger this schedule`}
                </span>
              )}
              {/* the model-name guard (owner 2026-07-27): a `contains` trigger on the item description
                  also catches device MODEL names — show WHAT it matches and whether the same word is a
                  value of another field (e.g. the financing tender). */}
              <MatchEvidence opts={planOpts} rule={{ match_field: draft.trigger_match_field, match_op: draft.trigger_match_op, match_value: draft.trigger_match_value }} />
            </label>
          )}
          <label style={{ fontSize: 12, gridColumn: '1 / -1' }}>Qualifying device categories for THIS schedule
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center', marginTop: 4 }}>
              <label style={{ display: 'flex', gap: 5, alignItems: 'center', fontSize: 12 }}>
                <input type="radio" checked={!draft.qualifying_categories}
                  onChange={() => setDraft({ ...draft, qualifying_categories: null })} />
                Use the tenant setting above
              </label>
              <label style={{ display: 'flex', gap: 5, alignItems: 'center', fontSize: 12 }}>
                <input type="radio" checked={!!draft.qualifying_categories}
                  onChange={() => setDraft({ ...draft, qualifying_categories: { ...(catCfg?.qualification || {}) } })} />
                Just for this schedule:
              </label>
              {draft.qualifying_categories && (catCfg?.categories || []).map(c => (
                <label key={c.key} style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 12 }}>
                  <input type="checkbox" checked={draft.qualifying_categories?.[c.key] !== false}
                    onChange={e => setDraft({ ...draft, qualifying_categories: { ...(draft.qualifying_categories || {}), [c.key]: e.target.checked } })} />
                  {c.label}
                </label>
              ))}
            </div>
          </label>
          <label style={{ fontSize: 12 }}>Effective from (cutover)<input type="date" style={{ ...sel, width: '100%' }} value={draft.effective_from} onChange={e => setDraft({ ...draft, effective_from: e.target.value })} /></label>
          <label style={{ fontSize: 12 }}>Effective to<input type="date" style={{ ...sel, width: '100%' }} value={draft.effective_to} onChange={e => setDraft({ ...draft, effective_to: e.target.value })} /></label>
          <label style={{ fontSize: 12 }}>Eligible sale months (overrides dates)
            {/* RULE THREE §3b: months are PICKED from the periods this tenant actually has sales for —
                a mistyped month ('Jun 2026') silently makes every sale ineligible. Free entry stays
                available for a period whose sales haven't landed yet. */}
            <EntityPicker multi width="100%" options={periodOptions} allowCreate
              value={draft.eligible_sale_periods || []} placeholder="all months (no restriction)…"
              createLabel={v => `Use “${v}”`} ariaLabel="Eligible sale months"
              onChange={vals => setDraft({ ...draft, eligible_sale_periods: vals })} />
          </label>
        </div>
        <div style={{ marginBottom: 10 }}><OptionsSourceNote opts={planOpts} /></div>
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
      <div className="card" id="mrc" style={{ marginBottom: 20 }}>
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
      <div className="card" id="preview">
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
              {(preview.chain_guard?.deduped || 0) > 0 ? ` · ${preview.chain_guard.deduped} duplicate line(s) of an activation merged into their chain` : ''}
              {(preview.category_guard?.excluded_chains || 0) > 0
                ? ` · ${preview.category_guard.excluded_chains} activation(s) excluded by category (${fmt(preview.category_guard.excluded_amount || 0)} not paid)` : ''}
            </div>
            {preview.category_guard && Object.keys(preview.category_guard.by_category || {}).length > 0 && (
              <div style={{ marginBottom: 8, fontSize: 12, color: 'var(--text2)' }}>
                {Object.entries(preview.category_guard.by_category || {}).map(([k, v]: any) => (
                  <span key={k} style={{ marginRight: 12 }}>
                    {k}: {v.chains} · {fmt(v.amount || 0)} {v.qualifies ? '' : '(excluded)'}
                  </span>
                ))}
              </div>
            )}
            {(preview.warnings || []).length > 0 && (
              <div style={{ marginBottom: 10, padding: '8px 10px', borderLeft: '3px solid var(--amber)', background: 'var(--surface2)', fontSize: 12 }}>
                <b>{preview.warnings.length} activation(s) need attention</b>
                {(preview.chain_guard?.mrc_unresolved || 0) > 0 && <span> · {preview.chain_guard.mrc_unresolved} with no identifiable rate-plan line (paid $0 instead of a % of a device price)</span>}
                {(preview.chain_guard?.mrc_ambiguous || 0) > 0 && <span> · {preview.chain_guard.mrc_ambiguous} where two lines imply different monthly charges</span>}
                <ul style={{ margin: '6px 0 0 16px', padding: 0 }}>
                  {preview.warnings.slice(0, 8).map((w: any, i: number) => (
                    <li key={i} style={{ color: 'var(--text2)', marginBottom: 3 }}>
                      <b>{w.type}</b> · {w.rep || '—'} · trans {w.trans_id || '—'} · M{w.month_index} — {w.detail}
                      {w.products ? <span style={{ color: 'var(--text3)' }}> [{(w.products || []).join(' | ')}]</span> : null}
                    </li>
                  ))}
                </ul>
                {preview.warnings.length > 8 && <div style={{ color: 'var(--text3)' }}>…and {preview.warnings.length - 8} more.</div>}
              </div>
            )}
            <table>
              <thead><tr><th>Rep</th><th>Device — Rate plan</th><th>Category</th><th>MDN</th><th>IMEI</th><th>Sale mo</th><th>Month</th><th>Kind</th><th>MRC</th><th style={{ textAlign: 'right' }}>$</th><th>Gate</th></tr></thead>
              <tbody>
                {(preview.ledger || []).slice(0, 40).map((l: any, i: number) => (
                  <tr key={i}>
                    <td>{l.epay_salesperson}</td>
                    <td style={{ fontSize: 11 }} title={l.display_label || ''}>{l.display_label || '—'}</td>
                    <td style={{ fontSize: 11 }}>{l.device_category || '—'}</td>
                    <td>{l.mdn}</td><td style={{ fontSize: 11 }}>{l.serial_1 || '—'}</td>
                    <td>{l.sale_period}</td><td>M{l.month_index}</td>
                    <td>{l.payout_kind}</td>
                    <td style={{ fontSize: 11 }} title={l.mrc_from_product || ''}>
                      {l.payout_kind === 'pct_mrc' ? `${fmt(l.mrc_at_pay || 0)} · ${l.mrc_source}` : '—'}
                      {l.chain_lines_merged ? <span style={{ color: 'var(--text3)' }}> ({l.chain_lines_merged} lines)</span> : null}
                    </td>
                    <td style={{ textAlign: 'right' }}>{fmt(l.amount || 0)}</td>
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
