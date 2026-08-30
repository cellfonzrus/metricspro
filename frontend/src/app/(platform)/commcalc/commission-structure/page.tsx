'use client'
import { useState, useEffect, useMemo, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import RunCommissionButton from '../_lib/RunCommissionButton'

// EMPLOYEE COMMISSION STRUCTURE — the FRONT DOOR (owner directive 2026-08-26).
//
//   "I don't want commissions set up from the backend — the ENTIRE commission structure must be set up by
//    the user in the UI."
//
// ONE guided place that walks the owner through the whole structure without touching SQL or config:
//   1. pick / create the PLAN                (composes the existing /commcalc/commission-plans editor)
//   2. set the ACTIVATION + ACCESSORY payouts (guides to the plan editor's Rules; detects what's set)
//   3. set the per-plan ACTIVATION SOURCE     (the mig-297 dropdown — settable inline here)
//   4. confirm ACCESSORY CLASSIFICATION       (departments / categories / product-keywords + the
//                                              "definition decides pay" toggle — the /accessory-config API,
//                                              the SAME single source the Sales Report + Accessory Definition
//                                              pages edit; with a live "N lines classify as accessory" check)
//   5. ASSIGN the plan to reps                (composes the plan editor's assignment surface)
//   6. see the estimated $                    (the read-only /commission-plans/preview — changes no pay)
//
// NOTHING here changes payout math. It only writes the SAME two config surfaces the existing pages already
// write (commission_plan.activation_source via the plan-save, and accessory_config via /accessory-config),
// and every existing page keeps working. The estimate and every diagnostic is READ-ONLY; live pay moves
// only when Run Incentive is pressed for a period.

type Rule = {
  label?: string; match_field?: string; match_op?: string; match_value?: string
  qualifies?: boolean; payout_kind?: string; amount?: number; pct?: number; tiered?: boolean
  [k: string]: any
}
type Assign = { scope?: string; scope_value?: string | null; priority?: number; [k: string]: any }
type Plan = {
  id?: string; name: string; is_active?: boolean; activation_source?: string | null
  rules?: Rule[]; assignments?: Assign[]; [k: string]: any
}

const ACT_SRC_LABEL: Record<string, string> = {
  inherit: 'Inherit (default)', raw_sales: 'POS sales', activation_details: 'Activation Details report',
}

// The Exec MTD activation categories — the SAME columns for every tenant (Boost / Cricket just relabel);
// each is its own payout option so Upgrade / BYOD / Tablet / Home Internet can pay different rates.
const MTD_CATS: { key: string; label: string }[] = [
  { key: 'activation', label: 'New Activation' }, { key: 'port', label: 'Port' },
  { key: 'byod', label: 'BYOD' }, { key: 'tablet', label: 'Tablet' },
  { key: 'home_internet', label: 'Home Internet' }, { key: 'edge', label: 'Edge' },
  { key: 'upgrade', label: 'Upgrade' },
]

// The plan's simple defaults: the flat $/unit of its activation rule + its accessory %. Used to seed the
// per-category rate editor (flat rate on every category except Upgrade, which starts at $0).
function planFlatAndAcc(p?: Plan | null): { flat: number; acc: number } {
  let flat = 0, acc = 0
  for (const r of (p?.rules || [])) {
    if (r.qualifies === false) continue
    const mf = r.match_field || '', pk = r.payout_kind || ''
    if (!flat && pk === 'flat_per_unit' && ['activation_bucket', 'department'].includes(mf)) flat = Number(r.amount) || 0
    else if (!acc && mf === 'accessory' && String(pk).startsWith('pct')) acc = Number(r.pct) || 0
  }
  return { flat, acc }
}

// Does this plan carry an ACTIVATION payout rule? A flat $/unit rule keyed on activation_bucket
// (premium/byod) OR on department — the latter being the Activation Details "Department" (service-plan)
// values the owner checks to pay activations from the report.
function activationRules(p?: Plan | null): Rule[] {
  return (p?.rules || []).filter(r =>
    ['activation_bucket', 'department'].includes(r.match_field || '') &&
    (r.payout_kind || '') === 'flat_per_unit')
}
// Does this plan carry an ACCESSORY payout rule? (% of price on accessory=yes.)
function accessoryRules(p?: Plan | null): Rule[] {
  return (p?.rules || []).filter(r =>
    (r.match_field || '') === 'accessory' && String(r.payout_kind || '').startsWith('pct'))
}

const chip: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6, padding: '2px 8px', borderRadius: 12,
  background: 'var(--panel2, #f1f5f9)', border: '1px solid var(--border, #e2e8f0)', fontSize: 12,
}
const sectionNum: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 26,
  borderRadius: '50%', background: 'var(--accent, #2563eb)', color: '#fff', fontWeight: 700, fontSize: 13,
  flexShrink: 0,
}

