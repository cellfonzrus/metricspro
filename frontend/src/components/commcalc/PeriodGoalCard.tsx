'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmt } from '@/lib/client'
import { SetupNotice } from '@/lib/setupNotice'

// WHERE MUST THIS YEAR LAND (owner ask 2026-11-17, index §COMMCALC-79).
//
// Owner, verbatim: *"The goal is to make the store a platinum store which is 1 Million in annual
// sale including the commission earned - make this user filled entry but fill it for me where the
// targets have to be declared"*. So this is a USER-FILLED declaration and it lives on the Daily
// Targets screen — "where the targets have to be declared" in his own words — rather than on a
// sibling page nobody would find.
//
// THIS CARD COMPUTES NOTHING. The need, the per-month rate, the run-rate, the pace it is measured
// against and the verdict are all fields of `GET /commcalc/period-goal/{period}`, which asks
// `annual_goal.progress`, which dereferences `targets_engine.rate_to_close` — the same home the
// per-day backward calculation asks. A browser-side total would be a SECOND answer to "what do I
// still need", and the two would drift silently because both look right.
//
// THE CATEGORY LIST IS NOT HERE. `axis` is the tenant's own declared money categories, served by
// the endpoint. A list spelled in this file would be one tenant's vocabulary behind a house default
// — the thing the 2026-10-30 directive bans outright — and a store selling printing and mailboxes
// would be offered four wireless words.

type Measure = { key: string; label: string; declared: boolean; in_revenue: boolean }
type AxisItem = { key: string; label: string }
type Goal = {
  goal: number; grain: string; period: string; label: string
  measured: boolean; achieved: number; pct: number | null
  need: number | null; per_month: number | null
  run_rate: number | null; pace_needed: number | null; on_pace: boolean | null
  months_elapsed: number; months_remaining: number; months_total: number; placed: boolean
  why: string; missing_keys: string[]; by_key: Record<string, number>
  measure: Measure[]; axis: AxisItem[]; axis_note: string; grains: string[]
  horizon: { from: string | null; to: string | null }
  declared: boolean; may_edit: boolean; notes: string
  metric_source: string; metric_source_note: string
  migration_pending: boolean
}

const box: React.CSSProperties = {
  padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)',
  fontSize: 13, background: 'var(--surface)', color: 'var(--text)',
}
const tileLabel: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 0.4 }
const tileVal: React.CSSProperties = { fontSize: 19, fontWeight: 700, marginTop: 2 }

function why(e: unknown) {
  const m = (e as { message?: string } | null)?.message
  return m || 'The goal could not be loaded.'
}

/** One tile. The value is a STRING the caller already formatted — this component does no maths. */
function Tile({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ minWidth: 118 }}>
      <div style={tileLabel}>{label}</div>
      <div style={{ ...tileVal, color: tone || 'var(--text)' }}>{value}</div>
    </div>
  )
}

