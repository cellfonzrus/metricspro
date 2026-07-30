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

// MARKETPLACE HANDSET COGS (owner-approved package 2026-07-29).
//
// "What did the handsets we ordered cost us — by product, by month, by ship-to — and what is still open?"
// Source: the master-agent marketplace fulfillment feed (commcalc.raw_ma_fulfillment). Extended cost is
// qty × unit price, computed SERVER-side in the pure `ma_handset_cogs` module, so the tiles, the rollup,
// the table and every export are the same numbers by construction.
//
// CARRIER- AND TENANT-AGNOSTIC: nothing here names a carrier or a tenant — an org either has marketplace
// fulfillment rows or it does not. Markets come from the org's own /store-match chain; anything unresolved
// is grouped under the SELECTABLE "(no market)" bucket, never dropped.
//
// COST REPORT, NOT A PAY REPORT: these are dollars the DISTRIBUTOR invoiced us, not dollars anyone is
// paid. Nothing on this page reads or changes a rate, tier, plan rule or payout, and nothing writes.
//
// ACCESS: NO DEFAULT ACCESS — gated by the 'ma_handset_cogs' DATA_GRANT (what a dealer pays for inventory
// is commercially sensitive). The BACKEND is the enforcement (`_require_ma_handset_cogs` → 403 before a
// single row is read); `hasDataGrant` here is the frontend MIRROR, and because that mirror is optimistic
// while permissions load, the 403 is ALSO handled → the same lock note, never a raw red error.
//
// RULE FOUR: ReportShell = Excel / PDF / Print + Send (email & WhatsApp) over the rows on screen, plus a
// second export bar over the rollup. RULE FIVE: <StandardFilterBar> core set (period · stores · market),
// with the appended pick-don't-type facets. DOCUMENTED DEVIATION: the fulfillment feed carries no
// rep/salesperson (marketplace orders are placed against the dealer account, not a seller), so the rep
// control is not rendered — and if a `reps` value ever reaches the endpoint it answers with a note rather
// than silently ignoring it.

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const lbl: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }
const tile: React.CSSProperties = { flex: 1, minWidth: 165, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }
const tileCap: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase' }
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '5px 8px', fontSize: 13 }

const STATE_TINT: Record<string, string> = { open: '#b45309', fulfilled: '#15803d', cancelled: '#b91c1c' }
const STATE_ICON: Record<string, string> = { open: '📦', fulfilled: '✅', cancelled: '⛔' }

function thisMonth() { return new Date().toISOString().slice(0, 7) }
const n0 = (v: any) => (v == null ? '—' : Number(v).toLocaleString())

type Row = any

// The backend 403 detail names the grant key verbatim; client.ts `api()` throws an Error carrying only
// that detail string (the status code is not preserved), so the key IS the signal.
const isGateError = (m: string) => /ma_handset_cogs/i.test(m) || /restricted/i.test(m)

