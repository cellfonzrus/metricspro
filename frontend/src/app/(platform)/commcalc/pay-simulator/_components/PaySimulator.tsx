'use client'
// EMPLOYEE PAY SIMULATOR — "what would I make if I sold X?" (owner 2026-08-03).
//
// ZERO PAY MATH LIVES IN THIS FILE. Every dollar shown comes back from
// POST /api/v1/commcalc/pay-simulator/simulate, which runs the REAL engine
// (`commission_engine.preview(..., sales_override=…)`) server-side. A duplicated formula in the
// browser is the documented failure mode for a tool like this: it silently keeps quoting last
// quarter's rules after the plan changes. So the UI's only job is to collect lever values, debounce,
// and render the server's answer.
//
// SELF-ONLY BY DEFAULT. The backend resolves WHO from the bearer token and 403s anything else. The
// employee picker below renders ONLY when the server says this caller may name another rep
// (`can_pick_rep` — a super-admin, or a company-wide 'all'/admin role, i.e. exactly the people who
// already read every rep's pay on the Rep Commission Report). A rep never sees it, and asking for it
// by hand still 403s server-side: the dropdown is an affordance, never the gate. The compact widget
// is the same component in `compact` mode and stays strictly self-only.
//
// MULTI-TENANT: nothing here names a tenant. `api()` already carries the active org (it appends
// `org_id=<active>` to org-less URLs and sends `x-active-org`), and the backend now RESOLVES the
// acting tenant from that verified org_id — which is what makes this page follow the tenant switcher
// instead of always answering for the house/Boost org.
//
// PICK-DON'T-TYPE (§3b): the levers are a fixed list rendered from the resolved plan's OWN rules
// (server-derived, never typed); the employee picker is a dropdown over the tenant's real roster,
// same-name people disambiguated by email; the quantity inputs are numeric steppers. Nothing here
// asks a human to type the name of an existing entity.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, fmt } from '@/lib/client'

export type Lever = {
  key: string; rule_id: string; label: string; payout_kind: string
  match_field: string; match_value: string | null
  count_unit: string; amount_input: boolean; amount_meaning: string | null; amount_label: string
  rate: number; rate_kind: 'pct' | 'flat'; tiered: boolean; qualifies: boolean
  simulatable: boolean; note: string | null
  /** The rep's ACTUAL figures for the period, from the pay engine — the simulator's starting point. */
  current?: { units: number; amount: number; month_total: number; payout: number; from_actuals: boolean }
  /** Present on percent levers: express the input per-unit, or as the month's total $. */
  basis_options?: { value: 'item' | 'month'; label: string; amount_label: string }[]
}
export type RosterPerson = { value: string; label: string; email?: string; store?: string; active?: boolean }
type Ctx = {
  ok: boolean; ready?: boolean; reason?: string | null; unsupported?: string
  rep?: string; store?: string; period?: string; carrier_mode?: string
  /** true only for a caller the SERVER says may model another rep (super-admin / 'all' scope). */
  can_pick_rep?: boolean
  /** the caller has no employee record in the tenant they're acting as → pick someone. */
  needs_rep?: boolean
  impersonated?: boolean
  reps?: RosterPerson[]
  plan?: { id: string; name: string } | null
  levers?: Lever[]
  seeded_from_actuals?: number
  seed_note?: string
  tier?: { metric: string; basis: string; below_min_multiplier: any; steps: { min_count: number; multiplier: number }[] } | null
}
type SimResult = {
  total_payout: number; base_payout: number; tiered_payout: number; tier_multiplier: number
  tier_units?: number; tier_basis?: string; qualifying_units: number
  setup_fee_comm?: number; setup_fee_collected?: number
  rules: { rule_id: string; label: string; payout_kind: string; tiered: boolean
           matched_lines: number; qualifying_units: number; payout: number }[]
}
type Sim = { ok: boolean; result: SimResult | null; warnings?: any[]; no_input?: boolean; reason?: string | null }

export type LeverInput = { units: number; amount: number; basis?: 'item' | 'month' }

const inp: React.CSSProperties = {
  width: 82, padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)',
  fontSize: 13, background: 'var(--surface)', textAlign: 'right',
}
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)', fontWeight: 700 }
const td: React.CSSProperties = { padding: '6px 8px', fontSize: 13 }

