'use client'
// YOUR INCENTIVE PLAN — a guided workflow, not a wall of settings (owner directive 2026-11-01).
//
// Owner, verbatim: "the plan which is active in incentive payout when you click on it it takes you to
// the page which has all kinds of Incentive structure listed which becomes confusing even to me ...
// When we click on the plan it should show the one which is active and below it should give the
// option to change the plan and when you click on change the plan it should show what the available
// options are and then clicking on the available options the user should be able to create a new plan
// — all of this should be a work flow than random data seen on the screen".
//
// THE STEPS ARE DATA, NOT LAYOUT. Everything below renders what
// GET /commcalc/incentive-plan/workflow returns: the active plan, the options, and the sentences that
// describe them. No label, option list or ordering is kept here — a second copy of the workflow in
// TypeScript is exactly how this screen and the engine would start disagreeing about which plan a
// tenant is on (backend: commcalc/plan_workflow.py, index §COMMCALC-54).
//
// IT WRITES THROUGH THE DOORS THAT ALREADY EXISTED. Picking an option asks the backend for the draft
// that choice implies (which endpoint + which body) and posts it unchanged — the plan save and the
// carrier-template cloner stay the only writers, and the money-config payload is never assembled in
// the browser.
//
// EVERY TENANT (project directive 2026-10-30): the engine, the plans and the options all come from
// the tenant's own rows. A Boost-engine tenant is told it is paid by the built-in KPI-tier engine
// instead of being shown a plan it is not paid by — never a silent fall back to another tenant's shape.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

type Cover = { scope: string; value: string | null }
type PlanSummary = {
  id: string; name: string; carrier_id: string | null; is_active: boolean
  rule_count: number; tier_count: number; assignment_count: number
  covers: Cover[]; notes: string | null
}
type ActiveStep = {
  key: 'active'; engine: string; built_in: boolean; plan: PlanSummary | null
  pays_nobody: boolean; can_change: boolean; headline: string; rates_href: string | null
}
type PlanOption = {
  kind: string; key: string; label: string; detail: string
  creates: 'plan' | 'schedules'; source_plan_id: string | null; caveat: string | null
}
type ChangeStep = { key: 'change'; action_label: string; headline: string; options: PlanOption[]; option_count: number }
type CreateStep = { key: 'create'; action_label: string; headline: string; then: string[]; creates_inactive: boolean }
type Step = ActiveStep | ChangeStep | CreateStep
type Carrier = { id: string; name: string | null; code: string | null; is_default: boolean }
type Workflow = {
  ready: boolean; engine: string | null; carrier_id: string | null
  steps: Step[]; carriers?: Carrier[]; advanced_href?: string; note?: string
}
type Draft = { kind: string; endpoint: string; body: Record<string, unknown>; copied_from?: string | null }

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

