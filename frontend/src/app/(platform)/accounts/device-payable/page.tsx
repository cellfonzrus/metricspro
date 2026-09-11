'use client'
// DEVICE PAYABLE AS AT A DATE (owner directive 2026-09-11) — of the devices the distributor had
// already BILLED us on a given day, which ones had not yet been PAID for, by company and by store.
//
// Owner, verbatim: "I need the payable at the end of the year accounts. Payable on 12/31/2025
// company wise" … "we need to check which of the imei a billed in 2025 got paid in 2025 and which
// ones were paid in 2026".
//
// THIS IS A BACKDATED FIGURE, WHICH IS WHY IT IS NOT THE LIABILITIES-DUE PAGE. That page (and the
// Balance Sheet's distributor payable) answer the CURRENT open balance from the ledger's status
// column, and a status is a snapshot — a unit paid in 2026 reads "paid" when you ask about 2025.
// Standing on 31 December needs a per-unit PAYMENT DATE, which is what this report reads.
//
// THREE STATES, NEVER A BARE $0.00. Coverage is derived from the data server-side; an as-at date
// outside it renders the reason and the window instead of a figure. A measured figure renders. A
// measured zero renders as a zero and says it is measured.
//
// Reads ONLY GET /account/device-payable (org-scoped). Store and company come from the platform's
// shared resolvers server-side, so this page cannot disagree with the P&L about where a store sits.
import { useEffect, useMemo, useState } from 'react'
import { api, fmt } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import ReportExportBar from '@/components/ReportExportBar'
import StatTile from '@/components/StatTile'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { ExportSheet } from '@/lib/export'

const th: React.CSSProperties = { textAlign: 'left', padding: '7px 10px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const thr: React.CSSProperties = { ...th, textAlign: 'right' }
const td: React.CSSProperties = { padding: '7px 10px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const tdr: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

// A figure we do not have renders '—'. Never $0.00 for "not measured" — that is the house rule.
const money = (v: any) => (v == null ? '—' : fmt(v))
const num = (v: any) => (v == null ? '—' : Number(v).toLocaleString())
const pct = (v: any) => (v == null ? '—' : `${(Number(v) * 100).toFixed(2)}%`)

const today = () => new Date().toISOString().slice(0, 10)

function Card({ title, note, children }: { title: string; note?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: 14, marginBottom: 14 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>{title}</div>
      {note && <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8 }}>{note}</div>}
      {children}
    </div>
  )
}