function ChipEditor({ label, values, onChange, placeholder, disabled }: {
  label: string; values: string[]; onChange: (v: string[]) => void; placeholder?: string; disabled?: boolean
}) {
  const [input, setInput] = useState('')
  const add = () => {
    const parts = input.split(',').map(s => s.trim()).filter(Boolean)
    if (!parts.length) return
    const next = Array.from(new Set([...values, ...parts]))
    onChange(next); setInput('')
  }
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 4 }}>{label}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
        {values.length === 0 && <span style={{ color: 'var(--text2)', fontSize: 12 }}>None yet.</span>}
        {values.map(v => (
          <span key={v} style={chip}>
            {v}
            {!disabled && (
              <button type="button" onClick={() => onChange(values.filter(x => x !== v))}
                style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--text2)', fontSize: 13, padding: 0 }}
                aria-label={`Remove ${v}`}>×</button>
            )}
          </span>
        ))}
      </div>
      {!disabled && (
        <div style={{ display: 'flex', gap: 6 }}>
          <input className="input" value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
            placeholder={placeholder || 'Type a value, Enter to add'} style={{ flex: 1, maxWidth: 320 }} />
          <button type="button" className="btn btn-sm" onClick={add}>Add</button>
        </div>
      )}
    </div>
  )
}

