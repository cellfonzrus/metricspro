'use client'
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api, fmt } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { SortableTh, useTableSort } from '@/components/SortableTh'

// WHAT A DAY PAYS, AND WHY (§COMMCALC-78). Owner 2026-11-17: *"Build that screen"*.
//
// WHY IT EXISTS. `GET /commcalc/daily-incentive/{date}` has computed a store-day's payouts since
// mig 1072, and until now NOTHING in the browser called it: the rules editor writes the rules and
// the engine answers them, and the only way to ask "what does the store owe for the 20th" was the
// API. A mechanism an operator cannot reach is the §19.18 defect, and this is it in the place it
// costs most — the figure a rep is paid.
//
// THIS SCREEN COMPUTES NOTHING. Every number on it, including every verdict and every reason, comes
// off that one endpoint, which is itself a projection of `daily_incentive.compute_day`. A total
// added up here would be a second answer to "what is owed" and the two would drift; the house rule
// is that the fold has one home, so the page renders what it is handed. The only arithmetic below
// is `.length` on a list.
//
// WHY IT SHOWS THE WHY-TRAIL AND NOT JUST THE MONEY. A rep who sees $0.00 and no reason assumes the
// software is broken, and an owner cannot tell "the store missed the gate" from "the rule names a
// category nobody declares" from "nobody has uploaded the day". The engine already distinguishes
// all three. Showing only the money would throw that away, which is the absence-with-no-reason
// defect this platform keeps re-learning (§COMMCALC-44).

const sel: React.CSSProperties = {
  padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13,
  background: 'var(--surface)', color: 'var(--text)',
}
const cell: React.CSSProperties = {
  padding: '8px', borderTop: '1px solid var(--border)', fontSize: 13, verticalAlign: 'top',
}
const th: React.CSSProperties = {
  textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)',
}

type Verdict = {
  scope?: string; passed?: boolean; op?: string; threshold?: number; value?: number
  reason?: string; missing?: string[]
}
type RuleNote = {
  rule?: string; paid?: number; pool?: number; unallocated?: number; note?: string
  qualifier?: Verdict; per_employee_qualifier?: Record<string, Verdict>
  eligible?: string[]; skipped?: boolean; reason?: string; missing?: string[]
  payout_measurable?: boolean; payout_note?: string
}
type PayLine = {
  employee?: string; rule?: string; rule_kind?: string; amount?: number; basis?: string
}
type NotCounted = { metric_key?: string; metric_basis?: string; line_label?: string; units?: number; amount?: number }
type Day = {
  close_date?: string; store_code?: string; total?: number; unallocated?: number
  store_revenue?: number; employee_revenue?: Record<string, number>; top_earner?: string | null
  by_employee?: Record<string, number>; lines?: PayLine[]; rules?: RuleNote[]
  hours?: Record<string, number>
  metric_source?: string; metric_source_declared?: string; metric_source_note?: string
  metric_source_overridden?: boolean; metric_source_not_counted?: NotCounted[]
  metric_source_options?: { value: string; label: string }[]
  metric_source_declaration_pending?: boolean
}
type Store = { store_code?: string; store_address?: string }

