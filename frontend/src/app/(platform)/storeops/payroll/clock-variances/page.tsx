'use client'
// Clock variances — the correction board (owner directive 2026-10-10, index §14z).
//
// Owner: "import the clock in clock out report from b2b and match with what we have in metrics pro
// and asses the difference and ask the DM on every monday and require the correction to be done
// before the payroll is run."
//
// Owner ruling, same day: "if they are clocking out at earlier time from the elevate then it means
// they are spending time in store to make extra hours and they ahve no more work to do, use that as
// the time used to create payroll and mention on the report why you are doing so." So the Elevate
// report is a PAY SOURCE, and the reason it was used travels on every row — the `Our basis` column
// and its sentence, both from the server.
//
// THIS PAGE IS THE GATE'S ESCAPE. From the gate weekday a manager with uncorrected rows signs in
// normally and is confined HERE (★ NO LOCKOUT ★, index §22) — never refused a sign-in, because a
// person who cannot sign in cannot fix what blocks them. So it must work standalone, with no other
// screen reachable.
//
// RULE FIVE: the standard filter bar drives the table AND the export. RULE FOUR: ReportExportBar
// gives Excel / PDF / Print / email / WhatsApp on the visible rows — there is no exporter here.
// The server is the authority on every gate: it strips money for a caller who may not see pay, and
// it refuses an override with no reason. This page mirrors those rules; it does not enforce them.
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import { ReportExportBar, type ExportColumn } from '@/components/ReportExportBar'
import type { StandardFilterValue } from '@/lib/standard-filters'

type Row = {
  store_code?: string; employee_id?: string; employee_name?: string; employee_raw?: string
  date?: string; kind?: string; reason?: string
  feed_hours?: number | null; our_hours?: number | null; delta_hours?: number | null
  our_basis?: string; our_basis_reason?: string
  blocks?: boolean; resolved?: boolean
  auto_closed_punches?: number; feed_stores?: string[]; our_stores?: string[]
  detail?: Record<string, any>
}
type Board = {
  enabled: boolean; not_enabled_reason?: string | null
  is_pay_source?: boolean; clock_source_rule?: string; clock_source_rule_reason?: string
  agreement_tolerance_hours?: number; tolerance_hours?: number
  period?: { start: string; end: string }
  weekday_plan?: { ask: number; gate: number; run: number }
  rows: Row[]
  by_store?: Record<string, { variances?: number; blocking?: number; all_clear?: boolean }>
  all_clear?: Record<string, boolean>
  totals?: Record<string, number>
  blocking_count?: number
  kinds?: Record<string, string>
  blockers?: { table?: string; reason?: string }[]
}

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const card: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 16, marginBottom: 12 }
const btn: React.CSSProperties = { padding: '6px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', fontSize: 12.5, cursor: 'pointer', fontWeight: 600 }
const btnP: React.CSSProperties = { ...btn, background: 'var(--accent)', color: '#fff', border: 'none' }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', fontSize: 11.5, fontWeight: 700, color: 'var(--text2)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)', verticalAlign: 'top' }
const h = (n?: number | null) => n == null ? '—' : Number(n).toFixed(2)

// What a reviewer has typed for one row but not yet submitted. The server REFUSES an override with
// no reason — "This figure becomes somebody's pay and the reason travels with it onto the report and
// into the payroll change log" — so the button stays disabled until there is one, rather than
// sending a request that is going to be rejected.
type Edit = { resolution: string; hours: string; reason: string }
const EMPTY: Edit = { resolution: 'corrected', hours: '', reason: '' }