export default function CommissionStructurePage() {
  const { period, setPeriod, periods } = usePeriod()
  const [plans, setPlans] = useState<Plan[]>([])
  const [planReady, setPlanReady] = useState(true)
  const [selId, setSelId] = useState<string>('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  // activation-source inline editor
  const [actSrc, setActSrc] = useState<string>('inherit')
  const [savingActSrc, setSavingActSrc] = useState(false)

  // accessory classification editor (the /accessory-config surface)
  const [acc, setAcc] = useState<any>(null)
  const [accSel, setAccSel] = useState<{ d: string[]; c: string[]; p: string[]; drives: boolean }>(
    { d: [], c: [], p: [], drives: false })
  const [accCanEdit, setAccCanEdit] = useState(true)
  const [accMsg, setAccMsg] = useState('')
  const [accCount, setAccCount] = useState<number | null>(null)
  const [accCountBusy, setAccCountBusy] = useState(false)

  // estimate
  const [estimate, setEstimate] = useState<any>(null)
  const [estBusy, setEstBusy] = useState(false)
  const [estWhatIf, setEstWhatIf] = useState(false)

  // commission-from-Executive-MTD (matches the report numbers) + per-category rate editor
  const [mtd, setMtd] = useState<any>(null)
  const [mtdBusy, setMtdBusy] = useState(false)
  // one rate per Exec MTD activation category (tenant-agnostic columns) + the accessory %
  const [mtdRates, setMtdRates] = useState<Record<string, number>>({})
  const [mtdAccPct, setMtdAccPct] = useState<number>(0)

  const selected = useMemo(() => plans.find(p => p.id === selId) || null, [plans, selId])

  const loadPlans = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api('/api/v1/commcalc/commission-plans')
      setPlanReady(r.ready !== false)
      const ps: Plan[] = r.plans || []
      setPlans(ps)
      setSelId(prev => (prev && ps.some(p => p.id === prev) ? prev : (ps[0]?.id || '')))
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setLoading(false) }
  }, [])

  const loadAcc = useCallback(async () => {
    try {
      const c = await api('/api/v1/commcalc/accessory-config')
      setAcc(c)
      setAccSel({
        d: c.departments || [], c: c.categories || [], p: c.product_keywords || [],
        drives: !!c.definition_drives_pay,
      })
    } catch (e: any) { setAccMsg('❌ ' + (e?.message || e)) }
  }, [])

  useEffect(() => { loadPlans(); loadAcc() }, [loadPlans, loadAcc])
  useEffect(() => { setActSrc(selected?.activation_source || 'inherit'); setEstimate(null) }, [selId, selected])
  // Seed the per-category rate editor from the selected plan: its flat rate on every category except
  // Upgrade ($0 by default — a separate, opt-in payout), and its accessory %.
  useEffect(() => {
    const { flat, acc } = planFlatAndAcc(selected)
    setMtdRates(Object.fromEntries(MTD_CATS.map(c => [c.key, c.key === 'upgrade' ? 0 : flat])))
    setMtdAccPct(acc)
    setMtd(null)
  }, [selId, selected])

  // ── STEP 3: save the per-plan activation source. Re-POSTs the FULL loaded plan (rules/tiers/assignments
  // included, byte-for-byte as GET returned them) with only activation_source changed — exactly what the
  // plan editor does on save, so no child rows are lost. The backend collapses an unknown value to 'inherit'.
  async function saveActivationSource(next: string) {
    if (!selected) return
    setSavingActSrc(true); setMsg('')
    try {
      const body = { ...selected, activation_source: next }
      await api('/api/v1/commcalc/commission-plans', { method: 'POST', body: JSON.stringify(body) })
      setActSrc(next)
      await loadPlans()
      setMsg('✅ Activation source saved. Recalculate the period to apply it to live pay.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setSavingActSrc(false) }
  }

  // ── STEP 4: save the accessory classification (the SAME /accessory-config the Sales Report + Accessory
  // Definition pages write). Partial save — only the keys we edit here are sent.
  async function saveAcc() {
    setAccMsg('Saving…')
    try {
      await api('/api/v1/commcalc/accessory-config', {
        method: 'PUT',
        body: JSON.stringify({
          departments: accSel.d, categories: accSel.c, product_keywords: accSel.p,
          definition_drives_pay: accSel.drives,
        }),
      })
      setAccMsg('✅ Saved.'); loadAcc()
    } catch (e: any) { setAccMsg('❌ ' + (e?.message || e)) }
  }

  // Live "N lines classify as accessory this period" — reads the Accessory Definition period view's
  // sku_coverage.accessory_lines. READ-ONLY; on demand only (one call), so it stays cheap.
  async function checkAccCount() {
    setAccCountBusy(true); setAccCount(null)
    try {
      const r = await api(`/api/v1/commcalc/accessory-definition?period=${encodeURIComponent(period)}`)
      setAccCount(Number(r?.sku_coverage?.accessory_lines ?? 0))
    } catch (e: any) { setAccMsg('❌ ' + (e?.message || e)) } finally { setAccCountBusy(false) }
  }

  // ── STEP 6: read-only estimate for the selected plan.
  // DEFAULT (assignment-scoped): preview WITHOUT plan_id, so every rep resolves to their OWN assigned
  // plan; we then show only the reps whose effective plan IS the selected one — "who does THIS plan
  // actually pay," and what they earn under it. WHAT-IF: pass plan_id, which the engine applies to ALL
  // reps (the org-wide hypothetical — that mode is why Chicago reps appeared on an NY-plan estimate).
  async function runEstimate() {
    if (!selected) return
    setEstBusy(true); setEstimate(null); setMsg('')
    try {
      const q = estWhatIf
        ? `?period=${encodeURIComponent(period)}&plan_id=${selected.id}`
        : `?period=${encodeURIComponent(period)}`
      setEstimate(await api(`/api/v1/commcalc/commission-plans/preview${q}`))
    } catch (e: any) { setMsg('❌ Estimate: ' + (e?.message || e)) } finally { setEstBusy(false) }
  }

  // ── Commission FROM Executive MTD: the SAME numbers the owner sees on that report drive the payout
  // (Total Activation × the plan's rate + Acc. Sales × the plan's %). One data source for report + pay,
  // so an accessory number visible on Exec MTD can never be missing from the commission. READ-ONLY.
  async function runMtd() {
    if (!selected) return
    setMtdBusy(true); setMtd(null); setMsg('')
    try {
      const rateStr = MTD_CATS.map(c => `${c.key}:${Number(mtdRates[c.key]) || 0}`).join(',')
      const q = `?period=${encodeURIComponent(period)}&plan_id=${selected.id}` +
        `&rates=${encodeURIComponent(rateStr)}&acc_pct=${Number(mtdAccPct) || 0}`
      setMtd(await api(`/api/v1/commcalc/commission-mtd/${encodeURIComponent(period)}${q}`))
    } catch (e: any) { setMsg('❌ MTD commission: ' + (e?.message || e)) } finally { setMtdBusy(false) }
  }

  const editHref = selected ? `/commcalc/commission-plans` : '/commcalc/commission-plans'
  const actRules = activationRules(selected)
  const accRules = accessoryRules(selected)
  const assignCount = (selected?.assignments || []).length
  const estAllRows: any[] = estimate?.by_rep || []
  // WHAT-IF: every row already IS the forced plan. SCOPED: keep only reps whose effective plan is this one.
  const estRows = estWhatIf ? estAllRows
    : estAllRows.filter(r => String(r.plan_id) === String(selected?.id))
  const estTotal = estRows.reduce((s, r) => s + (Number(r.total_payout) || 0), 0)
  // Plan distribution across the whole period (scoped mode) — the real diagnostic: how many reps resolve
  // to EACH plan. If the NY reps aren't on a distinct plan, they all show under one plan here.
  const planDist: [string, { reps: number; payout: number }][] = (() => {
    const m: Record<string, { reps: number; payout: number }> = {}
    for (const r of estAllRows) {
      const k = r.plan_name || '(no plan)'
      if (!m[k]) m[k] = { reps: 0, payout: 0 }
      m[k].reps += 1; m[k].payout += Number(r.total_payout) || 0
    }
    return Object.entries(m).sort((a, b) => b[1].reps - a[1].reps)
  })()

  return (
    <div style={{ maxWidth: 960 }}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧭 Employee Commission Structure</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
          Set up the entire commission structure here — no backend needed. Work top to bottom: pick a plan,
          set its activation &amp; accessory payouts, choose where activations come from, confirm what counts
          as an accessory, assign reps, then preview the estimate. Nothing you do here moves live pay until
          you press <b>Run Incentive</b> for a period.
        </p>
      </div>

      {msg && <div className="card" style={{ padding: 12, marginBottom: 14, fontSize: 13 }}>{msg}</div>}

      {!planReady && (
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          Commission plans are not enabled for this tenant yet. Set them up on{' '}
          <Link href="/commcalc/commission-plans" style={{ color: 'var(--accent)' }}>Incentive Plans</Link>.
        </div>
      )}

      {/* STEP 1 — pick / create the plan */}
      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span style={sectionNum}>1</span>
          <div style={{ fontWeight: 700 }}>Pick or create the plan</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select className="input" value={selId} onChange={e => setSelId(e.target.value)} disabled={loading || !plans.length}
            style={{ minWidth: 260 }}>
            {!plans.length && <option value="">No plans yet</option>}
            {plans.map(p => (
              <option key={p.id} value={p.id}>{p.name}{p.is_active === false ? ' (inactive)' : ''}</option>
            ))}
          </select>
          <Link href={editHref} className="btn btn-sm">🧮 Open plan editor / create a plan →</Link>
        </div>
        {selected && (
          <div style={{ marginTop: 10, fontSize: 13, color: 'var(--text2)' }}>
            <b style={{ color: 'var(--text)' }}>{selected.name}</b> — {(selected.rules || []).length} rule(s),{' '}
            {assignCount} assignment(s), activation source: <b>{ACT_SRC_LABEL[actSrc] || actSrc}</b>.
          </div>
        )}
      </div>

      {/* STEP 2 — activation + accessory payouts */}
      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span style={sectionNum}>2</span>
          <div style={{ fontWeight: 700 }}>Define the activation &amp; accessory payouts</div>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 10px' }}>
          These are <b>Rules</b> on the plan. An <b>activation payout</b> is a flat $ per unit on{' '}
          <code>activation_bucket in premium, byod</code>. An <b>accessory payout</b> is a % of price on{' '}
          <code>accessory = yes</code>. Add or edit them in the plan editor — this box just confirms what the
          selected plan carries.
        </p>
        {selected ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
            <div style={{ border: '1px solid var(--border, #e2e8f0)', borderRadius: 8, padding: 10 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>📶 Activation payout</div>
              {actRules.length ? actRules.map((r, i) => (
                <div key={i} style={{ fontSize: 12, color: 'var(--text2)' }}>
                  {r.label || 'Activation'} — <b>{fmt(Number(r.amount) || 0)}</b> per unit{' '}
                  ({r.match_op || 'in'} {r.match_value || 'premium, byod'})
                </div>
              )) : <div style={{ fontSize: 12, color: '#b45309' }}>No activation rule yet.</div>}
            </div>
            <div style={{ border: '1px solid var(--border, #e2e8f0)', borderRadius: 8, padding: 10 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>🎧 Accessory payout</div>
              {accRules.length ? accRules.map((r, i) => (
                <div key={i} style={{ fontSize: 12, color: 'var(--text2)' }}>
                  {r.label || 'Accessory'} — <b>{Number(r.pct) || 0}%</b> of price ({r.payout_kind})
                </div>
              )) : <div style={{ fontSize: 12, color: '#b45309' }}>No accessory rule yet.</div>}
            </div>
          </div>
        ) : <div style={{ fontSize: 13, color: 'var(--text2)' }}>Pick a plan above first.</div>}
        <div style={{ marginTop: 10 }}>
          <Link href={editHref} className="btn btn-sm">✏️ Edit rules in the plan editor →</Link>
        </div>
      </div>

      {/* STEP 3 — activation source */}
      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span style={sectionNum}>3</span>
          <div style={{ fontWeight: 700 }}>Set the activation source</div>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 10px' }}>
          Where this plan's reps get their activations counted from. <b>Activation Details report</b> pays
          activations from the uploaded report and suppresses POS activations for this plan's reps (single
          source, no double-count); <b>POS sales</b> always counts POS activations; <b>Inherit</b> uses the
          org default. Also settable on the plan editor.
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="input" value={actSrc} onChange={e => setActSrc(e.target.value)}
            disabled={!selected} style={{ minWidth: 240 }}>
            <option value="inherit">Inherit (default)</option>
            <option value="raw_sales">POS sales</option>
            <option value="activation_details">Activation Details report</option>
          </select>
          <button className="btn btn-sm" disabled={!selected || savingActSrc || actSrc === (selected?.activation_source || 'inherit')}
            onClick={() => saveActivationSource(actSrc)}>
            {savingActSrc ? 'Saving…' : 'Save activation source'}
          </button>
        </div>
      </div>

      {/* STEP 4 — accessory classification */}
      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span style={sectionNum}>4</span>
          <div style={{ fontWeight: 700 }}>Confirm what counts as an accessory</div>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 10px' }}>
          A sale line is an accessory if its <b>Department</b> or <b>Category</b> is listed, or its product
          description contains a <b>keyword</b> (e.g. <code>KittedBranded</code>). This is the same tenant
          config the Sales Report and Accessory Definition pages edit. Changing it only changes what
          classifies as an accessory — no rate moves.
        </p>
        {acc == null ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>Loading…</div> : (
          <>
            {!accCanEdit && <div style={{ fontSize: 12, color: '#b45309', marginBottom: 8 }}>Read-only — you don't have the Classification permission.</div>}
            <ChipEditor label="Accessory departments" values={accSel.d} disabled={!accCanEdit}
              onChange={v => setAccSel(s => ({ ...s, d: v }))} placeholder="e.g. Accessories" />
            <ChipEditor label="Accessory categories" values={accSel.c} disabled={!accCanEdit}
              onChange={v => setAccSel(s => ({ ...s, c: v }))} placeholder="e.g. Accessory" />
            <ChipEditor label="Product-description keywords" values={accSel.p} disabled={!accCanEdit}
              onChange={v => setAccSel(s => ({ ...s, p: v }))} placeholder="e.g. KittedBranded" />
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, margin: '4px 0 12px' }}>
              <input type="checkbox" checked={accSel.drives} disabled={!accCanEdit}
                onChange={e => setAccSel(s => ({ ...s, drives: e.target.checked }))} />
              <span><b>Accessory Definition decides pay</b> — let this classification drive the plan engine's{' '}
                <code>accessory</code> match field.</span>
            </label>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              {accCanEdit && <button className="btn btn-sm" onClick={saveAcc}>Save classification</button>}
              <button className="btn btn-sm" onClick={checkAccCount} disabled={accCountBusy}>
                {accCountBusy ? 'Checking…' : `Check: how many lines classify as accessory in ${period}?`}
              </button>
              {accCount != null && (
                <span style={{ fontSize: 13 }}><b>{accCount.toLocaleString()}</b> line(s) classify as accessory this period.</span>
              )}
              <Link href="/commcalc/accessory-definition" className="btn btn-sm">🎧 Full Accessory Definition →</Link>
              <Link href="/commcalc/sales-report" className="btn btn-sm">🧾 Sales Report settings →</Link>
            </div>
            {accMsg && <div style={{ fontSize: 12, marginTop: 8 }}>{accMsg}</div>}
          </>
        )}
      </div>

      {/* STEP 5 — assign to reps */}
      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span style={sectionNum}>5</span>
          <div style={{ fontWeight: 700 }}>Assign the plan to reps</div>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 10px' }}>
          A rep pays under exactly one plan (most-specific assignment wins: employee &gt; role &gt; store &gt;
          market &gt; default). Assign people, roles, stores or markets in the plan editor's assignment surface.
        </p>
        <div style={{ fontSize: 13, marginBottom: 10 }}>
          {selected
            ? <>This plan currently has <b>{assignCount}</b> assignment(s).</>
            : <span style={{ color: 'var(--text2)' }}>Pick a plan above first.</span>}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Link href={editHref} className="btn btn-sm">👥 Assign reps in the plan editor →</Link>
          <Link href="/commcalc/plan-assignment-audit" className="btn btn-sm">🔎 Assignment audit →</Link>
        </div>
      </div>

      {/* STEP 6 — estimate */}
      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span style={sectionNum}>6</span>
          <div style={{ fontWeight: 700 }}>Preview the estimated payout</div>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 10px' }}>
          Read-only — changes no stored pay. By default this shows only the reps whose <b>assigned</b> plan
          is the selected one, and what they'd earn under it. Tick <b>“apply to everyone”</b> to instead see
          the org-wide hypothetical (the selected plan forced onto every rep, ignoring assignments).
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
          <select className="input" value={period} onChange={e => setPeriod(e.target.value)} style={{ minWidth: 160 }}>
            {periods.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <button className="btn btn-sm" onClick={runEstimate} disabled={!selected || estBusy}>
            {estBusy ? 'Estimating…' : (estWhatIf ? '💲 Estimate (apply to everyone)' : '💲 Estimate assigned reps')}
          </button>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
            <input type="checkbox" checked={estWhatIf} onChange={e => setEstWhatIf(e.target.checked)} />
            Apply to everyone (what-if)
          </label>
        </div>
        {estimate && (
          <div>
            {/* SCOPED diagnostic: how many reps resolve to EACH plan this period. A tenant with one broad
                plan shows every rep under it here — the fastest way to see the NY reps aren't split out. */}
            {!estWhatIf && planDist.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Who pays under which plan this period</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {planDist.map(([name, d]) => {
                    const isSel = String(name) === String(selected?.name)
                    return (
                      <span key={name} style={{ ...chip,
                        background: isSel ? 'var(--accent, #2563eb)' : chip.background,
                        color: isSel ? '#fff' : undefined }}>
                        {name}: <b>{d.reps}</b> rep(s) · {fmt(d.payout)}
                      </span>
                    )
                  })}
                </div>
              </div>
            )}
            <div style={{ fontWeight: 700, marginBottom: 6 }}>
              {estWhatIf
                ? <>Estimated total (applied to everyone): {fmt(Number(estTotal) || 0)}</>
                : <>Estimated total — {estRows.length} rep(s) on <b>{selected?.name}</b>: {fmt(Number(estTotal) || 0)}</>}
            </div>
            {estRows.length ? (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: 480 }}>
                  <thead><tr>{['Rep', 'Store', 'Plan', 'Units', 'Payout'].map(h =>
                    <th key={h} style={{ textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid var(--border, #e2e8f0)' }}>{h}</th>)}</tr></thead>
                  <tbody>
                    {estRows.map((r, i) => (
                      <tr key={i}>
                        <td style={{ padding: '4px 8px' }}>{r.rep}</td>
                        <td style={{ padding: '4px 8px' }}>{r.store}</td>
                        <td style={{ padding: '4px 8px' }}>{r.plan_name}</td>
                        <td style={{ padding: '4px 8px' }}>{r.qualifying_units}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(Number(r.total_payout) || 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ fontSize: 13, color: '#b45309' }}>
                No reps are assigned to <b>{selected?.name}</b> for {period}. Assign your NY / Luxelink reps to
                this plan in <b>Step 5</b> (or open the Assignment audit) — until then this plan pays no one, and
                those reps are paid by whichever plan the distribution above shows.
              </div>
            )}
          </div>
        )}
      </div>

      {/* COMMISSION FROM EXECUTIVE MTD — the same numbers the owner sees on that report drive the pay. */}
      <div className="card" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span style={{ ...sectionNum, background: '#0891b2' }}>📈</span>
          <div style={{ fontWeight: 700 }}>Commission from Executive MTD (matches the report)</div>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 10px' }}>
          Computes each rep's commission straight from the <b>Executive MTD</b> numbers, over this plan's
          stores. Set a <b>$ rate per activation category</b> below (each type is its own option — pay BYOD,
          Tablet or Home Internet differently, and <b>Upgrade</b> separately, $0 by default) plus the
          accessory %. Same data source as the Sales Report and Exec MTD, so the accessory number you see on
          that report is the one that pays. The categories are the same for every tenant — Boost / Cricket
          just relabel. Read-only; nothing pays until you Run below.
        </p>
        {/* PER-CATEGORY RATE EDITOR — one $ input per Exec MTD activation column + the accessory %. */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
          {MTD_CATS.map(c => (
            <label key={c.key} style={{ display: 'flex', flexDirection: 'column', fontSize: 11, gap: 2 }}>
              <span style={{ color: 'var(--text2)' }}>{c.label}</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <span style={{ fontSize: 12, color: 'var(--text3)' }}>$</span>
                <input className="input" type="number" step="0.5" min="0" style={{ width: 66 }}
                  value={mtdRates[c.key] ?? 0}
                  onChange={e => setMtdRates(s => ({ ...s, [c.key]: Number(e.target.value) }))} />
              </div>
            </label>
          ))}
          <label style={{ display: 'flex', flexDirection: 'column', fontSize: 11, gap: 2 }}>
            <span style={{ color: 'var(--text2)' }}>Accessories</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <input className="input" type="number" step="1" min="0" style={{ width: 60 }}
                value={Math.round((Number(mtdAccPct) || 0) * 1000) / 10}
                onChange={e => setMtdAccPct((Number(e.target.value) || 0) / 100)} />
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>%</span>
            </div>
          </label>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
          <select className="input" value={period} onChange={e => setPeriod(e.target.value)} style={{ minWidth: 160 }}>
            {periods.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <button className="btn btn-sm" onClick={runMtd} disabled={!selected || mtdBusy}>
            {mtdBusy ? 'Computing…' : '📈 Commission from Exec MTD'}
          </button>
        </div>
        {mtd && (
          <div>
            <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>
              <b>{((Number(mtd.accessory_pct) || 0) * 100).toFixed(1)}%</b> of accessories
              {mtd?.activation_source?.active && <> · activations from the Activation Details report</>}
            </div>
            {/* Per-category totals: count × rate = $ paid, so the owner sees where every dollar came from. */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
              {MTD_CATS.map(c => {
                const t = mtd?.totals?.by_category?.[c.key]
                if (!t || (!t.count && !t.rate)) return null
                return (
                  <span key={c.key} style={chip}>
                    {c.label}: <b>{t.count}</b> × {fmt(Number(t.rate) || 0)} = {fmt(Number(t.pay) || 0)}
                  </span>
                )
              })}
              <span style={{ ...chip, background: 'var(--panel2, #f1f5f9)' }}>
                Accessories: {fmt(Number(mtd?.totals?.acc_sales) || 0)} × {((Number(mtd.accessory_pct) || 0) * 100).toFixed(1)}% = {fmt(Number(mtd?.totals?.accessory_pay) || 0)}
              </span>
            </div>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>
              Total commission — {(mtd.by_rep || []).length} rep(s): {fmt(Number(mtd?.totals?.commission) || 0)}
              {' '}<span style={{ fontWeight: 400, color: 'var(--text2)' }}>
                (activation {fmt(Number(mtd?.totals?.activation_pay) || 0)} + accessory {fmt(Number(mtd?.totals?.accessory_pay) || 0)})
              </span>
            </div>
            {(mtd.by_rep || []).length ? (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: 520 }}>
                  <thead><tr>{['Rep', 'Activations', 'Acc. Sales', 'Activation $', 'Accessory $', 'Commission'].map(h =>
                    <th key={h} style={{ textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid var(--border, #e2e8f0)' }}>{h}</th>)}</tr></thead>
                  <tbody>
                    {(mtd.by_rep || []).map((r: any, i: number) => (
                      <tr key={i}>
                        <td style={{ padding: '4px 8px' }}>{r.employee}</td>
                        <td style={{ padding: '4px 8px' }}>{r.activations}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(Number(r.acc_sales) || 0)}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(Number(r.activation_pay) || 0)}</td>
                        <td style={{ padding: '4px 8px' }}>{fmt(Number(r.accessory_pay) || 0)}</td>
                        <td style={{ padding: '4px 8px', fontWeight: 600 }}>{fmt(Number(r.commission) || 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ fontSize: 13, color: '#b45309' }}>
                No Exec MTD rows for <b>{selected?.name}</b>’s stores in {period}. Check the plan's store
                assignments (Step 5) and that {period} has sales.
              </div>
            )}
          </div>
        )}
      </div>

      {/* APPLY — the shared Run control (nothing above moves live pay until this runs). */}
      <div className="card" style={{ padding: 16, marginBottom: 24, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>⚡ Apply the structure to live pay</div>
        <RunCommissionButton period={period} onPeriodChange={setPeriod}
          note="Everything above is config or read-only until this period is recalculated." />
      </div>
    </div>
  )
}
