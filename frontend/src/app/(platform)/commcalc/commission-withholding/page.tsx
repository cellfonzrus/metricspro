'use client'
// COMMISSION WITHHOLDING (owner ask 2026-10-06, index §55, mig 1060).
//
// The owner, verbatim: "create a report for commisison withodimng which is a payment type in
// commimssion details reports uploaded everymonth - ti should be under flags which shows the acrrier
// has not paid the commimssion , need the standard filters as we enever got paid for these
// actiavtions and have to see what is the apeal status and whether they got paid int eh following
// month s- also need a report whch shows parrallely oif the epay payment was made for these
// activations"
//
// WHAT IT SHOWS (reuse, never re-derive — duplicate-check gate):
//   · The findings ARE commcalc.flags rows of the registered CHARGEBACK type, so this page runs no
//     detection of its own and a manager's ruling survives next month's upload (flag_persist).
//     GET /commcalc/commission-withholding.
//   · TAKEN BACK — the processor's debit legs for that activation, from the one clawback test
//     (clawback.py), which recognises them by the DIRECTION the money moved rather than a category
//     name no tenant can declare. That is the defect this report was built on; see §55.
//   · CAME BACK — commission paid against the SAME activation after the clawback, by month, through
//     the platform's one number-OR-device join (event_sales, with its IMEI fence).
//   · PAID IN PARALLEL — the processor payment actually made against that activation, shown BESIDE
//     the clawback and never netted against it: they are two facts and one net number hides both.
//   · APPEAL — appeal filed / won / denied / written off, with note and who/when, through the SAME
//     pure state machine the Commission Discrepancy hub uses (discrepancy_appeals.py). ALLOWED_NEXT
//     below is its display twin and only chooses which buttons to OFFER; the server is authoritative.
//
// FILTERS: the standard bar (RULE FIVE) — period range + store(s) + market(s) + rep(s). Extras ride
// `right`: recovery state / appeal state / search. Client-side over the loaded range, so
// what-you-see-is-what-exports.
//
// ABSENCE IS NEVER A ZERO, on screen as well as in the maths: an activation that could not be looked
// up reads "not known" and is kept OUT of the still-out headline, with its own count beside it. A
// $0.00 where the answer is unknown would be a fabricated loss.
//
// Money posture: READ-ONLY on money. Nothing here books, pays or re-declares anything. Where the
// org's own declarations are wrong about the money — a clawback declared as earnings, so the same
// rows also net into commission — that is REPORTED in the banner for a ruling, never repaired here.
import { useEffect, useMemo, useState } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { actorLabel } from '@/lib/actor'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, matchesStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SetupNotice } from '@/lib/setupNotice'

type Row = {
  flag_id: string; period: string; imei: string; mdn: string
  store: string; store_code: string; market: string; rep: string
  withheld: number; rebate_lost: number | null; phone_model: string | null
  first_withheld_on: string; last_withheld_on: string; description: string | null
  recovery_state: string; recovered: number | null; still_out: number
  recovered_months: Record<string, number>; undated_paid: number
  events_after: number; recovery_reason: string | null
  epay_state: string; epay_paid: number | null; epay_rows: number
  epay_last_paid_on: string; epay_types: string[]
  appeal_status: string | null; appeal_note: string | null
  appealed_by: string | null; appealed_at: string | null
}
type Cards = {
  findings: number; withheld: number; recovered: number; still_out: number
  unknown_withheld: number; unknown_findings: number; still_out_note: string | null
  epay_paid: number; epay_unknown_findings: number; epay_none_findings: number
  undated_paid: number
  by_recovery_state: Record<string, number>; by_appeal: Record<string, number>
  stores: number; reps: number
}
type Declaration = { type: string; category: string | null; rows: number; amount: number }
type Resp = {
  rows: Row[]; cards: Cards; appeals_ready: boolean; appeal_states: string[]
  as_of: string; feeds_loaded: string[]; feed_loaded: boolean
  recovery_notes: Record<string, string>; cutoff_note: string
  epay_notes: Record<string, string>; parallel_note: string
  declarations: {
    undeclared: Declaration[]; as_earnings: Declaration[]; outside_pay: Declaration[]
    undeclared_note: string; outside_pay_note: string; as_earnings_note: string | null
  } | null
  pending_calculation: { count: number; withheld: number; note: string | null }
}

