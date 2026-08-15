'use client'
// Management Incentive (migration 852) — one framework for every management level. Build a plan
// (store-performance components + qualification-gated bonuses), ASSIGN it to an employee/role/level
// (same precedence as the employee commission plan), then COMPUTE a manager's payout for a period from
// the actuals + qualification metrics and move it draft → approved → paid. The Total Wireless default
// ($2,090 at full attainment) is seeded; clone/edit it or add plans per level.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'

type Comp = { label?: string; kind?: string; rate?: number; metric_source?: string; target_per_store?: number; store_count?: number | null; cap_at_target?: boolean; sort?: number }
type Bonus = { label?: string; kind?: string; amount?: number; gated_by?: string; config?: any; sort?: number }
type Qual = { metric_key?: string; label?: string; source?: string; op?: string; threshold?: number; unit?: string; applies_to?: string; sort?: number }
type Assign = { scope?: string; scope_value?: string; priority?: number }
type Plan = {
  id?: string; name?: string; level?: string; period_type?: string; consolidated_bonus_amount?: number
  is_active?: boolean; is_default?: boolean; notes?: string
  components?: Comp[]; bonuses?: Bonus[]; qualifiers?: Qual[]; assignments?: Assign[]
}

const KIND = ['percent', 'per_unit']
const BONUS_KIND = ['consolidated', 'inventory_selloff', 'flat']
const GATED = ['qualifiers', 'inventory_aging', 'manual', 'none']
const SOURCE = ['kpi', 'cash_deposit', 'inventory', 'manual']
const OP = ['lt', 'lte', 'gt', 'gte', 'eq']
const SCOPE = ['employee', 'role', 'market', 'store', 'default']
const OP_LABEL: Record<string, string> = { lt: '<', lte: '≤', gt: '>', gte: '≥', eq: '=' }

