'use client'
// A BROUGHT DEVICE AND AN UPGRADE ON ONE NUMBER IN ONE DAY (owner ask 2026-10-28, index
// §COMMCALC-42).
//
// Owner, verbatim: *"Need to create a new flag report to check if a byod and an upgrade is done on
// the same day for the same phone number at the same store, add standard filters and send options"*.
//
// THE FILTERS AND THE SEND BUTTON ARE NOT BUILT HERE, AND THAT IS THE WHOLE POINT. `<ReportShell>`
// is the platform's report frame and it already owns, from the same `columns` an export needs:
// the By-rep / By-store / By-date-range / By-month quick filters plus "＋ Add filter" on ANY column,
// Group-by with subtotals, Excel / PDF / Print, and 📤 Send (`lib/send-report.SendReportButton`,
// THE one sender — the recipient picker, the email/WhatsApp ladder, the Notify history). Hand-rolling
// either would be the §COMMCALC-19 defect again: a report whose numbers nobody can mail gets retyped
// into an email. So this file supplies columns, rows and the explanation, and nothing else.
//
// WHY THE STORE FILTER BINDS ON THE ROW'S OWN VALUE AND IS SAFE HERE (§64). A store filter that
// compares a PICKER's folded label to a row's free-text spelling silently matches nothing, which is
// the 2026-10-22 defect. ReportShell derives its store options from the ROWS it was handed, so the
// option and the value are the same string by construction and there is no second vocabulary to
// disagree with. The server has already span-scoped those rows through the same keyset the flag
// board uses, so this is a narrowing of what the viewer may see, never a widening.
//
// IT REPORTS AND DOES NOT CONCLUDE. A customer who activates a line on a phone they brought in and
// then buys a handset the same day is an ordinary sale and reads identically on this screen. Measured
// live 2026-10-28 over Aug–Oct: all 95 house cases carry the SAME shape (no handset on the first
// activation, a promo handset on the second, two receipts), which is worth a manager's eye and is
// not a finding of wrongdoing. The sentence per row says what was rung; the call is the manager's.
//
// THREE ABSENCES ARE RENDERED DIFFERENTLY FROM ZERO, on purpose:
//   · the check switched off,
//   · no sales received for the period,
//   · and the two that bite hardest — only a small share of activations could be matched to a phone
//     line at all (one live tenant: 6.1%, because its feed carries no number), and a declared box
//     vocabulary that matched no line in the period (so every handset figure is unreliable rather
//     than 0). A chain that was never measured must not read as a chain that is clean.
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import PageIntro from '@/components/PageIntro'
import StatTile from '@/components/StatTile'
import ReportShell from '@/components/ReportShell'
import { type ExportColumn } from '@/lib/export'

type Line = {
  cls: string; class_label: string; receipt: string; rep: string | null
  item: string | null; department: string | null; serial: string | null; amount: number
}
type Case = {
  store: string; day: string; number: string
  classes: string[]; class_labels: string[]
  events: number; receipts: string[]; receipt_count: number; separate_receipts: boolean
  dollars: number; all_zero_dollars: boolean; reps: string[]
  first_class: string; second_class: string
  first_handsets: number | null; second_handsets: number | null
  first_devices: string[] | null; second_devices: string[] | null
  first_receipts: string[]; second_receipts: string[]
  lines: Line[]
  description: string; coaching_note: string; signal: string[]
}
type Summary = {
  events?: number; events_with_number?: number; events_without_number?: number
  numbered_share?: number | null; number_days?: number
  handset_lines?: number | null; box_vocabulary_matched?: boolean | null
  cases?: number; multi_event_cases?: number; zero_dollar_cases?: number; dollars?: number
  cases_upgrade_device_only?: number | null
  cannot_answer?: string[]
}
type Payload = {
  period?: string; enabled?: boolean; classes?: string[]; class_labels?: string[]
  box_departments?: string[]; sales_table?: string; has_feed?: boolean
  summary?: Summary; cases?: Case[]
}

