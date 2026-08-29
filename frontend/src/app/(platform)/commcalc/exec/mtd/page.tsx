'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import PageIntro from '@/components/PageIntro'
import NarrativeBanner from '@/components/NarrativeBanner'
import Link from 'next/link'
import { api, fmt, getActiveOrg, localToday } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { StoreOpt } from '@/lib/market-store-cascade'
import { SortableTh, useTableSort } from '@/components/SortableTh'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import { useActiveCarrier } from '@/lib/auth-context'

// Super-admin org-resolution mitigation (same as the Sales Report page): reads carry the active tenant
// so a super-admin (whom the tenant middleware does NOT rewrite) reads the selected tenant, not the house
// org. No-op for normal users (the middleware overrides org_id to their membership) and when no tenant is
// selected. RULE FIVE (§3d): this page carries the SHARED <StandardFilterBar> — market -> store
// cascade with checkbox dropdowns (owner refinement 2026-08-04) + the employee multi-select — so it
// looks and behaves like every other report instead of carrying its own hand-rolled picker row.
const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }

// Executive MTD summary — replicates b2bsoft's "Month To Date Location / Employee Sales Report".
// Reads the org-corrected sales source (feed for the open month, raw_sales for a closed one) so it works
// for luxelink AND the house org with NO monthly upload. DISPLAY-ONLY.