// Display twin of discrepancy_appeals.ALLOWED_TRANSITIONS (server-authoritative; buttons only).
const ALLOWED_NEXT: Record<string, string[]> = {
  '': ['appeal_filed', 'written_off'],
  appeal_filed: ['appeal_won', 'appeal_denied', 'written_off', ''],
  appeal_denied: ['appeal_filed', 'written_off', ''],
  appeal_won: [''],
  written_off: ['appeal_filed', ''],
}
const APPEAL_LABEL: Record<string, string> = {
  '': 'Clear', appeal_filed: 'Appeal filed', appeal_won: 'Appeal won',
  appeal_denied: 'Appeal denied', written_off: 'Written off',
}
const APPEAL_COLOR: Record<string, string> = {
  no_appeal: '#6b7280', appeal_filed: '#2563eb', appeal_won: '#16a34a',
  appeal_denied: '#dc2626', written_off: '#92400e',
}
// The two three-state censuses, in words a manager reads. The keys mirror withholding_report.py.
const RECOVERY_LABEL: Record<string, string> = {
  recovered: 'Paid back later',
  recovered_partly: 'Partly paid back',
  matched_not_recovered: 'Still not paid',
  unmatchable: 'Not known',
}
const RECOVERY_COLOR: Record<string, string> = {
  recovered: '#16a34a', recovered_partly: '#b45309',
  matched_not_recovered: '#dc2626', unmatchable: '#6b7280',
}
const EPAY_LABEL: Record<string, string> = {
  paid: 'Paid', no_payment_found: 'No payment found', feed_not_loaded: 'Not known',
}

