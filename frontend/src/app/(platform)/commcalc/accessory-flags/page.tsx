'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

// Accessories sold above the user-defined threshold → flag → push to the rep's chargebacks.
// Phone model comes from the item mapping (the SU sheet). Chargeback $ is per-row editable with a
// configurable default. Unseen items auto-add to the mapping (manage them on Item / Model Mapping).
// Top dashboard rolls the flagged sales up per store + per rep; each row drills into the full receipt.
type Row = {
  sale_id: string; trans_id: string; trans_date: string | null; period: string | null
  store: string | null; rep: string | null; department: string | null; category: string | null
  item_desc: string | null; sku: string | null; ext_price: number; phone_model: string | null
  chargeback_amount: number; dedupe_key: string; already_flagged: boolean; flag_reason?: 'over' | 'under'
}
type Agg = { name: string; txns: number; items: number; flags: number; total: number; chargeback_total: number; flagged: number; over: number; under: number }
type StoreRepAgg = { store: string; rep: string; txns: number; flags: number; items: number; total: number; chargeback_total: number; over: number; under: number }
type Summary = { txns: number; items: number; flags: number; total: number; chargeback_total: number; over: number; under: number }
type ReceiptLine = {
  product_desc: string | null; sku: string | null; item_type: string | null; department: string | null
  category: string | null; contract_type: string | null; ext_price: number; gp: number
  mdn: string | null; serial_1: string | null; voided: boolean
}
type Receipt = { trans_id: string; header: any; lines: ReceiptLine[]; line_count: number; total: number }

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13, whiteSpace: 'nowrap' }
const money = (n: number) => '$' + (Number(n) || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

function firstOfMonth() { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10) }
function today() { return new Date().toISOString().slice(0, 10) }

