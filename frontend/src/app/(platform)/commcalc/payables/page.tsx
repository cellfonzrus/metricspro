'use client'
import { useState, useEffect, useMemo, useCallback } from 'react'
import { api, fmt } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, filterRows, optionsFromRows, type StandardFilterValue } from '@/lib/standard-filters'
import type { StoreOpt } from '@/lib/market-store-cascade'
import { SortableTh, useTableSort } from '@/components/SortableTh'

// Device Forecasting & Vendor Payables (module 095). Reads the config-driven ledger built by
// POST /api/v1/payables/rebuild. Forecasting is phones-only; payables + due are per-IMEI.

type Tab = 'forecast' | 'payables' | 'owed' | 'map'
const sel = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' } as const
const STATUS_COLORS: Record<string, string> = {
  discrepancy: '#dc2626', due: '#d97706', offset: '#16a34a', open: '#6b7280', unconfigured: '#6b7280',
}

// One unmapped raw-model candidate → map it to a canonical name + carrier. Own local state so typing
// isn't lost on parent re-renders (defined at module scope, not inside the page component).
function MapRow({ cand, carriers, onSave }: any) {
  const [canonical, setCanonical] = useState(cand.raw_model)
  const [cid, setCid] = useState(cand.carrier_id || '')
  const [saving, setSaving] = useState(false)
  const cell = { padding: '7px 10px', borderBottom: '1px solid var(--border)', fontSize: 13 }
  return (
    <tr>
      <td style={cell}><div style={{ fontSize: 12 }}>{cand.raw_model}</div><div style={{ fontSize: 11, color: 'var(--muted)' }}>{cand.side} · {cand.count}×</div></td>
      <td style={cell}><input value={canonical} onChange={e => setCanonical(e.target.value)} style={{ ...sel, width: '100%' }} /></td>
      <td style={cell}>
        <select value={cid} onChange={e => setCid(e.target.value)} style={sel}>
          <option value="">(carrier)</option>
          {carriers.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </td>
      <td style={cell}>
        <button disabled={saving} onClick={async () => { setSaving(true); try { await onSave(cand.raw_model, canonical, cid, cand.side) } finally { setSaving(false) } }}
          style={{ padding: '5px 12px', borderRadius: 6, border: 'none', background: 'var(--accent, #2563eb)', color: '#fff', fontSize: 12, cursor: 'pointer' }}>
          {saving ? '…' : 'Save'}
        </button>
      </td>
    </tr>
  )
}

export default function PayablesPage() {
  const [tab, setTab] = useState<Tab>('payables')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  // forecast
  const [lookback, setLookback] = useState(7)
  const [horizon, setHorizon] = useState(7)
  const [fRows, setFRows] = useState<any[]>([])
  const [fMeta, setFMeta] = useState<any>({})
  // phone mapping
  const [candidates, setCandidates] = useState<any[]>([])
  const [mappings, setMappings] = useState<any[]>([])
  const [carriersList, setCarriersList] = useState<any[]>([])
  // payables
  const [status, setStatus] = useState('')
  const [pRows, setPRows] = useState<any[]>([])
  const [drill, setDrill] = useState<any | null>(null)
  // owed-by-date
  const [oRows, setORows] = useState<any[]>([])
  const [oMeta, setOMeta] = useState<any>({})
  const [loading, setLoading] = useState(false)
  const [settings, setSettings] = useState<any>({ priority_ack_enabled: false, priority_window_pct: 25 })
  const [showSettings, setShowSettings] = useState(false)
  // RULE FIVE — shared standardized store/market filter (pick-don't-type, org-scoped options). Drives the
  // forecast + payables tabs CLIENT-SIDE (their rows carry `store`). The Daily-Owed tab is a server-side
  // date-aggregate with no store dimension, so it honors a SINGLE selected store server-side (0 or >1
  // selected → all stores). org_id is injected by the shared client from the caller's JWT — never hardcoded.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const owedStore = filt.stores.length === 1 ? filt.stores[0] : ''

  // The ledger rows carry a store but NO market, so the cascade's market vocabulary comes from the
  // org roster (the same org-scoped pick-don't-type source /core/filter-options serves every other bar).
  const [roster, setRoster] = useState<StoreOpt[]>([])
  useEffect(() => { api(`/api/v1/payables/settings`).then(setSettings).catch(() => {}) }, [])
  useEffect(() => {
    api(`/api/v1/core/filter-options`)
      .then((d: any) => setRoster((d?.stores || []).map((x: any) => ({ id: x.store, label: x.store, market: x.market || null }))))
      .catch(() => setRoster([]))
  }, [])
  useEffect(() => { const t = new URLSearchParams(window.location.search).get('tab'); if (t && ['payables', 'forecast', 'owed', 'map'].includes(t)) setTab(t as Tab) }, [])
  useEffect(() => { if (tab === 'forecast') loadForecast() }, [tab, lookback, horizon])
  useEffect(() => { if (tab === 'payables') loadPayables() }, [tab, status])
  useEffect(() => { if (tab === 'owed') loadOwed() }, [tab, owedStore])
  useEffect(() => { if (tab === 'map') loadMap() }, [tab])

  async function rebuild() {
    setBusy(true); setMsg('Rebuilding ledger… (may take a minute for a full Boost rebuild)')
    try {
      const r = await api(`/api/v1/payables/rebuild`, { method: 'POST' })
      const sc = r.status_counts || {}
      setMsg(`Rebuilt ${r.written} devices across ${r.carriers} carrier(s): ` +
        Object.entries(sc).map(([k, v]) => `${v} ${k}`).join(', ') +
        (r.flags_written != null ? ` · ${r.flags_written} discrepancy flags` : ''))
      if (tab === 'payables') loadPayables(); else if (tab === 'forecast') loadForecast(); else loadOwed()
    } catch (e: any) { setMsg('Rebuild error (it may still be completing server-side): ' + e.message) }
    setBusy(false)
  }

  async function loadForecast() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ lookback: String(lookback), horizon: String(horizon) })
      const d = await api(`/api/v1/payables/forecast?${qs}`); setFRows(d.rows || []); setFMeta(d)
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function loadMap() {
    setLoading(true)
    try {
      const [c, m] = await Promise.all([
        api(`/api/v1/payables/phone-map/candidates`),
        api(`/api/v1/payables/phone-map`),
      ])
      setCandidates(c.rows || []); setCarriersList(c.carriers || m.carriers || []); setMappings(m.rows || [])
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function saveMapping(raw: string, canonical: string, cid: string, side: string) {
    await api(`/api/v1/payables/phone-map`, { method: 'POST', body: JSON.stringify({ raw_model: raw, canonical_model: canonical, carrier_id: cid || null, side }) })
    setCandidates(prev => prev.filter(c => c.raw_model !== raw))    // drop the mapped one locally (candidates rescan is heavy)
    api(`/api/v1/payables/phone-map`).then((m: any) => setMappings(m.rows || [])).catch(() => {})
  }
  async function deleteMapping(id: string) {
    try { await api(`/api/v1/payables/phone-map/${id}`, { method: 'DELETE' }); loadMap() }
    catch (e: any) { setMsg(e.message) }
  }
  async function loadPayables() {
    setLoading(true)
    try {
      const qs = new URLSearchParams()
      if (status) qs.set('status', status)
      const d = await api(`/api/v1/payables/payables${qs.toString() ? `?${qs}` : ''}`); setPRows(d.rows || [])
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function loadOwed() {
    setLoading(true)
    try {
      const qs = new URLSearchParams()
      if (owedStore) qs.set('store', owedStore)   // date-aggregate has no store dim → single-store server filter
      const d = await api(`/api/v1/payables/owed-by-date${qs.toString() ? `?${qs}` : ''}`); setORows(d.rows || []); setOMeta(d || {})
    } catch (e) { console.error(e) }
    setLoading(false)
  }
  async function openDrill(imei: string) {
    try { setDrill(await api(`/api/v1/payables/offsets/${encodeURIComponent(imei)}`)) }
    catch (e: any) { setMsg(e.message) }
  }
  async function saveSettings(patch: any) {
    const next = { ...settings, ...patch }; setSettings(next)
    try { await api(`/api/v1/payables/settings`, { method: 'PUT', body: JSON.stringify(patch) }) }
    catch (e: any) { setMsg(e.message) }
  }

  // Pick-don't-type filter options from the ALREADY-org-scoped rows the page loaded (forecast ∪ payables
  // carry `store`; `market` shows only if the ledger has it → the market picker self-hides when absent).
  const facc = { store: (r: any) => r.store, market: (r: any) => r.market }
  const filterOpts = useMemo(() => optionsFromRows([...fRows, ...pRows], facc), [fRows, pRows])   // eslint-disable-line react-hooks/exhaustive-deps
  // Cascade options = the org roster (which carries the markets) UNION any store present in the loaded
  // rows but missing from the roster — a store that genuinely has payables must never become unselectable
  // just because it isn't on the roster yet.
  const cascade: StoreOpt[] = useMemo(() => {
    const seen = new Map<string, StoreOpt>()
    roster.forEach(r => seen.set(r.id.trim().toLowerCase(), r))
    filterOpts.stores.forEach(st => {
      const k = st.trim().toLowerCase()
      if (!seen.has(k)) seen.set(k, { id: st, label: st, market: null })
    })
    return [...seen.values()].sort((a, b) => a.label.localeCompare(b.label))
  }, [roster, filterOpts.stores])

  const fRowsF = useMemo(() => filterRows(fRows, filt, facc), [fRows, filt])   // eslint-disable-line react-hooks/exhaustive-deps
  const pRowsF = useMemo(() => filterRows(pRows, filt, facc), [pRows, filt])   // eslint-disable-line react-hooks/exhaustive-deps

  // Click-a-header sorting (owner 2026-08-10, "for all reports"). One hook per table so a column picked
  // on Payables doesn't follow you to Forecast, where it has no meaning.
  const getCell = useCallback((r: any, field: string) => r?.[field], [])
  const fSort = useTableSort(fRowsF, getCell)
  const pSort = useTableSort(pRowsF, getCell)
  const oSort = useTableSort(oRows, getCell)

  const th = { textAlign: 'left' as const, padding: '8px 10px', borderBottom: '2px solid var(--border)', fontSize: 12, color: 'var(--muted)' }
  const td = { padding: '7px 10px', borderBottom: '1px solid var(--border)', fontSize: 13 }

  // RULE FOUR export — reflects the ACTIVE tab (what-you-see-is-what-exports). The Phone-Mapping tab is a
  // config surface (mapping input), so it is exempt (no export bar there).
  const exportSpec = (): { title: string; filename: string; columns: ExportColumn[]; rows: any[] } | null => {
    if (tab === 'forecast') return {
      title: 'Device Forecast', filename: 'device_forecast', rows: fRowsF,
      columns: [
        { header: 'Carrier', field: 'carrier', get: (r: any) => r.carrier },
        { header: 'Store', field: 'store', role: 'store', get: (r: any) => r.store || '' },
        { header: 'Model', field: 'device_model', get: (r: any) => r.device_model },
        { header: 'Sold', get: (r: any) => r.units },
        { header: 'Velocity/day', get: (r: any) => r.avg_daily_velocity },
        { header: 'Projected', get: (r: any) => r.projected_demand },
        { header: 'On hand', get: (r: any) => r.on_hand },
        { header: 'Order', get: (r: any) => r.recommend_order },
      ],
    }
    if (tab === 'payables') return {
      title: 'Vendor Payables (per IMEI)', filename: 'vendor_payables', rows: pRowsF,
      columns: [
        { header: 'IMEI', field: 'imei', get: (r: any) => r.imei },
        { header: 'Store', field: 'store', role: 'store', get: (r: any) => r.store },
        { header: 'Model', field: 'device_model', get: (r: any) => r.device_model },
        { header: 'Owed', money: true, get: (r: any) => r.owed ?? 0 },
        { header: 'Rebate', money: true, get: (r: any) => r.rebate_amount ?? 0 },
        { header: 'Net owed', money: true, get: (r: any) => r.net_owed ?? 0 },
        { header: 'Due', field: 'due_date', type: 'date', get: (r: any) => r.due_date || '' },
        { header: 'Status', field: 'status', get: (r: any) => r.status },
      ],
    }
    if (tab === 'owed') return {
      title: 'Daily Owed', filename: 'daily_owed', rows: oRows,
      columns: [
        { header: 'Due date', field: 'due_date', type: 'date', get: (r: any) => r.due_date },
        { header: 'Devices', get: (r: any) => r.count },
        { header: 'Owed', money: true, get: (r: any) => r.owed },
      ],
    }
    return null
  }
  const exp = exportSpec()

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Device Forecasting &amp; Vendor Payables</h1>
          <p style={{ color: 'var(--muted)', fontSize: 13, margin: '4px 0 0' }}>
            Per-IMEI owed vs due, reconciled against sold + rebate received — and phones-only stock forecasting.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setShowSettings(s => !s)}
            style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer' }}>⚙ Settings</button>
          <button onClick={rebuild} disabled={busy}
            style={{ padding: '8px 16px', borderRadius: 8, border: 'none', background: 'var(--accent, #2563eb)', color: '#fff', fontWeight: 600, cursor: busy ? 'wait' : 'pointer' }}>
            {busy ? 'Rebuilding…' : '↻ Rebuild ledger'}
          </button>
        </div>
      </div>
      {showSettings && (
        <div style={{ marginTop: 12, padding: 14, borderRadius: 10, background: 'var(--surface)', border: '1px solid var(--border)', display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            <input type="checkbox" checked={!!settings.priority_ack_enabled} onChange={e => saveSettings({ priority_ack_enabled: e.target.checked })} />
            Require clock-in acknowledgment of priority phones
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            Priority window (final %):
            <input type="number" min={1} max={100} value={settings.priority_window_pct}
              onChange={e => setSettings((s: any) => ({ ...s, priority_window_pct: +e.target.value }))}
              onBlur={e => saveSettings({ priority_window_pct: +e.target.value })} style={{ ...sel, width: 70 }} />
          </label>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>The % applies on the next Rebuild.</span>
        </div>
      )}
      {msg && <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 8, background: 'var(--surface)', border: '1px solid var(--border)', fontSize: 13 }}>{msg}</div>}

      <div style={{ display: 'flex', gap: 8, margin: '18px 0 14px', flexWrap: 'wrap' }}>
        {(['payables', 'forecast', 'owed', 'map'] as Tab[]).map(t => (
          <button key={t} onClick={() => setTab(t)}
            style={{ padding: '7px 14px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, cursor: 'pointer',
              background: tab === t ? 'var(--accent, #2563eb)' : 'var(--surface)', color: tab === t ? '#fff' : 'var(--text)', fontWeight: tab === t ? 600 : 400 }}>
            {t === 'payables' ? 'Payables (per IMEI)' : t === 'forecast' ? 'Forecast (phones)' : t === 'owed' ? 'Daily Owed' : 'Phone Mapping'}
          </button>
        ))}
        {tab === 'forecast' && (
          <>
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              Look-back (days): <input type="number" min={1} max={365} value={lookback} onChange={e => setLookback(+e.target.value || 7)} style={{ ...sel, width: 64 }} />
            </label>
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              Order for next (days): <input type="number" min={1} max={365} value={horizon} onChange={e => setHorizon(+e.target.value || 7)} style={{ ...sel, width: 64 }} />
            </label>
          </>
        )}
        {tab === 'payables' && (
          <select value={status} onChange={e => setStatus(e.target.value)} style={sel}>
            <option value="">All statuses</option>
            <option value="discrepancy">Discrepancy (sold, no rebate)</option>
            <option value="due">Due (unsold, past due)</option>
            <option value="offset">Offset (rebate received)</option>
            <option value="open">Open</option>
          </select>
        )}
        {exp && exp.rows.length > 0 && (
          <>
            <div style={{ flex: 1 }} />
            <ReportExportBar title={exp.title} filename={exp.filename} columns={exp.columns} rows={exp.rows} />
          </>
        )}
      </div>

      {tab !== 'map' && (filterOpts.stores.length > 0 || filterOpts.markets.length > 0) && (
        <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false, reps: false }}
          cascadeStores={cascade}
          storeOptions={filterOpts.stores} marketOptions={filterOpts.markets} />
      )}

      {loading && <div style={{ color: 'var(--muted)', fontSize: 13 }}>Loading…</div>}

      {tab === 'forecast' && !loading && (
        <>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
            Velocity from the last {fMeta.lookback ?? lookback} days → order for the next {fMeta.horizon ?? horizon} days, per carrier.
            {(fMeta.unmapped ?? 0) > 0 && <> · <button onClick={() => setTab('map')} style={{ background: 'none', border: 'none', color: '#d97706', cursor: 'pointer', fontSize: 12, padding: 0, textDecoration: 'underline' }}>{fMeta.unmapped} unmapped model(s)</button> — map them so sales &amp; stock line up.</>}
            {(fMeta.unassigned_store ?? 0) > 0 && <> · <b>{fMeta.unassigned_store}</b> row(s) with no store — a Total/marketplace order is placed against the dealer account, so its store is resolved from the POS line that sold the device; these could not be resolved.</>}
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <SortableTh field="carrier" sort={fSort.sort} onSort={fSort.toggle} style={th}>Carrier</SortableTh>
              <SortableTh field="store" sort={fSort.sort} onSort={fSort.toggle} style={th}>Store</SortableTh>
              <SortableTh field="device_model" sort={fSort.sort} onSort={fSort.toggle} style={th}>Model</SortableTh>
              <SortableTh field="units" sort={fSort.sort} onSort={fSort.toggle} style={th}>Sold ({fMeta.lookback ?? lookback}d)</SortableTh>
              <SortableTh field="avg_daily_velocity" sort={fSort.sort} onSort={fSort.toggle} style={th}>Velocity/day</SortableTh>
              <SortableTh field="projected_demand" sort={fSort.sort} onSort={fSort.toggle} style={th}>Projected ({fMeta.horizon ?? horizon}d)</SortableTh>
              <SortableTh field="on_hand" sort={fSort.sort} onSort={fSort.toggle} style={th}>On hand</SortableTh>
              <SortableTh field="recommend_order" sort={fSort.sort} onSort={fSort.toggle} style={th}>Order</SortableTh>
            </tr></thead>
            <tbody>{fSort.sorted.map((r: any, i: number) => (
              <tr key={i}><td style={td}>{r.carrier}{!r.mapped && <span title="unmapped model — map it on the Phone Mapping tab" style={{ color: '#d97706' }}> •</span>}</td><td style={td}>{r.store || <span style={{ color: 'var(--muted)' }}>(unassigned)</span>}</td><td style={td}>{r.device_model}</td><td style={td}>{r.units}</td><td style={td}>{r.avg_daily_velocity}</td><td style={td}>{r.projected_demand}</td><td style={td}>{r.on_hand}</td>
                <td style={{ ...td, fontWeight: r.recommend_order > 0 ? 700 : 400, color: r.recommend_order > 0 ? '#d97706' : 'inherit' }}>{r.recommend_order}</td></tr>
            ))}</tbody>
          </table>
        </>
      )}

      {/* STATUS LEGEND (owner 2026-08-10: "Payable per imei shows discrepancy — what does that mean and
          how to address that"). Four one-line definitions + the action, so the word on the row is
          self-explanatory instead of needing someone to remember the engine's routing rules. */}
      {tab === 'payables' && !loading && (
        <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10, lineHeight: 1.65, maxWidth: 960 }}>
          <b style={{ color: STATUS_COLORS.discrepancy }}>Discrepancy</b> = the device was <b>sold</b> but
          <b> no equipment rebate / reimbursement came back</b> — the dealer is still carrying its cost. Fix
          it by loading the rebate source (or the vendor invoice, if the cost itself is missing); it clears
          to <b style={{ color: STATUS_COLORS.offset }}>Offset</b> once a rebate ≥ the amount owed lands.
          {' · '}<b style={{ color: STATUS_COLORS.due }}>Due</b> = unsold and past its due date.
          {' · '}<b>Open</b> = inside the window, nothing to do yet.
          {' · '}An <b>Owed</b> of “—” means no invoice has priced this device — a discrepancy at $0 is a
          <i> missing rebate on an unpriced device</i>, not a $0 loss.
        </div>
      )}
      {tab === 'payables' && !loading && (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr>
            <SortableTh field="imei" sort={pSort.sort} onSort={pSort.toggle} style={th}>IMEI</SortableTh>
            <SortableTh field="store" sort={pSort.sort} onSort={pSort.toggle} style={th}>Store</SortableTh>
            <SortableTh field="device_model" sort={pSort.sort} onSort={pSort.toggle} style={th}>Model</SortableTh>
            <SortableTh field="owed" sort={pSort.sort} onSort={pSort.toggle} style={th}>Owed</SortableTh>
            <SortableTh field="rebate_amount" sort={pSort.sort} onSort={pSort.toggle} style={th}>Rebate</SortableTh>
            <SortableTh field="net_owed" sort={pSort.sort} onSort={pSort.toggle} style={th}>Net owed</SortableTh>
            <SortableTh field="due_date" sort={pSort.sort} onSort={pSort.toggle} style={th}>Due</SortableTh>
            <SortableTh field="status" sort={pSort.sort} onSort={pSort.toggle} style={th}>Status</SortableTh>
          </tr></thead>
          <tbody>{pSort.sorted.map((r: any, i: number) => (
            <tr key={i} onClick={() => openDrill(r.imei)} style={{ cursor: 'pointer' }}>
              <td style={td}>{r.imei}</td><td style={td}>{r.store}</td><td style={td}>{r.device_model}</td>
              <td style={td}>{r.owed == null ? '—' : fmt(r.owed)}</td>
              <td style={td}>{r.rebate_amount ? fmt(r.rebate_amount) : '—'}{r.rebate_mismatch && <span title="ePay cross-check mismatch" style={{ color: '#dc2626' }}> ⚠︎</span>}</td>
              <td style={{ ...td, fontWeight: 600 }}>{r.net_owed == null ? '—' : fmt(r.net_owed)}</td>
              <td style={td}>{r.due_date || '—'}</td>
              <td style={td}><span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 11, color: '#fff', background: STATUS_COLORS[r.status] || '#6b7280' }}>{r.status}</span>{r.priority && <span title="Priority sell (final 25% of window)"> 🔥</span>}</td>
            </tr>
          ))}</tbody>
        </table>
      )}

      {tab === 'owed' && !loading && (
        oRows.length === 0 ? (
          // HONEST EMPTY (owner report 2026-08-10: "Daily owed is also not showing, it should show how
          // much is owed"). An empty table here is almost never "you owe nothing" — it is "no device
          // carries an amount", which happens when the tenant's payable source map has no owed field
          // (every Total/MA map: the MA reports say what was ACTIVATED, never what was INVOICED). Say
          // that, and point at the one thing that fixes it, instead of rendering a blank grid.
          <div className="card" style={{ padding: 20, fontSize: 13, color: 'var(--text2)', maxWidth: 820 }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>No dated amounts to show yet.</div>
            This tab groups <b>net owed</b> by due date, so it is empty whenever no device carries an
            amount. That is the case when the carrier&apos;s source map has no <b>owed</b> field — the
            Total / marketplace reports record what was <i>activated</i>, never what was <i>invoiced</i>,
            so there is no price and no due date to group by. Loading the <b>vendor invoices</b> is what
            supplies owed + invoice date + terms; until then every sold device with no rebate lands in{' '}
            <button onClick={() => { setTab('payables'); setStatus('discrepancy') }}
              style={{ background: 'none', border: 'none', color: '#dc2626', cursor: 'pointer', fontSize: 13, padding: 0, textDecoration: 'underline' }}>
              Payables → Discrepancy
            </button>{' '}at $0.
          </div>
        ) : (
          <>
          {/* GRAIN + SOURCE, said out loud (owner 2026-08-10 "if we have the actual due date build that
              and ship it"). When the amounts come from the processor's own feed rather than the
              per-IMEI ledger, the rows are ORDER LINES and there is no device to drill into — the
              reader has to know that before comparing this to the Payables tab. */}
          {oMeta.source === 'vendor_feed' && oMeta.note && (
            <div style={{ fontSize: 12, color: 'var(--text2)', background: 'var(--surface2)', borderRadius: 8, padding: '8px 11px', marginBottom: 10, maxWidth: 980 }}>
              ℹ️ {oMeta.note}
            </div>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <SortableTh field="due_date" sort={oSort.sort} onSort={oSort.toggle} style={th}>Due date</SortableTh>
              <SortableTh field="count" sort={oSort.sort} onSort={oSort.toggle} style={th}>{oMeta.grain === 'order_line' ? 'Order lines' : 'Devices'}</SortableTh>
              <SortableTh field="owed" sort={oSort.sort} onSort={oSort.toggle} style={th}>Owed</SortableTh>
            </tr></thead>
            <tbody>{oSort.sorted.map((r: any, i: number) => (
              <tr key={i}><td style={td}>{r.due_date}</td><td style={td}>{r.count}</td><td style={{ ...td, fontWeight: 600 }}>{fmt(r.owed)}</td></tr>
            ))}</tbody>
            <tfoot><tr>
              <td style={{ ...td, fontWeight: 800 }}>TOTAL</td>
              <td style={td} />
              <td style={{ ...td, fontWeight: 800 }}>{fmt(oMeta.total_owed ?? 0)}</td>
            </tr></tfoot>
          </table>
          </>
        )
      )}

      {tab === 'map' && !loading && (
        <div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
            Map each raw model name (as it appears in sales or inventory) to a canonical model + its carrier, so the forecast can line sales up with stock and split by carrier. This is an onboarding to-do — map the highest-frequency ones first.
          </div>
          <h3 style={{ fontSize: 15, margin: '0 0 8px' }}>Unmapped models ({candidates.length})</h3>
          {candidates.length === 0
            ? <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 20 }}>All seen models are mapped. 🎉</div>
            : <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 24 }}>
                <thead><tr><th style={th}>Raw model (side · freq)</th><th style={th}>Canonical model</th><th style={th}>Carrier</th><th style={th}></th></tr></thead>
                <tbody>{candidates.map((c, i) => <MapRow key={c.raw_model + i} cand={c} carriers={carriersList} onSave={saveMapping} />)}</tbody>
              </table>}
          <h3 style={{ fontSize: 15, margin: '0 0 8px' }}>Existing mappings ({mappings.length})</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>Raw model</th><th style={th}>Canonical</th><th style={th}>Carrier</th><th style={th}></th></tr></thead>
            <tbody>{mappings.map((m: any) => (
              <tr key={m.id}><td style={td}>{m.raw_model}</td><td style={td}>{m.canonical_model}</td><td style={td}>{m.carrier_name || '—'}</td>
                <td style={td}><button onClick={() => deleteMapping(m.id)} style={{ background: 'none', border: 'none', color: '#dc2626', cursor: 'pointer', fontSize: 12 }}>Remove</button></td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {drill && (
        <div onClick={() => setDrill(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }}>
          <div onClick={e => e.stopPropagation()} style={{ background: 'var(--surface)', borderRadius: 12, padding: 24, maxWidth: 560, width: '90%', maxHeight: '80vh', overflow: 'auto' }}>
            <h3 style={{ margin: '0 0 4px' }}>What offsets what — {drill.device_model}</h3>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 14 }}>IMEI {drill.imei} · {drill.store} · {drill.status}{drill.sold ? ` · sold ${drill.sold_date || ''}` : ' · unsold'}</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <tbody>
                <tr><td style={td}>Owed to vendor</td><td style={{ ...td, textAlign: 'right' }}>{drill.owed == null ? `— (${drill.owed_source})` : fmt(drill.owed)}</td></tr>
                <tr><td style={td}>Primary rebate ({drill.primary_rebate?.source})</td><td style={{ ...td, textAlign: 'right', color: '#16a34a' }}>{drill.primary_rebate?.amount ? '− ' + fmt(drill.primary_rebate.amount) : '—'}</td></tr>
                {(drill.epay_crosscheck?.lines || []).map((l: any, i: number) => (
                  <tr key={i}><td style={td}>ePay: {l.type} ({l.date})</td><td style={{ ...td, textAlign: 'right', color: 'var(--muted)' }}>{fmt(l.amount)}</td></tr>
                ))}
                {drill.epay_crosscheck?.mismatch && <tr><td style={td} colSpan={2}><span style={{ color: '#dc2626' }}>⚠︎ ePay cross-check ({fmt(drill.epay_crosscheck.amount || 0)}) disagrees with the primary rebate</span></td></tr>}
                <tr><td style={{ ...td, fontWeight: 700 }}>Net owed</td><td style={{ ...td, textAlign: 'right', fontWeight: 700 }}>{drill.net_owed == null ? '—' : fmt(drill.net_owed)}</td></tr>
              </tbody>
            </table>
            <button onClick={() => setDrill(null)} style={{ marginTop: 16, padding: '7px 16px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer' }}>Close</button>
          </div>
        </div>
      )}
    </div>
  )
}
