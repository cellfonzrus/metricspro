'use client'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { hasDataGrant } from '@/lib/rbac'
import ReportShell from '@/components/ReportShell'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import EntityPicker, { type EntityOption } from '@/components/EntityPicker'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'

// DEVICE COST RECONCILIATION — the OPTION-A MEASUREMENT PASS (owner GO 2026-07-30; the design note
// docs/designs/device-cost-ledger.md §9 "Execution order locked", item 1).
//
// "What did each device cost us, according to WHICH source, and how much of that number is the same
// device counted twice?" MetricsPro knows a handset's cost in four places, in four shapes, with no
// shared key and no agreement about WHEN the cost hits the books:
//   ① commcalc.raw_ma_fulfillment      purchase price   timed on the order date
//   ② commcalc.asset_ledger            VIP billing      timed on the verified Billing Friday
//   ③ raw_sales ∪ daily_sales_feed     ext price − GP   timed on the sale date
//   ④ commcalc.inventory_aging_device  unit cost        a point-in-time snapshot valuation
// This page lays them side by side, tags each row with its source + ARRANGEMENT (read from this org's
// distributor config, never guessed) + timing date, flags the IMEI overlaps, COUNTS the rows that
// cannot reach an IMEI at all, and previews the month × store delta between today's device-COGS route
// and the owner's §9 policy.
//
// DISPLAY ONLY. Nothing here changes a P&L, GP, payout, rate, tier or plan number, and the backend
// writes nothing at all (in particular never the asset ledger, which belongs to the asset module). The
// Option-C flip that would point the P&L at the policy column is HELD pending the owner's review of
// exactly the delta table below.
//
// ACCESS: NO DEFAULT ACCESS — gated by the 'device_cost_recon' DATA_GRANT. The BACKEND is the
// enforcement (403 before a single row is read); `hasDataGrant` here is the frontend MIRROR, and because
// that mirror is optimistic while permissions load, the 403 is ALSO handled → the same lock note, never
// a raw red error.
//
// RULE FOUR: ReportShell (Excel / PDF / Print + Send by email & WhatsApp) over the rows on screen, plus
// a ReportExportBar over each of the four analysis tables — the delta preview, the overlaps, the rollup
// and the per-source honesty table. RULE FIVE: <StandardFilterBar> core set (period · stores · market ·
// reps) with the appended pick-don't-type facets; every filter is applied SERVER-side so the tiles, the
// tables, the delta and the exports are one set of numbers by construction.

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const lbl: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }
const tile: React.CSSProperties = { flex: 1, minWidth: 168, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }
const tileCap: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase' }
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '5px 8px', fontSize: 13 }
const card: React.CSSProperties = { padding: 0, marginBottom: 12 }

const SRC_TINT: Record<string, string> = {
  ma_fulfillment: '#2563eb', asset_lending: '#7c3aed', pos_sale: '#15803d', inventory_snapshot: '#b45309',
}

