'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { useSearchParams } from 'next/navigation'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MultiSelect } from '@/lib/multiselect'

// EQUIPMENT REIMBURSEMENT PER LINE — owner ask 2026-10-09: *"i was checking the p&l for 652 , the
// equipment rebate is almost 5000 less than the equipment reimbursement, we need to check what is
// going on and also create another report for the equipment reimbursement per line , cost per line,
// and device payment charged in the store to asses which line items dod not get paid"*.
//
// The backend does ALL of the math and all of the honesty (see commcalc/device_reimb_recon.py, the
// device-grain layer of the SAME one home the store-month reconciliation lives in). This page renders
// and never computes — in particular it never fills a blank with a 0: a device with no recorded sale
// price shows an em dash, because "the ledger records no price" is not "sold for $0.00".

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
function thisMonth() { return new Date().toISOString().slice(0, 7) }

// A figure the backend could not answer comes back null. Em dash, never 0.
function money(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : fmt(v)
}

const STATUS_STYLE: Record<string, { bg: string; fg: string; icon: string; title: string }> = {
  paid: { bg: 'var(--surface2)', fg: 'var(--text2)', icon: '✓', title: 'Paid to this store' },
  paid_to_other_store: { bg: '#fee2e2', fg: '#991b1b', icon: '⇄', title: 'Paid to a different store' },
  not_paid: { bg: '#fef3c7', fg: '#92400e', icon: '⚑', title: 'Not paid anywhere' },
  not_measured: { bg: '#e0e7ff', fg: '#3730a3', icon: '◻︎', title: 'Not measured' },
}
const STATUS_ORDER = ['paid_to_other_store', 'not_paid', 'not_measured', 'paid']

