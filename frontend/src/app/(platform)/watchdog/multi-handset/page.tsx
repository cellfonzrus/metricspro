'use client'
// SEVERAL HANDSETS TO ONE BUYER — THE DRILL-DOWN (owner ask 2026-10-12: "for all these flagged
// activations with more than one phone sold we need to have the drilldown to check who made the sale
// for how much, if no accessories are sold with the sale and no activation fee it is definletly
// leaning towards fraud").
//
// WHY A PAGE AND NOT A COLUMN ON THE BOARD. The findings table holds one row per case, and a
// per-line breakdown is not one of its columns — so the board keeps the one-line finding a manager
// scans, and this reads the lines back from the sales feed at request time via
// GET /commcalc/watchdog/multi-handset. Both go through one backend reader, so the finding and the
// drill-down cannot disagree about what the month contained.
//
// IT REPORTS AND DOES NOT CONCLUDE, and that is deliberate rather than squeamish. Measured on live
// October 2026 (17 flagged cases): a set-up or activation fee is present on EVERY one of them,
// because the house bills one per handset, so the owner's "no accessories AND no activation fee"
// would have rendered an empty screen. Accessory dollars alone narrow 17 cases to 2 — one of which
// is four promo phones at $0.00 to one buyer, and the other of which looks like a genuine household
// bundle. So the numbers are shown, the filter is offered, and the call stays the manager's.
//
// "Not measured" and "none sold" are rendered DIFFERENTLY on purpose: a company that has not
// declared what an accessory is must not read as a chain full of fraud.
import { Fragment, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import PageIntro from '@/components/PageIntro'
import StatTile from '@/components/StatTile'
import { ExportButtons, type ExportPayload } from '@/lib/export'
import { SortableTh, useTableSort } from '@/components/SortableTh'

type Line = {
  receipt: string; rep: string | null; device: string | null
  serial: string | null; amount: number; department: string | null
}
type Case = {
  store: string; day: string; buyer: string; buyer_as_recorded: string[]
  handsets: number; receipts: string[]; receipt_count: number
  split_across_receipts: boolean; dollars: number; all_zero_dollars: boolean
  reps: string[]; lines: Line[]
  accessory_dollars: number | null; accessory_lines: number | null
  fee_lines: number | null; companion_lines: number
  description: string; coaching_note: string; signal: string[]
}
// One handset line with its case's identity folded in — the second export sheet's row. Named so the
// export columns can be typed; `any` there would let a renamed field through silently.
type FlatLine = Line & { day: string; store: string; buyer: string }
type Summary = {
  handset_lines?: number; buyer_days?: number; cases?: number
  handsets_in_cases?: number; split_cases?: number; zero_dollar_cases?: number
  unattributable_lines?: number
  cases_no_accessory?: number | null; cases_no_accessory_no_fee?: number | null
  cannot_answer?: string[]
}
type Payload = {
  period?: string; threshold?: number; enabled?: boolean
  box_departments?: string[]; has_feed?: boolean
  summary?: Summary; cases?: Case[]
}

// THE accessor for the table below, at module scope so `useTableSort`'s memo is not rebuilt on
// every render. It returns raw numbers and raw nulls rather than the formatted cell, so an
// unmeasured accessory figure sinks instead of sorting as a $0.00 that would read as a finding.
const caseCell = (c: Case, field: string): string | number | null => {
  switch (field) {
    case 'day': return c.day || ''
    case 'store': return c.store || ''
    case 'buyer': return (c.buyer_as_recorded || [])[0] || c.buyer || ''
    case 'handsets': return c.handsets
    case 'receipts': return c.receipt_count
    case 'dollars': return c.dollars
    case 'accessories': return c.accessory_dollars
    case 'fees': return c.fee_lines
    case 'reps': return (c.reps || []).join(', ')
    default: return ''
  }
}

const th: React.CSSProperties = { padding: '6px 8px' }
const thRight: React.CSSProperties = { padding: '6px 8px', textAlign: 'right' }
const td: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border, var(--c-bg-e5e7eb))' }
const tdRight: React.CSSProperties = { ...td, textAlign: 'right' }

// A measured zero and an unmeasured signal must never render the same.
function money(v: number | null | undefined) {
  return v === null || v === undefined ? 'not measured' : fmt(v)
}

export default function MultiHandsetDrillDownPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<Payload | null>(null)
  const [err, setErr] = useState('')
  const [loadedPeriod, setLoadedPeriod] = useState<string | null>(null)
  const [onlyNoAcc, setOnlyNoAcc] = useState(false)
  const [open, setOpen] = useState<Record<string, boolean>>({})

  // `loading` is DERIVED from "which period have we actually loaded", not held in its own state.
  // Writing setLoading(true) in the effect body is a synchronous setState inside an effect, which
  // cascades a render and which the house lint rejects — and the derived form is simply truer: the
  // page is loading exactly while the period on screen is not the period in hand.
  const loading = loadedPeriod !== (period || '')

  useEffect(() => {
    let live = true
    api(`/api/v1/commcalc/watchdog/multi-handset?period=${encodeURIComponent(period || '')}`)
      .then((d: Payload) => { if (live) { setData(d); setErr('') } })
      .catch(e => { if (live) { setErr(e?.message || String(e)); setData(null) } })
      .finally(() => { if (live) setLoadedPeriod(period || '') })
    // A period switched twice in quick succession must not let the FIRST answer land second.
    return () => { live = false }
  }, [period])

  const cases = useMemo(() => data?.cases || [], [data])
  const shown = useMemo(
    () => (onlyNoAcc ? cases.filter(c => c.accessory_dollars === 0) : cases),
    [cases, onlyNoAcc])

  const keyOf = (c: Case) => `${c.store}|${c.day}|${c.buyer}`

  // Click-a-header sorting through the ONE comparison home (lib/table-sort via useTableSort) —
  // owner directive 2026-08-10, locked by harness_table_sort_lock. The default order is the
  // detector's own (most handsets first) until a header is clicked: `sort === null` passes the rows
  // through untouched.
  const sorted = useTableSort(shown, caseCell)

  const buildPayload = (): ExportPayload => ({
    title: 'Several handsets to one buyer',
    subtitle: data?.period || period || '',
    filename: `multi-handset-${(data?.period || period || '').replace(/\s+/g, '-')}`,
    sheets: [
      {
        name: 'Flagged days',
        columns: [
          { header: 'Date', get: (c: Case) => c.day, type: 'date' },
          { header: 'Store', get: (c: Case) => c.store, role: 'store' },
          { header: 'Buyer as recorded', get: (c: Case) => (c.buyer_as_recorded || []).join(' / ') },
          { header: 'Handsets', get: (c: Case) => c.handsets, type: 'number' },
          { header: 'Receipts', get: (c: Case) => c.receipt_count, type: 'number' },
          { header: 'Sold for', get: (c: Case) => c.dollars, money: true, align: 'right' },
          {
            header: 'Accessories on the sale',
            get: (c: Case) => (c.accessory_dollars === null ? 'not measured' : c.accessory_dollars),
            align: 'right',
          },
          {
            header: 'Fee lines',
            get: (c: Case) => (c.fee_lines === null ? 'not measured' : c.fee_lines),
          },
          { header: 'Who sold', get: (c: Case) => (c.reps || []).join(' / '), role: 'rep' },
          { header: 'Signal', get: (c: Case) => (c.signal || []).join('; ') },
        ],
        rows: sorted.sorted,   // export in the order the manager sorted them
      },
      {
        name: 'Every handset line',
        columns: [
          { header: 'Date', get: (r: FlatLine) => r.day, type: 'date' },
          { header: 'Store', get: (r: FlatLine) => r.store, role: 'store' },
          { header: 'Buyer', get: (r: FlatLine) => r.buyer },
          { header: 'Receipt', get: (r: FlatLine) => r.receipt },
          { header: 'Who sold it', get: (r: FlatLine) => r.rep || '', role: 'rep' },
          { header: 'Device', get: (r: FlatLine) => r.device || '' },
          { header: 'Serial', get: (r: FlatLine) => r.serial || '' },
          { header: 'Amount', get: (r: FlatLine) => r.amount, money: true, align: 'right' },
        ],
        rows: sorted.sorted.flatMap((c): FlatLine[] => (c.lines || []).map(l => ({
          day: c.day, store: c.store,
          buyer: (c.buyer_as_recorded || [])[0] || c.buyer, ...l,
        }))),
      },
    ],
  })

  const s = data?.summary || {}

  return (
    <div>
      <PageIntro
        title="Several handsets to one buyer"
        right={<Link href="/watchdog" style={{ fontSize: 13, color: 'var(--blue, var(--c-fg-2563eb))' }}>
          ← Management Watchdog
        </Link>}
        help={
          <>
            Days where one buyer took more handsets from one store than the company allows for,
            counted across every receipt the sale was split over. Open a row to see who sold each
            handset, for how much, and what else was on the same receipts. These are findings to
            check, not conclusions: the system does not decide what happened.
          </>
        }
      />

      {err && (
        <div style={{ border: '1px solid var(--red, var(--c-bg-dc2626))', borderRadius: 8, padding: '10px 12px',
                      marginBottom: 14, fontSize: 13, color: 'var(--text2)' }}>
          The drill-down could not be loaded: {err}
        </div>
      )}

      {loading && !data && <p style={{ color: 'var(--text2)', fontSize: 13 }}>Loading…</p>}

      {data && data.enabled === false && (
        <p style={{ fontSize: 13, color: 'var(--text2)' }}>
          This check is switched off for your company, so nothing is being looked for. Turn it on
          from the Watchdog rules and the days appear here.
        </p>
      )}

      {data && data.enabled !== false && (data.box_departments || []).length === 0 && (
        <p style={{ fontSize: 13, color: 'var(--text2)' }}>
          Your company has not declared which departments are a device sale, so no line can be
          recognised as a handset and nothing can be counted — which is not the same as nothing to
          find. Declare them on the Sales settings screen and this fills in.
        </p>
      )}

      {data && data.has_feed === false && (
        <p style={{ fontSize: 13, color: 'var(--text2)' }}>
          No sales were received for {data.period || period}, so there is nothing to report — which is
          not the same as nothing to find.
        </p>
      )}

      {data && data.enabled !== false && data.has_feed !== false
        && (data.box_departments || []).length > 0 && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
            <StatTile label="Days to check" value={s.cases ?? 0} accent="#d97706"
                      sub={`more than ${data.threshold ?? 2} handsets to one buyer`} />
            <StatTile label="Handsets involved" value={s.handsets_in_cases ?? 0}
                      sub={`of ${s.handset_lines ?? 0} sold in the period`} />
            <StatTile label="Split over receipts" value={s.split_cases ?? 0}
                      sub="a per-receipt check would miss these" />
            <StatTile label="No accessories on the sale"
                      value={s.cases_no_accessory === null || s.cases_no_accessory === undefined
                        ? 'not measured' : s.cases_no_accessory}
                      accent="#dc2626" sub="the signal worth opening first" />
            <StatTile label="Every line at $0.00" value={s.zero_dollar_cases ?? 0}
                      sub="nothing was charged" />
            <StatTile label="No buyer recorded" value={s.unattributable_lines ?? 0}
                      sub="cannot be grouped to anyone" />
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center',
                        marginBottom: 12 }}>
            <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={onlyNoAcc}
                     onChange={e => setOnlyNoAcc(e.target.checked)} />
              Only days with no accessories sold alongside
            </label>
            <span style={{ fontSize: 12.5, color: 'var(--text2)' }}>
              {shown.length} of {cases.length}
            </span>
            <span style={{ marginLeft: 'auto' }}>
              <ExportButtons payload={buildPayload} compact />
            </span>
          </div>

          {cases.length === 0 ? (
            <p style={{ fontSize: 13, color: 'var(--text2)' }}>
              No buyer took more than {data.threshold ?? 2} handsets from one store on one day in{' '}
              {data.period || period}. The feed was received and{' '}
              {s.handset_lines ?? 0} handset lines were looked at, so this is a measured clean period.
            </p>
          ) : shown.length === 0 ? (
            <p style={{ fontSize: 13, color: 'var(--text2)' }}>
              Every flagged day in {data.period || period} had accessories sold alongside. Clear the
              filter to see all {cases.length}.
            </p>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--text2)', fontSize: 11.5,
                               textTransform: 'uppercase', letterSpacing: '0.4px' }}>
                    <th style={th}></th>
                    <SortableTh field="day" sort={sorted.sort} onSort={sorted.toggle} style={th}>Date</SortableTh>
                    <SortableTh field="store" sort={sorted.sort} onSort={sorted.toggle} style={th}>Store</SortableTh>
                    <SortableTh field="buyer" sort={sorted.sort} onSort={sorted.toggle} style={th}>Buyer as recorded</SortableTh>
                    <SortableTh field="handsets" sort={sorted.sort} onSort={sorted.toggle} style={thRight}>Handsets</SortableTh>
                    <SortableTh field="receipts" sort={sorted.sort} onSort={sorted.toggle} style={thRight}>Receipts</SortableTh>
                    <SortableTh field="dollars" sort={sorted.sort} onSort={sorted.toggle} style={thRight}>Sold for</SortableTh>
                    <SortableTh field="accessories" sort={sorted.sort} onSort={sorted.toggle} style={thRight}>Accessories</SortableTh>
                    <SortableTh field="fees" sort={sorted.sort} onSort={sorted.toggle} style={thRight}>Fee lines</SortableTh>
                    <SortableTh field="reps" sort={sorted.sort} onSort={sorted.toggle} style={th}>Who sold</SortableTh>
                  </tr>
                </thead>
                <tbody>
                  {sorted.sorted.map(c => {
                    const k = keyOf(c)
                    const isOpen = !!open[k]
                    return (
                      <Fragment key={k}>
                        <tr style={{ cursor: 'pointer' }}
                            onClick={() => setOpen(o => ({ ...o, [k]: !o[k] }))}>
                          <td style={td}>{isOpen ? '▾' : '▸'}</td>
                          <td style={td}>{c.day}</td>
                          <td style={td}>{c.store}</td>
                          <td style={td}>{(c.buyer_as_recorded || [])[0] || c.buyer}</td>
                          <td style={tdRight}>{c.handsets}</td>
                          <td style={tdRight}>
                            {c.receipt_count}
                            {c.split_across_receipts && (
                              <span style={{ color: 'var(--c-fg-d97706)' }} title="split over more than one receipt"> ✱</span>
                            )}
                          </td>
                          <td style={tdRight}>{c.all_zero_dollars ? '$0.00' : fmt(c.dollars)}</td>
                          <td style={{ ...tdRight,
                                       color: c.accessory_dollars === 0 ? '#dc2626' : undefined }}>
                            {money(c.accessory_dollars)}
                          </td>
                          <td style={tdRight}>
                            {c.fee_lines === null ? 'not measured' : c.fee_lines}
                          </td>
                          <td style={td}>{(c.reps || []).join(', ') || '—'}</td>
                        </tr>
                        {isOpen && (
                          <tr key={`${k}-d`}>
                            <td style={{ ...td, background: 'var(--surface-2, var(--c-bg-f9fafb))' }} colSpan={10}>
                              <p style={{ margin: '0 0 8px', fontSize: 13 }}>{c.description}</p>
                              <p style={{ margin: '0 0 10px', fontSize: 12.5,
                                          color: 'var(--text2)' }}>{c.coaching_note}</p>
                              <table style={{ width: '100%', borderCollapse: 'collapse',
                                              fontSize: 12.5 }}>
                                <thead>
                                  <tr style={{ textAlign: 'left', color: 'var(--text2)',
                                               fontSize: 11 }}>
                                    <th style={th}>Receipt</th>
                                    <th style={th}>Who sold it</th>
                                    <th style={th}>Device</th>
                                    <th style={th}>Serial</th>
                                    <th style={thRight}>Amount</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {(c.lines || []).map((l, i) => (
                                    <tr key={`${k}-${i}`}>
                                      <td style={td}>{l.receipt || '—'}</td>
                                      <td style={td}>{l.rep || 'nobody named'}</td>
                                      <td style={td}>{l.device || '—'}</td>
                                      <td style={td}>{l.serial || '—'}</td>
                                      <td style={tdRight}>{fmt(l.amount)}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                              <p style={{ margin: '10px 0 0', fontSize: 12,
                                          color: 'var(--text2)' }}>
                                {c.companion_lines} other line
                                {c.companion_lines === 1 ? '' : 's'} on the same receipt
                                {c.receipt_count === 1 ? '' : 's'}
                                {c.accessory_lines !== null && (
                                  <>, {c.accessory_lines} of them accessories</>
                                )}.
                              </p>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {(s.cannot_answer || []).length > 0 && (
            <div style={{ marginTop: 18, fontSize: 12.5, color: 'var(--text2)' }}>
              <p style={{ margin: '0 0 6px', fontWeight: 600 }}>What this check cannot tell you</p>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {(s.cannot_answer || []).map((t, i) => <li key={i}>{t}</li>)}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  )
}