export default function AccessoryFlagsPage() {
  const [start, setStart] = useState(firstOfMonth())
  const [end, setEnd] = useState(today())
  const [storeF, setStoreF] = useState('')
  const [repF, setRepF] = useState('')
  const [rows, setRows] = useState<Row[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [byRep, setByRep] = useState<Agg[]>([])
  const [byStore, setByStore] = useState<Agg[]>([])
  const [byStoreRep, setByStoreRep] = useState<StoreRepAgg[]>([])
  const [amts, setAmts] = useState<Record<string, number>>({})
  const [picked, setPicked] = useState<Record<string, boolean>>({})
  const [threshold, setThreshold] = useState(35)
  const [minThreshold, setMinThreshold] = useState(0)
  const [defaultCb, setDefaultCb] = useState(0)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [receiptBusy, setReceiptBusy] = useState(false)

  const loadRules = useCallback(() => {
    api('/api/v1/commcalc/flag-rules').then((r: any) => {
      setThreshold(Number(r.accessory_threshold ?? 35)); setDefaultCb(Number(r.accessory_chargeback_amount ?? 0))
      setMinThreshold(Number(r.accessory_min_threshold ?? 0))
    }).catch(() => {})
  }, [])
  useEffect(() => { loadRules() }, [loadRules])

  async function saveRules() {
    setMsg('')
    try {
      await api('/api/v1/commcalc/flag-rules', { method: 'PUT', body: JSON.stringify({
        accessory_threshold: threshold, accessory_min_threshold: minThreshold, accessory_chargeback_amount: defaultCb }) })
      setMsg('Saved as the default thresholds.')
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
  }

  const load = useCallback(async () => {
    setLoading(true); setMsg('')
    try {
      const qs = new URLSearchParams()
      if (start) qs.set('start', start)
      if (end) qs.set('end', end)
      if (threshold || threshold === 0) qs.set('threshold', String(threshold))     // apply the user-defined max now
      if (minThreshold) qs.set('min_threshold', String(minThreshold))              // 0 = under-min check off
      const r = await api(`/api/v1/commcalc/accessory-flags?${qs.toString()}`)
      const rws: Row[] = r.rows || []
      setRows(rws)
      setSummary(r.summary || null); setByRep(r.by_rep || []); setByStore(r.by_store || []); setByStoreRep(r.by_store_rep || [])
      setThreshold(Number(r.threshold ?? threshold)); setDefaultCb(Number(r.default_chargeback ?? defaultCb))
      setMinThreshold(Number(r.min_threshold ?? minThreshold))
      setAmts(Object.fromEntries(rws.map(x => [x.dedupe_key, x.chargeback_amount])))
      setPicked({})
      const um = r.summary?.under ? ` (${r.summary.under} under ${money(r.min_threshold)})` : ''
      setMsg(`${rws.length} flagged accessory sale(s)${um}${r.flagged_qty ? ` · ${r.flagged_qty} already pushed` : ''}.`)
    } catch (e: any) { setMsg('Load failed: ' + (e?.message || e)) }
    setLoading(false)
  }, [start, end, threshold, minThreshold, defaultCb])

  async function openReceipt(trans_id: string) {
    setReceiptBusy(true); setReceipt(null)
    try { setReceipt(await api(`/api/v1/commcalc/accessory-flags/receipt?trans_id=${encodeURIComponent(trans_id)}`)) }
    catch (e: any) { setMsg('Receipt failed: ' + (e?.message || e)) }
    setReceiptBusy(false)
  }

  const stores = Array.from(new Set(rows.map(r => r.store || '').filter(Boolean))).sort()
  const reps = Array.from(new Set(rows.map(r => r.rep || '').filter(Boolean))).sort()
  const filtered = rows.filter(r => (!storeF || r.store === storeF) && (!repF || r.rep === repF))
  const pickedRows = filtered.filter(r => picked[r.dedupe_key])

  function toggleAll(v: boolean) {
    const p: Record<string, boolean> = { ...picked }
    filtered.forEach(r => { p[r.dedupe_key] = v })
    setPicked(p)
  }

  async function pushSelected() {
    if (!pickedRows.length) { setMsg('Select at least one row to flag.'); return }
    const noRep = pickedRows.filter(r => !r.rep)
    if (noRep.length && !confirm(`${noRep.length} selected row(s) have no salesperson and will be skipped. Continue?`)) return
    if (!confirm(`Flag ${pickedRows.length} accessory sale(s) and add them to the sellers' chargebacks?`)) return
    setMsg('Pushing…')
    try {
      const payload = pickedRows.map(r => ({ ...r, chargeback_amount: amts[r.dedupe_key] ?? r.chargeback_amount }))
      const res = await api('/api/v1/commcalc/accessory-flags/push', { method: 'POST', body: JSON.stringify({ rows: payload }) })
      setMsg(`Pushed ${res.pushed} to chargebacks${res.errors?.length ? ` · ${res.errors.length} skipped` : ''}.`)
      await load()
    } catch (e: any) { setMsg('Push failed: ' + (e?.message || e)) }
  }

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔖 Accessory Flags</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Accessories sold <b>over</b> the max threshold or <b>under</b> the allowed minimum (underselling), by store + rep + date range. Click any row for the full receipt; flag rows to push a chargeback to the rep who sold it.
        </p>
      </div>

      {/* Filters (top of page) */}
      <div className="card" style={{ padding: 14, marginBottom: 14, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>From<br /><input type="date" style={{ ...sel, marginTop: 4 }} value={start} onChange={e => setStart(e.target.value)} /></label>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>To<br /><input type="date" style={{ ...sel, marginTop: 4 }} value={end} onChange={e => setEnd(e.target.value)} /></label>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Store<br />
          <select style={{ ...sel, marginTop: 4, minWidth: 150 }} value={storeF} onChange={e => setStoreF(e.target.value)}>
            <option value="">All stores</option>{stores.map(s => <option key={s} value={s}>{s}</option>)}</select></label>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Rep<br />
          <select style={{ ...sel, marginTop: 4, minWidth: 150 }} value={repF} onChange={e => setRepF(e.target.value)}>
            <option value="">All reps</option>{reps.map(s => <option key={s} value={s}>{s}</option>)}</select></label>
        <button className="btn btn-primary" onClick={load} disabled={loading}>{loading ? '…' : '🔍 Load'}</button>
      </div>

      {/* Thresholds / rules */}
      <div className="card" style={{ padding: 14, marginBottom: 14, display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Flag over ($)<br />
          <input type="number" style={{ ...sel, width: 100, marginTop: 4 }} value={threshold} onChange={e => setThreshold(Number(e.target.value))} /></label>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Flag under ($)<br />
          <input type="number" style={{ ...sel, width: 100, marginTop: 4 }} value={minThreshold} onChange={e => setMinThreshold(Number(e.target.value))} placeholder="0 = off" /></label>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Default chargeback ($)<br />
          <input type="number" style={{ ...sel, width: 130, marginTop: 4 }} value={defaultCb} onChange={e => setDefaultCb(Number(e.target.value))} /></label>
        <button className="btn btn-primary" onClick={load} disabled={loading}>{loading ? '…' : '🔍 Apply & load'}</button>
        <button className="btn" onClick={saveRules}>💾 Save as default</button>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>Flags accessories sold <b>over</b> the max <i>or</i> <b>under</b> the min (underselling; set 0 to disable). <b>Apply &amp; load</b> uses the typed values now; <b>Save as default</b> persists them.</span>
      </div>

      {/* Dashboard summary */}
      {summary && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
          <Tile label="Flags" value={String(summary.flags)} sub={`${summary.txns} transaction(s)`} />
          <Tile label="Over max" value={String(summary.over)} sub={`> ${money(threshold)}`} />
          <Tile label="Under min" value={String(summary.under)} sub={minThreshold > 0 ? `< ${money(minThreshold)}` : 'min off'} />
          <Tile label="Total rung $" value={money(summary.total)} sub="in flagged sales" />
          <Tile label="Chargeback exposure" value={money(summary.chargeback_total)} sub="at default amounts" />
        </div>
      )}
      {(byStore.length > 0 || byRep.length > 0) && (
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 14 }}>
          <AggCard title="By store" rows={byStore} onPick={(n) => setStoreF(n === storeF ? '' : n)} active={storeF} />
          <AggCard title="By rep" rows={byRep} onPick={(n) => setRepF(n === repF ? '' : n)} active={repF} />
        </div>
      )}
      {byStoreRep.length > 0 && <StoreRepCard rows={byStoreRep} onPick={(s, r) => { setStoreF(s === storeF ? '' : s); setRepF(r === repF ? '' : r) }} activeStore={storeF} activeRep={repF} />}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
        <button className="btn" onClick={() => toggleAll(true)}>Select all ({filtered.length})</button>
        <button className="btn" onClick={() => toggleAll(false)}>Clear</button>
        <button className="btn btn-primary" onClick={pushSelected} disabled={!pickedRows.length}>🔻 Flag {pickedRows.length || ''} → chargebacks</button>
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1000 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['', 'Date', 'Store', 'Rep', 'Item', 'SKU', 'Dept / Cat', 'Phone model', 'Sold $', 'Reason', 'Chargeback $', 'Receipt'].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {filtered.map(r => (
              <tr key={r.dedupe_key} style={{ opacity: r.already_flagged ? 0.6 : 1 }}>
                <td style={cell}><input type="checkbox" checked={!!picked[r.dedupe_key]} onChange={e => setPicked(p => ({ ...p, [r.dedupe_key]: e.target.checked }))} /></td>
                <td style={cell}>{(r.trans_date || '').slice(0, 10)}</td>
                <td style={cell}>{r.store || '—'}</td>
                <td style={cell}>{r.rep || <span style={{ color: '#dc2626' }}>no rep</span>}</td>
                <td style={{ ...cell, whiteSpace: 'normal', maxWidth: 240 }}>
                  <button onClick={() => openReceipt(r.trans_id)} style={{ border: 'none', background: 'none', padding: 0, cursor: 'pointer', color: 'var(--accent, #2563eb)', textAlign: 'left', fontSize: 13 }}>{r.item_desc || '—'}</button>
                </td>
                <td style={cell}>{r.sku || '—'}</td>
                <td style={{ ...cell, fontSize: 11, color: 'var(--text3)' }}>{[r.department, r.category].filter(Boolean).join(' / ') || '—'}</td>
                <td style={cell}>{r.phone_model || <span style={{ color: 'var(--text3)' }}>—</span>}</td>
                <td style={{ ...cell, fontWeight: 600 }}>{money(r.ext_price)}</td>
                <td style={cell}>
                  <span style={{ fontSize: 11, fontWeight: 600, padding: '1px 7px', borderRadius: 10,
                    background: r.flag_reason === 'under' ? '#fef3c7' : '#fee2e2',
                    color: r.flag_reason === 'under' ? '#92400e' : '#b42318' }}>
                    {r.flag_reason === 'under' ? '▼ under' : '▲ over'}</span>
                </td>
                <td style={cell}><input type="number" style={{ ...sel, width: 100 }} value={amts[r.dedupe_key] ?? r.chargeback_amount}
                  onChange={e => setAmts(a => ({ ...a, [r.dedupe_key]: Number(e.target.value) }))} /></td>
                <td style={cell}>
                  <button className="btn btn-sm" onClick={() => openReceipt(r.trans_id)}>🧾 View</button>
                  {r.already_flagged && <span className="badge badge-blue" style={{ fontSize: 11, marginLeft: 6 }}>flagged</span>}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && <tr><td colSpan={12} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>{loading ? 'Loading…' : 'No flagged accessory sales for this range. Click Load.'}</td></tr>}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
        Items are classified on the <a href="/commcalc/item-mapping">Item / Model Mapping</a> page. New items auto-add there as "unclassified" — set their type so they flag correctly.
      </p>

      {(receipt || receiptBusy) && <ReceiptModal receipt={receipt} busy={receiptBusy} onClose={() => { setReceipt(null); setReceiptBusy(false) }} />}
    </div>
  )
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card" style={{ flex: '1 1 170px', minWidth: 150, padding: 14 }}>
      <div style={{ fontSize: 12, color: 'var(--text3)', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function AggCard({ title, rows, onPick, active }: { title: string; rows: Agg[]; onPick: (n: string) => void; active: string }) {
  return (
    <div className="card" style={{ flex: '1 1 320px', minWidth: 280, padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '10px 12px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>{title}</div>
      <div style={{ maxHeight: 240, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Name', 'Flags', 'Total $'].map(h => <th key={h} style={{ textAlign: h === 'Name' ? 'left' : 'right', padding: '6px 10px', fontSize: 11, color: 'var(--text2)', fontWeight: 600 }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map(a => (
              <tr key={a.name} onClick={() => onPick(a.name)} style={{ cursor: 'pointer', background: active === a.name ? 'var(--surface2)' : undefined }}>
                <td style={{ ...cell, whiteSpace: 'normal' }}>{a.name}</td>
                <td style={{ ...cell, textAlign: 'right' }}>{a.flags}{a.under ? <span style={{ color: '#92400e', fontSize: 11 }}> ({a.under}▼)</span> : ''}</td>
                <td style={{ ...cell, textAlign: 'right', fontWeight: 600 }}>{money(a.total)}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={3} style={{ padding: 16, textAlign: 'center', color: 'var(--text3)', fontSize: 12 }}>No data</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function StoreRepCard({ rows, onPick, activeStore, activeRep }:
  { rows: StoreRepAgg[]; onPick: (store: string, rep: string) => void; activeStore: string; activeRep: string }) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden', marginBottom: 14 }}>
      <div style={{ padding: '10px 12px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
        By store · per employee <span style={{ fontWeight: 400, color: 'var(--text3)' }}>— flags &amp; $ rung out per rep</span>
      </div>
      <div style={{ maxHeight: 320, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Store', 'Employee', 'Flags', 'Over', 'Under', 'Txns', 'Total $'].map(h =>
              <th key={h} style={{ textAlign: h === 'Store' || h === 'Employee' ? 'left' : 'right', padding: '6px 10px', fontSize: 11, color: 'var(--text2)', fontWeight: 600 }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map((a, i) => {
              const on = activeStore === a.store && activeRep === a.rep
              return (
                <tr key={i} onClick={() => onPick(a.store, a.rep)} style={{ cursor: 'pointer', background: on ? 'var(--surface2)' : undefined }}>
                  <td style={{ ...cell, whiteSpace: 'normal' }}>{a.store}</td>
                  <td style={{ ...cell, whiteSpace: 'normal' }}>{a.rep}</td>
                  <td style={{ ...cell, textAlign: 'right', fontWeight: 600 }}>{a.flags}</td>
                  <td style={{ ...cell, textAlign: 'right', color: '#b42318' }}>{a.over || ''}</td>
                  <td style={{ ...cell, textAlign: 'right', color: '#92400e' }}>{a.under || ''}</td>
                  <td style={{ ...cell, textAlign: 'right' }}>{a.txns}</td>
                  <td style={{ ...cell, textAlign: 'right', fontWeight: 600 }}>{money(a.total)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ReceiptModal({ receipt, busy, onClose }: { receipt: Receipt | null; busy: boolean; onClose: () => void }) {
  const h = receipt?.header || {}
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex',
      alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 16 }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{ maxWidth: 720, width: '100%', maxHeight: '85vh', overflowY: 'auto', padding: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 16 }}>🧾 Receipt {receipt?.trans_id ? `#${receipt.trans_id}` : ''}</div>
          <span style={{ flex: 1 }} />
          <button className="btn btn-sm" onClick={onClose}>Close</button>
        </div>
        {busy && <div style={{ color: 'var(--text3)', padding: 20 }}>Loading receipt…</div>}
        {receipt && (
          <>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 13, color: 'var(--text2)', marginBottom: 12 }}>
              <span><b>Store:</b> {h.store || '—'}</span>
              <span><b>Rep:</b> {h.rep || '—'}</span>
              <span><b>Date:</b> {(h.trans_date || '').slice(0, 10) || '—'}</span>
              {h.register && <span><b>Register:</b> {h.register}</span>}
              {h.tender_type && <span><b>Tender:</b> {h.tender_type}</span>}
              {h.trans_type && <span><b>Type:</b> {h.trans_type}</span>}
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Item', 'Type', 'Dept / Cat', 'Contract', 'GP', 'Price'].map(c =>
                  <th key={c} style={{ textAlign: c === 'GP' || c === 'Price' ? 'right' : 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)', fontWeight: 600 }}>{c}</th>)}
              </tr></thead>
              <tbody>
                {receipt.lines.map((l, i) => (
                  <tr key={i} style={{ opacity: l.voided ? 0.5 : 1 }}>
                    <td style={{ ...cell, whiteSpace: 'normal', maxWidth: 260 }}>
                      {l.product_desc || '—'}{l.voided && <span style={{ color: '#dc2626', fontSize: 11 }}> (void)</span>}
                      {(l.mdn || l.serial_1) && <div style={{ fontSize: 11, color: 'var(--text3)' }}>{[l.mdn, l.serial_1].filter(Boolean).join(' · ')}</div>}
                    </td>
                    <td style={cell}>{l.item_type || '—'}</td>
                    <td style={{ ...cell, fontSize: 11, color: 'var(--text3)' }}>{[l.department, l.category].filter(Boolean).join(' / ') || '—'}</td>
                    <td style={{ ...cell, fontSize: 12 }}>{l.contract_type || '—'}</td>
                    <td style={{ ...cell, textAlign: 'right' }}>{money(l.gp)}</td>
                    <td style={{ ...cell, textAlign: 'right', fontWeight: 600 }}>{money(l.ext_price)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot><tr>
                <td colSpan={5} style={{ ...cell, textAlign: 'right', fontWeight: 700 }}>Receipt total</td>
                <td style={{ ...cell, textAlign: 'right', fontWeight: 700 }}>{money(receipt.total)}</td>
              </tr></tfoot>
            </table>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>{receipt.line_count} line item(s) on this transaction.</div>
          </>
        )}
      </div>
    </div>
  )
}