export default function PeriodGoalCard({ storeCode = '', year = '' }: { storeCode?: string; year?: string }) {
  // The horizon this card is showing. A YEAR, because that is what the owner declared; the endpoint
  // also serves a quarter and `grains` carries the vocabulary, so the selector below is the
  // server's list rather than two words typed here.
  const thisYear = useMemo(() => year || String(new Date().getFullYear()), [year])
  const [period, setPeriod] = useState(thisYear)
  const [grain, setGrain] = useState('year')
  const [d, setD] = useState<Goal | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [nonce, setNonce] = useState(0)
  const [open, setOpen] = useState(false)
  const [msg, setMsg] = useState('')

  // The edit form. Seeded from the served row when the editor opens, never from a guess.
  const [fGoal, setFGoal] = useState('')
  const [fLabel, setFLabel] = useState('')
  const [fKeys, setFKeys] = useState<string[]>([])

  useEffect(() => {
    let alive = true
    const q = new URLSearchParams({ grain })
    if (storeCode) q.set('store_code', storeCode)
    api(`/api/v1/commcalc/period-goal/${encodeURIComponent(period)}?${q.toString()}`)
      .then(r => { if (!alive) return; setErr(null); setD(r as Goal); setBusy(false) })
      .catch((e: unknown) => { if (!alive) return; setErr(why(e)); setD(null); setBusy(false) })
    return () => { alive = false }
  }, [period, grain, storeCode, nonce])

  const reload = useCallback(() => { setBusy(true); setNonce(n => n + 1) }, [])

  const edit = useCallback(() => {
    if (!d) return
    setFGoal(d.goal > 0 ? String(d.goal) : '')
    setFLabel(d.label || '')
    setFKeys(d.measure.map(m => m.key))
    setOpen(true)
    setMsg('')
  }, [d])

  const save = useCallback(async () => {
    setMsg('Saving…')
    try {
      await api(`/api/v1/commcalc/period-goal/${encodeURIComponent(period)}`, {
        method: 'PUT',
        body: JSON.stringify({
          store_code: storeCode, grain, goal_value: Number(fGoal) || 0,
          label: fLabel, metric_keys: fKeys,
        }),
      })
      setMsg('Saved.')
      setOpen(false)
      reload()
    } catch (e: unknown) { setMsg(why(e)) }
  }, [period, storeCode, grain, fGoal, fLabel, fKeys, reload])

  const toggle = useCallback((k: string) => {
    setFKeys(prev => prev.includes(k) ? prev.filter(x => x !== k) : [...prev, k])
  }, [])

  if (busy && !d) {
    return <div className="card" style={{ padding: '12px 16px', marginBottom: 20, fontSize: 13, color: 'var(--text3)' }}>Loading the goal…</div>
  }
  if (err) {
    return (
      <div className="card" style={{ padding: '12px 16px', marginBottom: 20, fontSize: 13 }}>
        ⚠ {err}{' '}
        <button className="btn btn-secondary" style={{ fontSize: 12, marginLeft: 8 }} onClick={reload}>Retry</button>
      </div>
    )
  }
  if (!d) return null

  // THE VERDICT IS THE SERVER'S. `on_pace` is deliberately three-valued: null means the goal could
  // not be measured or the horizon could not be placed, and a store told it is BEHIND on a figure
  // nobody measured would act on nothing. So null is never rendered as "behind".
  const tone = d.on_pace == null ? 'var(--text3)' : (d.on_pace ? 'var(--green)' : 'var(--c-fg-b45309)')
  const verdict = d.on_pace == null ? 'Not measured' : (d.on_pace ? 'On pace' : 'Behind pace')

  return (
    <div className="card" style={{ padding: 0, marginBottom: 24 }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600 }}>
          {d.label ? `${d.label} goal` : 'Annual goal'}
          {storeCode ? ` — ${storeCode}` : ' — whole company'}
        </span>
        <select value={grain} onChange={e => setGrain(e.target.value)} style={box} aria-label="Horizon">
          {d.grains.map(g => <option key={g} value={g}>{g === 'year' ? 'Year' : 'Quarter'}</option>)}
        </select>
        <input value={period} onChange={e => setPeriod(e.target.value)} style={{ ...box, width: 96 }}
               aria-label="Period" placeholder={grain === 'year' ? '2027' : '2027-04'} />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>
          {d.horizon.from ? `${d.horizon.from} → ${d.horizon.to}` : 'that period could not be read'}
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {d.may_edit && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={edit}>
            {d.declared ? 'Edit goal' : 'Set a goal'}
          </button>}
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={reload}>Refresh</button>
        </span>
      </div>

      {/* A MIGRATION THAT HAS NOT LANDED SAYS SO. Code deploys on merge here while the migration
          waits for the owner, and "no goal set" must not be shown when the truth is "the table is
          not there yet" — the first is a prompt to the user, the second to the owner. */}
      {d.migration_pending && (
        <div style={{ padding: '9px 16px', fontSize: 12.5, color: 'var(--text2)', background: 'var(--surface2)', borderBottom: '1px solid var(--border)' }}>
          ⚙️ <SetupNotice
            lead={'Goals cannot be stored yet, so everything below reads as “no goal set”.'}
            detail={'1125_period_goal.sql — commcalc.period_goal'} />
        </div>
      )}

      {!d.declared && !d.migration_pending ? (
        <div style={{ padding: '18px 16px', fontSize: 13, color: 'var(--text2)' }}>
          No goal has been set for {d.period}.{' '}
          {d.may_edit
            ? 'Use “Set a goal” to declare where this year has to land, and tick the categories whose sales count toward it.'
            : 'Ask an administrator with the Target Settings permission to declare one.'}
        </div>
      ) : (
        <>
          <div style={{ padding: '14px 16px', display: 'flex', gap: 26, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <Tile label="Goal" value={fmt(d.goal)} />
            <Tile label="Earned so far" value={d.measured ? fmt(d.achieved) : '—'} />
            <Tile label="% there" value={d.pct == null ? '—' : `${d.pct}%`} />
            <Tile label="Still needed" value={d.need == null ? '—' : fmt(d.need)} />
            <Tile label={`Per month (${d.months_remaining} left)`} value={d.per_month == null ? 'no months left' : fmt(d.per_month)} />
            <Tile label="Running at" value={d.run_rate == null ? '—' : `${fmt(d.run_rate)}/mo`} />
            <Tile label="Needs" value={d.pace_needed == null ? '—' : `${fmt(d.pace_needed)}/mo`} />
            <Tile label="Verdict" value={verdict} tone={tone} />
          </div>

          {/* NOTHING DECLARED MEANS NOTHING MEASURED, AND THE SURFACE SAYS SO (owner directive
              2026-10-30). A zero here would look exactly like a quiet year. */}
          {d.why && (
            <div style={{ padding: '10px 16px', fontSize: 12.5, color: 'var(--text2)', background: 'var(--surface2)', borderTop: '1px solid var(--border)' }}>
              {d.measured ? 'ℹ️' : '⚠'} {d.why}
            </div>
          )}

          <div style={{ padding: '10px 16px', fontSize: 12.5, color: 'var(--text3)', borderTop: '1px solid var(--border)' }}>
            Measured over{' '}
            {d.measure.length === 0 ? <em>no category</em> : d.measure.map((m, i) => (
              <span key={m.key}>
                {i > 0 ? ', ' : ''}
                <strong style={{ color: m.declared ? 'var(--text2)' : 'var(--c-fg-b45309)' }}>{m.label}</strong>
                {d.by_key[m.key] == null ? '' : ` (${fmt(d.by_key[m.key])})`}
                {m.declared ? '' : ' — no longer declared'}
              </span>
            ))}
            {d.metric_source_note ? <> · {d.metric_source_note}</> : null}
          </div>
        </>
      )}

      {open && (
        <div style={{ padding: '14px 16px', borderTop: '1px solid var(--border)', background: 'var(--surface2)' }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12 }}>
            <label style={{ fontSize: 12, color: 'var(--text2)' }}>
              <div style={{ marginBottom: 3 }}>Goal amount</div>
              <input value={fGoal} onChange={e => setFGoal(e.target.value)} style={{ ...box, width: 150 }}
                     inputMode="decimal" placeholder="0.00" />
            </label>
            <label style={{ fontSize: 12, color: 'var(--text2)' }}>
              <div style={{ marginBottom: 3 }}>Name for it (optional)</div>
              <input value={fLabel} onChange={e => setFLabel(e.target.value)} style={{ ...box, width: 170 }}
                     placeholder="e.g. the tier you are aiming at" />
            </label>
          </div>

          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>
            Which sales count toward it
          </div>
          {d.axis.length === 0 ? (
            <div style={{ fontSize: 12.5, color: 'var(--text2)' }}>⚠ {d.axis_note}</div>
          ) : (
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
              {d.axis.map(a => (
                <label key={a.key} style={{ fontSize: 12.5, display: 'flex', gap: 5, alignItems: 'center', padding: '4px 8px', borderRadius: 7, border: '1px solid var(--border)', background: 'var(--surface)' }}>
                  <input type="checkbox" checked={fKeys.includes(a.key)} onChange={() => toggle(a.key)} />
                  {a.label}
                </label>
              ))}
            </div>
          )}
          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 10 }}>
            Tick nothing and the goal is saved as unmeasured — it will say so rather than read as
            zero. If a kind of income that should count has no box here, declare it as a category on
            Daily Closing → Count Fields and it will appear.
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="btn" style={{ fontSize: 12 }} onClick={save}>Save goal</button>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setOpen(false)}>Cancel</button>
            {msg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</span>}
          </div>
        </div>
      )}
    </div>
  )
}