const th: React.CSSProperties = { textAlign: 'right', padding: '7px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const thL: React.CSSProperties = { ...th, textAlign: 'left' }
const td: React.CSSProperties = { textAlign: 'right', padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 12.5, whiteSpace: 'nowrap' }
const tdL: React.CSSProperties = { ...td, textAlign: 'left' }

const pct = (n: number) => `${((n || 0) * 100).toFixed(1)}%`
const n2 = (n: number) => Number(n || 0).toFixed(2)
const int = (n: number) => String(Math.round(n || 0))

export default function ExecMtdPage() {
  const { period } = usePeriod()
  // Active-carrier lens: the dealer-share tooltip's example names only the active carrier for a
  // dual-carrier tenant (single-carrier tenants keep the original "Boost 100%, Total 50%" example).
  const { activeCarrier } = useActiveCarrier()
  const [data, setData] = useState<any>(null)
  const [tab, setTab] = useState<'location' | 'employee'>('location')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [showCfg, setShowCfg] = useState(false)
  const [fresh, setFresh] = useState<any>(null)   // per-feed data freshness — surfaces a stalled ingest
  // RULE FIVE standardized filters — one StandardFilterValue (store(s) / market(s) / rep(s)), applied
  // SERVER-SIDE. `period` is NOT part of it here: this page follows the global period selector in the
  // app header (usePeriod), and a second month control in the bar would give the user two competing
  // answers to "which month am I looking at".
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const selStores = filt.stores, selMarkets = filt.markets, selReps = filt.reps
  // DATE RANGE (owner 2026-08-11) — the bar's range mode writes the two bounds into `period`/`periodTo`
  // (the shared StandardFilterValue shape). They are sent to the SERVER, like every other filter on this
  // page, so the tables, the trending math and the exports narrow together instead of the table alone.
  const dFrom = filt.period || '', dTo = filt.periodTo || ''

  const load = useCallback(() => {
    if (!period) return
    setLoading(true); setErr(null)
    const qs = new URLSearchParams()
    // Send the BROWSER's local date, exactly like the Daily/Accessory Targets pages do. Without it the
    // MTD cut + trending divisor were resolved on the server's UTC clock, so after ~8pm ET the two
    // surfaces projected off a different "complete days elapsed" and could not agree.
    qs.set('today', localToday())
    if (dFrom) qs.set('date_from', dFrom)
    if (dTo) qs.set('date_to', dTo)
    selStores.forEach((s) => qs.append('stores', s))
    selMarkets.forEach((s) => qs.append('markets', s))
    selReps.forEach((s) => qs.append('reps', s))
    const q = qs.toString()
    api(`/api/v1/commcalc/exec-mtd/${encodeURIComponent(period)}?${q}${orgParam()}`)
      .then(setData).catch((e) => setErr(String(e?.message || e))).finally(() => setLoading(false))
  }, [period, selStores, selMarkets, selReps, dFrom, dTo])
  useEffect(() => { load() }, [load])

  // Data freshness — a stalled feed is why report numbers "freeze" on a date. Fetch once; show a banner
  // only when something is actually stale, so a healthy tenant sees nothing.
  useEffect(() => {
    api(`/api/v1/commcalc/ingest-freshness${orgParam() ? '?' + orgParam().slice(1) : ''}`)
      .then(setFresh).catch(() => setFresh(null))
  }, [])

  // Switching the MONTH in the app header drops a stale day-range. Without this, moving from July to
  // August while a July range is set would show an empty report whose emptiness is real but reads as a
  // bug — the range is scoped to the month the report is built from.
  useEffect(() => {
    setFilt((f) => (f.period || f.periodTo ? { ...f, period: '', periodTo: '' } : f))
  }, [period])

  // Options are computed by the backend from the UNFILTERED union (pick-don't-type over real data).
  // `stores_detail` carries each store's market, which is what the cascade narrows on; a backend that
  // predates it (or a store with no store_mapping row) still yields a usable flat list via the fallback.
  const opt = data?.filters || {}
  const storeOpts: string[] = opt.stores || []
  const repOpts: { id: string; label: string }[] = (opt.reps || []).map((r: string) => ({ id: r, label: r }))
  const cascadeStores: StoreOpt[] = useMemo(() => (
    (opt.stores_detail as any[] | undefined)?.map(s => ({ id: s.id, label: s.label, market: s.market })) ||
    storeOpts.map(s => ({ id: s, label: s, market: null }))
  ), [opt.stores_detail, storeOpts])
  // NARRATIVE BANNER (owner 2026-08-29). A deterministic "this MTD vs the same days last month" summary,
  // computed server-side from the SAME aggregation the table renders. It honours the store/market/rep
  // filters, but is SUPPRESSED while a custom date range is active — the narrative always compares whole
  // month-to-date windows, so pairing it with a slice of the month would be comparing two different things.
  const narrativeUrl = useMemo(() => {
    if (!period || dFrom || dTo) return null
    const qs = new URLSearchParams()
    qs.set('today', localToday())
    selStores.forEach((s) => qs.append('stores', s))
    selMarkets.forEach((s) => qs.append('markets', s))
    selReps.forEach((s) => qs.append('reps', s))
    return `/api/v1/commcalc/exec-mtd/${encodeURIComponent(period)}/narrative?${qs.toString()}${orgParam()}`
  }, [period, selStores, selMarkets, selReps, dFrom, dTo])

  const hasFilter = selStores.length > 0 || selMarkets.length > 0 || selReps.length > 0 || !!dFrom || !!dTo
  const clearFilters = () => setFilt(emptyStandardFilter())
  const src = data?.source || {}

  const active = tab === 'location' ? data?.by_location : data?.by_employee
  const labelKey = tab === 'location' ? 'store' : 'employee'
  const labelHdr = tab === 'location' ? 'Store' : 'Employee'
  const rows: any[] = active?.rows || []
  const total = active?.total || {}
  const tr = data?.trending || {}
  // What the SERVER actually did with the requested window (clamped / no overlap / undated lines it had
  // to drop). A backend that predates the range simply omits it -> {} -> the status line never renders.
  const dr = data?.date_range || {}
  // ACTIVATION-CLASSIFICATION GAP (mig 213/224). Total Activation counts only transactions whose Contract
  // Type resolves to an activation bucket; a Contract Type the tenant's map doesn't cover (e.g. Home
  // Internet / FiOS / Tablet activation labels) is silently EXCLUDED — the usual reason this total reads
  // LOWER than the b2bsoft MTD number. The backend returns the unrecognized labels + a human note; a
  // backend that predates this simply omits it -> {} -> the banner never renders. Fully-mapped tenant or
  // the house org -> note null -> hidden.
  const gaps = data?.classification_gaps || {}
  const unrecCts: { contract_type: string; transactions: number; lines: number }[] = gaps.unrecognized_contract_types || []

  // 16-column layout, in the exact order of the owner's spreadsheet, THEN two appended reconciliation
  // columns (the spreadsheet's own order is preserved). Conv. exported as the raw ratio (as the file
  // stores it); money columns flagged so Excel/PDF format + subtotal correctly.
  // WHY the two extra columns (2026-07-30): "Acc. Sales" here is the PURE accessory$ — the same number
  // the Sales Report shows and the same one this b2bsoft report has always meant. The Accessory Targets
  // page measures achieved/target on accessory$ + the device SET-UP FEE (owner directive 2026-07-17:
  // the set-up fee is a separate PAY item, never folded into accessory$, but it DOES count toward the
  // accessory TARGET). Both come from the one shared classifier; showing the bridge (Set-up Fee) and
  // the target basis (Acc.+Set-up) is what lets the two pages be reconciled to the cent.
  const cols = (lk: string): ExportColumn[] => [
    { header: lk === 'store' ? 'Store' : 'Employee', field: lk, role: lk === 'store' ? 'store' : 'rep', get: (r) => r[lk] },
    { header: 'Total Activation', field: 'total_activation', type: 'number', get: (r) => r.total_activation },
    { header: 'Activation', field: 'activation', type: 'number', get: (r) => r.activation },
    { header: 'Port', field: 'port', type: 'number', get: (r) => r.port },
    { header: 'BYOD', field: 'byod', type: 'number', get: (r) => r.byod },
    { header: 'Tablet', field: 'tablet', type: 'number', get: (r) => r.tablet },
    { header: 'Home Internet', field: 'home_internet', type: 'number', get: (r) => r.home_internet },
    { header: 'Edge', field: 'edge', type: 'number', get: (r) => r.edge },
    { header: 'Upgrade', field: 'upgrade', type: 'number', get: (r) => r.upgrade },
    { header: 'Total Phones', field: 'total_phones', type: 'number', get: (r) => r.total_phones },
    { header: 'Trending Box', field: 'trending_box', type: 'number', get: (r) => r.trending_box },
    { header: 'Bill Payment Qty', field: 'bill_payment_qty', type: 'number', get: (r) => r.bill_payment_qty },
    { header: '$', field: 'amount', money: true, get: (r) => r.amount },
    { header: 'Conv.', field: 'conv', type: 'number', get: (r) => r.conv },
    { header: 'Acc. Sales', field: 'acc_sales', money: true, get: (r) => r.acc_sales },
    { header: 'APB', field: 'apb', type: 'number', get: (r) => r.apb },
    { header: 'Trending Acc. Sales', field: 'trending_acc_sales', money: true, get: (r) => r.trending_acc_sales },
    { header: 'Activation Fee', field: 'activation_fee', money: true, get: (r) => r.activation_fee },
    { header: 'Total Protect', field: 'total_protect', type: 'number', get: (r) => r.total_protect },
    { header: 'Set-up Fee', field: 'setup_fee', money: true, get: (r) => r.setup_fee },
    // SET-UP FEE ECONOMICS (owner 2026-08-01, mig 263). Both are null until the tenant states the
    // percentage — rendered as an em-dash, never as a $0.00 that looks like a real answer.
    { header: 'Dealer share', field: 'setup_fee_dealer_share', money: true, get: (r) => r.setup_fee_dealer_share },
    { header: 'Employee pay', field: 'setup_fee_employee_pay', money: true, get: (r) => r.setup_fee_employee_pay },
    { header: 'Acc.+Set-up (target basis)', field: 'acc_plus_setup', money: true, get: (r) => r.acc_plus_setup },
  ]
  // CLICK-A-HEADER SORT (owner 2026-08-10) — the same shared primitive <ReportShell> reports use, so a
  // hand-rolled table behaves identically. Column keys come from `cols(...)` (the export definition), so
  // the sortable columns and the exported columns can never drift apart. The TOTAL row is rendered
  // separately below and is therefore never sorted into the middle of the table.
  const sortCols = cols(labelKey)
  const getCell = useCallback((r: any, field: string) => r?.[field], [])
  const { sort, toggle, sorted: viewRows } = useTableSort(rows, getCell)

  // Export BOTH tabs (like the file's two sheets), each with its own totals row appended.
  const withTotal = (rs: any[], tot: any, lk: string) => [...rs, { ...tot, [lk]: 'TOTAL' }]
  const exportSheets = [
    { name: 'By location', columns: cols('store'), rows: withTotal(data?.by_location?.rows || [], data?.by_location?.total || {}, 'store') },
    { name: 'By employee', columns: cols('employee'), rows: withTotal(data?.by_employee?.rows || [], data?.by_employee?.total || {}, 'employee') },
  ]

  const HEADERS = ['Total Activation', 'Activation', 'Port', 'BYOD', 'Tablet', 'Home Internet', 'Edge', 'Upgrade', 'Total Phones', 'Trending Box',
    'Bill Payment Qty', '$', 'Conv.', 'Acc. Sales', 'APB', 'Trending Acc. Sales', 'Activation Fee', 'Total Protect',
    'Set-up Fee', 'Dealer share', 'Employee pay', 'Acc.+Set-up']
  // Tooltips only on the two appended reconciliation columns (the b2bsoft 15 are unchanged).
  const HEADER_TIPS: Record<string, string> = {
    'Acc. Sales': 'Accessory sales revenue ONLY — the device set-up fee is excluded (it is a separate pay item). Same number as the Sales Report.',
    'Set-up Fee': 'Device set-up fee sold. A separate pay item, so it is NOT in Acc. Sales — but it DOES count toward the accessory target.',
    'Acc.+Set-up': 'Accessory sales + device set-up fee = the basis the Accessory Targets page measures achieved vs target on. THIS is the number to compare with that page.',
    'Dealer share': 'What the CARRIER pays the dealer of the set-up / activation fee collected'
      + (activeCarrier === 'total' ? ' (e.g. 50%)' : ' (e.g. 100%)')
      + '. Informational — no employee payout reads it. “—” means nobody has entered the percentage yet.',
    'Employee pay': 'The employee’s share of the set-up / activation fee collected, at the percentage configured for this tenant. “—” means the fee is not part of employee commission here, or no percentage has been entered.',
  }

  // A percentage nobody has entered is NOT zero dollars. Render it as an em-dash so the column
  // cannot be read as "the dealer gets nothing".
  const dash = (v: any) => (v === null || v === undefined ? '—' : fmt(v))
  const cellVals = (r: any) => [
    int(r.total_activation), int(r.activation), int(r.port), int(r.byod), int(r.tablet), int(r.home_internet), int(r.edge), int(r.upgrade), int(r.total_phones),
    int(r.trending_box), int(r.bill_payment_qty), fmt(r.amount), pct(r.conv), fmt(r.acc_sales), n2(r.apb),
    fmt(r.trending_acc_sales), fmt(r.activation_fee), int(r.total_protect),
    fmt(r.setup_fee), dash(r.setup_fee_dealer_share), dash(r.setup_fee_employee_pay), fmt(r.acc_plus_setup),
  ]

  return (
    <div>
      <PageIntro
        title={<>📈 Executive MTD — {period}</>}
        right={<Link href="/commcalc/exec" style={{ fontSize: 12 }}>← Owner Overview</Link>}
        help={<>
          Month-to-date location &amp; employee sales — activations by type, phones, bill payments, accessory
          sales, and trending projections.{' '}
          {tr.factor
            ? (tr.basis === 'range'
              ? `Trending = selected days × ${tr.days_in_month} ÷ ${tr.elapsed_days} complete day${tr.elapsed_days === 1 ? '' : 's'} in the range.`
              : `Trending = MTD × ${tr.days_in_month} ÷ ${tr.elapsed_days} complete days.`)
            : ''}
        </>}
      />

      {/* DATA-FRESHNESS banner (owner 2026-08-27). A stalled feed is why report numbers "freeze" on a date —
          the report reads live, so if a feed stops ingesting, the numbers stop moving. Show which feed is
          stale, when it last ingested and the latest transaction date it carries, and link to fix it. Only
          renders when something is actually stale. */}
      {fresh?.any_stale && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e', borderRadius: 8,
          padding: '10px 12px', fontSize: 12.5, marginBottom: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>⚠️ Some data hasn’t updated — numbers below may be stale</div>
          {(fresh.feeds || []).filter((f: any) => f.stale && f.rows).map((f: any) => (
            <div key={f.key} style={{ marginTop: 2 }}>
              <b>{f.label}</b>: latest data {f.latest_data_date || '—'}
              {typeof f.days_stale === 'number' ? ` (${f.days_stale} day${f.days_stale === 1 ? '' : 's'} behind)` : ''}
              {f.last_ingest_at ? ` · last ingested ${String(f.last_ingest_at).slice(0, 10)}` : ''}
              {f.source === 'raw_custom_import' && f.recent_files?.length ? ` · last file: ${f.recent_files[0]}` : ''}
            </div>
          ))}
          <div style={{ marginTop: 6 }}>
            New files aren’t being ingested. Open <Link href="/commcalc/email-imports" style={{ color: '#92400e', textDecoration: 'underline' }}>Data Imports → Email Imports</Link>,
            check the processed history, and click <b>Run now</b>. If the latest file isn’t in the inbox, the report email stopped arriving for those days.
          </div>
        </div>
      )}

      {/* NARRATIVE BANNER — plain-English "how this month is going vs last" above the grid. Deterministic
          (computed from the same numbers below), so it can never disagree with the table. */}
      <NarrativeBanner url={narrativeUrl} />

      {/* RULE FIVE standardized filter bar — market -> store cascade (checkbox dropdowns) + employees,
          pick-don't-type over the org's real data, applied SERVER-SIDE so the tables, the trending math
          AND the exports all reflect the same selection. Period comes from the global header selector. */}
      <StandardFilterBar
        value={filt} onChange={setFilt} periodMode="range"
        show={{ period: true, stores: true, markets: true, reps: true }}
        cascadeStores={cascadeStores}
        repOptions={repOpts}
        storeLabel="Stores…" marketLabel="Markets…" repLabel="Employees…"
      />

      {/* DATE-RANGE STATUS (owner 2026-08-11). The window is scoped to the month in the header — the
          report is built from that one month's sales union — so when the server clamps it, says the
          window misses the month entirely, or drops undated lines, the page states it rather than
          showing a narrowed number that looks like the whole story. */}
      {dr.active && (
        <div style={{ fontSize: 12, marginBottom: 10 }}>
          <span style={{ background: dr.no_overlap ? '#fef3f2' : 'var(--surface2)',
            color: dr.no_overlap ? '#b42318' : 'var(--text2)', borderRadius: 8, padding: '4px 10px' }}>
            {dr.no_overlap ? (
              <>The selected dates fall outside <b>{period}</b> ({dr.month_from} → {dr.month_to}) — nothing to
                show. Pick dates inside the month, or change the month in the header.</>
            ) : (
              <>Showing <b>{dr.from}</b> → <b>{dr.to}</b>
                {dr.clamped && <> · clamped to <b>{period}</b> (you asked for {dr.requested_from || dr.month_from} → {dr.requested_to || dr.month_to}; this report is built from one month at a time)</>}
                {dr.undated_excluded > 0 && <> · {dr.undated_excluded} line{dr.undated_excluded === 1 ? '' : 's'} with no date excluded (they cannot be placed in a range)</>}
                {' '}· tables, totals and exports all follow the range.</>
            )}
          </span>
        </div>
      )}

      {/* Source-coverage transparency (owner's debug-first mandate): which source led the union and how
          many stores it surfaced — so a partial-feed month that hid stores is self-evident here. */}
      {(src.stores_shown != null) && (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
          <span style={{ background: 'var(--surface2)', borderRadius: 8, padding: '4px 10px' }}>
            Reading <b>{src.primary === 'daily_sales_feed' ? 'daily email feed' : 'monthly raw_sales'}</b>
            {' '}· <b>{src.stores_shown}</b> store{src.stores_shown === 1 ? '' : 's'} shown
            {' '}(feed {src.feed_rows ?? 0} · raw_sales {src.raw_rows ?? 0} rows)
            {src.stores_from_other > 0 && <> · <b>{src.stores_from_other}</b> store{src.stores_from_other === 1 ? '' : 's'} pulled from {src.other === 'raw_sales' ? 'raw_sales' : 'the feed'} that the primary didn’t carry</>}
            {src.filled_cells > 0 && <> · {src.filled_cells} filled + {src.richer_cells || 0} richer store-day cell(s)</>}
          </span>
        </div>
      )}

      {/* ACTIVATION-CLASSIFICATION GAP (owner 2026-08-13, "luxelink activations don't match b2bsoft").
          Total Activation = distinct transactions whose Contract Type resolves to an activation bucket
          (Activation/Port/BYOD/Upgrade). A Contract Type the tenant's map doesn't cover — a Total-carrier
          tenant's Home Internet / FiOS / Tablet activation labels are the usual culprits — resolves to None
          and is EXCLUDED, so this total reads lower than the b2bsoft MTD count that includes them. Naming the
          uncounted labels (and how many transactions each hides) turns a silent low number into a one-click
          fix in Classification settings. Hidden when nothing is unmapped (note null). */}
      {gaps.note && (
        <div style={{ fontSize: 12.5, marginBottom: 10, background: '#fffbeb', border: '1px solid #fde68a',
          color: '#92400e', borderRadius: 8, padding: '9px 12px' }}>
          <div style={{ fontWeight: 700, marginBottom: 3 }}>⚠️ Some activations aren’t being counted in Total Activation</div>
          <div style={{ marginBottom: unrecCts.length ? 6 : 0 }}>
            Home Internet, FiOS, FWA and Tablet activations are now counted automatically. These remaining
            Contract Type labels still aren’t recognized, so their transactions fall out of the activation
            count (this is why the total can read lower than the b2bsoft MTD report, which counts them).
          </div>
          {unrecCts.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
              {unrecCts.slice(0, 12).map((u) => (
                <span key={u.contract_type} style={{ background: '#fef3c7', border: '1px solid #fcd34d',
                  borderRadius: 6, padding: '2px 8px', whiteSpace: 'nowrap' }}>
                  {u.contract_type} <b>×{u.transactions}</b>
                </span>
              ))}
              {unrecCts.length > 12 && <span style={{ padding: '2px 4px' }}>+{unrecCts.length - 12} more…</span>}
            </div>
          )}
          <div>
            Map each label to <b>Activation</b>, <b>BYOD</b> or <b>Upgrade</b> (or <b>None</b> to exclude on
            purpose) in{' '}
            <Link href="/commcalc/sales-report" style={{ color: '#92400e', fontWeight: 700, textDecoration: 'underline' }}>
              Sales Report → ⚙ Classification settings
            </Link>{' '}
            and this total will reconcile to b2bsoft. This changes reporting only — no commission pay is affected.
          </div>
        </div>
      )}

      {/* SOURCE OF TRUTH (mig 923). When the tenant names Activation Details the activation basis, Total
          Activation on this page comes from that report (distinct Serial#) and EXCLUDES Upgrade — the
          b2b-consistent definition that matches /activation-counts. Says so plainly so the number is never
          silently redefined. Hidden on the default sales basis (active:false). */}
      {data?.activation_source?.active && (
        <div style={{ fontSize: 12.5, marginBottom: 10, background: '#ecfdf5', border: '1px solid #6ee7b7',
          color: '#065f46', borderRadius: 8, padding: '9px 12px' }}>
          <span style={{ fontWeight: 700 }}>✓ Activations from the Activation Details report (basis of truth).</span>{' '}
          Total Activation counts distinct devices and <b>excludes Upgrade</b> (b2b-consistent); Upgrade is shown
          in its own column. {int(data.activation_source.ad_rows || 0)} activation rows for this window.{' '}
          <Link href="/commcalc/activations" style={{ color: '#065f46', fontWeight: 700, textDecoration: 'underline' }}>
            Open the Activations report &amp; reconciliation →
          </Link>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
          {(['location', 'employee'] as const).map((tb) => (
            <button key={tb} onClick={() => setTab(tb)}
              style={{ padding: '6px 14px', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer',
                background: tab === tb ? 'var(--accent)' : 'transparent', color: tab === tb ? '#fff' : 'var(--text2)' }}>
              {tb === 'location' ? 'By location' : 'By employee'}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <button onClick={() => setShowCfg((s) => !s)} style={{ fontSize: 12, background: 'none', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 10px', cursor: 'pointer', color: 'var(--text2)' }}>
            ⚙︎ Metric definitions
          </button>
          <ReportExportBar title={`Executive MTD ${period}`} filename={`exec_mtd_${String(period).replace(/\s+/g, '_')}`} sheets={exportSheets} />
        </div>
      </div>

      {showCfg && <MetricConfigPanel onClose={() => setShowCfg(false)} onSaved={load} />}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, borderLeft: '3px solid #b42318', color: '#b42318', fontSize: 13 }}>Could not load MTD summary: {err}</div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          {hasFilter
            ? <>No sales match the selected filter for {period}. <span style={{ color: 'var(--accent)', cursor: 'pointer' }} onClick={clearFilters}>Clear filters</span>.</>
            : <>No sales for {period}. (If the daily feed is ingesting but this is empty, run the sales promotion —
              POST /commcalc/sales/promote-due — or check the tenant&apos;s sales mailbox.)</>}
        </div>
      ) : (
        <div className="card table-wrapper" style={{ padding: 0 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              <SortableTh field={labelKey} sort={sort} onSort={toggle} style={thL}>{labelHdr}</SortableTh>
              {HEADERS.map((h, i) => (
                <SortableTh key={h} field={String(sortCols[i + 1]?.field || h)} sort={sort} onSort={toggle}
                  style={th} title={HEADER_TIPS[h]}>{h}</SortableTh>
              ))}
            </tr></thead>
            <tbody>
              {viewRows.map((r, i) => (
                <tr key={i}>
                  <td style={{ ...tdL, fontWeight: 600 }}>{r[labelKey] || '—'}</td>
                  {cellVals(r).map((v, j) => <td key={j} style={td}>{v}</td>)}
                </tr>
              ))}
              <tr style={{ background: 'var(--surface2)', fontWeight: 700 }}>
                <td style={{ ...tdL, fontWeight: 800 }}>TOTAL</td>
                {cellVals(total).map((v, j) => <td key={j} style={{ ...td, fontWeight: 700 }}>{v}</td>)}
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {!loading && !err && rows.length > 0 && (
        <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12, maxWidth: 900 }}>
          <b>Two accessory numbers, on purpose.</b> <b>Acc. Sales</b> is accessory sales revenue only —
          the device <b>set-up fee</b> is a separate pay item and is never folded into it (owner
          directive 2026‑07‑17), which is why it has its own column. The{' '}
          <a href="/commcalc/targets/accessories">Accessory Targets</a> page measures achieved‑vs‑target
          on <b>Acc.+Set‑up</b> — so compare that column, not Acc. Sales, when the two pages look
          different. Both bases come from the SAME shared classifier and the same sales rows as the
          Sales Report; <b>APB</b> and <b>Trending Acc. Sales</b> stay on the pure Acc. Sales basis.
        </p>
      )}
    </div>
  )
}

// ── Admin-editable metric definitions (SAP-configurable; no hard-coded classifier) ──────────────────
function MetricConfigPanel({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [cfg, setCfg] = useState<any>(null)
  const [saving, setSaving] = useState<string | null>(null)
  useEffect(() => { api('/api/v1/commcalc/exec-metric-config').then(setCfg).catch(console.error) }, [])
  const BUCKET_KEYS: Record<string, string[]> = {
    activation: ['byod', 'upgrade', 'port'],
    phones: ['category'],
    bill_payment: ['department', 'category'],
    accessory: ['category'],
    activation_fee: ['product_desc_contains'],
    protect: ['product_desc_contains', 'exclude_product_desc_contains', 'exclude_department', 'exclude_category'],
  }
  const setTok = (bucket: string, key: string, csv: string) => {
    setCfg((c: any) => {
      const nc = { ...c, config: { ...c.config, [bucket]: { ...c.config[bucket], rules: { ...c.config[bucket].rules, [key]: csv.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean) } } } }
      return nc
    })
  }
  const save = (bucket: string) => {
    setSaving(bucket)
    api('/api/v1/commcalc/exec-metric-config', { method: 'PUT', body: JSON.stringify({ bucket, rules: cfg.config[bucket].rules, basis: cfg.config[bucket].basis }) })
      .then(() => onSaved()).catch(console.error).finally(() => setSaving(null))
  }
  if (!cfg) return <div className="card" style={{ padding: 16, marginBottom: 12 }}>Loading definitions…</div>
  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>Metric definitions (config — comma-separated tokens, case-insensitive)</div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text3)' }}>✕</button>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
        These map raw sales lines to the MTD buckets. Tokens match the stored <code>department</code> /
        <code>category</code> (a b2bsoft export stores either the Category or the System Category column) or
        a substring of <code>product_desc</code>. Editing here does NOT touch commission pay.
      </p>
      <p style={{ fontSize: 12, color: 'var(--text2)', margin: '0 0 10px', padding: '7px 9px', background: 'var(--surface2)', borderRadius: 6 }}>
        ℹ️ <b>Activation type</b> (Activation/BYOD/Upgrade counts) and <b>Accessory $</b> now flow from the
        shared Sales Report classifier + the Sales Report’s <b>Classification settings</b> — counted by
        <b> distinct transaction</b> — so Executive MTD, the Sales Report and Daily Targets always agree.
        The tokens below still drive the <b>Port</b> split and the per-line columns
        (<b>Total Phones / Bill Payment / Activation Fee / Total Protect</b>); the <code>activation</code>
        byod/upgrade and <code>accessory</code> tokens no longer change the headline counts.
      </p>
      {(cfg.buckets as string[]).map((b) => (
        <div key={b} style={{ borderTop: '1px solid var(--border)', padding: '8px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>{b} <span style={{ color: 'var(--text3)', fontWeight: 400 }}>({cfg.config[b].basis})</span></div>
            <button onClick={() => save(b)} disabled={saving === b} style={{ fontSize: 12, padding: '3px 10px', cursor: 'pointer' }}>{saving === b ? 'Saving…' : 'Save'}</button>
          </div>
          {(BUCKET_KEYS[b] || Object.keys(cfg.config[b].rules)).map((k) => (
            <label key={k} style={{ display: 'grid', gridTemplateColumns: '190px 1fr', gap: 8, alignItems: 'center', margin: '4px 0' }}>
              <span style={{ fontSize: 12, color: 'var(--text2)' }}>{k}</span>
              <input value={(cfg.config[b].rules[k] || []).join(', ')} onChange={(e) => setTok(b, k, e.target.value)}
                style={{ fontSize: 12, padding: '4px 8px', border: '1px solid var(--border)', borderRadius: 6 }} />
            </label>
          ))}
        </div>
      ))}
    </div>
  )
}