function LockNote() {
  return (
    <div className="card" style={{ padding: 18, marginTop: 14, fontSize: 13, lineHeight: 1.7,
      background: 'var(--surface2, #f8fafc)', border: '1px solid var(--border)' }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>🔒 This report is restricted</div>
      Ask an admin to grant <b>“Marketplace handset COGS report”</b> on your role
      (Roles &amp; Access → your role → sensitive data grants). This report has <b>no default access</b>:
      what the company pays for handset inventory — the lines, the quantities and the costs — is
      restricted for everyone until it is explicitly granted.
      <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
        Nothing is wrong with your login — administrators and company-wide roles already have it.
      </div>
    </div>
  )
}

export default function MaHandsetCogsPage() {
  const { permissions } = useAuth()
  const clientGranted = hasDataGrant(permissions, 'ma_handset_cogs')
  const [locked, setLocked] = useState(false)
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter(thisMonth()))
  const [win, setWin] = useState(1)
  const [groupBy, setGroupBy] = useState('product')
  const [basis, setBasis] = useState('unit')
  const [products, setProducts] = useState<string[]>([])
  const [statuses, setStatuses] = useState<string[]>([])
  const [orderTypes, setOrderTypes] = useState<string[]>([])
  const [states, setStates] = useState<string[]>([])
  const [monthsSel, setMonthsSel] = useState<string[]>([])
  const [openOnly, setOpenOnly] = useState(false)
  const [minDays, setMinDays] = useState(0)
  const [showMapping, setShowMapping] = useState(false)
  const [d, setD] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    if (!clientGranted) { setLocked(true); setBusy(false); return }   // no grant → don't fire a doomed request
    setBusy(true); setMsg('')
    try {
      const qs = new URLSearchParams({
        period: filt.period || thisMonth(), window_months: String(win),
        group_by: groupBy, price_basis: basis,
      })
      if (filt.stores.length) qs.set('stores', filt.stores.join(','))
      if (filt.markets.length) qs.set('markets', filt.markets.join(','))
      if (products.length) qs.set('products', products.join(','))
      if (statuses.length) qs.set('statuses', statuses.join(','))
      if (orderTypes.length) qs.set('order_types', orderTypes.join(','))
      if (states.length) qs.set('states', states.join(','))
      if (monthsSel.length) qs.set('months', monthsSel.join(','))
      if (openOnly) qs.set('open_only', '1')
      if (minDays) qs.set('min_days_open', String(minDays))
      setD(await api(`/api/v1/commcalc/ma-handset-cogs?${qs.toString()}${orgParam()}`))
    } catch (e: any) {
      const m = String(e?.message || e)
      if (isGateError(m)) { setLocked(true); setD(null) } else setMsg('❌ ' + m)
    }
    setBusy(false)
  }, [filt, win, groupBy, basis, products, statuses, orderTypes, states, monthsSel, openOnly, minDays, clientGranted])

  useEffect(() => { load() }, [])            // eslint-disable-line react-hooks/exhaustive-deps
  const didMount = useRef(false)
  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return }
    load()
  }, [load])

  // Pick-don't-type options — computed by the backend from the UNFILTERED rows, so a picker never
  // collapses to the current selection.
  const shipOpts: EntityOption[] = (d?.ship_to_options || []).map((s: string) => ({ id: s, label: s }))
  const marketOpts: EntityOption[] = (d?.market_options || []).map((s: string) => ({ id: s, label: s }))
  const productOpts: EntityOption[] = (d?.product_options || []).map((s: string) => ({ id: s, label: s }))
  const statusOpts: EntityOption[] = (d?.status_options || []).map((s: string) => ({ id: s, label: s }))
  const typeOpts: EntityOption[] = (d?.order_type_options || []).map((s: string) => ({ id: s, label: s }))
  const stateOpts: EntityOption[] = (d?.state_options || []).map((o: any) => ({ id: o.id, label: o.label }))
  const monthOpts: EntityOption[] = (d?.month_options || []).map((o: any) => ({ id: o.id, label: o.label }))
  const groupOpts: { id: string; label: string }[] = d?.group_by_options || []

  const rows: Row[] = d?.rows || []
  const groups: any[] = d?.groups || []
  const t = d?.tiles

  // RULE FOUR columns — the same rows on screen, exported verbatim.
  const cols: ExportColumn[] = [
    { header: 'Ordered', field: 'date_ordered', type: 'date', role: 'date', get: (r: Row) => r.date_ordered || '' },
    { header: 'Order #', field: 'order_number', get: (r: Row) => r.order_number || '' },
    { header: 'Product', field: 'product_label', get: (r: Row) => r.product_label || '' },
    { header: 'Qty', field: 'qty', type: 'number', get: (r: Row) => r.qty },
    { header: 'Unit price', field: 'unit_price', money: true, get: (r: Row) => r.unit_price },
    { header: 'Extended cost', field: 'ext_cost', money: true, get: (r: Row) => r.ext_cost },
    { header: 'Ship-to', field: 'ship_to_label', role: 'store', get: (r: Row) => r.ship_to_label || '' },
    // A blank market is a REAL answer here (the "(no market)" bucket), so it is NAMED rather than left as
    // an ambiguous empty cell — page, filter and export then all say the same thing.
    { header: 'Market', field: 'market', get: (r: Row) => r.market || (d?.no_market_label || '') },
    { header: 'State', field: 'state_label', get: (r: Row) => r.state_label || '' },
    { header: 'Why', field: 'state_reason', get: (r: Row) => r.state_reason || '' },
    { header: 'Days open', field: 'days_open', type: 'number', get: (r: Row) => r.days_open },
    { header: 'Order status', field: 'order_status', get: (r: Row) => r.order_status || '' },
    { header: 'Order type', field: 'order_type', get: (r: Row) => r.order_type || '' },
    { header: 'Filled', field: 'date_filled', type: 'date', get: (r: Row) => r.date_filled || '' },
    { header: 'Shipped', field: 'date_shipped', type: 'date', get: (r: Row) => r.date_shipped || '' },
    { header: 'Tracking', field: 'tracking_number', get: (r: Row) => r.tracking_number || '' },
    { header: 'TSPID', field: 'tspid', get: (r: Row) => r.tspid || '' },
    { header: 'City', field: 'city', get: (r: Row) => r.city || '' },
    { header: 'State (US)', field: 'state_code', get: (r: Row) => r.state_code || '' },
  ]

  const groupCols: ExportColumn[] = [
    { header: d?.group_label || 'Group', field: 'label', get: (g: any) => g.label },
    { header: 'Lines', field: 'lines', type: 'number', get: (g: any) => g.lines },
    { header: 'Orders', field: 'orders', type: 'number', get: (g: any) => g.orders },
    { header: 'Units', field: 'units', type: 'number', get: (g: any) => g.units },
    { header: 'COGS', field: 'cogs', money: true, get: (g: any) => g.cogs },
    { header: 'Avg unit cost', field: 'avg_unit_cost', money: true, get: (g: any) => g.avg_unit_cost },
    { header: 'Open units', field: 'open_units', type: 'number', get: (g: any) => g.open_units },
    { header: 'Open $', field: 'open_cogs', money: true, get: (g: any) => g.open_cogs },
    { header: 'Cancelled $', field: 'cancelled_cogs', money: true, get: (g: any) => g.cancelled_cogs },
    { header: 'First order', field: 'first_order', type: 'date', get: (g: any) => g.first_order || '' },
    { header: 'Last order', field: 'last_order', type: 'date', get: (g: any) => g.last_order || '' },
  ]

  const subtitle = useMemo(() => {
    const bits = [`${rows.length} line(s)`, d?.definition_note, d?.basis_note].filter(Boolean)
    return bits.join(' · ')
  }, [rows.length, d?.definition_note, d?.basis_note])

  const header = (
    <div style={{ marginBottom: 14 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📦 Marketplace Handset COGS</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
        What the handsets we ordered actually cost — quantity × unit price — by product, month and
        ship-to, with the <b>open (unfulfilled)</b> orders first. These are dollars the distributor
        invoiced us; nothing here changes what anyone is paid.
      </p>
    </div>
  )

  // NO DEFAULT ACCESS: without the grant nothing is rendered — not the filters, not the tiles, not the
  // costs. (The backend refuses independently.)
  if (locked) {
    return <div style={{ maxWidth: 1280 }}>{header}<LockNote /></div>
  }

  return (
    <div style={{ maxWidth: 1280 }}>
      {header}

      {/* RULE FIVE core set (period · stores/ship-to · market) + the appended module facets.
          `reps` is intentionally not rendered — see the file header. */}
      <StandardFilterBar
        value={filt} onChange={setFilt} periodMode="month"
        show={{ period: true, stores: true, markets: true, reps: false }}
        storeOptions={shipOpts} marketOptions={marketOpts}
        storeLabel="Ship-to stores…"
        right={<>
          <label style={lbl}>Window
            <select style={sel} value={win} onChange={e => setWin(Number(e.target.value))}>
              {[1, 2, 3, 6, 12].map(n => <option key={n} value={n}>{n === 1 ? 'this month' : `last ${n} months`}</option>)}
            </select>
          </label>
          <label style={lbl}>Group by
            <select style={sel} value={groupBy} onChange={e => setGroupBy(e.target.value)}>
              {(groupOpts.length ? groupOpts : [{ id: 'product', label: 'Product' }]).map(o =>
                <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </label>
          <label style={lbl} title="How the feed's Price column is read. Device History reads it as a per-device price, so 'per unit' is the default.">Price is
            <select style={sel} value={basis} onChange={e => setBasis(e.target.value)}>
              <option value="unit">per unit</option>
              <option value="line">a line total</option>
            </select>
          </label>
          {productOpts.length > 0 && (
            <EntityPicker multi options={productOpts} value={products} onChange={setProducts}
              placeholder="Products…" width={180} ariaLabel="Filter by product" />
          )}
          {stateOpts.length > 0 && (
            <EntityPicker multi options={stateOpts} value={states} onChange={setStates}
              placeholder="State…" width={165} ariaLabel="Filter by order state" />
          )}
          {statusOpts.length > 0 && (
            <EntityPicker multi options={statusOpts} value={statuses} onChange={setStatuses}
              placeholder="Order status…" width={160} ariaLabel="Filter by raw order status" />
          )}
          {typeOpts.length > 0 && (
            <EntityPicker multi options={typeOpts} value={orderTypes} onChange={setOrderTypes}
              placeholder="Order type…" width={150} ariaLabel="Filter by order type" />
          )}
          {monthOpts.length > 1 && (
            <EntityPicker multi options={monthOpts} value={monthsSel} onChange={setMonthsSel}
              placeholder="Months…" width={155} ariaLabel="Filter by order month" />
          )}
          <label style={lbl} title="Only order lines with no fill or ship date yet">
            <input type="checkbox" checked={openOnly} onChange={e => setOpenOnly(e.target.checked)} /> Open only
          </label>
          <label style={lbl}>Open ≥
            <select style={sel} value={minDays} onChange={e => setMinDays(Number(e.target.value))}>
              {[0, 7, 14, 30, 60].map(n => <option key={n} value={n}>{n === 0 ? 'any age' : `${n} days`}</option>)}
            </select>
          </label>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy} onClick={() => load()}>
            {busy ? '…' : '↻ Reload'}
          </button>
        </>}
      />

      {msg && <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13 }}>{msg}</div>}

      {d?.ready && <>
        {/* Tiles — the OPEN bucket is a headline, not a footnote. */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
          <div style={tile}>
            <div style={tileCap}>Handset COGS</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{fmt(t.cogs)}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              {d.period}{d.window_months > 1 ? ` · ${d.window_months} months` : ''} · committed (excl. cancelled)
            </div>
          </div>
          <div style={tile}>
            <div style={tileCap}>Units / lines / orders</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{n0(t.units)}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{n0(t.lines)} lines · {n0(t.orders)} orders</div>
          </div>
          <button onClick={() => setOpenOnly(v => !v)} style={{ ...tile, textAlign: 'left', cursor: 'pointer',
            background: openOnly ? 'var(--surface2)' : 'transparent',
            borderColor: t.open.lines ? '#fcd34d' : 'var(--border)' }}>
            <div style={tileCap}>{STATE_ICON.open} Open orders</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: t.open.lines ? STATE_TINT.open : undefined }}>{n0(t.open.lines)}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              {n0(t.open.units)} units · {fmt(t.open.amount)}
              {t.oldest_open_days != null ? ` · oldest ${t.oldest_open_days}d` : ''}
            </div>
          </button>
          <div style={tile}>
            <div style={tileCap}>Avg unit cost</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>{t.avg_unit_cost == null ? '—' : fmt(t.avg_unit_cost)}</div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{n0(t.products)} products · {n0(t.ship_tos)} ship-to</div>
          </div>
          {t.cancelled.lines > 0 && (
            <div style={{ ...tile, borderColor: '#fca5a5' }}>
              <div style={tileCap}>{STATE_ICON.cancelled} Cancelled</div>
              <div style={{ fontSize: 22, fontWeight: 800, color: STATE_TINT.cancelled }}>{n0(t.cancelled.lines)}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>{fmt(t.cancelled.amount)} · not counted as cost</div>
            </div>
          )}
        </div>

        {/* What the report MEANS — on the page AND carried into every export subtitle. */}
        <div className="card" style={{ padding: '10px 14px', marginBottom: 12, fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
          <div><b>Definition.</b> {d.definition_note}</div>
          <div><b>Basis.</b> {d.basis_note}</div>
          <div><b>Open orders.</b> {d.open_note}</div>
          <div><b>Source.</b> <code>{d.source_table}</code> · window {d.window_from} → {d.window_to}</div>
          {d.unmapped_market_rows > 0 && (
            <div><b>Market coverage.</b> {d.unmapped_market_rows} of {d.unfiltered_rows} line(s) have no
              market and are grouped under <b>{d.no_market_label}</b> — pick that bucket to see them, or
              map the ship-to store at{' '}
              <a href="/commcalc/store-match" style={{ color: 'var(--accent,#2563eb)' }}>Store Matching</a>
              {' '}and they will filter by market with no other change.
            </div>
          )}
        </div>

        {d.note && (
          <div className="card" style={{ padding: 12, marginBottom: 12, fontSize: 13, background: '#fffbeb', border: '1px solid #fde68a' }}>
            ⚠️ {d.note}
          </div>
        )}

        {/* WHERE THESE NUMBERS COME FROM — the NAMED cost targets in the existing upload mapping, with
            their asset-lending equivalents, so a $0 column is traceable to an unmapped field. */}
        {d.cost_fields?.length > 0 && (
          <div className="card" style={{ padding: 0, marginBottom: 12 }}>
            <button onClick={() => setShowMapping(v => !v)}
              style={{ width: '100%', textAlign: 'left', padding: '10px 14px', background: 'transparent', border: 0, cursor: 'pointer', fontWeight: 700, fontSize: 13 }}>
              {showMapping ? '▾' : '▸'} Cost fields &amp; how they are mapped
              <span style={{ fontWeight: 400, color: 'var(--text3)' }}>
                {' '}({d.cost_fields.filter((f: any) => f.mapped).length}/{d.cost_fields.length} mapped · {d.cost_map_source})
              </span>
            </button>
            {showMapping && (
              <div style={{ borderTop: '1px solid var(--border)' }}>
                <div style={{ padding: '8px 14px', fontSize: 12, color: 'var(--text3)', lineHeight: 1.6 }}>
                  These are the named targets of the existing upload mapping for{' '}
                  <b>MA - Marketplace Handset Fulfillment Orders</b>. Change them at{' '}
                  <a href="/commcalc/ma-upload" style={{ color: 'var(--accent,#2563eb)' }}>MA upload → map columns</a>.
                  The right-hand column is the equivalent field in the <b>Asset Lending</b> file, so one
                  handset cost is described the same way in both places. Nothing here writes to the asset
                  ledger.
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['Field', 'Column', 'Mapped from', 'Asset-lending equivalent'].map(h => <th key={h} style={th}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {d.cost_fields.map((f: any) => (
                      <tr key={f.col} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={td}>
                          {f.cost ? '💵 ' : ''}{f.label}
                          {f.unit ? <span style={{ color: 'var(--text3)' }}> ({f.unit})</span> : null}
                        </td>
                        <td style={{ ...td, fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>{f.col}</td>
                        <td style={td}>
                          {f.mapped
                            ? <span style={{ color: '#15803d' }}>✓ {f.source_header}</span>
                            : <span style={{ color: '#b45309' }}>not mapped</span>}
                        </td>
                        <td style={{ ...td, fontSize: 12, color: 'var(--text2)' }}>
                          {f.asset_label ? <b>{f.asset_label}</b> : <span style={{ color: 'var(--text3)' }}>—</span>}
                          {f.asset_field ? <span style={{ color: 'var(--text3)' }}> → {f.asset_field}</span> : null}
                          {f.parity_note ? <div style={{ color: 'var(--text3)' }}>{f.parity_note}</div> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* The ROLLUP the operator asked for — by product / month / ship-to / market / … */}
        {groups.length > 0 && (
          <div className="card" style={{ padding: 0, marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px' }}>
              <div style={{ fontWeight: 700, fontSize: 13 }}>
                By {d.group_label?.toLowerCase()} <span style={{ fontWeight: 400, color: 'var(--text3)' }}>({groups.length})</span>
              </div>
              <ReportExportBar title={`Marketplace handset COGS by ${d.group_label} — ${d.period}`}
                subtitle={subtitle}
                filename={`ma-handset-cogs-by-${d.group_by}-${String(d.period).replace(/\s+/g, '-')}`}
                columns={groupCols} rows={groups} />
            </div>
            <div style={{ borderTop: '1px solid var(--border)', overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {[d.group_label, 'Lines', 'Orders', 'Units', 'COGS', 'Avg unit', 'Open units', 'Open $', 'Cancelled $'].map(h =>
                    <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {groups.map(g => (
                    <tr key={g.key || g.label} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={td}>{g.label}</td>
                      <td style={td}>{n0(g.lines)}</td>
                      <td style={td}>{n0(g.orders)}</td>
                      <td style={td}>{n0(g.units)}</td>
                      <td style={{ ...td, fontWeight: 700 }}>{fmt(g.cogs)}</td>
                      <td style={td}>{g.avg_unit_cost == null ? '—' : fmt(g.avg_unit_cost)}</td>
                      <td style={{ ...td, color: g.open_units ? STATE_TINT.open : undefined }}>{n0(g.open_units)}</td>
                      <td style={{ ...td, color: g.open_cogs ? STATE_TINT.open : undefined }}>{fmt(g.open_cogs)}</td>
                      <td style={{ ...td, color: g.cancelled_cogs ? STATE_TINT.cancelled : undefined }}>{fmt(g.cancelled_cogs)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {d.truncated && (
          <div style={{ fontSize: 12, color: '#b45309', marginBottom: 8 }}>
            Showing the first {rows.length} of {d.total_rows} matching order lines — the tiles and the
            rollup above still describe all {d.total_rows}. Narrow the filters to see the rest.
          </div>
        )}

        <ReportShell
          title={`Marketplace Handset COGS — ${d.period}`}
          subtitle={subtitle}
          filename={`ma-handset-cogs-${String(d.period).replace(/\s+/g, '-')}`}
          columns={cols}
          rows={rows}
          totals
          stickyHeader
          rowStyle={(r: Row) => (r.state === 'open' ? { background: 'rgba(245,158,11,0.07)' }
            : r.state === 'cancelled' ? { background: 'rgba(239,68,68,0.06)' } : undefined)}
        />

        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 14, lineHeight: 1.6 }}>
          State key: <b style={{ color: STATE_TINT.open }}>{STATE_ICON.open} Open</b> — no fill or ship date
          yet (ordered, not in hand). <b style={{ color: STATE_TINT.fulfilled }}>{STATE_ICON.fulfilled} Fulfilled</b> —
          a fill/ship date arrived, or the status says so. <b style={{ color: STATE_TINT.cancelled }}>{STATE_ICON.cancelled} Cancelled</b> —
          shown, but never counted as a cost. A line with no price is counted and left out of the COGS sum
          rather than added as $0. Note that the table&apos;s TOTAL row adds up every line ON SCREEN
          (cancelled included, because they are listed), while the <b>Handset COGS</b> tile and the rollup
          are the <b>committed</b> cost with cancelled lines excluded — the Cancelled tile is the
          difference. Wiring these costs into a single device-cost ledger or the P&amp;L is a separate,
          money-touching decision and is not done here.
        </div>
      </>}

      {!d && !busy && !msg && <div className="card" style={{ padding: 14, fontSize: 13 }}>Loading…</div>}
    </div>
  )
}
