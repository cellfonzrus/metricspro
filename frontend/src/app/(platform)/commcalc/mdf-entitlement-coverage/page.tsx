'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MultiSelect } from '@/lib/multiselect'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'

// WHERE EACH OF THESE TILES CAME FROM (index §CORE-17.3v). The server serves a panel per figure it
// can stand behind, from commcalc/entitlement_coverage_drilldown.py, folded over the SAME
// store-month rows the table below prints. THREE grains on one row: the verdict COUNTS, the money
// that ARRIVED, and what an ABSENT month is worth — the last two in the same unit on the same row,
// separated only by where the number came from, because a gap is priced from an amount somebody
// DECLARED (§CORE-17.3m / §CORE-17.3n).
//
// THIS PAGE IS SERVER-FOLDED AND IS DELIBERATELY *NOT* NARROWED. Every tile reads `data.totals` as
// served and this page folds nothing; its store picker is a query parameter the server filters on
// before it folds, and the verdict tick list narrows the TABLE and touches no tile. A panel explains
// the FIGURE, not the table (§CORE-17.3o), so narrowing here would make every panel disagree with
// the tile it was opened from. `lib/drill-narrow` must not appear in this file;
// `harness_batch22_drilldown.py` §G fails the build in BOTH directions if that changes.
//
// WHAT GETS NO LINK, AND WHY. The store counts — "across N stores" and "N stores have no declared
// first entitled month" — are cardinalities of a STORE set, not sums over the store-month rows this
// report folds, so a panel for one would be a row list at a different grain from the figure it
// claimed to explain. The report's own verdict sentence is a verdict, not a figure.
const COVERAGE_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Verdict', get: (r: DrillRow) => String(r.fields?.verdict ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.verdict ?? '') },
  { header: 'Month of term', get: (r: DrillRow) => String(r.fields?.['month of term'] ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.['month of term'] ?? '') },
  { header: 'Monthly amount', get: (r: DrillRow) => String(r.fields?.['monthly amount'] ?? '—'),
    sortValue: (r: DrillRow) => Number(r.fields?.['monthly amount'] ?? 0) },
  { header: 'Amount basis', get: (r: DrillRow) => String(r.fields?.['amount basis'] ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.['amount basis'] ?? '') },
]

// SUBSIDY MONTHS MISSING — owner instruction 2026-10-10: *"mdf / ramp up subsidy is being finsihed it
// is inly for 12 months, if we did not get for 12 months for each store it should be flagged and the
// missing month and store to be poimted out , make it into management overview"*.
//
// The backend does ALL of the judging (commcalc/entitlement_coverage.py, index §68). This page renders
// and never computes a verdict, a window or a month of its own — in particular:
//   · it never fills a blank with a 0: a gap nothing can price shows an em dash;
//   · it keeps "this store did not get its month" and "no payment reached us for ANY store that
//     month" in SEPARATE columns and separate tiles, because only the first is the carrier's doing;
//   · a store whose first entitled month is not declared is shown as NOT MEASURED with the reason,
//     never as a store missing its whole term.

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

// A figure the backend could not answer comes back null. Em dash, never 0.
function money(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : fmt(v)
}
function monthLabel(m: string | null | undefined): string {
  if (!m || m.length < 7) return '—'
  const d = new Date(`${m}-01T00:00:00`)
  return isNaN(d.getTime()) ? m : d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
}

const STATUS_STYLE: Record<string, { bg: string; fg: string; icon: string; title: string }> = {
  missing: { bg: 'var(--c-bg-fee2e2)', fg: 'var(--c-fg-991b1b)', icon: '⚑', title: 'Missing' },
  unconfirmed_missing: { bg: 'var(--c-bg-fef3c7)', fg: 'var(--c-fg-92400e)', icon: '⚠︎', title: 'No payment reached us that month' },
  not_measured: { bg: 'var(--c-bg-e0e7ff)', fg: 'var(--c-fg-3730a3)', icon: '◻︎', title: 'Not measured' },
  not_due: { bg: 'var(--surface2)', fg: 'var(--text3)', icon: '…', title: 'Not yet owed' },
  received: { bg: 'var(--surface2)', fg: 'var(--text2)', icon: '✓', title: 'Received' },
}
const STATUS_ORDER = ['missing', 'unconfirmed_missing', 'not_measured', 'not_due', 'received']