function toMonth(label: string): string {
  const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December']
  const [mon, yr] = label.split(' ')
  const m = months.indexOf(mon) + 1
  return m ? `${yr}-${String(m).padStart(2, '0')}` : label
}
function monthsBack(ym: string, n: number): string {
  const [y, m] = ym.split('-').map(Number)
  const t = y * 12 + (m - 1) - n
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, '0')}`
}
/** A dollar figure, or an em dash when the answer is NOT KNOWN. Never $0.00 for an unknown. */
function money(v: number | null | undefined): string {
  return v == null ? '—' : fmt(v)
}

export default function CommissionWithholdingPage() {
  const { period } = usePeriod()
  const cur = toMonth(period)

  const [from, setFrom] = useState(cur)
  const [to, setTo] = useState(cur)
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState<string | null>(null)
  const [showDecl, setShowDecl] = useState(false)

  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [recFilter, setRecFilter] = useState('')
  const [appealFilter, setAppealFilter] = useState('')
  const [search, setSearch] = useState('')

  useEffect(() => { setFrom(cur); setTo(cur) }, [cur])

  const load = async (f = from, t = to) => {
    setLoading(true); setErr('')
    try {
      const r = await api(`/api/v1/commcalc/commission-withholding?org_id=${ORG_ID}` +
        `&period_from=${encodeURIComponent(f)}&period_to=${encodeURIComponent(t)}`)
      setData(r)
    } catch (e: any) { setErr(e.message || 'Failed to load') }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [from, to]) // eslint-disable-line react-hooks/exhaustive-deps

  const rows = data?.rows || []
  const filtered = useMemo(() => rows.filter(r => {
    if (!matchesStandardFilter(r, filt, {
      store: x => x.store_code || x.store, market: x => x.market, rep: x => x.rep,
      date: x => x.last_withheld_on,
    })) return false
    if (recFilter && r.recovery_state !== recFilter) return false
    if (appealFilter === 'none' && r.appeal_status) return false
    if (appealFilter && appealFilter !== 'none' && r.appeal_status !== appealFilter) return false
    if (search) {
      const s = search.toLowerCase()
      if (![r.imei, r.mdn, r.phone_model, r.rep, r.store, r.appeal_note, r.description]
        .some(v => (v || '').toLowerCase().includes(s))) return false
    }
    return true
  }), [rows, filt, recFilter, appealFilter, search])

  const setAppeal = async (row: Row, next: string) => {
    let note: string | null = ''
    if (next) {
      note = window.prompt(`${APPEAL_LABEL[next]} — add a note (optional):`, row.appeal_note || '')
      if (note === null) return
    } else if (!window.confirm('Clear the appeal state on this finding?')) return
    setSaving(row.flag_id); setMsg('')
    try {
      const r = await api(
        `/api/v1/commcalc/commission-withholding/${row.flag_id}/appeal?org_id=${ORG_ID}`,
        { method: 'PATCH', body: JSON.stringify({ appeal_status: next, appeal_note: note }) })
      setData(d => d ? {
        ...d,
        rows: d.rows.map(x => x.flag_id === row.flag_id ? {
          ...x, appeal_status: r.appeal_status, appeal_note: r.appeal_note,
          appealed_by: r.appealed_by, appealed_at: r.appealed_at,
        } : x),
      } : d)
      setMsg(next ? `Finding marked "${APPEAL_LABEL[next]}".` : 'Appeal state cleared.')
    } catch (e: any) { setErr(e.message || 'Could not save appeal state') }
    finally { setSaving(null) }
  }

  const c = data?.cards
  const decl = data?.declarations
  const card = (label: string, value: string, sub: string, color: string) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 160, flex: '1 1 160px' }}>
      <div style={{ fontSize: 11.5, color: 'var(--text2)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.04em' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color, margin: '2px 0' }}>{value}</div>
      <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>{sub}</div>
    </div>
  )

  function buildPayload(): ExportPayload {
    return {
      title: 'Commission Withholding',
      subtitle: `${from} → ${to} · taken back, paid back, and the processor payment beside it`,
      filename: `commission-withholding-${from}-${to}`,
      sheets: [{
        name: 'Withholding', rows: filtered, columns: [
          { header: 'Period', get: (r: Row) => r.period },
          { header: 'Store', get: (r: Row) => r.store_code || r.store },
          { header: 'Market', get: (r: Row) => r.market },
          { header: 'Rep', get: (r: Row) => r.rep },
          { header: 'IMEI', get: (r: Row) => r.imei },
          { header: 'MDN', get: (r: Row) => r.mdn },
          { header: 'Device', get: (r: Row) => r.phone_model || '' },
          { header: 'Taken back on', get: (r: Row) => r.last_withheld_on },
          { header: 'Taken back', get: (r: Row) => r.withheld, money: true },
          { header: 'Outcome', get: (r: Row) => RECOVERY_LABEL[r.recovery_state] || r.recovery_state },
          { header: 'Paid back later', get: (r: Row) => r.recovered },
          { header: 'Paid back in', get: (r: Row) => Object.keys(r.recovered_months || {}).join(', ') },
          { header: 'Still out', get: (r: Row) => r.recovery_state === 'unmatchable' ? '' : r.still_out },
          { header: 'Why not known', get: (r: Row) => r.recovery_reason || '' },
          { header: 'Processor paid', get: (r: Row) => r.epay_paid },
          { header: 'Processor paid state', get: (r: Row) => EPAY_LABEL[r.epay_state] || r.epay_state },
          { header: 'Appeal', get: (r: Row) => r.appeal_status ? APPEAL_LABEL[r.appeal_status] : '' },
          { header: 'Appeal note', get: (r: Row) => r.appeal_note || '' },
          { header: 'Appealed at', get: (r: Row) => r.appealed_at || '' },
        ],
      }],
    }
  }

  const selStyle: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const th: React.CSSProperties = { padding: '8px 10px', textAlign: 'left', fontSize: 11.5, color: 'var(--text2)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.03em', whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { padding: '8px 10px', borderTop: '1px solid var(--border)', fontSize: 13, verticalAlign: 'top' }
  const num: React.CSSProperties = { ...td, textAlign: 'right', whiteSpace: 'nowrap' }

  return (
    <div style={{ padding: 24, maxWidth: 1600, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 4 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔙 Commission Withholding</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13.5, margin: '4px 0 0', maxWidth: 900, lineHeight: 1.55 }}>
            Every activation the carrier took commission <b>back</b> on. For each one: what was taken,
            whether commission landed against the same activation in a <b>later month</b> and which
            months it landed in, and — side by side, never netted — the <b>payment the processor
            actually made</b> against that activation. Mark the appeal on each finding; who and when
            is recorded. An activation that could not be looked up reads <b>not known</b> and is kept
            out of the still-out total rather than counted as a loss.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>From{' '}
            <input type="month" value={from} onChange={e => setFrom(e.target.value)} style={selStyle} /></label>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>To{' '}
            <input type="month" value={to} onChange={e => setTo(e.target.value)} style={selStyle} /></label>
          <button className="btn" onClick={() => { setFrom(monthsBack(cur, 5)); setTo(cur) }} style={{ fontSize: 12 }}>Last 6 months</button>
          {filtered.length > 0 && <ExportButtons payload={buildPayload} />}
        </div>
      </div>

      {err && <div style={{ background: '#fef2f2', color: '#991b1b', padding: 12, borderRadius: 8, margin: '12px 0', fontSize: 13.5 }}>{err}</div>}
      {msg && <div style={{ background: '#f0fdf4', color: '#166534', padding: 10, borderRadius: 8, margin: '12px 0', fontSize: 13 }}>{msg}</div>}
      {data && !data.appeals_ready && (
        <div style={{ background: '#fffbeb', color: '#92400e', padding: 12, borderRadius: 8, margin: '12px 0', fontSize: 13 }}>
          <SetupNotice lead="Appeal tracking is not available yet. Every figure below still works." detail="1060_flag_appeal_state.sql" />
        </div>
      )}

      {/* THE MONEY FINDING. A clawback the org declares as EARNINGS nets into commission with
          nothing saying it was taken back. Re-declaring it moves money, so it is surfaced, not done. */}
      {decl?.as_earnings_note && decl.as_earnings.length > 0 && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', padding: '12px 16px', borderRadius: 8, margin: '12px 0', fontSize: 13, lineHeight: 1.55 }}>
          <b>⚠️ Worth a decision.</b> {decl.as_earnings_note}
          <table style={{ borderCollapse: 'collapse', marginTop: 8, fontSize: 12.5 }}>
            <tbody>
              {decl.as_earnings.map(d => (
                <tr key={d.type}>
                  <td style={{ padding: '2px 14px 2px 0' }}><b>{d.type}</b></td>
                  <td style={{ padding: '2px 14px 2px 0' }}>declared as “{d.category}”</td>
                  <td style={{ padding: '2px 14px 2px 0', textAlign: 'right' }}>{fmt(d.amount)}</td>
                  <td style={{ padding: '2px 0', color: 'var(--text2)' }}>{d.rows} row{d.rows === 1 ? '' : 's'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(decl.undeclared.length > 0 || decl.outside_pay.length > 0) && (
            <div style={{ marginTop: 8 }}>
              <button className="btn" style={{ fontSize: 12 }} onClick={() => setShowDecl(s => !s)}>
                {showDecl ? 'Hide' : 'Show'} the other money the processor took back
              </button>
              {showDecl && (
                <div style={{ marginTop: 8, fontSize: 12.5 }}>
                  {decl.undeclared.length > 0 && (
                    <div style={{ marginBottom: 6 }}>
                      <b>Not declared at all</b> — {decl.undeclared_note}
                      <ul style={{ margin: '4px 0 0 18px' }}>
                        {decl.undeclared.map(d => <li key={d.type}>{d.type} — {fmt(d.amount)} over {d.rows} row{d.rows === 1 ? '' : 's'}</li>)}
                      </ul>
                    </div>
                  )}
                  {decl.outside_pay.length > 0 && (
                    <div>
                      <b>Correctly outside this report</b> — {decl.outside_pay_note}
                      <ul style={{ margin: '4px 0 0 18px' }}>
                        {decl.outside_pay.map(d => <li key={d.type}>{d.type} ({d.category}) — {fmt(d.amount)} over {d.rows} row{d.rows === 1 ? '' : 's'}</li>)}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* The feed has clawbacks this queue does not: a REASON, never a quietly shorter report. */}
      {data?.pending_calculation?.note && (
        <div style={{ background: '#eff6ff', color: '#1e40af', padding: '10px 16px', borderRadius: 8, margin: '12px 0', fontSize: 13 }}>
          <b>{data.pending_calculation.count}</b> clawback{data.pending_calculation.count === 1 ? '' : 's'} worth{' '}
          <b>{fmt(data.pending_calculation.withheld)}</b> are in the feed but not yet in this queue. {data.pending_calculation.note}
        </div>
      )}

      {/* Headline cards — the whole loaded range, before client filters. */}
      <div style={{ display: 'flex', gap: 12, margin: '14px 0', flexWrap: 'wrap' }}>
        {card('Taken back', fmt(c?.withheld || 0), `${c?.findings || 0} activation${c?.findings === 1 ? '' : 's'} · ${c?.stores || 0} store${c?.stores === 1 ? '' : 's'}`, '#dc2626')}
        {card('Paid back later', fmt(c?.recovered || 0), 'commission that landed after the clawback', RECOVERY_COLOR.recovered)}
        {card('Still out', fmt(c?.still_out || 0), `${c?.by_recovery_state?.matched_not_recovered || 0} never paid back`, '#b45309')}
        {card('Not known', fmt(c?.unknown_withheld || 0), `${c?.unknown_findings || 0} could not be looked up`, RECOVERY_COLOR.unmatchable)}
        {card('Processor paid', fmt(c?.epay_paid || 0), 'on these same activations · never netted', '#2563eb')}
        {card('Appeals open', String(c?.by_appeal?.appeal_filed || 0), `${c?.by_appeal?.no_appeal || 0} with no ruling yet`, APPEAL_COLOR.appeal_filed)}
      </div>
      {c?.still_out_note && (
        <p style={{ fontSize: 12.5, color: 'var(--text2)', margin: '-6px 0 10px', lineHeight: 1.5 }}>{c.still_out_note}</p>
      )}
      {data?.cutoff_note && (
        <p style={{ fontSize: 12.5, color: 'var(--text3)', margin: '0 0 10px', lineHeight: 1.5 }}>{data.cutoff_note}</p>
      )}

      <StandardFilterBar
        value={filt}
        onChange={setFilt}
        periodMode="range"
        show={{ period: true, stores: true, markets: true, reps: true }}
        optionsUrl={`/api/v1/core/filter-options?org_id=${ORG_ID}`}
        right={
          <>
            <select value={recFilter} onChange={e => setRecFilter(e.target.value)} style={selStyle} aria-label="Outcome">
              <option value="">Any outcome</option>
              {Object.entries(RECOVERY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <select value={appealFilter} onChange={e => setAppealFilter(e.target.value)} style={selStyle} aria-label="Appeal state">
              <option value="">Any appeal state</option>
              <option value="none">No ruling yet</option>
              {(data?.appeal_states || []).map(s => <option key={s} value={s}>{APPEAL_LABEL[s] || s}</option>)}
            </select>
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search IMEI / number / device / rep"
              style={{ ...selStyle, width: 240 }} aria-label="Search" />
          </>
        }
      />

      {loading && <p style={{ color: 'var(--text2)', fontSize: 13.5 }}>Loading…</p>}
      {!loading && filtered.length === 0 && (
        <div className="card" style={{ padding: 18, fontSize: 13.5, color: 'var(--text2)', lineHeight: 1.6 }}>
          {rows.length === 0
            ? <>No commission was taken back in this range. {!data?.feed_loaded && <>The processor feed for this range is <b>not loaded</b>, so this is “nothing to look at”, not “nothing happened” — upload the month and reload.</>}</>
            : <>No finding matches these filters. {rows.length} are loaded for the range.</>}
        </div>
      )}

      {filtered.length > 0 && (
        <div className="card" style={{ padding: 0, overflowX: 'auto', marginTop: 12 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1280 }}>
            <thead>
              <tr>
                <th style={th}>Store · rep</th>
                <th style={th}>Activation</th>
                <th style={th}>Taken back</th>
                <th style={{ ...th, textAlign: 'right' }}>Amount</th>
                <th style={th}>Outcome</th>
                <th style={{ ...th, textAlign: 'right' }}>Paid back</th>
                <th style={{ ...th, textAlign: 'right' }}>Still out</th>
                <th style={{ ...th, textAlign: 'right' }}>Processor paid</th>
                <th style={th}>Appeal</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(r => {
                const next = ALLOWED_NEXT[r.appeal_status || ''] || []
                const months = Object.entries(r.recovered_months || {})
                return (
                  <tr key={r.flag_id}>
                    <td style={td}>
                      <div style={{ fontWeight: 600 }}>{r.store_code || r.store || '—'}</div>
                      <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                        {r.rep || 'nobody named'}{r.market ? ` · ${r.market}` : ''}
                      </div>
                    </td>
                    <td style={td}>
                      <div style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.imei || r.mdn || '—'}</div>
                      <div style={{ fontSize: 12, color: 'var(--text3)' }}>{r.phone_model || ''}</div>
                    </td>
                    <td style={td}>
                      <div>{r.last_withheld_on || '—'}</div>
                      <div style={{ fontSize: 12, color: 'var(--text3)' }}>{r.period}</div>
                    </td>
                    <td style={{ ...num, fontWeight: 600, color: '#dc2626' }}>{fmt(r.withheld)}</td>
                    <td style={td}>
                      <span style={{ fontWeight: 600, color: RECOVERY_COLOR[r.recovery_state] || 'var(--text)' }}
                        title={data?.recovery_notes?.[r.recovery_state] || ''}>
                        {RECOVERY_LABEL[r.recovery_state] || r.recovery_state}
                      </span>
                      {r.recovery_reason && (
                        <div style={{ fontSize: 12, color: 'var(--text3)' }}>{r.recovery_reason}</div>
                      )}
                      {months.length > 0 && (
                        <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                          paid in {months.map(([m, v]) => `${m} (${fmt(v)})`).join(', ')}
                        </div>
                      )}
                      {r.undated_paid > 0 && (
                        <div style={{ fontSize: 12, color: '#92400e' }}>
                          {fmt(r.undated_paid)} paid with no date — not counted either way
                        </div>
                      )}
                    </td>
                    <td style={num}>{money(r.recovered)}</td>
                    <td style={{ ...num, fontWeight: r.still_out > 0 ? 600 : 400 }}>
                      {r.recovery_state === 'unmatchable' ? '—' : fmt(r.still_out)}
                    </td>
                    <td style={num}>
                      {money(r.epay_paid)}
                      <div style={{ fontSize: 11.5, color: 'var(--text3)' }}
                        title={data?.epay_notes?.[r.epay_state] || ''}>
                        {EPAY_LABEL[r.epay_state] || r.epay_state}
                        {r.epay_last_paid_on ? ` · ${r.epay_last_paid_on}` : ''}
                      </div>
                    </td>
                    <td style={td}>
                      <div style={{ fontWeight: 600, color: APPEAL_COLOR[r.appeal_status || 'no_appeal'] }}>
                        {r.appeal_status ? APPEAL_LABEL[r.appeal_status] : 'No ruling yet'}
                      </div>
                      {r.appealed_at && (
                        <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
                          {actorLabel(r.appealed_by)} · {String(r.appealed_at).slice(0, 10)}
                        </div>
                      )}
                      {r.appeal_note && (
                        <div style={{ fontSize: 11.5, color: 'var(--text2)', maxWidth: 220 }}>{r.appeal_note}</div>
                      )}
                      {data?.appeals_ready && (
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
                          {next.map(n => (
                            <button key={n} className="btn" disabled={saving === r.flag_id}
                              onClick={() => setAppeal(r, n)}
                              style={{ fontSize: 11, padding: '2px 7px' }}>
                              {APPEAL_LABEL[n]}
                            </button>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {data && (
        <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12, lineHeight: 1.6 }}>
          {data.parallel_note} Paid-back figures are a floor as of {data.as_of}: commission on a line
          keeps arriving for months, so a “still not paid” row can stop being one.
          {data.feeds_loaded?.length ? ` Looked up in: ${data.feeds_loaded.join(', ')}.` : ' No per-line commission feed is loaded, so no outcome could be looked up.'}
        </p>
      )}
    </div>
  )
}