const money = (n: any) => `$${(Number(n) || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
const emptyPlan = (): Plan => ({
  name: '', level: 'district_manager', period_type: 'monthly', consolidated_bonus_amount: 300,
  is_active: true, is_default: false,
  components: [{ label: 'Accessory Sales', kind: 'percent', rate: 0.02, metric_source: 'accessory_gp', target_per_store: 8000, store_count: 7, cap_at_target: true }],
  bonuses: [], qualifiers: [], assignments: [{ scope: 'role', scope_value: 'district_manager', priority: 0 }],
})

export default function ManagementIncentivePage() {
  const [plans, setPlans] = useState<Plan[]>([])
  const [plan, setPlan] = useState<Plan | null>(null)
  const [tab, setTab] = useState<'plans' | 'compute'>('plans')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api('/api/v1/commcalc/management-incentive/plans')
      .then((r: any) => setPlans(r.plans || []))
      .catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [])
  useEffect(() => { load() }, [load])

  const lbl: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 3 }
  const inp: React.CSSProperties = { width: '100%', padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)', fontWeight: 600 }
  const td: React.CSSProperties = { padding: '4px 6px', verticalAlign: 'top' }

  // ── plan editing helpers ──
  const setP = (patch: Partial<Plan>) => setPlan(p => ({ ...(p || {}), ...patch }))
  const setRow = (key: keyof Plan, i: number, patch: any) =>
    setPlan(p => { const arr = [...((p as any)[key] || [])]; arr[i] = { ...arr[i], ...patch }; return { ...(p as any), [key]: arr } })
  const addRow = (key: keyof Plan, blank: any) => setPlan(p => ({ ...(p as any), [key]: [...((p as any)[key] || []), blank] }))
  const delRow = (key: keyof Plan, i: number) => setPlan(p => ({ ...(p as any), [key]: ((p as any)[key] || []).filter((_: any, j: number) => j !== i) }))

  async function savePlan() {
    if (!plan?.name?.trim()) { setMsg('Plan needs a name.'); return }
    setBusy(true); setMsg('')
    try {
      const r: any = await api('/api/v1/commcalc/management-incentive/plans', { method: 'POST', body: JSON.stringify(plan) })
      setMsg('✅ Saved.'); setPlan(r.plan); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false) }
  }
  async function deletePlan() {
    if (!plan?.id || !confirm(`Delete plan "${plan.name}"? (A seeded default returns on the next sync.)`)) return
    setBusy(true)
    try { await api(`/api/v1/commcalc/management-incentive/plans/${plan.id}`, { method: 'DELETE' }); setPlan(null); setMsg('Deleted.'); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  const fullOpportunity = useMemo(() => {
    if (!plan) return 0
    const comp = (plan.components || []).reduce((s, c) => {
      const cnt = (c.store_count ?? 0) || 0
      return s + (Number(c.rate) || 0) * (Number(c.target_per_store) || 0) * cnt
    }, 0)
    const bon = (plan.bonuses || []).reduce((s, b) => s + (b.kind === 'consolidated' ? (Number(plan.consolidated_bonus_amount) || 0) : (Number(b.amount) || 0)), 0)
    return comp + bon
  }, [plan])

  return (
    <div style={{ maxWidth: 1080 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>🏆 Management Incentives</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 14px', maxWidth: 820 }}>
        Incentive plans for managers, scored per period across the stores they run: store-performance
        components (paid on production vs target) plus qualification-gated bonuses. Assign a plan to an
        employee, a role/level, a market or store — most-specific wins, just like the rep commission plan.
      </p>

      <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
        {(['plans', 'compute'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)} className="btn"
            style={{ fontSize: 13, background: tab === t ? 'var(--accent)' : undefined, color: tab === t ? '#fff' : undefined }}>
            {t === 'plans' ? '🧩 Plans' : '🧮 Compute & Payouts'}
          </button>
        ))}
      </div>
      {msg && <div style={{ fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      {tab === 'plans' && (
        <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 16 }}>
          <div className="card" style={{ padding: 12, height: 'fit-content' }}>
            <button className="btn btn-primary" style={{ width: '100%', fontSize: 13, marginBottom: 8 }} onClick={() => setPlan(emptyPlan())}>+ New plan</button>
            {plans.map(p => (
              <div key={p.id} onClick={() => setPlan(JSON.parse(JSON.stringify(p)))}
                style={{ padding: '8px 9px', borderRadius: 7, cursor: 'pointer', fontSize: 13, marginBottom: 4,
                  background: plan?.id === p.id ? 'var(--surface2)' : 'transparent', border: '1px solid var(--border)' }}>
                <div style={{ fontWeight: 600 }}>{p.name} {p.is_default && <span title="Platform default" style={{ fontSize: 11 }}>⭐</span>}</div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{p.level || '—'} · {(p.components || []).length} comp · {(p.bonuses || []).length} bonus</div>
              </div>
            ))}
            {plans.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)' }}>No plans yet (has migration 852 run?).</div>}
          </div>

          {!plan ? <div style={{ color: 'var(--text3)', fontSize: 13, paddingTop: 8 }}>Pick a plan to edit, or start a new one.</div> : (
            <div className="card" style={{ padding: 18 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12, marginBottom: 14 }}>
                <label><span style={lbl}>Plan name</span><input style={inp} value={plan.name || ''} onChange={e => setP({ name: e.target.value })} /></label>
                <label><span style={lbl}>Management level</span><input style={inp} value={plan.level || ''} placeholder="district_manager" onChange={e => setP({ level: e.target.value })} /></label>
                <label><span style={lbl}>Consolidated bonus $</span><input style={inp} type="number" value={plan.consolidated_bonus_amount ?? ''} onChange={e => setP({ consolidated_bonus_amount: Number(e.target.value) })} /></label>
                <label><span style={lbl}>Active</span><select style={inp} value={plan.is_active ? '1' : '0'} onChange={e => setP({ is_active: e.target.value === '1' })}><option value="1">Active</option><option value="0">Inactive</option></select></label>
              </div>

              {/* Components */}
              <Section title="Store-performance components" onAdd={() => addRow('components', { label: '', kind: 'per_unit', rate: 0, metric_source: '', target_per_store: 0, store_count: null, cap_at_target: true })}>
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead><tr>{['Label', 'Type', 'Rate', 'Metric source', 'Target/store', 'Stores', 'Cap', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {(plan.components || []).map((c, i) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={td}><input style={inp} value={c.label || ''} onChange={e => setRow('components', i, { label: e.target.value })} /></td>
                        <td style={td}><select style={inp} value={c.kind} onChange={e => setRow('components', i, { kind: e.target.value })}>{KIND.map(k => <option key={k}>{k}</option>)}</select></td>
                        <td style={{ ...td, width: 70 }}><input style={inp} type="number" step="0.01" value={c.rate ?? ''} onChange={e => setRow('components', i, { rate: Number(e.target.value) })} /></td>
                        <td style={td}><input style={inp} value={c.metric_source || ''} placeholder="accessory_gp" onChange={e => setRow('components', i, { metric_source: e.target.value })} /></td>
                        <td style={{ ...td, width: 90 }}><input style={inp} type="number" value={c.target_per_store ?? ''} onChange={e => setRow('components', i, { target_per_store: Number(e.target.value) })} /></td>
                        <td style={{ ...td, width: 60 }}><input style={inp} type="number" value={c.store_count ?? ''} placeholder="auto" onChange={e => setRow('components', i, { store_count: e.target.value === '' ? null : Number(e.target.value) })} /></td>
                        <td style={{ ...td, width: 40, textAlign: 'center' }}><input type="checkbox" checked={!!c.cap_at_target} onChange={e => setRow('components', i, { cap_at_target: e.target.checked })} /></td>
                        <td style={td}><button className="btn" style={{ fontSize: 11, padding: '2px 6px' }} onClick={() => delRow('components', i)}>✕</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>percent: rate 0.02 = 2% of the $ metric · per_unit: rate is $ per unit · Stores blank = the manager&apos;s own store count · Cap = never pay past the target opportunity.</div>
              </Section>

              {/* Bonuses */}
              <Section title="Flat bonuses" onAdd={() => addRow('bonuses', { label: '', kind: 'flat', amount: 0, gated_by: 'none', config: {} })}>
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead><tr>{['Label', 'Kind', 'Amount', 'Gated by', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {(plan.bonuses || []).map((b, i) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={td}><input style={inp} value={b.label || ''} onChange={e => setRow('bonuses', i, { label: e.target.value })} /></td>
                        <td style={td}><select style={inp} value={b.kind} onChange={e => setRow('bonuses', i, { kind: e.target.value })}>{BONUS_KIND.map(k => <option key={k}>{k}</option>)}</select></td>
                        <td style={{ ...td, width: 90 }}><input style={inp} type="number" value={b.kind === 'consolidated' ? (plan.consolidated_bonus_amount ?? '') : (b.amount ?? '')} disabled={b.kind === 'consolidated'} title={b.kind === 'consolidated' ? 'Uses the plan-level Consolidated bonus $' : ''} onChange={e => setRow('bonuses', i, { amount: Number(e.target.value) })} /></td>
                        <td style={td}><select style={inp} value={b.gated_by} onChange={e => setRow('bonuses', i, { gated_by: e.target.value })}>{GATED.map(k => <option key={k}>{k}</option>)}</select></td>
                        <td style={td}><button className="btn" style={{ fontSize: 11, padding: '2px 6px' }} onClick={() => delRow('bonuses', i)}>✕</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>gated by <b>qualifiers</b> = earned only when all qualification metrics pass · <b>inventory_aging</b> = no device over 10 days · <b>manual</b> = you decide on the statement.</div>
              </Section>

              {/* Qualifiers */}
              <Section title="Qualification metrics (gate the consolidated bonus)" onAdd={() => addRow('qualifiers', { metric_key: '', label: '', source: 'kpi', op: 'gte', threshold: 0, unit: 'percent', applies_to: 'consolidated' })}>
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead><tr>{['Metric key', 'Label', 'Source', 'Test', 'Threshold', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {(plan.qualifiers || []).map((q, i) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={td}><input style={inp} value={q.metric_key || ''} placeholder="zulu" onChange={e => setRow('qualifiers', i, { metric_key: e.target.value })} /></td>
                        <td style={td}><input style={inp} value={q.label || ''} onChange={e => setRow('qualifiers', i, { label: e.target.value })} /></td>
                        <td style={td}><select style={inp} value={q.source} onChange={e => setRow('qualifiers', i, { source: e.target.value })}>{SOURCE.map(k => <option key={k}>{k}</option>)}</select></td>
                        <td style={{ ...td, width: 60 }}><select style={inp} value={q.op} onChange={e => setRow('qualifiers', i, { op: e.target.value })}>{OP.map(k => <option key={k} value={k}>{OP_LABEL[k]}</option>)}</select></td>
                        <td style={{ ...td, width: 80 }}><input style={inp} type="number" value={q.threshold ?? ''} onChange={e => setRow('qualifiers', i, { threshold: Number(e.target.value) })} /></td>
                        <td style={td}><button className="btn" style={{ fontSize: 11, padding: '2px 6px' }} onClick={() => delRow('qualifiers', i)}>✕</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Section>

              {/* Assignments */}
              <Section title="Assign to (employee / role / market / store / default)" onAdd={() => addRow('assignments', { scope: 'employee', scope_value: '', priority: 0 })}>
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead><tr>{['Scope', 'Value (name / role / market / store)', 'Priority', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {(plan.assignments || []).map((a, i) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ ...td, width: 120 }}><select style={inp} value={a.scope} onChange={e => setRow('assignments', i, { scope: e.target.value })}>{SCOPE.map(k => <option key={k}>{k}</option>)}</select></td>
                        <td style={td}><input style={inp} value={a.scope_value || ''} disabled={a.scope === 'default'} placeholder={a.scope === 'default' ? '(everyone)' : ''} onChange={e => setRow('assignments', i, { scope_value: e.target.value })} /></td>
                        <td style={{ ...td, width: 70 }}><input style={inp} type="number" value={a.priority ?? 0} onChange={e => setRow('assignments', i, { priority: Number(e.target.value) })} /></td>
                        <td style={td}><button className="btn" style={{ fontSize: 11, padding: '2px 6px' }} onClick={() => delRow('assignments', i)}>✕</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>Most-specific wins: employee &gt; role &gt; store &gt; market &gt; default. A per-employee row overrides a role/default plan.</div>
              </Section>

              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 16, borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                <button className="btn btn-primary" disabled={busy} onClick={savePlan}>{busy ? 'Saving…' : '💾 Save plan'}</button>
                {plan.id && <button className="btn" disabled={busy} style={{ color: '#dc2626' }} onClick={deletePlan}>Delete</button>}
                <span style={{ marginLeft: 'auto', fontSize: 13, color: 'var(--text2)' }}>Full-attainment opportunity: <b>{money(fullOpportunity)}</b></span>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'compute' && <ComputeTab plans={plans} />}
    </div>
  )
}

function Section({ title, onAdd, children }: { title: string; onAdd: () => void; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6 }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>{title}</div>
        <button className="btn" style={{ fontSize: 12, padding: '2px 8px', marginLeft: 'auto' }} onClick={onAdd}>+ Add</button>
      </div>
      {children}
    </div>
  )
}

// ── Compute & Payouts tab ─────────────────────────────────────────────────────────────────────
function ComputeTab({ plans }: { plans: Plan[] }) {
  const [planId, setPlanId] = useState('')
  const [employeeId, setEmployeeId] = useState('')
  const [employeeName, setEmployeeName] = useState('')
  const [period, setPeriod] = useState(() => new Date().toISOString().slice(0, 7))
  const [storeCount, setStoreCount] = useState<number | ''>('')
  const [actuals, setActuals] = useState<Record<string, number>>({})
  const [metrics, setMetrics] = useState<Record<string, number>>({})
  const [invOk, setInvOk] = useState(true)
  const [result, setResult] = useState<any>(null)
  const [payouts, setPayouts] = useState<any[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [pullInfo, setPullInfo] = useState<any>(null)

  const plan = plans.find(p => p.id === planId) || null
  const inp: React.CSSProperties = { width: '100%', padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const lbl: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: 'var(--text2)', display: 'block', marginBottom: 3 }

  const loadPayouts = useCallback(() => {
    if (!period) return
    api(`/api/v1/commcalc/management-incentive/payouts?period=${encodeURIComponent(period)}`)
      .then((r: any) => setPayouts(r.payouts || [])).catch(() => setPayouts([]))
  }, [period])
  useEffect(() => { loadPayouts() }, [loadPayouts])

  async function pull() {
    if (!plan || !employeeId) { setMsg('Pick a plan and enter the manager id first.'); return }
    setBusy(true); setMsg(''); setPullInfo(null)
    try {
      const r: any = await api('/api/v1/commcalc/management-incentive/resolve', {
        method: 'POST', body: JSON.stringify({ plan_id: planId, employee_id: employeeId, period }),
      })
      if (r.actuals) setActuals(a => ({ ...a, ...r.actuals }))
      if (r.qualifier_values) setMetrics(m => ({ ...m, ...r.qualifier_values }))
      if (typeof r.manager_store_count === 'number' && r.manager_store_count > 0) setStoreCount(r.manager_store_count)
      if (r.derived && typeof r.derived.inventory_aging === 'boolean') setInvOk(r.derived.inventory_aging)
      setPullInfo({ resolved: r.resolved || [], unresolved: r.unresolved || [], notes: r.notes || {}, stores: r.store_codes || [] })
      setMsg(`⤵ Pulled ${(r.resolved || []).length} value(s) across ${(r.store_codes || []).length} store(s). Review, then compute.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  async function compute(save: boolean) {
    if (!plan) { setMsg('Pick a plan.'); return }
    setBusy(true); setMsg('')
    try {
      const r: any = await api('/api/v1/commcalc/management-incentive/compute', {
        method: 'POST', body: JSON.stringify({
          plan_id: planId, employee_id: employeeId, employee_name: employeeName, period,
          manager_store_count: storeCount === '' ? undefined : storeCount,
          actuals, qualifier_values: metrics, derived: { inventory_aging: invOk }, save,
        }),
      })
      setResult(r.breakdown)
      if (save) { setMsg('✅ Saved as draft.'); loadPayouts() }
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  async function decide(id: string, decision: string) {
    try { await api(`/api/v1/commcalc/management-incentive/payouts/${id}/decision`, { method: 'POST', body: JSON.stringify({ decision }) }); loadPayouts() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  return (
    <div>
      {msg && <div style={{ fontSize: 13, marginBottom: 10 }}>{msg}</div>}
      <div className="card" style={{ padding: 18, marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12, marginBottom: 12 }}>
          <label><span style={lbl}>Plan</span><select style={inp} value={planId} onChange={e => { setPlanId(e.target.value); setResult(null) }}><option value="">— pick —</option>{plans.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label><span style={lbl}>Manager (employee id)</span><input style={inp} value={employeeId} onChange={e => setEmployeeId(e.target.value)} /></label>
          <label><span style={lbl}>Manager name</span><input style={inp} value={employeeName} onChange={e => setEmployeeName(e.target.value)} /></label>
          <label><span style={lbl}>Period</span><input style={inp} type="month" value={period} onChange={e => setPeriod(e.target.value)} /></label>
          <label><span style={lbl}>Store count</span><input style={inp} type="number" value={storeCount} placeholder="from plan" onChange={e => setStoreCount(e.target.value === '' ? '' : Number(e.target.value))} /></label>
        </div>

        {plan && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18 }}>
            <div>
              <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 6 }}>Actuals (production)</div>
              {(plan.components || []).map((c, i) => (
                <label key={i} style={{ display: 'block', marginBottom: 6 }}>
                  <span style={lbl}>{c.label} <span style={{ color: 'var(--text3)' }}>({c.metric_source})</span></span>
                  <input style={inp} type="number" value={actuals[c.metric_source || ''] ?? ''} onChange={e => setActuals(a => ({ ...a, [c.metric_source || '']: Number(e.target.value) }))} />
                </label>
              ))}
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 6 }}>Qualification metrics</div>
              {(plan.qualifiers || []).map((q, i) => (
                <label key={i} style={{ display: 'block', marginBottom: 6 }}>
                  <span style={lbl}>{q.label || q.metric_key} <span style={{ color: 'var(--text3)' }}>(need {OP_LABEL[q.op || 'gte']} {q.threshold})</span></span>
                  <input style={inp} type="number" value={metrics[q.metric_key || ''] ?? ''} onChange={e => setMetrics(m => ({ ...m, [q.metric_key || '']: Number(e.target.value) }))} />
                </label>
              ))}
              <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginTop: 4 }}>
                <input type="checkbox" checked={invOk} onChange={e => setInvOk(e.target.checked)} />
                No device over 10 days in stock (inventory bonus qualifies)
              </label>
            </div>
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, marginTop: 14, alignItems: 'center' }}>
          <button className="btn" disabled={busy || !plan || !employeeId} title="Auto-pull the numbers from the app's sales / DLAR / deposit / inventory data" onClick={pull}>{busy ? '…' : '⤵ Pull numbers'}</button>
          <button className="btn btn-primary" disabled={busy || !plan} onClick={() => compute(false)}>Compute</button>
          <button className="btn" disabled={busy || !plan || !employeeId} onClick={() => compute(true)}>Compute & save draft</button>
        </div>
        {pullInfo && (
          <div style={{ marginTop: 12, fontSize: 12, background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
            {pullInfo.resolved.length > 0 && <div style={{ marginBottom: 4 }}><b style={{ color: '#166534' }}>Auto-filled:</b> {pullInfo.resolved.join(', ')}</div>}
            {pullInfo.unresolved.length > 0 && <div style={{ marginBottom: 4 }}><b style={{ color: '#b45309' }}>Enter manually (no data source):</b> {pullInfo.unresolved.join(', ')}</div>}
            {Object.keys(pullInfo.notes || {}).length > 0 && (
              <ul style={{ margin: '6px 0 0', paddingLeft: 16, color: 'var(--text3)' }}>
                {Object.entries(pullInfo.notes).map(([k, v]: any) => <li key={k}><b>{k}:</b> {v}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>

      {result && (
        <div className="card" style={{ padding: 18, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', marginBottom: 10 }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>Statement</div>
            <div style={{ marginLeft: 'auto', fontSize: 20, fontWeight: 800, color: 'var(--accent)' }}>{money(result.total)}</div>
          </div>
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <tbody>
              {result.components?.map((c: any, i: number) => (
                <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '5px 6px' }}>{c.label}</td>
                  <td style={{ padding: '5px 6px', color: 'var(--text3)', fontSize: 12 }}>{c.actual} / {c.target_qty}{c.attainment != null ? ` · ${Math.round(c.attainment * 100)}%` : ''}</td>
                  <td style={{ padding: '5px 6px', textAlign: 'right', fontWeight: 600 }}>{money(c.payout)}</td>
                </tr>
              ))}
              {result.bonuses?.map((b: any, i: number) => (
                <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '5px 6px' }}>{b.label} {!b.earned && <span style={{ fontSize: 11, color: '#b45309' }}>· not earned</span>}</td>
                  <td style={{ padding: '5px 6px', color: 'var(--text3)', fontSize: 12 }}>{b.gated_by}</td>
                  <td style={{ padding: '5px 6px', textAlign: 'right', fontWeight: 600 }}>{money(b.payout)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.qualifiers?.length > 0 && (
            <div style={{ marginTop: 10, fontSize: 12 }}>
              <span style={{ fontWeight: 600 }}>Qualification: </span>
              {result.qualifiers.map((q: any, i: number) => (
                <span key={i} style={{ marginRight: 10, color: q.passed ? '#166534' : '#991b1b' }}>
                  {q.passed ? '✓' : '✕'} {q.label} ({q.value ?? '—'} {OP_LABEL[q.op] || q.op} {q.threshold})
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ padding: 18 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>Payouts — {period}</div>
        {payouts.length === 0 ? <div style={{ fontSize: 13, color: 'var(--text3)' }}>Nothing computed for this period yet.</div> : (
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Manager', 'Total', 'Qualified', 'Status', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {payouts.map(p => (
                <tr key={p.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '6px 8px' }}>{p.employee_name || p.employee_id}</td>
                  <td style={{ padding: '6px 8px', fontWeight: 600 }}>{money(p.total)}</td>
                  <td style={{ padding: '6px 8px' }}>{p.qualified ? '✓' : '—'}</td>
                  <td style={{ padding: '6px 8px' }}><span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: p.status === 'paid' ? '#dcfce7' : p.status === 'approved' ? '#dbeafe' : '#fef3c7', color: p.status === 'paid' ? '#166534' : p.status === 'approved' ? '#1e40af' : '#92400e' }}>{p.status}</span></td>
                  <td style={{ padding: '6px 8px' }}>
                    {p.status === 'draft' && <button className="btn" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => decide(p.id, 'approve')}>Approve</button>}{' '}
                    {p.status === 'approved' && <button className="btn btn-primary" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => decide(p.id, 'pay')}>Mark paid</button>}{' '}
                    {p.status !== 'draft' && <button className="btn" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => decide(p.id, 'deny')}>↩ Draft</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