export default function ClockVariancesPage() {
  const [f, setF] = useState<StandardFilterValue>({ period: '', periodTo: '', stores: [], markets: [], reps: [] })
  const [d, setD] = useState<Board | null>(null)
  const [edits, setEdits] = useState<Record<string, Edit>>({})
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [onlyBlocking, setOnlyBlocking] = useState(true)

  const keyOf = (r: Row) => `${r.store_code || ''}|${r.employee_id || r.employee_raw || ''}|${r.date || ''}`

  const load = useCallback(async () => {
    setErr(''); setMsg('')
    const qs = new URLSearchParams()
    if (f.period && f.periodTo) { qs.set('start', f.period); qs.set('end', f.periodTo) }
    if (f.stores.length) qs.set('stores', f.stores.join(','))
    if (f.markets.length) qs.set('markets', f.markets.join(','))
    if (f.reps.length) qs.set('reps', f.reps.join(','))
    try {
      setD(await api(`/api/v1/storeops/payroll/clock-variances?${qs.toString()}`))
      setEdits({})
    } catch (e: any) { setErr(e?.message || 'Could not load the clock-variance board') }
  }, [f.period, f.periodTo, f.stores, f.markets, f.reps])
  useEffect(() => { load() }, [load])

  function setEdit(k: string, patch: Partial<Edit>) {
    setEdits(s => ({ ...s, [k]: { ...(s[k] ?? EMPTY), ...patch } }))
  }

  async function resolve(r: Row) {
    const k = keyOf(r)
    const e = edits[k] ?? EMPTY
    setBusy(k); setErr(''); setMsg('')
    try {
      const res = await api('/api/v1/storeops/payroll/clock-variances/resolve', {
        method: 'POST',
        body: JSON.stringify({
          store_code: r.store_code, employee_id: r.employee_id, date: r.date,
          kind: r.kind, resolution: e.resolution, reason: e.reason,
          ...(e.resolution === 'corrected' && e.hours !== '' ? { override_hours: Number(e.hours) } : {}),
        }),
      })
      setMsg(res?.reason || 'Recorded.')
      await load()
    } catch (ex: any) { setErr(ex?.message || 'The server refused that correction') }
    finally { setBusy('') }
  }

  const rows = useMemo(() => (d?.rows || []).filter(r => !onlyBlocking || r.blocks), [d, onlyBlocking])

  // RULE FOUR: the export renders EXACTLY these rows and columns — including the basis and its
  // sentence, because the ruling asked for the reason to be ON THE REPORT, not only on the screen.
  const cols: ExportColumn[] = [
    { header: 'Store', field: 'store_code', role: 'store', get: (r: Row) => r.store_code || '' },
    { header: 'Employee', field: 'employee_name', role: 'rep', get: (r: Row) => r.employee_name || r.employee_raw || r.employee_id || '' },
    { header: 'Day', field: 'date', get: (r: Row) => r.date || '' },
    { header: 'What differs', field: 'kind', get: (r: Row) => (d?.kinds?.[r.kind || ''] || r.kind || '') },
    { header: 'Elevate hrs', field: 'feed_hours', type: 'number', get: (r: Row) => h(r.feed_hours) },
    { header: 'Our hrs', field: 'our_hours', type: 'number', get: (r: Row) => h(r.our_hours) },
    { header: 'Difference', field: 'delta_hours', type: 'number', get: (r: Row) => h(r.delta_hours) },
    { header: 'Our basis', field: 'our_basis', get: (r: Row) => r.our_basis || '' },
    { header: 'Why', field: 'our_basis_reason', get: (r: Row) => r.our_basis_reason || r.reason || '' },
    { header: 'Blocks payroll', field: 'blocks', get: (r: Row) => r.blocks ? 'Yes' : 'No' },
  ]

  if (err && !d) return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700 }}>⏱ Clock variances</h1>
      <div style={{ ...card, borderColor: '#f87171', color: '#b42318' }}>{err}</div>
    </div>
  )

  return (
    <div style={{ padding: 24, maxWidth: 1400 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>⏱ Clock variances</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 14 }}>
        Where the Elevate clock-in/clock-out report and our own time clock disagree, for the same pay
        period payroll pays. Every row shows BOTH numbers and where our figure came from — you are
        never asked to choose between two unexplained figures.
      </p>

      {/* SWITCHED OFF READS AS OFF, never as a clean board. An empty list that cannot say whether it
          looked is the defect this notice exists to prevent. */}
      {d && !d.enabled && (
        <div style={{ ...card, background: 'var(--surface2)' }}>
          <strong>Nothing is being imported yet.</strong>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>{d.not_enabled_reason}</div>
        </div>
      )}

      {/* A board that could not READ its own tables must say so. Silence here would look identical
          to a clean week. */}
      {!!d?.blockers?.length && (
        <div style={{ ...card, borderColor: '#f59e0b', background: 'rgba(245,158,11,0.08)' }}>
          <strong style={{ color: '#b45309' }}>Part of this board could not be read.</strong>
          <ul style={{ margin: '6px 0 0 18px', fontSize: 12.5, color: 'var(--text2)' }}>
            {d.blockers.map((b, i) => <li key={i}><code>{b.table}</code> — {b.reason}</li>)}
          </ul>
        </div>
      )}

      {d?.enabled && (
        <div style={{ ...card, display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 12.5 }}>
          <div>
            <div style={{ color: 'var(--text3)', fontSize: 11, fontWeight: 700 }}>STILL BLOCKING</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: (d.blocking_count || 0) > 0 ? '#b42318' : '#15803d' }}>
              {d.blocking_count || 0}
            </div>
          </div>
          <div style={{ maxWidth: 520 }}>
            <div style={{ color: 'var(--text3)', fontSize: 11, fontWeight: 700 }}>
              WHICH CLOCK PAYS {d.is_pay_source ? '· the Elevate report IS a pay source' : '· the Elevate report is a check only'}
            </div>
            <div style={{ color: 'var(--text2)' }}>{d.clock_source_rule_reason || d.clock_source_rule}</div>
            {d.agreement_tolerance_hours != null && (
              <div style={{ color: 'var(--text3)', marginTop: 2 }}>
                Differences under {(d.agreement_tolerance_hours * 60).toFixed(0)} minute(s) are treated as
                agreement and our figure stands — rounding a minute off somebody&apos;s pay every day is not a
                reconciliation.
              </div>
            )}
          </div>
          {d.weekday_plan && (
            <div>
              <div style={{ color: 'var(--text3)', fontSize: 11, fontWeight: 700 }}>THE WEEK</div>
              <div style={{ color: 'var(--text2)' }}>
                Asked {WEEKDAYS[d.weekday_plan.ask] || d.weekday_plan.ask} · due{' '}
                {WEEKDAYS[d.weekday_plan.gate] || d.weekday_plan.gate} · payroll{' '}
                {WEEKDAYS[d.weekday_plan.run] || d.weekday_plan.run}
              </div>
            </div>
          )}
          <div style={{ flex: 1 }} />
          <Link href="/storeops/payroll" style={{ ...btn, textDecoration: 'none', alignSelf: 'center' }}>Payroll →</Link>
        </div>
      )}

      <div style={{ ...card, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <StandardFilterBar value={f} onChange={setF} periodMode="range"
          show={{ period: true, stores: true, markets: true, reps: true }}
          optionsUrl="/api/v1/core/filter-options" />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--text2)' }}>
          <input type="checkbox" checked={onlyBlocking} onChange={e => setOnlyBlocking(e.target.checked)} />
          Only what still blocks payroll
        </label>
        <div style={{ flex: 1 }} />
        <ReportExportBar title="Clock variances"
          subtitle={d?.period ? `${d.period.start} to ${d.period.end}` : undefined}
          columns={cols} rows={rows} />
      </div>

      {msg && <div style={{ ...card, borderColor: '#86efac', color: '#15803d' }}>{msg}</div>}
      {err && <div style={{ ...card, borderColor: '#f87171', color: '#b42318' }}>{err}</div>}

      {/* A STORE WITH NOTHING WRONG APPEARS SAYING SO. A board that lists only problems cannot tell
          "clean" from "not looked at". */}
      {d?.all_clear && Object.keys(d.all_clear).length > 0 && (
        <div style={{ ...card, fontSize: 12.5 }}>
          <div style={{ color: 'var(--text3)', fontSize: 11, fontWeight: 700, marginBottom: 6 }}>BY STORE</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {Object.entries(d.all_clear).map(([code, clear]) => (
              <span key={code} style={{ borderRadius: 999, padding: '3px 10px', fontWeight: 600,
                background: clear ? '#dcfce7' : '#fee2e2', color: clear ? '#15803d' : '#b42318' }}>
                {code} {clear ? '✓ all clear' : `· ${d.by_store?.[code]?.blocking ?? 0} blocking`}
              </span>
            ))}
          </div>
        </div>
      )}

      <div style={{ ...card, padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1100 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Store', 'Employee', 'Day', 'What differs', 'Elevate hrs', 'Our hrs', 'Diff',
              'Our basis — and why', 'Record a correction'].map(x => <th key={x} style={th}>{x}</th>)}
          </tr></thead>
          <tbody>
            {rows.map(r => {
              const k = keyOf(r)
              const e = edits[k] ?? EMPTY
              const needsReason = !String(e.reason || '').trim()
              return (
                <tr key={k}>
                  <td style={td}>{r.store_code || '—'}</td>
                  <td style={td}>
                    {r.employee_name || r.employee_raw || '—'}
                    {/* The login we could not bind to a person is a FIRST-CLASS row, never a guess. */}
                    {!r.employee_id && <div style={{ fontSize: 11, color: '#b45309' }}>not linked to anybody yet</div>}
                  </td>
                  <td style={td}>{r.date || '—'}</td>
                  <td style={td}>
                    <div style={{ fontWeight: 600 }}>{(r.kind || '').replace(/_/g, ' ')}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', maxWidth: 260 }}>
                      {d?.kinds?.[r.kind || ''] || r.reason}
                    </div>
                    {!!r.auto_closed_punches && (
                      <div style={{ fontSize: 11, color: '#b45309' }}>
                        {r.auto_closed_punches} punch(es) the kiosk closed by itself — not evidence of a clock-out
                      </div>
                    )}
                  </td>
                  <td style={{ ...td, fontVariantNumeric: 'tabular-nums' }}>{h(r.feed_hours)}</td>
                  <td style={{ ...td, fontVariantNumeric: 'tabular-nums' }}>{h(r.our_hours)}</td>
                  <td style={{ ...td, fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>{h(r.delta_hours)}</td>
                  <td style={td}>
                    <div style={{ fontWeight: 600 }}>{(r.our_basis || '—').replace(/_/g, ' ')}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', maxWidth: 320 }}>{r.our_basis_reason}</div>
                  </td>
                  <td style={td}>
                    {r.resolved ? <span style={{ color: '#15803d', fontWeight: 600 }}>✓ settled</span> : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 260 }}>
                        <select value={e.resolution} onChange={ev => setEdit(k, { resolution: ev.target.value })}
                          style={{ padding: '4px 6px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)' }}>
                          <option value="corrected">Correct it — I am setting the hours</option>
                          <option value="accepted_ours">Accept OUR clock</option>
                          <option value="accepted_feed">Accept the ELEVATE report</option>
                        </select>
                        {e.resolution === 'corrected' && (
                          <input type="number" step="0.01" placeholder="hours" value={e.hours}
                            onChange={ev => setEdit(k, { hours: ev.target.value })}
                            style={{ padding: '4px 6px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)' }} />
                        )}
                        <input placeholder="Reason (required)" value={e.reason}
                          onChange={ev => setEdit(k, { reason: ev.target.value })}
                          style={{ padding: '4px 6px', borderRadius: 6, border: `1px solid ${needsReason ? '#f59e0b' : 'var(--border)'}`, fontSize: 12, background: 'var(--surface)' }} />
                        <button style={{ ...btnP, opacity: needsReason || busy === k ? 0.5 : 1 }}
                          disabled={needsReason || busy === k} onClick={() => resolve(r)}
                          title={needsReason
                            ? 'A reason is required. This figure becomes somebody\'s pay, and the reason travels with it onto the report and into the payroll change log.'
                            : undefined}>
                          {busy === k ? '…' : 'Record'}
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              )
            })}
            {rows.length === 0 && (
              <tr><td style={{ ...td, color: 'var(--text3)' }} colSpan={9}>
                {d?.enabled
                  ? (onlyBlocking
                      ? 'Nothing is blocking payroll for this period. Untick the filter above to see rows already settled or inside the tolerance.'
                      : 'No differences at all for this period.')
                  : 'The clock report import is switched off for this organisation, so nothing has been compared.'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