function Rail({ at }: { at: 1 | 2 | 3 }) {
  const steps = ['What you pay today', 'Pick a starting point', 'Create the plan']
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
      {steps.map((label, i) => {
        const n = i + 1
        const done = n < at
        const here = n === at
        return (
          <div key={label} style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderRadius: 999,
            fontSize: 12.5, fontWeight: here ? 700 : 600,
            background: here ? 'var(--c-bg-f0fdf4)' : 'var(--surface2)',
            color: here ? 'var(--c-fg-166534)' : done ? 'var(--text2)' : 'var(--text3)',
            border: `1px solid ${here ? 'var(--c-fg-166534)' : 'var(--border)'}`,
          }}>
            <span>{done ? '✓' : n}</span><span>{label}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function IncentivePlanWorkflow() {
  const [wf, setWf] = useState<Workflow | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [lens, setLens] = useState('')
  const [at, setAt] = useState<1 | 2 | 3>(1)
  const [picked, setPicked] = useState<PlanOption | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState('')

  // THE FETCH LIVES IN THE EFFECT and every state write happens in its own callback, behind an
  // `alive` flag. Writing state in the effect BODY (a `setLoading(true)` before the call) is the
  // react-hooks/set-state-in-effect shape the lint ratchet refuses, and the flag is what stops a
  // carrier switch landing its answer after the next one.
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let alive = true
    const q = lens ? `?carrier_id=${encodeURIComponent(lens)}` : ''
    api(`/api/v1/commcalc/incentive-plan/workflow${q}`)
      .then((d: Workflow) => { if (alive) { setWf(d); setErr(''); setLoading(false) } })
      .catch((e: unknown) => { if (alive) { setWf(null); setErr(errText(e)); setLoading(false) } })
    return () => { alive = false }
  }, [lens, reload])
  const reloadWorkflow = useCallback(() => setReload(n => n + 1), [])

  const stepOf = <T extends Step>(key: T['key']): T | null =>
    ((wf?.steps || []).find(s => s.key === key) as T | undefined) || null
  const active = stepOf<ActiveStep>('active')
  const change = stepOf<ChangeStep>('change')
  const create = stepOf<CreateStep>('create')

  function pick(option: PlanOption) {
    setPicked(option)
    setDone('')
    setErr('')
    setName(option.creates === 'schedules' ? '' : suggestName(option))
    setAt(3)
  }

  function suggestName(option: PlanOption): string {
    // A suggestion only — the operator types over it. Derived from the option the BACKEND labelled,
    // so this holds no plan vocabulary of its own.
    const quoted = option.label.match(/“([^”]+)”/)
    return quoted ? `${quoted[1]} (new)` : ''
  }

  function go() {
    if (!picked) return
    setBusy(true); setErr(''); setDone('')
    const p = new URLSearchParams({ option: picked.key, name })
    if (lens) p.set('carrier_id', lens)
    api(`/api/v1/commcalc/incentive-plan/draft?${p.toString()}`)
      .then((d: Draft) => api(d.endpoint, { method: 'POST', body: JSON.stringify(d.body) })
        .then(() => {
          setDone(picked.creates === 'schedules'
            ? 'Imported. The payout schedules are now this company’s own editable copy.'
            : `Created “${name}”. It is switched off and assigned to nobody, so pay has not changed yet.`)
          setPicked(null); setAt(1); reloadWorkflow()
        }))
      .catch((e: unknown) => setErr(errText(e)))
      .finally(() => setBusy(false))
  }

  const chip = (bg: string, fg: string) => ({
    padding: '2px 9px', borderRadius: 999, fontSize: 11, fontWeight: 700,
    background: bg, color: fg, display: 'inline-block',
  })

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧭 Your Incentive Plan</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
          Three steps: see the plan you pay today, pick what a new plan should start from, create it.
          Nothing here changes what anyone is paid until you assign the new plan and run the calculation.
        </p>
      </div>

      <Rail at={at} />

      {(wf?.carriers?.length || 0) > 1 && (
        <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12.5, color: 'var(--text2)', fontWeight: 600 }}>Carrier</span>
          <select className="select" value={lens} onChange={e => { setLens(e.target.value); setAt(1); setPicked(null) }}>
            <option value="">Every carrier</option>
            {(wf?.carriers || []).map(c => (
              <option key={c.id} value={c.id}>{c.name || c.code || c.id}{c.is_default ? ' (default)' : ''}</option>
            ))}
          </select>
        </div>
      )}

      {loading && <div className="card" style={{ padding: 16 }}>Loading…</div>}
      {err && <div className="card" style={{ padding: 16, color: 'var(--c-fg-991b1b)', background: 'var(--c-bg-fef2f2)' }}>{err}</div>}
      {done && <div className="card" style={{ padding: 16, marginBottom: 14, color: 'var(--c-fg-166534)', background: 'var(--c-bg-f0fdf4)' }}>✓ {done}</div>}

      {wf && wf.ready === false && (
        <div className="card" style={{ padding: 16 }}>{wf.note || 'Incentive plans are not enabled yet.'}</div>
      )}

      {/* ── STEP 1 — the plan you pay today ──────────────────────────────────────────── */}
      {active && (
        <div className="card" style={{ padding: 18, marginBottom: 14 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.05em', color: 'var(--text3)' }}>STEP 1 · ACTIVE</span>
            {active.built_in && <span style={chip('var(--surface2)', 'var(--text2)')}>BUILT-IN ENGINE</span>}
            {active.pays_nobody && <span style={chip('var(--c-bg-fef2f2)', 'var(--c-fg-991b1b)')}>PAYS NOBODY</span>}
            {active.plan && <span style={chip('var(--c-bg-f0fdf4)', 'var(--c-fg-166534)')}>ACTIVE</span>}
          </div>

          <div style={{ fontSize: 16, fontWeight: 650, lineHeight: 1.5 }}>{active.headline}</div>

          {active.plan && (
            <div style={{ marginTop: 12, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 10 }}>
              {[['Rules', active.plan.rule_count], ['Tiers', active.plan.tier_count],
                ['Assignments', active.plan.assignment_count]].map(([k, v]) => (
                <div key={String(k)} className="card" style={{ padding: '8px 10px' }}>
                  <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{k}</div>
                  <div style={{ fontWeight: 700, marginTop: 2 }}>{v}</div>
                </div>
              ))}
            </div>
          )}

          {active.plan && active.plan.covers.length > 0 && (
            <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--text2)' }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>Who it pays</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {active.plan.covers.map((c, i) => (
                  <span key={`${c.scope}-${i}`} style={chip('var(--surface2)', 'var(--text2)')}>
                    {c.scope}{c.value ? `: ${c.value}` : ''}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 16, flexWrap: 'wrap' }}>
            {active.can_change && at === 1 && (
              <button className="btn btn-primary btn-sm" onClick={() => { setAt(2); setPicked(null); setDone('') }}>
                {change?.action_label || 'Change the plan'} →
              </button>
            )}
            {active.rates_href && (
              <Link href={active.rates_href} className="btn btn-sm">See its rates</Link>
            )}
            {wf?.advanced_href && (
              <Link href={wf.advanced_href} className="btn btn-sm">All plan settings (advanced)</Link>
            )}
          </div>
        </div>
      )}

      {/* ── STEP 2 — what can replace it ─────────────────────────────────────────────── */}
      {change && at >= 2 && (
        <div className="card" style={{ padding: 18, marginBottom: 14 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.05em', color: 'var(--text3)', marginBottom: 6 }}>
            STEP 2 · PICK A STARTING POINT
          </div>
          <div style={{ fontSize: 15, fontWeight: 650, marginBottom: 12 }}>{change.headline}</div>

          <div style={{ display: 'grid', gap: 10 }}>
            {change.options.map(o => {
              const on = picked?.key === o.key
              return (
                <button key={o.key} onClick={() => pick(o)} className="card"
                  style={{
                    textAlign: 'left', padding: 14, cursor: 'pointer',
                    borderColor: on ? 'var(--c-fg-166534)' : 'var(--border)',
                    background: on ? 'var(--c-bg-f0fdf4)' : 'var(--surface)',
                  }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 700, fontSize: 14 }}>{o.label}</span>
                    <span style={chip('var(--surface2)', 'var(--text2)')}>
                      {o.creates === 'schedules' ? 'PAYOUT SCHEDULES' : 'NEW PLAN'}
                    </span>
                  </div>
                  <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 6, lineHeight: 1.6 }}>{o.detail}</div>
                  {o.caveat && (
                    <div style={{ fontSize: 12, color: 'var(--c-fg-991b1b)', marginTop: 6 }}>⚠ {o.caveat}</div>
                  )}
                </button>
              )
            })}
          </div>

          <button className="btn btn-sm" style={{ marginTop: 12 }} onClick={() => { setAt(1); setPicked(null) }}>
            ← Back
          </button>
        </div>
      )}

      {/* ── STEP 3 — create it ───────────────────────────────────────────────────────── */}
      {create && at === 3 && picked && (
        <div className="card" style={{ padding: 18, marginBottom: 14 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.05em', color: 'var(--text3)', marginBottom: 6 }}>
            STEP 3 · CREATE
          </div>
          <div style={{ fontSize: 15, fontWeight: 650 }}>
            {picked.creates === 'schedules' ? picked.label : create.headline}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 6, lineHeight: 1.6 }}>{picked.detail}</div>

          {picked.creates === 'plan' && (
            <div style={{ marginTop: 14, maxWidth: 420 }}>
              <label htmlFor="plan-name" style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>
                Name the new plan
              </label>
              <input id="plan-name" className="input" style={{ marginTop: 4 }} value={name}
                placeholder="e.g. 2027 market plan" onChange={e => setName(e.target.value)} />
            </div>
          )}

          <ol style={{ margin: '14px 0 0 18px', fontSize: 12.5, color: 'var(--text2)', lineHeight: 1.8 }}>
            {create.then.map((t, i) => <li key={i}>{t}</li>)}
          </ol>

          <div style={{ display: 'flex', gap: 8, marginTop: 16, flexWrap: 'wrap' }}>
            <button className="btn btn-primary btn-sm" disabled={busy || (picked.creates === 'plan' && !name.trim())}
              onClick={go}>
              {busy ? 'Working…' : picked.creates === 'schedules' ? 'Import it' : create.action_label}
            </button>
            <button className="btn btn-sm" onClick={() => { setAt(2); setPicked(null) }}>← Back</button>
          </div>
        </div>
      )}
    </div>
  )
}