function why(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** Yesterday, in the browser's own day — a payout screen opened in the morning is almost always
 *  asked about the day that just finished, and today's figures are by definition incomplete. */
function defaultDate(): string {
  const d = new Date()
  d.setDate(d.getDate() - 1)
  return d.toISOString().slice(0, 10)
}

/** One qualifier verdict in the operator's words. The ENGINE decides; this only reads its answer —
 *  `passed` is never inferred from the numbers, because a gate that could not be evaluated at all
 *  fails closed and must not read as "missed it by a little". */
function verdictWords(v?: Verdict): string {
  if (!v) return ''
  if (v.reason && !v.passed) {
    return v.missing?.length ? `${v.reason}: ${v.missing.join(', ')}` : v.reason
  }
  if (v.scope === 'none') return 'no gate — this rule always applies'
  const cmp = v.op === 'gt' ? 'over' : v.op === 'lte' ? 'at or under'
    : v.op === 'lt' ? 'under' : v.op === 'eq' ? 'exactly' : 'at least'
  const who = v.scope === 'employee' ? 'the rep' : 'the store'
  return `${who} reached ${fmt(v.value || 0)} of ${cmp} ${fmt(v.threshold || 0)}`
    + (v.passed ? '' : ' — missed')
}

export default function DailyIncentivePage() {
  const [date, setDate] = useState(defaultDate())
  const [storeCode, setStoreCode] = useState('')
  const [source, setSource] = useState('')       // '' = whatever this business has declared
  const [stores, setStores] = useState<Store[]>([])
  const [d, setD] = useState<Day | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP)
      .then((s: unknown) => setStores(Array.isArray(s) ? s as Store[] : ((s as { stores?: Store[] })?.stores || [])))
      .catch(() => {})
  }, [])

  /** The one read. `nonce` is what a Refresh press bumps, so asking for the same day again is a
   *  real request and not a no-op — the figures behind it move as a day is uploaded or declared. */
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    let alive = true
    const q = new URLSearchParams()
    if (storeCode) q.set('store_code', storeCode)
    if (source) q.set('metric_source', source)
    const qs = q.toString()
    // EVERY STATE WRITE HAPPENS IN THE FETCH'S OWN CALLBACK, never in the effect body: a setState
    // run synchronously inside an effect cascades a render for nothing. `alive` drops a reply that
    // arrives after the question changed, so a slow answer for the 3rd cannot overwrite the 4th.
    api(`/api/v1/commcalc/daily-incentive/${encodeURIComponent(date)}${qs ? '?' + qs : ''}`)
      .then(r => { if (!alive) return; setErr(null); setD(r as Day); setBusy(false) })
      .catch((e: unknown) => { if (!alive) return; setErr(why(e)); setD(null); setBusy(false) })
    return () => { alive = false }
  }, [date, storeCode, source, nonce])

  const load = useCallback(() => { setBusy(true); setNonce(n => n + 1) }, [])

  const people = Object.keys(d?.by_employee || {}).sort()
  // The sort reads the row's OWN field — no second shape of a pay line is defined here.
  const paySort = useTableSort<PayLine>(d?.lines || [],
    (row, field) => (row as unknown as Record<string, unknown>)[field])
  const notCounted = d?.metric_source_not_counted || []

  return (
    <div style={{ padding: 20, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, margin: '0 0 2px' }}>What this day pays</h1>
      <div style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 14 }}>
        One store-day, run through <Link href="/commcalc/incentive-rules">your daily incentive rules</Link>.
        Nothing here is saved and nobody is paid by opening it — it answers what the rules say is owed,
        and shows the working for every rule, including the ones that paid nothing.
      </div>

      <div className="card" style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 11, color: 'var(--text3)' }}>Day
          <input type="date" style={{ ...sel, display: 'block', marginTop: 2 }}
                 value={date} onChange={e => setDate(e.target.value)} />
        </label>
        <label style={{ fontSize: 11, color: 'var(--text3)' }}>Store
          <select style={{ ...sel, display: 'block', marginTop: 2, minWidth: 190 }}
                  value={storeCode} onChange={e => setStoreCode(e.target.value)}>
            <option value="">Every store</option>
            {stores.map((s, i) => (
              <option key={i} value={s.store_code || ''}>{s.store_address || s.store_code}</option>
            ))}
          </select>
        </label>
        {/* WHICH SOURCE ANSWERS THE DAY (§COMMCALC-77). The options come from the endpoint, never
            from a list typed here. Choosing one here answers THIS screen only and saves nothing:
            it is how an operator sees what the store's own sales report would pay before declaring
            it as the standing authority — which is a separate, deliberate act. */}
        <label style={{ fontSize: 11, color: 'var(--text3)' }}>Figures from
          <select style={{ ...sel, display: 'block', marginTop: 2, minWidth: 200 }}
                  value={source} onChange={e => setSource(e.target.value)}>
            <option value="">What this business has declared</option>
            {(d?.metric_source_options || []).map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={busy} onClick={load}>
          {busy ? 'Working…' : 'Refresh'}
        </button>
      </div>

      {err && (
        <div style={{ background: 'var(--c-bg-fef2f2)', color: 'var(--c-fg-991b1b)', borderRadius: 8,
          padding: '8px 10px', fontSize: 12, marginTop: 12 }}>{err}</div>
      )}

      {/* THE SOURCE IS NAMED ON EVERY ANSWER. Two sources can disagree about a day, so a figure
          shown without saying which one answered is one of two answers presented as the only one. */}
      {d?.metric_source_note && (
        <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: '8px 10px',
          fontSize: 12, marginTop: 12 }}>
          {d.metric_source_note}
          {d.metric_source_overridden && (
            <strong> You are looking at this on purpose — it is not what this business has declared,
              and nothing has been changed.</strong>
          )}
          {d.metric_source_declaration_pending && (
            <div style={{ marginTop: 4, color: 'var(--text3)' }}>
              This business has not been able to choose a standing source yet — the switch is not in
              the database. Until it is, the reps&apos; own declarations answer, which is what every
              company reads today.
            </div>
          )}
        </div>
      )}

      {d && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 12 }}>
          {[['Owed for the day', fmt(d.total || 0)],
            ["The day's sales, as measured", fmt(d.store_revenue || 0)],
            ['People paid', String(people.length)],
            ['Top seller', d.top_earner || '—']].map(([k, v]) => (
            <div key={k} className="card" style={{ flex: '1 1 180px', padding: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>{k}</div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{v}</div>
            </div>
          ))}
        </div>
      )}

      {/* A POOL NOBODY COULD SHARE is money the rules produced and the engine refused to invent an
          owner for. It is shown, never folded into the total, because an even split of a by-hours
          pool when nobody clocked in would be a payment nobody earned. */}
      {!!d?.unallocated && (
        <div style={{ background: 'var(--c-bg-fffbeb)', color: 'var(--c-fg-92400e)', borderRadius: 8,
          padding: '8px 10px', fontSize: 12, marginTop: 12 }}>
          {fmt(d.unallocated)} of pool money could not be shared out, so it is not in the total above.
          The rule&apos;s own reason is in the table below.
        </div>
      )}

      {!!people.length && (
        <div className="card table-wrapper" style={{ marginTop: 16, padding: 0 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {[['employee', 'Who'], ['rule', 'Rule'], ['basis', 'How it was worked out'],
                ['amount', 'Amount']].map(([f, h]) => (
                <SortableTh key={f} field={f} sort={paySort.sort} onSort={paySort.toggle} style={th}>
                  {h}
                </SortableTh>))}
            </tr></thead>
            <tbody>
              {paySort.sorted.map((l, i) => (
                <tr key={i}>
                  <td style={cell}>
                    <div style={{ fontWeight: 600 }}>{l.employee}</div>
                    {d?.hours?.[l.employee || ''] !== undefined && (
                      <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                        {d.hours[l.employee || '']} h on the schedule
                      </div>
                    )}
                  </td>
                  <td style={cell}>{l.rule}</td>
                  <td style={{ ...cell, fontSize: 11, color: 'var(--text3)' }}>{l.basis}</td>
                  <td style={{ ...cell, fontWeight: 700 }}>{fmt(l.amount || 0)}</td>
                </tr>
              ))}
              {people.map(p => (
                <tr key={'t' + p} style={{ background: 'var(--surface2)' }}>
                  <td style={{ ...cell, fontWeight: 700 }} colSpan={3}>{p} — owed for the day</td>
                  <td style={{ ...cell, fontWeight: 700 }}>{fmt((d?.by_employee || {})[p] || 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* EVERY RULE, INCLUDING THE ONES THAT PAID NOTHING. This is the part a rep needs: a rule
          that paid nothing because the store missed a gate is a coaching conversation, and a rule
          that paid nothing because it cannot be measured is a configuration job. The engine tells
          them apart; showing only the payers would hide both. */}
      <h2 style={{ fontSize: 15, fontWeight: 700, margin: '20px 0 6px' }}>Every rule, and what it did</h2>
      <div className="card table-wrapper" style={{ padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            <th style={th}>Rule</th><th style={th}>Did it apply?</th>
            <th style={th}>Paid</th><th style={th}>What to do about it</th>
          </tr></thead>
          <tbody>
            {(d?.rules || []).map((r, i) => (
              <tr key={i}>
                <td style={{ ...cell, fontWeight: 600 }}>{r.rule}</td>
                <td style={{ ...cell, fontSize: 12 }}>{verdictWords(r.qualifier)}</td>
                <td style={{ ...cell, fontWeight: 700 }}>
                  {fmt(r.paid || 0)}
                  {!!r.pool && (
                    <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>
                      pool {fmt(r.pool)}
                    </div>
                  )}
                </td>
                <td style={{ ...cell, fontSize: 12 }}>
                  {/* A RULE THAT CAN NEVER PAY says so here rather than showing a zero that looks
                      like a quiet day. This is the §COMMCALC-77 `payout_measurable` verdict, read,
                      not re-derived. */}
                  {r.payout_measurable === false && r.payout_note && (
                    <div style={{ color: 'var(--c-fg-92400e)' }}>
                      <strong>This rule can never pay.</strong> {r.payout_note}{' '}
                      <Link href="/commcalc/incentive-rules">Fix the rule</Link>.
                    </div>
                  )}
                  {r.skipped && <div>{r.reason}</div>}
                  {!!r.missing?.length && <div>Names a category nobody declares: {r.missing.join(', ')}</div>}
                  {!!r.unallocated && <div>{fmt(r.unallocated)} could not be shared: {r.note}</div>}
                  {!r.unallocated && r.note && <div style={{ color: 'var(--text3)' }}>{r.note}</div>}
                  {!!r.eligible?.length && (
                    <div style={{ color: 'var(--text3)' }}>Shared between {r.eligible.join(', ')}</div>
                  )}
                </td>
              </tr>
            ))}
            {!(d?.rules || []).length && (
              <tr><td style={{ ...cell, color: 'var(--text3)' }} colSpan={4}>
                This business has no active incentive rules, so there is nothing to work out.{' '}
                <Link href="/commcalc/incentive-rules">Add a rule</Link>.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* WHAT THE SOURCE CARRIED AND COULD NOT PAY ON. A sales line that matched no declared
          category is evidence the store did something; it is reported rather than dropped, because
          a silent omission reads as "the store did not sell it". */}
      {!!notCounted.length && (
        <>
          <h2 style={{ fontSize: 15, fontWeight: 700, margin: '20px 0 6px' }}>
            Seen in the figures, but not counted
          </h2>
          <div style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 6 }}>
            These lines reached us but are not evidence of a declared category, so they pay nothing.
            Map them on the report, or declare the category, and they will count.
          </div>
          <div className="card table-wrapper" style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                <th style={th}>Line</th><th style={th}>Why it is not counted</th>
                <th style={th}>Units</th><th style={th}>Amount</th>
              </tr></thead>
              <tbody>
                {notCounted.map((u, i) => (
                  <tr key={i}>
                    <td style={cell}>{u.line_label || u.metric_key}</td>
                    <td style={{ ...cell, fontSize: 12, color: 'var(--text3)' }}>{u.metric_basis}</td>
                    <td style={cell}>{u.units ?? ''}</td>
                    <td style={cell}>{fmt(u.amount || 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
