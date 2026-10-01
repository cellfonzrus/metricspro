'use client'
// Supply → Price Compare (index §36). Every vendor's newest price read, the same product matched across
// vendors (the ONE pricing logic the price-compare kit also runs), the cheapest IN-STOCK vendor, and an
// "Add to cart" that carries every vendor's offer — the cart then decides where to buy, shipping included.
//
// Owner 2026-10-01: a supplier dropdown and an item dropdown to filter; a ★ to keep an item in a favourites list
// (mig 1032 — per company, by vendor + item so it survives every price read) and "Reorder favourites", which puts
// every starred item in the cart at its saved quantity. The favourites list IS this comparison, filtered
// (GET /supply/compare?favorites=true) — never a second comparison.
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/client'
import { panel, input, btn, btnPrimary, th, cell, fmtMoney, fmtDate, addToCart, useStoredCart, favoriteKeys, orderRules,
         AVAIL_LABEL, AVAIL_COLOR, type CompareRow } from '@/lib/supply'

// The fields of GET /supply/compare this page reads.
interface CompareResp {
  rows?: CompareRow[]; vendors?: { id: string; name: string; catalog_seen_at?: string | null }[]
  migrated?: boolean; note?: string; favorites_ready?: boolean; favorites_note?: string | null; favorite_count?: number
}
type ApiError = { message?: string } | null | undefined

