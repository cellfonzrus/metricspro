'use client'
// A BROUGHT DEVICE AND AN UPGRADE ON ONE NUMBER IN ONE DAY (owner ask 2026-10-28, index
// §COMMCALC-43).
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
import DrillDownPanel, { useDrillTile, type DrillPanel } from '@/components/DrillDownPanel'

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
  // The one-line summary the owner asked for (2026-10-28), plus the carrier money behind it.
  rep: string | null; phone_sold: string | null; meid: string | null
  meids: string[]; phones_sold: number | null
  chargeback_amount: number | null; chargeback_measured: boolean
  chargeback_phones_measured: number; chargeback_partial: boolean
  second_dollars: number
}
type Summary = {
  events?: number; events_with_number?: number; events_without_number?: number
  numbered_share?: number | null; number_days?: number
  handset_lines?: number | null; box_vocabulary_matched?: boolean | null
  cases?: number; multi_event_cases?: number; zero_dollar_cases?: number; dollars?: number
  cases_upgrade_device_only?: number | null
  chargeback_dollars?: number
  chargeback_measured_cases?: number; chargeback_unmeasured_cases?: number
  cannot_answer?: string[]
}
type Payload = {
  period?: string; enabled?: boolean; classes?: string[]; class_labels?: string[]
  box_departments?: string[]; sales_table?: string; has_feed?: boolean
  summary?: Summary; cases?: Case[]
  panels?: Record<string, DrillPanel>
  chargeback_source?: { available?: boolean; reason?: string; configured?: boolean
                        components?: string[]; lag_months?: number | null
                        lag_window?: string[]; feed_table?: string; feed_rows?: number }
}

// A measured zero and an unmeasured signal must never render the same string.
const num = (v: number | null | undefined) =>
  v === null || v === undefined ? 'not measured' : v