const rateLabel = (l: Lever) =>
  l.rate_kind === 'pct' ? `${(Number(l.rate || 0) * 100).toFixed(1)}%` : fmt(l.rate)

/** Start from the rep's OWN numbers for the period.
 *
 *  OWNER 2026-08-11: "should have the current numbers not just placeholder 10 each". This used to
 *  seed 1/10 units and $50/$25 — figures nobody chose, that render as a projection. `lever.current`
 *  now arrives from the server, computed by the SAME engine that pays the rep (preview() over their
 *  real lines), so the opening screen is their actual month.
 *
 *  A lever with no history seeds ZERO, deliberately. An invented quantity is how a simulator starts
 *  lying; an empty one asks a question. */
function seedInputs(levers: Lever[]): Record<string, LeverInput> {
  const out: Record<string, LeverInput> = {}
  for (const l of levers) {
    if (!l.simulatable) continue
    const c = l.current
    out[l.key] = {
      units: Math.max(0, Number(c?.units ?? 0)),
      amount: Number(c?.amount ?? 0),
      basis: 'item',
    }
  }
  return out
}

export function usePaySimulator(period: string) {
  const [ctx, setCtx] = useState<Ctx | null>(null)
  const [inputs, setInputs] = useState<Record<string, LeverInput>>({})
  const [sim, setSim] = useState<Sim | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  // Blank = "me". Only ever set from the server-supplied roster, and the server re-checks the
  // permission on every call — this is a convenience, not an authorization.
  const [rep, setRep] = useState('')
  const timer = useRef<any>(null)

  // The plan, the levers AND the tier all change per rep, so a rep switch refetches the context
  // rather than replaying the old levers against a different plan.
  useEffect(() => {
    let alive = true
    setCtx(null); setSim(null); setErr('')
    api(`/api/v1/commcalc/pay-simulator/context?period=${encodeURIComponent(period || '')}`
        + `&rep=${encodeURIComponent(rep || '')}`)
      .then((d: Ctx) => { if (!alive) return; setCtx(d); setInputs(seedInputs(d.levers || [])) })
      .catch((e) => { if (alive) setErr(String(e?.message || e)) })
    return () => { alive = false }
  }, [period, rep])

  // Live result, debounced — one server round-trip per settled edit, never a formula in the browser.
  const run = useCallback((next: Record<string, LeverInput>) => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      setBusy(true)
      api('/api/v1/commcalc/pay-simulator/simulate', {
        method: 'POST',
        body: JSON.stringify({ period, inputs: next, rep: rep || '' }),
      }).then((d: Sim) => setSim(d))
        .catch((e) => setErr(String(e?.message || e)))
        .finally(() => setBusy(false))
    }, 280)
  }, [period, rep])

  useEffect(() => { if (ctx?.ok && Object.keys(inputs).length) run(inputs) }, [ctx?.ok, inputs, run])

  const setLever = useCallback((key: string, patch: Partial<LeverInput>) => {
    setInputs(prev => ({ ...prev, [key]: { ...{ units: 0, amount: 0 }, ...prev[key], ...patch } }))
  }, [])

  return { ctx, inputs, setLever, sim, busy, err, rep, setRep }
}

function Unavailable({ ctx, err }: { ctx: Ctx | null; err: string }) {
  const msg = err || ctx?.reason ||
    'Your pay plan could not be resolved, so there is nothing to simulate yet.'
  return (
    <div style={{ borderLeft: '3px solid #d97706', background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 3 }}>
        {ctx?.needs_rep ? 'Pick an employee' : 'Not available yet'}
      </div>
      <div style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</div>
    </div>
  )
}

/** Employee picker — rendered ONLY when the server said this caller may model another rep. Pure
 *  dropdown over the acting tenant's own roster (§3b pick-don't-type); "Me" is always first. */
