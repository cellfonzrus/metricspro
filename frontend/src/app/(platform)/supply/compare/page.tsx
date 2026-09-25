'use client'
// Supply → Price Compare (index §36). Every vendor's newest price read, the same product matched across
// vendors (the ONE pricing logic the price-compare kit also runs), the cheapest IN-STOCK vendor, and an
// "Add to cart" that carries every vendor's offer — the cart then decides where to buy, shipping included.
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, input, btn, btnPrimary, th, cell, fmtMoney, fmtDate, addToCart, useStoredCart,
         AVAIL_LABEL, AVAIL_COLOR, type CompareRow } from '@/lib/supply'

// The fields of GET /supply/compare this page reads.
interface CompareResp {
  rows?: CompareRow[]; vendors?: { id: string; name: string; catalog_seen_at?: string | null }[]
  migrated?: boolean; note?: string
}

export default function SupplyComparePage() {
  const [rows, setRows] = useState<CompareRow[]>([])
  const [vendors, setVendors] = useState<{ id: string; name: string; catalog_seen_at?: string | null }[]>([])
  const [q, setQ] = useState('')
  const [onlyCompared, setOnlyCompared] = useState(false)
  const [note, setNote] = useState('')
  const [qty, setQty] = useState<Record<number, string>>({})
  // The stored cart's size until an Add changes it (then the count addToCart returns).
  const storedCart = useStoredCart()
  const [addedCartN, setCartN] = useState<number | null>(null)
  const cartN = addedCartN ?? storedCart?.length ?? 0
  // A load runs on mount and whenever the search or the filter changes: `loading` starts true, and the
  // two controls flip it on as they change their value (the effect itself never sets state synchronously).
  const [loading, setLoading] = useState(true)
  const changeQ = (v: string) => { if (v !== q) { setLoading(true); setQ(v) } }
  const changeOnlyCompared = (v: boolean) => { if (v !== onlyCompared) { setLoading(true); setOnlyCompared(v) } }

  // A promise chain (not async/await) so every setState visibly runs in a callback, after the fetch.
  const load = useCallback(() => api(`/api/v1/supply/compare?q=${encodeURIComponent(q)}&only_compared=${onlyCompared}`)
    .then((r: CompareResp) => { setRows(r.rows || []); setVendors(r.vendors || []); setNote(r.migrated === false ? r.note ?? '' : '') })
    .catch(e => { setNote(e?.message || String(e)) })
    .finally(() => setLoading(false)), [q, onlyCompared])
  useEffect(() => { load() }, [load])

  const names = useMemo(() => Object.fromEntries(vendors.map(v => [v.id, v.name])), [vendors])

  // Same rule as the cart optimizer: the quantity is UNITS only when every vendor states its pack size.
  const unitsBasis = (r: CompareRow) => {
    const os = Object.values(r.offers).filter(o => o.price != null)
    return os.length > 0 && os.every(o => (o.pack_qty || 0) > 1)
  }
  function add(r: CompareRow, i: number) {
    const n = Math.max(1, parseInt(qty[i] || '1', 10) || 1)
    const ids = Object.values(r.offers).map(o => o.row_id).filter(Boolean)
    const cart = addToCart({ key: ids.slice().sort().join('|'), label: r.product, qty: n, offer_ids: ids,
                             basis: unitsBasis(r) ? 'units' : 'packs' })
    setCartN(cart.length)
  }

  return (
    <div style={{ padding: 20, maxWidth: 1400 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Price compare</h1>
        <span style={{ flex: 1 }} />
        <Link href="/supply/cart" style={{ ...btnPrimary, textDecoration: 'none' }}>Cart ({cartN})</Link>
      </div>
      <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 12px' }}>
        {vendors.map(v => `${v.name}: prices from ${fmtDate(v.catalog_seen_at)}`).join(' · ') || 'No vendor prices yet — read them on the Vendors page.'}
      </p>
      {note && <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 12, fontSize: 13 }}>{note}</div>}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <input style={{ ...input, maxWidth: 320 }} placeholder="Search (e.g. 12x12x12 box, tape)" value={q}
               onChange={e => changeQ(e.target.value)} />
        <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={onlyCompared} onChange={e => changeOnlyCompared(e.target.checked)} /> Only items sold by 2+ vendors
        </label>
        {loading && <span style={{ fontSize: 12, color: 'var(--text2)' }}>Loading…</span>}
      </div>
      <section style={{ ...panel, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            <th style={th}>Product</th>
            {vendors.map(v => <th key={v.id} style={th}>{v.name}</th>)}
            <th style={th}>Cheapest in stock</th><th style={th}>Saves</th><th style={th}>Add to cart</th>
          </tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td style={cell}>
                  <div>{r.product}</div>
                  <div style={{ fontSize: 11, color: 'var(--text2)' }}>{r.match}{r.basis === 'per unit' ? ' · compared per unit' : ''}</div>
                  {r.note && <div style={{ fontSize: 11, color: '#b45309' }}>{r.note}</div>}
                </td>
                {vendors.map(v => {
                  const o = r.offers[v.id]
                  if (!o) return <td key={v.id} style={{ ...cell, color: 'var(--text2)' }}>—</td>
                  return (
                    <td key={v.id} style={{ ...cell, background: r.best_vendor === v.id ? 'rgba(22,163,74,.08)' : undefined }}>
                      <div style={{ fontWeight: r.best_vendor === v.id ? 700 : 400 }}>{fmtMoney(o.price)}{o.pack_qty ? ` / ${o.pack_qty}` : ''}</div>
                      {o.pack_qty ? <div style={{ fontSize: 11, color: 'var(--text2)' }}>{fmtMoney(o.unit_price)} each</div> : null}
                      <div style={{ fontSize: 11, color: AVAIL_COLOR[o.availability] || '#6b7280' }}>
                        {AVAIL_LABEL[o.availability] || o.availability}{o.stock_qty != null ? ` (${o.stock_qty})` : ''}
                      </div>
                      {o.url && <a href={o.url} target="_blank" rel="noreferrer" style={{ fontSize: 11 }}>open</a>}
                    </td>
                  )
                })}
                <td style={cell}>{r.best_vendor ? names[r.best_vendor] || '—' : '—'}</td>
                <td style={cell}>{r.savings ? `${fmtMoney(r.savings)}${r.savings_pct ? ` (${Math.round(r.savings_pct * 100)}%)` : ''}` : '—'}</td>
                <td style={cell}>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <input style={{ ...input, width: 64 }} inputMode="numeric" placeholder="1" value={qty[i] || ''}
                           title={unitsBasis(r) ? 'How many UNITS you need (whole packs are ordered)' : 'How many of the vendor\'s packs'}
                           onChange={e => setQty(p => ({ ...p, [i]: e.target.value }))} />
                    <button style={btn} onClick={() => add(r, i)}>Add</button>
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text2)' }}>{unitsBasis(r) ? 'units' : 'packs'}</div>
                </td>
              </tr>
            ))}
            {!rows.length && !loading && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={vendors.length + 4}>Nothing to compare yet.</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  )
}
