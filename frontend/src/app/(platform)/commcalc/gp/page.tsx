'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { SendReportButton } from '@/lib/send-report'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { TrendChart } from '@/components/TrendChart'
import { useColumnResize, ResizeHandle } from '@/lib/col-resize'
import EntityPicker from '@/components/EntityPicker'
import { optionsFromRows } from '@/lib/standard-filters'

const r2 = (n: number) => Math.round((n || 0) * 100) / 100
const shortPeriod = (p: string) => {
  const m = String(p || '').match(/^([A-Za-z]+)\s+(\d{4})$/)
  return m ? `${m[1].slice(0, 3)} '${m[2].slice(2)}` : p
}

interface StoreRow {
  store: string; store_code: string; market: string
  acc_gp: number; setup_gp: number; phone_sales: number; plan_gp: number; other_gp: number
  comm: number; reimb: number; mdf: number; chargeback: number; unmapped: number
  comp_comm: number; comp_reimb: number; comp_mdf: number
  // COMMISSION LEG SPLIT (owner 2026-08-04) — a decomposition of the columns above, never an addition:
  // comm_m1 + comm_m2_12 + comm_unsplit === comm, and likewise for comp_comm / mi / atu.
  comm_m1: number; comm_m2_12: number; comm_unsplit: number
  comp_comm_m1: number; comp_comm_m2_12: number; comp_comm_unsplit: number
  mi_m1: number; mi_m2_12: number; mi_unsplit: number
  atu_m1: number; atu_m2_12: number; atu_unsplit: number
  mi: number; atu: number; total_rev: number
  rep_pay: number; exp_total: number; net_phone_cost: number
  net_profit: number; net_excl_mdf: number; net_profit_target?: number; net_profit_attainment?: number
}

// The By-Rep view's row shape (data.rep_rows) — named so the export columns aren't `any`.
interface RepRow {
  rep: string; storeops_name?: string; store?: string
  acc_gp?: number; setup_gp?: number; phone_sales?: number; plan_gp?: number; comm_earned?: number
}

interface ColDef { key: string; label: string; group: string; bold?: boolean; red?: boolean; highlight?: boolean; leg?: boolean; title?: string }
// One row of the server's commission-leg card. `unsplit_fields` / `unsplit_why` are present only on the
// Commission row of a master-agent-fed org, naming the export columns that are not commission legs.
type LegSource = { key: string; label?: string; splits_on?: string; m1?: number; m2_12?: number
                   unsplit?: number; total?: number; identity_ok?: boolean
                   unsplit_fields?: string[]; unsplit_why?: string }

// Owner directive 2026-08-04: the Commission column is split into the 1st-month leg and the M2–M12
// trailing legs. They are SUB-columns of Commission (Commission itself stays, so nothing that read this
// report before reads differently) and are shown/hidden together by the 🧩 toggle — which the exports
// follow, per WYSIWYG. "Unsplit" only appears when the org actually has money whose source states no
// month-of-life, so a fully-mapped tenant sees exactly the two columns that were asked for.
const COLS_BASE: ColDef[] = [
  { key: 'acc_gp',       label: 'Acc GP',      group: 'Revenue' },
  { key: 'setup_gp',     label: 'Setup GP',    group: 'Revenue' },
  { key: 'phone_sales',  label: 'Phone Sales', group: 'Revenue' },
  { key: 'plan_gp',      label: 'Plan GP',     group: 'Revenue' },
  { key: 'other_gp',     label: 'Other',       group: 'Revenue' },
  { key: 'comm',         label: 'Commission',  group: 'Payments' },
  { key: 'comm_m1',      label: '· 1st Month',  group: 'Payments', leg: true,
    title: 'Commission received in the SAME month the number activated.' },
  { key: 'comm_m2_12',   label: '· M2–M12',     group: 'Payments', leg: true,
    title: 'Commission received for a number that activated in an EARLIER month (the trailing legs).' },
  { key: 'comm_unsplit', label: '· Unsplit',    group: 'Payments', leg: true,
    title: 'Commission whose source states no month-of-life, so it is honestly left unattributed. Map these labels on Commission Legs.' },
  { key: 'reimb',        label: 'Re-imb',      group: 'Payments' },
  { key: 'mdf',          label: 'MDF',         group: 'Payments' },
  { key: 'comp_comm',    label: 'Comp Comm',   group: 'Payments' },
  { key: 'comp_reimb',   label: 'Comp Rebate', group: 'Payments' },
  { key: 'comp_mdf',     label: 'Comp MDF',    group: 'Payments' },
  { key: 'chargeback',   label: 'Chargebacks', group: 'Payments' },
  { key: 'mi',           label: 'MI',          group: 'Payments' },
  { key: 'atu',          label: 'ATU',         group: 'Payments' },
  { key: 'total_rev',    label: 'Total Rev',   group: 'Summary', bold: true },
  { key: 'rep_pay',      label: '−Rep Pay',    group: 'Deductions', red: true },
  { key: 'exp_total',    label: '−Expenses',   group: 'Deductions', red: true },
  { key: 'net_phone_cost', label: '−Phone Cost', group: 'Deductions', red: true },
  { key: 'net_profit',   label: 'Net Profit',  group: 'Summary', bold: true, highlight: true },
  { key: 'net_profit_target', label: 'NP Target', group: 'Summary' },
  { key: 'net_excl_mdf', label: 'Excl. MDF',  group: 'Summary' },
]