function RepPicker({ ctx, rep, setRep }: { ctx: Ctx; rep: string; setRep: (v: string) => void }) {
  const reps = ctx.reps || []
  if (!ctx.can_pick_rep || reps.length === 0) return null
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
      <label htmlFor="ps-rep" style={{ fontSize: 12, color: 'var(--text2)', fontWeight: 700 }}>Employee</label>
      <select id="ps-rep" value={rep} onChange={e => setRep(e.target.value)}
              style={{ padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)',
                       fontSize: 13, background: 'var(--surface)', minWidth: 220 }}>
        <option value="">— me —</option>
        {reps.map(p => (
          <option key={p.value} value={p.value}>
            {p.label}{p.active === false ? ' (inactive)' : ''}
          </option>
        ))}
      </select>
      <span style={{ fontSize: 11, color: 'var(--text3)' }}>
        {rep ? 'modelling this employee’s plan — projection only, nothing is saved' : 'your own plan'}
      </span>
    </div>
  )
}

/** The simulator body. `compact` renders the dashboard-widget layout (top 4 levers, one total);
 *  otherwise the full table with the per-rule breakdown. ONE component, so the widget and the page
 *  can never show different dollars. */
export default function PaySimulator({ period, compact = false }: { period: string; compact?: boolean }) {
  const { ctx, inputs, setLever, sim, busy, err, rep, setRep } = usePaySimulator(period)

  const levers = useMemo(() => (ctx?.levers || []).filter(l => l.simulatable), [ctx])
  const blocked = useMemo(() => (ctx?.levers || []).filter(l => !l.simulatable), [ctx])
  const shown = compact ? levers.slice(0, 4) : levers
  const r = sim?.result || null
  // The widget on someone else's dashboard must stay strictly self-only (see PaySimulatorWidget).
  const picker = !compact && ctx
    ? <RepPicker ctx={ctx} rep={rep} setRep={setRep} />
    : null

  // An unusable state still keeps the picker: a super-admin acting in a tenant they don't sell in
  // has no plan of their OWN, and the whole point is that they can pick whose plan to model.
  if (err || (ctx && !ctx.ok)) return <div>{picker}<Unavailable ctx={ctx} err={err} /></div>
  if (!ctx) return <div style={{ color: 'var(--text3)', fontSize: 13, padding: 8 }}>Loading your plan…</div>

  return (
    <div>
      {picker}
      <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
        Plan: <b>{ctx.plan?.name || '—'}</b>
        {ctx.impersonated && ctx.rep ? <> · <b>{ctx.rep}</b></> : null}
        {ctx.store ? <> · {ctx.store}</> : null} · {ctx.period}
        {' '}· projection only, nothing is saved
      </div>
      {/* Say WHERE the opening numbers came from. A simulator that silently pre-fills is
          indistinguishable from one that made the figures up — which is the bug this replaced. */}
      {ctx.seed_note && (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10, padding: '7px 10px',
                      background: 'var(--surface2, rgba(127,127,127,.07))', borderRadius: 8 }}>
          {(ctx.seeded_from_actuals ?? 0) > 0 ? '📌 ' : 'ℹ️ '}{ctx.seed_note}
        </div>
      )}

      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            <th style={th}>What you sell</th>
            <th style={{ ...th, textAlign: 'right' }}>Rate</th>
            <th style={{ ...th, textAlign: 'right' }}>How many</th>
            <th style={{ ...th, textAlign: 'right' }}>Amount</th>
            <th style={{ ...th, textAlign: 'right' }}>Pays</th>
          </tr>
        </thead>
        <tbody>
          {shown.map(l => {
            const v = inputs[l.key] || { units: 0, amount: 0 }
            const rb = (r?.rules || []).find(x => `rule:${x.rule_id}` === l.key)
            return (
              <tr key={l.key} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={td}>
                  {l.label}
                  {l.tiered && <span className="badge" style={{ marginLeft: 6, fontSize: 10 }}>tiered</span>}
                  {l.note && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{l.note}</div>}
                </td>
                <td style={{ ...td, textAlign: 'right', color: 'var(--text2)' }}>{rateLabel(l)}</td>
                <td style={{ ...td, textAlign: 'right' }}>
                  <input type="number" min={0} step={1} style={inp} value={v.units}
                    aria-label={`${l.label} — how many`}
                    onChange={e => setLever(l.key, { units: Math.max(0, Number(e.target.value) || 0) })} />
                </td>
                <td style={{ ...td, textAlign: 'right' }}>
                  {l.amount_input
                    ? (() => {
                        // OWNER 2026-08-11: express a percent lever EITHER as price-per-unit or as the
                        // month's total $. Same 'item' / 'month' vocabulary the What-If page uses, so
                        // the two surfaces can never mean different things by the same word.
                        const basis = v.basis || 'item'
                        const opt = (l.basis_options || []).find(o => o.value === basis)
                        const lbl = opt?.amount_label || l.amount_label
                        return (
                          <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
                            {(l.basis_options || []).length > 1 && (
                              <select value={basis} aria-label={`${l.label} — how to enter this`}
                                style={{ ...inp, width: 128, textAlign: 'left' }}
                                onChange={e => setLever(l.key, { basis: e.target.value as 'item' | 'month' })}>
                                {(l.basis_options || []).map(o =>
                                  <option key={o.value} value={o.value}>{o.label}</option>)}
                              </select>
                            )}
                            <input type="number" min={0} step={1} style={inp} value={v.amount}
                              aria-label={`${l.label} — ${lbl}`} title={lbl}
                              onChange={e => setLever(l.key, { amount: Math.max(0, Number(e.target.value) || 0) })} />
                          </div>
                        )
                      })()
                    : <span style={{ color: 'var(--text3)' }}>—</span>}
                </td>
                <td style={{ ...td, textAlign: 'right', fontWeight: 600 }}>
                  {rb ? fmt(rb.payout) : <span style={{ color: 'var(--text3)' }}>—</span>}
                </td>
              </tr>
            )
          })}
          {shown.length === 0 && (
            <tr><td style={{ ...td, color: 'var(--text3)' }} colSpan={5}>
              Your plan has no rules that can be modelled yet.
            </td></tr>
          )}
        </tbody>
      </table>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end',
                    gap: 12, marginTop: 14, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 12, color: 'var(--text3)' }}>
          {r && r.tier_multiplier != null && Number(r.tier_multiplier) !== 1 && (
            <>Tier ×{Number(r.tier_multiplier).toFixed(2)} applied on {r.tier_units ?? r.qualifying_units} {r.tier_basis || 'units'}<br /></>
          )}
          {r && Number(r.setup_fee_comm || 0) > 0 && <>Set-up fee item: {fmt(r.setup_fee_comm || 0)}<br /></>}
          {busy ? 'updating…' : 'projection only — nothing is saved and no pay is changed'}
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>
            Projected commission
          </div>
          <div style={{ fontSize: compact ? 28 : 36, fontWeight: 800, lineHeight: 1.1, color: 'var(--accent)' }}>
            {fmt(r?.total_payout || 0)}
          </div>
        </div>
      </div>

      {!compact && r && (r.rules || []).length > 0 && (
        <div style={{ marginTop: 16, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>How that adds up</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 4 }}>
            <span>Not tiered</span><span style={{ textAlign: 'right' }}>{fmt(r.base_payout)}</span>
            <span>Tiered (before ×{Number(r.tier_multiplier || 1).toFixed(2)})</span>
            <span style={{ textAlign: 'right' }}>{fmt(r.tiered_payout)}</span>
            {Number(r.setup_fee_comm || 0) > 0 && (
              <><span>Set-up / activation fee</span><span style={{ textAlign: 'right' }}>{fmt(r.setup_fee_comm || 0)}</span></>
            )}
            <span style={{ fontWeight: 700 }}>Total</span>
            <span style={{ textAlign: 'right', fontWeight: 700 }}>{fmt(r.total_payout)}</span>
          </div>
        </div>
      )}

      {(sim?.warnings || []).length > 0 && (
        <ul style={{ margin: '10px 0 0', paddingLeft: 16, fontSize: 11, color: '#b45309' }}>
          {(sim?.warnings || []).map((w: any, i: number) => <li key={i}>{w.message}</li>)}
        </ul>
      )}
      {!compact && blocked.length > 0 && (
        <ul style={{ margin: '8px 0 0', paddingLeft: 16, fontSize: 11, color: 'var(--text3)' }}>
          {blocked.map(l => <li key={l.key}><b>{l.label}</b> — {l.note}</li>)}
        </ul>
      )}
    </div>
  )
}
