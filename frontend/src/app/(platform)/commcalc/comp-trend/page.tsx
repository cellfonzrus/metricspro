'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, isStandardFilterActive, type StandardFilterValue } from '@/lib/standard-filters'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'
import { narrowPanel, screenKey, agreesToTheCent } from '@/lib/drill-narrow'

// WHERE THE BY-REP TILES CAME FROM (index §CORE-17.3q). Declaration:
// commcalc/exec_month_drilldown.py. The figures are read out of what the last commission
// calculation WROTE DOWN for each month, never out of the sales it read (§CORE-17.3j).
//
// `lib/drill-narrow` IS WIRED HERE, and harness_batch17_drilldown.py §F fails the build if it stops
// being: the standard filter bar on the BY-REP view is applied in the BROWSER (it narrows each
// person's per-month attribution points and re-sums them), and both tiles are a client `reduce` over
// the surviving rows. So each panel is narrowed to that same selection and each tile is read OFF its
// narrowed panel rather than reduced a second time — one fold for the figure and its evidence.
//
// THE BY-ACCOUNT VIEW OFFERS NO DRILL AT ALL, with four different reasons: its four month tiles are
// one column of ONE served month row with nothing folded per account beneath them, the delta is a
// difference taken at the TOTAL (§CORE-17.3k) and its percentage a quotient (§CORE-17.3h), the
// account count is over a set the report never enumerates, and "Comp lost to dips" is added
// ENTIRELY IN THE BROWSER over rows the browser filtered, so the producer folds no figure for a
// panel to explain. All four are written up in the declaration's header.
const REP_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Stores', get: (r: DrillRow) => String(r.fields?.['stores'] ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.['stores'] ?? '') },
  { header: 'Months paid', get: (r: DrillRow) => String(r.fields?.['months'] ?? '—'),
    sortValue: (r: DrillRow) => Number(r.fields?.['months'] ?? 0) },
]