export default function SupplyComparePage() {
  const router = useRouter()
  const [rows, setRows] = useState<CompareRow[]>([])
  const [vendors, setVendors] = useState<{ id: string; name: string; catalog_seen_at?: string | null }[]>([])
  const [q, setQ] = useState('')
  const [vendor, setVendor] = useState('')
  const [favOnly, setFavOnly] = useState(false)
  const [onlyCompared, setOnlyCompared] = useState(false)
  const [note, setNote] = useState('')
  const [favNote, setFavNote] = useState('')
  const [favCount, setFavCount] = useState(0)
  const [msg, setMsg] = useState('')
  const [qty, setQty] = useState<Record<number, string>>({})
  // The stored cart's size until an Add changes it (then the count addToCart returns).
  const storedCart = useStoredCart()
  const [addedCartN, setCartN] = useState<number | null>(null)
  const cartN = addedCartN ?? storedCart?.length ?? 0
  // A load runs on mount and whenever a filter changes: `loading` starts true, and each control flips it on as
  // it changes its value (the effect itself never sets state synchronously).
  const [loading, setLoading] = useState(true)
  const [reloadN, setReloadN] = useState(0)
  const changeQ = (v: string) => { if (v !== q) { setLoading(true); setQ(v) } }
  const changeVendor = (v: string) => { if (v !== vendor) { setLoading(true); setVendor(v) } }
  const changeFavOnly = (v: boolean) => { if (v !== favOnly) { setLoading(true); setFavOnly(v) } }
  const changeOnlyCompared = (v: boolean) => { if (v !== onlyCompared) { setLoading(true); setOnlyCompared(v) } }
  const reload = () => { setLoading(true); setReloadN(n => n + 1) }

  // A promise chain (not async/await) so every setState visibly runs in a callback, after the fetch.
  const load = useCallback(() => api(`/api/v1/supply/compare?q=${encodeURIComponent(q)}&only_compared=${onlyCompared}`
                                     + `&vendor=${encodeURIComponent(vendor)}&favorites=${favOnly}&r=${reloadN}`)
    .then((r: CompareResp) => {
      setRows(r.rows || []); setVendors(r.vendors || []); setNote(r.migrated === false ? r.note ?? '' : '')
      setFavNote(r.favorites_ready === false ? r.favorites_note ?? '' : ''); setFavCount(r.favorite_count || 0)
    })
    .catch(e => { setNote(e?.message || String(e)) })
    .finally(() => setLoading(false)), [q, onlyCompared, vendor, favOnly, reloadN])
  useEffect(() => { load() }, [load])

  const names = useMemo(() => Object.fromEntries(vendors.map(v => [v.id, v.name])), [vendors])
  // The item dropdown: every product name on the page, for the search box's suggestion list.
  const itemNames = useMemo(() => Array.from(new Set(rows.map(r => r.product))).sort().slice(0, 500), [rows])

  // Same rule as the cart optimizer: the quantity is UNITS only when every vendor states its pack size.
  const unitsBasis = (r: CompareRow) => {
    const os = Object.values(r.offers).filter(o => o.price != null)
    return os.length > 0 && os.every(o => (o.pack_qty || 0) > 1)
  }
  const cartItem = (r: CompareRow, n: number) => {
    const ids = Object.values(r.offers).map(o => o.row_id).filter(Boolean)
    return { key: ids.slice().sort().join('|'), label: r.product, qty: n, offer_ids: ids, basis: unitsBasis(r) ? 'units' : 'packs' }
  }
  function add(r: CompareRow, i: number) {
    const n = Math.max(1, parseInt(qty[i] || '1', 10) || 1)
    setCartN(addToCart(cartItem(r, n)).length)
  }

  async function toggleStar(r: CompareRow, i: number) {
    setMsg('')
    const keys = favoriteKeys(r)
    if (!keys.length) { setMsg('❌ This item has no stable identity to star yet — read its prices again.'); return }
    try {
      if (r.favorite) await api('/api/v1/supply/favorites/remove', { method: 'POST', body: JSON.stringify({ keys }) })
      else {
        const n = parseInt(qty[i] || '', 10)
        await api('/api/v1/supply/favorites', { method: 'POST', body: JSON.stringify({ keys, label: r.product, reorder_qty: n > 0 ? n : null }) })
      }
      reload()
    } catch (e) { setMsg('❌ ' + ((e as ApiError)?.message || e)) }
  }
  async function saveReorderQty(r: CompareRow, i: number) {
    const n = parseInt(qty[i] || '', 10)
    if (!(n > 0)) { setMsg('Type the quantity first, then save it as the reorder quantity.'); return }
    try {
      await api('/api/v1/supply/favorites/qty', { method: 'POST', body: JSON.stringify({ keys: favoriteKeys(r), reorder_qty: n }) })
      setMsg(`✅ Reorder quantity for "${r.product}" saved: ${n}.`); reload()
    } catch (e) { setMsg('❌ ' + ((e as ApiError)?.message || e)) }
  }
  // Reorder: every starred item into the cart at its saved quantity (1 when none was saved), then the cart.
  async function reorderFavorites() {
    setMsg('')
    try {
      const r: CompareResp = await api('/api/v1/supply/compare?favorites=true&limit=5000')
      const favs = r.rows || []
      if (!favs.length) { setMsg('No favourites yet — star items with ☆ first.'); return }
      let n = 0
      for (const f of favs) n = addToCart(cartItem(f, f.reorder_qty || 1)).length
      setCartN(n)
      router.push('/supply/cart')
    } catch (e) { setMsg('❌ ' + ((e as ApiError)?.message || e)) }
  }

  return (
    <div style={{ padding: 20, maxWidth: 1400 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Price compare</h1>
        <span style={{ flex: 1 }} />
        <button style={btn} disabled={!!favNote} onClick={reorderFavorites}>★ Reorder favourites ({favCount})</button>
        <Link href="/supply/cart" style={{ ...btnPrimary, textDecoration: 'none' }}>Cart ({cartN})</Link>
      </div>
      <p style={{ fontSize: 13, color: 'var(--text2)', margin: '0 0 12px' }}>
        {vendors.map(v => `${v.name}: prices from ${fmtDate(v.catalog_seen_at)}`).join(' · ') || 'No vendor prices yet — read them on the Vendors page.'}
      </p>
      {note && <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 12, fontSize: 13 }}>{note}</div>}
      {favNote && <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 12, fontSize: 13 }}>{favNote}</div>}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <select style={{ ...input, maxWidth: 220 }} value={vendor} onChange={e => changeVendor(e.target.value)} aria-label="Supplier">
          <option value="">All suppliers</option>
          {vendors.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
        <input style={{ ...input, maxWidth: 340 }} list="supply-items" placeholder="Item — type or pick (e.g. 12x12x12 box, tape)"
               value={q} onChange={e => changeQ(e.target.value)} aria-label="Item" />
        <datalist id="supply-items">{itemNames.map(n => <option key={n} value={n} />)}</datalist>
        {q && <button style={btn} onClick={() => changeQ('')}>Clear</button>}
        <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={favOnly} disabled={!!favNote} onChange={e => changeFavOnly(e.target.checked)} /> ★ Favourites only
        </label>
        <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={onlyCompared} onChange={e => changeOnlyCompared(e.target.checked)} /> Only items sold by 2+ vendors
        </label>
        {loading && <span style={{ fontSize: 12, color: 'var(--text2)' }}>Loading…</span>}
      </div>
      {msg && <div style={{ fontSize: 13, marginBottom: 10 }}>{msg}</div>}
      <section style={{ ...panel, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            <th style={th}></th><th style={th}>Product</th>
            {vendors.map(v => <th key={v.id} style={th}>{v.name}</th>)}
            <th style={th}>Cheapest in stock</th><th style={th}>Saves</th><th style={th}>Add to cart</th>
          </tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td style={cell}>
                  <button title={r.favorite ? 'Remove from favourites' : 'Add to favourites'} disabled={!!favNote}
                          style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 18, color: r.favorite ? '#f59e0b' : 'var(--text2)' }}
                          onClick={() => toggleStar(r, i)}>{r.favorite ? '★' : '☆'}</button>
                </td>
                <td style={cell}>
                  <div>{r.product}</div>
                  <div style={{ fontSize: 11, color: 'var(--text2)' }}>{r.match}{r.basis === 'per unit' ? ' · compared per unit' : ''}</div>
                  {r.note && <div style={{ fontSize: 11, color: '#b45309' }}>{r.note}</div>}
                  {r.favorite && r.reorder_qty ? <div style={{ fontSize: 11, color: '#b45309' }}>Reorder quantity: {r.reorder_qty}</div> : null}
                </td>
                {vendors.map(v => {
                  const o = r.offers[v.id]
                  if (!o) return <td key={v.id} style={{ ...cell, color: 'var(--text2)' }}>—</td>
                  const rules = orderRules(o)
                  return (
                    <td key={v.id} style={{ ...cell, background: r.best_vendor === v.id ? 'rgba(22,163,74,.08)' : undefined }}>
                      <div style={{ fontWeight: r.best_vendor === v.id ? 700 : 400 }}>{fmtMoney(o.price)}{o.pack_qty ? ` / ${o.pack_qty}` : ' each'}</div>
                      {o.pack_qty ? <div style={{ fontSize: 11, color: 'var(--text2)' }}>{fmtMoney(o.unit_price)} each</div> : null}
                      {rules && <div style={{ fontSize: 11, color: '#2563eb' }}>Order {rules}</div>}
                      {o.sku && <div style={{ fontSize: 10, color: 'var(--text2)' }}>#{o.sku}</div>}
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
                    <input style={{ ...input, width: 64 }} inputMode="numeric" placeholder={r.reorder_qty ? String(r.reorder_qty) : '1'} value={qty[i] || ''}
                           title={unitsBasis(r) ? 'How many UNITS you need (whole packs are ordered)' : 'How many of the vendor\'s packs'}
                           onChange={e => setQty(p => ({ ...p, [i]: e.target.value }))} />
                    <button style={btn} onClick={() => add(r, i)}>Add</button>
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text2)' }}>{unitsBasis(r) ? 'units' : 'packs'} · the cart raises it to each vendor&apos;s minimum</div>
                  {r.favorite && <button style={{ ...btn, fontSize: 10, padding: '1px 6px', marginTop: 2 }} onClick={() => saveReorderQty(r, i)}>Save as reorder qty</button>}
                </td>
              </tr>
            ))}
            {!rows.length && !loading && <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={vendors.length + 5}>
              {favOnly ? 'No favourites match — star items with ☆.' : 'Nothing to compare yet.'}</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  )
}