export default function DeviceLineReimbursementPage() {
  // ?view=transferred opens on the phones a DIFFERENT store was paid for — the owner asked for that
  // set as its own report. It is the same derivation, deep-linked, not a second one: a sibling page
  // reading the same two feeds would be the duplicate defect the house rules forbid, and the two
  // would disagree the first time the lag window or a verdict changed.
  const qp = useSearchParams()
  const transferredView = qp.get('view') === 'transferred'
  const [period, setPeriod] = useState(thisMonth())
  const [store, setStore] = useState('')
  const [statuses, setStatuses] = useState<string[]>(transferredView ? ['paid_to_other_store'] : [])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams({ period })
    if (store) qs.set('store', store)
    api(`/api/v1/commcalc/device-line-reimbursement?${qs.toString()}${orgParam()}`)
      .then(setData)
      .catch(e => setData({ error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period, store])
  useEffect(() => { load() }, [load])

  const allRows: any[] = data?.rows || []
  const totals: any = data?.totals || {}
  const byStore: any[] = data?.by_store || []
  const rows = useMemo(() => (statuses.length ? allRows.filter(r => statuses.includes(r.status)) : allRows), [allRows, statuses])
  const storeOpts = useMemo(() => byStore.map(s => s.store).filter(Boolean), [byStore])

  const cols = useMemo<ExportColumn[]>(() => ([
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Verdict', get: r => (STATUS_STYLE[r.status]?.title || r.status) },
    { header: 'Device', get: r => r.model || '—' },
    { header: 'Device ID', get: r => r.device_id || '—' },
    { header: 'Sold by', get: r => r.sold_by || '—', role: 'rep' },
    { header: 'Sold at', get: r => r.sold_by_store || '—' },
    { header: 'Reimbursement claimed', get: r => r.distributor_claimed, money: true },
    { header: 'Carrier paid, this store', get: r => r.carrier_paid_here, money: true },
    { header: 'Carrier paid, other store', get: r => r.carrier_paid_other_total, money: true },
    { header: 'Which other store', get: r => (r.carrier_paid_other_stores || []).map((o: any) => `${o.store} ${fmt(o.amount)}`).join('; ') || '—' },
    { header: 'Device cost', get: r => r.device_cost, money: true },
    { header: 'Charged in store', get: r => (r.store_payment_recorded ? r.store_device_payment : '—'), money: true },
    { header: 'Paid in', get: r => r.carrier_paid_here_month_label || '—' },
    { header: 'Late', get: r => (r.carrier_paid_after_claim_month ? 'yes' : '') },
    { header: 'Sold', get: r => r.sold || '—', type: 'date' },
    { header: 'Reimbursement date', get: r => r.reimbursement_date || '—', type: 'date' },
    { header: 'Acquired', get: r => r.acquired || '—', type: 'date' },
    { header: 'Why not measured', get: r => r.reason_label || '' },
  ]), [])

  const Tile = ({ label, value, sub, warn }: { label: string; value: string; sub?: string; warn?: boolean }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 190 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 3, color: warn ? '#991b1b' : 'var(--text1)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )

  const counts = totals.by_status || {}

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>
          📱 {transferredView ? 'Phones Activated at Another Store' : 'Equipment Reimbursement per Line'}
        </h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          {transferredView
            ? <>Every phone whose cost sits on one store while the carrier paid <b>another</b> store for
                it — with <b>who sold it</b>, the <b>selling price</b>, the <b>cost</b> and the{' '}
                <b>reimbursement</b> on the same row. The distributor books the claim to the store the
                device was stocked to; the carrier pays the store it was activated at. Clear the verdict
                filter to see every device.</>
            : <>One row per device the distributor claims a reimbursement for: <b>what it claimed</b>,{' '}
                <b>what the device cost</b>, <b>what the store charged for it</b>, <b>who sold it</b>,
                and <b>what the carrier actually paid</b> — at this store and at any other. A device the
                carrier paid a <b>different</b> store for is its own verdict and never counted as
                unpaid: the distributor books the claim to the store the device was stocked to, and the
                carrier pays the store it was activated at.</>}
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Month{' '}
          <input type="month" style={sel} value={period.length === 7 ? period : thisMonth()} onChange={e => setPeriod(e.target.value)} />
        </label>
        {storeOpts.length > 0 &&
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Store{' '}
            <select style={{ ...sel, maxWidth: 280 }} value={store} onChange={e => setStore(e.target.value)}>
              <option value="">All stores</option>
              {storeOpts.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>}
        <MultiSelect allLabel="All verdicts" width={210} value={statuses}
                     options={STATUS_ORDER.filter(s => counts[s])
                       .map(s => ({ value: s, label: `${STATUS_STYLE[s].icon} ${STATUS_STYLE[s].title} (${counts[s]})` }))}
                     onChange={setStatuses} />
        {(store !== '' || statuses.length > 0) &&
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setStore(''); setStatuses([]) }}>Clear</button>}
      </div>

      {data?.error &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: '#fee2e2', color: '#991b1b', fontSize: 13 }}>
          <b>❌ This report could not be built.</b> {data.error}
        </div>}

      {/* THE ABSENCE THAT MAKES THE WHOLE REPORT UNANSWERABLE, said before any number is shown. */}
      {!loading && !data?.error && data?.configured === false &&
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: '#e0e7ff', color: '#3730a3', fontSize: 13 }}>
          <b>◻︎ Nothing is declared as device-financing money</b>, so no device can be said to have been
          paid or not paid. Declare which carrier component books on its own P&L line and this report
          fills in. Until then every row reads <i>not measured</i> — never $0.00.
        </div>}

      {!loading && !data?.error && allRows.length > 0 &&
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <Tile label="Reimbursement claimed" value={fmt(totals.distributor_claimed || 0)}
                sub={`${(totals.devices || 0).toLocaleString()} devices · cost ${fmt(totals.device_cost || 0)}`} />
          <Tile label="Carrier paid this store" value={fmt(totals.carrier_paid_here || 0)}
                sub={`${(counts.paid || 0).toLocaleString()} devices`} />
          <Tile label="Paid to a DIFFERENT store" value={fmt(totals.carrier_paid_other_total || 0)}
                sub={`${(counts.paid_to_other_store || 0).toLocaleString()} devices — this store carries the cost`}
                warn={!!totals.carrier_paid_other_total} />
          <Tile label="Not paid anywhere" value={fmt(totals.not_paid_total || 0)}
                sub={`${(counts.not_paid || 0).toLocaleString()} devices, on a complete month`}
                warn={!!totals.not_paid_total} />
          <Tile label="Could not be measured" value={fmt(totals.not_measured_total || 0)}
                sub={`${(counts.not_measured || 0).toLocaleString()} devices — reason on the row`} />
          <Tile label="Charged in store" value={fmt(totals.store_device_payment || 0)}
                sub={`${(totals.store_payment_not_recorded || 0).toLocaleString()} of ${(totals.devices || 0).toLocaleString()} devices record no price`} />
        </div>}

      {/* Device-financing money the carrier paid with no device on the line — reported so the paid
          side is never quietly understated by what could not be placed on a device. */}
      {!loading && !data?.error && (totals.carrier_paid_unidentified || 0) > 0 &&
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, background: '#fef3c7', color: '#92400e', fontSize: 13 }}>
          <b>⚠︎ {fmt(totals.carrier_paid_unidentified)}</b> of device-financing money on{' '}
          {totals.carrier_paid_unidentified_rows} carrier line{totals.carrier_paid_unidentified_rows === 1 ? '' : 's'}{' '}
          carries no device identifier, so it could not be matched to any device. It is not in the
          per-store figures above.
        </div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.error && allRows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          The distributor claims no device reimbursement in this month.
        </div>
      ) : !data?.error ? (
        <ReportShell
          title={`Equipment Reimbursement per Line — ${data?.period || period}`}
          subtitle={`${rows.length} device${rows.length === 1 ? '' : 's'}${statuses.length ? ' · filtered' : ''} · carrier payments read over ${(data?.lag_window || []).length} month(s) either side of the claim`}
          filename={`device-line-reimbursement-${data?.period || period}`}
          columns={cols}
          rows={rows}
          totals
          stickyHeader
          defaultGroupBy="Verdict"
          collapsibleGroups
          groupPersistKey="device-line-reimbursement:groupBy"
        />
      ) : null}

      {!loading && !data?.error && allRows.length > 0 &&
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
          ℹ︎ The claim side is the distributor ledger, bucketed by the month it says it reimbursed. The
          paid side is the carrier’s own per-line feed, classified exactly as the P&amp;L classifies it,
          read over {(data?.lag_months ?? 1)} month(s) either side of the claim — the distributor settles
          after the carrier pays as often as before it, so a one-sided window would call a paid device
          unpaid. A device with nothing paid anywhere in a month whose feed arrived <b>short</b> is
          reported <i>not measured</i> with the missing days named, never as “never paid”.
        </div>}
    </div>
  )
}
