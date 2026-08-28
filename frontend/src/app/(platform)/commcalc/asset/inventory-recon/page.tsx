'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

type Cat = { asset: number; b2b: number; diff: number }
type Row = { store: string; market: string | null; categories: Record<string, Cat>; total_abs_diff: number; in_asset: boolean; in_b2b: boolean }

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'center', padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid var(--border)', fontSize: 13, textAlign: 'center', whiteSpace: 'nowrap' }
const CAT_LABEL: Record<string, string> = { iphone: 'iPhone', android: 'Android', tablet: 'Tablet', watch: 'Watch', hotspot: 'Hotspot' }

export default function InventoryReconPage() {
  const [data, setData] = useState<any>(null)
  const [buckets, setBuckets] = useState<string[]>(['iphone', 'android', 'tablet', 'watch', 'hotspot'])
  const [markets, setMarkets] = useState<string[]>([])
  const [stores, setStores] = useState<{ store: string }[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [fStore, setFStore] = useState('')
  const [fMarket, setFMarket] = useState('')
  const [asOf, setAsOf] = useState('')
  const [upDate, setUpDate] = useState(localToday())

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = `store=${encodeURIComponent(fStore)}&market=${encodeURIComponent(fMarket)}&as_of=${asOf}`
      const [opt, recon] = await Promise.all([
        apiCached('/api/v1/asset/filter-options', LOOKUP).catch(() => ({ stores: [], markets: [] })),
        api(`/api/v1/asset/inventory-recon?${qs}`),
      ])
      setStores(opt.stores || []); setMarkets(opt.markets || [])
      setData(recon); setBuckets(recon.buckets || buckets)
    } catch (e: any) { setMsg('Load failed: ' + (e?.message || e)) }
    setLoading(false)
  }, [fStore, fMarket, asOf])
  useEffect(() => { load() }, [load])

  async function uploadFile(file: File) {
    setBusy(true); setMsg('Reading sheet…')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(await file.arrayBuffer())
      const raw: any[] = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: '' })
      const pick = (r: any, keys: string[]) => { for (const k of Object.keys(r)) if (keys.includes(k.trim().toLowerCase())) return String(r[k]).trim(); return '' }
      // `value` is optional: most b2bsoft/POS "Inventory Aging" exports carry a $ cost/retail
      // column alongside qty. When present, it's aggregated per store (canonicalized against
      // store_mapping on the backend) and written into commcalc.inventory_value — the Balance
      // Sheet inventory line (see Inventory Values) — in the SAME upload, independent of
      // whether the row's category maps to one of the 5 recon buckets below.
      const rows = raw.map(r => ({
        store: pick(r, ['store', 'location', 'address', 'store address', 'store name']),
        category: pick(r, ['category', 'type', 'device type', 'group', 'product type', 'model', 'item']),
        qty: pick(r, ['qty', 'quantity', 'count', 'on hand', 'on-hand', 'stock', 'in stock', 'inventory']),
        value: pick(r, ['value', '$ value', 'inventory value', 'on hand value', 'on-hand value',
          'stock value', 'cost', 'unit cost', 'ext cost', 'extended cost', 'total cost',
          'item cost', 'inventory cost', 'amount', 'retail', 'retail value', 'ext price', 'extended price']),
      })).filter(r => r.store && (r.value !== '' || (r.category && r.qty !== '')))
      if (!rows.length) { setMsg('No usable rows — need store + (category & qty) and/or a $ value column.'); setBusy(false); return }
      const res = await api('/api/v1/asset/b2b-inventory/upload', { method: 'POST', body: JSON.stringify({ as_of_date: upDate, rows }) })
      const valueMsg = res.inventory_value_stores
        ? ` · wrote $${Number(res.inventory_value_total).toLocaleString(undefined, { maximumFractionDigits: 0 })} inventory value to the Balance Sheet for ${res.inventory_value_stores} store(s).`
        : ''
      const staleMsg = res.inventory_value_skipped_stale?.length
        ? ` · ⚠️ ${res.inventory_value_skipped_stale.length} store(s) NOT updated — the Balance Sheet already has a value from a newer date (${res.inventory_value_skipped_stale.map((s: any) => `${s.store}: kept ${s.existing_as_of_date}`).join('; ')}).`
        : ''
      setMsg(`Loaded ${res.loaded} category rows as of ${res.as_of_date}${res.skipped ? ` · ${res.skipped} skipped (unmapped category/qty)` : ''}.${valueMsg}${staleMsg}`)
      setAsOf(res.as_of_date)
      await load()
    } catch (e: any) { setMsg('Upload failed: ' + (e?.message || e)) }
    setBusy(false)
  }

  async function syncFlags() {
    setBusy(true); setMsg('Syncing flags…')
    try {
      const res = await api('/api/v1/asset/sync-inventory-flags', { method: 'POST' })
      setMsg(res.b2b_loaded ? `Wrote ${res.flagged} mismatch flags (as of ${res.as_of}).` : 'No b2bsoft data loaded yet — upload a snapshot first.')
    } catch (e: any) { setMsg('Sync failed: ' + (e?.message || e)) }
    setBusy(false)
  }

  const rows: Row[] = data?.rows || []
  const buildPayload = (): ExportPayload => ({
    title: 'On-Inventory ↔ b2bsoft Reconciliation',
    subtitle: `As of ${data?.as_of || '—'}${fMarket ? ` · ${fMarket}` : ''}${fStore ? ` · ${fStore}` : ''}`,
    filename: 'inventory-recon',
    sheets: [{
      name: 'Recon',
      columns: [
        { header: 'Store', get: (r: Row) => r.store },
        { header: 'Market', get: (r: Row) => r.market || '' },
        ...buckets.flatMap(b => [
          { header: `${CAT_LABEL[b]} asset`, get: (r: Row) => r.categories[b]?.asset ?? 0, align: 'right' as const },
          { header: `${CAT_LABEL[b]} b2b`, get: (r: Row) => r.categories[b]?.b2b ?? 0, align: 'right' as const },
          { header: `${CAT_LABEL[b]} diff`, get: (r: Row) => r.categories[b]?.diff ?? 0, align: 'right' as const },
        ]),
        { header: 'Total |diff|', get: (r: Row) => r.total_abs_diff, align: 'right' as const },
      ],
      rows,
    }],
  })

  const diffCell = (c: Cat | undefined, key: string) => {
    if (!c) return <td key={key} style={td}>—</td>
    const bad = c.diff !== 0
    return (
      <td key={key} style={{ ...td, background: bad ? '#fef2f2' : undefined }}>
        <span style={{ color: 'var(--text2)' }}>{c.asset}</span>
        <span style={{ color: 'var(--text3)' }}> / {c.b2b}</span>
        {bad && <strong style={{ color: c.diff > 0 ? '#b45309' : '#dc2626', marginLeft: 4 }}>{c.diff > 0 ? '+' : ''}{c.diff}</strong>}
      </td>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12, flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📦 On-Inventory ↔ b2bsoft Recon</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Asset On-Inventory vs b2bsoft inventory, per store · iPhone / Android / Tablet / Watch / Hotspot. Each cell = <b>asset / b2b</b>; mismatches highlighted.
          </p>
          {/* PURPOSE LINE (owner 2026-08-10) — see the twin note on /accounts/inventory. */}
          <p style={{ color: 'var(--text3)', fontSize: 12.5, margin: '6px 0 0', maxWidth: 780 }}>
            <b>Purpose — UNITS.</b> Do the device COUNTS agree? It compares the VIP asset ledger’s unsold
            On-Inventory devices against b2bsoft’s snapshot, per store and per device type, to surface
            missing/extra handsets. It says nothing about value. For “what is the stock WORTH” (the
            Balance Sheet line), use <Link href="/accounts/inventory">Inventory Values</Link>.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <a className="btn" href="/commcalc/asset" style={{ textDecoration: 'none' }}>← Asset</a>
          <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>
        </div>
      </div>

      <div className="card" style={{ padding: '10px 14px', marginBottom: 14, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13 }}>
        ⚙️ Auto-import from <b>wsreports.b2bsoft.com</b> is pending the live b2bsoft portal sweep. Until it's wired,
        upload a b2bsoft <b>Inventory Aging</b> export below (columns: <code>store</code>, <code>category</code>, <code>qty</code> —
        plus an optional $ <code>value</code>/<code>cost</code> column). If the file has a $ column, this same upload also
        populates the <Link href="/accounts/inventory" style={{ color: 'inherit', textDecoration: 'underline' }}>Balance Sheet inventory value</Link> per store —
        no separate step needed.
      </div>

      {/* Upload + filters */}
      <div className="card" style={{ padding: 14, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>⬆️ Upload b2bsoft inventory:</span>
        <label style={{ fontSize: 13 }}>as of <input style={{ ...sel, width: 150 }} type="date" value={upDate} onChange={e => setUpDate(e.target.value)} /></label>
        <label className="btn" style={{ cursor: busy ? 'default' : 'pointer', margin: 0 }}>
          {busy ? '⏳ Working…' : '📄 Choose file'}
          <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={busy}
            onChange={e => { const f = e.target.files?.[0]; if (f) uploadFile(f); e.currentTarget.value = '' }} />
        </label>
        <div style={{ flex: 1 }} />
        <button className="btn" disabled={busy || !data?.b2b_loaded} onClick={syncFlags}>🚩 Sync mismatches to Flags</button>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Filter:</span>
        <select style={sel} value={fMarket} onChange={e => setFMarket(e.target.value)}><option value="">All markets</option>{markets.map(m => <option key={m}>{m}</option>)}</select>
        <select style={sel} value={fStore} onChange={e => setFStore(e.target.value)}><option value="">All stores</option>{stores.map(s => <option key={s.store} value={s.store}>{s.store}</option>)}</select>
        <select style={sel} value={asOf} onChange={e => setAsOf(e.target.value)}>
          <option value="">Latest snapshot</option>
          {(data?.available_dates || []).map((d: string) => <option key={d} value={d}>{d}</option>)}
        </select>
        {(fStore || fMarket || asOf) && <button className="btn" onClick={() => { setFStore(''); setFMarket(''); setAsOf('') }}>Clear</button>}
        {msg && <span style={{ fontSize: 13, marginLeft: 8 }}>{msg}</span>}
      </div>

      {/* Summary */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
        <div className="card" style={{ padding: '12px 16px' }}><div style={{ fontSize: 12, color: 'var(--text2)' }}>Mismatched stores</div><div style={{ fontSize: 20, fontWeight: 700, color: (data?.mismatch_stores || 0) ? '#dc2626' : '#059669' }}>{data?.mismatch_stores ?? 0}</div></div>
        <div className="card" style={{ padding: '12px 16px' }}><div style={{ fontSize: 12, color: 'var(--text2)' }}>Total |diff| units</div><div style={{ fontSize: 20, fontWeight: 700 }}>{data?.total_abs_diff ?? 0}</div></div>
        <div className="card" style={{ padding: '12px 16px' }}><div style={{ fontSize: 12, color: 'var(--text2)' }}>b2bsoft snapshot</div><div style={{ fontSize: 16, fontWeight: 700 }}>{data?.b2b_loaded ? (data?.as_of || '—') : 'none yet'}</div></div>
      </div>

      {loading ? <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div> : !data?.b2b_loaded ? (
        <div className="card" style={{ padding: 24, color: 'var(--text2)', fontSize: 14 }}>
          No b2bsoft inventory loaded yet. Asset On-Inventory is classified and ready — upload a b2bsoft
          snapshot below to reconcile.
          {/* Owner report 2026-08-10: "inventory aging is being pulled in ... but it is not updating the
              relevant tables". It is landing — just not HERE. Say so, instead of leaving an empty page
              that looks like the import failed. */}
          <div style={{ marginTop: 10, fontSize: 12.5, color: 'var(--text3)', maxWidth: 820 }}>
            <b>Note:</b> the automatic <i>Inventory Aging</i> import does <b>not</b> feed this page. It writes
            the per-store <b>$ value</b> (→ <Link href="/accounts/inventory">Inventory Values</Link> and the
            Balance Sheet) and the per-device cost rows — but the per-store × device-type <b>unit counts</b>
            this recon compares against are only written by the upload on this page. So a green
            “inventory_aging ✓ N rows” on the Imports screen is real and still leaves this table empty.
          </div>
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              <th style={{ ...th, textAlign: 'left' }}>Store</th>
              {buckets.map(b => <th key={b} style={th}>{CAT_LABEL[b]}</th>)}
              <th style={th}>Σ|diff|</th>
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.store} style={{ background: r.total_abs_diff > 0 ? '#fff7f7' : undefined }}>
                  <td style={{ ...td, textAlign: 'left', fontWeight: 600 }}>{r.store}{!r.in_asset && <span className="badge" style={{ fontSize: 10, marginLeft: 6 }}>b2b only</span>}<div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{r.market || ''}</div></td>
                  {buckets.map(b => diffCell(r.categories[b], b))}
                  <td style={{ ...td, fontWeight: 700, color: r.total_abs_diff > 0 ? '#dc2626' : '#059669' }}>{r.total_abs_diff}</td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={buckets.length + 2} style={{ ...td, color: 'var(--text3)' }}>No rows.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