function thisMonth() { return new Date().toISOString().slice(0, 7) }
const n0 = (v: any) => (v == null ? '—' : Number(v).toLocaleString())
const money = (v: any) => (v == null ? '—' : fmt(v))
const pct = (v: any) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(1)}%`)

type Row = any

// The backend 403 detail names the grant key verbatim; client.ts `api()` throws an Error carrying only
// that detail string (the status code is not preserved), so the key IS the signal.
const isGateError = (m: string) => /device_cost_recon/i.test(m) || /restricted/i.test(m)

function LockNote() {
  return (
    <div className="card" style={{ padding: 18, marginTop: 14, fontSize: 13, lineHeight: 1.7,
      background: 'var(--surface2, #f8fafc)', border: '1px solid var(--border)' }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>🔒 This report is restricted</div>
      Ask an admin to grant <b>“Device cost reconciliation”</b> on your role
      (Roles &amp; Access → your role → sensitive data grants). This report has <b>no default access</b>:
      it shows what every single device cost the company, from every source at once, which is more
      sensitive than any one of the per-source reports it reconciles.
      <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
        Nothing is wrong with your login — administrators and company-wide roles already have it.
      </div>
    </div>
  )
}

function Collapsible({ title, sub, children, open, onToggle }: {
  title: React.ReactNode; sub?: React.ReactNode; children: React.ReactNode
  open: boolean; onToggle: () => void
}) {
  return (
    <div className="card" style={card}>
      <button onClick={onToggle} style={{ width: '100%', textAlign: 'left', padding: '10px 14px',
        background: 'transparent', border: 0, cursor: 'pointer', fontWeight: 700, fontSize: 13 }}>
        {open ? '▾' : '▸'} {title}
        {sub ? <span style={{ fontWeight: 400, color: 'var(--text3)' }}> {sub}</span> : null}
      </button>
      {open && <div style={{ borderTop: '1px solid var(--border)' }}>{children}</div>}
    </div>
  )
}

export default function DeviceCostReconPage() {
  const { permissions } = useAuth()
  const clientGranted = hasDataGrant(permissions, 'device_cost_recon')
  const [locked, setLocked] = useState(false)
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter(thisMonth()))
  const [win, setWin] = useState(1)
  const [groupBy, setGroupBy] = useState('source')
  const [basis, setBasis] = useState('unit')
  const [maDate, setMaDate] = useState('ordered')
  const [prec, setPrec] = useState('ma_fulfillment,asset_lending,pos_sale')
  const [srcSel, setSrcSel] = useState<string[]>([])
  const [arrSel, setArrSel] = useState<string[]>([])
  const [timeSel, setTimeSel] = useState<string[]>([])
  const [prodSel, setProdSel] = useState<string[]>([])
  const [monthSel, setMonthSel] = useState<string[]>([])
  const [overlapOnly, setOverlapOnly] = useState(false)
  const [unlinkOnly, setUnlinkOnly] = useState(false)
  const [recOnly, setRecOnly] = useState(false)
  const [inclCancelled, setInclCancelled] = useState(false)
  const [openLegend, setOpenLegend] = useState(false)
  const [openOverlap, setOpenOverlap] = useState(true)
  const [openHonesty, setOpenHonesty] = useState(true)
  const [openRollup, setOpenRollup] = useState(false)
  const [d, setD] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    if (!clientGranted) { setLocked(true); setBusy(false); return }   // no grant → don't fire a doomed request
    setBusy(true); setMsg('')
    try {
      const qs = new URLSearchParams({
        period: filt.period || thisMonth(), window_months: String(win),
        group_by: groupBy, price_basis: basis, ma_recognition_date: maDate, precedence: prec,
      })
      if (filt.stores.length) qs.set('stores', filt.stores.join(','))
      if (filt.markets.length) qs.set('markets', filt.markets.join(','))
      if (filt.reps.length) qs.set('reps', filt.reps.join(','))
      if (srcSel.length) qs.set('sources', srcSel.join(','))
      if (arrSel.length) qs.set('arrangements', arrSel.join(','))
      if (timeSel.length) qs.set('timings', timeSel.join(','))
      if (prodSel.length) qs.set('products', prodSel.join(','))
      if (monthSel.length) qs.set('months', monthSel.join(','))
      if (overlapOnly) qs.set('overlap_only', '1')
      if (unlinkOnly) qs.set('unlinkable_only', '1')
      if (recOnly) qs.set('recognized_only', '1')
      if (inclCancelled) qs.set('include_cancelled', '1')
      setD(await api(`/api/v1/commcalc/device-cost-recon?${qs.toString()}${orgParam()}`))
    } catch (e: any) {
      const m = String(e?.message || e)
      if (isGateError(m)) { setLocked(true); setD(null) } else setMsg('❌ ' + m)
    }
    setBusy(false)
  }, [filt, win, groupBy, basis, maDate, prec, srcSel, arrSel, timeSel, prodSel, monthSel,
      overlapOnly, unlinkOnly, recOnly, inclCancelled, clientGranted])

  useEffect(() => { load() }, [])            // eslint-disable-line react-hooks/exhaustive-deps
  const didMount = useRef(false)
  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return }
    load()
  }, [load])

  // Pick-don't-type options — computed by the backend from the UNFILTERED rows, so a picker never
  // collapses to the current selection.
  const storeOpts: EntityOption[] = (d?.store_options || []).map((s: string) => ({ id: s, label: s }))
  const marketOpts: EntityOption[] = (d?.market_options || []).map((s: string) => ({ id: s, label: s }))
  const repOpts: EntityOption[] = (d?.rep_options || []).map((s: string) => ({ id: s, label: s }))
  const sourceOpts: EntityOption[] = (d?.source_options || []).map((o: any) => ({ id: o.id, label: o.label }))
  const arrOpts: EntityOption[] = (d?.arrangement_options || []).map((s: string) => ({ id: s, label: s }))
  const timingOpts: EntityOption[] = (d?.timing_options || []).map((o: any) => ({ id: o.id, label: o.label }))
  const productOpts: EntityOption[] = (d?.product_options || []).map((s: string) => ({ id: s, label: s }))
  const monthOpts: EntityOption[] = (d?.month_options || []).map((o: any) => ({ id: o.id, label: o.label }))
  const groupOpts: { id: string; label: string }[] = d?.group_by_options || []

  const rows: Row[] = d?.rows || []
  const groups: any[] = d?.groups || []
  const overlaps: any[] = d?.overlaps || []
  const deltaRows: any[] = d?.delta_rows || []
  const t = d?.tiles
  const pol = d?.policy
  const inv = d?.inventory
  const liab = d?.liability
  const unlink = d?.unlinkable

  // RULE FOUR columns — the same rows on screen, exported verbatim.
  const cols: ExportColumn[] = [
    { header: 'Src', field: 'source_n', get: (r: Row) => r.source_n || '' },
    { header: 'Source', field: 'source_label', get: (r: Row) => r.source_label || '' },
    { header: 'Arrangement', field: 'arrangement_label', get: (r: Row) => r.arrangement_label || '' },
    { header: 'Distributor', field: 'distributor', get: (r: Row) => r.distributor || '' },
    { header: 'Device (IMEI)', field: 'device_key', get: (r: Row) => r.device_key || (d?.no_device_label || '') },
    { header: 'Device / item', field: 'product_label', get: (r: Row) => r.product_label || '' },
    { header: 'Amount', field: 'amount', money: true, get: (r: Row) => r.amount },
    { header: 'What the $ is', field: 'amount_kind', get: (r: Row) => r.amount_kind || '' },
    { header: 'Timing date', field: 'event_date', type: 'date', role: 'date', get: (r: Row) => r.event_date || '' },
    { header: 'Timed on', field: 'timing_label', get: (r: Row) => r.timing_label || '' },
    { header: 'Month', field: 'month_label', role: 'month', get: (r: Row) => r.month_label || '' },
    { header: 'Store', field: 'store_label', role: 'store', get: (r: Row) => r.store_label || '' },
    { header: 'Market', field: 'market', get: (r: Row) => r.market || (d?.no_market_label || '') },
    { header: 'Rep', field: 'rep', role: 'rep', get: (r: Row) => r.rep || '' },
    { header: 'Recognized (policy)', field: 'recognized', get: (r: Row) => (r.recognized ? 'YES' : 'no') },
    { header: 'Why', field: 'recognition_reason', get: (r: Row) => r.recognition_reason || '' },
    { header: 'Superseded by', field: 'suppressed_by', get: (r: Row) => r.suppressed_by || '' },
    { header: 'IMEI-linkable', field: 'linkable', get: (r: Row) => (r.linkable ? 'yes' : 'NO') },
    { header: 'Why not linkable', field: 'unlink_reason', get: (r: Row) => r.unlink_reason || '' },
    { header: 'Reference', field: 'ref', get: (r: Row) => r.ref || '' },
    { header: 'Source table', field: 'source_table', get: (r: Row) => r.source_table || '' },
  ]

  const deltaCols: ExportColumn[] = [
    { header: 'Month', field: 'month_label', role: 'month', get: (r: any) => r.month_label || '' },
    { header: 'Store', field: 'store', role: 'store', get: (r: any) => r.store || '' },
    { header: 'Market', field: 'market', get: (r: any) => r.market || '' },
    { header: "Today's device COGS", field: 'today', money: true, get: (r: any) => r.today },
    { header: '§9 policy', field: 'policy', money: true, get: (r: any) => r.policy },
    { header: 'Δ', field: 'delta', money: true, get: (r: any) => r.delta },
    { header: 'Δ %', field: 'delta_pct', type: 'number', get: (r: any) => r.delta_pct },
    { header: 'Policy rows', field: 'policy_rows', type: 'number', get: (r: any) => r.policy_rows },
    { header: 'Un-dedupable $ in cell', field: 'at_risk', money: true, get: (r: any) => r.at_risk },
    { header: 'Only in', field: 'only_in', get: (r: any) => r.only_in || 'both' },
  ]

  const overlapCols: ExportColumn[] = [
    { header: 'Device (IMEI)', field: 'device_key', get: (o: any) => o.device_key || '' },
    { header: 'Sources', field: 'source_ns', get: (o: any) => o.source_ns || '' },
    { header: 'Overlap pairs', field: 'pair_labels', get: (o: any) => (o.pair_labels || []).join(' · ') },
    { header: 'Rows', field: 'rows', type: 'number', get: (o: any) => o.rows },
    { header: 'Sum of all sources', field: 'gross_amount', money: true, get: (o: any) => o.gross_amount },
    { header: 'Would be double-counted', field: 'duplicate_amount', money: true, get: (o: any) => o.duplicate_amount },
    { header: 'Store(s)', field: 'stores', role: 'store', get: (o: any) => (o.stores || []).join(' · ') },
    { header: 'Device / item', field: 'products', get: (o: any) => (o.products || []).join(' · ') },
    { header: 'Month(s)', field: 'months', get: (o: any) => (o.months || []).join(' · ') },
  ]

  const groupCols: ExportColumn[] = [
    { header: d?.group_label || 'Group', field: 'label', get: (g: any) => g.label },
    { header: 'Rows', field: 'rows', type: 'number', get: (g: any) => g.rows },
    { header: 'Devices', field: 'devices', type: 'number', get: (g: any) => g.devices },
    { header: 'Naive $', field: 'amount', money: true, get: (g: any) => g.amount },
    { header: 'Recognized $', field: 'recognized_amount', money: true, get: (g: any) => g.recognized_amount },
    { header: 'Suppressed $', field: 'suppressed_amount', money: true, get: (g: any) => g.suppressed_amount },
    { header: 'Un-linkable rows', field: 'unlinkable_rows', type: 'number', get: (g: any) => g.unlinkable_rows },
    { header: 'Un-linkable $', field: 'unlinkable_amount', money: true, get: (g: any) => g.unlinkable_amount },
    { header: 'No-$ rows', field: 'priceless_rows', type: 'number', get: (g: any) => g.priceless_rows },
    { header: 'First date', field: 'first_date', type: 'date', get: (g: any) => g.first_date || '' },
    { header: 'Last date', field: 'last_date', type: 'date', get: (g: any) => g.last_date || '' },
  ]

  const sourceRows = (t?.by_source || [])
  const sourceCols: ExportColumn[] = [
    { header: 'Src', field: 'n', get: (s: any) => s.n },
    { header: 'Source', field: 'label', get: (s: any) => s.label },
    { header: 'Rows', field: 'rows', type: 'number', get: (s: any) => s.rows },
    { header: 'Total $', field: 'amount', money: true, get: (s: any) => s.amount },
    { header: 'IMEI-linkable rows', field: 'linkable_rows', type: 'number', get: (s: any) => s.linkable_rows },
    { header: 'Un-linkable rows', field: 'unlinkable_rows', type: 'number', get: (s: any) => s.unlinkable_rows },
    { header: 'Un-linkable $', field: 'unlinkable_amount', money: true, get: (s: any) => s.unlinkable_amount },
    { header: 'Rows with no $', field: 'priceless_rows', type: 'number', get: (s: any) => s.priceless_rows },
    { header: 'Why not linkable', field: 'reasons', get: (s: any) => (s.reasons || []).join(' · ') },
  ]

  const subtitle = useMemo(() => {
    const bits = [`${rows.length} row(s)`, d?.definition_note, d?.policy_note, d?.caveat_note].filter(Boolean)
    return bits.join(' · ')
  }, [rows.length, d?.definition_note, d?.policy_note, d?.caveat_note])

  const header = (
    <div style={{ marginBottom: 14 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧮 Device Cost Reconciliation</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
        The same handset&apos;s cost, from all four sources at once — a marketplace <b>purchase</b>, a
        distributor&apos;s <b>consignment billing</b>, the <b>POS-derived</b> cost at sale, and the
        <b> inventory</b> valuation — each tagged with its arrangement and the date it is timed on, with
        the overlaps measured. <b>Display only:</b> nothing here changes the P&amp;L, GP or anyone&apos;s pay.
      </p>
    </div>
  )

  // NO DEFAULT ACCESS: without the grant nothing is rendered — not the filters, not the tiles, not the
  // costs. (The backend refuses independently.)
  if (locked) {
    return <div style={{ maxWidth: 1400 }}>{header}<LockNote /></div>
  }

  return (
    <div style={{ maxWidth: 1400 }}>
      {header}

      {/* RULE FIVE core set (period · stores · market · reps) + the appended module facets. */}
      <StandardFilterBar
        value={filt} onChange={setFilt} periodMode="month"
        show={{ period: true, stores: true, markets: true, reps: true }}
        storeOptions={storeOpts} marketOptions={marketOpts} repOptions={repOpts}
        repLabel="Reps (POS source only)…"
        right={<>
          <label style={lbl}>Window
            <select style={sel} value={win} onChange={e => setWin(Number(e.target.value))}>
              {[1, 2, 3, 6, 12].map(n => <option key={n} value={n}>{n === 1 ? 'this month' : `last ${n} months`}</option>)}
            </select>
          </label>
          <label style={lbl}>Group by
            <select style={sel} value={groupBy} onChange={e => setGroupBy(e.target.value)}>
              {(groupOpts.length ? groupOpts : [{ id: 'source', label: 'Source' }]).map(o =>
                <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </label>
          {sourceOpts.length > 0 && (
            <EntityPicker multi options={sourceOpts} value={srcSel} onChange={setSrcSel}
              placeholder="Sources…" width={210} ariaLabel="Filter by cost source" />
          )}
          {arrOpts.length > 0 && (
            <EntityPicker multi options={arrOpts} value={arrSel} onChange={setArrSel}
              placeholder="Arrangements…" width={200} ariaLabel="Filter by distributor arrangement" />
          )}
          {timingOpts.length > 0 && (
            <EntityPicker multi options={timingOpts} value={timeSel} onChange={setTimeSel}
              placeholder="Timing…" width={165} ariaLabel="Filter by cost timing" />
          )}
          {productOpts.length > 0 && (
            <EntityPicker multi options={productOpts} value={prodSel} onChange={setProdSel}
              placeholder="Device / item…" width={180} ariaLabel="Filter by device or item" />
          )}
          {monthOpts.length > 1 && (
            <EntityPicker multi options={monthOpts} value={monthSel} onChange={setMonthSel}
              placeholder="Months…" width={150} ariaLabel="Filter by month" />
          )}
          <label style={lbl} title="Only devices seen in more than one source">
            <input type="checkbox" checked={overlapOnly} onChange={e => setOverlapOnly(e.target.checked)} /> Overlaps only
          </label>
          <label style={lbl} title="Only rows that cannot be joined to an IMEI">
            <input type="checkbox" checked={unlinkOnly} onChange={e => setUnlinkOnly(e.target.checked)} /> Un-linkable only
          </label>
          <label style={lbl} title="Only the rows the §9 policy would recognize as cost">
            <input type="checkbox" checked={recOnly} onChange={e => setRecOnly(e.target.checked)} /> Recognized only
          </label>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy} onClick={() => load()}>
            {busy ? '…' : '↻ Reload'}
          </button>
        </>}
      />

      {msg && <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13 }}>{msg}</div>}

      {d?.ready && <>
        {/* ── TILES: what each source says, what overlaps, what cannot be joined, and the net delta ── */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          {sourceRows.map((s: any) => (
            <div key={s.source} style={{ ...tile, borderColor: s.rows ? SRC_TINT[s.source] : 'var(--border)' }}>
              <div style={tileCap}>{s.n} {s.label}</div>
              <div style={{ fontSize: 20, fontWeight: 800 }}>{money(s.amount)}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                {n0(s.rows)} rows
                {s.unlinkable_rows ? <span style={{ color: '#b45309' }}> · {n0(s.unlinkable_rows)} un-linkable ({money(s.unlinkable_amount)})</span> : null}
                {s.priceless_rows ? ` · ${n0(s.priceless_rows)} with no $` : ''}
              </div>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          <div style={{ ...tile, borderColor: '#fca5a5' }}>
            <div style={tileCap}>⚠️ Naive four-source sum</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{money(t.naive_total)}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              what you get by adding all four — the thing this page exists to show is wrong
            </div>
          </div>
          <button onClick={() => setOverlapOnly(v => !v)} style={{ ...tile, textAlign: 'left', cursor: 'pointer',
            background: overlapOnly ? 'var(--surface2)' : 'transparent',
            borderColor: t.overlap?.devices ? '#fcd34d' : 'var(--border)' }}>
            <div style={tileCap}>🔗 Matched overlap</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: t.overlap?.devices ? '#b45309' : undefined }}>
              {money(t.overlap?.duplicate_amount)}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              {n0(t.overlap?.devices)} device(s) in 2+ sources · {money(t.overlap?.gross_amount)} gross
            </div>
          </button>
          <button onClick={() => setUnlinkOnly(v => !v)} style={{ ...tile, textAlign: 'left', cursor: 'pointer',
            background: unlinkOnly ? 'var(--surface2)' : 'transparent',
            borderColor: t.unlinkable?.rows ? '#fcd34d' : 'var(--border)' }}>
            <div style={tileCap}>🚫 Cannot reach an IMEI</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: t.unlinkable?.rows ? '#b45309' : undefined }}>
              {money(t.unlinkable?.amount)}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{n0(t.unlinkable?.rows)} row(s) — counted, never assumed unique</div>
          </button>
          <div style={{ ...tile, borderColor: '#a7f3d0' }}>
            <div style={tileCap}>§9 policy would recognize</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{money(pol?.recognized_amount)}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              {n0(pol?.recognized_rows)} rows · {money(pol?.invoice_amount)} invoice + {money(pol?.fallback_amount)} at sale
            </div>
          </div>
          <div style={tile}>
            <div style={tileCap}>Today&apos;s device COGS</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{money(t.today?.device_cogs)}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              {t.today?.available ? "the P&L's own route" : (t.today?.note || 'unavailable')}
            </div>
          </div>
          <div style={{ ...tile, borderColor: t.net_delta ? '#93c5fd' : 'var(--border)' }}>
            <div style={tileCap}>Net delta (policy − today)</div>
            <div style={{ fontSize: 22, fontWeight: 800,
              color: t.net_delta == null ? undefined : (t.net_delta > 0 ? '#b91c1c' : '#15803d') }}>
              {t.net_delta == null ? '—' : money(t.net_delta)}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              {d.delta_totals?.delta_pct != null ? pct(d.delta_totals.delta_pct) + ' vs today · ' : ''}
              review this before any P&amp;L change
            </div>
          </div>
        </div>

        {/* What the report MEANS — on the page AND carried into every export subtitle. */}
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
          <div><b>Definition.</b> {d.definition_note}</div>
          <div><b>Policy preview.</b> {d.policy_note}</div>
          <div><b>The IMEI caveat.</b> {d.caveat_note}</div>
          <div><b>Window.</b> {d.window_from} → {d.window_to} ({d.months_in_window?.length || 0} month(s)) ·
            {' '}precedence <code>{d.precedence_label}</code></div>
        </div>

        {d.degraded?.length > 0 && (
          <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13, background: '#fef2f2', border: '1px solid #fecaca' }}>
            <b>Some of this window could not be read in full.</b> The numbers below are still real, but
            they are bounds, not totals:
            <ul style={{ margin: '6px 0 0 18px', padding: 0 }}>
              {d.degraded.map((x: string, i: number) => <li key={i} style={{ marginBottom: 3 }}>{x}</li>)}
            </ul>
          </div>
        )}

        {d.note && (
          <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13, background: '#fffbeb', border: '1px solid #fde68a' }}>
            ⚠️ {d.note}
          </div>
        )}

        {/* ── THE DELIVERABLE: month × store, today's route vs the §9 policy ───────────────────── */}
        <div className="card" style={card}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', gap: 10, flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontWeight: 700, fontSize: 13 }}>
                Delta preview — month × store
                <span style={{ fontWeight: 400, color: 'var(--text3)' }}> ({n0(d.delta_total_rows)} cell(s))</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                Today&apos;s device-COGS route vs what the owner&apos;s §9 policy would produce. This is the
                table to review <b>before</b> any P&amp;L change — the change itself is not made here.
              </div>
            </div>
            <ReportExportBar title={`Device cost delta preview — ${d.period}`} subtitle={subtitle}
              filename={`device-cost-delta-${String(d.period).replace(/\s+/g, '-')}`}
              columns={deltaCols} rows={deltaRows} />
          </div>
          <div style={{ borderTop: '1px solid var(--border)', overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Month', 'Store', 'Market', "Today's device COGS", '§9 policy', 'Δ', 'Δ %', 'Un-dedupable $', ''].map(h =>
                  <th key={h} style={th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {deltaRows.map((r: any, i: number) => (
                  <tr key={`${r.month}|${r.store_key}|${i}`} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={td}>{r.month_label}</td>
                    <td style={td}>{r.store}</td>
                    <td style={{ ...td, color: 'var(--text3)' }}>{r.market}</td>
                    <td style={td}>{money(r.today)}</td>
                    <td style={td}>{money(r.policy)}</td>
                    <td style={{ ...td, fontWeight: 700, color: r.delta > 0 ? '#b91c1c' : (r.delta < 0 ? '#15803d' : undefined) }}>
                      {money(r.delta)}
                    </td>
                    <td style={{ ...td, color: 'var(--text3)' }}>{pct(r.delta_pct)}</td>
                    <td style={{ ...td, color: r.at_risk ? '#b45309' : 'var(--text3)' }}>{money(r.at_risk)}</td>
                    <td style={{ ...td, fontSize: 11, color: 'var(--text3)' }}>
                      {r.only_in === 'today' ? 'today only' : r.only_in === 'policy' ? 'policy only' : ''}
                    </td>
                  </tr>
                ))}
                {deltaRows.length === 0 && (
                  <tr><td colSpan={9} style={{ ...td, color: 'var(--text3)' }}>
                    No month/store cell has a device cost from either leg in this window.
                  </td></tr>
                )}
              </tbody>
              {deltaRows.length > 0 && (
                <tfoot><tr style={{ borderTop: '2px solid var(--border)', background: 'var(--surface2)', fontWeight: 700 }}>
                  <td style={td} colSpan={3}>TOTAL — {n0(d.delta_totals?.months)} month(s), {n0(d.delta_totals?.stores)} store(s)</td>
                  <td style={td}>{money(d.delta_totals?.today)}</td>
                  <td style={td}>{money(d.delta_totals?.policy)}</td>
                  <td style={{ ...td, color: (d.delta_totals?.delta || 0) > 0 ? '#b91c1c' : '#15803d' }}>{money(d.delta_totals?.delta)}</td>
                  <td style={td}>{pct(d.delta_totals?.delta_pct)}</td>
                  <td style={td}>{money(d.delta_totals?.at_risk)}</td>
                  <td style={td} />
                </tr></tfoot>
              )}
            </table>
          </div>
          {/* §9 Q3 — BOTH legs, and the honest reason the Δ leg cannot be computed today. */}
          <div style={{ borderTop: '1px solid var(--border)', padding: '10px 14px', fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
            <div><b>Leg 1 — recognized cost.</b> {money(pol?.recognized_amount)} over {n0(pol?.recognized_rows)} row(s)
              ({money(pol?.suppressed_amount)} suppressed as a duplicate on the same IMEI).
              {/* The dedup DECISION is taken over the whole window (filtering first would let a
                  different row win); the TOTAL above describes only the filtered rows. When a filter is
                  narrowing, both figures are shown so neither can be mistaken for the other. */}
              {d.policy_window && d.policy_window.recognized_amount !== pol?.recognized_amount && (
                <span style={{ color: 'var(--text3)' }}> Whole window, before these filters:
                  {' '}{money(d.policy_window.recognized_amount)} — the IMEI dedup is decided across the
                  full window, so a filtered total is a subset of it, never a re-decision.</span>
              )}
            </div>
            <div><b>Leg 2 — inventory asset (periodic inventory).</b> closing valuation {money(inv?.snapshot_amount)}
              {' '}over {n0(inv?.snapshot_devices)} device(s)
              {inv?.snapshot_as_of_to ? ` (snapshot as of ${inv.snapshot_as_of_to})` : ''};
              the consignment ledger separately values {n0(inv?.ledger_unsold_devices)} unsold device(s) at
              {' '}{money(inv?.ledger_unsold_amount)}
              {inv?.double_valued_devices ? <span style={{ color: '#b45309' }}> — {n0(inv.double_valued_devices)} device(s)
                ({money(inv.double_valued_amount)}) are valued by BOTH</span> : null}.
            </div>
            <div style={{ color: '#b45309' }}><b>Δ(inventory) is not derivable.</b> {inv?.delta_note}</div>
            <div><b>Liability (§9 Q2).</b> {liab?.note} Unsold consignment owed:
              {' '}<b>{money(liab?.unsold_owed)}</b> across {n0(liab?.unsold_devices)} device(s).</div>
            {liab?.definition_note && <div style={{ color: '#b45309' }}>{liab.definition_note}</div>}
            {d.today?.available === false && <div style={{ color: '#b91c1c' }}><b>Today&apos;s leg unavailable.</b> {d.today?.note}</div>}
            {d.today?.available && <div style={{ color: 'var(--text3)' }}>
              Today&apos;s leg: <code>{d.today.route}</code> — {d.today.basis}.
            </div>}
          </div>
        </div>

        {/* ── OVERLAPS: the design-§3 double-count map, measured ────────────────────────────────── */}
        <Collapsible open={openOverlap} onToggle={() => setOpenOverlap(v => !v)}
          title="Overlaps — the same device in more than one source"
          sub={`(${n0(d.overlap_summary?.devices)} device(s) · ${money(d.overlap_summary?.duplicate_amount)} would be double-counted)`}>
          <div style={{ padding: '8px 14px', fontSize: 12, color: 'var(--text3)', lineHeight: 1.6 }}>
            Each pair below is one of the four double-counts the design note names. “Would be
            double-counted” is what a naive sum ADDS ON TOP of the single best figure — it is not a claim
            about which source is right.
          </div>
          <div style={{ padding: '0 14px 8px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {(d.overlap_summary?.pairs || []).map((p: any) => (
              <div key={p.code} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', minWidth: 230 }}>
                <div style={{ fontSize: 12, fontWeight: 700 }}>{p.label}</div>
                <div style={{ fontSize: 18, fontWeight: 800, color: p.devices ? '#b45309' : undefined }}>
                  {money(p.duplicate_amount)}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{n0(p.devices)} device(s) · {p.why}</div>
              </div>
            ))}
          </div>
          <div style={{ padding: '0 14px 10px' }}>
            <ReportExportBar title={`Device cost overlaps — ${d.period}`} subtitle={subtitle}
              filename={`device-cost-overlaps-${String(d.period).replace(/\s+/g, '-')}`}
              columns={overlapCols} rows={overlaps} />
          </div>
          <div style={{ overflowX: 'auto', borderTop: '1px solid var(--border)' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Device (IMEI)', 'Sources', 'Overlap', 'Rows', 'Sum of sources', 'Double-counted', 'Store(s)', 'Device / item'].map(h =>
                  <th key={h} style={th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {overlaps.map((o: any) => (
                  <tr key={o.device_key} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ ...td, fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>{o.device_key}</td>
                    <td style={td}>{o.source_ns}</td>
                    <td style={{ ...td, fontSize: 12 }}>{(o.pair_labels || []).join(' · ')}</td>
                    <td style={td}>{n0(o.rows)}</td>
                    <td style={td}>{money(o.gross_amount)}</td>
                    <td style={{ ...td, fontWeight: 700, color: '#b45309' }}>{money(o.duplicate_amount)}</td>
                    <td style={{ ...td, fontSize: 12 }}>{(o.stores || []).join(' · ')}</td>
                    <td style={{ ...td, fontSize: 12 }}>{(o.products || []).join(' · ')}</td>
                  </tr>
                ))}
                {overlaps.length === 0 && (
                  <tr><td colSpan={8} style={{ ...td, color: 'var(--text3)' }}>
                    No device in this window appears in more than one source. With un-linkable rows
                    present that is not the same as “no double-count” — see the panel below.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
          {d.overlap_total > overlaps.length && (
            <div style={{ padding: '8px 14px', fontSize: 12, color: '#b45309' }}>
              Showing the {overlaps.length} biggest of {n0(d.overlap_total)} overlapping devices — the
              tiles above still describe all of them.
            </div>
          )}
        </Collapsible>

        {/* ── HONESTY PANEL: what cannot be joined, per source ──────────────────────────────────── */}
        <Collapsible open={openHonesty} onToggle={() => setOpenHonesty(v => !v)}
          title="What cannot be joined to an IMEI (per source)"
          sub={`(${n0(unlink?.total?.unlinkable_rows)} row(s) · ${money(unlink?.total?.unlinkable_amount)})`}>
          <div style={{ padding: '8px 14px', fontSize: 12, color: 'var(--text3)', lineHeight: 1.6 }}>
            {d.caveat_note}
          </div>
          <div style={{ padding: '0 14px 10px' }}>
            <ReportExportBar title={`Device cost — per-source linkability, ${d.period}`} subtitle={subtitle}
              filename={`device-cost-linkability-${String(d.period).replace(/\s+/g, '-')}`}
              columns={sourceCols} rows={sourceRows} />
          </div>
          <div style={{ overflowX: 'auto', borderTop: '1px solid var(--border)' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Source', 'Rows', 'Total $', 'Linkable', 'Un-linkable', 'Un-linkable $', 'No $', 'Why not'].map(h =>
                  <th key={h} style={th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {sourceRows.map((s: any) => (
                  <tr key={s.source} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={td}><b style={{ color: SRC_TINT[s.source] }}>{s.n}</b> {s.label}</td>
                    <td style={td}>{n0(s.rows)}</td>
                    <td style={td}>{money(s.amount)}</td>
                    <td style={td}>{n0(s.linkable_rows)}</td>
                    <td style={{ ...td, color: s.unlinkable_rows ? '#b45309' : undefined }}>{n0(s.unlinkable_rows)}</td>
                    <td style={{ ...td, color: s.unlinkable_rows ? '#b45309' : undefined }}>{money(s.unlinkable_amount)}</td>
                    <td style={td}>{n0(s.priceless_rows)}</td>
                    <td style={{ ...td, fontSize: 11, color: 'var(--text3)' }}>{(s.reasons || []).join(' · ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
            <div><b>Recognized but un-dedupable.</b> {money(pol?.at_risk_amount)} across {n0(pol?.at_risk_rows)} row(s)
              carry no IMEI/serial, so the “IMEI-based, therefore never duplicate” rule cannot cover them.</div>
            {d.vip_serial_caveat && (
              <div><b>VIP invoice evidence.</b> {n0(d.vip_serial_caveat.rows)} invoice-device row(s) across
                {' '}{n0(d.vip_serial_caveat.invoices)} invoice(s) in this window:
                {' '}{n0(d.vip_serial_caveat.by_imei)} carry a usable IMEI,
                {' '}<b>{n0(d.vip_serial_caveat.by_serial_only)} are reachable only by SERIAL</b>
                {d.vip_serial_caveat.neither ? `, ${n0(d.vip_serial_caveat.neither)} by neither` : ''}.
                {d.vip_serial_caveat.truncated ? ' (read capped — a lower bound.)' : ''}
              </div>
            )}
            {d.overlap_summary?.ambiguous_link_rows > 0 && (
              <div><b>Shared activation orders.</b> {n0(d.overlap_summary.ambiguous_link_rows)} marketplace
                order line(s) link to MORE THAN ONE IMEI, so they are counted in the overlap scan but are
                never used to recognize a single device&apos;s cost.</div>
            )}
          </div>
        </Collapsible>

        {/* ── ROLLUP ────────────────────────────────────────────────────────────────────────────── */}
        <Collapsible open={openRollup} onToggle={() => setOpenRollup(v => !v)}
          title={<>By {String(d.group_label || '').toLowerCase()}</>} sub={`(${groups.length})`}>
          <div style={{ padding: '0 14px 10px', paddingTop: 10 }}>
            <ReportExportBar title={`Device cost by ${d.group_label} — ${d.period}`} subtitle={subtitle}
              filename={`device-cost-by-${d.group_by}-${String(d.period).replace(/\s+/g, '-')}`}
              columns={groupCols} rows={groups} />
          </div>
          <div style={{ overflowX: 'auto', borderTop: '1px solid var(--border)' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {[d.group_label, 'Rows', 'Devices', 'Naive $', 'Recognized $', 'Suppressed $', 'Un-linkable', 'No $'].map(h =>
                  <th key={h} style={th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {groups.map(g => (
                  <tr key={g.key || g.label} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={td}>{g.label}</td>
                    <td style={td}>{n0(g.rows)}</td>
                    <td style={td}>{n0(g.devices)}</td>
                    <td style={td}>{money(g.amount)}</td>
                    <td style={{ ...td, fontWeight: 700 }}>{money(g.recognized_amount)}</td>
                    <td style={{ ...td, color: g.suppressed_amount ? '#b45309' : undefined }}>{money(g.suppressed_amount)}</td>
                    <td style={{ ...td, color: g.unlinkable_rows ? '#b45309' : undefined }}>{n0(g.unlinkable_rows)}</td>
                    <td style={td}>{n0(g.priceless_rows)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Collapsible>

        {/* ── SOURCE LEGEND + the policy knobs (§9 Q4: stated, not buried) ──────────────────────── */}
        <Collapsible open={openLegend} onToggle={() => setOpenLegend(v => !v)}
          title="The four sources, their arrangements, and the recognition knobs"
          sub={`(${n0(d.distributors?.length)} distributor(s) configured)`}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Source', 'Table', 'Grain', 'What the $ means', 'Timed on', 'Invoice?', 'IMEI link'].map(h =>
                  <th key={h} style={th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {(d.source_legend || []).map((s: any) => (
                  <tr key={s.source} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={td}><b style={{ color: SRC_TINT[s.source] }}>{s.n}</b> {s.label}</td>
                    <td style={{ ...td, fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>{s.table}</td>
                    <td style={{ ...td, fontSize: 12 }}>{s.grain}</td>
                    <td style={{ ...td, fontSize: 12 }}>{s.amount_kind}<div style={{ color: 'var(--text3)' }}>{s.means}</div></td>
                    <td style={{ ...td, fontSize: 12 }}>{s.timing_label}</td>
                    <td style={td}>{s.is_invoice ? '✅ yes' : '—'}</td>
                    <td style={{ ...td, fontSize: 11, color: 'var(--text3)' }}>{s.link}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)', display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <label style={lbl} title="Invoice-first, sale-time fallback (§9 Q1). Option B moves this into a config table with an admin UI.">
              Recognition precedence
              <select style={sel} value={prec} onChange={e => setPrec(e.target.value)}>
                <option value="ma_fulfillment,asset_lending,pos_sale">① order → ② consignment billed → ③ at sale (§9 default)</option>
                <option value="asset_lending,ma_fulfillment,pos_sale">② consignment billed → ① order → ③ at sale</option>
                <option value="pos_sale,ma_fulfillment,asset_lending">③ at sale first (compare only)</option>
              </select>
            </label>
            <label style={lbl} title="Which fulfillment date times the cost. Ordered ≠ received.">
              ① timed on
              <select style={sel} value={maDate} onChange={e => setMaDate(e.target.value)}>
                <option value="ordered">date ordered</option>
                <option value="filled">date filled</option>
                <option value="shipped">date shipped</option>
              </select>
            </label>
            <label style={lbl} title="How the marketplace feed's Price column is read. Device History reads it per device, so 'per unit' is the default.">
              ① price is
              <select style={sel} value={basis} onChange={e => setBasis(e.target.value)}>
                <option value="unit">per unit</option>
                <option value="line">a line total</option>
              </select>
            </label>
            <label style={lbl}>
              <input type="checkbox" checked={inclCancelled} onChange={e => setInclCancelled(e.target.checked)} />
              include cancelled ① lines
            </label>
          </div>
          {(d.distributors || []).length > 0 && (
            <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
              <b>Arrangements read from this org&apos;s config</b> (commcalc.distributors — edit at{' '}
              <a href="/commcalc/distributors" style={{ color: 'var(--accent,#2563eb)' }}>Distributors</a>,
              and map a carrier&apos;s cost columns at{' '}
              <a href="/commcalc/payables" style={{ color: 'var(--accent,#2563eb)' }}>Payables → source maps</a>):
              {' '}{(d.distributors || []).map((x: any) => `${x.name} (${x.arrangement || 'unset'}${x.has_asset_lending ? ', asset lending' : ''})`).join(' · ')}
            </div>
          )}
        </Collapsible>

        {d.truncated && (
          <div style={{ fontSize: 12, color: '#b45309', marginBottom: 8 }}>
            Showing the first {rows.length} of {n0(d.total_rows)} matching cost rows — the tiles, the
            rollup and the delta preview still describe all {n0(d.total_rows)}. Narrow the filters to see
            the rest.
          </div>
        )}

        <ReportShell
          title={`Device Cost Reconciliation — ${d.period}`}
          subtitle={subtitle}
          filename={`device-cost-recon-${String(d.period).replace(/\s+/g, '-')}`}
          columns={cols}
          rows={rows}
          stickyHeader
          rowStyle={(r: Row) => (!r.linkable ? { background: 'rgba(245,158,11,0.07)' }
            : r.suppressed_by ? { background: 'rgba(239,68,68,0.06)' }
              : r.recognized ? { background: 'rgba(16,185,129,0.05)' } : undefined)}
        />

        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 14, lineHeight: 1.6 }}>
          Row shading: <b style={{ color: '#15803d' }}>green</b> — the §9 policy would recognize this row
          as the device&apos;s cost. <b style={{ color: '#b91c1c' }}>red</b> — suppressed as a duplicate of
          the same IMEI. <b style={{ color: '#b45309' }}>amber</b> — the row cannot be joined to an IMEI at
          all, so nothing can prove it is or is not a duplicate. A row with no usable dollar is counted and
          left out of every sum rather than added as $0. There is deliberately <b>no TOTAL row</b> on this
          table: adding all four sources together is the double-count this page exists to expose — read the
          per-source tiles and the policy total instead. Wiring the policy column into the P&amp;L or the GP
          report is a separate, money-touching decision and is <b>not</b> done here.
        </div>
      </>}

      {!d && !busy && !msg && <div className="card" style={{ padding: 14, fontSize: 13 }}>Loading…</div>}
    </div>
  )
}