// The four money columns the leg split decomposes, with the source that makes each splittable — shown
// verbatim on the page so nobody has to trust an unexplained number.
const LEG_SOURCE_ROWS: { key: string; label: string }[] = [
  { key: 'comm',      label: 'Commission received' },
  { key: 'comp_comm', label: 'Comp Comm' },
  { key: 'mi',        label: 'MI residual' },
  { key: 'atu',       label: 'ATU residual' },
]

export default function GPReportPage() {
  const { period } = usePeriod()
  const [view, setView] = useState<'store'|'rep'>('store')
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selStores, setSelStores] = useState<string[]>([])
  const [selReps, setSelReps] = useState<string[]>([])   // RULE FIVE rep(s) multi — applies to the By-Rep view
  const [data, setData] = useState<any>({})
  const [loading, setLoading] = useState(true)
  const [markets, setMarkets] = useState<string[]>([])
  const [gpTrend, setGpTrend] = useState<any>(null)   // month-over-month net-profit chart on top
  const [showComp, setShowComp] = useState(false)     // expand the 'Other' GP bucket department breakdown
  const [showLegs, setShowLegs] = useState(true)      // 🧩 1st-month vs M2–M12 commission sub-columns
  const [legTrend, setLegTrend] = useState<any>(null) // month-over-month M1 vs M2–M12 (owner 2026-08-04)
  const [legLadder, setLegLadder] = useState(false)   // expand the per-month-of-life ladder table
  const cw = useColumnResize()                          // auto-fit + user-resizable columns

  useEffect(() => {
    setLoading(true)
    api(`/api/v1/commcalc/gp/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then(d => {
        setData(d || {})
        if (d?.store_rows) {
          const mkts = [...new Set(d.store_rows.map((r: StoreRow) => r.market).filter(Boolean))].sort() as string[]
          setMarkets(mkts)
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [period])
  // Commission-leg trend — 12 months ending at the page's period (RULE FIVE: the period filter drives
  // it), refetched when the market/store filter changes so what you see is what the chart charts.
  useEffect(() => {
    const qs = new URLSearchParams({ period, months: '12', org_id: ORG_ID })
    if (selMarkets.length) qs.set('market', selMarkets.join(','))
    if (selStores.length) qs.set('store', selStores.join(','))
    api(`/api/v1/commcalc/commission-leg-trend?${qs.toString()}`).then(setLegTrend).catch(() => setLegTrend(null))
  }, [period, selMarkets, selStores])
  // Net-profit + revenue trend for the chart on top (cross-period, fetched once).
  useEffect(() => { api(`/api/v1/commcalc/gp-trend?months=6&org_id=${ORG_ID}`).then(setGpTrend).catch(() => {}) }, [])

  const gpTrendData = useMemo(() => (gpTrend?.months || []).map((p: string) => {
    let np = 0, rev = 0
    if (!selMarkets.length) { const c = (gpTrend?.company || []).find((x: any) => x.period === p); np = c?.net_profit || 0; rev = c?.total_rev || 0 }
    else (gpTrend?.stores || []).filter((s: any) => selMarkets.includes(s.market)).forEach((s: any) => { const pt = s.series.find((x: any) => x.period === p); np += pt?.net_profit || 0; rev += pt?.total_rev || 0 })
    return { name: shortPeriod(p), net_profit: r2(np), total_rev: r2(rev) }
  }), [gpTrend, selMarkets])

  // Commission-leg chart data (server already applied the market/store filter, so this is a straight map).
  const legTrendData = (legTrend?.company || []).map((c: any) => ({
    name: shortPeriod(c.period), m1: r2(c.m1 || 0), m2_12: r2(c.m2_12 || 0), unsplit: r2(c.unsplit || 0),
  }))
  const allRows: StoreRow[] = data.store_rows || []
  // The 'Unsplit' sub-column earns its place only when this org actually HAS money whose source states
  // no month-of-life; otherwise the owner gets exactly the two columns asked for.
  const anyUnsplit = allRows.some(r => Math.abs(r.comm_unsplit || 0) > 0.004)
  const COLS: ColDef[] = COLS_BASE.filter(c =>
    !c.leg ? true : (showLegs && (c.key !== 'comm_unsplit' || anyUnsplit)))
  const rows: StoreRow[] = allRows.filter(r => {
    if (selMarkets.length && !selMarkets.includes(r.market)) return false
    if (selStores.length && !selStores.includes(r.store)) return false
    return true
  })
  const allRepRows: any[] = (data.rep_rows || [])
  const repName = (r: any) => r.storeops_name || r.rep
  // RULE FIVE rep(s) picker options — from the already-org-scoped rep rows (pick-don't-type §3b).
  const repOpts = useMemo(() => optionsFromRows(allRepRows, { rep: repName }).reps, [allRepRows])
  // The By-Rep view honors the store filter (existing) AND the new rep-multi (AND-composed).
  const repRows: any[] = allRepRows.filter((r: any) =>
    (!selStores.length || selStores.some(s => r.store?.includes(s.split(' ')[0]))) &&
    (!selReps.length || selReps.includes(repName(r))))
  const totals: any = {}
  // Totalled over COLS_BASE (the FULL set), not the visible COLS — the leg tiles/exports must have their
  // numbers even while the 🧩 sub-columns are collapsed.
  COLS_BASE.forEach(c2 => { totals[c2.key] = rows.reduce((s, r) => s + ((r as any)[c2.key] || 0), 0) })
  // Per-source leg decomposition of the CURRENTLY FILTERED rows. `total` is each source's own existing
  // column, unchanged; `ok` is the identity the owner asked to be able to trust — the parts must add
  // back to it. It is recomputed here from the rows on screen (not taken from the server) precisely so
  // the filtered view proves itself.
  const legRows = LEG_SOURCE_ROWS.map(({ key, label }) => {
    const m1 = totals[`${key}_m1`] || 0, m2 = totals[`${key}_m2_12`] || 0, un = totals[`${key}_unsplit`] || 0
    const tot = totals[key] || 0
    return { key, label, m1, m2, un, tot, ok: Math.abs(r2(m1 + m2 + un) - r2(tot)) < 0.01 }
  })
  const legOk = legRows.every(l => l.ok)
  const legCfg = data?.commission_legs?.config
  const legSources: LegSource[] = data?.commission_legs?.sources || []
  const legSourceHow: Record<string, string> = Object.fromEntries(
    legSources.map(x => [x.key, x.splits_on || '']))
  // When the Commission column is fed by the VidaPay/master-agent export there are no carrier LABELS to
  // map — the leg is the column name, and the activation-order margin columns simply are not commission
  // legs (owner 2026-08-04). The server names those columns so this page can explain the Unsplit figure
  // instead of pointing at a label-mapping screen that cannot resolve it.
  const commSrc = legSources.find(x => x.key === 'comm')
  const commUnsplitFields: string[] = commSrc?.unsplit_fields || []
  const commUnsplitWhy: string = commSrc?.unsplit_why || ''

  function Cell({ val, col }: { val: number; col: ColDef }) {
    const color = col.highlight
      ? val >= 0 ? 'var(--green)' : 'var(--red)'
      : col.red ? 'var(--red)' : undefined
    return (
      <td style={{ textAlign: 'right', fontWeight: col.bold ? 700 : 400, color, fontSize: 12, padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
        {fmt(val)}
      </td>
    )
  }

  function exportCSV() {
    const head = ['Store', 'Market', ...COLS.map(c => c.label)].join(',')
    const csvRows = rows.map(r => [
      `"${r.store}"`, `"${r.market}"`,
      ...COLS.map(c => r[c.key as keyof StoreRow]?.toString() || '0'),
    ].join(','))
    const a = document.createElement('a')
    a.href = 'data:text/csv,' + encodeURIComponent([head, ...csvRows].join('\n'))
    a.download = `gp-report-${period.replace(' ', '-')}.csv`
    a.click()
  }

  // WYSIWYG (§3c/§3d) — Send used to take the server report-key path (reportKey "gp", period only), so
  // notify/report_registry._gp re-queried view="store" for the WHOLE org: the market chips, the store
  // filter, the rep(s) picker and the By-Store/By-Rep toggle were all dropped. Excel · PDF · Print ·
  // Send now render from the SAME filtered rows the table is showing.
  const gpFilterDesc = () => [
    selMarkets.length && `markets: ${selMarkets.join(', ')}`,
    selStores.length && `stores: ${selStores.join(', ')}`,
    view === 'rep' && selReps.length && `reps: ${selReps.join(', ')}`,
    view === 'store' && !showLegs && 'commission legs collapsed',
  ].filter(Boolean).join(' · ')

  function buildPayload(): ExportPayload {
    const fd = gpFilterDesc()
    if (view === 'rep') {
      return {
        title: 'Gross Profit Report — By Rep',
        subtitle: `${period} · ${repRows.length}${repRows.length !== allRepRows.length ? ` of ${allRepRows.length}` : ''} reps${fd ? ` · ${fd}` : ''}`,
        filename: `gp-report-by-rep-${period.replace(/ /g, '-')}${fd ? '-filtered' : ''}`.toLowerCase(),
        sheets: [{ name: 'By Rep', rows: repRows, columns: [
          { header: 'Rep', get: (r: RepRow) => r.storeops_name || r.rep },
          { header: 'Store', get: (r: RepRow) => r.store || '' },
          { header: 'Acc GP', get: (r: RepRow) => r.acc_gp, money: true },
          { header: 'Setup GP', get: (r: RepRow) => r.setup_gp, money: true },
          { header: 'Phone Sales', get: (r: RepRow) => r.phone_sales, money: true },
          { header: 'Plan GP', get: (r: RepRow) => r.plan_gp, money: true },
          { header: 'Comm Earned', get: (r: RepRow) => r.comm_earned, money: true },
        ] }],
      }
    }
    // WYSIWYG (§3c): the By-Store sheet follows the VISIBLE columns — so the 1st-Month / M2–M12 split
    // exports exactly as it is on screen (and stays out when the 🧩 toggle is collapsed). A second sheet
    // always carries the full per-source leg decomposition with its identity check, because that is the
    // number the owner asked to be able to check, and it must survive an export.
    const legSheet = {
      name: 'Commission legs',
      rows: [...legRows, { key: '_note', label: '', m1: 0, m2: 0, un: 0, tot: 0, ok: true }],
      columns: [
        { header: 'Source', get: (r: any) => r.key === '_note' ? '1st Month = received in the month the number activated · M2–M12 = received later for an already-activated number' : r.label },
        { header: 'Splits on', get: (r: any) => r.key === '_note' ? '' : (legSourceHow[r.key] || '') },
        { header: '1st Month', get: (r: any) => r.key === '_note' ? null : r.m1, money: true },
        { header: 'M2-M12', get: (r: any) => r.key === '_note' ? null : r.m2, money: true },
        { header: 'Unsplit', get: (r: any) => r.key === '_note' ? null : r.un, money: true },
        { header: 'Total (unchanged)', get: (r: any) => r.key === '_note' ? null : r.tot, money: true },
        { header: 'Parts add back?', get: (r: any) => r.key === '_note' ? '' : (r.ok ? 'yes' : 'NO — report this') },
      ] as ExportColumn[],
    }
    return {
      title: 'Gross Profit Report',
      subtitle: `${period} · ${rows.length}${rows.length !== allRows.length ? ` of ${allRows.length}` : ''} stores · net ${fmt(totals.net_profit || 0)} · commission ${fmt(totals.comm || 0)} = 1st month ${fmt(totals.comm_m1 || 0)} + M2–M12 ${fmt(totals.comm_m2_12 || 0)}${(totals.comm_unsplit || 0) ? ` + unsplit ${fmt(totals.comm_unsplit)}` : ''}${fd ? ` · ${fd}` : ''}`,
      filename: `gp-report-${period.replace(/ /g, '-')}${fd ? '-filtered' : ''}`.toLowerCase(),
      sheets: [{ name: 'By Store', rows, columns: [
        { header: 'Store', get: (r: StoreRow) => r.store },
        { header: 'Market', get: (r: StoreRow) => r.market || '' },
        ...COLS.map(c => ({ header: c.label, get: (r: StoreRow) => r[c.key as keyof StoreRow], money: true } as ExportColumn)),
      ] }, legSheet],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Gross Profit Report</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · {rows.length} stores · Net: <strong style={{ color: totals.net_profit >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmt(totals.net_profit || 0)}</strong>
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
            {markets.map(m => (
              <button key={m} onClick={() => setSelMarkets(s => s.includes(m) ? s.filter(x => x !== m) : [...s, m])}
                className="btn" style={{
                  fontSize: 12, padding: '4px 10px',
                  background: selMarkets.includes(m) ? 'var(--accent)' : 'var(--surface2)',
                  color: selMarkets.includes(m) ? 'white' : 'var(--text2)',
                }}>
                {m}
              </button>
            ))}
            {(selMarkets.length > 0 || selStores.length > 0) && (
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 10px' }}
                onClick={() => { setSelMarkets([]); setSelStores([]) }}>✕ Clear</button>
            )}
          </div>
          <select className="select" value="" onChange={e => {
            const v = e.target.value
            if (v && !selStores.includes(v)) setSelStores(s => [...s, v])
          }}>
            <option value="">+ Add store filter</option>
            {allRows.map(r => <option key={r.store} value={r.store}>{r.store.substring(0, 40)}</option>)}
          </select>
          <div style={{ display: 'flex', background: 'var(--surface2)', padding: 3, borderRadius: 8, gap: 3 }}>
            {(['store', 'rep'] as const).map(v => (
              <button key={v} onClick={() => setView(v)} className="btn" style={{
                background: view === v ? 'white' : 'transparent',
                color: view === v ? 'var(--accent)' : 'var(--text2)',
                fontSize: 13, boxShadow: view === v ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
              }}>
                {v === 'store' ? '🏪 By Store' : '👤 By Rep'}
              </button>
            ))}
          </div>
          {/* RULE FIVE rep(s) multi — meaningful only in the By-Rep view (store rows carry no rep). */}
          {view === 'rep' && repOpts.length > 0 && (
            <EntityPicker multi options={repOpts} value={selReps} onChange={setSelReps} placeholder="Reps…" width={170} ariaLabel="Filter by rep" />
          )}
          {view === 'store' && (
            <button className="btn btn-secondary" onClick={() => setShowLegs(v => !v)}
              title="Split the Commission column into 1st-month and M2–M12 legs"
              style={{ fontSize: 12, background: showLegs ? 'var(--accent)' : undefined, color: showLegs ? 'white' : undefined }}>
              🧩 Legs
            </button>
          )}
          <button className="btn btn-secondary" onClick={exportCSV}>📥 CSV</button>
          <ExportButtons payload={buildPayload} compact />
          <SendReportButton exportPayload={buildPayload} title={view === 'rep' ? 'Gross Profit Report — By Rep' : 'Gross Profit Report'} compact />
        </div>
      </div>

      {/* Summary cards. The two commission-leg tiles (owner 2026-08-04) sit alongside the originals —
          nothing was removed, and they decompose the Commission column, not Total Revenue. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12, marginBottom: 20 }}>
        {([
          { label: 'Total Revenue', val: totals.total_rev, icon: '💰' },
          { label: '1st Month Commission', val: totals.comm_m1, icon: '①', sub: 'received in the activation month' },
          { label: 'M2–M12 Commission', val: totals.comm_m2_12, icon: '🔁', sub: 'received for an already-active number' },
          { label: 'Rep Incentives', val: totals.rep_pay, icon: '👥', red: true },
          { label: 'Store Expenses', val: totals.exp_total, icon: '🏪', red: true },
          { label: 'Net Profit', val: totals.net_profit, icon: '📊', highlight: true },
        ] as { label: string; val: number; icon: string; sub?: string; red?: boolean; highlight?: boolean }[])
        .map(({ label, val, icon, sub, red, highlight }) => (
          <div key={label} className="card">
            <div style={{ fontSize: 22 }}>{icon}</div>
            <div style={{ fontSize: 20, fontWeight: 700, marginTop: 8,
              color: highlight ? (val >= 0 ? 'var(--green)' : 'var(--red)') : red ? 'var(--red)' : 'var(--accent)' }}>
              {fmt(val || 0)}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{label}</div>
            {sub && <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 2, lineHeight: 1.3 }}>{sub}</div>}
          </div>
        ))}
      </div>

      {/* GP bucket transparency (owner 2026-07-24: "'Other' does not detail any information"). Shows the
          departments currently landing in the 'Other' bucket — how many lines + $ — with a link to map
          them into the right GP bucket. Fed by the /gp response's unmapped_departments. */}
      {!loading && (data.unmapped_departments?.length > 0) && (
        <div className="card" style={{ padding: '12px 14px', marginBottom: 20, border: '1px solid #fde047', background: '#fffbeb' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#92400e' }}>
              🗂️ &ldquo;Other&rdquo; GP breakdown — {data.unmapped_departments.length} department(s) are unclassified
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <a href="/commcalc/gp-category-map" style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)' }}>Map them → (GP Category Map)</a>
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '2px 10px' }} onClick={() => setShowComp(s => !s)}>{showComp ? 'Hide' : 'Show'} detail</button>
            </div>
          </div>
          <div style={{ fontSize: 11, color: '#92400e', marginTop: 4 }}>
            These departments&apos; sales roll into the single &ldquo;Other&rdquo; column. Assign each to Accessory / Device / Plan (or exclude) on the GP Category Map so the report details them properly.
          </div>
          {/* ⑦ (Gate-1 follow-up 2026-07-25): this breakdown counts only COUNTABLE sale lines — the same
              voided / Return / unattributed skip rules the shared sales aggregation uses — so its $ ties out
              to the Sales Report instead of quietly including voided + returned lines. What was skipped is
              stated here rather than hidden; the GP money columns above still count every line. */}
          {(data.bucket_composition_excluded?.total?.lines > 0) && (
            <div style={{ fontSize: 11, color: '#92400e', marginTop: 4, opacity: 0.9 }}>
              Counts exclude {data.bucket_composition_excluded.total.lines} non-countable line(s) —{' '}
              {data.bucket_composition_excluded.voided?.lines || 0} voided ·{' '}
              {data.bucket_composition_excluded.return?.lines || 0} return ·{' '}
              {data.bucket_composition_excluded.unattributed?.lines || 0} unattributed (no rep/admin) —
              worth {fmt(data.bucket_composition_excluded.total.ext_price || 0)} ext price /{' '}
              {fmt(data.bucket_composition_excluded.total.gp || 0)} GP. The GP columns above still include them.
            </div>
          )}
          {showComp && (
            <div className="table-wrapper" style={{ marginTop: 10 }}>
              <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                <thead>
                  <tr style={{ fontSize: 11, color: 'var(--text3)', textAlign: 'left' }}>
                    <th style={{ padding: '3px 8px' }}>Department</th>
                    <th style={{ padding: '3px 8px', textAlign: 'right' }}>Lines</th>
                    <th style={{ padding: '3px 8px', textAlign: 'right' }}>Ext Price</th>
                    <th style={{ padding: '3px 8px', textAlign: 'right' }}>GP</th>
                    <th style={{ padding: '3px 8px', textAlign: 'right' }} title="Voided / Return / unattributed lines — excluded from the counts on the left, shown so nothing is hidden">Excluded</th>
                  </tr>
                </thead>
                <tbody>
                  {data.unmapped_departments.map((d: any) => (
                    <tr key={d.department} style={{ fontSize: 12, borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '3px 8px' }}>{d.department}</td>
                      <td style={{ padding: '3px 8px', textAlign: 'right' }}>{d.lines}</td>
                      <td style={{ padding: '3px 8px', textAlign: 'right' }}>{fmt(d.ext_price || 0)}</td>
                      <td style={{ padding: '3px 8px', textAlign: 'right' }}>{fmt(d.gp || 0)}</td>
                      <td style={{ padding: '3px 8px', textAlign: 'right', color: 'var(--text3)' }}>
                        {d.excluded_lines ? `${d.excluded_lines} · ${fmt(d.excluded_ext_price || 0)}` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}


      {/* ── COMMISSION LEGS (owner directive 2026-08-04) ────────────────────────────────────────
          "1st Month commission which is paid the same month of the activation and the other is
           M2-M12 commission, any commission received for an activated number after the activated
           month will be in this category."
          This card is a DECOMPOSITION of the four commission columns already on this report — every
          row's three parts add back to the column total shown on the right, and the page says so
          rather than asking anyone to take it on trust. */}
      {!loading && view === 'store' && (
        <div className="card" style={{ padding: '12px 14px', marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>🧩 Commission legs — 1st Month vs M2–M12</div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <a href="/commcalc/commission-legs" style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)' }}>Map a label&apos;s leg →</a>
              <a href="/commcalc/commission-ledger" style={{ fontSize: 12, color: 'var(--text3)' }}>same split on the Commission Ledger →</a>
            </div>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4, lineHeight: 1.5 }}>
            <b>1st Month</b> = commission received in the SAME month the number activated. <b>M2–M12</b> = commission
            received for a number that activated in an earlier month. Each row below adds back to its own column on
            this report — this splits the money, it never adds any.
          </div>
          {!legOk && (
            <div style={{ fontSize: 12, color: 'var(--red)', marginTop: 6, fontWeight: 600 }}>
              ⚠ A row&apos;s parts do not add back to its total — treat this split as unreliable and report it.
            </div>
          )}
          <div className="table-wrapper" style={{ marginTop: 10 }}>
            <table style={{ borderCollapse: 'collapse', width: '100%' }}>
              <thead>
                <tr style={{ fontSize: 11, color: 'var(--text3)', textAlign: 'left' }}>
                  <th style={{ padding: '4px 8px' }}>Money</th>
                  <th style={{ padding: '4px 8px' }}>What decides the leg</th>
                  <th style={{ padding: '4px 8px', textAlign: 'right' }}>1st Month</th>
                  <th style={{ padding: '4px 8px', textAlign: 'right' }}>M2–M12</th>
                  <th style={{ padding: '4px 8px', textAlign: 'right' }}>Unsplit</th>
                  <th style={{ padding: '4px 8px', textAlign: 'right' }}>Total</th>
                  <th style={{ padding: '4px 8px', textAlign: 'right' }}>M2–M12 share</th>
                </tr>
              </thead>
              <tbody>
                {legRows.map(l => (
                  <tr key={l.key} style={{ fontSize: 12, borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 8px', fontWeight: 600 }}>{l.label}</td>
                    <td style={{ padding: '4px 8px', color: 'var(--text3)', fontSize: 11 }}>{legSourceHow[l.key] || '—'}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right' }}>{fmt(l.m1)}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right' }}>{fmt(l.m2)}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', color: l.un ? '#b45309' : 'var(--text3)' }}>{l.un ? fmt(l.un) : '—'}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', fontWeight: 700 }}>{fmt(l.tot)}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', color: 'var(--text3)' }}>
                      {l.tot ? `${Math.round(l.m2 / l.tot * 1000) / 10}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(totals.comm_unsplit || 0) !== 0 && (commUnsplitFields.length ? (
            <div style={{ fontSize: 11, color: '#b45309', marginTop: 8, lineHeight: 1.5 }}>
              {fmt(totals.comm_unsplit)} of Commission sits outside both legs: it is{' '}
              {commUnsplitFields.map(f => f.replace(/_/g, ' ')).join(', ')} on the master-agent export
              {commUnsplitWhy ? ` — ${commUnsplitWhy}` : ''}. It is still counted in the Commission total on the
              right; it is simply not 1st-month commission, so <b>1st Month here is what the carrier portal states
              as Commissions Paid</b>. Nothing was guessed and nothing was dropped.
            </div>
          ) : (
            <div style={{ fontSize: 11, color: '#b45309', marginTop: 8, lineHeight: 1.5 }}>
              {fmt(totals.comm_unsplit)} of Commission could not be attributed to a leg — those carrier labels never
              state a month-of-life. Nothing was guessed. Assign them on{' '}
              <a href="/commcalc/commission-legs" style={{ fontWeight: 600, color: 'var(--accent)' }}>Commission Legs</a>{' '}
              and they move into one of the two columns.
            </div>
          ))}
          {legCfg?.resolved_from && (
            <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 6 }}>
              Rules in use: {legCfg.resolved_from.replace(/_/g, ' ')}
              {legCfg.label_overrides ? ` · ${legCfg.label_overrides} label override(s)` : ''}
              {legCfg.mi_split_by_activation === false ? ' · residual split off' : ''}
            </div>
          )}
        </div>
      )}

      {/* ── COMMISSION-LEG TREND (owner: "trend alignment with the 3MR and 6MR") ────────────────
          Two series over the last 12 months: money received in the activation month vs money received
          later for already-activated numbers. The optional ladder table underneath breaks the SAME
          money down by the leg's month-of-life — M2/M3 are what 3-month retention pays, M4–M6 the
          6-month tail — which is the alignment the owner asked to be able to see. */}
      {legTrend?.company?.length > 1 && view === 'store' && (
        <div className="card" style={{ padding: '12px 12px 6px', marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', paddingLeft: 6 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>
              📈 Commission by leg — last {legTrend.company.length} months{selMarkets.length ? ` · ${selMarkets.join(', ')}` : ' · all stores'}
            </div>
            <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 10px' }} onClick={() => setLegLadder(v => !v)}>
              {legLadder ? 'Hide' : 'Show'} month-of-life ladder (3MR / 6MR)
            </button>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', padding: '2px 6px 6px', lineHeight: 1.5 }}>{legTrend.basis}</div>
          {(legTrend.notes || []).map((n: string) => (
            <div key={n} style={{ fontSize: 11, color: '#b45309', padding: '0 6px 4px' }}>⚠ {n}</div>
          ))}
          <TrendChart data={legTrendData} height={220}
            series={[{ key: 'm1', name: '1st month', color: '#16a34a', money: true },
                     { key: 'm2_12', name: 'M2–M12', color: '#2e75b6', money: true },
                     ...((legTrend.company || []).some((c: any) => c.unsplit) ? [{ key: 'unsplit', name: 'Unsplit', color: '#f59e0b', money: true, dashed: true }] : [])]} />
          {legLadder && (
            <div className="table-wrapper" style={{ marginTop: 6 }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', padding: '0 6px 6px', lineHeight: 1.5 }}>
                {legTrend.retention_note}
              </div>
              <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                <thead>
                  <tr style={{ fontSize: 11, color: 'var(--text3)', textAlign: 'right' }}>
                    <th style={{ padding: '3px 8px', textAlign: 'left' }}>Month received</th>
                    {(legTrend.ladder_months || []).map((m: number) => <th key={m} style={{ padding: '3px 8px' }}>M{m}</th>)}
                    {legTrend.has_unknown_leg && <th style={{ padding: '3px 8px' }}>Unsplit</th>}
                    <th style={{ padding: '3px 8px' }}>Total</th>
                    <th style={{ padding: '3px 8px' }} title="Share of that month's commission that came from already-activated numbers">M2–M12 %</th>
                    <th style={{ padding: '3px 8px' }} title="DLAR rep-average 3-month retention for that month, when the org has it">3MR</th>
                  </tr>
                </thead>
                <tbody>
                  {legTrend.company.map((c: any) => (
                    <tr key={c.period} style={{ fontSize: 12, borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '3px 8px' }}>{shortPeriod(c.period)}</td>
                      {(legTrend.ladder_months || []).map((m: number) => (
                        <td key={m} style={{ padding: '3px 8px', textAlign: 'right', color: c.ladder?.[String(m)] ? 'inherit' : 'var(--text3)' }}>
                          {c.ladder?.[String(m)] ? fmt(c.ladder[String(m)]) : '·'}
                        </td>
                      ))}
                      {legTrend.has_unknown_leg && (
                        <td style={{ padding: '3px 8px', textAlign: 'right', color: '#b45309' }}>{c.ladder?.unknown ? fmt(c.ladder.unknown) : '·'}</td>
                      )}
                      <td style={{ padding: '3px 8px', textAlign: 'right', fontWeight: 700 }}>{fmt(c.total)}</td>
                      <td style={{ padding: '3px 8px', textAlign: 'right' }}>{c.total ? `${c.m2_12_pct}%` : '—'}</td>
                      <td style={{ padding: '3px 8px', textAlign: 'right', color: 'var(--text3)' }}>{c.tmr3 != null ? `${c.tmr3}%` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Net-profit trend on top */}
      {gpTrendData.length > 1 && (
        <div className="card" style={{ padding: '12px 12px 6px', marginBottom: 20 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4, paddingLeft: 6 }}>
            📈 Net profit &amp; revenue — last {gpTrendData.length} months{selMarkets.length ? ` · ${selMarkets.join(', ')}` : ' · all stores'}
            {gpTrend?.pending_months?.length ? <span style={{ fontWeight: 400, color: '#f59e0b' }}> · {gpTrend.pending_months.length} month(s) computing (reload)</span> : ''}
          </div>
          <TrendChart data={gpTrendData} height={220}
            series={[{ key: 'net_profit', name: 'Net profit', color: '#16a34a', money: true }, { key: 'total_rev', name: 'Revenue', color: '#2e75b6', money: true, dashed: true }]} />
        </div>
      )}

      {/* Rep view */}
      {!loading && view === 'rep' && (
        <div className="table-wrapper" style={{ marginBottom: 20 }}>
          <table>
            <thead>
              <tr>
                <th>Rep</th><th>Store</th>
                <th style={{ textAlign: 'right' }}>Acc GP</th>
                <th style={{ textAlign: 'right' }}>Setup GP</th>
                <th style={{ textAlign: 'right' }}>Phone Sales</th>
                <th style={{ textAlign: 'right' }}>Plan GP</th>
                <th style={{ textAlign: 'right' }}>Comm Earned</th>
              </tr>
            </thead>
            <tbody>
              {repRows
                .map((r: any, i: number) => (
                <tr key={i}>
                  <td style={{ fontWeight: 500 }}>{r.storeops_name || r.rep}</td>
                  <td style={{ fontSize: 12, color: 'var(--text3)' }}>{r.store?.substring(0, 30)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.acc_gp)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.setup_gp)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.phone_sales)}</td>
                  <td style={{ textAlign: 'right' }}>{fmt(r.plan_gp)}</td>
                  <td style={{ textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>{fmt(r.comm_earned)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Full GP table */}
      {view === 'store' && loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : view === 'store' && (
        <div style={{ overflow: 'auto', maxHeight: 'calc(100vh - 320px)', background: 'white', border: '1px solid var(--border)', borderRadius: 12 }}>
          {cw.dirty && <div style={{ padding: '4px 10px', fontSize: 11, color: 'var(--text3)' }}><button className="btn" style={{ padding: '2px 8px', fontSize: 11 }} onClick={cw.resetAll}>↺ Reset column widths</button> <span>drag a column edge to resize · double-click to auto-fit</span></div>}
          <table style={{ borderCollapse: 'collapse', tableLayout: 'auto' }}>
            <colgroup>
              <col style={{ width: cw.width('store') }} />
              {COLS.map(c => <col key={c.key} style={{ width: cw.width(c.key) }} />)}
            </colgroup>
            <thead>
              <tr>
                <th style={{ padding: '12px 14px', color: 'white', fontSize: 12, fontWeight: 700, letterSpacing: '0.03em', textAlign: 'left', position: 'sticky', left: 0, top: 0, zIndex: 3, background: '#1e3a5f', whiteSpace: 'nowrap' }}>
                  STORE<ResizeHandle onDown={e => cw.start('store', e)} onReset={() => cw.reset('store')} />
                </th>
                {COLS.map(c => (
                  <th key={c.key} style={{ padding: '12px 10px', color: 'white', fontSize: 12, fontWeight: 700, letterSpacing: '0.03em', textAlign: 'right',
                    position: 'sticky', top: 0, zIndex: 2, background: '#1e3a5f', whiteSpace: 'nowrap',
                    borderLeft: ['comm', 'total_rev', 'rep_pay', 'net_profit'].includes(c.key) ? '2px solid rgba(255,255,255,0.25)' : undefined }}>
                    {c.label}<ResizeHandle onDown={e => cw.start(c.key, e)} onReset={() => cw.reset(c.key)} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} style={{ background: i % 2 === 1 ? '#fafbfc' : 'white' }}>
                  <td style={{ padding: '8px 14px', fontWeight: 500, fontSize: 12, position: 'sticky', left: 0, background: i % 2 === 1 ? '#fafbfc' : 'white', borderBottom: '1px solid var(--border)' }}>
                    <div>{r.store?.substring(0, 30)}</div>
                    <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 999, background: '#dbeafe', color: '#1e40af', fontWeight: 600 }}>
                      {r.market || '—'}
                    </span>
                  </td>
                  {COLS.map(c => <Cell key={c.key} val={(r as any)[c.key] || 0} col={c} />)}
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={COLS.length + 1} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                  No data — upload files and run calculation
                </td></tr>
              )}
            </tbody>
            {rows.length > 0 && (
              <tfoot>
                <tr style={{ background: '#1e3a5f', fontWeight: 700 }}>
                  <td style={{ padding: '10px 14px', color: 'white', fontSize: 12, position: 'sticky', left: 0, background: '#1e3a5f' }}>
                    TOTAL ({rows.length} stores)
                  </td>
                  {COLS.map(c => {
                    const val = (totals as any)[c.key] || 0
                    const color = c.highlight ? (val >= 0 ? '#86efac' : '#fca5a5') : c.red ? '#fca5a5' : 'white'
                    return (
                      <td key={c.key} style={{ padding: '10px 10px', textAlign: 'right', color, fontSize: 12,
                        borderLeft: ['comm', 'total_rev', 'rep_pay', 'net_profit'].includes(c.key) ? '2px solid rgba(255,255,255,0.2)' : undefined }}>
                        {fmt(val)}
                      </td>
                    )
                  })}
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </div>
  )
}