const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? 'not measured' : `${(v * 100).toFixed(1)}%`
// A rebate lands months after the sale, so "nothing has arrived yet" and "$0.00 was paid" are
// DIFFERENT facts and must never render as the same string. `fmt(0)` would state the second.
const money = (v: number | null | undefined) =>
  v === null || v === undefined ? 'not yet' : fmt(v)

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

  // THE TILE DRILL-DOWN, on the house mechanism (§CORE-17). `dl` keeps only the figures the SERVER
  // served a panel for, so a feed that never resolved for this range shows no link rather than a
  // link that opens an empty panel. The tiles print the server's own fold and do NOT move when the
  // table below is filtered in the browser, so the panel is served whole and says so (§CORE-17.3o)
  // — `lib/drill-narrow` must not appear on this page.
  const drill = useDrillTile(data?.panels || {})
  const cb = data?.chargeback_source || {}

  // ONE column list, handed to ReportShell, which uses it for the table, every filter, the Group-by
  // menu, all three exports AND the mailed file — so what a manager filters is what they send.
  // `role` is set explicitly rather than left to the header-text sniffing, so the quick filters bind
  // to the columns meant for them.
  // ── THE ONE-LINE SUMMARY (owner ask 2026-10-28) ────────────────────────────────────────────────
  // Owner, verbatim: *"make it one line summary top show the sales rep the store the phone sold and
  // the phone number meid and the $ amount and then drill down like other menus to get more details
  // of both transactions"*. So the table carries a PERSON, a PLACE, a PHONE and a FIGURE, in that
  // order, and everything the screen used to print beside them — the activation and receipt counts,
  // the handset counts per side, what the receipts rang, what was observed, what to check — moved
  // into the row's own drill-down below. Nothing was deleted; it is one click away instead of
  // eleven columns wide.
  //
  // Still ONE column list, handed to ReportShell, which uses it for the table, every filter, the
  // Group-by menu, all three exports AND the mailed file — so what a manager filters is what they
  // send. `role` is set explicitly rather than left to header-text sniffing, so the quick filters
  // bind to the columns meant for them.
  const cols: ExportColumn[] = useMemo(() => [
    { header: 'Date', field: 'day', get: (c: Case) => c.day, type: 'date', role: 'date',
      tip: 'The day both activations were rung. The pair is only ever looked for inside one day at one store.' },
    { header: 'Sales rep', field: 'rep', get: (c: Case) => c.rep || 'nobody named', role: 'rep',
      tip: 'The salesperson on the lines of both sales. Two names means the two sales were rung by different people, which is itself worth seeing.' },
    { header: 'Store', field: 'store', get: (c: Case) => c.store, role: 'store',
      tip: 'The store as the sales feed spells it.' },
    { header: 'Phone sold', field: 'phone_sold',
      get: (c: Case) => c.phone_sold || (c.phones_sold === null ? 'not measured' : 'none identified'),
      tip: 'The handset handed over on the second activation — the one the store sold, not the phone the customer brought in. "not measured" means no device department has been declared, or the declared ones match nothing in this feed.' },
    { header: 'Phone number', field: 'number', get: (c: Case) => c.number,
      tip: 'The subscriber number both activations were recorded against. Sales whose lines carry no number cannot be grouped at all and are counted separately below.' },
    { header: 'MEID', field: 'meid', get: (c: Case) => c.meid || '—',
      tip: 'The device identifier the sale carried for the handset sold. It is the key the carrier pays against, which is how the chargeback amount beside it is found.' },
    { header: 'Chargeback amount', field: 'chargeback_amount',
      get: (c: Case) => money(c.chargeback_amount), align: 'right',
      tip: 'The carrier money already recorded against that handset — what a chargeback on this sale would take back. It is not a charge anybody has made. "not yet" means no payment has arrived for this phone; a rebate usually lands months after the sale, so recent days are mostly not-yet rather than nothing at stake.' },
  ], [])

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
            <StatTile label="Chargeback amount" value={fmt(s.chargeback_dollars ?? 0)}
                      accent="var(--red)"
                      sub={`carrier money against ${s.chargeback_measured_cases ?? 0} of ${s.cases ?? 0} handsets`} />
            <StatTile label="No carrier payment yet"
                      value={s.chargeback_unmeasured_cases ?? 0}
                      sub="a rebate usually lands months later" />
            <StatTile label="Value of the flagged sales" value={fmt(s.dollars ?? 0)}
                      sub="every line of both sales" />
            <StatTile label="Activations looked at" value={s.events_with_number ?? 0}
                      sub={`of ${s.events ?? 0} in the period · ${pct(s.numbered_share)} matched to a line`} />
            <StatTile label="No number on the sale" value={s.events_without_number ?? 0}
                      sub="cannot be grouped to a line" />
          </div>

          {/* WHERE DID THAT FIGURE COME FROM — the tile panels, on the shared mechanism. */}
          {drill.dl('chargeback_dollars', 'Chargeback amount', 'dollars', 'Value of the flagged sales').length > 0 && (
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12,
                          fontSize: 12.5 }}>
              <span style={{ color: 'var(--text3)' }}>Where a figure came from:</span>
              {drill.dl('chargeback_dollars', 'Chargeback amount',
                        'dollars', 'Value of the flagged sales').map(d => (
                <button key={d.key} onClick={() => drill.toggle(d.key)}
                        style={{ fontSize: 12.5, background: 'none', cursor: 'pointer',
                                 border: '1px solid var(--border)', borderRadius: 6,
                                 padding: '2px 8px',
                                 color: drill.isOpen(d.key) ? 'var(--text)' : 'var(--text2)' }}>
                  {drill.isOpen(d.key) ? '▾ ' : '▸ '}{d.label}
                </button>
              ))}
            </div>
          )}
          {drill.open && (
            <div style={{ marginBottom: 16 }}>
              <DrillDownPanel panel={data?.panels?.[drill.open]} />
            </div>
          )}

          {/* THE CARRIER FEED THE CHARGEBACK AMOUNT WAS READ FROM, stated rather than assumed. A
              feed that could not be read leaves every amount "not yet", and a reader has to be able
              to tell that from a carrier that has genuinely paid nothing. */}
          {cb.available === false && (
            <div className="card" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 13,
                                           background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)' }}>
              <b>⚠︎ No chargeback amount could be looked up for this period.</b>{' '}
              {cb.reason || 'The carrier payment feed was not read.'} Every amount below therefore
              reads &ldquo;not yet&rdquo; because nothing was looked at, not because the carrier has
              paid nothing.
            </div>
          )}
          {cb.available !== false && cb.configured === false && (
            <div className="card" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 13,
                                           background: 'var(--c-bg-fef3c7)', color: 'var(--c-fg-92400e)' }}>
              <b>⚠︎ Your company has not declared which carrier payments are device money.</b>{' '}
              Until it does, no payment can be called a device rebate, so every chargeback amount
              below is &ldquo;not yet&rdquo; rather than a figure. The same declaration is what the
              P&amp;L books its equipment-reimbursement line on.
            </div>
          )}

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

              {/* EVERYTHING THE TABLE USED TO PRINT BESIDE THE ROW. The owner's one-line summary
                  removed eleven columns from the list above; none of those facts was deleted, they
                  are stated here per row instead. A fact that is NOT MEASURED says so in its own
                  words — a blank or a 0 beside "handsets" would read as a sale with no phone on it,
                  which is the opposite of an unmeasured one. */}
              <dl style={facts}>
                <div><dt style={dtS}>Activations</dt><dd style={ddS}>
                  {open.events} on this number, this day · {open.class_labels.join(' then ')}
                </dd></div>
                <div><dt style={dtS}>Receipts</dt><dd style={ddS}>
                  {open.receipt_count} ({open.receipts.join(', ') || '—'}){' '}
                  {open.separate_receipts ? '· rung separately' : '· on one receipt'}
                </dd></div>
                <div><dt style={dtS}>{firstLabel}</dt><dd style={ddS}>
                  {num(open.first_handsets)} handset{open.first_handsets === 1 ? '' : 's'}
                  {open.first_devices && open.first_devices.length > 0
                    ? ` · ${open.first_devices.join(', ')}` : ''}
                  {open.first_receipts.length > 0 ? ` · receipt ${open.first_receipts.join(', ')}` : ''}
                </dd></div>
                <div><dt style={dtS}>{secondLabel}</dt><dd style={ddS}>
                  {num(open.second_handsets)} handset{open.second_handsets === 1 ? '' : 's'}
                  {open.second_devices && open.second_devices.length > 0
                    ? ` · ${open.second_devices.join(', ')}` : ''}
                  {open.second_receipts.length > 0 ? ` · receipt ${open.second_receipts.join(', ')}` : ''}
                  {' · '}{fmt(open.second_dollars)}
                </dd></div>
                <div><dt style={dtS}>Who rang it</dt><dd style={ddS}>
                  {open.rep || 'nobody named'}
                </dd></div>
                <div><dt style={dtS}>Rung for</dt><dd style={ddS}>
                  {fmt(open.dollars)} across every line of both sales
                  {open.all_zero_dollars ? ' · every line rang $0.00' : ''}
                </dd></div>
                {/* THE CHARGEBACK AMOUNT, AND WHAT IT RESTS ON. Three states, three sentences: a
                    figure, a figure that covers only some of the handsets sold, and no payment
                    arrived yet. The third is the common one on a recent day and it is not a zero. */}
                <div><dt style={dtS}>Chargeback amount</dt><dd style={ddS}>
                  {open.chargeback_measured ? (
                    <>
                      <b>{money(open.chargeback_amount)}</b> of carrier money is already recorded
                      against {open.chargeback_phones_measured} of{' '}
                      {open.meids.length || 1} handset
                      {(open.meids.length || 1) === 1 ? '' : 's'} on this sale
                      {open.meids.length > 0 ? ` (${open.meids.join(', ')})` : ''}.
                      {open.chargeback_partial
                        ? ' No payment has arrived for the other handset, so this is part of the exposure, not all of it.'
                        : ''}
                      {' '}It is what a chargeback here would take back, not a charge anybody has made.
                    </>
                  ) : (
                    <>No carrier payment has arrived against{' '}
                      {open.meid ? `this handset (${open.meid})` : 'the handset on this sale'} yet.
                      A rebate usually lands months after the sale, so this is <em>not yet</em>
                      {' '}rather than nothing at stake
                      {cb.available === false ? ', and the carrier feed could not be read at all' : ''}
                      {cb.available !== false && cb.configured === false
                        ? ', and your company has not declared which carrier payments are device money' : ''}.
                    </>
                  )}
                </dd></div>
                {open.signal.length > 0 && (
                  <div><dt style={dtS}>What stands out</dt><dd style={ddS}>
                    {open.signal.join(' · ')}
                  </dd></div>
                )}
              </dl>
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

const facts: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
  gap: '8px 18px',
  margin: '0 0 14px',
  fontSize: 12.5,
}
const dtS: React.CSSProperties = {
  color: 'var(--text3)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.3,
}
const ddS: React.CSSProperties = { margin: '2px 0 0', color: 'var(--text)' }

const cell: React.CSSProperties = {
  padding: '5px 8px',
  borderTop: '1px solid var(--border, var(--c-bg-e5e7eb))',
}
