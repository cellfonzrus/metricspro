'use client'
import { useState, useEffect, useMemo, useCallback } from 'react'
import { api, fmt } from '@/lib/client'

// ── COMMISSION COVERAGE WIZARD ──────────────────────────────────────────────────────────────────
// A guided, step-by-step replacement for the dense "lines not paying" table. It walks the owner from
// "$X across N lines are not paying" to a concrete, reviewed set of changes that make them pay, and
// only WRITES on the final Apply. MONEY-ADJACENT but conservative:
//   • it never invents an amount — every rate is typed by the owner;
//   • it changes NO payout formula — it only APPENDS owner-authored rules to a chosen plan and ATTACHES
//     that plan to reps the owner ticked;
//   • nothing is written until the owner clicks Apply on the review step; every step has a Skip.
// It reuses the SAME endpoints the coverage view and plan editor already use:
//   read   GET  /commission-plans/coverage-unmatched   (unpaid buckets + line-level reps, for estimates)
//   read   GET  /commission-plans/coverage             (reps with no plan attached)
//   write  POST /commission-plans                       (re-save target plan with appended rules)
//   write  POST /commission-plans/bulk-assign           (attach the plan to the ticked reps)
//   write  POST /recompute-rep                          (refresh just the touched reps' stored rows)
// The $-estimate is computed CLIENT-SIDE from each group's aggregates (lines/ext_price/gp) — no new math.

type Rule = {
  id?: string; label?: string; match_field: string; match_op: string; match_value: string
  qualifies?: boolean; payout_kind: string; amount?: number; pct?: number; tiered?: boolean
  unit_basis?: string; applies_scope_kind?: string; applies_scope_value?: string; sort?: number
}
type Plan = {
  id?: string; name: string; carrier_id?: string | null; base_tier_metric?: string | null
  is_active?: boolean; notes?: string | null; rules?: Rule[]; tiers?: any[]; assignments?: any[]
  tier_count_basis?: string | null; tier_match_field?: string | null; tier_match_op?: string | null
  tier_match_value?: string | null; tier_below_min_multiplier?: number | string | null
}
type UmGroup = {
  department?: string; category?: string; label: string; lines: number; ext_price: number; gp: number
  reps: number; rep_unassigned_lines: number; no_rule_matched_lines: number
  matching_rules: any[]; matching_rule_count: number; suggestion: string
}
type UnassignedRep = { rep: string; store?: string; market?: string; role?: string; lines?: number; ext_price?: number }

// The three payout shapes the wizard offers, mapped to the engine's payout_kind vocabulary. 'flat_per_unit'
// uses `amount` ($ per line/unit); the two percentages use `pct`. These are the exact kinds the plan editor
// and commission_engine.PAYOUT_KINDS already understand — the wizard writes the SAME rule shape.
const PAYOUTS: { kind: string; label: string; uses: 'amount' | 'pct'; unit: string }[] = [
  { kind: 'flat_per_unit', label: 'Flat $ per line / unit', uses: 'amount', unit: '$' },
  { kind: 'pct_price', label: '% of price', uses: 'pct', unit: '%' },
  { kind: 'pct_gp', label: '% of GP', uses: 'pct', unit: '%' },
]

type Decision = { pay: boolean; matchOn: 'category' | 'department'; kind: string; amount: string; pct: string }

const groupKey = (g: UmGroup) => `${(g.department || '').trim()}||${(g.category || '').trim()}`