export default function CompTrendPage() {
  // 'rep' = the commission WE pay each rep (rep_commissions.total_payout) — what "commission being
  // paid to the sales rep" means. 'account' = the legacy account-level carrier-comp trend.
  const [view, setView] = useState<'rep' | 'account'>('rep')
  const [months, setMonths] = useState(6)
  const [storeFilter, setStoreFilter] = useState('')   // account-view free-text (accounts have no store_mapping market)
  // RULE FIVE (§3d) standard bar for the per-REP view — store(s)/market/rep(s) multi over the loaded
  // org-scoped rep rows (period comes from the Window selector, so period is omitted from the bar).
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [monthFilter, setMonthFilter] = useState('')
  const [data, setData] = useState<any>(null)
  const [repData, setRepData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  function load() {
    setLoading(true)
    const url = view === 'rep'
      ? `/api/v1/commcalc/comp/rep-pay-trend?months=${months}&org_id=${ORG_ID}`
      : `/api/v1/commcalc/comp/residual-trend?months=${months}&org_id=${ORG_ID}`
    api(url)
      .then((d) => view === 'rep' ? setRepData(d) : setData(d))
      .catch((e) => view === 'rep' ? setRepData({ error: e?.message || String(e) }) : setData({ error: e?.message || String(e) }))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [months, view])

  const totals = data?.totals_by_month || []
  const allDips = data?.dips || []
  const dips = allDips.filter((d: any) =>
    (!monthFilter || d.period === monthFilter) &&
    (!storeFilter || (d.store || '').toLowerCase().includes(storeFilter.toLowerCase()) ||
                     (d.business_name || '').toLowerCase().includes(storeFilter.toLowerCase())))

  const latest = totals[totals.length - 1] || {}
  const lostThisMonth = dips.filter((d: any) => !monthFilter || d.period === monthFilter)
    .reduce((s: number, d: any) => s + Math.abs(d.delta || 0), 0)
  const vanished = dips.filter((d: any) => d.vanished).length

  // ── per-rep view (the commission WE pay each rep) ──
  const repMonths: string[] = repData?.months || []
  const repsAll: any[] = repData?.reps || []
  const norm = (v: any) => (v == null ? '' : String(v)).trim()
  // Options come from the per-month POINTS (a floating rep spans multiple stores/markets across the
  // window), not one collapsed store per rep — pick-don't-type over the org-scoped rows (RULE THREE/FIVE).
  const repOpts = useMemo(() => {
    const stores = new Set<string>(), markets = new Set<string>(), repsM = new Set<string>()
    for (const r of repsAll) {
      if (norm(r.rep)) repsM.add(norm(r.rep))
      for (const pt of (r.points || [])) {
        if (norm(pt.store)) stores.add(norm(pt.store))
        if (norm(pt.market)) markets.add(norm(pt.market))
      }
    }
    return { stores: [...stores].sort(), markets: [...markets].sort(),
             reps: [...repsM].sort().map(id => ({ id, label: id })) }
  }, [repsAll])
  // FIX 4 — PER-MONTH attribution. A store/market filter narrows the MONTHS within each rep (only the
  // months worked at that store/market count), never the whole rep by one collapsed store. Totals re-sum
  // from the narrowed months; a rep with no surviving month drops out. The table, tiles AND export
  // (WYSIWYG §3c) all read this narrowed set. With no store/market filter, by_period/total are unchanged.
  const reps = useMemo(() => {
    const storeSet = new Set(filt.stores.map(norm)), marketSet = new Set(filt.markets.map(norm)), repSet = new Set(filt.reps.map(norm))
    const hasStore = storeSet.size > 0, hasMarket = marketSet.size > 0, hasRep = repSet.size > 0
    const out: any[] = []
    for (const r of repsAll) {
      if (hasRep && !repSet.has(norm(r.rep))) continue
      const points: any[] = r.points || []
      if (!hasStore && !hasMarket) {
        const seen = new Set<string>()
        for (const pt of points) if (norm(pt.store)) seen.add(norm(pt.store))
        out.push({ ...r, _stores: [...seen].sort(), _pts: points })   // by_period/total untouched (byte-identical)
        continue
      }
      const kept = points.filter(pt =>
        (!hasStore || storeSet.has(norm(pt.store))) && (!hasMarket || marketSet.has(norm(pt.market))))
      if (kept.length === 0) continue   // this rep worked none of the selected stores/markets → drop
      const by_period: Record<string, number> = {}
      const seen = new Set<string>()
      let total = 0
      for (const pt of kept) {
        by_period[pt.period] = (by_period[pt.period] || 0) + (pt.pay || 0)
        total += pt.pay || 0
        if (norm(pt.store)) seen.add(norm(pt.store))
      }
      out.push({ ...r, by_period, total: Math.round(total * 100) / 100, _stores: [...seen].sort(), _pts: kept })
    }
    return out.sort((a, b) => (b.total || 0) - (a.total || 0))
  }, [repsAll, filt])
  const repFilterActive = isStandardFilterActive(filt)
  const latestRepMonth = repMonths[repMonths.length - 1] || ''
  const repPerMonth = (m: string) => reps.reduce((s: number, r: any) => s + (r.by_period?.[m] || 0), 0)
  // The page's own fold over the rows it is showing — kept as the FALLBACK for a payload carrying no
  // panel, and as the figure a panel that cannot be narrowed honestly (a capped row list) leaves in
  // place. Where a panel IS served, each tile below reads the panel's number instead, so the figure
  // and its evidence are one fold.
  const reducedGrand = reps.reduce((s: number, r: any) => s + (r.total || 0), 0)
  const reducedLatest = repPerMonth(latestRepMonth)
  // THE PANELS, NARROWED TO THE ROWS ON SCREEN — the same selection `reps` is, matched on the row
  // key the server served with every drill row. A capped panel narrows to null and the drill is then
  // not offered at all, rather than a smaller number presented as the whole of one.
  // THE KEY IS THE GRAIN, and the grain here is the ATTRIBUTION POINT (rep x period x store), because
  // that is what the filter actually acts on: it keeps and drops a person's individual months, not the
  // person. A rep-level key could only ever drop a whole rep, so a panel narrowed on one would keep
  // months the screen is not showing and the figure would not be the tile (index §CORE-17.3l).
  const repKeys = new Set(reps.flatMap(r => (r._pts || []).map(
    (pt: { period?: string; store?: string }) =>
      screenKey({ rep: r.rep, period: pt.period, store: pt.store }))))
  const repKeep = (_r: DrillRow, k: string) => repKeys.has(k)
  const narrowedLatest = narrowPanel(repData?.drilldown?.['latest'], repKeep)
  const narrowedWindow = narrowPanel(repData?.drilldown?.['window'], repKeep)
  const latestRepTotal = agreesToTheCent(narrowedLatest || undefined, reducedLatest)
    ? Number(narrowedLatest?.figure?.amount ?? reducedLatest) : reducedLatest
  const repGrandTotal = agreesToTheCent(narrowedWindow || undefined, reducedGrand)
    ? Number(narrowedWindow?.figure?.amount ?? reducedGrand) : reducedGrand
  // THE OFFER RULE, ONE HOME: a drill is offered only where the narrowed panel agrees with the tile
  // to the cent — the shared gate, not a condition this page invented.
  const narrowedRepPanels: Record<string, DrillPanel> = {
    ...(narrowedLatest ? { latest: narrowedLatest } : {}),
    ...(narrowedWindow ? { window: narrowedWindow } : {}),
  }
  const repDrill = useDrillTile(narrowedRepPanels, (k, p) =>
    agreesToTheCent(p, k === 'latest' ? latestRepTotal : repGrandTotal))

  function buildRepPayload(): ExportPayload {
    return {
      title: 'Commission Paid per Rep', subtitle: `total_payout · last ${months} months${repFilterActive ? ' · filtered' : ''}`,
      filename: `commission-per-rep`,
      sheets: [{ name: 'By rep', rows: reps, columns: [
        { header: 'Rep', get: (r: any) => r.rep },
        { header: 'Store', get: (r: any) => (r._stores?.length ? r._stores.join(', ') : r.store) || '' },
        ...repMonths.map((m) => ({ header: m, get: (r: any) => r.by_period?.[m] || 0, money: true })),
        { header: 'Total', get: (r: any) => r.total, money: true },
      ] }],
    }
  }

  function buildPayload(): ExportPayload {
    return {
      title: 'Total Compensation Trend', subtitle: `last ${months} months${monthFilter ? ` · ${monthFilter}` : ''}${storeFilter ? ` · ${storeFilter}` : ''}`,
      filename: `total-compensation-trend`,
      sheets: [
        { name: 'By month', rows: totals, columns: [
          { header: 'Month', get: (r: any) => r.period },
          { header: 'Total Comp', get: (r: any) => r.total_comp ?? r.residual, money: true },
          { header: 'Residual (MI+ATU)', get: (r: any) => r.residual_mi_atu, money: true },
          { header: 'Accounts', get: (r: any) => r.accounts },
          { header: 'Δ vs prev', get: (r: any) => r.delta_vs_prev, money: true },
          { header: '% vs prev', get: (r: any) => r.pct_vs_prev },
        ] },
        { name: 'Dips', rows: dips, columns: [
          { header: 'Dipped in', get: (r: any) => r.period },
          { header: 'From month', get: (r: any) => r.prev_period },
          { header: 'Account', get: (r: any) => r.account_id },
          { header: 'Business', get: (r: any) => r.business_name },
          { header: 'Store', get: (r: any) => r.store },
          { header: 'Was', get: (r: any) => r.prev_residual, money: true },
          { header: 'Now', get: (r: any) => r.residual, money: true },
          { header: 'Drop', get: (r: any) => r.delta, money: true },
          { header: '%', get: (r: any) => r.pct },
          { header: 'Reason', get: (r: any) => r.reason },
        ] },
      ],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📊 Total Compensation</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {view === 'rep'
              ? <>The <strong>commission we actually pay each rep</strong> (the spiff stack × KPI tier = rep payout), month over month. Switch to <em>By account</em> for the carrier-compensation trend.</>
              : <>Month-over-month <strong>total carrier compensation</strong> per account from the Comprehensive Comp report (~95% promos + bounties = Commission + SPIFF, <em>not</em> residual). True <strong>Residual = MI + ATU</strong> shown alongside. A <strong>dip</strong> = comp that fell or an account that vanished.</>}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: view === 'rep' ? 'var(--accent)' : 'transparent', color: view === 'rep' ? 'white' : 'var(--text2)' }} onClick={() => setView('rep')}>By rep</button>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: view === 'account' ? 'var(--accent)' : 'transparent', color: view === 'account' ? 'white' : 'var(--text2)' }} onClick={() => setView('account')}>By account</button>
          </div>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Window
            <select className="select" value={months} onChange={e => setMonths(parseInt(e.target.value))} style={{ marginLeft: 6 }}>
              <option value={3}>3 months</option><option value={6}>6 months</option><option value={12}>12 months</option>
            </select>
          </label>
          {view === 'account' && <input className="select" placeholder="filter store / business…" value={storeFilter} onChange={e => setStoreFilter(e.target.value)} style={{ width: 180 }} />}
          {view === 'rep'
            ? (repData?.reps?.length ? <><ExportButtons payload={buildRepPayload} /><SendReportButton exportPayload={buildRepPayload} compact /></> : null)
            : (data?.totals_by_month ? <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></> : null)}
        </div>
      </div>

      {/* RULE FIVE standard bar — per-REP view (store(s)/market/rep(s) multi; period = the Window selector). */}
      {view === 'rep' && (repsAll.length > 0 || repFilterActive) && (
        <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false }}
          storeOptions={repOpts.stores} marketOptions={repOpts.markets} repOptions={repOpts.reps} repLabel="Reps…" />
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : view === 'rep' ? (
        repData?.error ? (
          <div className="card" style={{ padding: 16, color: 'var(--c-fg-b91c1c)' }}>Error: {repData.error}</div>
        ) : repData?.note ? (
          <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>{repData.note}</div>
        ) : (
          <div style={{ display: 'grid', gap: 16 }}>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <Tile label={`Paid to reps — ${latestRepMonth || 'latest'}`} value={fmt(latestRepTotal)} accent="#15803d"
                sub={<DrillTileLinks drills={repDrill.dl('latest', 'rows behind this')} open={repDrill.open} onToggle={repDrill.toggle} />} />
              <Tile label={`Total — last ${months} mo`} value={fmt(repGrandTotal)}
                sub={<DrillTileLinks drills={repDrill.dl('window', 'rows behind this')} open={repDrill.open} onToggle={repDrill.toggle} />} />
              {/* NO DRILL ON PURPOSE: the table below IS the list this counts, and "Top rep" is a
                  name rather than a figure. */}
              <Tile label="Reps paid" value={reps.length} />
              <Tile label="Top rep" value={reps[0]?.rep || '—'} />
            </div>
            {repDrill.panel && <DrillDownPanel panel={repDrill.panel} extraColumns={REP_DRILL_COLUMNS} />}
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
                Commission paid per rep (total payout) — newest month last; highest total first
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
                <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px' }}>Rep</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px' }}>Store</th>
                  {repMonths.map((m) => <th key={m} style={{ textAlign: 'right', padding: '8px 12px' }}>{m}</th>)}
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Total</th>
                </tr></thead>
                <tbody>
                  {reps.map((r: any) => (
                    <tr key={r.rep} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{r.rep}</td>
                      <td style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text3)' }}>{(r._stores?.length ? r._stores.join(', ') : r.store) || '—'}</td>
                      {repMonths.map((m) => <td key={m} style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{r.by_period?.[m] ? fmt(r.by_period[m]) : '—'}</td>)}
                      <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(r.total)}</td>
                    </tr>
                  ))}
                  {reps.length === 0 && <tr><td colSpan={repMonths.length + 3} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No commissions{storeFilter ? ' match the filter' : ' calculated yet'}.</td></tr>}
                </tbody>
                {reps.length > 0 && (
                  <tfoot><tr style={{ borderTop: '2px solid var(--border)', background: 'var(--surface2)' }}>
                    <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 700 }}>All reps</td>
                    <td />
                    {repMonths.map((m) => <td key={m} style={{ padding: '8px 12px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(repPerMonth(m))}</td>)}
                    <td style={{ padding: '8px 12px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(repGrandTotal)}</td>
                  </tr></tfoot>
                )}
              </table>
            </div>
          </div>
        )
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: 'var(--c-fg-b91c1c)' }}>Error: {data.error}</div>
      ) : data?.note ? (
        <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>{data.note}</div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Tile label={`Total comp — ${latest.period || 'latest'}`} value={fmt(latest.total_comp ?? latest.residual ?? 0)} />
            <Tile label={`Residual (MI+ATU) — ${latest.period || 'latest'}`} value={fmt(latest.residual_mi_atu || 0)} accent="#15803d" />
            <Tile label="Δ vs prior month" value={fmt(latest.delta_vs_prev || 0)} accent={(latest.delta_vs_prev || 0) < 0 ? '#b91c1c' : '#15803d'} />
            <Tile label="Active accounts" value={latest.accounts ?? 0} />
            <Tile label={`Comp lost to dips${monthFilter ? '' : ' (all shown)'}`} value={fmt(lostThisMonth)} accent="#b91c1c" />
            <Tile label="Accounts vanished" value={vanished} accent="#b45309" />
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>By month — total compensation vs. true residual (MI+ATU). Click a month to filter the dips below</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Month</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Total Comp</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }} title="From the carrier category map">Commission</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>SPIFF</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Reimb</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Residual (MI+ATU)</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Accounts</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Δ vs prev</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>% vs prev</th>
              </tr></thead>
              <tbody>
                {totals.map((r: any) => (
                  <tr key={r.period} onClick={() => setMonthFilter(monthFilter === r.period ? '' : r.period)}
                      style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', background: monthFilter === r.period ? 'var(--surface2,#eef2ff)' : undefined }}>
                    <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{r.period}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.total_comp ?? r.residual)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: 'var(--c-fg-16794a)' }}>{fmt(r.components?.COMMISSION || 0)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: 'var(--c-fg-b45309)' }}>{fmt(r.components?.SPIFF || 0)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: 'var(--c-fg-7c3aed)' }}>{fmt(r.components?.REIMBURSEMENT || 0)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: 'var(--c-fg-15803d)' }}>{fmt(r.residual_mi_atu || 0)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13 }}>{r.accounts}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: (r.delta_vs_prev ?? 0) < 0 ? 'var(--c-fg-b91c1c)' : ((r.delta_vs_prev ?? 0) > 0 ? 'var(--c-fg-15803d)' : 'var(--text3)') }}>{r.delta_vs_prev == null ? '—' : fmt(r.delta_vs_prev)}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right', fontSize: 13, color: (r.pct_vs_prev ?? 0) < 0 ? 'var(--c-fg-b91c1c)' : 'var(--text3)' }}>{r.pct_vs_prev == null ? '—' : `${r.pct_vs_prev}%`}</td>
                  </tr>
                ))}
                {totals.length === 0 && <tr><td colSpan={9} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No comp data.</td></tr>}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
              Residual dips{monthFilter ? ` — ${monthFilter}` : ''} ({dips.length}) — biggest drop first
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Dipped in</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Account</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Business / store</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Was</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Now</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Drop</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>%</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Why</th>
              </tr></thead>
              <tbody>
                {dips.map((d: any, i: number) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)', background: d.vanished ? 'var(--c-bg-fff7ed)' : 'var(--c-bg-fffafa)' }}>
                    <td style={{ padding: '6px 12px', fontSize: 12, fontWeight: 600 }}>{d.period}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{d.account_id || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{d.business_name || d.store || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(d.prev_residual)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right', color: d.residual === 0 ? '#b91c1c' : undefined }}>{fmt(d.residual)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right', color: 'var(--c-fg-b91c1c)', fontWeight: 600 }}>{fmt(d.delta)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, textAlign: 'right' }}>{d.pct}%</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{d.reason}</td>
                  </tr>
                ))}
                {dips.length === 0 && <tr><td colSpan={8} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No residual dips{monthFilter ? ` in ${monthFilter}` : ''}.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Tile({ label, value, accent, sub }: { label: string; value: any; accent?: string; sub?: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150 }}>
      <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: accent || 'var(--text)' }}>{value}</div>
      {sub}
    </div>
  )
}