export default function MdfEntitlementCoveragePage() {
  const [store, setStore] = useState('')
  const [statuses, setStatuses] = useState<string[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams()
    if (store) qs.set('store', store)
    api(`/api/v1/commcalc/mdf-entitlement-coverage?${qs.toString()}${orgParam()}`)
      .then(setData)
      .catch(e => setData({ error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [store])
  useEffect(() => { load() }, [load])

  const allRows: any[] = data?.rows || []
  const totals: any = data?.totals || {}
  const byStore: any[] = data?.by_store || []
  const rows = useMemo(() => (statuses.length ? allRows.filter(r => statuses.includes(r.status)) : allRows), [allRows, statuses])
  const storeOpts = useMemo(() => byStore.map(s => s.store).filter(Boolean), [byStore])
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const r of allRows) c[r.status] = (c[r.status] || 0) + 1
    return c
  }, [allRows])

  const cols = useMemo<ExportColumn[]>(() => ([
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Month', get: r => monthLabel(r.month) },
    { header: 'Verdict', get: r => (STATUS_STYLE[r.status]?.title || r.status) },
    { header: 'Month of term', get: r => (r.entitlement_month ? `${r.entitlement_month} of ${r.term_months}` : '—') },
    { header: 'First entitled month', get: r => monthLabel(r.window_start) },
    { header: 'Received', get: r => r.amount, money: true },
    { header: 'Monthly amount', get: r => r.monthly_rate, money: true },
    { header: 'Amount basis', get: r => r.monthly_rate_basis || '—' },
    { header: 'Other stores paid that month', get: r => (r.stores_paid_in_month === null || r.stores_paid_in_month === undefined ? '—' : r.stores_paid_in_month) },
    { header: 'Months received in all', get: r => r.received_months },
    { header: 'Last received', get: r => monthLabel(r.last_received) },
    { header: 'Month state', get: r => r.month_state || '—' },
    { header: 'Why not measured', get: r => r.reason_label || '' },
  ]), [])

  // The server's fold, printed as served. The shared offer gate carries NO extra condition: figure
  // and panel came out of the same fold over the same rows.
  const panels: Record<string, DrillPanel> = data?.drilldown || {}
  const drill = useDrillTile(panels)

  const Tile = ({ label, value, sub, warn, links }:
    { label: string; value: string; sub?: string; warn?: boolean; links?: React.ReactNode }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 200 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 3, color: warn ? 'var(--c-fg-991b1b)' : 'var(--text1)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
      {links}
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📆 Subsidy Months Missing</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          A ramp-up subsidy is a <b>fixed number of months per store</b>. This names any store-month
          inside a store’s own entitlement that <b>never arrived</b> — the store and the month. A month
          in which <b>no</b> payment of this kind reached us for <b>any</b> store is kept in its own
          bucket: that is a month of ours that did not arrive, not the carrier withholding it. A month
          that has not finished yet is not owed and is never reported as missing.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        {storeOpts.length > 0 &&
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Store{' '}
            <select style={{ ...sel, maxWidth: 280 }} value={store} onChange={e => setStore(e.target.value)}>
              <option value="">All stores</option>
              {storeOpts.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>}
        <MultiSelect allLabel="All verdicts" width={280} value={statuses}
                     options={STATUS_ORDER.filter(s => counts[s])
                       .map(s => ({ value: s, label: `${STATUS_STYLE[s].icon} ${STATUS_STYLE[s].title} (${counts[s]})` }))}
                     onChange={setStatuses} />
        {(store !== '' || statuses.length > 0) &&
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setStore(''); setStatuses([]) }}>Clear</button>}
      </div>

      {data?.error &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: 'var(--c-bg-fee2e2)', color: 'var(--c-fg-991b1b)', fontSize: 13 }}>
          <b>❌ This report could not be built.</b> {data.error}
        </div>}

      {/* THE ABSENCE THAT MAKES THE WHOLE REPORT UNANSWERABLE, said before any number is shown. */}
      {!loading && !data?.error && data?.configured === false &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: 'var(--c-bg-e0e7ff)', color: 'var(--c-fg-3730a3)', fontSize: 13 }}>
          <b>◻︎ No entitlement has been declared for this company</b>, so no store-month can be judged
          against one. Declare which payment category is the entitlement and how many months it runs,
          and this report fills in. Until then every store reads <i>not measured</i> — never a gap.
        </div>}

      {/* THE HONEST ANSWER TODAY, and the one thing only the owner can supply. */}
      {!loading && !data?.error && data?.configured !== false && (totals.windows_undeclared || 0) > 0 &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: 'var(--c-bg-e0e7ff)', color: 'var(--c-fg-3730a3)', fontSize: 13 }}>
          <b>◻︎ {totals.windows_undeclared} store{totals.windows_undeclared === 1 ? '' : 's'} have no declared first entitled month.</b>{' '}
          Nothing in the carrier’s feed says when a store’s {data?.term_months || ''}-month entitlement
          began, so a month with no payment cannot be told apart from the entitlement having run out —
          and claiming otherwise would accuse the carrier of something it may not have done. Those
          store-months are listed below as <i>not measured</i> with that reason.
          {(totals.undeclared_absence_store_months || 0) > 0 &&
            <> <b>{totals.undeclared_absence_store_months}</b> of them are months the store had been
              paid in before and then was not, which is what to look at first.</>}
        </div>}

      {!loading && !data?.error && allRows.length > 0 &&
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <Tile label="Missing store-months" value={(totals.missing_store_months || 0).toLocaleString()}
                sub={`${money(totals.missing_amount)}${totals.unpriced_missing_store_months ? ` · ${totals.unpriced_missing_store_months} nothing can price` : ''}`}
                warn={!!totals.missing_store_months}
                links={<DrillTileLinks
                  drills={drill.dl('missing_store_months', 'which store-months',
                                   'missing_amount', 'what they are worth',
                                   'unpriced_missing_store_months', 'the ones nothing can price')}
                  open={drill.open} onToggle={drill.toggle} />} />
          <Tile label="No payment reached us" value={(totals.unconfirmed_missing_store_months || 0).toLocaleString()}
                sub={`${money(totals.unconfirmed_missing_amount)} — our feed, not the carrier. Counted apart.`}
                links={<DrillTileLinks
                  drills={drill.dl('unconfirmed_missing_store_months', 'which store-months',
                                   'unconfirmed_missing_amount', 'what they are worth')}
                  open={drill.open} onToggle={drill.toggle} />} />
          <Tile label="Named, not measured" value={(totals.undeclared_absence_store_months || 0).toLocaleString()}
                sub={`${money(totals.undeclared_absence_amount_at_observed_rate)} at the store’s own monthly amount — not a claim`}
                links={<DrillTileLinks
                  drills={drill.dl('undeclared_absence_store_months', 'which store-months',
                                   'undeclared_absence_amount_at_observed_rate', 'what they would be worth')}
                  open={drill.open} onToggle={drill.toggle} />} />
          {/* NO LINK ON THE STORE COUNT IN THE SUB-LINE: it is a cardinality of a STORE set, not a
              sum over the store-month rows this report folds. */}
          <Tile label="Received" value={(totals.received_store_months || 0).toLocaleString()}
                sub={`${fmt(totals.received_total || 0)} across ${totals.stores || 0} store${totals.stores === 1 ? '' : 's'}`}
                links={<DrillTileLinks
                  drills={drill.dl('received_store_months', 'which store-months',
                                   'received_total', 'the money that arrived')}
                  open={drill.open} onToggle={drill.toggle} />} />
          <Tile label="Still to come" value={(totals.not_due_store_months || 0).toLocaleString()}
                sub={`of ${(totals.store_months_judged || 0).toLocaleString()} store-months in a declared term`}
                links={<DrillTileLinks
                  drills={drill.dl('not_due_store_months', 'which store-months',
                                   'store_months_judged', 'everything in a declared term')}
                  open={drill.open} onToggle={drill.toggle} />} />
        </div>}

      {/* ONE renderer for whichever figure is open — the store-months it was folded from, each
          naming the records it was read out of or saying plainly that there were none. */}
      {drill.panel && <DrillDownPanel panel={drill.panel} extraColumns={COVERAGE_DRILL_COLUMNS} />}

      {/* The report's own verdict, in its own words — never re-worded here. */}
      {!loading && !data?.error && data?.reason &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, fontSize: 13,
                                       background: data.verdict === 'failed' ? '#fee2e2' : data.verdict === 'passed' ? 'var(--surface2)' : '#e0e7ff',
                                       color: data.verdict === 'failed' ? '#991b1b' : data.verdict === 'passed' ? 'var(--text2)' : '#3730a3' }}>
          <b>{data.verdict === 'failed' ? '⚑' : data.verdict === 'passed' ? '✓' : '◻︎'}</b> {data.reason}
        </div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.error && allRows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No payment of a declared entitlement kind has reached us, and no store has a declared
          entitlement window — so there is nothing to judge.
        </div>
      ) : !data?.error ? (
        <ReportShell
          title="Subsidy Months Missing"
          subtitle={`${rows.length} store-month${rows.length === 1 ? '' : 's'}${statuses.length ? ' · filtered' : ''}${data?.term_months ? ` · a ${data.term_months}-month entitlement per store` : ''}`}
          filename="subsidy-months-missing"
          columns={cols}
          rows={rows}
          stickyHeader
          defaultGroupBy="Verdict"
          collapsibleGroups
          groupPersistKey="mdf-entitlement-coverage:groupBy"
        />
      ) : null}

      {!loading && !data?.error && allRows.length > 0 &&
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
          ℹ︎ Both of the carrier’s own records of this payment are read and combined, so a month that
          has landed in one and not yet the other is never reported as an absence. A month is only
          called <b>missing</b> for a store when other stores <i>were</i> paid in it — proof the
          payment run happened and this store was not in it. A month in which nothing of this kind
          reached us at all is reported separately and should be re-pulled before it is read as a gap.
          Which payments count as the entitlement is this company’s own declaration, and the term and
          each store’s first entitled month are declared settings — not guesses made here.
        </div>}
    </div>
  )
}
