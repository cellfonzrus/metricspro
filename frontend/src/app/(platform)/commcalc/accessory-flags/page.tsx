'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

// Accessories sold above the user-defined threshold → flag → push to the rep's chargebacks.
// Phone model comes from the item mapping (the SU sheet). Chargeback $ is per-row editable with a
// configurable default. Unseen items auto-add to the mapping (manage them on Item / Model Mapping).
type Row = {
  sale_id: string; trans_id: string; trans_date: string | null; period: string | null
  store: string | null; rep: string | null; department: string | null; category: string | null
  item_desc: string | null; sku: string | null; ext_price: number; phone_model: string | null
  chargeback_amount: number; dedupe_key: string; already_flagged: boolean
}
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
  const [amts, setAmts] = useState<Record<string, number>>({})
  const [picked, setPicked] = useState<Record<string, boolean>>({})
  const [threshold, setThreshold] = useState(35)
  const [defaultCb, setDefaultCb] = useState(0)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')

  const loadRules = useCallback(() => {
    api('/api/v1/commcalc/flag-rules').then((r: any) => {
      setThreshold(Number(r.accessory_threshold ?? 35)); setDefaultCb(Number(r.accessory_chargeback_amount ?? 0))
    }).catch(() => {})
  }, [])
  useEffect(() => { loadRules() }, [loadRules])

  async function saveRules() {
    setMsg('')
    try {
      await api('/api/v1/commcalc/flag-rules', { method: 'PUT', body: JSON.stringify({
        accessory_threshold: threshold, accessory_chargeback_amount: defaultCb }) })
      setMsg('Rules saved.')
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
  }

  async function load() {
    setLoading(true); setMsg('')
    try {
      const qs = new URLSearchParams()
      if (start) qs.set('start', start)
      if (end) qs.set('end', end)
      const r = await api(`/api/v1/commcalc/accessory-flags?${qs.toString()}`)
      const rws: Row[] = r.rows || []
      setRows(rws)
      setThreshold(Number(r.threshold ?? threshold)); setDefaultCb(Number(r.default_chargeback ?? defaultCb))
      setAmts(Object.fromEntries(rws.map(x => [x.dedupe_key, x.chargeback_amount])))
      setPicked({})
      setMsg(`${rws.length} accessory sale(s) over ${money(r.threshold)}${r.flagged_qty ? ` · ${r.flagged_qty} already flagged` : ''}.`)
    } catch (e: any) { setMsg('Load failed: ' + (e?.message || e)) }
    setLoading(false)
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
          Accessories sold over your threshold, by store + rep + date range. Flag them to push a chargeback to the rep who sold it.
        </p>
      </div>

      {/* Rules */}
      <div className="card" style={{ padding: 14, marginBottom: 14, display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Flag accessories over ($)<br />
          <input type="number" style={{ ...sel, width: 110, marginTop: 4 }} value={threshold} onChange={e => setThreshold(Number(e.target.value))} /></label>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Default chargeback ($)<br />
          <input type="number" style={{ ...sel, width: 130, marginTop: 4 }} value={defaultCb} onChange={e => setDefaultCb(Number(e.target.value))} /></label>
        <button className="btn" onClick={saveRules}>💾 Save rules</button>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>Applies to all flagged rows; override per row below.</span>
      </div>

      {/* Filters */}
      <div className="card" style={{ padding: 14, marginBottom: 14, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>From<br /><input type="date" style={{ ...sel, marginTop: 4 }} value={start} onChange={e => setStart(e.target.value)} /></label>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>To<br /><input type="date" style={{ ...sel, marginTop: 4 }} value={end} onChange={e => setEnd(e.target.value)} /></label>
        <button className="btn btn-primary" onClick={load} disabled={loading}>{loading ? '…' : '🔍 Load'}</button>
        <div style={{ flex: 1 }} />
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Store<br />
          <select style={{ ...sel, marginTop: 4, minWidth: 150 }} value={storeF} onChange={e => setStoreF(e.target.value)}>
            <option value="">All stores</option>{stores.map(s => <option key={s} value={s}>{s}</option>)}</select></label>
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Rep<br />
          <select style={{ ...sel, marginTop: 4, minWidth: 150 }} value={repF} onChange={e => setRepF(e.target.value)}>
            <option value="">All reps</option>{reps.map(s => <option key={s} value={s}>{s}</option>)}</select></label>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
        <button className="btn" onClick={() => toggleAll(true)}>Select all ({filtered.length})</button>
        <button className="btn" onClick={() => toggleAll(false)}>Clear</button>
        <button className="btn btn-primary" onClick={pushSelected} disabled={!pickedRows.length}>🔻 Flag {pickedRows.length || ''} → chargebacks</button>
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1000 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['', 'Date', 'Store', 'Rep', 'Item', 'SKU', 'Dept / Cat', 'Phone model', 'Sold $', 'Chargeback $', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {filtered.map(r => (
              <tr key={r.dedupe_key} style={{ opacity: r.already_flagged ? 0.6 : 1 }}>
                <td style={cell}><input type="checkbox" checked={!!picked[r.dedupe_key]} onChange={e => setPicked(p => ({ ...p, [r.dedupe_key]: e.target.checked }))} /></td>
                <td style={cell}>{(r.trans_date || '').slice(0, 10)}</td>
                <td style={cell}>{r.store || '—'}</td>
                <td style={cell}>{r.rep || <span style={{ color: '#dc2626' }}>no rep</span>}</td>
                <td style={{ ...cell, whiteSpace: 'normal', maxWidth: 240 }}>{r.item_desc || '—'}</td>
                <td style={cell}>{r.sku || '—'}</td>
                <td style={{ ...cell, fontSize: 11, color: 'var(--text3)' }}>{[r.department, r.category].filter(Boolean).join(' / ') || '—'}</td>
                <td style={cell}>{r.phone_model || <span style={{ color: 'var(--text3)' }}>—</span>}</td>
                <td style={{ ...cell, fontWeight: 600 }}>{money(r.ext_price)}</td>
                <td style={cell}><input type="number" style={{ ...sel, width: 100 }} value={amts[r.dedupe_key] ?? r.chargeback_amount}
                  onChange={e => setAmts(a => ({ ...a, [r.dedupe_key]: Number(e.target.value) }))} /></td>
                <td style={cell}>{r.already_flagged && <span className="badge badge-blue" style={{ fontSize: 11 }}>flagged</span>}</td>
              </tr>
            ))}
            {filtered.length === 0 && <tr><td colSpan={11} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>{loading ? 'Loading…' : 'No accessory sales over the threshold for this range. Click Load.'}</td></tr>}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
        Items are classified on the <a href="/commcalc/item-mapping">Item / Model Mapping</a> page. New items auto-add there as "unclassified" — set their type so they flag correctly.
      </p>
    </div>
  )
}