// $-ESTIMATE (client-side only, from the group aggregates the read endpoint already returns):
//   flat $ per line = rate × lines ; % of price = pct/100 × ext_price ; % of GP = pct/100 × gp
function estimateFor(g: UmGroup, d: Decision): number {
  if (!d.pay) return 0
  const p = PAYOUTS.find(x => x.kind === d.kind)
  if (!p) return 0
  if (p.uses === 'amount') return (Number(d.amount) || 0) * (g.lines || 0)
  const rate = (Number(d.pct) || 0) / 100
  return d.kind === 'pct_gp' ? rate * (g.gp || 0) : rate * (g.ext_price || 0)
}

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '5px 8px', fontSize: 11, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '5px 8px', fontSize: 12.5, borderTop: '1px solid var(--border)' }
const stepDot = (active: boolean, done: boolean): React.CSSProperties => ({
  width: 22, height: 22, borderRadius: 11, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  fontSize: 11, fontWeight: 800, flex: '0 0 auto',
  background: done ? '#16a34a' : active ? 'var(--accent)' : 'var(--surface2)',
  color: done || active ? '#fff' : 'var(--text2)', border: '1px solid var(--border)',
})

export default function CoverageWizard(
  { open, onClose, period, plans, onApplied }:
  { open: boolean; onClose: () => void; period: string; plans: Plan[]; onApplied?: () => void },
) {
  const [step, setStep] = useState(1)
  const [wizPeriod, setWizPeriod] = useState(period)
  const [targetPlanId, setTargetPlanId] = useState('')
  const [um, setUm] = useState<any>(null)       // coverage-unmatched payload
  const [cov, setCov] = useState<any>(null)      // coverage payload (unassigned reps)
  const [loading, setLoading] = useState(false)
  const [loadErr, setLoadErr] = useState('')
  // step 2: ticked reps to attach the plan to (keyed by rep name)
  const [pickedReps, setPickedReps] = useState<Set<string>>(new Set())
  // step 3: per-group decision
  const [decisions, setDecisions] = useState<Record<string, Decision>>({})
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>(null)

  const activePlans = useMemo(() => (plans || []).filter(p => p.is_active !== false && p.id), [plans])

  const load = useCallback(async (per: string) => {
    setLoading(true); setLoadErr('')
    try {
      const [u, c] = await Promise.all([
        api(`/api/v1/commcalc/commission-plans/coverage-unmatched?period=${encodeURIComponent(per)}&group_by=category&limit=5000`),
        api(`/api/v1/commcalc/commission-plans/coverage?period=${encodeURIComponent(per)}`),
      ])
      setUm(u); setCov(c)
    } catch (e: any) { setLoadErr(e?.message || String(e)); setUm(null); setCov(null) }
    finally { setLoading(false) }
  }, [])

  // (re)load whenever the wizard opens or the chosen period changes
  useEffect(() => {
    if (!open) return
    setStep(1); setResult(null); setPickedReps(new Set()); setDecisions({})
    setWizPeriod(period)
  }, [open, period])
  useEffect(() => { if (open && wizPeriod) load(wizPeriod) }, [open, wizPeriod, load])

  // default target plan = the plan the most currently-covered reps are already on (fallback: most
  // assignments, then first active plan). Picking the majority plan means appended rules reach the
  // most reps with no further attaching.
  useEffect(() => {
    if (!open || targetPlanId) return
    if (!activePlans.length) return
    const counts: Record<string, number> = {}
    for (const r of (cov?.by_rep || [])) {
      const nm = String(r.plan_name || '').trim()
      if (nm) counts[nm] = (counts[nm] || 0) + 1
    }
    let best: Plan | undefined
    let bestN = -1
    for (const p of activePlans) {
      const n = counts[String(p.name || '').trim()] ?? -1
      const asg = (p.assignments || []).length
      const score = n >= 0 ? n * 1000 + asg : asg
      if (score > bestN) { bestN = score; best = p }
    }
    setTargetPlanId((best || activePlans[0]).id!)
  }, [open, cov, activePlans, targetPlanId])

  if (!open) return null

  const targetPlan = activePlans.find(p => p.id === targetPlanId)
  const totals = um?.totals || {}
  // NO rule anywhere → this group needs a NEW rule (step 3). A group whose matching_rule_count>0 is an
  // "attach a plan" case, handled by step 2 (reps with no plan), not a new-rule case.
  const noRuleGroups: UmGroup[] = (um?.groups || []).filter((g: UmGroup) => (g.matching_rule_count || 0) === 0)
  const unassignedReps: UnassignedRep[] = cov?.coverage?.unassigned_reps || []

  // queued work (nothing is written until Apply)
  const queuedRules = noRuleGroups
    .map(g => ({ g, d: decisions[groupKey(g)] }))
    .filter(x => x.d?.pay && (Number(x.d.amount) > 0 || Number(x.d.pct) > 0))
  const rulesEstimate = queuedRules.reduce((s, x) => s + estimateFor(x.g, x.d!), 0)
  const attachEligible = unassignedReps
    .filter(u => pickedReps.has(u.rep))
    .reduce((s, u) => s + (u.ext_price || 0), 0)

  const setDec = (k: string, patch: Partial<Decision>) =>
    setDecisions(prev => ({ ...prev, [k]: { ...(prev[k] || { pay: false, matchOn: 'category', kind: 'flat_per_unit', amount: '', pct: '' }), ...patch } }))
  const toggleRep = (rep: string) =>
    setPickedReps(prev => { const n = new Set(prev); n.has(rep) ? n.delete(rep) : n.add(rep); return n })

  // distinct reps whose lines fall in the "pay it" groups — recomputed after Apply so their stored row
  // reflects the new rule immediately (union'd with the reps we just attached the plan to).
  function affectedReps(): string[] {
    const set = new Set<string>()
    pickedReps.forEach(r => set.add(r))
    const wantKeys = new Set(queuedRules.map(x => groupKey(x.g)))
    for (const l of (um?.lines || [])) {
      const k = `${(l.department || '').trim()}||${(l.category || '').trim()}`
      if (wantKeys.has(k) && l.rep) set.add(l.rep)
    }
    return Array.from(set).filter(Boolean)
  }

  // Build the plan-save body EXACTLY as the plan editor's save() does (delete-then-insert of all
  // children, so we must round-trip the plan's existing rules/tiers/assignments + tier config), with our
  // new rules appended. This is why no new backend endpoint is needed: the existing save cleanly appends.
  function buildPlanBody(plan: Plan, newRules: Rule[]) {
    const merged: Rule[] = [...(plan.rules || []), ...newRules]
    return {
      id: plan.id,
      name: plan.name,
      carrier_id: plan.carrier_id || null,
      base_tier_metric: plan.base_tier_metric || null,
      is_active: plan.is_active !== false,
      notes: plan.notes || null,
      tier_count_basis: plan.tier_count_basis || '',
      tier_match_field: plan.tier_count_basis ? (plan.tier_match_field || 'any') : '',
      tier_match_op: plan.tier_count_basis ? (plan.tier_match_op || 'equals') : '',
      tier_match_value: plan.tier_count_basis ? (plan.tier_match_value || '') : '',
      tier_below_min_multiplier: (plan.tier_below_min_multiplier === null || plan.tier_below_min_multiplier === undefined || plan.tier_below_min_multiplier === '')
        ? '' : Number(plan.tier_below_min_multiplier),
      rules: merged.map((r, i) => ({ ...r, amount: Number(r.amount) || 0, pct: Number(r.pct) || 0, sort: i })),
      tiers: (plan.tiers || []).map((t: any, i: number) => ({ ...t, min_count: Number(t.min_count) || 0, multiplier: Number(t.multiplier) || 1, sort: i })),
      assignments: (plan.assignments || []).map((a: any) => ({ ...a, priority: Number(a.priority) || 0 })),
    }
  }

  async function apply() {
    if (!targetPlan) { setResult({ error: 'Pick a target plan first.' }); return }
    setApplying(true); setResult(null)
    const before = Number(totals.ext_price || 0)
    const steps: string[] = []
    try {
      // 1) APPEND RULES — one plan save with all queued rules.
      if (queuedRules.length) {
        const newRules: Rule[] = queuedRules.map(({ g, d }) => {
          const p = PAYOUTS.find(x => x.kind === d!.kind)!
          const value = d!.matchOn === 'department' ? (g.department || '') : (g.category || '')
          return {
            label: `${value || g.label} (wizard)`,
            match_field: d!.matchOn,
            match_op: 'equals',
            match_value: value,
            qualifies: true,
            payout_kind: d!.kind,
            amount: p.uses === 'amount' ? Number(d!.amount) || 0 : 0,
            pct: p.uses === 'pct' ? Number(d!.pct) || 0 : 0,
            tiered: false,
            unit_basis: '',
            applies_scope_kind: '',
            applies_scope_value: '',
          }
        })
        await api('/api/v1/commcalc/commission-plans', { method: 'POST', body: JSON.stringify(buildPlanBody(targetPlan, newRules)) })
        steps.push(`Added ${newRules.length} rule(s) to ${targetPlan.name}`)
      }
      // 2) ATTACH PLAN — bulk-assign the target plan to the ticked reps.
      if (pickedReps.size) {
        const r = await api('/api/v1/commcalc/commission-plans/bulk-assign', {
          method: 'POST',
          body: JSON.stringify({ plan_id: targetPlan.id, replace_existing: true, people: Array.from(pickedReps) }),
        })
        steps.push(`Attached ${targetPlan.name} to ${r?.summary?.rows_inserted ?? pickedReps.size} rep(s)`)
      }
      // 3) RECOMPUTE just the touched reps (per-rep, not a full-company run).
      const reps = affectedReps()
      let recomputed = 0
      for (const rep of reps) {
        try { await api('/api/v1/commcalc/recompute-rep', { method: 'POST', body: JSON.stringify({ period: wizPeriod, rep }) }); recomputed++ }
        catch { /* one rep failing must not sink the batch — the re-read below still reports reality */ }
      }
      if (reps.length) steps.push(`Recomputed ${recomputed}/${reps.length} affected rep(s)`)
      // 4) RE-READ coverage-unmatched to report what is now covered vs still unpaid.
      let after = before
      try {
        const u2 = await api(`/api/v1/commcalc/commission-plans/coverage-unmatched?period=${encodeURIComponent(wizPeriod)}&group_by=category&limit=5000`)
        setUm(u2)
        after = Number(u2?.totals?.ext_price || 0)
      } catch { /* keep the pre-apply number if the re-read fails */ }
      setResult({ ok: true, steps, covered: Math.max(0, before - after), stillUnpaid: after })
      onApplied && onApplied()
    } catch (e: any) {
      setResult({ error: e?.message || String(e), steps })
    } finally { setApplying(false) }
  }

  const STEPS = ['Scope', 'Reps with no plan', 'Unpaid categories', 'Review & Apply']
  const canApply = (queuedRules.length + pickedReps.size) > 0 && !!targetPlan

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)', display: 'flex', alignItems: 'flex-start', justifyContent: 'center', zIndex: 70, padding: 16, overflow: 'auto' }} onClick={onClose}>
      <div style={{ background: 'var(--surface)', color: 'var(--text)', borderRadius: 14, width: 820, maxWidth: '96vw', margin: '2vh 0', boxShadow: '0 12px 40px rgba(0,0,0,.35)' }} onClick={e => e.stopPropagation()}>
        {/* header + stepper */}
        <div style={{ padding: '16px 18px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ fontWeight: 800, fontSize: 16 }}>▶ Commission Coverage Wizard</div>
            <span style={{ flex: 1 }} />
            <button className="btn btn-secondary" onClick={onClose}>Close</button>
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
            {STEPS.map((s, i) => (
              <div key={s} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={stepDot(step === i + 1, step > i + 1)}>{step > i + 1 ? '✓' : i + 1}</span>
                <span style={{ fontSize: 12, fontWeight: step === i + 1 ? 700 : 500, color: step === i + 1 ? 'var(--text)' : 'var(--text2)' }}>{s}</span>
                {i < STEPS.length - 1 && <span style={{ width: 18, height: 1, background: 'var(--border)' }} />}
              </div>
            ))}
          </div>
        </div>

        <div style={{ padding: 18, minHeight: 240 }}>
          {loadErr && <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b', borderRadius: 8, padding: '8px 10px', fontSize: 13, marginBottom: 12 }}>Load failed: {loadErr}</div>}
          {loading && <div style={{ fontSize: 13, color: 'var(--text3)' }}>Loading coverage for {wizPeriod}…</div>}

          {!loading && result && (
            <div>
              {result.error
                ? <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b', borderRadius: 8, padding: '10px 12px', fontSize: 13 }}>❌ {result.error}</div>
                : (
                  <div style={{ background: '#f0fdf4', border: '1px solid #86efac', borderRadius: 10, padding: '14px 16px' }}>
                    <div style={{ fontWeight: 800, fontSize: 15, marginBottom: 6 }}>✅ Applied.</div>
                    <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 8 }}>
                      <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>Now covered</div><div style={{ fontSize: 20, fontWeight: 800, color: '#166534' }}>{fmt(result.covered)}</div></div>
                      <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>Still unpaid</div><div style={{ fontSize: 20, fontWeight: 800, color: '#b45309' }}>{fmt(result.stillUnpaid)}</div></div>
                    </div>
                    <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12.5 }}>
                      {(result.steps || []).map((s: string, i: number) => <li key={i}>{s}</li>)}
                    </ul>
                    <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
                      {result.stillUnpaid > 0.01 && (
                        <button className="btn btn-secondary" onClick={() => { setResult(null); setPickedReps(new Set()); setDecisions({}); setStep(1) }}>
                          ▶ Fix the rest
                        </button>
                      )}
                      <button className="btn btn-primary" onClick={onClose}>Done</button>
                    </div>
                  </div>
                )}
            </div>
          )}

          {/* STEP 1 — SCOPE */}
          {!loading && !result && step === 1 && (
            <div>
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 14 }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>
                  Period
                  <input style={{ ...sel, width: 180 }} value={wizPeriod} onChange={e => setWizPeriod(e.target.value)} placeholder="e.g. June 2026" />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>
                  Add new rules to plan
                  <select style={{ ...sel, minWidth: 260 }} value={targetPlanId} onChange={e => setTargetPlanId(e.target.value)}>
                    {!activePlans.length && <option value="">(no active plans)</option>}
                    {activePlans.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </label>
              </div>
              <div style={{ background: 'var(--surface2)', borderRadius: 10, padding: '14px 16px' }}>
                <div style={{ fontSize: 13, color: 'var(--text2)' }}>Not paying this period</div>
                <div style={{ fontSize: 24, fontWeight: 800 }}>{fmt(totals.ext_price || 0)} <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--text2)' }}>across {totals.lines || 0} lines</span></div>
                <div style={{ fontSize: 12.5, color: 'var(--text3)', marginTop: 4 }}>
                  {(totals.by_why?.rep_unassigned?.lines || 0)} line(s) from reps with no plan · {noRuleGroups.length} category group(s) with no rule anywhere.
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8 }}>
                  Read-only so far — nothing is written until you click Apply on the last step.
                </div>
              </div>
            </div>
          )}

          {/* STEP 2 — REPS WITH NO PLAN */}
          {!loading && !result && step === 2 && (
            <div>
              <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 8 }}>
                These reps sold but have <b>no plan attached</b>, so every line they sell pays $0. Tick the ones to attach
                <b> {targetPlan?.name}</b> to. This only queues the change.
              </div>
              {!unassignedReps.length && <div style={{ fontSize: 13, color: 'var(--text3)' }}>No reps are missing a plan for {wizPeriod}. 🎉</div>}
              {unassignedReps.length > 0 && (
                <>
                  <div style={{ marginBottom: 6 }}>
                    <button className="btn btn-secondary" onClick={() => setPickedReps(pickedReps.size === unassignedReps.length ? new Set() : new Set(unassignedReps.map(u => u.rep)))}>
                      {pickedReps.size === unassignedReps.length ? 'Clear all' : 'Select all'}
                    </button>
                    <span style={{ fontSize: 12.5, color: 'var(--text2)', marginLeft: 10 }}>
                      {pickedReps.size} selected · {fmt(attachEligible)} of sales would become eligible
                    </span>
                  </div>
                  <div style={{ overflowX: 'auto', maxHeight: 300, border: '1px solid var(--border)', borderRadius: 8 }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <thead><tr>{['', 'Rep', 'Store', 'Role', 'Lines', 'Sales $'].map((h, i) => <th key={i} style={th}>{h}</th>)}</tr></thead>
                      <tbody>{unassignedReps.map((u, i) => (
                        <tr key={i} style={{ background: pickedReps.has(u.rep) ? 'var(--surface2)' : undefined, cursor: 'pointer' }} onClick={() => toggleRep(u.rep)}>
                          <td style={td}><input type="checkbox" checked={pickedReps.has(u.rep)} onChange={() => toggleRep(u.rep)} onClick={e => e.stopPropagation()} /></td>
                          <td style={td}>{u.rep}</td><td style={td}>{u.store}</td><td style={td}>{u.role || '—'}</td>
                          <td style={td}>{u.lines}</td><td style={td}>{fmt(u.ext_price || 0)}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}

          {/* STEP 3 — UNPAID CATEGORIES WITH NO RULE */}
          {!loading && !result && step === 3 && (
            <div>
              <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 10 }}>
                No rule in any active plan pays these categories. For each, choose <b>Don't pay</b> or <b>Pay it</b> and type the rate —
                a rule is queued onto <b>{targetPlan?.name}</b>. You set every amount; a live estimate shows the impact.
              </div>
              {!noRuleGroups.length && <div style={{ fontSize: 13, color: 'var(--text3)' }}>No unpaid categories need a new rule for {wizPeriod}. 🎉</div>}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 380, overflowY: 'auto' }}>
                {noRuleGroups.map(g => {
                  const k = groupKey(g)
                  const d = decisions[k] || { pay: false, matchOn: (g.category || '').trim() ? 'category' : 'department', kind: 'flat_per_unit', amount: '', pct: '' } as Decision
                  const payout = PAYOUTS.find(x => x.kind === d.kind)!
                  const est = estimateFor(g, d)
                  const catBlank = !(g.category || '').trim()
                  return (
                    <div key={k} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12, background: d.pay ? 'var(--surface2)' : undefined }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                        <div style={{ fontWeight: 700, fontSize: 13 }}>{g.label}</div>
                        <span style={{ fontSize: 12, color: 'var(--text2)' }}>{g.lines} lines · {fmt(g.ext_price)} · GP {fmt(g.gp)}</span>
                        <span style={{ flex: 1 }} />
                        <div style={{ display: 'flex', gap: 4 }}>
                          <button className={d.pay ? 'btn btn-secondary' : 'btn btn-primary'} style={{ padding: '4px 10px' }} onClick={() => setDec(k, { ...d, pay: false })}>Don't pay</button>
                          <button className={d.pay ? 'btn btn-primary' : 'btn btn-secondary'} style={{ padding: '4px 10px' }} onClick={() => setDec(k, { ...d, pay: true })}>Pay it</button>
                        </div>
                      </div>
                      {d.pay && (
                        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap', marginTop: 10 }}>
                          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>
                            Payout type
                            <select style={sel} value={d.kind} onChange={e => setDec(k, { kind: e.target.value })}>
                              {PAYOUTS.map(p => <option key={p.kind} value={p.kind}>{p.label}</option>)}
                            </select>
                          </label>
                          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>
                            {payout.uses === 'amount' ? '$ per line' : '%'}
                            <input type="number" min={0} step={payout.uses === 'amount' ? 0.5 : 0.25} style={{ ...sel, width: 110 }}
                              value={payout.uses === 'amount' ? d.amount : d.pct}
                              onChange={e => setDec(k, payout.uses === 'amount' ? { amount: e.target.value } : { pct: e.target.value })}
                              placeholder="0" />
                          </label>
                          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>
                            Match on
                            <select style={sel} value={d.matchOn} onChange={e => setDec(k, { matchOn: e.target.value as any })}>
                              <option value="category" disabled={catBlank}>category{catBlank ? ' (blank)' : ` = ${g.category}`}</option>
                              <option value="department">department = {g.department || '(blank)'}</option>
                            </select>
                          </label>
                          <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                            <div style={{ fontSize: 11, color: 'var(--text2)' }}>Estimated new pay</div>
                            <div style={{ fontSize: 18, fontWeight: 800, color: '#166534' }}>{fmt(est)}</div>
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* STEP 4 — REVIEW & APPLY */}
          {!loading && !result && step === 4 && (
            <div>
              <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 10 }}>
                Review the exact changes. Nothing has been written yet. <b>Apply</b> saves {targetPlan?.name} once with the new
                rules, attaches it to the ticked reps, then recomputes just those reps.
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Rules to create ({queuedRules.length})</div>
                  {!queuedRules.length && <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>None.</div>}
                  {queuedRules.map(({ g, d }, i) => {
                    const p = PAYOUTS.find(x => x.kind === d!.kind)!
                    const value = d!.matchOn === 'department' ? g.department : g.category
                    return (
                      <div key={i} style={{ fontSize: 12.5, padding: '4px 0', borderTop: i ? '1px solid var(--border)' : undefined }}>
                        <b>{d!.matchOn}</b> = “{value || '(blank)'}” → {p.label} {p.uses === 'amount' ? `$${d!.amount}` : `${d!.pct}%`}
                        <span style={{ color: '#166534', fontWeight: 700 }}> · {fmt(estimateFor(g, d!))}</span>
                      </div>
                    )
                  })}
                </div>
                <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>Plan attaches ({pickedReps.size})</div>
                  {!pickedReps.size && <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>None.</div>}
                  {Array.from(pickedReps).map((r, i) => (
                    <div key={i} style={{ fontSize: 12.5, padding: '3px 0', borderTop: i ? '1px solid var(--border)' : undefined }}>{r} → {targetPlan?.name}</div>
                  ))}
                </div>
              </div>
              <div style={{ background: 'var(--surface2)', borderRadius: 10, padding: '12px 14px', display: 'flex', gap: 28, flexWrap: 'wrap' }}>
                <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>Estimated new $ paid (rules)</div><div style={{ fontSize: 20, fontWeight: 800, color: '#166534' }}>{fmt(rulesEstimate)}</div></div>
                <div><div style={{ fontSize: 11, color: 'var(--text2)' }}>Sales becoming eligible (attaches)</div><div style={{ fontSize: 20, fontWeight: 800 }}>{fmt(attachEligible)}</div></div>
              </div>
              {!canApply && <div style={{ fontSize: 12.5, color: '#b45309', marginTop: 10 }}>Queue at least one rule or one plan-attach before applying.</div>}
            </div>
          )}
        </div>

        {/* footer nav */}
        {!result && (
          <div style={{ padding: '12px 18px', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <button className="btn btn-secondary" disabled={step === 1 || applying} onClick={() => setStep(s => Math.max(1, s - 1))}>Back</button>
            <span style={{ flex: 1 }} />
            {step < 4 && <button className="btn btn-secondary" disabled={applying} onClick={() => setStep(s => Math.min(4, s + 1))}>Skip / I&apos;ll do it later</button>}
            {step < 4 && <button className="btn btn-primary" disabled={applying || !targetPlan} onClick={() => setStep(s => Math.min(4, s + 1))}>Next</button>}
            {step === 4 && <button className="btn btn-primary" disabled={applying || !canApply} onClick={apply}>{applying ? 'Applying…' : `Apply${canApply ? '' : ' (nothing queued)'}`}</button>}
          </div>
        )}
      </div>
    </div>
  )
}
