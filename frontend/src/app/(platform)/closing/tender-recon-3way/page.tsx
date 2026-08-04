'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, localToday, apiUpload, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'

// 3-Way Tender Recon — the SAME tenders captured three independent ways, per store, per day, per tender:
//  (1) Daily Closing (what the rep entered), (2) POS X-report, (3) Sales Transactions (raw_sales/feed).
// Reads GET /api/v1/closing/tender-recon-3way?date_from=&date_to=. Click a Sales figure to drill into
// the transactions behind it.
//
// RULE FIVE (§3d) retrofit (retail-ops-22, OWNER DIRECTIVE 2026-08-03: "3-Way Tender Recon should also
// have our standard filters with date range" — this OVERRIDES the retail-ops-15 out-of-scope call):
// StandardFilterBar drives store(s)/market(s)/date-range. The old custom `MultiSelect` + market-join is
// replaced with the shared `optionsFromRows`/`filterRows` framework (§3b pick-don't-type). Store/market
// filtering stays CLIENT-SIDE (the backend leg only ever accepted ONE `store=` code, incompatible with a
// multi-select — never sent from here now); the market join itself is unchanged in substance (still
// `/closing/stores` by code, falling back to `/asset/filter-options` by address/leading-street-number —
// preserved rather than simplified away, since dropping either source would be a real regression for a
// store that only resolves through one of them). DATE RANGE is now genuinely server-side: `closing/
// router.py`'s `tender_recon_3way` gained additive `date_from`/`date_to` params (mig-free, no schema
// change) returning ONE DAY-BLOCK PER CALENDAR DATE — never a single days-summed total. That choice is
// deliberate: netting a store's +$50 day against a -$50 day nets to a clean-looking $0 that hides two
// real discrepancies, so the frontend gets (and renders) the individual per-store-per-day rows and does
// its own filter-aware aggregation on top (see the "Selection totals" summary below, ADDENDUM owner
// request same day: "should also have a total of all the stores... and show the discrepancy for that
// date range"). The historical single-date `date=` call shape is UNCHANGED byte-for-byte server-side
// (this page just no longer uses it) — see the park record for the harness proof.

type Drill = { date: string; store: string; tender: string; label: string; storeName: string }
type StoreDay = {
  date: string
  store_code: string
  store_address: string
  market: string
  tenders: { tender: string; label: string; closing: number; x_report: number; sales: number; match: boolean }[]
  totals: { closing: number; x_report: number; sales: number }
  x_report_unmapped?: { amount: number; raw_labels: string[] } | null
  sales_unmapped?: { amount: number; raw_labels: string[] } | null
  bank_deposit?: any
}