export default function DevicePayablePage() {
  // The as-at date is this report's whole question, so it is its own control rather than a row in
  // the shared bar: the bar's period is a month or a RANGE, and a payable is asked for on ONE DAY.
  const [asAt, setAsAt] = useState<string>(today())
  const [filt, setFilt] = useState<StandardFilterValue>(() => emptyStandardFilter())
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  // STALE-RESPONSE GUARD. Without this the response that RESOLVES LAST wins, not the one that was
  // asked for last — so clicking through dates leaves the page showing a payable for a date the
  // picker is no longer on, while the picker reads the new one. That is the worst shape a money bug
  // takes: nothing on screen contradicts it. Not theoretical here — this report reads the whole
  // device, ledger and invoice feeds and takes the better part of a minute, so two in-flight
  // requests is the NORMAL case, not the edge one.
  // Third occurrence of this defect class: Cash/ePay Pickup (2026-09-10) and Device Purchases
  // (2026-09-11, where the owner saw two years of spend under a one-year window). `alive` is the
  // pattern StandardFilterBar itself already uses, and §J pins it.
  useEffect(() => {
    if (!asAt) return
    let alive = true
    setLoading(true); setErr('')
    api(`/api/v1/account/device-payable?as_at=${encodeURIComponent(asAt)}`)
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (!alive) return; setErr(e?.message || String(e)); setData(null) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [asAt])

  const distributor = data?.distributor_label || 'distributor'
  const cov = data?.coverage || null
  const measured = cov?.state === 'measured'
  const T = data?.totals || {}
  const stores: any[] = useMemo(() => data?.by_store || [], [data])

  // Store/market narrowing is client-side over the rows already loaded — what you see is what exports.
  const selStores = useMemo(() => new Set(filt.stores.map(s => s.trim().toUpperCase())), [filt.stores])
  const selMkts = useMemo(() => new Set(filt.markets.map(m => m.trim().toUpperCase())), [filt.markets])
  const keep = (r: any) => {
    if (selStores.size && !selStores.has(String(r.store || '').trim().toUpperCase())) return false
    if (selMkts.size && !selMkts.has(String(r.market || '').trim().toUpperCase())) return false
    return true
  }
  const rows = useMemo(() => stores.filter(keep), [stores, selStores, selMkts])
  const narrowed = selStores.size > 0 || selMkts.size > 0

  // The company roll-up is folded from the SAME filtered store rows, so the two tables can never
  // disagree and a market filter narrows both at once.
  const companies = useMemo(() => {
    const m = new Map<string, any>()
    for (const r of rows) {
      const c = m.get(r.company) || { company: r.company, payable_amount: 0, payable_devices: 0, paid_amount: 0, paid_devices: 0, unmatched_devices: 0, stores: 0 }
      c.payable_amount += r.payable_amount || 0; c.payable_devices += r.payable_devices || 0
      c.paid_amount += r.paid_amount || 0; c.paid_devices += r.paid_devices || 0
      c.unmatched_devices += r.unmatched_devices || 0; c.stores += 1
      m.set(r.company, c)
    }
    return [...m.values()].sort((a, b) => b.payable_amount - a.payable_amount)
  }, [rows])

  const payableTotal = rows.reduce((a, r) => a + (r.payable_amount || 0), 0)
  const payableDevices = rows.reduce((a, r) => a + (r.payable_devices || 0), 0)
  const cascade = useMemo(() => [...new Map(stores.map(r => [r.store, { id: r.store, label: r.store, market: r.market || '' }])).values()], [stores])

  const nd = data?.non_device || null
  const ndItems: any[] = nd?.by_item || []
  const ndStores: any[] = nd?.by_store || []
  const covMonths: any[] = cov?.months || []
  const evidence: any[] = data?.meta?.payment_date_evidence || []

  function sheets(): ExportSheet[] {
    return [
      { name: 'Payable by company', rows: companies, columns: [
        { header: 'Company', get: (r: any) => r.company },
        { header: 'Stores', get: (r: any) => r.stores },
        { header: 'Devices unpaid', get: (r: any) => r.payable_devices },
        { header: 'Payable', money: true, get: (r: any) => r.payable_amount },
        { header: 'Devices already paid', get: (r: any) => r.paid_devices },
        { header: 'Paid', money: true, get: (r: any) => r.paid_amount },
        { header: 'Devices not in the unit ledger', get: (r: any) => r.unmatched_devices },
      ] },
      { name: 'Payable by store', rows, columns: [
        { header: 'Company', get: (r: any) => r.company },
        { header: 'Store', get: (r: any) => r.store },
        { header: 'Market', get: (r: any) => r.market },
        { header: 'Devices unpaid', get: (r: any) => r.payable_devices },
        { header: 'Payable', money: true, get: (r: any) => r.payable_amount },
        { header: 'Devices already paid', get: (r: any) => r.paid_devices },
        { header: 'Paid', money: true, get: (r: any) => r.paid_amount },
        { header: 'Devices not in the unit ledger', get: (r: any) => r.unmatched_devices },
      ] },
      { name: 'Non-device (billed basis)', rows: ndItems, columns: [
        { header: 'Item', get: (r: any) => r.name },
        { header: 'Lines', get: (r: any) => r.lines },
        { header: 'Billed', money: true, get: (r: any) => r.amount },
        { header: 'Latest billed', get: (r: any) => r.latest },
      ] },
      { name: 'Non-device by company', rows: ndStores, columns: [
        { header: 'Company', get: (r: any) => r.company },
        { header: 'Store / account', get: (r: any) => r.store },
        { header: 'Lines', get: (r: any) => r.lines },
        { header: 'Billed', money: true, get: (r: any) => r.amount },
      ] },
      { name: 'Ledger coverage', rows: covMonths, columns: [
        { header: 'Month', get: (r: any) => r.month },
        { header: 'Units invoiced', get: (r: any) => r.invoiced_devices },
        { header: 'Units in the unit ledger', get: (r: any) => r.ledger_rows },
        { header: 'Coverage ratio', get: (r: any) => r.ratio },
        { header: 'Counted as covered', get: (r: any) => (r.covered == null ? 'not judged' : r.covered ? 'yes' : 'no') },
      ] },
    ]
  }

  return (
    <div style={{ padding: 20, maxWidth: 1280, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>Device Payable as at a date — {distributor}</h1>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 3 }}>
            Of the devices already billed to us on <strong>{asAt || '—'}</strong>, which ones had not yet been
            paid for — by company and by store.
          </div>
        </div>
        <ReportExportBar
          title={`Device Payable as at ${asAt}`}
          subtitle={`Unpaid billed devices as at ${asAt}${narrowed ? ' · filtered' : ''}`}
          filename="device_payable" sheets={sheets()} />
      </div>

      {/* ── WHAT THIS ANSWERS, AND WHY IT IS NOT THE OTHER PAYABLE ──────────────────────────── */}
      <div className="card" style={{ padding: '10px 14px', marginBottom: 14, borderLeft: '3px solid var(--accent, #6b7cff)' }}>
        <div style={{ fontSize: 13 }}>
          <strong>This is a BACKDATED payable.</strong> It stands on the date you pick and asks, unit by unit:
          had this device been paid for yet? Each serialised unit carries two dates — the day it was
          <em> billed</em> and the day it was <em>paid</em> — and the payable is the units billed on or before
          your date whose payment came later, or has not come at all. The <strong>Liabilities Due</strong> page
          and the Balance Sheet distributor payable answer a different question — the <em>current</em> open
          balance from the ledger&rsquo;s status column — and a status cannot be backdated: a unit paid this year
          reads &ldquo;paid&rdquo; even when you ask about last year. Nothing on this page is booked to the P&amp;L
          or the Balance Sheet, and nothing is written.
        </div>
      </div>

      <div className="card" style={{ padding: '10px 14px', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 8 }}>
          <strong>Payable as at</strong>
          <input type="date" value={asAt} onChange={e => setAsAt(e.target.value)}
                 style={{ padding: '5px 8px', fontSize: 13, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg2, transparent)', color: 'inherit' }} />
        </label>
        <div style={{ fontSize: 12, color: 'var(--text2)' }}>
          Any day — a year-end, a quarter-end, a closing date. Day granularity, because a payable is asked
          for on a day.
        </div>
      </div>

      <StandardFilterBar
        value={filt} onChange={setFilt}
        periodMode="none"
        show={{ period: false, stores: true, markets: true, reps: false }}
        cascadeStores={cascade}
        storeLabel="Stores…" marketLabel="Markets…" />

      {loading && <div className="card" style={{ padding: 14 }}>Loading…</div>}
      {err && <div className="card" style={{ padding: 14, color: 'var(--danger, #c33)' }}>Could not load: {err}</div>}

      {/* ── NOT MEASURED: the reason and the window, never a figure ─────────────────────────── */}
      {data && !loading && !measured && (
        <Card title="Not measured at this date — and that is the honest answer">
          <div style={{ fontSize: 13, marginBottom: 8 }}>
            A device payable for <strong>{asAt}</strong> is <strong>not measured</strong>: {cov?.reason}.
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text2)' }}>
            The per-unit ledger this report reads is a wipe-and-reinsert <em>current</em> snapshot, and old
            rows have been pruned out of it. For a date it does not reach, the join would still find a few
            units and still print a total — a small, confident, wrong number. So no number is printed.
            {cov?.start_month && <> Measurable coverage starts <strong>{cov.start_month}</strong>
              {cov?.end_month && <> and currently runs to <strong>{cov.end_month}</strong></>}. Pick a date in
              that window.</>}
            {' '}The coverage table below shows, month by month, how the ledger was judged.
          </div>
        </Card>
      )}

      {data && !loading && measured && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10, marginBottom: 14 }}>
            <StatTile label={`Device payable at ${asAt}`} value={money(payableTotal)} />
            <StatTile label="Devices still unpaid" value={num(payableDevices)} />
            <StatTile label="Already paid by this date" value={narrowed ? '—' : money(T.paid_amount)} />
            <StatTile label="Devices matched to the unit ledger" value={narrowed ? '—' : pct(T.match_rate)} />
            <StatTile label="Non-device items billed (separate basis)" value={narrowed ? '—' : money(nd?.amount)} />
          </div>

          {cov?.stale_tail && (
            <div className="card" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 12.5, color: 'var(--text2)' }}>
              Your date is <strong>after</strong> the last month the unit ledger currently holds
              ({cov.end_month}). Units billed since then have no ledger row yet, so they count in
              &ldquo;not in the unit ledger&rdquo; below rather than in the payable. That is a feed-freshness
              gap, not a payment.
            </div>
          )}

          <Card title="By company"
                note={<>Company comes from the store&rsquo;s assignment through the same matcher the P&amp;L books
                      with. A store nobody has assigned shows as <strong>(company not mapped)</strong> rather
                      than folded into the default company.</>}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Company</th><th style={thr}>Stores</th><th style={thr}>Devices unpaid</th><th style={thr}>Payable</th><th style={thr}>Devices paid</th><th style={thr}>Paid by this date</th><th style={thr}>Not in unit ledger</th></tr></thead>
                <tbody>
                  {companies.map(c => (
                    <tr key={c.company}>
                      <td style={td}>{c.company}</td>
                      <td style={tdr}>{num(c.stores)}</td>
                      <td style={tdr}>{num(c.payable_devices)}</td>
                      <td style={tdr}><strong>{money(c.payable_amount)}</strong></td>
                      <td style={tdr}>{num(c.paid_devices)}</td>
                      <td style={tdr}>{money(c.paid_amount)}</td>
                      <td style={tdr}>{num(c.unmatched_devices)}</td>
                    </tr>
                  ))}
                  {!companies.length && <tr><td style={td} colSpan={7}>No billed devices in the measured window at this date.</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="By store">
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Company</th><th style={th}>Store</th><th style={th}>Market</th><th style={thr}>Devices unpaid</th><th style={thr}>Payable</th><th style={thr}>Devices paid</th><th style={thr}>Paid by this date</th><th style={thr}>Not in unit ledger</th></tr></thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={`${r.company}|${r.store}`}>
                      <td style={td}>{r.company}</td>
                      <td style={td}>{r.store}</td>
                      <td style={td}>{r.market || '—'}</td>
                      <td style={tdr}>{num(r.payable_devices)}</td>
                      <td style={tdr}><strong>{money(r.payable_amount)}</strong></td>
                      <td style={tdr}>{num(r.paid_devices)}</td>
                      <td style={tdr}>{money(r.paid_amount)}</td>
                      <td style={tdr}>{num(r.unmatched_devices)}</td>
                    </tr>
                  ))}
                  {!rows.length && <tr><td style={td} colSpan={8}>No billed devices in the measured window at this date.</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>

          {/* ── HOW MUCH OF THIS FIGURE IS EVIDENCED, SAID OUT LOUD ───────────────────────── */}
          <Card title="How well this figure is evidenced — read this before quoting it"
                note="Every number a reader would need to judge the payable, rather than trust it.">
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <tbody>
                  <tr><td style={td}>Devices billed on or before {asAt}, inside the measured window</td><td style={tdr}><strong>{num(T.invoiced_devices)}</strong></td></tr>
                  <tr><td style={td}>…matched to a row in the per-unit ledger</td><td style={tdr}>{num(T.matched_devices)} ({pct(T.match_rate)})</td></tr>
                  <tr><td style={td}>…<strong>not</strong> in the per-unit ledger — no payment date exists for these, so they are in NO figure above, neither paid nor payable</td><td style={tdr}><strong>{num(T.unmatched_devices)}</strong></td></tr>
                  <tr><td style={td}>Matched units carrying <strong>no payment date at all</strong> — counted INTO the payable, because an absent date is not evidence of payment</td><td style={tdr}>{num(T.payment_date_unknown_devices)} · {money(T.payment_date_unknown_amount)}</td></tr>
                  <tr><td style={td}>The same unit billed on a second invoice inside the window — the ledger owes for it once, so this much of the payable is counted twice</td><td style={tdr}>{num(T.repeat_serial_rows)} · {money(T.repeat_serial_amount)}</td></tr>
                  <tr><td style={td}>Payable counting each distinct unit once (the figure above less those repeats)</td><td style={tdr}><strong>{money(T.distinct_device_payable_amount)}</strong> over {num(T.distinct_device_payable_devices)} units</td></tr>
                  <tr><td style={td}>Units on an invoice whose header says <strong>VOIDED</strong> — <strong>excluded</strong> from every figure above, because a cancelled invoice is not something we owe. The Device Purchases report excludes the same invoices under the same rule, so the two cannot disagree</td><td style={tdr}>{num(T.excluded_voided_payable_devices)} · {money(T.excluded_voided_payable_amount)}</td></tr>
                  <tr><td style={td}>Payable as it read <em>before</em> voided invoices were excluded — shown so an earlier figure stays explainable rather than merely gone</td><td style={tdr}>{money(T.payable_including_voided)}</td></tr>
                  <tr><td style={td}>Devices billed before the measured window opened ({cov?.start_month}) — the pruned ledger cannot evidence whether these were paid, so they are excluded rather than assumed settled</td><td style={tdr}><strong>{num(T.before_coverage_devices)}</strong></td></tr>
                </tbody>
              </table>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>
              That last line is the real limit of this report: a unit billed before {cov?.start_month} and still
              unpaid on your date would belong in a complete payable and is not in this one. The figure is a
              payable <em>on the units the unit ledger can evidence</em>, and it says so rather than implying
              completeness it does not have.
            </div>
          </Card>

          {/* ── THE OTHER BASIS — SEPARATE, LABELLED, NEVER MERGED ────────────────────────── */}
          <Card title="Non-device items — a DIFFERENT basis, shown apart and never added in"
                note={<><strong>These are not in the payable above and must not be added to it.</strong>
                      Chargebacks, loans and exchanges, managed services, SIM packs and activation fees are not
                      serialised units, so the per-unit ledger cannot see them at all. What is knowable is what
                      was <strong>billed</strong> on or before {asAt}. Whether each was settled by that date is
                      <strong> not measured</strong>: {nd?.settlement_reason}.</>}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Item, as the {distributor} bills it</th><th style={thr}>Lines</th><th style={thr}>Billed on or before {asAt}</th><th style={th}>Latest billed</th></tr></thead>
                <tbody>
                  {ndItems.map((i: any) => (
                    <tr key={i.name}><td style={td}>{i.name}</td><td style={tdr}>{num(i.lines)}</td><td style={tdr}><strong>{money(i.amount)}</strong></td><td style={td}>{i.latest || '—'}</td></tr>
                  ))}
                  {!ndItems.length && <tr><td style={td} colSpan={4}>No non-device items were billed in this window.</td></tr>}
                </tbody>
              </table>
            </div>
            {ndStores.length > 0 && (
              <>
                <div style={{ fontSize: 12.5, fontWeight: 700, margin: '14px 0 5px' }}>…and where they were billed</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>
                  Expect the {distributor}&rsquo;s own <strong>master / dealer account</strong> at the top of this
                  list. That is a head-office account, not a retail location, and it is kept as its own named
                  row rather than absorbed into whichever store shares its street number.
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead><tr><th style={th}>Company</th><th style={th}>Store or account, as billed</th><th style={thr}>Lines</th><th style={thr}>Billed</th></tr></thead>
                    <tbody>{ndStores.map((b: any) => (
                      <tr key={`${b.company}|${b.store}`}><td style={td}>{b.company}</td><td style={td}>{b.store}</td><td style={tdr}>{num(b.lines)}</td><td style={tdr}><strong>{money(b.amount)}</strong></td></tr>
                    ))}</tbody>
                  </table>
                </div>
              </>
            )}
            {nd?.device_lines_without_serial?.lines > 0 && (
              <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 10 }}>
                A further <strong>{money(nd.device_lines_without_serial.amount)}</strong> across
                {' '}{num(nd.device_lines_without_serial.lines)} device-named line(s) arrived on invoices that
                carried no serialised unit at all. There is no serial to trace them by, so they are in neither
                figure on this page — named here so they are not silently missing.
              </div>
            )}
          </Card>

          <Card title="What licenses the payment date — measured on this run, not claimed once"
                note={<>The whole report rests on one column meaning &ldquo;the day this unit was paid
                      for&rdquo;. That is not assumed: each year, what the per-unit ledger says was paid
                      is measured against the {distributor}&rsquo;s own settled payment batches — a
                      different feed, swept separately, which knows nothing about units. Close agreement
                      is the licence. <strong>Reported, never enforced</strong>: a timing difference must
                      not blank the page, so you are handed both numbers and can judge. Expect wide
                      variance in the years the unit ledger was pruned — that is the same prune the
                      coverage table below reads, showing up independently.</>}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Year paid</th><th style={thr}>Per-unit ledger says paid</th><th style={thr}>Settled payment batches</th><th style={thr}>Difference</th><th style={thr}>Variance</th></tr></thead>
                <tbody>{evidence.map((e: any) => (
                  <tr key={e.year}>
                    <td style={td}>{e.year}</td>
                    <td style={tdr}>{money(e.ledger_paid)}</td>
                    <td style={tdr}>{money(e.settled_batches)}</td>
                    <td style={tdr}>{money(e.difference)}</td>
                    <td style={tdr}>{e.variance_pct == null ? '—' : `${e.variance_pct}%`}</td>
                  </tr>
                ))}
                {!evidence.length && <tr><td style={td} colSpan={5}>No settled payment batches to measure against.</td></tr>}</tbody>
              </table>
            </div>
          </Card>

          <Card title="Ledger coverage, month by month — how the measurable window was derived"
                note={<>Nothing here is hard-coded. Each month compares how many units the per-unit ledger holds
                      against how many units were invoiced that month; coverage begins after the last month that
                      falls short of {cov ? `${Math.round((cov.ratio_threshold || 0) * 100)}%` : '—'}. A month with
                      fewer than {num(cov?.min_devices_judged)} invoiced units is not evidence either way and is
                      not judged.</>}>
            <div style={{ overflowX: 'auto', maxHeight: 340, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Month</th><th style={thr}>Units invoiced</th><th style={thr}>Units in the unit ledger</th><th style={thr}>Coverage</th><th style={th}>Counted as covered</th></tr></thead>
                <tbody>{covMonths.map((m: any) => (
                  <tr key={m.month}>
                    <td style={td}>{m.month}</td>
                    <td style={tdr}>{num(m.invoiced_devices)}</td>
                    <td style={tdr}>{num(m.ledger_rows)}</td>
                    <td style={tdr}>{m.ratio == null ? '—' : `${(m.ratio * 100).toFixed(0)}%`}</td>
                    <td style={{ ...td, fontSize: 12, color: 'var(--text2)' }}>{m.covered == null ? 'not judged' : m.covered ? 'yes' : 'no — ledger pruned here'}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