// A measured zero and an unmeasured signal must never render the same string.
const num = (v: number | null | undefined) =>
  v === null || v === undefined ? 'not measured' : v
const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? 'not measured' : `${(v * 100).toFixed(1)}%`

export default function ByodUpgradeSameNumberPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<Payload | null>(null)
  const [err, setErr] = useState('')
  const [loadedPeriod, setLoadedPeriod] = useState<string | null>(null)
  const [openKey, setOpenKey] = useState<string>('')

  // `loading` is DERIVED from "which period have we actually loaded" rather than held in its own
  // state: a setState in the effect body cascades a render and the house lint rejects it. The
  // derived form is also simply truer — the page is loading exactly while the period on screen is
  // not the period in hand.
  const loading = loadedPeriod !== (period || '')

  useEffect(() => {
    let live = true
    api(`/api/v1/commcalc/watchdog/byod-upgrade?period=${encodeURIComponent(period || '')}`)
      .then((d: Payload) => { if (live) { setData(d); setErr('') } })
      .catch(e => { if (live) { setErr(e?.message || String(e)); setData(null) } })
      .finally(() => { if (live) setLoadedPeriod(period || '') })
    // A period switched twice quickly must not let the FIRST answer land second.
    return () => { live = false }
  }, [period])

  const cases = useMemo(() => data?.cases || [], [data])
  const s = data?.summary || {}
  const labels = data?.class_labels || []
  const firstLabel = labels[0] || 'first activation'
  const secondLabel = labels[labels.length - 1] || 'second activation'
  const keyOf = (c: Case) => `${c.store}|${c.day}|${c.number}`
  const open = useMemo(() => cases.find(c => keyOf(c) === openKey) || null, [cases, openKey])

  // ONE column list, handed to ReportShell, which uses it for the table, every filter, the Group-by
  // menu, all three exports AND the mailed file — so what a manager filters is what they send.
  // `role` is set explicitly rather than left to the header-text sniffing, so the quick filters bind
  // to the columns meant for them.
  const cols: ExportColumn[] = useMemo(() => [
    { header: 'Date', field: 'day', get: (c: Case) => c.day, type: 'date', role: 'date',
      tip: 'The day both activations were rung. The pair is only ever looked for inside one day at one store.' },
    { header: 'Store', field: 'store', get: (c: Case) => c.store, role: 'store',
      tip: 'The store as the sales feed spells it.' },
    { header: 'Phone number', field: 'number', get: (c: Case) => c.number,
      tip: 'The subscriber number both activations were recorded against. Sales whose lines carry no number cannot be grouped at all and are counted separately below.' },
    { header: 'Activations', field: 'events', get: (c: Case) => c.events, type: 'number',
      align: 'right',
      tip: 'How many separate activations were rung on this number that day. Two is the pair; more than two is worth opening first.' },
    { header: 'Receipts', field: 'receipt_count', get: (c: Case) => c.receipt_count, type: 'number',
      align: 'right',
      tip: 'Always two or more: two activations rung on ONE receipt for one number are a single activation, so a pair is on separate receipts by construction.' },
    { header: `Handsets · ${firstLabel}`, field: 'first_handsets',
      get: (c: Case) => num(c.first_handsets), align: 'right',
      tip: 'Device lines on the first activation, counted in the company’s own box departments. "not measured" means no department has been declared, or the declared ones match nothing in this feed.' },
    { header: `Handsets · ${secondLabel}`, field: 'second_handsets',
      get: (c: Case) => num(c.second_handsets), align: 'right',
      tip: 'Device lines on the second activation. None on the first and one here is the shape every measured case carries.' },
    { header: 'Device handed over', field: 'device',
      get: (c: Case) => (c.second_devices || []).join(' / ') || (c.first_devices || []).join(' / '),
      tip: 'The device sold on the activation that carried one.' },
    { header: 'Sold for', field: 'dollars', get: (c: Case) => c.dollars, money: true,
      align: 'right', tip: 'Every line of both sales added up.' },
    { header: 'Who rang it', field: 'reps', get: (c: Case) => (c.reps || []).join(' / '),
      role: 'rep', tip: 'The salesperson on the lines. More than one name means the two sales were rung by different people.' },
    { header: 'What was observed', field: 'signal', get: (c: Case) => (c.signal || []).join('; '),
      tip: 'What the check saw, not what it means. Nothing here is a conclusion about the sale.' },
    { header: 'What to check', field: 'coaching_note', get: (c: Case) => c.coaching_note,
      tip: 'The check to perform before the return window closes.' },
  ], [firstLabel, secondLabel])

  const measuredNothing = s.events !== undefined && s.events > 0
    && (s.numbered_share ?? 1) < 0.5
  const boxUnmatched = s.box_vocabulary_matched === false

  return (
    <div>
      <PageIntro
        title="Brought device then upgrade, same number, same day"
        right={<Link href="/watchdog" style={{ fontSize: 13, color: 'var(--blue, var(--c-fg-2563eb))' }}>
          ← Management Watchdog
        </Link>}
        help={
          <>
            Phone lines that took a {firstLabel.toLowerCase()} activation <em>and</em> an{' '}
            {secondLabel.toLowerCase()} at the same store on the same day. Open a row to see both
            receipts line by line — who rang each one, what was handed over and for how much. These
            are sales to check, not conclusions: someone who activates a line on a phone they brought
            in and then buys a handset the same day looks exactly like this.
          </>
        }
      />

      {err && (
        <div className="card" style={{ border: '1px solid var(--red, var(--c-bg-dc2626))',
                                       padding: '10px 12px', marginBottom: 14, fontSize: 13 }}>
          The report could not be loaded: {err}
        </div>
      )}

      {loading && !data && <p style={{ color: 'var(--text2)', fontSize: 13 }}>Loading…</p>}

      {data && data.enabled === false && (
        <p style={{ fontSize: 13, color: 'var(--text2)' }}>
          This check is switched off for your company, so nothing is being looked for — which is not
          the same as nothing to find. Turn it on from the Watchdog rules and the days appear here.
        </p>
      )}

      {data && data.enabled !== false && data.has_feed === false && (
        <p style={{ fontSize: 13, color: 'var(--text2)' }}>
          No sales were received for {data.period || period}, so there is nothing to report — which
          is not the same as nothing to find.
        </p>
      )}

      {data && data.enabled !== false && data.has_feed !== false && (
        <>
          {/* THE GAP THAT MATTERS MOST, said before any number. One live tenant's feed carries no
              phone number on 94% of its activations, so for it a count of 0 means nothing was
              looked at — and that must not render as a clean month. */}
          {measuredNothing && (
            <div className="card" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 13,
                                           background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)' }}>
              <b>⚠︎ Only {pct(s.numbered_share)} of this period&rsquo;s activations could be matched
              to a phone line.</b> {s.events_without_number} of {s.events} carry no subscriber number
              on their own lines, so they cannot be grouped to a number and this check could not look
              at them. A low count below is mostly <em>not measured</em>, not <em>clean</em>.
            </div>
          )}
          {boxUnmatched && (
            <div className="card" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 13,
                                           background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)' }}>
              <b>⚠︎ None of your declared device departments appear in this period&rsquo;s sales.</b>{' '}
              The handset columns are therefore unreliable rather than zero. The departments being
              looked for are {(data.box_departments || []).join(', ') || 'none'} — correct them on the
              Sales settings screen and the handset figures become meaningful.
            </div>
          )}

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
            <StatTile label="Numbers to check" value={s.cases ?? 0} accent="var(--amber)"
                      sub={`both kinds of activation in one day at one store`} />
            <StatTile label="Nothing handed over, then a handset"
                      value={num(s.cases_upgrade_device_only)} accent="var(--red)"
                      sub="the shape worth opening first" />
            <StatTile label="More than two activations" value={s.multi_event_cases ?? 0}
                      sub="on the same number, the same day" />
            <StatTile label="Value of the flagged sales" value={fmt(s.dollars ?? 0)}
                      sub="every line of both sales" />
            <StatTile label="Activations looked at" value={s.events_with_number ?? 0}
                      sub={`of ${s.events ?? 0} in the period · ${pct(s.numbered_share)} matched to a line`} />
            <StatTile label="No number on the sale" value={s.events_without_number ?? 0}
                      sub="cannot be grouped to a line" />
          </div>

          {/* The rows behind the selected number: both receipts, line by line (§CORE-17 — a figure
              opens to what it is made of, naming the feed each row came from). */}
          {open && (
            <div className="card" style={{ padding: 14, marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
                <strong style={{ fontSize: 14 }}>{open.number} · {open.store} · {open.day}</strong>
                <button onClick={() => setOpenKey('')}
                        style={{ marginLeft: 'auto', fontSize: 12, background: 'none',
                                 border: '1px solid var(--border)', borderRadius: 6,
                                 padding: '2px 8px', cursor: 'pointer', color: 'var(--text2)' }}>
                  Close
                </button>
              </div>
              <p style={{ margin: '0 0 6px', fontSize: 13 }}>{open.description}</p>
              <p style={{ margin: '0 0 12px', fontSize: 12.5, color: 'var(--text2)' }}>
                {open.coaching_note}
              </p>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--text2)', fontSize: 11 }}>
                    <th style={{ padding: '5px 8px' }}>Activation</th>
                    <th style={{ padding: '5px 8px' }}>Receipt</th>
                    <th style={{ padding: '5px 8px' }}>Who rang it</th>
                    <th style={{ padding: '5px 8px' }}>Item</th>
                    <th style={{ padding: '5px 8px' }}>Department</th>
                    <th style={{ padding: '5px 8px' }}>Serial</th>
                    <th style={{ padding: '5px 8px', textAlign: 'right' }}>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {(open.lines || []).map((l, i) => (
                    <tr key={i}>
                      <td style={cell}>{l.class_label || l.cls}</td>
                      <td style={cell}>{l.receipt || '—'}</td>
                      <td style={cell}>{l.rep || 'nobody named'}</td>
                      <td style={cell}>{l.item || '—'}</td>
                      <td style={cell}>{l.department || '—'}</td>
                      <td style={cell}>{l.serial || '—'}</td>
                      <td style={{ ...cell, textAlign: 'right' }}>{fmt(l.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p style={{ margin: '10px 0 0', fontSize: 12, color: 'var(--text3)' }}>
                Every line above came from the {data.sales_table === 'raw_sales'
                  ? 'month-end sales archive' : 'daily sales feed'}.
              </p>
            </div>
          )}

          {cases.length === 0 ? (
            <p style={{ fontSize: 13, color: 'var(--text2)' }}>
              No number took both a {firstLabel.toLowerCase()} and an {secondLabel.toLowerCase()} at
              one store on one day in {data.period || period}.{' '}
              {measuredNothing
                ? 'But most of this period’s activations carry no phone number, so this is mostly not measured rather than clean.'
                : `${s.events_with_number ?? 0} activations were matched to a phone line and looked at, so this is a measured clean period.`}
            </p>
          ) : (
            <ReportShell
              title="Brought device then upgrade, same number, same day"
              subtitle={`${cases.length} number${cases.length === 1 ? '' : 's'} · ${data.period || period} · click a row for both receipts`}
              notice={
                <>These are sales to <b>check</b>, not conclusions. Someone who activates a line on a
                phone they brought in and then buys a handset the same day appears here too. Filter,
                group and send this list with the controls above; the file carries exactly the rows
                left after your filters.</>
              }
              noticeTone="info"
              filename={`byod-then-upgrade-${(data.period || period || '').replace(/\s+/g, '-')}`}
              columns={cols}
              rows={cases}
              onRowClick={(c: Case) => setOpenKey(k => (k === keyOf(c) ? '' : keyOf(c)))}
              stickyHeader
              defaultGroupBy="Store"
              collapsibleGroups
              groupPersistKey="byod-upgrade:groupBy"
            />
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

const cell: React.CSSProperties = {
  padding: '5px 8px',
  borderTop: '1px solid var(--border, var(--c-bg-e5e7eb))',
}