export default function TenderRecon3WayPage() {
  const [filt, setFilt] = useState<StandardFilterValue>(() => { const t = localToday(); return { ...emptyStandardFilter(t), periodTo: t } })
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [storeMeta, setStoreMeta] = useState<{ store_code: string; store_address: string; market: string }[]>([])
  const [assetStores, setAssetStores] = useState<{ store: string; market: string }[]>([])
  const [onlyMismatch, setOnlyMismatch] = useState(false)
  const [drill, setDrill] = useState<Drill | null>(null)
  const [xrBusy, setXrBusy] = useState(false)
  const [xrMsg, setXrMsg] = useState('')

  const from = filt.period || localToday()
  const to = filt.periodTo || from
  const uploadDate = to   // an X-Report upload is always for ONE specific day — the latest day in the
                          // selected range, matching the (usually today-today) common case; pick a
                          // different "To" date in the filter to target a different upload day.

  async function uploadXReport(f: File) {
    setXrBusy(true); setXrMsg('')
    const form = new FormData(); form.append('file', f)
    try {
      const d: any = await apiUpload(`/api/v1/commcalc/upload/x_report?close_date=${encodeURIComponent(uploadDate)}&org_id=${ORG_ID}`, form)
      const n = d?.tenders ?? d?.rows_saved ?? 0
      setXrMsg(`✅ X‑Report ingested — ${n} tender rows${d?.date ? ' for ' + d.date : ''}. Recon refreshed.`)
      load()
    } catch (e: any) {
      setXrMsg(`❌ ${e?.message || String(e)}`)
    }
    setXrBusy(false)
  }

  function load() {
    setLoading(true)
    api(`/api/v1/closing/tender-recon-3way?date_from=${from}&date_to=${to}`)
      .then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [from, to])
  useEffect(() => {
    api('/api/v1/closing/stores').then((d: any) => setStoreMeta(Array.isArray(d) ? d : (d?.stores || d?.data || []))).catch(() => {})
    api('/api/v1/asset/filter-options').then((d: any) => setAssetStores(d?.stores || [])).catch(() => {})
  }, [])

  const tenders: { key: string; label: string }[] = data?.tenders || []
  const days: any[] = data?.days || []

  // Market per store: prefer closing/stores by code, else asset market by address / leading street-number
  // (unchanged join logic, just no longer feeding a bespoke MultiSelect).
  const marketOf = useMemo(() => {
    const mktByCode: Record<string, string> = {}
    storeMeta.forEach(s => { if (s.store_code && s.market) mktByCode[s.store_code] = s.market })
    const mktByAddr: Record<string, string> = {}, mktByNum: Record<string, string> = {}
    const leadNum = (a: string) => (a.match(/^\s*([0-9][0-9-]*)/)?.[1] || '').replace(/\D/g, '')
    assetStores.forEach(s => {
      const a = (s.store || '').trim().toLowerCase(); if (!a || !s.market) return
      mktByAddr[a] = s.market
      const nk = leadNum(a); if (nk && !mktByNum[nk]) mktByNum[nk] = s.market
    })
    return (code: string, address: string): string => {
      if (mktByCode[code]) return mktByCode[code]
      const a = (address || '').trim().toLowerCase()
      if (mktByAddr[a]) return mktByAddr[a]
      const nk = leadNum(a); return (nk && mktByNum[nk]) || ''
    }
  }, [storeMeta, assetStores])

  // Flatten every day's stores into one row-per-store-per-day list — the shared filter framework's unit.
  const flat: StoreDay[] = useMemo(() => days.flatMap((d: any) =>
    (d.stores || []).map((s: any) => ({ ...s, date: d.date, market: marketOf(s.store_code, s.store_address) }))
  ), [days, marketOf])

  const acc = useMemo(() => ({ store: (r: StoreDay) => r.store_address, market: (r: StoreDay) => r.market }), [])
  const opts = useMemo(() => optionsFromRows(flat, acc), [flat, acc])
  // OWNER DIRECTIVE 2026-08-04 (market->store cascade + checkbox picker): distinct store/market pairs
  // from the same flattened rows — no new backend read.
  const storesForCascade: StoreOpt[] = useMemo(() => {
    const seen = new Map<string, StoreOpt>()
    for (const r of flat) {
      const id = (r.store_address || '').trim()
      if (id && !seen.has(id)) seen.set(id, { id, label: id, market: r.market || null })
    }
    return [...seen.values()]
  }, [flat])
  // The RULE FIVE filter selection (store/market) — drives everything below, live. "Mismatches only" is
  // a module-specific DISPLAY toggle (not part of the core filter set), applied on top for the table/
  // cards but NOT for the selection-totals summary, which reflects the full filtered selection.
  const filtered = useMemo(() => filterRows(flat, filt, acc), [flat, filt, acc])
  const shown = filtered.filter(r => !onlyMismatch || r.tenders.some(t => !t.match))

  // ── Selection totals (ADDENDUM, owner 2026-08-03: "a total of all the stores... for that date
  //    range"). Computed CLIENT-SIDE from the rows already fetched, over the FULL filtered selection
  //    (stores/markets × date range) — live-updates with every filter change since it derives from
  //    `filtered`. Net variance ALONE can hide offsetting errors across stores/days (a +$50 day netting
  //    against a -$50 day reads as a clean $0), so a GROSS (absolute, never netted) figure and a
  //    mismatched-store-day COUNT are shown alongside the net — per the owner's explicit caution. ──
  const summary = useMemo(() => {
    let closing = 0, xrep = 0, sales = 0, grossCX = 0, grossXS = 0, mismatchDays = 0
    for (const r of filtered) {
      closing += r.totals.closing; xrep += r.totals.x_report; sales += r.totals.sales
      grossCX += Math.abs(r.totals.x_report - r.totals.closing)
      grossXS += Math.abs(r.totals.sales - r.totals.x_report)
      if (r.tenders.some(t => !t.match)) mismatchDays++
    }
    return {
      closing, xrep, sales, netCX: xrep - closing, netXS: sales - xrep, grossCX, grossXS,
      mismatchDays, total: filtered.length,
      unmappedX: filtered.reduce((a, r) => a + (r.x_report_unmapped?.amount || 0), 0),
      unmappedS: filtered.reduce((a, r) => a + (r.sales_unmapped?.amount || 0), 0),
    }
  }, [filtered])

  // sources_present / x_report_ever are tenant/day-level honesty signals, independent of the store/
  // market filter — OR'd across every day actually FETCHED (the selected date range), not narrowed by
  // store/market (a store filter narrowing "no X-report" to "not shown" would be dishonest).
  const sp = useMemo(() => days.reduce((a: any, d: any) => ({
    closing: a.closing || !!d.sources_present?.closing, x_report: a.x_report || !!d.sources_present?.x_report,
    sales: a.sales || !!d.sources_present?.sales, bank_deposit: a.bank_deposit || !!d.sources_present?.bank_deposit,
  }), { closing: false, x_report: false, sales: false, bank_deposit: false }), [days])
  const xReportEver = days.length > 0 ? !!days[0]?.x_report_ever : false
  const depositBasis = flat.find(r => r.bank_deposit?.match_target)?.bank_deposit?.match_target as string | undefined
  const note = useMemo(() => {
    let n = 'X-report tender amounts include tax; sales-transaction figures are merchandise (ext price), so small deltas between those two are expected.'
    if (depositBasis) n += ` Bank Deposit is compared against the tenant's configured basis (${depositBasis.replace(/_/g, ' ')}).`
    if (!xReportEver) n += ' This tenant has NEVER had a POS X-report imported — check (1) the mailbox has an *X-Report* -> x_report rule and (2) b2bsoft is actually scheduled to email an X-Report for this tenant.'
    if (summary.unmappedX) n += ` ⚠ ${fmt(summary.unmappedX)} of X-report tenders (selected range/selection) used a raw label this tenant's mapping doesn't recognize — map it on /closing/tender-config.`
    if (summary.unmappedS) n += ` ⚠ ${fmt(summary.unmappedS)} of sales-transaction tenders (selected range/selection) used a raw label this tenant's mapping doesn't recognize.`
    return n
  }, [depositBasis, xReportEver, summary.unmappedX, summary.unmappedS])

  // Group the (already filtered/display-narrowed) rows back by date for the per-store-card render —
  // dates descending (mirrors /closing/summary's range-mode sort), stores ascending within each date.
  const grouped = useMemo(() => {
    const byDate = new Map<string, StoreDay[]>()
    for (const r of shown) { const list = byDate.get(r.date) || []; list.push(r); byDate.set(r.date, list) }
    for (const list of byDate.values()) list.sort((a, b) => (a.store_address || '').localeCompare(b.store_address || ''))
    return [...byDate.entries()].sort((a, b) => b[0].localeCompare(a[0]))
  }, [shown])

  function buildPayload(): ExportPayload {
    const rows: any[] = []
    for (const s of shown) {
      for (const t of s.tenders) rows.push({ date: s.date, store: s.store_address, ...t })
      if (s.x_report_unmapped?.amount) rows.push({
        date: s.date, store: s.store_address, tender: 'unmapped',
        label: `⚠ Unmapped X-report (${(s.x_report_unmapped.raw_labels || []).join(', ') || 'unlabeled'})`,
        closing: '', x_report: s.x_report_unmapped.amount, sales: '', match: false,
      })
      if (s.sales_unmapped?.amount) rows.push({
        date: s.date, store: s.store_address, tender: 'unmapped',
        label: `⚠ Unmapped sales (${(s.sales_unmapped.raw_labels || []).join(', ') || 'unlabeled'})`,
        closing: '', x_report: '', sales: s.sales_unmapped.amount, match: false,
      })
    }
    const scope = from === to ? from : `${from} → ${to}`
    return {
      title: '3-Way Tender Recon', subtitle: scope,
      filename: `tender-recon-3way_${from}_${to}`,
      sheets: [{ name: 'By store/day/tender', rows, columns: [
        { header: 'Date', get: (r: any) => r.date, type: 'date' },
        { header: 'Store', get: (r: any) => r.store },
        { header: 'Tender', get: (r: any) => r.label },
        { header: 'Closing', get: (r: any) => r.closing, money: true },
        { header: 'X-report', get: (r: any) => r.x_report, money: true },
        { header: 'Sales', get: (r: any) => r.sales, money: true },
        { header: 'Match', get: (r: any) => r.match ? 'OK' : 'CHECK' },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧮 3-Way Tender Recon</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            The same money captured three ways — <strong>Daily Closing</strong> (rep entry),
            <strong> POS X-report</strong>, and <strong>Sales Transactions</strong> — per store, per day, across
            cash / credit / external CC / gift card / store account / zelle. The X-report is generated from
            the sales transactions, so those two should agree; the closing is the human cross-check.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label id="xr-upload-btn" className="btn" style={{ cursor: xrBusy ? 'wait' : 'pointer', whiteSpace: 'nowrap' }}
            title={`Upload the POS X-Report for ${uploadDate} (the "To" date of the filter above). A single-day report only — a date-range file is rejected.`}>
            {xrBusy ? '⏳ Uploading…' : `⬆ Upload X‑Report (${uploadDate})`}
            <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={xrBusy}
              onChange={e => { const f = e.target.files?.[0]; if (f) uploadXReport(f); e.currentTarget.value = '' }} />
          </label>
          {flat.length > 0 && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
        </div>
      </div>
      {xrMsg && <div style={{ fontSize: 12, marginBottom: 10, color: xrMsg.startsWith('❌') ? '#b91c1c' : 'var(--text2)' }}>{xrMsg}</div>}

      {!loading && !data?.error && xReportEver === false && (
        <div className="card" style={{ padding: '14px 16px', marginBottom: 14, border: '1px solid #f59e0b', background: '#fffbeb' }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#92400e', marginBottom: 4 }}>
            ⚠ No POS X-Report has EVER been imported for this tenant
          </div>
          <div style={{ fontSize: 13, color: '#78350f', lineHeight: 1.5 }}>
            The <strong>POS X-report</strong> column below will read $0 for every store/tender until this is set up —
            it isn't a bug in the recon, there's simply no X-report data in the system yet. Two things to do:
          </div>
          <ol style={{ fontSize: 13, color: '#78350f', margin: '6px 0 0', paddingLeft: 18, lineHeight: 1.6 }}>
            <li>
              <strong>Prove the pipe today:</strong> click <a href="#xr-upload-btn" onClick={e => { e.preventDefault(); document.getElementById('xr-upload-btn')?.click() }} style={{ color: '#92400e', fontWeight: 600 }}>⬆ Upload X‑Report</a> above
              with today's report file (or upload it from <a href="/commcalc/upload" style={{ color: '#92400e', fontWeight: 600 }}>Data Imports</a>, type "X Report (POS tenders)").
            </li>
            <li>
              <strong>Set up automatic daily import:</strong> under <a href="/commcalc/email-imports" style={{ color: '#92400e', fontWeight: 600 }}>Email Imports</a>,
              confirm the mailbox has a <code>*X-Report*</code> → <code>x_report</code> rule (this is a default rule on a freshly configured mailbox,
              so if it's missing it may have been edited out), AND confirm with b2bsoft that the X-Report is actually
              <em> scheduled</em> to be emailed to that inbox daily — that's a separate step from the mailbox rule itself.
            </li>
          </ol>
          <div style={{ fontSize: 12, color: '#92400e', marginTop: 6 }}>
            See <a href="/closing/readiness" style={{ color: '#92400e', fontWeight: 600 }}>Closing → Readiness</a> for the full per-tenant setup check.
          </div>
        </div>
      )}
      {!loading && !data?.error && xReportEver && (summary.unmappedX > 0 || summary.unmappedS > 0) && (
        <div className="card" style={{ padding: '10px 14px', marginBottom: 14, border: '1px solid #f59e0b', background: '#fffbeb', fontSize: 13, color: '#78350f' }}>
          {summary.unmappedX > 0 && <>⚠ {fmt(summary.unmappedX)} of the selected range's X-report tenders used a raw label this tenant's mapping doesn't recognize. </>}
          {summary.unmappedS > 0 && <>⚠ {fmt(summary.unmappedS)} of the selected range's sales-transaction tenders used a raw label this tenant's mapping doesn't recognize. </>}
          See the ⚠ per-store note below. Map it on <a href="/closing/tender-config" style={{ color: '#92400e', fontWeight: 600 }}>Tender Setup</a> so
          it's bucketed instead of sitting outside the table.
        </div>
      )}
      {data?.range_capped && (
        <div className="card" style={{ padding: '8px 14px', marginBottom: 14, border: '1px solid #f59e0b', background: '#fffbeb', fontSize: 12, color: '#78350f' }}>
          ⚠ The selected range spans {data.dates_total} days — showing the most recent {days.length} (this report replays real per-day report legs, so it's bounded). Narrow the date range to see earlier days.
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12, fontSize: 12 }}>
        <Src label="Daily Closing" ok={sp.closing} />
        <Src label="POS X-report" ok={sp.x_report} />
        <Src label="Sales Transactions" ok={sp.sales} />
        <Src label="Bank Deposit" ok={sp.bank_deposit} />
      </div>

      {/* RULE FIVE core set — store(s)/market(s)/date-range. No rep dimension in this per-store recon
          dataset (same as the sibling tender-recon page). Market/store render via the shared cascade-
          checkbox <MarketStorePicker> (OWNER DIRECTIVE 2026-08-04). */}
      <StandardFilterBar
        value={filt} onChange={setFilt} periodMode="range"
        show={{ reps: false, stores: false, markets: false }}
        storeOptions={opts.stores} marketOptions={opts.markets}
        storeLabel="Stores…" marketLabel="Markets…"
        right={
          <>
            <MarketStorePicker
              stores={storesForCascade}
              selectedMarkets={filt.markets} onMarketsChange={ids => setFilt(f => ({ ...f, markets: ids }))}
              selectedStores={filt.stores} onStoresChange={ids => setFilt(f => ({ ...f, stores: ids }))}
            />
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={onlyMismatch} onChange={e => setOnlyMismatch(e.target.checked)} /> Stores with a mismatch only
            </label>
          </>
        }
      />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          {/* Selection totals — ADDENDUM (owner 2026-08-03). Reflects the FULL filtered selection
              (store/market × date range), live. Net figures can hide offsetting errors across stores/
              days, so a gross (absolute, never netted) figure + a mismatched-store-day count sit right
              next to them. */}
          <div className="card" style={{ padding: '12px 16px' }}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>
              Selection totals — {opts.stores.length && filt.stores.length ? `${filt.stores.length} of ${opts.stores.length} store(s)` : `all ${opts.stores.length || summary.total} store(s)`}, {from === to ? from : `${from} → ${to}`} ({summary.total} store-day{summary.total === 1 ? '' : 's'})
            </div>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <Tile label="Daily Closing" value={fmt(summary.closing)} />
              <Tile label="POS X-report" value={fmt(summary.xrep)} sub={`net Δ vs closing ${summary.netCX >= 0 ? '+' : ''}${fmt(summary.netCX)}`}
                accent={Math.abs(summary.netCX) > 1 ? '#b91c1c' : '#15803d'} />
              <Tile label="Sales Transactions" value={fmt(summary.sales)} sub={`net Δ vs X-report ${summary.netXS >= 0 ? '+' : ''}${fmt(summary.netXS)}`}
                accent={Math.abs(summary.netXS) > 1 ? '#b91c1c' : '#15803d'} />
              <Tile label="Gross |Δ| closing↔X-report" value={fmt(summary.grossCX)} sub="NOT netted — offsetting errors don't hide here" accent={summary.grossCX > 1 ? '#b91c1c' : '#15803d'} />
              <Tile label="Gross |Δ| X-report↔sales" value={fmt(summary.grossXS)} sub="NOT netted" accent={summary.grossXS > 1 ? '#b91c1c' : '#15803d'} />
              <Tile label="Store-days w/ a mismatch" value={`${summary.mismatchDays} / ${summary.total}`} accent={summary.mismatchDays > 0 ? '#b91c1c' : '#15803d'} />
            </div>
            {(summary.netCX === 0 || Math.abs(summary.netCX) <= 1) && summary.grossCX > 1 && (
              <div style={{ fontSize: 12, color: '#b91c1c', marginTop: 8, fontWeight: 600 }}>
                ⚠ The net closing↔X-report variance looks clean, but the gross (absolute) figure above is {fmt(summary.grossCX)} —
                offsetting over/under days are netting out. Check the mismatched store-days below.
              </div>
            )}
            {note && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>ℹ️ {note}</div>}
          </div>

          {shown.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No tender data for {from === to ? from : `${from} → ${to}`}{onlyMismatch ? ' (no mismatches)' : ''}.</div>
          ) : grouped.map(([d, storesForDay]) => (
            <div key={d} style={{ display: 'grid', gap: 12 }}>
              {grouped.length > 1 && (
                <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ padding: '3px 10px', borderRadius: 20, background: 'var(--surface2)', border: '1px solid var(--border)' }}>📅 {d}</span>
                  <span style={{ fontWeight: 400, color: 'var(--text3)' }}>{storesForDay.length} store(s)</span>
                </div>
              )}
              {storesForDay.map((s) => (
                <div key={`${d}-${s.store_code}`} className="card" style={{ padding: 0, overflow: 'auto' }}>
                  <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 700, fontSize: 14 }}>{s.store_address}</span>
                    <span style={{ fontSize: 12, color: 'var(--text3)' }}>
                      totals — closing {fmt(s.totals.closing)} · X-report {fmt(s.totals.x_report)} · sales {fmt(s.totals.sales)}
                      {s.bank_deposit?.has_deposit && (
                        <> · bank deposit {fmt(s.bank_deposit.deposited)}
                          {s.bank_deposit.declared != null && <> vs {s.bank_deposit.match_target.replace('_', ' ')} {fmt(s.bank_deposit.declared)}</>}
                          {s.bank_deposit.flag
                            ? <span style={{ color: '#b91c1c', fontWeight: 700 }}> ⚠ {s.bank_deposit.var! >= 0 ? '+' : ''}{fmt(s.bank_deposit.var)}</span>
                            : <span style={{ color: '#15803d' }}> ✓</span>}
                          {s.bank_deposit.any_mismatch_flag && <span style={{ color: '#b91c1c' }}> · OCR flagged</span>}
                        </>
                      )}
                      {!s.bank_deposit?.has_deposit && <span> · bank deposit — not recorded</span>}
                      {(s.x_report_unmapped?.amount || 0) > 0 && (
                        <span style={{ color: '#b91c1c', fontWeight: 700 }} title={`Raw label(s): ${(s.x_report_unmapped?.raw_labels || []).join(', ')}`}>
                          {' '}· ⚠ {fmt(s.x_report_unmapped!.amount)} unmapped X-report
                        </span>
                      )}
                      {(s.sales_unmapped?.amount || 0) > 0 && (
                        <span style={{ color: '#b91c1c', fontWeight: 700 }} title={`Raw label(s): ${(s.sales_unmapped?.raw_labels || []).join(', ')}`}>
                          {' '}· ⚠ {fmt(s.sales_unmapped!.amount)} unmapped sales
                        </span>
                      )}
                    </span>
                  </div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 620 }}>
                    <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                      <th style={{ textAlign: 'left', padding: '7px 12px' }}>Tender</th>
                      <th style={{ textAlign: 'right', padding: '7px 12px' }}>Daily Closing</th>
                      <th style={{ textAlign: 'right', padding: '7px 12px' }}>X-report</th>
                      <th style={{ textAlign: 'right', padding: '7px 12px' }}>Sales Transactions</th>
                      <th style={{ textAlign: 'center', padding: '7px 12px' }}>Match</th>
                    </tr></thead>
                    <tbody>
                      {s.tenders.map((t) => (
                        <tr key={t.tender} style={{ borderTop: '1px solid var(--border)', background: t.match ? undefined : '#fffafa' }}>
                          <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{t.label}</td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(t.closing)}</td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(t.x_report)}</td>
                          <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>
                            <button onClick={() => setDrill({ date: s.date, store: s.store_code, tender: t.tender, label: t.label, storeName: s.store_address })}
                              style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 13, textDecoration: 'underline', padding: 0 }}>
                              {fmt(t.sales)}
                            </button>
                          </td>
                          <td style={{ padding: '7px 12px', textAlign: 'center', fontSize: 12 }}>
                            {t.match ? <span style={{ color: '#15803d' }}>✓</span> : <span style={{ color: '#b91c1c', fontWeight: 700 }}>⚠</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {drill && <DrillModal drill={drill} onClose={() => setDrill(null)} />}
    </div>
  )
}

function Tile({ label, value, sub, accent }: { label: string; value: any; sub?: string; accent?: string }) {
  return (
    <div className="card" style={{ padding: '10px 14px', minWidth: 150 }}>
      <div style={{ fontSize: 10, color: 'var(--text2)', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, marginTop: 2, color: accent || 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function Src({ label, ok }: { label: string; ok?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 10px', borderRadius: 20, border: '1px solid var(--border)', background: ok ? '#e7f6ec' : 'var(--surface2)' }}>
      <span style={{ color: ok ? '#15803d' : 'var(--text3)' }}>{ok ? '●' : '○'}</span>
      {label}{ok ? '' : ' — not loaded'}
    </span>
  )
}

function DrillModal({ drill, onClose }: { drill: Drill; onClose: () => void }) {
  const [rows, setRows] = useState<any[] | null>(null)
  const [total, setTotal] = useState(0)
  useEffect(() => {
    api(`/api/v1/closing/tender-drilldown?date=${drill.date}&store=${encodeURIComponent(drill.store)}&tender=${drill.tender}`)
      .then(r => { setRows(r?.rows || []); setTotal(r?.total || 0) }).catch(() => setRows([]))
  }, [drill])
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 16 }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{ padding: 0, maxWidth: 820, width: '100%', maxHeight: '85vh', overflow: 'auto' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15 }}>{drill.label} — {drill.storeName}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{drill.date} · sales transactions under this tender · total {fmt(total)}</div>
          </div>
          <button className="btn btn-secondary" onClick={onClose}>✕ Close</button>
        </div>
        {rows === null ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
              <th style={{ textAlign: 'left', padding: '7px 12px' }}>Trans ID</th>
              <th style={{ textAlign: 'left', padding: '7px 12px' }}>Salesperson</th>
              <th style={{ textAlign: 'left', padding: '7px 12px' }}>Product</th>
              <th style={{ textAlign: 'left', padding: '7px 12px' }}>Raw tender</th>
              <th style={{ textAlign: 'right', padding: '7px 12px' }}>Amount</th>
            </tr></thead>
            <tbody>
              {rows.map((r: any, i: number) => (
                <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '6px 12px', fontSize: 12 }}>{r.trans_id || '—'}</td>
                  <td style={{ padding: '6px 12px', fontSize: 12 }}>{r.salesperson || '—'}</td>
                  <td style={{ padding: '6px 12px', fontSize: 12 }}>{r.product_desc || '—'}</td>
                  <td style={{ padding: '6px 12px', fontSize: 12, color: 'var(--text3)' }}>{r.tender_type || '—'}</td>
                  <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(r.amount)}</td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={5} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No transactions under this tender for {drill.date}.</td></tr>}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
